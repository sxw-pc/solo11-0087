"""Authorised recusal contracts that resolve disclosure conflicts.

Only two fully authorised routes exist.  Each must keep ``quorum`` independent
effective ballots for every scoreable bid, exclude conflicted ballots, and use
the single scoring rule locked by the case.  A partial recusal may never be
improvised: if the case did not authorise it, the reviewer's ballots are not
silently dropped to favour one bid.
"""

from __future__ import annotations

from typing import Any

from .panel_base import parse_time
from .panel_votes import (
    STAGE_REREVIEW, assemble_review, build_bid_rows, build_votes, eligible_bids,
)


def _scope(
    case: dict[str, Any],
    contract: dict[str, Any],
    basis: list[dict[str, Any]],
    scorable: set[str],
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """Return ``(recused_pairs, required_replacement_pairs)`` for *scoreable* bids.

    Validation already guarantees that a whole-round scope covers every bid of
    the round and an absolute scope only strikes disclosed pairs, so this
    function recomputes the same scope deterministically from the contract kind
    and its disclosure basis.
    """
    recused: set[tuple[str, str]] = set()
    required: set[tuple[str, str]] = set()
    alternate_map = contract["alternate_map"]
    declared = contract.get("scope")
    if contract["kind"] == "whole_round_recusal":
        for reviewer in {item["reviewer_id"] for item in basis}:
            for bid_id in scorable:
                recused.add((reviewer, bid_id))
    elif declared:
        # An absolute contract's declared scope *is* its authorisation
        # boundary; only those disclosed pairs may be struck.
        recused = {
            (reviewer, bid_id) for reviewer, bid_id in declared
            if bid_id in scorable
        }
    else:
        for item in basis:
            if item["bid_id"] in scorable:
                recused.add((item["reviewer_id"], item["bid_id"]))
    for reviewer, bid_id in recused:
        alternate = alternate_map.get(reviewer)
        if alternate:
            required.add((alternate, bid_id))
    return recused, required


def apply_contract(
    case: dict[str, Any], contract: dict[str, Any], decisions: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    quorum = int(case["scoring"]["quorum"])
    initial_at = parse_time(case["initial_review_at"])
    initial_ids = {item["reviewer_id"] for item in case["reviewers"] if item["role"] == "initial"}
    availability = {item["reviewer_id"]: item["available"] for item in case["reviewers"]}
    scorable = eligible_bids(decisions)
    disclosures = {item["disclosure_id"]: item for item in case["disclosures"]}
    basis = [disclosures[item] for item in contract["basis_disclosure_ids"]]
    authorized_at = parse_time(contract["authorized_at"], "contract authorized_at")
    recused, required = _scope(case, contract, basis, scorable)
    alternate_ids = set(contract["alternate_map"].values())
    candidate_reviewers = initial_ids | alternate_ids
    latest_evidence = max(
        [initial_at, authorized_at, *[parse_time(item["at"]) for item in basis],
         *[parse_time(ballot["at"]) for ballot in case["ballots"]
           if ballot["reviewer_id"] in candidate_reviewers]]
    )
    known = [
        item for item in case["disclosures"]
        if parse_time(item["at"]) <= latest_evidence
    ]
    late = [
        item for item in case["disclosures"]
        if initial_at < parse_time(item["at"]) <= latest_evidence
    ]
    known_pairs = {(item["reviewer_id"], item["bid_id"]) for item in known}
    issues: list[str] = []
    missing: list[dict[str, str]] = []
    if authorized_at < max(parse_time(item["at"]) for item in basis):
        issues.append("contract_unauthorised")
    for item in basis:
        if item["reviewer_id"] not in contract["alternate_map"]:
            issues.append("contract_unauthorised")
    if {
        pair for pair in known_pairs
        if pair[1] in scorable and pair not in recused
    }:
        issues.append("uncovered_disclosure")
    # A disclosure arriving after this contract's evidence cutoff is not part of
    # this re-review; it must be resolved by a later authorised contract rather
    # than silently ignored.
    if any(
        item["bid_id"] in scorable and parse_time(item["at"]) > latest_evidence
        for item in case["disclosures"]
    ):
        issues.append("disclosure_after_review")
    included: set[str] = set()
    reasons: dict[str, str] = {}
    for ballot in case["ballots"]:
        bid_id = ballot["bid_id"]
        reviewer = ballot["reviewer_id"]
        pair = (reviewer, bid_id)
        cast_at = parse_time(ballot["at"])
        if bid_id not in scorable:
            reasons[ballot["ballot_id"]] = "bid_ineligible"
        elif reviewer in initial_ids:
            if pair in recused:
                reasons[ballot["ballot_id"]] = "conflict_disclosure"
            elif cast_at > latest_evidence:
                reasons[ballot["ballot_id"]] = "cast_after_review"
            else:
                included.add(ballot["ballot_id"])
        elif reviewer in alternate_ids:
            if pair not in required:
                reasons[ballot["ballot_id"]] = "outside_contract_scope"
            elif not availability.get(reviewer, True):
                reasons[ballot["ballot_id"]] = "alternate_unavailable"
            elif cast_at > latest_evidence:
                reasons[ballot["ballot_id"]] = "cast_after_review"
            else:
                included.add(ballot["ballot_id"])
        else:
            reasons[ballot["ballot_id"]] = "not_in_contract_panel"
    votes = build_votes(case["ballots"], included, reasons)
    present = {
        (vote["reviewer_id"], vote["bid_id"])
        for vote in votes if vote["included"]
    }
    for reviewer, bid_id in sorted(required):
        if (reviewer, bid_id) in present:
            continue
        missing.append({"reviewer_id": reviewer, "bid_id": bid_id})
        issues.append(
            "alternate_unavailable" if not availability.get(reviewer, True)
            else "replacement_ballot_missing"
        )
    if len(initial_ids) != quorum:
        issues.append("initial_panel_size_mismatch")
    if not scorable:
        issues.append("no_eligible_bids")
    rows = build_bid_rows(case["bids"], decisions, votes, quorum)
    if any(row["mean_score"] is None for row in rows if row["status"] == "eligible"):
        issues.append("quorum_short")
    return assemble_review(
        case,
        stage=STAGE_REREVIEW,
        contract_id=contract["contract_id"],
        effective_at=latest_evidence.isoformat(),
        known=known,
        late=late,
        votes=votes,
        rows=rows,
        issues=issues,
        missing=missing,
        contract=contract,
    )
