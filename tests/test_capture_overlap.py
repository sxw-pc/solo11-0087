import json
from pathlib import Path

import pytest

from tender_anchor.capture_overlap import preview_overlap, read_capture_panel


PANEL = Path(__file__).resolve().parents[1] / "fixtures" / "coverage" / "two-portals.json"


def test_two_portal_receipts_preserve_distinct_identity_contract_evidence():
    panel = read_capture_panel(PANEL)
    assert preview_overlap(panel) == {
        "cohort_id": "CAPTURE-2026-PANEL",
        "portal_a_count": 100,
        "portal_b_count": 100,
        "exact_id_overlap": 40,
        "attested_aliases": 20,
        "contracts": ["operator_canonical_notices", "public_id_records"],
        "coverage_decision": None,
    }


def test_alias_receipt_must_link_matching_stored_content(tmp_path):
    panel = json.loads(PANEL.read_text(encoding="utf-8"))
    panel["alias_receipts"][0]["portal_b_id"] = "MIR-061"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(panel), encoding="utf-8")
    with pytest.raises(ValueError, match="alias"):
        read_capture_panel(path)
