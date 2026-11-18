"""Shared helpers for offline panel tests."""

from pathlib import Path

import pytest

from tender_anchor.models import NoticeRecord
from tender_anchor.panel_intake import read_panel_case
from tender_anchor.store import Store

PANEL = Path(__file__).parents[1] / "fixtures/panel"
CASE = PANEL / "case.json"

NOTICE = NoticeRecord(
    notice_id="TA-2026-0001", kind="tender", kind_label="招标公告",
    title="滨河片区智慧照明设备采购项目招标公告",
    project_code="YC-BH-2026-1101", region="示例市滨河片区",
    purchaser="示例市滨河片区市政设施服务中心", agency="示例澄明招标咨询有限公司",
    publish_at="2026-11-02T00:00:00+08:00",
    deadline_at="2026-12-01T09:30:00+08:00", budget_amount="1280000.00",
    budget_currency="CNY", award_amount=None, winner="", related_notice_id="",
    content_excerpt="示例内容", source_url="http://127.0.0.1:5810/notices/TA-2026-0001",
    source_hash="hash-initial",
)


def load(name):
    return read_panel_case(PANEL / name)


@pytest.fixture
def panel_store(tmp_path):
    store = Store(tmp_path / "panel.sqlite3")
    with store.transaction():
        store.start_run("run-panel-1", "http://127.0.0.1:5810", "config-hash")
        store.upsert_notice(NOTICE, "run-panel-1", {"version": 1}, [])
    yield store
    store.close()
