import csv
import json
from pathlib import Path

import pytest
import requests

from tender_anchor.app_paths import ensure_layout
from tender_anchor.extract import extract_notice
from tender_anchor.mock_portal.app import create_mock_app
from tender_anchor.mock_portal.fixtures import build_catalog
from tender_anchor.pipeline import run_crawl
from tender_anchor.store import Store
from tender_anchor.web import create_review_app

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def isolated_catalog():
    document = json.loads(
        (FIXTURES / "attribution_notices.json").read_text(encoding="utf-8")
    )
    return build_catalog(document)


@pytest.fixture
def isolated_url(portal_server):
    return portal_server(create_mock_app(catalog=isolated_catalog()))


def crawl_isolated(tmp_path, portal, config_builder, rules, entries=()):
    config = config_builder(portal)
    paths = ensure_layout(tmp_path / "data")
    from tender_anchor.attribution_config import load_attribution

    list_path = tmp_path / "attribution.yaml"
    lines = ["entries:"] + [
        f'  - {{result_notice_id: "{r}", tender_notice_id: "{t}"}}' for r, t in entries
    ]
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary = run_crawl(config, rules, paths, load_attribution(list_path))
    return summary, paths


def open_review_codes(paths):
    with Store(paths.db_path) as store:
        return {
            (item["notice_id"], item["rule_code"])
            for item in store.get_review_items(status="open")
        }


def test_second_tender_ambiguity_is_not_decided_by_code(
    tmp_path, isolated_url, config_builder, rules
):
    summary, paths = crawl_isolated(tmp_path, isolated_url, config_builder, rules)
    assert summary.notices_seen == 5
    with Store(paths.db_path) as store:
        assert store.count("attributions") == 1
        assert store.count("round_candidates") == 2
        record = store.get_attribution("TA-2026-1005")
        assert record["status"] == "pending"
        assert record["reason_code"] == "RESULT_CANDIDATES_AMBIGUOUS"
        assert record["chosen_notice_id"] == ""
        assert record["strict_status"] == record["auxiliary_status"] == "pending"
        candidates = {
            item["tender_notice_id"]: item["eligible"]
            for item in store.get_round_candidates("TA-2026-1005")
        }
        assert candidates == {"TA-2026-1001": 1, "TA-2026-1003": 1}
    codes = open_review_codes(paths)
    assert ("TA-2026-1005", "RESULT_CANDIDATES_AMBIGUOUS") in codes
    assert ("TA-2026-1001", "TENDER_RESULT_MISSING") in codes
    assert ("TA-2026-1003", "TENDER_RESULT_MISSING") in codes
    payload = json.loads((paths.exports / "notices.json").read_text(encoding="utf-8"))
    rounds = {item["tender_notice_id"]: item for item in payload["rounds"]}
    for tender_id in ("TA-2026-1001", "TA-2026-1003"):
        assert rounds[tender_id]["strict"]["status"] == "pending"
        assert rounds[tender_id]["auxiliary"]["status"] == "pending"
    assert rounds["TA-2026-1001"]["corrections"] == ["TA-2026-1002"]
    assert rounds["TA-2026-1003"]["corrections"] == ["TA-2026-1004"]
    client = create_review_app(paths.db_path, data_dir=paths.root).test_client()
    detail = client.get("/notices/TA-2026-1005").get_data(as_text=True)
    assert "RESULT_CANDIDATES_AMBIGUOUS" in detail and "待核" in detail
    assert "TA-2026-1001" in detail and "TA-2026-1003" in detail


def test_explicit_list_closes_only_designated_round(
    tmp_path, isolated_url, config_builder, rules
):
    entries = [("TA-2026-1005", "TA-2026-1003")]
    _, paths = crawl_isolated(tmp_path, isolated_url, config_builder, rules, entries)
    with Store(paths.db_path) as store:
        record = store.get_attribution("TA-2026-1005")
        assert record["status"] == "confirmed"
        assert record["evidence_level"] == "explicit_list"
        assert record["chosen_notice_id"] == "TA-2026-1003"
        assert record["strict_status"] == record["auxiliary_status"] == "closed"
    codes = open_review_codes(paths)
    assert ("TA-2026-1003", "TENDER_RESULT_MISSING") not in codes
    assert ("TA-2026-1001", "TENDER_RESULT_MISSING") in codes
    assert ("TA-2026-1005", "RESULT_CANDIDATES_AMBIGUOUS") not in codes
    payload = json.loads((paths.exports / "notices.json").read_text(encoding="utf-8"))
    rounds = {item["tender_notice_id"]: item for item in payload["rounds"]}
    assert rounds["TA-2026-1003"]["strict"]["status"] == "closed"
    assert rounds["TA-2026-1003"]["strict"]["result_notice_ids"] == ["TA-2026-1005"]
    assert rounds["TA-2026-1003"]["strict"]["evidence_level"] == "explicit_list"
    assert rounds["TA-2026-1001"]["strict"]["status"] == "pending"
    result_doc = next(
        item for item in payload["notices"] if item["notice_id"] == "TA-2026-1005"
    )
    assert result_doc["attribution"]["tender_notice_id"] == "TA-2026-1003"
    assert result_doc["attribution"]["evidence_level"] == "explicit_list"
    with (paths.exports / "notices.csv").open(encoding="utf-8", newline="") as handle:
        rows = {row["notice_id"]: row for row in csv.DictReader(handle)}
    assert rows["TA-2026-1005"]["attributed_tender_id"] == "TA-2026-1003"
    assert rows["TA-2026-1005"]["attribution_evidence"] == "explicit_list"
    assert rows["TA-2026-1003"]["round_strict_status"] == "closed"
    assert rows["TA-2026-1001"]["round_strict_status"] == "pending"
    client = create_review_app(paths.db_path, data_dir=paths.root).test_client()
    detail = client.get("/notices/TA-2026-1005").get_data(as_text=True)
    assert "本地显式清单" in detail and "已确认" in detail
    round_page = client.get("/notices/TA-2026-1003").get_data(as_text=True)
    assert "轮次结论" in round_page and "已关闭" in round_page


