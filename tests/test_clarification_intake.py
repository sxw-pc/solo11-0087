import json
from pathlib import Path

import pytest

from tender_anchor.clarification_intake import preview_clarification, read_clarification_case
from tender_anchor.store import Store


FIXTURE = Path(__file__).parents[1] / "fixtures/clarifications/case.json"


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
