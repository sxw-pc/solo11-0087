"""Offline intake of synthetic procurement award and delivery evidence."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

from .store import Store


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


def read_contract_case(path: str | Path) -> dict[str, Any]:
    """Validate identities and raw events without accepting milestones."""
    case = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(case, dict) or case.get("version") != 1 or not isinstance(case.get("contract"), dict):
        raise ValueError("contract case requires version 1 and contract")
    contract = case["contract"]
    if not all(contract.get(key) for key in ("contract_id", "notice_id", "supplier_id", "award_at", "award_amount")):
        raise ValueError("contract identity is incomplete")
    _time(contract["award_at"])
    _amount(contract["award_amount"])
    milestones = case.get("milestones")
    events = case.get("events")
    if not isinstance(milestones, list) or not milestones or not isinstance(events, list):
        raise ValueError("milestones and events are required")
    milestone_ids: set[str] = set()
    for item in milestones:
        if not isinstance(item, dict) or not all(item.get(key) for key in ("milestone_id", "amount", "due_at")):
            raise ValueError("milestone is incomplete")
        if item["milestone_id"] in milestone_ids:
            raise ValueError("duplicate milestone id")
        milestone_ids.add(item["milestone_id"])
        _amount(item["amount"])
        _time(item["due_at"])
    event_ids: set[str] = set()
    for event in events:
        if not isinstance(event, dict) or not all(event.get(key) for key in
            ("event_id", "contract_id", "milestone_id", "kind", "at")):
            raise ValueError("delivery evidence is incomplete")
        if event["event_id"] in event_ids or event["contract_id"] != contract["contract_id"]:
            raise ValueError("delivery evidence identity is invalid")
        if event["milestone_id"] not in milestone_ids:
            raise ValueError("delivery evidence references unknown milestone")
        event_ids.add(event["event_id"])
        _time(event["at"])
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
