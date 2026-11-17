"""Deterministic information-parity adjudication for clarification disclosures.

The adjudication is a pure function of:

* the clarification case (question, private/public answers, access events);
* the tender notice captured by the crawl pipeline;
* correction evidence returned by ``Store.get_corrections``.

It never infers past knowledge from a page's current state: a participant's
receipt of the binding public terms exists only when an explicit access event
for that exact published answer is on record.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .clarification_intake import _time
from .parity_evidence import (
    PREPARATION_DAYS,
    build_case_view,
    deadline_as_dict,
)

__all__ = [
    "PUBLIC_OPEN", "PER_PERSON_RECEIPT", "CONTRACT_MODES",
    "CERTIFIED", "PENDING", "REJECTED", "UNVERIFIABLE",
    "PREPARATION_DAYS", "build_case_view",
    "public_receipts", "adjudicate", "adjudicate_all_modes",
]

PUBLIC_OPEN = "public_open"
PER_PERSON_RECEIPT = "per_person_receipt"
CONTRACT_MODES = (PUBLIC_OPEN, PER_PERSON_RECEIPT)

# Eligibility outcomes.
CERTIFIED = "certified"
PENDING = "pending"
REJECTED = "rejected"
UNVERIFIABLE = "unverifiable"


def public_receipts(case: dict[str, Any], public_id: str | None) -> dict[str, datetime]:
    """Earliest explicit receipt of the exact public answer per bidder."""
    receipts: dict[str, datetime] = {}
    if public_id is None:
        return receipts
    for event in case["access_events"]:
        if event["answer_id"] != public_id:
            continue
        instant = _time(event["at"])
        previous = receipts.get(event["bidder_id"])
        if previous is None or instant < previous:
            receipts[event["bidder_id"]] = instant
    return receipts


def _participant_rows(
    case: dict[str, Any],
    view,
    contract_mode: str,
) -> list[dict[str, Any]]:
    receipts = public_receipts(case, view.public_answer_id)
    rows: list[dict[str, Any]] = []
    for bidder_id in view.participants:
        received_at = receipts.get(bidder_id)
        row = {
            "bidder_id": bidder_id,
            "public_receipt_at": received_at.isoformat() if received_at else None,
            "receipt_evidence": (
                "explicit_public_access" if received_at is not None
                else "no_public_receipt"
            ),
        }
        if contract_mode == PER_PERSON_RECEIPT:
            row["status"] = CERTIFIED if received_at is not None else PENDING
            row["reason"] = (
                "individual_receipt_recorded" if received_at is not None
                else "individual_receipt_missing"
            )
        else:
            row["status"] = CERTIFIED
            row["reason"] = "public_availability_contract"
        rows.append(row)
    return rows


def adjudicate(
    case: dict[str, Any],
    notice: dict[str, Any] | None,
    corrections: list[dict[str, Any]],
    contract_mode: str,
) -> dict[str, Any]:
    """Return the full, serializable adjudication for one contract mode."""
    if contract_mode not in CONTRACT_MODES:
        raise ValueError("unknown contract mode")
    view = build_case_view(case, notice, corrections)
    participant_rows = _participant_rows(case, view, contract_mode)

    blockers: list[str] = list(view.reason_codes())
    if view.public_answer_id is not None and view.deadline is not None:
        if not view.window_sufficient:
            blockers.append("preparation_window_short")
    elif not blockers:
        blockers.append("preparation_window_unknown")

    if view.evidence_ready and view.window_sufficient:
        if contract_mode == PUBLIC_OPEN:
            status, reason = CERTIFIED, "public_terms_equal_availability"
        elif any(row["status"] != CERTIFIED for row in participant_rows):
            status, reason = PENDING, "awaiting_individual_receipts"
        else:
            status, reason = CERTIFIED, "all_individual_receipts_recorded"
    elif view.evidence_ready:
        status, reason = REJECTED, "preparation_window_short"
    else:
        status, reason = UNVERIFIABLE, "evidence_chain_incomplete"

    return {
        "contract_mode": contract_mode,
        "status": status,
        "reason": reason,
        "notice_id": view.notice_id,
        "question_id": view.question_id,
        "public_answer_id": view.public_answer_id,
        "public_published_at": view.public_published_at,
        "preparation_days": view.preparation_days,
        "preparation_required_days": PREPARATION_DAYS,
        "window_sufficient": view.window_sufficient,
        "deadline": deadline_as_dict(view.deadline),
        "checks": dict(sorted(view.checks.items())),
        "blocking_reasons": sorted(set(blockers)),
        "participants": participant_rows,
    }


def adjudicate_all_modes(
    case: dict[str, Any],
    notice: dict[str, Any] | None,
    corrections: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        mode: adjudicate(case, notice, corrections, mode) for mode in CONTRACT_MODES
    }
