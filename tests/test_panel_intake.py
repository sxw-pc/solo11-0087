import json
from pathlib import Path

import pytest

from tender_anchor.panel_intake import preview_panel_case, read_panel_case
from tender_anchor.store import Store


FIXTURE = Path(__file__).parents[1] / "fixtures/panel/case.json"


def test_panel_intake_preserves_ballots_and_disclosure(crawled):
    _, paths, _ = crawled
    case = read_panel_case(FIXTURE)
    with Store(paths.db_path) as store:
        preview = preview_panel_case(store, case)
    assert preview["status"] == "evidence_ready"
    assert [row["status"] for row in preview["bids"]] == ["eligible", "eligible"]
    assert len(preview["ballots"]) == 8
    assert preview["disclosures"][0]["reviewer_id"] == "R1"


def test_panel_intake_rejects_duplicate_ballot(tmp_path):
    case = json.loads(FIXTURE.read_text(encoding="utf-8"))
    case["ballots"][1]["ballot_id"] = "R1-A"
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    with pytest.raises(ValueError, match="ballot identity"):
        read_panel_case(path)
