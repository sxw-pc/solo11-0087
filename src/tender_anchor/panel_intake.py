"""Offline intake of synthetic tender evaluation ballots and disclosures."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .bid_intake import evaluate_bid
from .store import Store


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("panel event time must be text")
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("panel event time is invalid") from exc
    if instant.tzinfo is None:
        raise ValueError("panel event time requires timezone")
    return instant


def read_panel_case(path: str | Path) -> dict[str, Any]:
    """Validate local ballot identities without computing an award."""
    case = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(case, dict) or case.get("version") != 1 or not case.get("notice_id"):
        raise ValueError("panel case requires version 1 and notice_id")
    for key in ("bids", "reviewers", "ballots", "disclosures"):
        if not isinstance(case.get(key), list):
            raise ValueError(f"panel case requires {key}")
    bid_ids = {bid.get("bid_id") for bid in case["bids"] if isinstance(bid, dict)}
    reviewer_ids = {reviewer.get("reviewer_id") for reviewer in case["reviewers"] if isinstance(reviewer, dict)}
    if None in bid_ids or None in reviewer_ids or len(bid_ids) != len(case["bids"]) or len(reviewer_ids) != len(case["reviewers"]):
        raise ValueError("bid or reviewer identity is invalid")
    ballot_ids: set[str] = set()
    for ballot in case["ballots"]:
        if not isinstance(ballot, dict) or not all(ballot.get(key) for key in
            ("ballot_id", "reviewer_id", "bid_id", "at")):
            raise ValueError("ballot is incomplete")
        if ballot["ballot_id"] in ballot_ids or ballot["reviewer_id"] not in reviewer_ids or ballot["bid_id"] not in bid_ids:
            raise ValueError("ballot identity is invalid")
        ballot_ids.add(ballot["ballot_id"])
        if not isinstance(ballot.get("score"), int) or isinstance(ballot["score"], bool) or not 0 <= ballot["score"] <= 100:
            raise ValueError("ballot score must be 0..100")
        _time(ballot["at"])
    for disclosure in case["disclosures"]:
        if not isinstance(disclosure, dict) or disclosure.get("reviewer_id") not in reviewer_ids or disclosure.get("bid_id") not in bid_ids:
            raise ValueError("disclosure identity is invalid")
        _time(disclosure.get("at"))
    return case


def preview_panel_case(store: Store, case: dict[str, Any]) -> dict[str, Any]:
    """Link ballots to captured tender facts and retain all source votes."""
    notice = store.get_notice(case["notice_id"])
    if notice is None or notice["kind"] != "tender":
        return {"status": "unverifiable", "reason": "tender_notice_missing"}
    decisions = [evaluate_bid(notice, bid) for bid in case["bids"]]
    return {
        "status": "evidence_ready",
        "notice_source_hash": notice["source_hash"],
        "bids": decisions,
        "reviewers": case["reviewers"],
        "ballots": case["ballots"],
        "disclosures": case["disclosures"],
    }
