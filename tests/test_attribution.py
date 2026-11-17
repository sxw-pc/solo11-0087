import pytest

from tender_anchor.attribution import attribute_results, conclude_rounds
from tender_anchor.attribution_config import load_attribution
from tender_anchor.errors import ConfigError
from tender_anchor.models import (
    AttributionEntry, CorrectionRecord, NoticeRecord,
)
from tender_anchor.rules import apply_rules


def make_notice(notice_id, kind="tender", project="YC-T-1", purchaser="示例单位",
                publish="2026-11-01T00:00:00+08:00", related=""):
    return NoticeRecord(
        notice_id=notice_id,
        kind=kind,
        kind_label={"tender": "招标公告", "result": "结果公告",
                    "correction": "更正公告"}[kind],
        title=f"示例公告{notice_id}",
        project_code=project,
        region="示例市",
        purchaser=purchaser,
        agency="示例代理",
        publish_at=publish,
        deadline_at="2026-12-01T09:30:00+08:00" if kind != "result" else None,
        budget_amount="100000.00",
        budget_currency="CNY",
        award_amount="90000.00" if kind == "result" else None,
        winner="示例供应商" if kind == "result" else "",
        related_notice_id=related,
        content_excerpt="示例",
        source_url=f"http://127.0.0.1:5810/notices/{notice_id}",
        source_hash="a" * 64,
    )


def make_correction(correction_id, original_id, publish="2026-11-02T00:00:00+08:00"):
    return CorrectionRecord(
        correction_notice_id=correction_id,
        original_notice_id=original_id,
        published_at=publish,
        changes=({"field": "budget_amount", "normalized_value": "1"},),
        source_url=f"http://127.0.0.1:5810/notices/{correction_id}",
    )


def only_attribution(notices, corrections=(), entries=()):
    _, attributions = attribute_results(notices, corrections, entries)
    assert len(attributions) == 1
    return attributions[0]


def test_unique_candidate_is_inferred_not_declared():
    result = make_notice("TA-R1", kind="result", publish="2026-11-10T00:00:00+08:00")
    tender = make_notice("TA-T1")
    record = only_attribution([tender, result])
    assert record.status == "confirmed"
    assert record.evidence_level == "inferred"
    assert record.chosen_notice_id == "TA-T1"
    assert record.strict_status == "pending"
    assert record.auxiliary_status == "closed"
    assert "推断" in record.detail and "并非源公告声明" in record.detail


def test_declared_reference_closes_strict_ledger():
    result = make_notice("TA-R1", kind="result", related="TA-T1",
                         publish="2026-11-10T00:00:00+08:00")
    tender = make_notice("TA-T1")
    record = only_attribution([tender, result])
    assert (record.status, record.evidence_level) == ("confirmed", "declared")
    assert record.strict_status == record.auxiliary_status == "closed"


def test_explicit_list_closes_strict_ledger():
    result = make_notice("TA-R1", kind="result", publish="2026-11-10T00:00:00+08:00")
    tender = make_notice("TA-T1")
    entry = AttributionEntry("TA-R1", "TA-T1")
    record = only_attribution([tender, result], entries=[entry])
    assert (record.status, record.evidence_level) == ("confirmed", "explicit_list")
    assert record.strict_status == record.auxiliary_status == "closed"


def test_declared_and_list_agree_is_confirmed():
    result = make_notice("TA-R1", kind="result", related="TA-T1",
                         publish="2026-11-10T00:00:00+08:00")
    tender = make_notice("TA-T1")
    record = only_attribution(
        [tender, result], entries=[AttributionEntry("TA-R1", "TA-T1")]
    )
    assert record.status == "confirmed" and record.chosen_notice_id == "TA-T1"


def test_source_conflict_is_kept_not_chosen():
    result = make_notice("TA-R1", kind="result", related="TA-T2",
                         publish="2026-11-10T00:00:00+08:00")
    tenders = [make_notice("TA-T1"), make_notice("TA-T2")]
    record = only_attribution(
        tenders + [result], entries=[AttributionEntry("TA-R1", "TA-T1")]
    )
    assert record.status == "conflict"
    assert record.reason_code == "ATTRIBUTION_SOURCE_CONFLICT"
    assert record.chosen_notice_id == ""
    assert record.strict_status == record.auxiliary_status == "pending"


def test_list_target_mismatch_and_not_tender():
    result = make_notice("TA-R1", kind="result", publish="2026-11-10T00:00:00+08:00")
    other = make_notice("TA-T9", project="YC-OTHER")
    entry = AttributionEntry("TA-R1", "TA-T9")
    record = only_attribution([other, result], entries=[entry])
    assert record.status == "conflict"
    assert record.reason_code == "ATTRIBUTION_TARGET_MISMATCH"
    correction = make_notice("TA-C1", kind="correction")
    record = only_attribution(
        [correction, result], entries=[AttributionEntry("TA-R1", "TA-C1")]
    )
    assert record.reason_code == "ATTRIBUTION_TARGET_MISMATCH"


def test_list_target_missing_is_result_tender_missing():
    result = make_notice("TA-R1", kind="result", publish="2026-11-10T00:00:00+08:00")
    record = only_attribution(
        [result], entries=[AttributionEntry("TA-R1", "TA-T404")]
    )
    assert record.status == "pending"
    assert record.reason_code == "RESULT_TENDER_MISSING"