def test_rerun_does_not_duplicate_attribution(
    tmp_path, isolated_url, config_builder, rules
):
    entries = [("TA-2026-1005", "TA-2026-1003")]
    first, paths = crawl_isolated(
        tmp_path, isolated_url, config_builder, rules, entries
    )
    config = config_builder(isolated_url)
    from tender_anchor.attribution_config import load_attribution

    second = run_crawl(config, rules, paths, load_attribution(tmp_path / "attribution.yaml"))
    assert second.notices_unchanged == 5
    with Store(paths.db_path) as store:
        assert store.count("notices") == 5
        assert store.count("attributions") == 1
        assert store.count("round_candidates") == 2
        assert store.count("review_items") == 1
        record = store.get_attribution("TA-2026-1005")
        assert record["first_seen_run"] == first.run_id
        assert record["last_seen_run"] == second.run_id
        assert record["chosen_notice_id"] == "TA-2026-1003"


def test_result_declared_reference_is_extracted(portal_server, config_builder):
    catalog = isolated_catalog()
    catalog["notices"]["TA-2026-1005"]["result"] = {
        "original_notice_id": "TA-2026-1003"
    }
    url = portal_server(create_mock_app(catalog=catalog))
    config = config_builder(url)
    response = requests.get(url + "/notices/TA-2026-1005", timeout=5)
    extracted = extract_notice(response.text, response.url, config.selectors)
    assert extracted.kind == "result"
    assert extracted.related_notice_id == "TA-2026-1003"


def test_main_fixture_inference_and_strict_pending(crawled):
    summary, paths, _ = crawled
    assert summary.review_open == 6
    with Store(paths.db_path) as store:
        assert store.count("attributions") == 2
        assert store.count("round_candidates") == 8
        first = store.get_attribution("TA-2026-0004")
        assert first["status"] == "confirmed"
        assert first["evidence_level"] == "inferred"
        assert first["chosen_notice_id"] == "TA-2026-0002"
        assert first["strict_status"] == "pending"
        assert first["auxiliary_status"] == "closed"
        second = store.get_attribution("TA-2026-0008")
        assert second["chosen_notice_id"] == "TA-2026-0005"
        codes = {
            (item["notice_id"], item["rule_code"])
            for item in store.get_review_items(status="open")
        }
    assert ("TA-2026-0002", "TENDER_RESULT_INFERRED") in codes
    assert ("TA-2026-0005", "TENDER_RESULT_INFERRED") in codes
    assert ("TA-2026-0001", "TENDER_RESULT_MISSING") in codes
    assert ("TA-2026-0006", "TENDER_RESULT_MISSING") in codes
    payload = json.loads((paths.exports / "notices.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    rounds = {item["tender_notice_id"]: item for item in payload["rounds"]}
    assert rounds["TA-2026-0002"]["strict"]["status"] == "pending"
    assert rounds["TA-2026-0002"]["auxiliary"]["status"] == "closed"
    assert rounds["TA-2026-0002"]["auxiliary"]["result_notice_ids"] == ["TA-2026-0004"]
    assert rounds["TA-2026-0001"]["corrections"] == ["TA-2026-0003"]
    result_doc = next(
        item for item in payload["notices"] if item["notice_id"] == "TA-2026-0004"
    )
    assert result_doc["attribution"]["evidence_level"] == "inferred"
    assert result_doc["attribution"]["strict_status"] == "pending"
    client = create_review_app(paths.db_path, data_dir=paths.root).test_client()
    detail = client.get("/notices/TA-2026-0004").get_data(as_text=True)
    assert "推断归属" in detail and "非源公告声明" in detail
    assert "TA-2026-0002" in detail
    round_page = client.get("/notices/TA-2026-0002").get_data(as_text=True)
    assert "轮次结论" in round_page and "待核" in round_page
