"""Pure adjudication tests: evidence chain, preparation window, contract modes."""

from pathlib import Path

import pytest

from tender_anchor.clarification_intake import read_clarification_case
from tender_anchor.parity import (
    CERTIFIED,
    PENDING,
    REJECTED,
    UNVERIFIABLE,
    PER_PERSON_RECEIPT,
    PUBLIC_OPEN,
    adjudicate,
    build_case_view,
)

CASES = Path(__file__).parents[1] / "fixtures/clarifications/cases"

NOTICE_0001 = {
    "notice_id": "TA-2026-0001",
    "kind": "tender",
    "deadline_at": "2026-12-01T09:30:00+08:00",
    "source_url": "http://127.0.0.1:5810/notices/TA-2026-0001",
    "source_hash": "h" * 64,
}
NOTICE_0002 = {
    "notice_id": "TA-2026-0002",
    "kind": "tender",
    "deadline_at": "2026-12-03T14:00:00+08:00",
    "source_url": "http://127.0.0.1:5810/notices/TA-2026-0002",
    "source_hash": "j" * 64,
}
CORRECTION_0003 = [
    {
        "correction_notice_id": "TA-2026-0003",
        "original_notice_id": "TA-2026-0001",
        "published_at": "2026-11-06T00:00:00+08:00",
        "source_url": "http://127.0.0.1:5810/notices/TA-2026-0003",
        "changes": [
            {
                "field": "deadline_at",
                "label": "截止时间",
                "raw_value": "2026年12月05日 09:30",
                "normalized_value": "2026-12-05T09:30:00+08:00",
            }
        ],
    }
]


def load(name: str) -> dict:
    return read_clarification_case(CASES / f"{name}.json")


def test_correction_deadline_extends_preparation_to_ten_days():
    case = load("baseline")
    view = build_case_view(case, NOTICE_0001, CORRECTION_0003)
    assert view.deadline.basis == "correction:TA-2026-0003"
    assert view.deadline.deadline_at == "2026-12-05T09:30:00+08:00"
    assert view.deadline.original_deadline_at == "2026-12-01T09:30:00+08:00"
    assert view.preparation_days == 10
    assert view.window_sufficient is True


def test_original_deadline_without_correction_fails_the_seven_day_window():
    # Public terms released 2026-11-25 10:00; original deadline 2026-12-01 09:30.
    case = load("baseline")
    decision = adjudicate(case, NOTICE_0001, [], PUBLIC_OPEN)
    assert decision["preparation_days"] == 6
    assert decision["window_sufficient"] is False
    assert decision["status"] == REJECTED
    assert decision["reason"] == "preparation_window_short"
    assert "preparation_window_short" in decision["blocking_reasons"]


def test_corrected_deadline_certifies_public_open_even_without_clicks():
    case = load("baseline")
    decision = adjudicate(case, NOTICE_0001, CORRECTION_0003, PUBLIC_OPEN)
    assert decision["status"] == CERTIFIED
    assert decision["reason"] == "public_terms_equal_availability"
    # supplier-a never clicked the public answer, but the open contract does
    # not require individual receipts.
    rows = {row["bidder_id"]: row for row in decision["participants"]}
    assert rows["supplier-a"]["public_receipt_at"] is None
    assert rows["supplier-b"]["public_receipt_at"] == "2026-11-26T09:00:00+08:00"


def test_per_person_contract_pending_when_receipts_are_incomplete():
    case = load("baseline")
    decision = adjudicate(case, NOTICE_0001, CORRECTION_0003, PER_PERSON_RECEIPT)
    assert decision["status"] == PENDING
    assert decision["reason"] == "awaiting_individual_receipts"
    rows = {row["bidder_id"]: row for row in decision["participants"]}
    assert rows["supplier-a"]["status"] == PENDING
    assert rows["supplier-a"]["reason"] == "individual_receipt_missing"
    assert rows["supplier-b"]["status"] == CERTIFIED


def test_private_draft_receipt_never_counts_as_public_receipt():
    case = load("private_access_conflict")
    decision = adjudicate(case, NOTICE_0001, CORRECTION_0003, PER_PERSON_RECEIPT)
    rows = {row["bidder_id"]: row for row in decision["participants"]}
    assert rows["supplier-a"]["public_receipt_at"] is None
    assert rows["supplier-a"]["status"] == PENDING
    # The public-open contract still certifies on availability alone.
    public = adjudicate(case, NOTICE_0001, CORRECTION_0003, PUBLIC_OPEN)
    assert public["status"] == CERTIFIED


def test_adding_real_public_receipt_flips_per_person_to_certified():
    case = load("public_receipt_complete")
    decision = adjudicate(case, NOTICE_0001, CORRECTION_0003, PER_PERSON_RECEIPT)
    assert decision["status"] == CERTIFIED
    assert decision["reason"] == "all_individual_receipts_recorded"
    rows = {row["bidder_id"]: row for row in decision["participants"]}
    assert rows["supplier-a"]["public_receipt_at"] == "2026-11-25T14:00:00+08:00"
    assert rows["supplier-b"]["public_receipt_at"] == "2026-11-26T09:00:00+08:00"


