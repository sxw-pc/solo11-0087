import csv
import json

import pytest
import requests

from tender_anchor.errors import FetchError
from tender_anchor.fetch import LoopbackPolicy, SafeFetcher
from tender_anchor.pipeline import run_crawl
from tender_anchor.store import Store


def test_full_crawl_is_complete_and_traceable(crawled):
    summary, paths, _ = crawled
    assert summary.pages_fetched == 3
    assert summary.notices_seen == 8
    assert summary.notices_inserted == 8
    assert summary.notices_unchanged == 0
    assert summary.attachments_ok == 5
    assert summary.attachments_failed == 1
    assert summary.review_open == 2
    assert summary.status == "completed_with_errors"
    assert any(item["code"] == "NORMALIZE_AMOUNT" for item in summary.errors)
    with Store(paths.db_path) as store:
        assert store.count("notices") == 8
        assert store.count("notice_revisions") == 8
        assert store.count("raw_snapshots") == 8
        assert store.count("attachments") == 6
        assert store.count("corrections") == 2
        assert store.count("runs") == 1
        assert store.get_notice("TA-2026-0001")["budget_amount"] == "1280000.00"
        assert store.get_notice("TA-2026-0005")["budget_amount"] is None
        correction = store.get_corrections("TA-2026-0001")[0]
        assert correction["correction_notice_id"] == "TA-2026-0003"
        assert correction["original_notice_id"] == "TA-2026-0001"
        failed = [item for item in store.get_attachments("TA-2026-0005")
                  if item["status"] == "failed"]
        assert failed[0]["error_code"] == "FETCH_HTTP_ERROR"
        assert all(item["stored_path"] and ".." not in item["stored_path"]
                   for item in store.get_attachments("TA-2026-0001"))


def test_second_run_is_idempotent_and_exports_are_ordered(crawled, rules):
    _, paths, config = crawled
    second = run_crawl(config, rules, paths)
    assert second.notices_unchanged == 8
    assert second.notices_inserted == 0
    assert second.review_open == 2
    with Store(paths.db_path) as store:
        assert store.count("notices") == 8
        assert store.count("notice_revisions") == 8
        assert store.count("raw_snapshots") == 8
        assert store.count("attachments") == 6
        assert store.count("review_items") == 2
        assert store.count("runs") == 2
    payload = json.loads((paths.exports / "notices.json").read_text(encoding="utf-8"))
    ids = [item["notice_id"] for item in payload["notices"]]
    assert ids == sorted(ids)
    assert payload["notice_count"] == 8
    assert payload["notices"][2]["corrections"][0]["changes"][0]["normalized_value"] == "1350000.00"
    with (paths.exports / "notices.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 8
    assert [row["notice_id"] for row in rows] == sorted(item["notice_id"] for item in rows)
    assert len(list(paths.reports.glob("run-*.json"))) == 2
    assert all((paths.root / item["stored_path"]).is_file()
               for item in payload["notices"][0]["attachments"])


def test_non_loopback_is_blocked_before_http(monkeypatch):
    audit = []
    fetcher = SafeFetcher(
        LoopbackPolicy(("127.0.0.1", "localhost", "::1")),
        audit=lambda kind, target, detail: audit.append((kind, target, detail)),
    )
    called = {"value": False}

    def forbidden(*args, **kwargs):
        called["value"] = True
        raise AssertionError("外部网络不应被调用")

    monkeypatch.setattr(requests.Session, "get", forbidden)
    with pytest.raises(FetchError) as caught:
        fetcher.fetch("http://203.0.113.10/notices")
    assert caught.value.code == "FETCH_HOST_BLOCKED"
    assert called["value"] is False
    assert audit and audit[0][0] == "FETCH_BLOCKED"
    fetcher.close()


def test_redirects_are_never_followed(monkeypatch):
    class FakeResponse:
        status_code = 302
        headers = {"Location": "/notices?page=2"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        requests.Session, "get", lambda *args, **kwargs: FakeResponse()
    )
    fetcher = SafeFetcher(LoopbackPolicy(("127.0.0.1",)))
    with pytest.raises(FetchError) as caught:
        fetcher.fetch("http://127.0.0.1:5810/redirect-demo")
    assert caught.value.code == "FETCH_REDIRECT_BLOCKED"
    fetcher.close()
