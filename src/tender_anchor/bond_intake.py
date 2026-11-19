"""Local intake of synthetic bid security instruments and bid references."""

from __future__ import annotations

import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .bid_intake import evaluate_bid
from .bond_coverage import (
    evaluate_contract, evidence_fingerprint, gather_evidence, joint_status,
)
from .normalize import canonical_json, stable_hash
from .store import Store

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,59}")


def _amount(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("security amount is invalid") from exc
    if not result.is_finite() or result <= 0 or result.as_tuple().exponent < -2:
        raise ValueError("security amount must be positive cents")
    return result


def _check_id(value: object, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} is invalid")


def read_bond_case(path: str | Path) -> dict[str, Any]:
    """Validate the shape of a local case without assigning collateral."""
    case = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(case, dict) or case.get("version") != 1:
        raise ValueError("bond case requires version 1")
    if not isinstance(case.get("bids"), list) or not isinstance(case.get("instruments"), list):
        raise ValueError("bond case requires bids and instruments")
    if not case["bids"]:
        raise ValueError("bond case requires at least one bid")
    _check_id(case.get("case_id"), "case id")
    bid_ids: set[str] = set()
    for bid in case["bids"]:
        if not isinstance(bid, dict) or not all(bid.get(key) for key in
            ("bid_id", "notice_id", "bidder_id", "received_at", "amount", "required_security")):
            raise ValueError("bond case has incomplete bid")
        if bid["bid_id"] in bid_ids:
            raise ValueError("duplicate bid id")
        bid_ids.add(bid["bid_id"])
        _amount(bid["amount"])
        _amount(bid["required_security"])
        if datetime.fromisoformat(bid["received_at"]).tzinfo is None:
            raise ValueError("bid received_at needs timezone")
    instrument_ids: set[str] = set()
    for instrument in case["instruments"]:
        if not isinstance(instrument, dict) or not all(instrument.get(key) for key in
            ("instrument_id", "bidder_id", "kind", "amount")):
            raise ValueError("bond case has incomplete instrument")
        if instrument["instrument_id"] in instrument_ids:
            raise ValueError("duplicate instrument id")
        instrument_ids.add(instrument["instrument_id"])
        _amount(instrument["amount"])
        if instrument["kind"] not in ("cash", "bank_guarantee"):
            raise ValueError("unsupported instrument kind")
        if instrument.get("valid_until"):
            if datetime.fromisoformat(instrument["valid_until"]).tzinfo is None:
                raise ValueError("valid_until needs timezone")
    if not isinstance(case.get("pledges"), list):
        raise ValueError("bond case requires pledges")
    pledge_ids: set[str] = set()
    for pledge in case["pledges"]:
        if not isinstance(pledge, dict) or not all(pledge.get(key) for key in
            ("pledge_id", "bid_id", "instrument_id", "amount", "posted_at")):
            raise ValueError("bond case has incomplete pledge")
        if pledge["pledge_id"] in pledge_ids or pledge["bid_id"] not in bid_ids or pledge["instrument_id"] not in instrument_ids:
            raise ValueError("pledge identity is invalid")
        pledge_ids.add(pledge["pledge_id"])
        _amount(pledge["amount"])
        if datetime.fromisoformat(pledge["posted_at"]).tzinfo is None:
            raise ValueError("pledge posted_at needs timezone")
    _check_internal_consistency(case)
    _check_contracts(case)
    return case


def _check_internal_consistency(case: dict[str, Any]) -> None:
    bidders = {bid["bid_id"]: bid["bidder_id"] for bid in case["bids"]}
    instruments = {item["instrument_id"]: item for item in case["instruments"]}
    totals: dict[str, Decimal] = {}
    for pledge in case["pledges"]:
        instrument = instruments[pledge["instrument_id"]]
        if instrument["bidder_id"] != bidders[pledge["bid_id"]]:
            raise ValueError("pledge bidder is inconsistent")
        inst_id = instrument["instrument_id"]
        totals[inst_id] = totals.get(inst_id, Decimal("0")) + _amount(pledge["amount"])
        if totals[inst_id] > _amount(instrument["amount"]):
            raise ValueError("pledges exceed instrument face value")


def _check_contracts(case: dict[str, Any]) -> None:
    contracts = case.get("contracts", [])
    if not isinstance(contracts, list):
        raise ValueError("bond case contracts must be a list")
    bidders = {bid["bid_id"]: bid["bidder_id"] for bid in case["bids"]}
    instruments = {item["instrument_id"]: item for item in case["instruments"]}
    contract_ids: set[str] = set()
    for contract in contracts:
        if not isinstance(contract, dict) or not all(contract.get(key) for key in
            ("contract_id", "kind")):
            raise ValueError("bond case has incomplete contract")
        _check_id(contract["contract_id"], "contract id")
        if contract["contract_id"] in contract_ids:
            raise ValueError("duplicate contract id")
        contract_ids.add(contract["contract_id"])
        if contract["kind"] == "fixed_pledge":
            continue
        if contract["kind"] != "redistribution":
            raise ValueError("unsupported contract kind")
        moves = contract.get("moves")
        if not contract.get("authorized_by") or not isinstance(moves, list) or not moves:
            raise ValueError("redistribution contract requires authorized_by and moves")
        for move in moves:
            if not isinstance(move, dict) or not all(move.get(key) for key in
                ("bid_id", "instrument_id", "amount")):
                raise ValueError("redistribution move is incomplete")
            if move["bid_id"] not in bidders or move["instrument_id"] not in instruments:
                raise ValueError("redistribution move references unknown bid or instrument")
            if (bidders[move["bid_id"]] != contract["authorized_by"]
                    or instruments[move["instrument_id"]]["bidder_id"] != contract["authorized_by"]):
                raise ValueError("redistribution is not authorized for this supplier")
            _amount(move["amount"])


def preview_bond_case(store: Store, case: dict[str, Any]) -> dict[str, Any]:
    """Show captured bid decisions and raw security capacity, without pledging it."""
    bids = [evaluate_bid(store.get_notice(bid["notice_id"]), bid) for bid in case["bids"]]
    return {
        "status": "evidence_ready",
        "bids": bids,
        "instruments": case["instruments"],
        "total_security": str(sum((_amount(item["amount"]) for item in case["instruments"]), Decimal("0"))),
    }


def _review_items(case_id: str, contract: dict[str, Any], result: dict[str, Any]) -> list[dict[str, str]]:
    items = []
    for decision in result["bids"]:
        base = {
            "notice_id": decision["notice_id"],
            "field_name": "required_security",
        }
        if decision["security_status"] == "pending_security":
            items.append({
                **base,
                "rule_code": f"BOND_SECURITY_SHORTFALL:{case_id}:{contract['contract_id']}",
                "severity": "warning",
                "message": f"投标 {decision['bid_id']} 保证缺口 {decision['shortfall']} 元，待补保证",
            })
        elif decision["security_status"] == "unverifiable":
            items.append({
                **base,
                "rule_code": f"BOND_SECURITY_UNVERIFIABLE:{case_id}:{contract['contract_id']}",
                "severity": "error",
                "message": f"投标 {decision['bid_id']} 保证证据不足（{decision['security_reason']}），待核",
            })
    return items


def _stored_result(
    contract: dict[str, Any], decisions: list[dict[str, Any]], ledger: list[dict[str, Any]]
) -> dict[str, Any]:
    bids = [row["decision"] for row in decisions]
    return {
        "contract_id": contract["contract_id"],
        "kind": contract["kind"],
        "joint_status": joint_status(bids),
        "bids": bids,
        "allocations": [
            {key: row[key] for key in ("action", "bid_id", "instrument_id", "amount", "source")}
            for row in ledger
        ],
    }


def intake_bond_case(store: Store, case: dict[str, Any]) -> dict[str, Any]:
    """Evaluate every contract of a case and persist the outcomes atomically.

    Re-importing identical materials and actions replays the persisted result;
    the same case or contract id with different content is a conflict. When
    the captured notice or correction evidence changes, the evaluation is
    recomputed and the new ledger is appended as a fresh version.
    """
    case_id = case["case_id"]
    case_hash = stable_hash(case)
    evidence = gather_evidence(store, case)
    evidence_hash = evidence_fingerprint(evidence)
    results: list[dict[str, Any]] = []
    changed = False
    with store.transaction():
        existing = store.get_bond_case(case_id)
        if existing and existing["payload_hash"] != case_hash:
            raise ValueError("bond case id was used with different content")
        for contract in case["contracts"]:
            saved = store.get_bond_contract(contract["contract_id"])
            contract_hash = stable_hash(contract)
            if saved and (saved["payload_hash"] != contract_hash or saved["case_id"] != case_id):
                raise ValueError("bond contract id was used with different terms")
        if existing is None:
            store.record_bond_case(case_id, case_hash, canonical_json(case))
            for contract in case["contracts"]:
                store.record_bond_contract(
                    contract["contract_id"], case_id,
                    stable_hash(contract), canonical_json(contract),
                )
        review_items: list[dict[str, str]] = []
        for contract in case["contracts"]:
            contract_id = contract["contract_id"]
            stored = store.get_bond_decisions(case_id, contract_id)
            if stored and all(row["evidence_hash"] == evidence_hash for row in stored):
                ledger = store.get_bond_ledger(case_id, contract_id)
                results.append(_stored_result(contract, stored, ledger))
                continue
            result = evaluate_contract(case, contract, evidence)
            store.replace_bond_evaluation(
                case_id, contract_id, evidence_hash, result["bids"], result["allocations"]
            )
            review_items.extend(_review_items(case_id, contract, result))
            results.append(result)
            changed = True
        if changed:
            store.sync_bond_review_items(case_id, review_items, f"bond-intake:{case_id}")
    return {
        "case_id": case_id,
        "evidence_hash": evidence_hash,
        "status": "evaluated" if changed else "replayed",
        "contracts": results,
    }
