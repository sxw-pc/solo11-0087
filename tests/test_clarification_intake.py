import json
from pathlib import Path

import pytest

from tender_anchor.clarification_intake import (
    case_fingerprint,
    preview_clarification,
    read_clarification_case,
)
from tender_anchor.store import Store


FIXTURE = Path(__file__).parents[1] / "fixtures/clarifications/case.json"
CASES = Path(__file__).parents[1] / "fixtures/clarifications/cases"


def test_clarification_case_links_captured_notice_but_keeps_raw_access(crawled):
    _, paths, _ = crawled
    case = read_clarification_case(FIXTURE)
    with Store(paths.db_path) as store:
        preview = preview_clarification(store, case)
    assert preview["status"] == "evidence_ready"
    assert len(preview["notice_source_hash"]) == 64
    assert [row["visibility"] for row in preview["answers"]] == ["private_draft", "public"]
    assert [row["bidder_id"] for row in preview["access_events"]] == ["supplier-a", "supplier-b"]


def test_clarification_rejects_unknown_answer_access(tmp_path):
    case = json.loads(FIXTURE.read_text(encoding="utf-8"))
    case["access_events"][1]["answer_id"] = "NOT-PUBLISHED"
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown answer"):
        read_clarification_case(path)


def test_clarification_rejects_changed_answer_content(tmp_path):
    case = json.loads(FIXTURE.read_text(encoding="utf-8"))
    case["answers"][0]["content"] = "changed"
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        read_clarification_case(path)


def test_public_answer_without_anonymization_is_rejected():
    with pytest.raises(ValueError, match="anonymized"):
        read_clarification_case(CASES / "redaction_missing.json")


def test_private_draft_leaked_to_non_recipient_is_rejected():
    with pytest.raises(ValueError, match="non-recipient"):
        read_clarification_case(CASES / "private_leaked.json")


def test_answer_id_content_conflict_still_reads_for_adjudication():
    # The reader preserves the raw evidence; the parity layer rejects it.
    case = read_clarification_case(CASES / "answer_id_content_conflict.json")
    assert len(case["answers"]) == 3


def test_participant_and_event_identity_validation(tmp_path):
    case = json.loads(FIXTURE.read_text(encoding="utf-8"))
    case["participants"] = ["supplier-a", "supplier-a"]
    path = tmp_path / "dup.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        read_clarification_case(path)

    case = json.loads(FIXTURE.read_text(encoding="utf-8"))
    case["access_events"][0]["bidder_id"] = "outsider"
    path = tmp_path / "outsider.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    with pytest.raises(ValueError, match="registered participant"):
        read_clarification_case(path)

    case = json.loads(FIXTURE.read_text(encoding="utf-8"))
    case["access_events"][1]["event_id"] = case["access_events"][0]["event_id"]
    path = tmp_path / "event.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    with pytest.raises(ValueError, match="event id must be unique"):
        read_clarification_case(path)


def test_fingerprint_changes_with_any_evidence_edit():
    base = read_clarification_case(FIXTURE)
    edited = json.loads(FIXTURE.read_text(encoding="utf-8"))
    edited["access_events"].append(
        {"event_id": "E-A2", "answer_id": "A-PUBLIC",
         "bidder_id": "supplier-a", "at": "2026-11-25T14:00:00+08:00"}
    )
    assert case_fingerprint(base) != case_fingerprint(edited)


def test_preview_is_unverifiable_when_notice_missing(tmp_path):
    case = read_clarification_case(CASES / "notice_missing.json")
    store = Store(tmp_path / "db.sqlite3")
    try:
        preview = preview_clarification(store, case)
    finally:
        store.close()
    assert preview == {"status": "unverifiable", "reason": "tender_notice_missing"}