def test_late_download_is_not_backdated_to_publication_day():
    case = load("baseline")
    decision = adjudicate(case, NOTICE_0001, CORRECTION_0003, PER_PERSON_RECEIPT)
    rows = {row["bidder_id"]: row for row in decision["participants"]}
    # b downloaded on the 26th; the recorded receipt keeps that timestamp.
    assert rows["supplier-b"]["public_receipt_at"] == "2026-11-26T09:00:00+08:00"
    assert rows["supplier-a"]["public_receipt_at"] is None


def test_missing_notice_makes_everything_unverifiable():
    case = load("notice_missing")
    for mode in (PUBLIC_OPEN, PER_PERSON_RECEIPT):
        decision = adjudicate(case, None, [], mode)
        assert decision["status"] == UNVERIFIABLE
        assert "tender_notice_missing" in decision["blocking_reasons"]
        assert "deadline_evidence_missing" in decision["blocking_reasons"]


def test_answer_id_content_conflict_blocks_certification():
    case = load("answer_id_content_conflict")
    view = build_case_view(case, NOTICE_0001, CORRECTION_0003)
    assert "answer_identity_consistent" in view.failed_checks()
    decision = adjudicate(case, NOTICE_0001, CORRECTION_0003, PUBLIC_OPEN)
    assert decision["status"] == UNVERIFIABLE
    assert "answer_id_content_conflict" in decision["blocking_reasons"]


def test_original_notice_only_uses_original_deadline_basis():
    case = load("no_correction")
    view = build_case_view(case, NOTICE_0002, [])
    assert view.deadline.basis == "original_notice"
    assert view.preparation_days == 7
    assert view.window_sufficient is True
    decision = adjudicate(case, NOTICE_0002, [], PER_PERSON_RECEIPT)
    assert decision["status"] == CERTIFIED


def test_short_window_rejects_both_contract_modes(tmp_path):
    # Assemble a case whose public release leaves fewer than seven days.
    case = load("baseline")
    late_release = "2026-11-28T10:00:00+08:00"
    for answer in case["answers"]:
        if answer["visibility"] == "public":
            answer["at"] = late_release
    for event in case["access_events"]:
        if event["answer_id"] == "A-PUBLIC":
            event["at"] = "2026-11-29T09:00:00+08:00"
    decision_po = adjudicate(case, NOTICE_0001, [], PUBLIC_OPEN)
    decision_pp = adjudicate(case, NOTICE_0001, [], PER_PERSON_RECEIPT)
    assert decision_po["status"] == REJECTED
    assert decision_pp["status"] == REJECTED
    assert decision_po["preparation_days"] == 3


def test_correction_change_moves_window_and_status():
    # Same case, two correction worlds: the 12-05 extension certifies,
    # a hypothetical 11-30 correction does not.
    case = load("baseline")
    short_correction = [
        {
            "correction_notice_id": "TA-2026-0010",
            "original_notice_id": "TA-2026-0001",
            "published_at": "2026-11-20T00:00:00+08:00",
            "source_url": "http://127.0.0.1:5810/notices/TA-2026-0010",
            "changes": [
                {
                    "field": "deadline_at",
                    "label": "截止时间",
                    "raw_value": "2026年11月30日 09:30",
                    "normalized_value": "2026-11-30T09:30:00+08:00",
                }
            ],
        }
    ]
    ok = adjudicate(case, NOTICE_0001, CORRECTION_0003, PUBLIC_OPEN)
    bad = adjudicate(case, NOTICE_0001, short_correction, PUBLIC_OPEN)
    assert ok["status"] == CERTIFIED
    assert bad["status"] == REJECTED
    assert bad["deadline"]["correction_notice_id"] == "TA-2026-0010"
    assert bad["preparation_days"] == 5  # 11-25 release -> 11-30 deadline


def test_ta_0003_extension_is_what_makes_the_baseline_certifiable():
    """Without TA-2026-0003 the original 12-01 deadline fails the seven-day rule."""
    case = load("baseline")
    without = adjudicate(case, NOTICE_0001, [], PUBLIC_OPEN)
    with_ext = adjudicate(case, NOTICE_0001, CORRECTION_0003, PUBLIC_OPEN)
    assert without["deadline"]["basis"] == "original_notice"
    assert without["preparation_days"] == 6
    assert without["status"] == REJECTED
    assert with_ext["deadline"]["basis"] == "correction:TA-2026-0003"
    assert with_ext["preparation_days"] == 10
    assert with_ext["status"] == CERTIFIED


def test_unknown_contract_mode_is_rejected():
    case = load("baseline")
    with pytest.raises(ValueError):
        adjudicate(case, NOTICE_0001, CORRECTION_0003, "paper_fax")


def test_exact_seven_day_window_is_sufficient_but_one_second_less_is_not():
    case = load("baseline")
    notice = dict(NOTICE_0001)
    # Public release 2026-11-25T10:00; deadline exactly seven days later.
    notice["deadline_at"] = "2026-12-02T10:00:00+08:00"
    exact = adjudicate(case, notice, [], PUBLIC_OPEN)
    assert exact["window_sufficient"] is True
    assert exact["status"] == CERTIFIED
    # One second short of the full seven-day window.
    notice["deadline_at"] = "2026-12-02T09:59:59+08:00"
    short = adjudicate(case, notice, [], PUBLIC_OPEN)
    assert short["window_sufficient"] is False
    assert short["status"] == REJECTED