def test_result_before_tender_explicit_and_inferred():
    result = make_notice("TA-R1", kind="result", publish="2026-11-01T00:00:00+08:00")
    late = make_notice("TA-T1", publish="2026-11-05T00:00:00+08:00")
    record = only_attribution(
        [late, result], entries=[AttributionEntry("TA-R1", "TA-T1")]
    )
    assert record.status == "conflict"
    assert record.reason_code == "RESULT_BEFORE_TENDER"
    record = only_attribution([late, result])
    assert record.status == "pending"
    assert record.reason_code == "RESULT_BEFORE_TENDER"


def test_ambiguous_candidates_cannot_be_decided_by_code():
    result = make_notice("TA-R1", kind="result", publish="2026-11-10T00:00:00+08:00")
    tenders = [make_notice("TA-T1"), make_notice("TA-T2")]
    record = only_attribution(tenders + [result])
    assert record.status == "pending"
    assert record.reason_code == "RESULT_CANDIDATES_AMBIGUOUS"
    assert record.strict_status == record.auxiliary_status == "pending"


def test_missing_tender_and_correction_interleave():
    result = make_notice("TA-R1", kind="result", publish="2026-11-10T00:00:00+08:00")
    record = only_attribution([result])
    assert record.reason_code == "RESULT_TENDER_MISSING"
    tender = make_notice("TA-T1")
    late_fix = make_correction("TA-C1", "TA-T1", "2026-11-12T00:00:00+08:00")
    record = only_attribution([tender, result], corrections=[late_fix])
    assert record.reason_code == "RESULT_TENDER_MISSING"
    assert "更正" in record.detail


def test_round_candidates_record_all_checks():
    result = make_notice("TA-R1", kind="result", publish="2026-11-10T00:00:00+08:00")
    other = make_notice("TA-T9", project="YC-OTHER", purchaser="其他单位")
    tender = make_notice("TA-T1")
    candidates, _ = attribute_results([tender, other, result], [], [])
    by_tender = {item.tender_notice_id: item for item in candidates}
    assert by_tender["TA-T1"].eligible is True
    assert by_tender["TA-T9"].same_project_code is False
    assert by_tender["TA-T9"].eligible is False


def test_rules_round_and_result_items(rules):
    result = make_notice("TA-R1", kind="result", publish="2026-11-10T00:00:00+08:00")
    tenders = [make_notice("TA-T1"), make_notice("TA-T2")]
    _, attributions = attribute_results(tenders + [result], [], [])
    findings = apply_rules(tenders + [result], [], [], rules, attributions)
    found = {(item.notice_id, item.rule_code) for item in findings}
    assert ("TA-R1", "RESULT_CANDIDATES_AMBIGUOUS") in found
    assert ("TA-T1", "TENDER_RESULT_MISSING") in found
    assert ("TA-T2", "TENDER_RESULT_MISSING") in found


def test_rules_inferred_and_self_revised_items(rules):
    result = make_notice("TA-R1", kind="result", publish="2026-11-10T00:00:00+08:00")
    tender = make_notice("TA-T1")
    _, attributions = attribute_results([tender, result], [], [])
    fix = make_correction("TA-C1", "TA-R1", "2026-11-11T00:00:00+08:00")
    findings = apply_rules([tender, result], [], [fix], rules, attributions)
    found = {(item.notice_id, item.rule_code) for item in findings}
    assert ("TA-T1", "TENDER_RESULT_INFERRED") in found
    assert ("TA-T1", "TENDER_RESULT_MISSING") not in found
    assert ("TA-R1", "RESULT_SELF_REVISED") in found


def test_conclude_rounds_modes():
    tenders = [
        {"notice_id": "TA-T1", "project_code": "P", "purchaser": "U",
         "publish_at": "2026-11-01T00:00:00+08:00"},
        {"notice_id": "TA-T2", "project_code": "P", "purchaser": "U",
         "publish_at": "2026-11-02T00:00:00+08:00"},
    ]
    corrections = [{"correction_notice_id": "TA-C1", "original_notice_id": "TA-T1"}]
    attributions = [
        {"result_notice_id": "TA-R1", "chosen_notice_id": "TA-T1",
         "status": "confirmed", "evidence_level": "inferred"},
        {"result_notice_id": "TA-R2", "chosen_notice_id": "TA-T2",
         "status": "confirmed", "evidence_level": "explicit_list"},
    ]
    rounds = {item["tender_notice_id"]: item for item in
              conclude_rounds(tenders, corrections, attributions)}
    assert rounds["TA-T1"]["strict"]["status"] == "pending"
    assert rounds["TA-T1"]["auxiliary"]["status"] == "closed"
    assert rounds["TA-T1"]["auxiliary"]["evidence_level"] == "inferred"
    assert rounds["TA-T1"]["corrections"] == ["TA-C1"]
    assert rounds["TA-T2"]["strict"]["status"] == "closed"
    assert rounds["TA-T2"]["strict"]["result_notice_ids"] == ["TA-R2"]


def test_load_attribution_validation(tmp_path):
    path = tmp_path / "attribution.yaml"
    path.write_text(
        'entries:\n  - result_notice_id: "TA-R1"\n    tender_notice_id: "TA-T1"\n',
        encoding="utf-8",
    )
    loaded = load_attribution(path)
    assert loaded.entries == (AttributionEntry("TA-R1", "TA-T1"),)
    path.write_text(
        'entries:\n  - result_notice_id: "TA-R1"\n    tender_notice_id: "TA-T1"\n'
        '  - result_notice_id: "TA-R1"\n    tender_notice_id: "TA-T2"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as caught:
        load_attribution(path)
    assert caught.value.code == "ATTRIBUTION_CONFIG_INVALID"
    with pytest.raises(ConfigError) as caught:
        load_attribution(tmp_path / "missing.yaml")
    assert caught.value.code == "CONFIG_NOT_FOUND"
