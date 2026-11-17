import pytest
import requests

from tender_anchor.errors import ParseError
from tender_anchor.extract import extract_notice, parse_list_page
from tender_anchor.normalize import format_amount, parse_amount, parse_deadline_at, parse_publish_at


def _normalized(extracted):
    return {
        "notice_id": extracted.notice_id,
        "kind": extracted.kind,
        "title": extracted.title,
        "project_code": extracted.project_code,
        "region": extracted.region,
        "purchaser": extracted.purchaser,
        "agency": extracted.agency,
        "publish_at": parse_publish_at(extracted.publish_raw),
        "deadline_at": parse_deadline_at(extracted.deadline_raw) if extracted.deadline_raw else None,
        "budget_amount": format_amount(parse_amount(extracted.budget_raw)) if extracted.budget_raw else None,
        "award_amount": format_amount(parse_amount(extracted.award_amount_raw)) if extracted.award_amount_raw else None,
        "attachments": sorted(ref.filename for ref in extracted.attachments),
        "related_notice_id": extracted.related_notice_id,
    }


def test_list_page_extracts_three_entries(config_builder, portal_url):
    config = config_builder(portal_url)
    response = requests.get(portal_url + "/notices?page=1&page_size=3", timeout=5)
    page = parse_list_page(response.text, response.url, config.selectors)
    assert [item.notice_id for item in page.entries] == [
        "TA-2026-0001", "TA-2026-0002", "TA-2026-0003",
    ]
    assert page.entries[0].kind == "tender"
    assert page.entries[2].kind == "correction"
    assert page.next_url.endswith("page=2&page_size=3")
    assert page.errors == ()
    assert all(item.detail_url.startswith(portal_url) for item in page.entries)


def test_detail_matches_expected_fixture(config_builder, portal_url, expected_notices):
    config = config_builder(portal_url)
    response = requests.get(portal_url + "/notices/TA-2026-0001", timeout=5)
    extracted = extract_notice(response.text, response.url, config.selectors)
    actual = _normalized(extracted)
    expected = expected_notices["TA-2026-0001"]
    for key in ("kind", "title", "project_code", "region", "purchaser", "agency",
                "publish_at", "deadline_at", "budget_amount", "award_amount",
                "attachments", "related_notice_id"):
        assert actual[key] == expected[key], key
    assert extracted.sources["budget_raw"].raw_value == "1,280,000.00元"
    assert "智慧照明" in extracted.content_excerpt


def test_correction_relation_and_changed_fields(config_builder, portal_url):
    config = config_builder(portal_url)
    response = requests.get(portal_url + "/notices/TA-2026-0003", timeout=5)
    extracted = extract_notice(response.text, response.url, config.selectors)
    assert extracted.kind == "correction"
    assert extracted.related_notice_id == "TA-2026-0001"
    changes = {item.field: item.new_value for item in extracted.correction_changes}
    assert changes["budget_amount"] == "1,350,000.00元"
    assert format_amount(parse_amount(changes["budget_amount"])) == "1350000.00"


def test_result_notice_has_no_deadline_and_has_award(config_builder, portal_url):
    config = config_builder(portal_url)
    response = requests.get(portal_url + "/notices/TA-2026-0004", timeout=5)
    extracted = extract_notice(response.text, response.url, config.selectors)
    assert extracted.kind == "result"
    assert extracted.deadline_raw == ""
    assert extracted.winner == "示例禾木教学设备有限公司"
    assert extracted.award_amount_raw == "812,000.00元"


def test_parse_errors_are_explicit(config_builder, portal_url):
    config = config_builder(portal_url)
    with pytest.raises(ParseError) as caught:
        parse_list_page("<html><body>空</body></html>", portal_url, config.selectors)
    assert caught.value.code == "PARSE_LIST_EMPTY"
    with pytest.raises(ParseError) as caught:
        extract_notice("<html><body></body></html>", portal_url + "/notices/TA-X", config.selectors)
    assert caught.value.code == "PARSE_TITLE_MISSING"
