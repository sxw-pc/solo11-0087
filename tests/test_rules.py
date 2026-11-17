from dataclasses import replace

from tender_anchor.models import AttachmentRecord, CorrectionRecord, NoticeRecord
from tender_anchor.rules import apply_rules


def notice(**changes):
    base = NoticeRecord(
        notice_id="TA-2026-1000",
        kind="tender",
        kind_label="招标公告",
        title="示例测试项目招标公告",
        project_code="YC-TEST-1000",
        region="示例市",
        purchaser="示例采购单位",
        agency="示例代理机构",
        publish_at="2026-11-01T00:00:00+08:00",
        deadline_at="2026-12-01T09:30:00+08:00",
        budget_amount="100000.00",
        budget_currency="CNY",
        award_amount=None,
        winner="",
        related_notice_id="",
        content_excerpt="示例",
        source_url="http://127.0.0.1:5810/notices/TA-2026-1000",
        source_hash="a" * 64,
    )
    return replace(base, **changes)


def codes(findings):
    return {(item.notice_id, item.rule_code) for item in findings}


def test_required_fields_and_budget_range(rules):
    findings = apply_rules(
        [
            notice(notice_id="TA-2026-1001", budget_amount=None),
            notice(notice_id="TA-2026-1002", budget_amount="10.00"),
            notice(notice_id="TA-2026-1003", deadline_at=None),
        ],
        [],
        [],
        rules,
    )
    found = codes(findings)
    assert ("TA-2026-1001", "MISSING_BUDGET_AMOUNT") in found
    assert ("TA-2026-1002", "BUDGET_OUT_OF_RANGE") in found
    assert ("TA-2026-1003", "MISSING_DEADLINE_AT") in found


def test_deadline_attachment_and_result_rules(rules):
    failed = [
        AttachmentRecord("TA-2026-1004", "a.txt", "http://127.0.0.1:5810/a", None,
                         0, None, "", "failed", "FETCH_HTTP_ERROR"),
        AttachmentRecord("TA-2026-1004", "b.txt", "http://127.0.0.1:5810/b", None,
                         0, None, "", "failed", "FETCH_HTTP_ERROR"),
    ]
    findings = apply_rules(
        [
            notice(notice_id="TA-2026-1004",
                   deadline_at="2026-10-01T00:00:00+08:00"),
            notice(notice_id="TA-2026-1005", kind="result", kind_label="结果公告",
                   deadline_at=None, award_amount=None, winner=""),
        ],
        failed,
        [],
        rules,
    )
    found = codes(findings)
    assert ("TA-2026-1004", "DEADLINE_BEFORE_PUBLISH") in found
    assert ("TA-2026-1004", "ATTACHMENT_FETCH_FAILED") in found
    assert ("TA-2026-1005", "RESULT_AWARD_MISSING") in found
    assert ("TA-2026-1005", "MISSING_DEADLINE_AT") not in found
    assert sum(item[1] == "ATTACHMENT_FETCH_FAILED" for item in found) == 1


def test_correction_relation_rules_and_deterministic_order(rules):
    correction = CorrectionRecord(
        correction_notice_id="TA-2026-1009",
        original_notice_id="TA-2026-9999",
        published_at="2026-11-02T00:00:00+08:00",
        changes=(),
        source_url="http://127.0.0.1:5810/notices/TA-2026-1009",
    )
    findings = apply_rules([notice()], [], [correction], rules)
    found = codes(findings)
    assert ("TA-2026-1009", "CORRECTION_ORIGINAL_MISSING") in found
    assert ("TA-2026-1009", "CORRECTION_CHANGES_EMPTY") in found
    ordered = [(item.notice_id, item.rule_code) for item in findings]
    assert ordered == sorted(ordered)
    assert all(item.severity in {"warning", "error"} for item in findings)
