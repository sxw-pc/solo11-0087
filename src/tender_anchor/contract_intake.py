"""Offline intake of synthetic procurement award and delivery evidence."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

from .store import Store

CONTRACT_REVIEW_SCHEMA_VERSION = 1

# The two explicitly authorized acceptance contracts (验收合同).
PROFILES = {
    # 分段合同 + 条件验收：按件签发部分应付；依赖未满足前的事前检验保留
    # provisional 状态，待依赖里程碑全部合格后生效。
    "segmented_conditional": {"payment": "per_unit", "provisional": "conditional"},
    # 整段合同 + 严格顺序：里程碑整段结算；依赖未满足前的事前检验不可采信，
    # 必须在硬件全部合格后重新检验，不得改写旧证据时间。
    "whole_strict": {"payment": "whole_milestone", "provisional": "strict"},
}

MILESTONE_KINDS = ("hardware", "commissioning")
EVENT_KINDS = ("delivered", "inspected", "replaced")
INSPECTION_RESULTS = ("provisional_pass", "pass", "fail")

_CONTRACT_DDL = """
CREATE TABLE IF NOT EXISTS contract_cases (
    contract_id TEXT PRIMARY KEY,
    profile TEXT NOT NULL,
    notice_id TEXT NOT NULL,
    identity_json TEXT NOT NULL,
    notice_source_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS contract_events (
    event_id TEXT NOT NULL,
    contract_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    PRIMARY KEY (contract_id, event_id)
);
CREATE TABLE IF NOT EXISTS contract_receipts (
    receipt_id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    milestone_id TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    amount TEXT NOT NULL,
    kind TEXT NOT NULL,
    trigger_event_id TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
"""


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("evidence time must be text")
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("evidence time is invalid") from exc
    if instant.tzinfo is None:
        raise ValueError("evidence time requires timezone")
    return instant


def _amount(value: object) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("contract amount is invalid") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("contract amount must be positive")
    return amount


def _fmt(amount: Decimal) -> str:
    return str(amount.quantize(Decimal("0.01")))


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _check_dependency_cycles(milestones: list[dict[str, Any]]) -> None:
    deps = {item["milestone_id"]: list(item.get("depends_on", [])) for item in milestones}
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(milestone_id: str) -> None:
        if milestone_id in done:
            return
        if milestone_id in visiting:
            raise ValueError("milestone dependency cycle")
        visiting.add(milestone_id)
        for dep in deps[milestone_id]:
            visit(dep)
        visiting.discard(milestone_id)
        done.add(milestone_id)

    for milestone_id in deps:
        visit(milestone_id)


def _validate_event_shape(event: dict[str, Any], milestone: dict[str, Any]) -> None:
    kind = event["kind"]
    if kind == "delivered":
        if milestone["kind"] != "hardware":
            raise ValueError("delivered event requires a hardware milestone")
        units = event.get("units")
        if (
            not isinstance(units, list)
            or not units
            or any(not isinstance(unit, str) or not unit for unit in units)
            or len(set(units)) != len(units)
        ):
            raise ValueError("delivered event requires distinct unit ids")
    elif kind == "replaced":
        if milestone["kind"] != "hardware":
            raise ValueError("replaced event requires a hardware milestone")
        old_unit, new_unit = event.get("old_unit"), event.get("new_unit")
        if (
            not isinstance(old_unit, str)
            or not isinstance(new_unit, str)
            or not old_unit
            or not new_unit
            or old_unit == new_unit
        ):
            raise ValueError("replaced event requires distinct old and new unit ids")
    elif kind == "inspected":
        if milestone["kind"] == "hardware":
            accepted, rejected = event.get("accepted"), event.get("rejected")
            if not isinstance(accepted, list) or not isinstance(rejected, list):
                raise ValueError("inspected event requires accepted and rejected lists")
            if any(not isinstance(unit, str) or not unit for unit in accepted + rejected):
                raise ValueError("inspected event unit ids must be text")
            if len(set(accepted)) != len(accepted) or len(set(rejected)) != len(rejected):
                raise ValueError("inspected event lists contain duplicates")
            if set(accepted) & set(rejected):
                raise ValueError("inspected event cannot accept and reject the same unit")
        elif event.get("result") not in INSPECTION_RESULTS:
            raise ValueError("inspected event result is not supported")


def read_contract_case(path: str | Path) -> dict[str, Any]:
    """Validate identities and raw events without accepting milestones."""
    case = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(case, dict) or case.get("version") != 1 or not isinstance(case.get("contract"), dict):
        raise ValueError("contract case requires version 1 and contract")
    contract = case["contract"]
    if not all(contract.get(key) for key in ("contract_id", "notice_id", "supplier_id", "award_at", "award_amount")):
        raise ValueError("contract identity is incomplete")
    _time(contract["award_at"])
    award = _amount(contract["award_amount"])
    milestones = case.get("milestones")
    events = case.get("events")
    if not isinstance(milestones, list) or not milestones or not isinstance(events, list):
        raise ValueError("milestones and events are required")
    milestone_ids: set[str] = set()
    milestone_map: dict[str, dict[str, Any]] = {}
    for item in milestones:
        if not isinstance(item, dict) or not all(item.get(key) for key in
            ("milestone_id", "kind", "amount", "due_at")):
            raise ValueError("milestone is incomplete")
        if item["milestone_id"] in milestone_ids:
            raise ValueError("duplicate milestone id")
        if item["kind"] not in MILESTONE_KINDS:
            raise ValueError("milestone kind is not supported")
        milestone_ids.add(item["milestone_id"])
        milestone_map[item["milestone_id"]] = item
        amount = _amount(item["amount"])
        _time(item["due_at"])
        if item["kind"] == "hardware":
            units = item.get("units")
            unit_amount = item.get("unit_amount")
            if (
                not isinstance(units, int)
                or isinstance(units, bool)
                or units <= 0
                or not unit_amount
            ):
                raise ValueError("hardware milestone requires positive units and unit amount")
            if _amount(unit_amount) * units != amount:
                raise ValueError("hardware milestone amount does not match units")
        elif item.get("units") is not None or item.get("unit_amount") is not None:
            raise ValueError("commissioning milestone must not define units")
    total = sum((_amount(item["amount"]) for item in milestones), Decimal("0"))
    if total > award:
        raise ValueError("milestone amounts exceed contract award")
    for item in milestones:
        deps = item.get("depends_on", [])
        if (
            not isinstance(deps, list)
            or any(not isinstance(dep, str) or not dep for dep in deps)
            or len(set(deps)) != len(deps)
            or item["milestone_id"] in deps
        ):
            raise ValueError("milestone dependency is invalid")
        for dep in deps:
            if dep not in milestone_ids:
                raise ValueError("milestone dependency references unknown milestone")
    _check_dependency_cycles(milestones)
    event_ids: set[str] = set()
    for event in events:
        if not isinstance(event, dict) or not all(event.get(key) for key in
            ("event_id", "contract_id", "milestone_id", "kind", "at")):
            raise ValueError("delivery evidence is incomplete")
        if event["event_id"] in event_ids or event["contract_id"] != contract["contract_id"]:
            raise ValueError("delivery evidence identity is invalid")
        if event["milestone_id"] not in milestone_ids:
            raise ValueError("delivery evidence references unknown milestone")
        if event["kind"] not in EVENT_KINDS:
            raise ValueError("delivery evidence kind is not supported")
        event_ids.add(event["event_id"])
        _time(event["at"])
        _validate_event_shape(event, milestone_map[event["milestone_id"]])
    return case


def preview_contract_case(store: Store, case: dict[str, Any]) -> dict[str, Any]:
    """Link a local award to a captured tender without adjudicating delivery."""
    contract = case["contract"]
    notice = store.get_notice(contract["notice_id"])
    if notice is None or notice["kind"] != "tender":
        return {"status": "unverifiable", "reason": "tender_notice_missing"}
    material = json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "status": "evidence_ready",
        "contract": contract,
        "notice_source_hash": notice["source_hash"],
        "milestones": case["milestones"],
        "events": case["events"],
        "evidence_sha256": sha256(material.encode("utf-8")).hexdigest(),
    }


# ---------------------------------------------------------------------------
# Acceptance adjudication (验收与里程碑应付裁定)
# ---------------------------------------------------------------------------


def _ensure_contract_tables(store: Store) -> None:
    store.connection.executescript(_CONTRACT_DDL)


def _contract_tables_exist(store: Store) -> bool:
    row = store.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='contract_cases'"
    ).fetchone()
    return row is not None


def _milestone_map(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["milestone_id"]: item for item in case["milestones"]}


def _initial_state(case: dict[str, Any]) -> dict[str, Any]:
    mmap = _milestone_map(case)
    return {
        "units": {mid: {} for mid, item in mmap.items() if item["kind"] == "hardware"},
        "unit_index": {},
        "held": {},
        "effective": {},
        "breached": set(),
        "failed_open": set(),
        "paid_units": set(),
        "net": {mid: Decimal("0") for mid in mmap},
        "complete_since": {},
        "receipt_seq": 0,
    }


def _hardware_complete(state: dict[str, Any], milestone: dict[str, Any]) -> bool:
    units = state["units"][milestone["milestone_id"]]
    accepted = sum(1 for status in units.values() if status == "accepted")
    return accepted == milestone["units"]


def _deps_satisfied(state: dict[str, Any], mmap: dict[str, dict[str, Any]], mid: str) -> bool:
    for dep in mmap[mid].get("depends_on", []):
        dependency = mmap[dep]
        if dependency["kind"] == "hardware":
            if not _hardware_complete(state, dependency):
                return False
        elif dep not in state["effective"]:
            return False
    return True


def _new_receipt(
    state: dict[str, Any],
    contract_id: str,
    milestone_id: str,
    unit_id: str,
    amount: Decimal,
    kind: str,
    event: dict[str, Any],
    detail: dict[str, Any],
) -> dict[str, Any]:
    state["receipt_seq"] += 1
    return {
        "receipt_id": f"RCP-{contract_id}-{state['receipt_seq']:04d}",
        "contract_id": contract_id,
        "milestone_id": milestone_id,
        "unit_id": unit_id,
        "amount": _fmt(amount),
        "kind": kind,
        "trigger_event_id": event["event_id"],
        "effective_at": event["at"],
        "detail": detail,
    }


def _adjust_paid_unit(
    state: dict[str, Any],
    mmap: dict[str, dict[str, Any]],
    conf: dict[str, str],
    event: dict[str, Any],
    mid: str,
    unit_id: str,
) -> list[dict[str, Any]]:
    """Append a negative adjustment for a previously paid unit; never rewrite."""
    unit_amount = _amount(mmap[mid]["unit_amount"])
    if conf["payment"] == "per_unit":
        if (mid, unit_id) not in state["paid_units"]:
            return []
        state["paid_units"].discard((mid, unit_id))
    elif state["net"][mid] <= 0:
        return []
    state["net"][mid] -= unit_amount
    receipt = _new_receipt(
        state, event["contract_id"], mid, unit_id, -unit_amount, "adjustment", event,
        {"adjusts_unit": unit_id, "cause_event_id": event["event_id"]},
    )
    return [receipt]


def _activate_held_evidence(
    state: dict[str, Any],
    mmap: dict[str, dict[str, Any]],
    conf: dict[str, str],
    event: dict[str, Any],
    effects: dict[str, Any],
    receipts: list[dict[str, Any]],
) -> None:
    """Conditional profile: held provisional evidence takes effect once deps pass."""
    if conf["provisional"] != "conditional":
        return
    for held_mid in sorted(list(state["held"])):
        if not _deps_satisfied(state, mmap, held_mid):
            continue
        held_event = state["held"].pop(held_mid)
        state["effective"][held_mid] = {
            "event_id": held_event["event_id"],
            "at": held_event["at"],
        }
        gap = _amount(mmap[held_mid]["amount"]) - state["net"][held_mid]
        if gap > 0:
            state["net"][held_mid] += gap
            receipts.append(_new_receipt(
                state, event["contract_id"], held_mid, "", gap, "payable", event,
                {
                    "evidence_event_id": held_event["event_id"],
                    "evidence_at": held_event["at"],
                    "activated_by": event["event_id"],
                },
            ))
        effects.setdefault("activated_provisional", []).append(held_mid)


def _apply_admitted(
    state: dict[str, Any],
    mmap: dict[str, dict[str, Any]],
    conf: dict[str, str],
    event: dict[str, Any],
) -> tuple[str, str, dict[str, Any], list[dict[str, Any]]]:
    """Fold one admitted event into the state; return status, reason, effects, receipts."""
    kind = event["kind"]
    mid = event["milestone_id"]
    milestone = mmap[mid]
    status, reason = "admitted", ""
    effects: dict[str, Any] = {}
    receipts: list[dict[str, Any]] = []
    if kind == "delivered":
        for unit in event["units"]:
            state["units"][mid][unit] = "delivered"
            state["unit_index"][unit] = mid
        reason = "units_delivered"
        effects["units_delivered"] = sorted(event["units"])
    elif kind == "replaced":
        old_unit, new_unit = event["old_unit"], event["new_unit"]
        previous = state["units"][mid][old_unit]
        if previous == "accepted":
            receipts.extend(_adjust_paid_unit(state, mmap, conf, event, mid, old_unit))
        state["units"][mid][old_unit] = "replaced"
        state["units"][mid][new_unit] = "delivered"
        state["unit_index"][new_unit] = mid
        reason = "unit_replaced"
        effects["replaced"] = {
            "old_unit": old_unit,
            "new_unit": new_unit,
            "old_status": previous,
        }
    elif kind == "inspected" and milestone["kind"] == "hardware":
        unit_amount = _amount(milestone["unit_amount"])
        reason = "inspection_recorded"
        effects.update({"accepted": [], "rejected": [], "unchanged": []})
        for unit in event["accepted"]:
            previous = state["units"][mid][unit]
            if previous == "accepted":
                effects["unchanged"].append(unit)
                continue
            state["units"][mid][unit] = "accepted"
            effects["accepted"].append(unit)
            if conf["payment"] == "per_unit" and (mid, unit) not in state["paid_units"]:
                state["paid_units"].add((mid, unit))
                state["net"][mid] += unit_amount
                receipts.append(_new_receipt(
                    state, event["contract_id"], mid, unit, unit_amount, "payable",
                    event, {"unit": unit},
                ))
        for unit in event["rejected"]:
            previous = state["units"][mid][unit]
            state["units"][mid][unit] = "rejected"
            effects["rejected"].append(unit)
            if previous == "accepted":
                receipts.extend(_adjust_paid_unit(state, mmap, conf, event, mid, unit))
        if conf["payment"] == "whole_milestone" and _hardware_complete(state, milestone):
            gap = _amount(milestone["amount"]) - state["net"][mid]
            if gap > 0:
                state["net"][mid] += gap
                receipts.append(_new_receipt(
                    state, event["contract_id"], mid, "", gap, "payable", event,
                    {"milestone_complete": True},
                ))
    else:  # inspected on a non-hardware milestone
        result = event["result"]
        if result == "fail":
            state["held"].pop(mid, None)
            if mid in state["effective"]:
                state["effective"].pop(mid)
                state["breached"].add(mid)
                settled = state["net"][mid]
                if settled > 0:
                    state["net"][mid] = Decimal("0")
                    receipts.append(_new_receipt(
                        state, event["contract_id"], mid, "", -settled, "adjustment",
                        event, {"adjusts_milestone": mid, "cause_event_id": event["event_id"]},
                    ))
                reason = "milestone_failed_settlement_adjusted"
                effects["milestone_breached"] = mid
            else:
                state["failed_open"].add(mid)
                reason = "milestone_failed"
                effects["milestone_failed"] = mid
        else:
            if mid in state["effective"]:
                reason = "milestone_already_effective"
                effects["milestone_effective"] = mid
            elif _deps_satisfied(state, mmap, mid):
                state["effective"][mid] = {"event_id": event["event_id"], "at": event["at"]}
                state["breached"].discard(mid)
                state["failed_open"].discard(mid)
                gap = _amount(milestone["amount"]) - state["net"][mid]
                if gap > 0:
                    state["net"][mid] += gap
                    receipts.append(_new_receipt(
                        state, event["contract_id"], mid, "", gap, "payable", event,
                        {"evidence_event_id": event["event_id"], "evidence_at": event["at"]},
                    ))
                reason = "milestone_accepted"
                effects["milestone_effective"] = mid
            else:
                # Strict profile is gated in _adjudicate; conditional holds the evidence.
                if mid not in state["held"]:
                    state["held"][mid] = event
                status, reason = "held", "awaiting_dependency"
                effects["milestone_held"] = mid
    if milestone["kind"] == "hardware":
        if _hardware_complete(state, milestone):
            state["complete_since"].setdefault(mid, event["at"])
        else:
            state["complete_since"].pop(mid, None)
    _activate_held_evidence(state, mmap, conf, event, effects, receipts)
    return status, reason, effects, receipts


def _check_admission(
    state: dict[str, Any],
    mmap: dict[str, dict[str, Any]],
    event: dict[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    kind = event["kind"]
    mid = event["milestone_id"]
    milestone = mmap[mid]
    if kind == "delivered":
        known = [unit for unit in event["units"] if unit in state["unit_index"]]
        if known:
            return "unit_already_known", {
                "units": sorted(known),
                "owner_milestone": sorted({state["unit_index"][unit] for unit in known}),
            }
        active = sum(1 for value in state["units"][mid].values() if value != "replaced")
        if active + len(event["units"]) > milestone["units"]:
            return "unit_scope_exceeded", {
                "active_units": active,
                "incoming_units": len(event["units"]),
                "contract_units": milestone["units"],
            }
    elif kind == "replaced":
        old_unit, new_unit = event["old_unit"], event["new_unit"]
        if old_unit not in state["units"][mid]:
            return "unit_not_delivered", {"units": [old_unit]}
        if state["units"][mid][old_unit] == "replaced":
            return "unit_not_active", {"units": [old_unit]}
        if new_unit in state["unit_index"]:
            return "unit_already_known", {
                "units": [new_unit],
                "owner_milestone": [state["unit_index"][new_unit]],
            }
    elif kind == "inspected" and milestone["kind"] == "hardware":
        refs = list(event["accepted"]) + list(event["rejected"])
        unknown = [unit for unit in refs if unit not in state["units"][mid]]
        if unknown:
            return "unit_not_delivered", {"units": sorted(unknown)}
        inactive = [unit for unit in refs if state["units"][mid][unit] == "replaced"]
        if inactive:
            return "unit_not_active", {"units": sorted(inactive)}
    return None


def _adjudicate(
    state: dict[str, Any],
    case: dict[str, Any],
    profile: str,
    event: dict[str, Any],
    notice_source_hash: str,
    seq: int,
    late: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Adjudicate one new event; return decision, receipts, and the next state."""
    mmap = _milestone_map(case)
    conf = PROFILES[profile]
    work = copy.deepcopy(state)
    base = {
        "event_id": event["event_id"],
        "contract_id": event["contract_id"],
        "milestone_id": event["milestone_id"],
        "kind": event["kind"],
        "seq": seq,
        "at": event["at"],
        "late": late,
        "profile": profile,
        "notice_source_hash": notice_source_hash,
    }
    problem = _check_admission(work, mmap, event)
    if problem is None and event["kind"] == "inspected":
        milestone = mmap[event["milestone_id"]]
        if milestone["kind"] != "hardware" and event["result"] in ("pass", "provisional_pass"):
            mid = event["milestone_id"]
            if (
                mid not in work["effective"]
                and not _deps_satisfied(work, mmap, mid)
                and conf["provisional"] == "strict"
            ):
                problem = "dependency_not_satisfied", {
                    "milestone_id": mid,
                    "depends_on": list(milestone.get("depends_on", [])),
                }
    if problem is not None:
        reason, detail = problem
        decision = {**base, "status": "rejected", "reason": reason,
                    "effects": {"detail": detail}, "receipts": []}
        return decision, [], state
    status, reason, effects, receipts = _apply_admitted(work, mmap, conf, event)
    award = _amount(case["contract"]["award_amount"])
    over = sorted(mid for mid, net in work["net"].items()
                  if net > _amount(mmap[mid]["amount"]))
    if over or sum(work["net"].values()) > award:
        decision = {**base, "status": "rejected", "reason": "amount_exceeds_contract_cap",
                    "effects": {"detail": {"milestones": over}}, "receipts": []}
        return decision, [], state
    decision = {**base, "status": status, "reason": reason,
                "effects": effects, "receipts": receipts}
    return decision, receipts, work


def _fold(
    case: dict[str, Any],
    profile: str,
    event_rows: list[Any],
    receipt_seq: int,
) -> dict[str, Any]:
    """Rebuild adjudication state by replaying admitted events in import order."""
    state = _initial_state(case)
    mmap = _milestone_map(case)
    conf = PROFILES[profile]
    for row in event_rows:
        decision = json.loads(row["decision_json"])
        if decision["status"] == "rejected":
            continue
        event = json.loads(row["payload_json"])
        _apply_admitted(state, mmap, conf, event)
    # Folded receipts are throwaway; the persisted ledger owns the sequence.
    state["receipt_seq"] = receipt_seq
    return state


def _receipt_from_row(row: Any) -> dict[str, Any]:
    return {
        "receipt_id": row["receipt_id"],
        "contract_id": row["contract_id"],
        "milestone_id": row["milestone_id"],
        "unit_id": row["unit_id"],
        "amount": row["amount"],
        "kind": row["kind"],
        "trigger_event_id": row["trigger_event_id"],
        "effective_at": row["effective_at"],
        "detail": json.loads(row["detail_json"]),
    }


def intake_contract_case(store: Store, case: dict[str, Any]) -> dict[str, Any]:
    """Adjudicate delivery evidence for an authorized acceptance contract.

    Events are admitted only while the linked tender notice stays captured and
    the local award identity matches its first import. Adjudication is
    append-only: re-imported events replay their persisted decisions, changed
    payloads conflict, and later evidence adds new receipts/adjustments without
    rewriting issued ones.
    """
    _ensure_contract_tables(store)
    contract = case["contract"]
    profile = contract.get("acceptance_profile")
    if not profile:
        raise ValueError("acceptance profile is required")
    if profile not in PROFILES:
        raise ValueError("acceptance profile is not authorized")
    notice = store.get_notice(contract["notice_id"])
    if notice is None or notice["kind"] != "tender":
        return {
            "status": "unverifiable",
            "reason": "tender_notice_missing",
            "contract_id": contract["contract_id"],
            "profile": profile,
            "decisions": [],
            "review": None,
        }
    identity = _canonical({"contract": contract, "milestones": case["milestones"]})
    row = store.connection.execute(
        "SELECT identity_json FROM contract_cases WHERE contract_id=?",
        (contract["contract_id"],),
    ).fetchone()
    if row is not None and row["identity_json"] != identity:
        raise ValueError("contract identity was used with different terms")
    if row is None:
        store.connection.execute(
            "INSERT INTO contract_cases VALUES (?, ?, ?, ?, ?)",
            (contract["contract_id"], profile, contract["notice_id"],
             identity, notice["source_hash"]),
        )
    event_rows = store.connection.execute(
        "SELECT * FROM contract_events WHERE contract_id=? ORDER BY seq",
        (contract["contract_id"],),
    ).fetchall()
    receipt_rows = store.connection.execute(
        "SELECT * FROM contract_receipts WHERE contract_id=? ORDER BY receipt_id",
        (contract["contract_id"],),
    ).fetchall()
    state = _fold(case, profile, event_rows, len(receipt_rows))
    persisted = {row["event_id"]: row for row in event_rows}
    max_prior_at: datetime | None = None
    for row in event_rows:
        instant = _time(json.loads(row["payload_json"])["at"])
        if max_prior_at is None or instant > max_prior_at:
            max_prior_at = instant
    seq = max((row["seq"] for row in event_rows), default=0)
    decisions: list[dict[str, Any]] = []
    ordered = sorted(case["events"], key=lambda item: (_time(item["at"]), item["event_id"]))
    for event in ordered:
        existing = persisted.get(event["event_id"])
        payload = _canonical(event)
        if existing is not None:
            if existing["payload_json"] != payload:
                raise ValueError("event id was used with different terms")
            decisions.append(json.loads(existing["decision_json"]))
            continue
        seq += 1
        late = max_prior_at is not None and _time(event["at"]) < max_prior_at
        decision, receipts, state = _adjudicate(
            state, case, profile, event, notice["source_hash"], seq, late,
        )
        store.connection.execute(
            "INSERT INTO contract_events VALUES (?, ?, ?, ?, ?)",
            (event["event_id"], event["contract_id"], seq, payload,
             json.dumps(decision, ensure_ascii=False, sort_keys=True)),
        )
        for receipt in receipts:
            store.connection.execute(
                "INSERT INTO contract_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (receipt["receipt_id"], receipt["contract_id"], receipt["milestone_id"],
                 receipt["unit_id"], receipt["amount"], receipt["kind"],
                 receipt["trigger_event_id"], receipt["effective_at"],
                 json.dumps(receipt["detail"], ensure_ascii=False, sort_keys=True)),
            )
        decisions.append(decision)
    store.connection.commit()
    return {
        "status": "adjudicated",
        "contract_id": contract["contract_id"],
        "profile": profile,
        "notice_id": contract["notice_id"],
        "notice_source_hash": notice["source_hash"],
        "decisions": decisions,
        "review": build_contract_review(store, contract["contract_id"]),
    }


def build_contract_review(store: Store, contract_id: str) -> dict[str, Any]:
    """Rebuild the checkable review export for one adjudicated contract."""
    _ensure_contract_tables(store)
    row = store.connection.execute(
        "SELECT * FROM contract_cases WHERE contract_id=?", (contract_id,),
    ).fetchone()
    if row is None:
        raise ValueError("unknown contract id")
    identity = json.loads(row["identity_json"])
    case = {"contract": identity["contract"], "milestones": identity["milestones"]}
    contract = case["contract"]
    profile = row["profile"]
    mmap = _milestone_map(case)
    event_rows = store.connection.execute(
        "SELECT * FROM contract_events WHERE contract_id=? ORDER BY seq", (contract_id,),
    ).fetchall()
    receipt_rows = store.connection.execute(
        "SELECT * FROM contract_receipts WHERE contract_id=? ORDER BY receipt_id",
        (contract_id,),
    ).fetchall()
    state = _fold(case, profile, event_rows, len(receipt_rows))
    notice = store.get_notice(contract["notice_id"])
    decisions = [json.loads(item["decision_json"]) for item in event_rows]
    receipts = [_receipt_from_row(item) for item in receipt_rows]
    rejected_dependency = {
        decision["milestone_id"]
        for decision in decisions
        if decision["status"] == "rejected" and decision["reason"] == "dependency_not_satisfied"
    }
    units_ledger: list[dict[str, Any]] = []
    milestones_review: list[dict[str, Any]] = []
    outstanding: list[dict[str, Any]] = []
    for mid in sorted(mmap):
        milestone = mmap[mid]
        net = state["net"][mid]
        entry: dict[str, Any] = {
            "milestone_id": mid,
            "kind": milestone["kind"],
            "amount": _fmt(_amount(milestone["amount"])),
            "net_paid": _fmt(net),
            "depends_on": list(milestone.get("depends_on", [])),
            "dependency_satisfied": _deps_satisfied(state, mmap, mid),
        }
        remaining = _amount(milestone["amount"]) - net
        reasons: list[str] = []
        if milestone["kind"] == "hardware":
            units = state["units"][mid]
            grouped = {
                name: sorted(unit for unit, value in units.items() if value == name)
                for name in ("accepted", "rejected", "delivered", "replaced")
            }
            entry["units"] = grouped
            entry["completed_at"] = state["complete_since"].get(mid, "")
            entry["status"] = (
                "complete" if _hardware_complete(state, milestone) else "open"
            )
            for unit in sorted(units):
                unit_net = sum(
                    (Decimal(receipt["amount"]) for receipt in receipts
                     if receipt["milestone_id"] == mid and receipt["unit_id"] == unit),
                    Decimal("0"),
                )
                units_ledger.append({
                    "milestone_id": mid,
                    "unit_id": unit,
                    "status": units[unit],
                    "net_paid": _fmt(unit_net),
                })
            if remaining > 0:
                if grouped["rejected"]:
                    reasons.append("awaiting_replacement")
                if grouped["delivered"]:
                    reasons.append("awaiting_inspection")
                active = len(units) - len(grouped["replaced"])
                if active < milestone["units"]:
                    reasons.append("awaiting_delivery")
                if not reasons:
                    reasons.append("awaiting_completion")
                outstanding.append({
                    "milestone_id": mid,
                    "amount": _fmt(remaining),
                    "reasons": reasons,
                    "units": {"rejected": grouped["rejected"], "delivered": grouped["delivered"]},
                })
        else:
            if mid in state["effective"]:
                status = "effective"
            elif mid in state["held"]:
                status = "held"
            elif mid in state["breached"]:
                status = "breached"
            elif mid in state["failed_open"]:
                status = "failed"
            else:
                status = "pending"
            entry["status"] = status
            entry["effective"] = state["effective"].get(mid)
            held = state["held"].get(mid)
            entry["held_evidence"] = (
                {"event_id": held["event_id"], "at": held["at"]} if held else None
            )
            if remaining > 0:
                if status == "held":
                    reasons.append("awaiting_dependency")
                elif status == "breached":
                    reasons.append("settlement_adjustment")
                elif status == "failed":
                    reasons.append("inspection_failed")
                elif mid in rejected_dependency:
                    reasons.append("requires_inspection")
                else:
                    reasons.append("awaiting_inspection")
                outstanding.append({
                    "milestone_id": mid,
                    "amount": _fmt(remaining),
                    "reasons": reasons,
                })
        milestones_review.append(entry)
    payable_total = sum(state["net"].values(), Decimal("0"))
    outstanding_total = sum(
        (Decimal(item["amount"]) for item in outstanding), Decimal("0")
    )
    verified = notice is not None and notice["kind"] == "tender"
    return {
        "schema_version": CONTRACT_REVIEW_SCHEMA_VERSION,
        "contract_id": contract_id,
        "profile": profile,
        "notice_id": contract["notice_id"],
        "notice_source_hash": notice["source_hash"] if notice else "",
        "notice_status": "verified" if verified else "unverifiable",
        "identity_sha256": sha256(row["identity_json"].encode("utf-8")).hexdigest(),
        "award_amount": _fmt(_amount(contract["award_amount"])),
        "events": decisions,
        "units": units_ledger,
        "milestones": milestones_review,
        "receipts": receipts,
        "totals": {
            "award_amount": _fmt(_amount(contract["award_amount"])),
            "payable_total": _fmt(payable_total),
            "outstanding_total": _fmt(outstanding_total),
        },
        "outstanding": outstanding,
    }


def list_contract_reviews(store: Store) -> list[dict[str, Any]]:
    """Return review exports for every adjudicated contract, ordered by id."""
    if not _contract_tables_exist(store):
        return []
    rows = store.connection.execute(
        "SELECT contract_id FROM contract_cases ORDER BY contract_id"
    ).fetchall()
    return [build_contract_review(store, row["contract_id"]) for row in rows]
