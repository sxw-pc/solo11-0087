from tender_anchor.models import (
    AttachmentRecord, CorrectionRecord, FieldSourceRecord, NoticeRecord,
)
from tender_anchor.store import Store


def make_notice(source_hash="hash-1", title="示例招标公告"):
    return NoticeRecord(
        notice_id="TA-2026-9001",
        kind="tender",
        kind_label="招标公告",
        title=title,
        project_code="YC-TEST-9001",
        region="示例市测试片区",
        purchaser="示例采购单位",
        agency="示例代理机构",
        publish_at="2026-11-01T00:00:00+08:00",
        deadline_at="2026-12-01T09:30:00+08:00",
        budget_amount="100000.00",
        budget_currency="CNY",
        award_amount=None,
        winner="",
        related_notice_id="",
        content_excerpt="示例内容",
        source_url="http://127.0.0.1:5810/notices/TA-2026-9001",
        source_hash=source_hash,
    )


def test_notice_and_provenance_are_idempotent(tmp_path):
    store = Store(tmp_path / "data" / "test.sqlite3")
    source = FieldSourceRecord(
        "budget_amount", "100,000.00元", "100000.00", "meta_table", "table tr"
    )
    with store.transaction():
        store.start_run("run-1", "http://127.0.0.1:5810", "config-hash")
        assert store.upsert_notice(make_notice(), "run-1", {"title": "第一版"}, [source]) == "inserted"
        assert store.archive_snapshot(
            "TA-2026-9001", "http://127.0.0.1:5810/notices/TA-2026-9001",
            "hash-1", "raw/TA-2026-9001/hash-1.html", 100,
        ) is True
        store.upsert_attachment(
            AttachmentRecord("TA-2026-9001", "example.txt",
                             "http://127.0.0.1:5810/attachments/example.txt",
                             "abc", 12, "attachments/example.txt", "text/plain",
                             "stored", None),
            "run-1",
        )
        store.upsert_correction(
            CorrectionRecord("TA-2026-0003", "TA-2026-9001",
                             "2026-11-02T00:00:00+08:00",
                             ({"field": "budget_amount", "normalized_value": "110000.00"},),
                             "http://127.0.0.1:5810/notices/TA-2026-0003"),
            "run-1",
        )
        store.sync_review_items(
            [{"notice_id": "TA-2026-9001", "rule_code": "MISSING_AGENCY",
              "severity": "warning", "message": "缺少代理机构", "field_name": "agency"}],
            "run-1",
        )
    assert store.count("notices") == 1
    assert store.count("notice_revisions") == 1
    assert store.count("raw_snapshots") == 1
    assert store.count("attachments") == 1
    assert store.count("corrections") == 1
    assert store.count("field_sources") == 1

    with store.transaction():
        store.start_run("run-2", "http://127.0.0.1:5810", "config-hash")
        assert store.upsert_notice(make_notice(), "run-2", {"title": "第一版"}, [source]) == "unchanged"
        assert store.archive_snapshot(
            "TA-2026-9001", "http://127.0.0.1:5810/notices/TA-2026-9001",
            "hash-1", "raw/TA-2026-9001/hash-1.html", 100,
        ) is False
        store.sync_review_items(
            [{"notice_id": "TA-2026-9001", "rule_code": "MISSING_AGENCY",
              "severity": "warning", "message": "缺少代理机构", "field_name": "agency"}],
            "run-2",
        )
    assert store.count("notices") == 1
    assert store.count("notice_revisions") == 1
    assert store.count("raw_snapshots") == 1
    assert store.review_count("open") == 1
    assert store.get_notice("TA-2026-9001")["revision"] == 1

    with store.transaction():
        store.start_run("run-3", "http://127.0.0.1:5810", "config-hash")
        assert store.upsert_notice(
            make_notice("hash-2", "更新后的示例公告"), "run-3", {"title": "第二版"}, [source]
        ) == "updated"
        store.sync_review_items([], "run-3")
    assert store.count("notice_revisions") == 2
    assert store.get_notice("TA-2026-9001")["revision"] == 2
    assert store.get_notice("TA-2026-9001")["title"] == "更新后的示例公告"
    assert store.review_count("open") == 0
    assert store.review_count("resolved") == 1
    bundle = store.get_bundle("TA-2026-9001")
    assert bundle["corrections"][0]["correction_notice_id"] == "TA-2026-0003"
    assert store.stats()["notices"] == 1
    store.close()
