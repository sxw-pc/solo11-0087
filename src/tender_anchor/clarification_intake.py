"""Offline intake of synthetic tender questions and answer disclosure evidence."""

from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .normalize import stable_hash
from .store import Store


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("event time must be a timestamp")
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("event time is invalid") from exc
    if instant.tzinfo is None:
        raise ValueError("event time requires timezone")
    return instant


def case_key(case: dict[str, Any]) -> str:
    """Stable identity of a disclosure case across re-imports."""
    return f"{case['notice_id']}#{case['question_id']}"


def case_fingerprint(case: dict[str, Any]) -> str:
    """Hash of the full evidence payload, including every access event."""
    return stable_hash(
        {
            "notice_id": case["notice_id"],
            "question_id": case["question_id"],
            "questioner": case["questioner"],
            "participants": case.get("participants", []),
            "answers": case["answers"],
            "access_events": case["access_events"],
        }
    )


def read_clarification_case(path: str | Path) -> dict[str, Any]:
    """Read and validate the local disclosure log without deciding parity."""
    case = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(case, dict) or case.get("version") != 1:
        raise ValueError("clarification case requires version 1")
    if not isinstance(case.get("answers"), list) or not isinstance(case.get("access_events"), list):
        raise ValueError("answers and access_events must be lists")
    if not case.get("notice_id") or not case.get("question_id") or not case.get("questioner"):
        raise ValueError("clarification identity is incomplete")
    participants = case.get("participants")
    if not isinstance(participants, list) or not participants or any(
        not isinstance(item, str) or not item for item in participants
    ):
        raise ValueError("participants must be a nonempty list of bidder ids")
    if len(set(participants)) != len(participants):
        raise ValueError("participants contain duplicate bidder ids")
    answer_ids: set[str] = set()
    public_ids: set[str] = set()
    for answer in case["answers"]:
        if not isinstance(answer, dict) or not all(answer.get(key) for key in
            ("answer_id", "at", "visibility", "content", "content_sha256")):
            raise ValueError("answer evidence is incomplete")
        if answer["visibility"] not in ("private_draft", "public"):
            raise ValueError("answer identity or visibility is invalid")
        answer_ids.add(answer["answer_id"])
        _time(answer["at"])
        if not isinstance(answer["content"], str) or sha256(answer["content"].encode("utf-8")).hexdigest() != answer["content_sha256"]:
            raise ValueError("answer content digest mismatch")
        if answer["visibility"] == "public":
            if answer.get("redacted_questioner") is not True:
                raise ValueError("public answer must be anonymized")
            public_ids.add(answer["answer_id"])
        else:
            if not answer.get("recipient"):
                raise ValueError("private draft requires a named recipient")
    if not answer_ids:
        raise ValueError("at least one answer is required")
    # More than one public row is structurally tolerated here so the parity
    # layer can adjudicate the id/content conflict; zero publics is invalid.
    if not public_ids:
        raise ValueError("at least one anonymized public answer is required")
    event_ids: set[str] = set()
    private_recipients = {
        row["answer_id"]: row["recipient"]
        for row in case["answers"]
        if row["visibility"] == "private_draft"
    }
    for event in case["access_events"]:
        if not isinstance(event, dict) or not all(event.get(key) for key in
            ("event_id", "answer_id", "bidder_id", "at")):
            raise ValueError("access evidence is incomplete")
        if event["event_id"] in event_ids:
            raise ValueError("access event id must be unique")
        event_ids.add(event["event_id"])
        if event["answer_id"] not in answer_ids:
            raise ValueError("access references unknown answer")
        if event["bidder_id"] not in participants:
            raise ValueError("access bidder is not a registered participant")
        recipient = private_recipients.get(event["answer_id"])
        if recipient is not None and event["bidder_id"] != recipient:
            raise ValueError("private draft was accessed by a non-recipient")
        _time(event["at"])
    return case


def preview_clarification(store: Store, case: dict[str, Any]) -> dict[str, Any]:
    """Link a disclosure case to captured notices and retain every source event."""
    original = store.get_notice(case["notice_id"])
    if original is None or original["kind"] != "tender":
        return {"status": "unverifiable", "reason": "tender_notice_missing"}
    return {
        "status": "evidence_ready",
        "notice_id": case["notice_id"],
        "notice_source_hash": original["source_hash"],
        "answers": case["answers"],
        "access_events": case["access_events"],
    }
