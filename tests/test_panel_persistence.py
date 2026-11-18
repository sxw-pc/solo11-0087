import json

import pytest

from tender_anchor.errors import PanelError
from tender_anchor.panel_case import normalize_case
from tender_anchor.panel_intake import preview_panel_case, record_panel_case
from tender_anchor.panel_store import load_panel_reviews

from tests.panel_helpers import CASE, load, panel_store  # noqa: F401


def test_record_persists_versions_award_and_correction(panel_store):
    result = record_panel_case(panel_store, load("case.json"))
    assert result["status"] == "recorded"
    assert result["final_award"]["winner_bidder_id"] == "supplier-b"
    reviews = load_panel_reviews(panel_store, "PANEL-TA-2026-0001-R1")
    assert [(row["version"], row["stage"], row["contract_id"]) for row in reviews] == [
        (1, "initial", ""),
        (2, "rereview", "CONTRACT-ABSOLUTE"),
        (3, "rereview", "CONTRACT-WHOLE-ROUND"),
    ]
    assert reviews[0]["winner_bidder_id"] == "supplier-a"
    assert reviews[2]["winner_bidder_id"] == "supplier-b"
    award = panel_store.connection.execute(
        "SELECT * FROM panel_awards WHERE case_id='PANEL-TA-2026-0001-R1'"
    ).fetchone()
    assert award["winner_bidder_id"] == "supplier-b"
    assert award["contract_id"] == "CONTRACT-WHOLE-ROUND"
    correction = panel_store.connection.execute(
        "SELECT * FROM panel_corrections WHERE case_id='PANEL-TA-2026-0001-R1'"
    ).fetchone()
    assert correction["from_winner_bidder_id"] == "supplier-a"
    assert correction["to_winner_bidder_id"] == "supplier-b"
    assert correction["prior_review_id"] == reviews[0]["review_id"]
    events = [row["event_type"] for row in panel_store.connection.execute(
        "SELECT event_type FROM audit_events ORDER BY event_id"
    ).fetchall()]
    assert "PANEL_INITIAL_RECORDED" in events
    assert "PANEL_AWARD_CORRECTED" in events


def test_reimporting_identical_evidence_replays(panel_store):
    first = record_panel_case(panel_store, load("case.json"))
    count = panel_store.count("panel_reviews")
    second = record_panel_case(panel_store, load("case.json"))
    assert second["final_award"] == first["final_award"]
    assert panel_store.count("panel_reviews") == count
    assert panel_store.count("panel_corrections") == 1


def test_changing_a_ballot_score_conflicts(panel_store):
    record_panel_case(panel_store, load("case.json"))
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    raw["ballots"][0]["score"] = 93
    with pytest.raises(PanelError) as caught:
        record_panel_case(panel_store, normalize_case(raw))
    assert caught.value.code == "PANEL_EVIDENCE_CONFLICT"


def test_changing_a_disclosure_target_conflicts(panel_store):
    record_panel_case(panel_store, load("case.json"))
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    raw["disclosures"][0]["bid_id"] = "BID-B"
    raw["contracts"] = []
    raw["governing_contract_id"] = ""
    with pytest.raises(PanelError) as caught:
        record_panel_case(panel_store, normalize_case(raw))
    assert caught.value.code == "PANEL_EVIDENCE_CONFLICT"


def test_late_disclosure_cannot_overwrite_signed_initial_review(panel_store):
    record_panel_case(panel_store, load("case.json"))
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    raw["disclosures"][0]["at"] = "2026-12-01T09:00:00+08:00"
    with pytest.raises(PanelError) as caught:
        record_panel_case(panel_store, normalize_case(raw))
    assert caught.value.code == "PANEL_EVIDENCE_CONFLICT"


def test_notice_correction_creates_traceable_update_and_keeps_v1(panel_store):
    case = load("case.json")
    record_panel_case(panel_store, case)
    # A tightened deadline rejects only the later BID-B, flipping the award to A.
    with panel_store.transaction():
        panel_store.connection.execute(
            "UPDATE notices SET deadline_at='2026-11-30T09:02:00+08:00', "
            "source_hash='hash-corrected' WHERE notice_id='TA-2026-0001'"
        )
    result = record_panel_case(panel_store, case)
    assert result["final_award"]["winner_bidder_id"] == "supplier-a"
    reviews = load_panel_reviews(panel_store, case["case_id"])
    assert reviews[0]["stage"] == "initial"
    assert reviews[0]["winner_bidder_id"] == "supplier-a"
    assert any(row["stage"] == "notice_update" for row in reviews)
    correction = panel_store.connection.execute(
        "SELECT * FROM panel_corrections WHERE case_id=? ORDER BY correction_id DESC LIMIT 1",
        (case["case_id"],),
    ).fetchone()
    assert correction is not None
    assert correction["from_winner_bidder_id"] == "supplier-b"
    assert correction["to_winner_bidder_id"] == "supplier-a"
    count = panel_store.count("panel_reviews")
    again = record_panel_case(panel_store, case)
    assert again["final_award"] == result["final_award"]
    assert panel_store.count("panel_reviews") == count


def test_case_scope_is_locked_across_reimport(panel_store):
    record_panel_case(panel_store, load("case.json"))
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    raw["round_id"] = "TA-2026-0001-R2"
    with pytest.raises(PanelError) as caught:
        record_panel_case(panel_store, normalize_case(raw))
    assert caught.value.code == "PANEL_SCOPE_MISMATCH"


def test_unauthorised_before_disclosure_is_flagged(panel_store):
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    for contract in raw["contracts"]:
        contract["authorized_at"] = "2026-12-01T11:00:00+08:00"
    preview = preview_panel_case(panel_store, normalize_case(raw))
    assert all(
        review["status"] == "pending_verification" for review in preview["rereviews"]
    )
    assert all("contract_unauthorised" in review["issues"]
               for review in preview["rereviews"])


def test_other_notice_case_is_unverifiable(panel_store):
    raw = json.loads(CASE.read_text(encoding="utf-8"))
    raw["notice_id"] = "TA-2026-9999"
    for bid in raw["bids"]:
        bid["notice_id"] = "TA-2026-9999"
    preview = preview_panel_case(panel_store, normalize_case(raw))
    assert preview["status"] == "unverifiable"
    assert preview["reason"] == "tender_notice_missing"
