"""Offline intake of synthetic tender questions and answer disclosure evidence."""

from __future__ import annotations

import json
from hashlib import sha256
from datetime import datetime
from pathlib import Path
from typing import Any

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


def read_clarification_case(path: str | Path) -> dict[str, Any]:
    """Read the local disclosure log without deciding information parity."""
    case = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(case, dict) or case.get("version") != 1:
        raise ValueError("clarification case requires version 1")
    if not isinstance(case.get("answers"), list) or not isinstance(case.get("access_events"), list):
        raise ValueError("answers and access_events must be lists")
    if not case.get("notice_id") or not case.get("question_id") or not case.get("questioner"):
        raise ValueError("clarification identity is incomplete")
    answer_ids: set[str] = set()
    for answer in case["answers"]:
        if not isinstance(answer, dict) or not all(answer.get(key) for key in
            ("answer_id", "at", "visibility", "content", "content_sha256")):
            raise ValueError("answer evidence is incomplete")
        if answer["answer_id"] in answer_ids or answer["visibility"] not in ("private_draft", "public"):
            raise ValueError("answer identity or visibility is invalid")
        answer_ids.add(answer["answer_id"])
        _time(answer["at"])
        if not isinstance(answer["content"], str) or sha256(answer["content"].encode("utf-8")).hexdigest() != answer["content_sha256"]:
            raise ValueError("answer content digest mismatch")
    for event in case["access_events"]:
        if not isinstance(event, dict) or not all(event.get(key) for key in
            ("event_id", "answer_id", "bidder_id", "at")):
            raise ValueError("access evidence is incomplete")
        if event["answer_id"] not in answer_ids:
            raise ValueError("access references unknown answer")
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
