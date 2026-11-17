"""Evidence-chain assembly for clarification information-parity adjudication.

This module only organizes captured facts (answers, access events, notice,
corrections) into a deterministic view. It never infers a participant's past
knowledge from a page's current state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from .clarification_intake import _time  # reuse the strict timestamp parser

PREPARATION_DAYS = 7

# Stable evidence-check codes; any failed hard check blocks certification.
HARD_CHECKS = (
    "tender_notice_captured",
    "answer_identity_consistent",
    "single_public_terms",
    "public_terms_redacted",
    "private_draft_never_public",
    "public_access_recorded",
    "deadline_evidence_present",
)


@dataclass(frozen=True)
class DeadlineEvidence:
    """The deadline the preparation window is measured against."""

    deadline_at: str
    basis: str  # "original_notice" | "correction:<notice_id>"
    source: str
    original_deadline_at: str | None
    correction_notice_id: str | None


@dataclass
class ParityCaseView:
    """Case-level evidence chain and shared preparation window."""

    notice_id: str
    question_id: str
    questioner: str
    participants: tuple[str, ...]
    public_answer_id: str | None
    public_published_at: str | None
    deadline: DeadlineEvidence | None
    preparation_days: int | None
    window_sufficient: bool
    checks: dict[str, str]  # check code -> "pass" | reason code
    answers: list[dict[str, Any]] = field(default_factory=list)
    access_events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def evidence_ready(self) -> bool:
        return all(self.checks.get(code) == "pass" for code in HARD_CHECKS)

    def failed_checks(self) -> list[str]:
        return [code for code in HARD_CHECKS if self.checks.get(code) != "pass"]

    def reason_codes(self) -> list[str]:
        return [self.checks[code] for code in self.failed_checks()]


def _answer_groups(case: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for answer in case["answers"]:
        groups.setdefault(answer["answer_id"], []).append(answer)
    return groups


def deadline_evidence(
    notice: dict[str, Any] | None, corrections: list[dict[str, Any]]
) -> DeadlineEvidence | None:
    """Pick the latest verified corrected deadline, else the original one."""
    if notice is None or not notice.get("deadline_at"):
        return None
    original = notice["deadline_at"]
    chosen: tuple[str, str, str | None] | None = None
    for correction in corrections:
        for change in correction.get("changes", []):
            if change.get("field") != "deadline_at":
                continue
            value = change.get("normalized_value") or ""
            if not value:
                continue
            published_at = correction.get("published_at") or ""
            candidate = (value, published_at, correction["correction_notice_id"])
            if chosen is None or published_at > chosen[1]:
                chosen = candidate
    if chosen is None:
        return DeadlineEvidence(
            deadline_at=original,
            basis="original_notice",
            source=notice.get("source_url", ""),
            original_deadline_at=original,
            correction_notice_id=None,
        )
    return DeadlineEvidence(
        deadline_at=chosen[0],
        basis=f"correction:{chosen[2]}",
        source=next(
            (item.get("source_url", "") for item in corrections
             if item["correction_notice_id"] == chosen[2]),
            "",
        ),
        original_deadline_at=original,
        correction_notice_id=chosen[2],
    )


def build_case_view(
    case: dict[str, Any],
    notice: dict[str, Any] | None,
    corrections: list[dict[str, Any]],
) -> ParityCaseView:
    """Assemble the re-readable evidence chain without persisting anything."""
    checks: dict[str, str] = {code: "pass" for code in HARD_CHECKS}
    groups = _answer_groups(case)

    checks["tender_notice_captured"] = (
        "pass" if notice is not None and notice.get("kind") == "tender"
        else "tender_notice_missing"
    )

    # Distinct contents sharing one answer_id cannot be treated as one term.
    identity_bad = any(
        len({row["content_sha256"] for row in rows}) > 1 for rows in groups.values()
    )
    checks["answer_identity_consistent"] = (
        "answer_id_content_conflict" if identity_bad else "pass"
    )

    public_answers = [row for row in case["answers"] if row["visibility"] == "public"]
    checks["single_public_terms"] = (
        "pass" if len(public_answers) == 1 else "public_terms_not_unique"
    )

    public = public_answers[0] if len(public_answers) == 1 else None
    checks["public_terms_redacted"] = (
        "pass" if public is not None and public.get("redacted_questioner") is True
        else "redaction_missing"
    )

    # A private draft must never have been exposed to a non-recipient.
    private_recipients = {
        row["answer_id"]: row.get("recipient")
        for row in case["answers"]
        if row["visibility"] == "private_draft"
    }
    leaked = any(
        event["answer_id"] in private_recipients
        and event["bidder_id"] != private_recipients[event["answer_id"]]
        for event in case["access_events"]
    )
    checks["private_draft_never_public"] = (
        "private_draft_leaked" if leaked else "pass"
    )

    # Touching a private draft never counts as public receipt. At least one
    # explicit contact with a published answer must exist on the record.
    public_ids = {row["answer_id"] for row in public_answers}
    checks["public_access_recorded"] = (
        "pass"
        if any(event["answer_id"] in public_ids for event in case["access_events"])
        else "no_public_access_event"
    )

    deadline = deadline_evidence(notice, corrections)
    checks["deadline_evidence_present"] = (
        "pass" if deadline is not None else "deadline_evidence_missing"
    )

    preparation_days: int | None = None
    window_sufficient = False
    if public is not None and deadline is not None:
        published = _time(public["at"])
        due = _time(deadline.deadline_at)
        preparation_days = (due.date() - published.date()).days
        window_sufficient = due >= published + timedelta(days=PREPARATION_DAYS)

    return ParityCaseView(
        notice_id=case["notice_id"],
        question_id=case["question_id"],
        questioner=case["questioner"],
        participants=tuple(case.get("participants", [])),
        public_answer_id=public["answer_id"] if public is not None else None,
        public_published_at=public["at"] if public is not None else None,
        deadline=deadline,
        preparation_days=preparation_days,
        window_sufficient=window_sufficient,
        checks=checks,
        answers=list(case["answers"]),
        access_events=list(case["access_events"]),
    )


def deadline_as_dict(deadline: DeadlineEvidence | None) -> dict[str, Any] | None:
    if deadline is None:
        return None
    return {
        "deadline_at": deadline.deadline_at,
        "basis": deadline.basis,
        "source": deadline.source,
        "original_deadline_at": deadline.original_deadline_at,
        "correction_notice_id": deadline.correction_notice_id,
    }
