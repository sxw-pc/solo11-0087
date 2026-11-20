"""Shared fixtures and helpers for the cross-bid security test suite."""

from __future__ import annotations

from pathlib import Path

from tender_anchor.models import CorrectionRecord, NoticeRecord
from tender_anchor.store import Store

ROOT = Path(__file__).parents[1]
BONDS = ROOT / "fixtures/bonds"
SHARED = BONDS / "shared_security.json"
REALLOC = BONDS / "reallocation_authorized.json"
OVER_FACE = BONDS / "reallocation_over_face.json"
NO_AUTH = BONDS / "reallocation_without_authorization.json"
MISSING_NOTICE = BONDS / "case_missing_notice.json"
GREEDY = BONDS / "case_greedy_counterexample.json"
NO_VALIDITY = BONDS / "case_guarantee_no_validity.json"


def notice(notice_id: str, deadline: str, budget: str = "1500000.00") -> NoticeRecord:
    return NoticeRecord(
        notice_id=notice_id, kind="tender", kind_label="招标公告",
        title=f"测试公告 {notice_id}", project_code="T", region="测试",
        purchaser="采购单位", agency="代理机构",
        publish_at="2026-11-01T00:00:00+08:00", deadline_at=deadline,
        budget_amount=budget, budget_currency="CNY", award_amount=None,
        winner="", related_notice_id="", content_excerpt="",
        source_url=f"http://127.0.0.1:5810/notices/{notice_id}",
        source_hash=f"hash-{notice_id}",
    )


def _correction(store: Store, correction_id: str, original_id: str, deadline: str) -> None:
    store.upsert_correction(
        CorrectionRecord(
            correction_notice_id=correction_id, original_notice_id=original_id,
            published_at="2026-11-12T00:00:00+08:00",
            changes=({"field": "deadline_at", "label": "截止时间",
                      "raw_value": deadline, "normalized_value": deadline},),
            source_url=f"http://127.0.0.1:5810/notices/{correction_id}",
        ),
        "run-test",
    )
    store.connection.commit()


def seed_store(path: Path) -> Store:
    store = Store(path)
    with store.transaction():
        store.start_run("run-test", "http://127.0.0.1:5810", "cfg")
        store.upsert_notice(
            notice("TA-2026-0001", "2026-12-05T09:30:00+08:00", "1350000.00"),
            "run-test", {}, [],
        )
        store.upsert_notice(
            notice("TA-2026-0002", "2026-12-03T14:00:00+08:00", "860500.00"),
            "run-test", {}, [],
        )
        store.upsert_notice(
            notice("TA-2026-0006", "2026-12-08T10:00:00+08:00"),
            "run-test", {}, [],
        )
    return store


def seed_extension(store: Store, deadline: str = "2026-12-12T10:00:00+08:00") -> None:
    with store.transaction():
        _correction(store, "TA-2026-0007", "TA-2026-0006", deadline)


def status_map(result: dict) -> dict[str, str]:
    return {row["bid_id"]: row["security_status"] for row in result["bids"]}
