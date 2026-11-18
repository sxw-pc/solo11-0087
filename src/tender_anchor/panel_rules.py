"""Orchestrate the signed first review, contract re-reviews, and final award."""

from __future__ import annotations

from typing import Any

from .panel_base import parse_time
from .panel_contract import apply_contract
from .panel_votes import (
    STAGE_INITIAL, assemble_review, build_bid_rows, build_votes, eligible_bids,
    score_decisions,
)


def _pending(case: dict[str, Any], reason: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """Hold the award when the evidence on hand cannot yet settle it."""
    return {
        **fallback,
        "stage": "pending",
        "contract_id": "",
        "status": "pending_verification",
        "reason": reason,
        "issues": sorted(set([*fallback["issues"], reason])),
        "winner_bid_id": "",
        "winner_bidder_id": "",
        "winner_amount": "",
        "winning_mean": None,
        "winning_exact": None,
        "winning_display_mean": None,
    }


def evaluate_initial(
    case: dict[str, Any], decisions: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Score the signed first review using only evidence known at signing."""
    quorum = int(case["scoring"]["quorum"])
    cutoff = parse_time(case["initial_review_at"])
    initial_ids = {
        item["reviewer_id"] for item in case["reviewers"] if item["role"] == "initial"
    }
    eligibility = eligible_bids(decisions)
    known = [
        item for item in case["disclosures"] if parse_time(item["at"]) <= cutoff
    ]
    late = [
        item for item in case["disclosures"] if parse_time(item["at"]) > cutoff
    ]
    conflicts = {(item["reviewer_id"], item["bid_id"]) for item in known}
    included: set[str] = set()
    reasons: dict[str, str] = {}
    for ballot in case["ballots"]:
        if ballot["bid_id"] not in eligibility:
            reasons[ballot["ballot_id"]] = "bid_ineligible"
        elif ballot["reviewer_id"] not in initial_ids:
            reasons[ballot["ballot_id"]] = "not_in_initial_panel"
        elif parse_time(ballot["at"]) > cutoff:
            reasons[ballot["ballot_id"]] = "cast_after_initial_review"
        elif (ballot["reviewer_id"], ballot["bid_id"]) in conflicts:
            reasons[ballot["ballot_id"]] = "conflict_disclosure"
        else:
            included.add(ballot["ballot_id"])
    votes = build_votes(case["ballots"], included, reasons)
    rows = build_bid_rows(case["bids"], decisions, votes, quorum)
    issues: list[str] = []
    if not eligibility:
        issues.append("no_eligible_bids")
    if len(initial_ids) != quorum:
        issues.append("initial_panel_size_mismatch")
    if conflicts:
        issues.append("disclosure_requires_contract")
    if any(row["mean_score"] is None for row in rows if row["status"] == "eligible"):
        issues.append("quorum_short")
    return assemble_review(
        case,
        stage=STAGE_INITIAL,
        contract_id="",
        effective_at=cutoff.isoformat(),
        known=known,
        late=late,
        votes=votes,
        rows=rows,
        issues=issues,
        missing=[],
        contract=None,
    )


def evaluate_case(case: dict[str, Any], notice: dict[str, Any]) -> dict[str, Any]:
    """Return the initial review, every contract re-review, and final award."""
    decisions = score_decisions(notice, case["bids"])
    initial = evaluate_initial(case, decisions)
    rereviews = [
        apply_contract(case, contract, decisions)
        for contract in sorted(case["contracts"], key=lambda item: item["contract_id"])
    ]
    governing_id = case.get("governing_contract_id", "")
    governing = next(
        (item for item in rereviews if item["contract_id"] == governing_id), None
    )
    for review in rereviews:
        review["is_governing"] = review is governing
    initial["is_governing"] = governing is None
    if governing is not None:
        effective = governing
    elif case["contracts"]:
        effective = _pending(case, "governing_contract_required", initial)
    elif initial["late_disclosure_ids"]:
        effective = _pending(case, "disclosure_requires_contract", initial)
    else:
        effective = initial
    final = {
        "case_id": case["case_id"],
        "notice_id": case["notice_id"],
        "round_id": case["round_id"],
        "status": effective["status"],
        "reason": effective["reason"],
        "stage": effective["stage"],
        "contract_id": effective["contract_id"],
        "governing_contract_id": governing_id,
        "winner_bid_id": effective["winner_bid_id"],
        "winner_bidder_id": effective["winner_bidder_id"],
        "winner_amount": effective["winner_amount"],
        "winning_mean": effective["winning_mean"],
        "winning_exact": effective["winning_exact"],
        "winning_display_mean": effective["winning_display_mean"],
        "initial_status": initial["status"],
        "initial_winner_bidder_id": initial["winner_bidder_id"],
        "late_disclosure_ids": initial["late_disclosure_ids"],
    }
    return {
        "case_id": case["case_id"],
        "notice_id": case["notice_id"],
        "round_id": case["round_id"],
        "decisions": decisions,
        "initial": initial,
        "rereviews": rereviews,
        "reviews": [initial, *rereviews],
        "governing_contract_id": governing_id,
        "final_award": final,
    }
