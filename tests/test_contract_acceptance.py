import json
from pathlib import Path

import pytest

from tender_anchor.__main__ import main
from tender_anchor.contract_intake import (
    intake_contract_case,
    list_contract_reviews,
    read_contract_case,
)
from tender_anchor.export import export_all
from tender_anchor.store import Store

CONTRACTS = Path(__file__).parents[1] / "fixtures/contracts"
SEGMENTED = CONTRACTS / "delivery_segmented_conditional.json"
STRICT = CONTRACTS / "delivery_whole_strict.json"
LATE_FAIL = CONTRACTS / "delivery_late_fail.json"
INVALID = CONTRACTS / "invalid"


def _decisions(result):
    return [(item["event_id"], item["status"], item["reason"]) for item in result["decisions"]]


def _milestone(review, milestone_id):
    return next(item for item in review["milestones"] if item["milestone_id"] == milestone_id)


def test_segmented_conditional_adjudicates_full_chain(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        result = intake_contract_case(store, read_contract_case(SEGMENTED))
    assert result["status"] == "adjudicated"
    assert _decisions(result) == [
        ("D1", "admitted", "units_delivered"),
        ("I1", "admitted", "inspection_recorded"),
        ("I2", "held", "awaiting_dependency"),
        ("D2", "admitted", "unit_replaced"),
        ("I3", "admitted", "inspection_recorded"),
    ]
    i3 = result["decisions"][4]
    assert i3["effects"]["activated_provisional"] == ["M2"]
    review = result["review"]
    receipts = review["receipts"]
    assert [(row["receipt_id"], row["milestone_id"], row["unit_id"], row["amount"])
            for row in receipts] == [
        ("RCP-C-001-0001", "M1", "U1", "100000.00"),
        ("RCP-C-001-0002", "M1", "U2", "100000.00"),
        ("RCP-C-001-0003", "M1", "U3", "100000.00"),
        ("RCP-C-001-0004", "M1", "U4R", "100000.00"),
        ("RCP-C-001-0005", "M2", "", "200000.00"),
    ]
    assert all(row["kind"] == "payable" for row in receipts)
    m2_receipt = receipts[-1]
    # M2 takes effect only when I3 proves the hardware complete on Jan 12,
    # while the I2 evidence keeps its original Jan 9 timestamp.
    assert m2_receipt["trigger_event_id"] == "I3"
    assert m2_receipt["effective_at"] == "2027-01-12T12:00:00+08:00"
    assert m2_receipt["detail"]["evidence_event_id"] == "I2"
    assert m2_receipt["detail"]["evidence_at"] == "2027-01-09T12:00:00+08:00"
    m1 = _milestone(review, "M1")
    assert m1["status"] == "complete"
    # Completion is dated by the Jan 12 replacement, never backdated to Jan 9.
    assert m1["completed_at"] == "2027-01-12T12:00:00+08:00"
    # U4 and U4R are never both counted among the accepted four.
    assert m1["units"]["accepted"] == ["U1", "U2", "U3", "U4R"]
    assert m1["units"]["replaced"] == ["U4"]
    units = {row["unit_id"]: row for row in review["units"]}
    assert units["U4"]["status"] == "replaced" and units["U4"]["net_paid"] == "0.00"
    assert units["U4R"]["status"] == "accepted" and units["U4R"]["net_paid"] == "100000.00"
    m2 = _milestone(review, "M2")
    assert m2["status"] == "effective"
    assert m2["effective"] == {"event_id": "I2", "at": "2027-01-09T12:00:00+08:00"}
    assert m2["dependency_satisfied"] is True
    assert review["totals"] == {
        "award_amount": "600000.00",
        "payable_total": "600000.00",
        "outstanding_total": "0.00",
    }
    assert review["outstanding"] == []
    # The I2 decision keeps its original evidence time.
    i2 = result["decisions"][2]
    assert i2["at"] == "2027-01-09T12:00:00+08:00"


def test_whole_strict_defers_payment_and_requires_reinspection(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        result = intake_contract_case(store, read_contract_case(STRICT))
    assert result["status"] == "adjudicated"
    assert _decisions(result) == [
        ("D1", "admitted", "units_delivered"),
        ("I1", "admitted", "inspection_recorded"),
        ("I2", "rejected", "dependency_not_satisfied"),
        ("D2", "admitted", "unit_replaced"),
        ("I3", "admitted", "inspection_recorded"),
    ]
    review = result["review"]
    # Whole-milestone settlement: nothing before all four units pass, then one receipt.
    assert [(row["milestone_id"], row["unit_id"], row["amount"], row["trigger_event_id"])
            for row in review["receipts"]] == [("M1", "", "400000.00", "I3")]
    m1 = _milestone(review, "M1")
    assert m1["status"] == "complete"
    assert m1["completed_at"] == "2027-01-12T12:00:00+08:00"
    m2 = _milestone(review, "M2")
    assert m2["status"] == "pending"
    assert m2["effective"] is None
    assert review["totals"]["payable_total"] == "400000.00"
    assert review["totals"]["outstanding_total"] == "200000.00"
    assert review["outstanding"] == [
        {"milestone_id": "M2", "amount": "200000.00", "reasons": ["requires_inspection"]}
    ]
    # The inadmissible I2 keeps its Jan 9 time; it is not rewritten to Jan 12.
    i2 = result["decisions"][2]
    assert i2["at"] == "2027-01-09T12:00:00+08:00"
    assert i2["status"] == "rejected"


def test_jan9_partial_state_differs_between_contracts(crawled):
    _, paths, _ = crawled
    segmented = read_contract_case(SEGMENTED)
    strict = read_contract_case(STRICT)
    segmented["events"] = segmented["events"][:3]
    strict["events"] = strict["events"][:3]
    with Store(paths.db_path) as store:
        seg_result = intake_contract_case(store, segmented)
        strict_result = intake_contract_case(store, strict)
    seg_review = seg_result["review"]
    assert seg_review["totals"]["payable_total"] == "300000.00"
    assert [row["unit_id"] for row in seg_review["receipts"]] == ["U1", "U2", "U3"]
    assert _milestone(seg_review, "M1")["status"] == "open"
    seg_m2 = _milestone(seg_review, "M2")
    assert seg_m2["status"] == "held"
    assert seg_m2["held_evidence"] == {"event_id": "I2", "at": "2027-01-09T12:00:00+08:00"}
    assert not any(row["milestone_id"] == "M2" for row in seg_review["receipts"])
    strict_review = strict_result["review"]
    assert strict_review["totals"]["payable_total"] == "0.00"
    assert strict_review["receipts"] == []
    assert _milestone(strict_review, "M2")["status"] == "pending"
    # Neither contract may treat M2 as completed on Jan 9.
    for review in (seg_review, strict_review):
        m2 = _milestone(review, "M2")
        assert m2["status"] != "effective"
        assert m2["net_paid"] == "0.00"


def test_same_evidence_yields_different_payable_per_contract(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        segmented = intake_contract_case(store, read_contract_case(SEGMENTED))
        strict = intake_contract_case(store, read_contract_case(STRICT))
        reviews = list_contract_reviews(store)
    assert segmented["review"]["totals"]["payable_total"] == "600000.00"
    assert segmented["review"]["outstanding"] == []
    assert strict["review"]["totals"]["payable_total"] == "400000.00"
    assert strict["review"]["totals"]["outstanding_total"] == "200000.00"
    assert [item["contract_id"] for item in reviews] == ["C-001", "C-002"]


def test_replay_is_stable_and_changed_payloads_conflict(crawled):
    _, paths, _ = crawled
    case = read_contract_case(SEGMENTED)
    with Store(paths.db_path) as store:
        first = intake_contract_case(store, case)
        second = intake_contract_case(store, read_contract_case(SEGMENTED))
        assert first == second
        count = store.connection.execute(
            "SELECT COUNT(*) FROM contract_receipts"
        ).fetchone()[0]
        assert count == 5
        changed_event = json.loads(json.dumps(case))
        changed_event["events"][1]["accepted"] = ["U1", "U2"]
        with pytest.raises(ValueError, match="different terms"):
            intake_contract_case(store, changed_event)
        changed_delivery = json.loads(json.dumps(case))
        changed_delivery["events"][0]["units"] = ["U1", "U2", "U3", "U9"]
        with pytest.raises(ValueError, match="different terms"):
            intake_contract_case(store, changed_delivery)
        changed_identity = json.loads(json.dumps(case))
        changed_identity["contract"]["award_amount"] = "700000.00"
        with pytest.raises(ValueError, match="different terms"):
            intake_contract_case(store, changed_identity)
        changed_profile = json.loads(json.dumps(case))
        changed_profile["contract"]["acceptance_profile"] = "whole_strict"
        with pytest.raises(ValueError, match="different terms"):
            intake_contract_case(store, changed_profile)
        # Failed conflicts leave the persisted adjudication untouched.
        again = intake_contract_case(store, case)
        assert again == first


def test_missing_notice_is_unverifiable_and_persists_nothing(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        result = intake_contract_case(store, read_contract_case(INVALID / "missing_notice.json"))
        assert result["status"] == "unverifiable"
        assert result["reason"] == "tender_notice_missing"
        assert result["decisions"] == []
        for table in ("contract_cases", "contract_events", "contract_receipts"):
            count = store.connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            assert count == 0


def test_inspection_before_replacement_is_rejected(crawled):
    _, paths, _ = crawled
    case = read_contract_case(INVALID / "inspection_before_replacement.json")
    with Store(paths.db_path) as store:
        result = intake_contract_case(store, case)
        assert _decisions(result) == [
            ("D1", "admitted", "units_delivered"),
            ("I1", "admitted", "inspection_recorded"),
            ("I3", "rejected", "unit_not_delivered"),
            ("D2", "admitted", "unit_replaced"),
        ]
        i3 = result["decisions"][2]
        assert i3["effects"]["detail"] == {"units": ["U4R"]}
        review = result["review"]
        m1 = _milestone(review, "M1")
        assert m1["status"] == "open"
        assert m1["units"]["delivered"] == ["U4R"]
        assert review["totals"]["payable_total"] == "300000.00"
        # Re-import replays the recorded rejection instead of admitting I3.
        replay = intake_contract_case(store, case)
        assert replay == result


def test_replacement_unit_reused_by_another_milestone_is_rejected(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        result = intake_contract_case(
            store, read_contract_case(INVALID / "replacement_unit_reused.json")
        )
    assert _decisions(result) == [
        ("D1", "admitted", "units_delivered"),
        ("I1", "admitted", "inspection_recorded"),
        ("D2", "admitted", "unit_replaced"),
        ("D3", "admitted", "units_delivered"),
        ("R9", "rejected", "unit_already_known"),
    ]
    r9 = result["decisions"][4]
    assert r9["effects"]["detail"]["units"] == ["U4R"]
    assert r9["effects"]["detail"]["owner_milestone"] == ["M1"]
    review = result["review"]
    m3 = _milestone(review, "M3")
    assert m3["units"]["delivered"] == ["X1"]
    assert review["totals"]["payable_total"] == "300000.00"


def test_milestone_amounts_over_contract_award_are_rejected():
    with pytest.raises(ValueError, match="exceed contract award"):
        read_contract_case(INVALID / "amount_over_cap.json")


def test_late_evidence_appends_adjustment_without_rewriting_receipts(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        base = intake_contract_case(store, read_contract_case(SEGMENTED))
        assert base["review"]["totals"]["payable_total"] == "600000.00"
        original_receipts = base["review"]["receipts"]
        followup = intake_contract_case(store, read_contract_case(LATE_FAIL))
        assert _decisions(followup) == [("I9", "admitted", "milestone_failed_settlement_adjusted")]
        decision = followup["decisions"][0]
        assert decision["late"] is True
        assert decision["at"] == "2027-01-09T18:00:00+08:00"
        review = followup["review"]
        # Previously issued receipts are untouched; the late fail appends one adjustment.
        assert review["receipts"][:5] == original_receipts
        adjustment = review["receipts"][5]
        assert adjustment["receipt_id"] == "RCP-C-001-0006"
        assert adjustment["kind"] == "adjustment"
        assert adjustment["amount"] == "-200000.00"
        assert adjustment["trigger_event_id"] == "I9"
        assert review["totals"]["payable_total"] == "400000.00"
        assert _milestone(review, "M2")["status"] == "breached"
        assert review["outstanding"] == [
            {"milestone_id": "M2", "amount": "200000.00", "reasons": ["settlement_adjustment"]}
        ]
        # Replaying the late evidence keeps the same adjudication and ledger.
        replay = intake_contract_case(store, read_contract_case(LATE_FAIL))
        assert replay == followup
        count = store.connection.execute(
            "SELECT COUNT(*) FROM contract_receipts"
        ).fetchone()[0]
        assert count == 6


def test_notice_change_blocks_event_admission(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        first = intake_contract_case(store, read_contract_case(SEGMENTED))
        assert first["status"] == "adjudicated"
        store.connection.execute(
            "UPDATE notices SET kind='result' WHERE notice_id='TA-2026-0001'"
        )
        store.connection.commit()
        followup = intake_contract_case(store, read_contract_case(LATE_FAIL))
        assert followup["status"] == "unverifiable"
        assert followup["reason"] == "tender_notice_missing"
        count = store.connection.execute(
            "SELECT COUNT(*) FROM contract_events"
        ).fetchone()[0]
        assert count == 5


def test_export_reflects_adjudication_and_later_events(crawled):
    _, paths, _ = crawled
    case = read_contract_case(SEGMENTED)
    first3 = json.loads(json.dumps(case))
    first3["events"] = first3["events"][:3]
    with Store(paths.db_path) as store:
        intake_contract_case(store, first3)
        result = export_all(store, paths)
        doc = json.loads((paths.exports / "contracts.json").read_text(encoding="utf-8"))
        assert result["contract_count"] == 1
        assert doc["contract_count"] == 1
        assert doc["contracts"][0]["totals"]["payable_total"] == "300000.00"
        # New inspection/replacement evidence changes the persisted export.
        intake_contract_case(store, case)
        export_all(store, paths)
        doc = json.loads((paths.exports / "contracts.json").read_text(encoding="utf-8"))
        review = doc["contracts"][0]
        assert review["totals"]["payable_total"] == "600000.00"
        assert _milestone(review, "M1")["status"] == "complete"
        # Notice exports keep their existing shape.
        notices = json.loads((paths.exports / "notices.json").read_text(encoding="utf-8"))
        assert notices["notice_count"] == 8


def test_cli_contract_intake(crawled, capsys):
    _, paths, _ = crawled
    code = main([
        "contract-intake",
        "--data-dir", str(paths.root),
        "--case", str(SEGMENTED),
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "adjudicated"
    assert payload["review"]["totals"]["payable_total"] == "600000.00"
    assert [item["event_id"] for item in payload["decisions"]] == ["D1", "I1", "I2", "D2", "I3"]
