from decimal import Decimal

import pytest

from tender_anchor.normalize import (
    canonical_json, clean_text, excerpt, format_amount, parse_amount,
    parse_deadline_at, parse_publish_at, stable_hash,
)
from tender_anchor.errors import NormalizeError


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,280,000.00元", "1280000.00"),
        ("￥860,500.00", "860500.00"),
        ("135万元", "1350000.00"),
        ("1.5亿元", "150000000.00"),
        ("1500000.00 元", "1500000.00"),
    ],
)
def test_parse_amount_forms(raw, expected):
    assert parse_amount(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["详见附件", "", "人民币壹佰万元整", "0元"])
def test_parse_amount_rejects_unusable_values(raw):
    with pytest.raises(NormalizeError) as caught:
        parse_amount(raw)
    assert caught.value.code == "NORMALIZE_AMOUNT"


def test_chinese_and_slash_dates_become_shanghai_iso():
    assert parse_deadline_at("2026年12月01日 09:30") == "2026-12-01T09:30:00+08:00"
    assert parse_deadline_at("2026/12/08 10:00") == "2026-12-08T10:00:00+08:00"
    assert parse_publish_at("2026-11-02") == "2026-11-02T00:00:00+08:00"


def test_deadline_date_only_uses_end_of_day():
    assert parse_deadline_at("2026年12月09日") == "2026-12-09T23:59:59+08:00"


def test_invalid_date_has_stable_code():
    with pytest.raises(NormalizeError) as caught:
        parse_deadline_at("下周三下午")
    assert caught.value.code == "NORMALIZE_DATE"


def test_amount_formatting_and_canonical_hash():
    assert format_amount(Decimal("812000")) == "812000.00"
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert stable_hash({"b": 1, "a": 2}) == stable_hash({"a": 2, "b": 1})


def test_text_helpers_are_deterministic():
    assert clean_text("  招标\t公告\n 正文 ") == "招标 公告 正文"
    assert excerpt("abcdef", limit=4) == "abc…"
