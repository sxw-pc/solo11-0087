"""Bond coverage orchestration: fixed pledges and pre-authorized replacement.

Two contracts are supported:

* ``fixed_pledge`` — pledges already posted for a formally submitted bid are
  immovable. New bids can only draw on genuinely free capacity, so a cash-first
  greedy never silently reallocates a submitted bid's security.
* ``preauthorized_reallocation`` — the supplier has signed an explicit
  redistribution contract. The replacement is planned as an ordered, atomic
  ledger transaction in which no instrument ever exceeds its face amount and no
  submitted bid is left without cover at any step.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from .bond_coverage import (
    FIXED_PLEDGE, REALLOCATION, allocate, best_effort_allocate, build_views,
    summarize,
)
from .bond_reallocation import plan_replacement, validate_action
from .bond_store import BondStore, canonical_case_id
from .normalize import now_iso
from .store import Store

COVERED = "security_covered"
PENDING = "pending_security"
SHORTFALL = "security_shortfall"
INELIGIBLE = "bid_ineligible"
UNVERIFIABLE = "evidence_pending"


def _decide(
    bids, views, solution: dict[str, dict[str, Decimal]] | None,
    net_pledges: dict[tuple[str, str], Decimal],
) -> None:
    legacy: dict[str, dict[str, Decimal]] = {}
    for (bid_id, instrument_id), amount in net_pledges.items():
        legacy.setdefault(bid_id, {})[instrument_id] = amount
    for bid in bids:
        if bid.envelope_status == "unverifiable":
            bid.status, bid.reason = UNVERIFIABLE, "notice_evidence_missing"
            continue
        if bid.envelope_status == "rejected":
            bid.status, bid.reason = INELIGIBLE, bid.envelope_reason
            continue
        # ``solution`` only contains slices drawn from genuinely free capacity,
        # so it is additive to the ledger-derived net pledges, never a duplicate.
        bid.allocations = dict(legacy.get(bid.bid_id, {}))
        for instrument_id, amount in (solution or {}).get(bid.bid_id, {}).items():
            bid.allocations[instrument_id] = (
                bid.allocations.get(instrument_id, Decimal("0")) + amount
            )
        covered = sum(bid.allocations.values(), Decimal("0"))
        if covered >= bid.required:
            bid.status, bid.reason = COVERED, "required_security_fully_pledged"
        elif covered > 0:
            bid.status, bid.reason = SHORTFALL, "free_capacity_cannot_cover_requirement"
        else:
            bid.status, bid.reason = PENDING, "no_usable_instrument_evidence"


def _sync_review(store: Store, case_id: str, bids) -> None:
    """Sync only BOND_ review items; crawl review items are left untouched."""
    run_id = f"bond-{case_id}"
    current = {(bid.notice_id, f"BOND_{bid.status.upper()}") for bid in bids
               if bid.status != COVERED}
    open_rows = store.connection.execute(
        "SELECT notice_id, rule_code FROM review_items "
        "WHERE status='open' AND rule_code LIKE 'BOND_%'"
    ).fetchall()
    for row in open_rows:
        if (row["notice_id"], row["rule_code"]) not in current:
            store.connection.execute(
                "UPDATE review_items SET status='resolved', resolved_run=? "
                "WHERE notice_id=? AND rule_code=?",
                (run_id, row["notice_id"], row["rule_code"]),
            )
    now = now_iso()
    for bid in bids:
        if bid.status == COVERED:
            continue
        store.connection.execute(
            "INSERT INTO review_items(notice_id, rule_code, severity, message, "
            "field_name, status, created_run, last_seen_run) "
            "VALUES (?, ?, ?, ?, ?, 'open', ?, ?) "
            "ON CONFLICT(notice_id, rule_code) DO UPDATE SET severity=excluded.severity, "
            "message=excluded.message, field_name=excluded.field_name, status='open', "
            "last_seen_run=excluded.last_seen_run, resolved_run=NULL",
            (bid.notice_id, f"BOND_{bid.status.upper()}",
             "error" if bid.status == SHORTFALL else "warning",
             f"投标 {bid.bid_id} 保证状态 {bid.status}：{bid.reason}",
             "required_security", run_id, now),
        )


def _persist_bids(bond_store: BondStore, case_id: str, case: dict[str, Any], bids) -> None:
    rows = []
    raw_by_id = {item["bid_id"]: item for item in case["bids"]}
    for row in summarize(bids):
        rows.append(
            {
                "bid_id": row["bid_id"], "notice_id": row["notice_id"],
                "bidder_id": row["bidder_id"], "required_security": row["required_security"],
                "payload": raw_by_id[row["bid_id"]],
                "envelope_status": row["envelope_status"],
                "envelope_reason": row["envelope_reason"],
                "security_status": row["security_status"], "decision": row,
            }
        )
    bond_store.replace_bids(case_id, rows)


def intake_bond_case(store: Store, case: dict[str, Any]) -> dict[str, Any]:
    """Intake a case under the fixed-pledge contract and persist the verdict."""
    case_id = canonical_case_id(case)
    bond_store = BondStore(store)
    bond_store.upsert_case(case, FIXED_PLEDGE)
    bids, views = build_views(store, case)
    reserved = bond_store.instrument_reserved(case_id)
    with store.transaction():
        if not bond_store.ledger(case_id):
            seq = 0
            for pledge in sorted(case["pledges"], key=lambda item: item["pledge_id"]):
                view = views.get(pledge["instrument_id"])
                if view is None or pledge["bid_id"] not in view.usable_for:
                    raise ValueError("posted pledge fails instrument evidence checks")
                seq += 1
                bond_store.append_event(
                    case_id, "intake", seq, "pledge",
                    pledge_id=pledge["pledge_id"], bid_id=pledge["bid_id"],
                    instrument_id=pledge["instrument_id"],
                    amount=Decimal(str(pledge["amount"])), source_action_id=None,
                )
        reserved = bond_store.instrument_reserved(case_id)
        net = bond_store.net_pledges(case_id)
        covered_per_bid: dict[str, Decimal] = {}
        for (bid_id, _instrument_id), amount in net.items():
            covered_per_bid[bid_id] = covered_per_bid.get(bid_id, Decimal("0")) + amount
        residual = [
            bid for bid in bids
            if bid.envelope_status == "eligible"
            and covered_per_bid.get(bid.bid_id, Decimal("0")) < bid.required
        ]
        solution = allocate(residual, views, reserved) if residual else {}
        if solution is None:
            solution = best_effort_allocate(residual, views, reserved)
        _decide(bids, views, solution, net)
        bond_store.replace_instruments(case_id, bids, views, case)
        _persist_bids(bond_store, case_id, case, bids)
        result = {
            "case_id": case_id, "contract_mode": FIXED_PLEDGE,
            "bids": summarize(bids),
            "instruments": _instrument_rows(bond_store, case_id),
            "ledger": bond_store.ledger(case_id),
            "evaluated_at": now_iso(),
        }
        if bond_store.get_action(case_id, _intake_action(case_id, case)["action_id"]) is None:
            bond_store.record_action(case_id, _intake_action(case_id, case), result)
        _sync_review(store, case_id, bids)
    return result


def _intake_action(case_id: str, case: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1, "action_id": f"{case_id}-intake",
        "action_type": "intake_fixed_pledge", "contract_mode": FIXED_PLEDGE,
        "authorization": "signed_fixed_pledge_contract",
        "allocations": [
            {"bid_id": item["bid_id"], "instrument_id": item["instrument_id"],
             "amount": item["amount"]}
            for item in case["pledges"]
        ],
    }


def _instrument_rows(bond_store: BondStore, case_id: str) -> list[dict[str, Any]]:
    rows = bond_store.list_instrument_rows(case_id)
    reserved = bond_store.instrument_reserved(case_id)
    for row in rows:
        face = Decimal(row["face_amount"])
        held = reserved.get(row["instrument_id"], Decimal("0.00"))
        row["reserved_amount"] = str(held.quantize(Decimal("0.01")))
        row["free_amount"] = str((face - held).quantize(Decimal("0.01")))
    return rows


def apply_reallocation(store: Store, case: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    """Apply an explicitly authorized replacement as one atomic ledger event."""
    validate_action(action, case)
    case_id = canonical_case_id(case)
    bond_store = BondStore(store)
    bond_store.require_case(case_id)
    existing = bond_store.check_action_payload(case_id, action)
    if existing is not None:
        return json.loads(existing["result_json"])
    bids, views = build_views(store, case)
    with store.transaction():
        steps = plan_replacement(case, action, bids, views, bond_store)
        seq = max(
            (row["seq"] for row in bond_store.ledger(case_id)), default=0
        )
        for step in steps:
            seq += 1
            bond_store.append_event(
                case_id, action["action_id"], seq, step["event_type"],
                pledge_id=step["pledge_id"], bid_id=step["bid_id"],
                instrument_id=step["instrument_id"], amount=step["amount"],
                source_action_id=step["source_action_id"],
            )
        reserved = bond_store.instrument_reserved(case_id)
        net = bond_store.net_pledges(case_id)
        # Bids already fully covered by the replacement ledger need no further
        # allocation; the solver only fills the remaining uncovered bids.
        covered_per_bid: dict[str, Decimal] = {}
        for (bid_id, _instrument_id), amount in net.items():
            covered_per_bid[bid_id] = covered_per_bid.get(bid_id, Decimal("0")) + amount
        residual = [
            bid for bid in bids
            if bid.envelope_status == "eligible"
            and covered_per_bid.get(bid.bid_id, Decimal("0")) < bid.required
        ]
        solution = allocate(residual, views, reserved) if residual else {}
        if solution is None:
            solution = best_effort_allocate(residual, views, reserved)
        _decide(bids, views, solution, net)
        bond_store.replace_instruments(case_id, bids, views, case)
        _persist_bids(bond_store, case_id, case, bids)
        result = {
            "case_id": case_id, "contract_mode": REALLOCATION,
            "action_id": action["action_id"], "bids": summarize(bids),
            "instruments": _instrument_rows(bond_store, case_id),
            "ledger": bond_store.ledger(case_id),
            "steps": [
                {**step, "amount": str(step["amount"])} for step in steps
            ],
            "evaluated_at": now_iso(),
        }
        bond_store.record_action(case_id, action, result)
        _sync_review(store, case_id, bids)
    return result


def bond_case_summary(store: Store, case_id: str) -> dict[str, Any] | None:
    bond_store = BondStore(store)
    if bond_store.get_case(case_id) is None:
        return None
    return {
        "case": bond_store.get_case(case_id),
        "bids": bond_store.list_bid_decisions(case_id),
        "instruments": _instrument_rows(bond_store, case_id),
        "ledger": bond_store.ledger(case_id),
        "events": [
            dict(row) for row in store.connection.execute(
                "SELECT case_id, action_id, action_type, contract_mode, payload_hash, "
                "created_at FROM bond_events WHERE case_id=? ORDER BY event_seq",
                (case_id,),
            ).fetchall()
        ],
    }
