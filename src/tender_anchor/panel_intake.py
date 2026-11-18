"""Offline intake of synthetic tender evaluation ballots and disclosures.

``read_panel_case`` validates identity, time, and scope.  ``preview_panel_case``
links the validated case to captured tender facts and returns a deterministic
disclosure-aware tally without writing.  ``record_panel_case`` persists the
signed first review, each authorised contract re-review, the resulting award,
and any correction evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .panel_case import load_panel_case, normalize_case
from .panel_rules import evaluate_case
from .panel_store import record_panel_case as _record
from .store import Store


def read_panel_case(path: str | Path) -> dict[str, Any]:
    """Validate a local panel case without computing an award."""
    return load_panel_case(path)


def preview_panel_case(store: Store, case: dict[str, Any]) -> dict[str, Any]:
    """Link ballots to captured tender facts and retain all source votes."""
    notice = store.get_notice(case["notice_id"])
    if notice is None or notice["kind"] != "tender":
        return {"status": "unverifiable", "reason": "tender_notice_missing"}
    normalized = normalize_case(case)
    evaluation = evaluate_case(normalized, notice)
    return {
        "status": "evidence_ready",
        "notice_source_hash": notice["source_hash"],
        "bids": list(evaluation["decisions"].values()),
        "reviewers": normalized["reviewers"],
        "ballots": normalized["ballots"],
        "disclosures": normalized["disclosures"],
        "contracts": normalized["contracts"],
        "initial": evaluation["initial"],
        "rereviews": evaluation["rereviews"],
        "governing_contract_id": evaluation["governing_contract_id"],
        "final_award": evaluation["final_award"],
    }


def record_panel_case(store: Store, case: dict[str, Any]) -> dict[str, Any]:
    """Persist the case, reviews, award, and corrections atomically."""
    notice = store.get_notice(case["notice_id"])
    if notice is None or notice["kind"] != "tender":
        return {"status": "unverifiable", "reason": "tender_notice_missing"}
    normalized = normalize_case(case)
    return {"status": "recorded", **_record(store, normalized, notice)}
