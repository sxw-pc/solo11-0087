import json
from pathlib import Path

import pytest

from tender_anchor.bond_intake import preview_bond_case, read_bond_case
from tender_anchor.store import Store


FIXTURE = Path(__file__).parents[1] / "fixtures/bonds/shared_security.json"


def test_bond_preview_keeps_bid_and_security_evidence(crawled):
    _, paths, _ = crawled
    case = read_bond_case(FIXTURE)
    with Store(paths.db_path) as store:
        preview = preview_bond_case(store, case)
    assert preview["status"] == "evidence_ready"
    assert [row["status"] for row in preview["bids"]] == ["eligible", "eligible"]
    assert preview["total_security"] == "60000.00"
    assert [item["instrument_id"] for item in preview["instruments"]] == ["CASH-01", "G-01", "G-02"]


def test_bond_case_rejects_duplicate_instrument(tmp_path):
    case = json.loads(FIXTURE.read_text(encoding="utf-8"))
    case["instruments"][1]["instrument_id"] = "CASH-01"
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate instrument"):
        read_bond_case(path)
