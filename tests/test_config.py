from pathlib import Path

import pytest

from tender_anchor.app_paths import ensure_within, safe_filename
from tender_anchor.config import load_config, load_rules
from tender_anchor.errors import AppError, ConfigError

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_valid_config_loads_and_hash_is_stable():
    first = load_config(FIXTURES / "config_valid.yaml")
    second = load_config(FIXTURES / "config_valid.yaml")
    assert first.portal.page_size == 3
    assert first.portal.allowed_hosts == ("127.0.0.1", "::1", "localhost")
    assert first.selectors.list_item == "ul.notice-list li.notice-item"
    assert first.fingerprint() == second.fingerprint()
    assert len(first.fingerprint()) == 64


def test_external_base_url_is_rejected_before_crawl():
    with pytest.raises(ConfigError) as caught:
        load_config(FIXTURES / "config_external.yaml")
    assert caught.value.code == "CONFIG_HOST_FORBIDDEN"


def test_bad_selector_is_rejected():
    with pytest.raises(ConfigError) as caught:
        load_config(FIXTURES / "config_bad_selector.yaml")
    assert caught.value.code == "CONFIG_SELECTOR_INVALID"


def test_missing_config_has_stable_error(tmp_path):
    with pytest.raises(ConfigError) as caught:
        load_config(tmp_path / "missing.yaml")
    assert caught.value.code == "CONFIG_NOT_FOUND"


def test_output_path_cannot_escape_data_root(tmp_path):
    with pytest.raises(AppError) as caught:
        ensure_within(tmp_path, tmp_path.parent / "outside.txt")
    assert caught.value.code == "PATH_ESCAPE"


def test_safe_filename_strips_traversal():
    assert safe_filename("../../private.txt") == "private.txt"
    assert safe_filename("a/b\\c.csv") == "c.csv"


def test_rules_load_with_documented_fields():
    settings = load_rules(Path(__file__).resolve().parents[1] / "config" / "rules.yaml")
    assert "budget_amount" in settings.required_fields
    assert settings.min_budget == 1000
    assert settings.max_budget == 500000000
