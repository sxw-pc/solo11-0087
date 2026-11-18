"""Pure vote-accounting primitives shared by initial and contract reviews."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from typing import Any

from .bid_intake import evaluate_bid

CENT = Decimal("0.01")
STAGE_INITIAL = "initial"
STAGE_REREVIEW = "rereview"


def mean_of(scores: list[int]) -> Fraction:
    return Fraction(sum(scores), len(scores))


def display_mean(value: Fraction | None) -> str | None:
    if value is None:
        return None
    exact = Decimal(value.numerator) / Decimal(value.denominator)
    return str(exact.quantize(CENT, rounding=ROUND_HALF_UP))


def score_decisions(notice: dict[str, Any], bids: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Re-evaluate bid eligibility against current captured notice terms."""
    return {bid["bid_id"]: evaluate_bid(notice, bid) for bid in bids}


def eligible_bids(decisions: dict[str, dict[str, Any]]) -> set[str]:
    return {bid_id for bid_id, item in decisions.items() if item["status"] == "eligible"}


def build_votes(
    ballots: list[dict[str, Any]],
    included: set[str],
    reasons: dict[str, str],
) -> list[dict[str, Any]]:
    return [
        {
            "ballot_id": ballot["ballot_id"],
            "reviewer_id": ballot["reviewer_id"],
            "bid_id": ballot["bid_id"],
            "score": ballot["score"],
            "cast_at": ballot["at"],
            "included": ballot["ballot_id"] in included,
            "exclusion_reason": "" if ballot["ballot_id"] in included
            else reasons.get(ballot["ballot_id"], "not_selected"),
        }
        for ballot in sorted(ballots, key=lambda item: (item["bid_id"], item["reviewer_id"]))
    ]


def build_bid_rows(
    bids: list[dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
    votes: list[dict[str, Any]],
    quorum: int,
) -> list[dict[str, Any]]:
    rows = []
    for bid in sorted(bids, key=lambda item: item["bid_id"]):
        decision = decisions[bid["bid_id"]]
        effective = [
            vote for vote in votes
            if vote["bid_id"] == bid["bid_id"] and vote["included"]
        ]
        reviewers = [vote["reviewer_id"] for vote in effective]
        complete = (
            decision["status"] == "eligible"
            and len(effective) == quorum
            and len(set(reviewers)) == quorum
        )
        score = mean_of([vote["score"] for vote in effective]) if complete else None
        rows.append(
            {
                "bid_id": bid["bid_id"],
                "bidder_id": bid["bidder_id"],
                "amount": bid["amount"],
                "status": decision["status"],
                "reason": decision["reason"],
                "included_ballot_ids": sorted(vote["ballot_id"] for vote in effective),
                "included_reviewer_ids": sorted(reviewers),
                "mean_score": score,
                "display_mean": display_mean(score),
            }
        )
    return rows


def rank_award(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Rank complete rows only; ties resolve by lower price, then bid id."""
    complete = [row for row in rows if row["mean_score"] is not None]
    complete.sort(
        key=lambda row: (-row["mean_score"], Decimal(row["amount"]), row["bid_id"])
    )
    for index, row in enumerate(complete):
        row["rank"] = index + 1
    for row in rows:
        row.setdefault("rank", None)
    if not complete:
        return None
    best = complete[0]
    return {
        "winner_bid_id": best["bid_id"],
        "winner_bidder_id": best["bidder_id"],
        "winner_amount": best["amount"],
        "winning_mean": best["mean_score"],
        "winning_display_mean": best["display_mean"],
    }


def assemble_review(
    case: dict[str, Any],
    *,
    stage: str,
    contract_id: str,
    effective_at: str,
    known: list[dict[str, Any]],
    late: list[dict[str, Any]],
    votes: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    issues: list[str],
    missing: list[dict[str, str]],
    contract: dict[str, Any] | None,
) -> dict[str, Any]:
    award = rank_award(rows) if not issues else None
    shown_award = dict(award) if award else None
    for row in rows:
        exact = row.get("mean_score")
        row["mean_exact"] = str(exact) if exact is not None else None
        row["mean_score"] = display_mean(exact)
    if shown_award is not None:
        exact = shown_award["winning_mean"]
        shown_award["winning_exact"] = str(exact)
        shown_award["winning_mean"] = display_mean(exact)
        shown_award["winning_display_mean"] = shown_award["winning_mean"]
    decided = shown_award is not None and not issues
    return {
        "case_id": case["case_id"],
        "notice_id": case["notice_id"],
        "round_id": case["round_id"],
        "stage": stage,
        "contract_id": contract_id,
        "status": "decided" if decided else "pending_verification",
        "reason": issues[0] if issues else "",
        "issues": sorted(set(issues)),
        "effective_at": effective_at,
        "quorum": int(case["scoring"]["quorum"]),
        "scoring_rule": case["scoring"]["rule"],
        "scoring_rule_id": case["scoring"]["rule_id"],
        "known_disclosure_ids": sorted(item["disclosure_id"] for item in known),
        "late_disclosure_ids": sorted(item["disclosure_id"] for item in late),
        "votes": votes,
        "bids": rows,
        "missing_replacements": missing,
        "winner_bid_id": shown_award["winner_bid_id"] if decided else "",
        "winner_bidder_id": shown_award["winner_bidder_id"] if decided else "",
        "winner_amount": shown_award["winner_amount"] if decided else "",
        "winning_mean": shown_award["winning_mean"] if decided else None,
        "winning_exact": shown_award["winning_exact"] if decided else None,
        "winning_display_mean": shown_award["winning_display_mean"] if decided else None,
        "contract": contract,
    }
