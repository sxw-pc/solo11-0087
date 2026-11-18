"""Structural, identity, time, and scope validation for offline panel cases.

The reader is deliberately strict: a case is a self-contained procurement
round whose bids, reviewers, ballots, disclosures, and recusal contracts all
belong to the same captured tender notice.  Anything that could mix rounds,
notices, bidders, or silently re-use an identity is rejected here, before any
tally is computed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import PanelError
from .normalize import stable_hash
from .panel_base import (
    REVIEWER_ROLES, SCORING_RULE, amount, index, objects, parse_time, text,
)
from .panel_contract_terms import normalize_contracts


def _normalize_bids(case: dict[str, Any], notice_id: str) -> list[dict[str, Any]]:
    bids = []
    for raw in objects(case, "bids"):
        bid = {
            "bid_id": text(raw.get("bid_id"), "bid_id"),
            "notice_id": text(raw.get("notice_id"), "bid notice_id"),
            "bidder_id": text(raw.get("bidder_id"), "bidder_id"),
            "received_at": text(raw.get("received_at"), "bid received_at"),
            "amount": amount(raw.get("amount"), "bid amount"),
        }
        parse_time(bid["received_at"], "bid received_at")
        if bid["notice_id"] != notice_id:
            raise PanelError(
                "PANEL_SCOPE_MISMATCH", f"bid {bid['bid_id']} belongs to another notice"
            )
        bids.append(bid)
    index(bids, "bid_id", "bid")
    if not bids:
        raise PanelError("PANEL_CASE_INVALID", "panel case requires at least one bid")
    bidders = [bid["bidder_id"] for bid in bids]
    if len(set(bidders)) != len(bidders):
        raise PanelError(
            "PANEL_SCOPE_MISMATCH", "a bidder may not appear in two bids of one round"
        )
    return bids


def _normalize_reviewers(case: dict[str, Any]) -> list[dict[str, Any]]:
    reviewers = []
    for raw in objects(case, "reviewers"):
        role = text(raw.get("role"), "reviewer role")
        if role not in REVIEWER_ROLES:
            raise PanelError("PANEL_CASE_INVALID", f"unknown reviewer role: {role}")
        available = raw.get("available", True)
        if not isinstance(available, bool):
            raise PanelError("PANEL_CASE_INVALID", "reviewer availability must be boolean")
        reviewers.append(
            {
                "reviewer_id": text(raw.get("reviewer_id"), "reviewer_id"),
                "role": role,
                "available": available,
            }
        )
    index(reviewers, "reviewer_id", "reviewer")
    if not reviewers:
        raise PanelError("PANEL_CASE_INVALID", "panel case requires reviewers")
    return reviewers


def _normalize_ballots(
    case: dict[str, Any],
    reviewers: dict[str, dict[str, Any]],
    bids: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    ballots = []
    pairs: set[tuple[str, str]] = set()
    ids: set[str] = set()
    for raw in objects(case, "ballots"):
        ballot = {
            "ballot_id": text(raw.get("ballot_id"), "ballot_id"),
            "reviewer_id": text(raw.get("reviewer_id"), "ballot reviewer_id"),
            "bid_id": text(raw.get("bid_id"), "ballot bid_id"),
            "score": raw.get("score"),
            "at": text(raw.get("at"), "ballot at"),
        }
        if ballot["reviewer_id"] not in reviewers or ballot["bid_id"] not in bids:
            raise PanelError("PANEL_CASE_INVALID", "ballot identity is invalid")
        score = ballot["score"]
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
            raise PanelError("PANEL_CASE_INVALID", "ballot score must be 0..100")
        cast_at = parse_time(ballot["at"], "ballot at")
        if cast_at < parse_time(bids[ballot["bid_id"]]["received_at"]):
            raise PanelError("PANEL_CASE_INVALID", "ballot predates bid receipt")
        pair = (ballot["reviewer_id"], ballot["bid_id"])
        if pair in pairs:
            raise PanelError(
                "PANEL_CASE_INVALID", "ballot identity is invalid: duplicate reviewer/bid"
            )
        pairs.add(pair)
        if ballot["ballot_id"] in ids:
            raise PanelError(
                "PANEL_CASE_INVALID", "ballot identity is invalid: duplicate ballot_id"
            )
        ids.add(ballot["ballot_id"])
        ballots.append(ballot)
    return ballots


def _normalize_disclosures(
    case: dict[str, Any],
    reviewers: dict[str, dict[str, Any]],
    bids: dict[str, dict[str, Any]],
    ballots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    voted = {(item["reviewer_id"], item["bid_id"]) for item in ballots}
    disclosures = []
    for raw in objects(case, "disclosures"):
        disclosure = {
            "disclosure_id": text(raw.get("disclosure_id"), "disclosure_id"),
            "reviewer_id": text(raw.get("reviewer_id"), "disclosure reviewer_id"),
            "bid_id": text(raw.get("bid_id"), "disclosure bid_id"),
            "kind": text(raw.get("kind"), "disclosure kind"),
            "at": text(raw.get("at"), "disclosure at"),
        }
        if disclosure["reviewer_id"] not in reviewers or disclosure["bid_id"] not in bids:
            raise PanelError("PANEL_CASE_INVALID", "disclosure identity is invalid")
        if (disclosure["reviewer_id"], disclosure["bid_id"]) not in voted:
            raise PanelError(
                "PANEL_CASE_INVALID",
                "disclosure identity is invalid: reviewer cast no ballot for bid",
            )
        disclosed_at = parse_time(disclosure["at"], "disclosure at")
        if disclosed_at < parse_time(bids[disclosure["bid_id"]]["received_at"]):
            raise PanelError("PANEL_CASE_INVALID", "disclosure predates bid receipt")
        disclosures.append(disclosure)
    index(disclosures, "disclosure_id", "disclosure")
    return disclosures


def normalize_case(case: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a normalized copy of a version-1 panel case."""
    if not isinstance(case, dict) or case.get("version") != 1:
        raise PanelError("PANEL_CASE_INVALID", "panel case requires version 1")
    notice_id = text(case.get("notice_id"), "notice_id")
    case_id = text(case.get("case_id", notice_id), "case_id")
    round_id = text(case.get("round_id", f"{notice_id}-R1"), "round_id")
    initial_at = text(
        case.get("initial_review_at", case.get("signed_at")), "initial_review_at"
    )
    parse_time(initial_at, "initial_review_at")
    scoring = case.get("scoring")
    if not isinstance(scoring, dict) or scoring.get("rule") != SCORING_RULE:
        raise PanelError("PANEL_CASE_INVALID", "panel case requires the locked scoring rule")
    quorum = scoring.get("quorum")
    if isinstance(quorum, bool) or not isinstance(quorum, int) or quorum < 1:
        raise PanelError("PANEL_CASE_INVALID", "scoring quorum must be a positive integer")
    rule_id = text(scoring.get("rule_id"), "scoring rule_id")
    bids = _normalize_bids(case, notice_id)
    reviewers = _normalize_reviewers(case)
    reviewer_index = index(reviewers, "reviewer_id", "reviewer")
    bid_index = index(bids, "bid_id", "bid")
    ballots = _normalize_ballots(case, reviewer_index, bid_index)
    disclosures = _normalize_disclosures(case, reviewer_index, bid_index, ballots)
    disclosure_index = index(disclosures, "disclosure_id", "disclosure")
    contracts = normalize_contracts(case, reviewer_index, disclosure_index, bid_index)
    governing_id = case.get("governing_contract_id", "")
    if governing_id:
        governing_id = text(governing_id, "governing_contract_id")
        if governing_id not in {item["contract_id"] for item in contracts}:
            raise PanelError(
                "PANEL_CONTRACT_UNAUTHORISED", "governing contract is unknown"
            )
    return {
        "version": 1,
        "case_id": case_id,
        "notice_id": notice_id,
        "round_id": round_id,
        "initial_review_at": initial_at,
        "scoring": {"rule": SCORING_RULE, "rule_id": rule_id, "quorum": quorum},
        "bids": bids,
        "reviewers": reviewers,
        "ballots": ballots,
        "disclosures": disclosures,
        "contracts": contracts,
        "governing_contract_id": governing_id,
    }


def load_panel_case(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalize_case(raw)


def record_hash(item: dict[str, Any]) -> str:
    """Stable hash of one evidence record, independent of storage metadata."""
    return stable_hash(item)


def case_hash(case: dict[str, Any]) -> str:
    return stable_hash(
        {
            "notice_id": case["notice_id"],
            "round_id": case["round_id"],
            "initial_review_at": case["initial_review_at"],
            "scoring": case["scoring"],
        }
    )
