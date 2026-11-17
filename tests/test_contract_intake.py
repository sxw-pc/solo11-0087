import json
from pathlib import Path

import pytest

from tender_anchor.contract_intake import preview_contract_case, read_contract_case
from tender_anchor.store import Store


FIXTURE = Path(__file__).parents[1] / "fixtures/contracts/delivery_case.json"


def test_contract_intake_links_award_to_captured_tender(crawled):
    _, paths, _ = crawled
    case = read_contract_case(FIXTURE)
    with Store(paths.db_path) as store:
        preview = preview_contract_case(store, case)
    assert preview["status"] == "evidence_ready"
    assert len(preview["notice_source_hash"]) == 64
    assert [row["milestone_id"] for row in preview["milestones"]] == ["M1", "M2"]
    assert [row["event_id"] for row in preview["events"]] == ["D1", "I1", "I2", "D2", "I3"]
    assert len(preview["evidence_sha256"]) == 64


def test_contract_intake_rejects_event_for_another_contract(tmp_path):
    case = json.loads(FIXTURE.read_text(encoding="utf-8"))
    case["events"][0]["contract_id"] = "C-OTHER"
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    with pytest.raises(ValueError, match="identity is invalid"):
        read_contract_case(path)
