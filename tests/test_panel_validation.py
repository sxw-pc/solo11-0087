import json

import pytest

from tender_anchor.errors import PanelError
from tender_anchor.panel_case import normalize_case

from tests.panel_helpers import CASE


def test_duplicate_bidder_in_one_round_is_rejected():
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    raw["bids"][1]["bidder_id"] = "supplier-a"
    with pytest.raises(PanelError) as caught:
        normalize_case(raw)
    assert caught.value.code == "PANEL_SCOPE_MISMATCH"


def test_scope_mismatch_across_notice_is_rejected():
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    raw["bids"][0]["notice_id"] = "TA-2026-0002"
    with pytest.raises(PanelError) as caught:
        normalize_case(raw)
    assert caught.value.code == "PANEL_SCOPE_MISMATCH"


def test_contract_citing_unknown_disclosure_is_unauthorised():
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    raw["contracts"][0]["basis_disclosure_ids"] = ["DISC-X"]
    with pytest.raises(PanelError) as caught:
        normalize_case(raw)
    assert caught.value.code == "PANEL_CONTRACT_UNAUTHORISED"


def test_alternate_must_be_declared_alternate():
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    raw["contracts"][0]["alternate_map"] = {"R1": "R2"}
    with pytest.raises(PanelError) as caught:
        normalize_case(raw)
    assert caught.value.code == "PANEL_CONTRACT_UNAUTHORISED"


def test_whole_round_scope_must_cover_every_bid():
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    raw["contracts"][0]["scope"] = [{"reviewer_id": "R1", "bid_id": "BID-A"}]
    with pytest.raises(PanelError) as caught:
        normalize_case(raw)
    assert caught.value.code == "PANEL_CONTRACT_UNAUTHORISED"


def test_absolute_scope_may_not_strike_undisclosed_pair():
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    raw["contracts"][1]["scope"] = [{"reviewer_id": "R1", "bid_id": "BID-B"}]
    with pytest.raises(PanelError) as caught:
        normalize_case(raw)
    assert caught.value.code == "PANEL_CONTRACT_UNAUTHORISED"


def test_round_and_notice_scope_filter_ballots_by_bidder():
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    raw["ballots"][0]["reviewer_id"] = "R9"
    with pytest.raises(PanelError) as caught:
        normalize_case(raw)
    assert caught.value.code == "PANEL_CASE_INVALID"


def test_bad_score_range_rejected():
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    raw["ballots"][0]["score"] = 101
    with pytest.raises(PanelError) as caught:
        normalize_case(raw)
    assert caught.value.code == "PANEL_CASE_INVALID"


def test_disclosure_without_ballot_rejected():
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    raw["ballots"] = [b for b in raw["ballots"] if b["ballot_id"] != "R2-B"]
    raw["disclosures"][0] = {
        "disclosure_id": "DISC-2", "reviewer_id": "R2", "bid_id": "BID-B",
        "kind": "financial_tie", "at": "2026-12-02T09:00:00+08:00",
    }
    with pytest.raises(PanelError) as caught:
        normalize_case(raw)
    assert caught.value.code == "PANEL_CASE_INVALID"


def test_ballot_before_bid_receipt_rejected():
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    raw["ballots"][0]["at"] = "2026-11-29T10:00:00+08:00"
    with pytest.raises(PanelError) as caught:
        normalize_case(raw)
    assert caught.value.code == "PANEL_CASE_INVALID"
