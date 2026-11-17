"""Conflict replay, locking, and provisional-to-final transition tests."""

import json

import pytest

from clarification_helpers import clarify, load
from tender_anchor.clarification_intake import case_key
from tender_anchor.clarification_store import ClarificationConflict
from tender_anchor.models import NoticeRecord
from tender_anchor.parity import adjudicate
from tender_anchor.store import Store


def test_evidence_change_after_certification_is_refused(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        clarify(store, load("base"))
        mutated = load("base")
        mutated["access_events"].append(
            {"event_id": "E-A2", "answer_id": "A-PUBLIC",
             "bidder_id": "supplier-a", "at": "2026-11-25T14:00:00+08:00"}
        )
        with pytest.raises(ClarificationConflict, match="certification"):
            clarify(store, mutated)
        rows = store.get_parity_certifications(case_key(load("base")))
        assert {row["status"] for row in rows} == {"certified", "pending"}


def test_correction_change_after_certification_conflicts_on_replay(crawled):
    _, paths, _ = crawled
    case = load("base")
    with Store(paths.db_path) as store:
        clarify(store, case)
        row = store.connection.execute(
            "SELECT changes_json FROM corrections "
            "WHERE correction_notice_id='TA-2026-0003'"
        ).fetchone()
        changes = json.loads(row["changes_json"])
        for change in changes:
            if change.get("field") == "deadline_at":
                change["normalized_value"] = "2026-11-30T09:30:00+08:00"
        store.connection.execute(
            "UPDATE corrections SET changes_json=? "
            "WHERE correction_notice_id='TA-2026-0003'",
            (json.dumps(changes, ensure_ascii=False, sort_keys=True),),
        )
        store.connection.commit()
        with pytest.raises(ClarificationConflict, match="changed after issuance"):
            clarify(store, case)


def test_redaction_missing_is_rejected_before_persistence(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        with pytest.raises(ValueError, match="anonymized"):
            clarify(store, load("redaction_missing"))
        assert store.count("clarification_cases") == 0
        assert store.count("parity_certifications") == 0


def test_private_draft_leak_is_rejected_before_persistence(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        with pytest.raises(ValueError, match="non-recipient"):
            clarify(store, load("private_leaked"))
        assert store.count("clarification_cases") == 0


def test_answer_identity_conflict_is_persisted_as_unverifiable(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        result = clarify(store, load("answer_id_content_conflict"))
        for decision in result["decisions"].values():
            assert decision["status"] == "unverifiable"
            assert "answer_id_content_conflict" in decision["blocking_reasons"]


def test_reimport_with_changed_but_unlocked_case_records_update(crawled):
    """A case whose first import is unverifiable can still be corrected."""
    _, paths, _ = crawled
    missing = load("notice_missing")
    with Store(paths.db_path) as store:
        first = clarify(store, missing)
        assert first["outcome"] == "inserted"
        updated = json.loads(json.dumps(missing))
        updated["access_events"].append(
            {"event_id": "E-LATE", "answer_id": "A-PUBLIC",
             "bidder_id": "supplier-a", "at": "2026-11-27T10:00:00+08:00"}
        )
        result = clarify(store, updated)
        assert result["outcome"] == "updated"
        bundle = store.get_clarification_bundle(case_key(missing))
        assert len(bundle["access_events"]) == 3
        assert store.count("clarification_cases") == 1


def test_provisional_case_progresses_to_certified_after_evidence_fix(crawled):
    """Unverifiable rows are provisional and progress once evidence is fixed."""
    _, paths, _ = crawled
    missing = load("notice_missing")
    with Store(paths.db_path) as store:
        first = clarify(store, missing)
        assert all(
            d["status"] == "unverifiable" for d in first["decisions"].values()
        )
        record = NoticeRecord(
            notice_id=missing["notice_id"], kind="tender", kind_label="招标公告",
            title="补采公告", project_code="", region="", purchaser="", agency="",
            publish_at="2026-11-02T00:00:00+08:00",
            deadline_at="2026-12-05T09:30:00+08:00",
            budget_amount=None, budget_currency="CNY", award_amount=None,
            winner="", related_notice_id="", content_excerpt="",
            source_url="http://127.0.0.1:5810/notices/TA-2026-9999",
            source_hash="z" * 64,
        )
        with store.transaction():
            store.upsert_notice(record, "run-backfill", {}, [])
        fixed = json.loads(json.dumps(load("public_receipt_complete")))
        fixed["notice_id"] = missing["notice_id"]
        fixed["question_id"] = missing["question_id"]
        result = clarify(store, fixed)
        assert result["outcome"] == "updated"
        assert result["decisions"]["public_open"]["status"] == "certified"
        assert result["decisions"]["per_person_receipt"]["status"] == "certified"
        rows = store.get_parity_certifications(case_key(missing))
        assert {row["status"] for row in rows} == {"certified"}
        mutated = json.loads(json.dumps(fixed))
        mutated["access_events"][0]["at"] = "2026-11-20T10:01:00+08:00"
        with pytest.raises(ClarificationConflict, match="certification"):
            clarify(store, mutated)


def test_deadline_basis_changes_when_correction_is_removed(crawled):
    """Removing correction evidence must move the basis back to the original."""
    _, paths, _ = crawled
    case = load("base")
    with Store(paths.db_path) as store:
        clarify(store, case)
        before = store.get_clarification_bundle(case_key(case))
        assert before["deadline_basis"] == "correction:TA-2026-0003"
        store.connection.execute(
            "DELETE FROM corrections WHERE correction_notice_id='TA-2026-0003'"
        )
        store.connection.commit()
        notice = store.get_notice(case["notice_id"])
        fresh = adjudicate(
            case, notice, store.get_corrections(case["notice_id"]), "public_open"
        )
        assert fresh["deadline"]["basis"] == "original_notice"
        assert fresh["status"] == "rejected"
