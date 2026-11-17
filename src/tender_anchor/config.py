"""YAML configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from bs4 import BeautifulSoup
from soupsieve import SelectorSyntaxError

from .errors import ConfigError, FetchError, explain
from .fetch import LoopbackPolicy, is_loopback_host
from .normalize import stable_hash


@dataclass(frozen=True)
class PortalConfig:
    base_url: str
    list_path: str
    allowed_hosts: tuple[str, ...]
    timeout_seconds: float
    page_size: int
    max_pages: int
    user_agent: str
    max_response_bytes: int
    max_attachment_bytes: int


@dataclass(frozen=True)
class SelectorsConfig:
    list_item: str
    list_link: str
    list_title: str
    list_date: str
    list_kind: str
    next_page: str
    detail_title: str
    meta_row: str
    body: str
    attachments: str
    correction_diff: str


@dataclass(frozen=True)
class AppConfig:
    portal: PortalConfig
    selectors: SelectorsConfig
    source_path: str

    def fingerprint(self) -> str:
        payload: dict[str, Any] = {
            "portal": {
                "base_url": self.portal.base_url,
                "list_path": self.portal.list_path,
                "allowed_hosts": list(self.portal.allowed_hosts),
                "timeout_seconds": self.portal.timeout_seconds,
                "page_size": self.portal.page_size,
                "max_pages": self.portal.max_pages,
                "user_agent": self.portal.user_agent,
                "max_response_bytes": self.portal.max_response_bytes,
                "max_attachment_bytes": self.portal.max_attachment_bytes,
            },
            "selectors": self.selectors.__dict__,
        }
        return stable_hash(payload)


@dataclass(frozen=True)
class RuleSettings:
    required_fields: tuple[str, ...]
    min_budget: Decimal
    max_budget: Decimal
    source_path: str


SELECTOR_KEYS = (
    "list_item",
    "list_link",
    "list_title",
    "list_date",
    "list_kind",
    "next_page",
    "detail_title",
    "meta_row",
    "body",
    "attachments",
    "correction_diff",
)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(
            "CONFIG_NOT_FOUND",
            f"配置文件不存在：{path}",
            hint=explain("CONFIG_NOT_FOUND"),
            path=str(path),
        ) from exc
    except OSError as exc:
        raise ConfigError(
            "CONFIG_NOT_FOUND", f"配置文件不可读：{path}", path=str(path)
        ) from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(
            "CONFIG_INVALID", f"YAML 语法错误：{path}", detail=str(exc)[:200]
        ) from exc
    if not isinstance(data, dict):
        raise ConfigError("CONFIG_INVALID", f"配置根节点必须是映射：{path}")
    return data


def _mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ConfigError("CONFIG_INVALID", f"配置节 {key!r} 必须是映射")
    return value


def _text(data: dict[str, Any], key: str, default: str | None = None) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError("CONFIG_INVALID", f"配置项 {key!r} 必须是非空字符串")
    return value.strip()


def _int(data: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ConfigError(
            "CONFIG_VALUE_OUT_OF_RANGE",
            f"配置项 {key!r} 必须是 {low}..{high} 的整数",
            value=str(value),
        )
    return value


def _positive_float(data: dict[str, Any], key: str, default: float) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError("CONFIG_INVALID", f"配置项 {key!r} 必须是数字")
    result = float(value)
    if not 0.1 <= result <= 30.0:
        raise ConfigError(
            "CONFIG_VALUE_OUT_OF_RANGE", f"配置项 {key!r} 必须在 0.1..30 秒之间"
        )
    return result


def _validate_selector(selector: str) -> None:
    try:
        BeautifulSoup("<ul><li></li></ul>", "html.parser").select(selector)
    except (SelectorSyntaxError, ValueError, TypeError) as exc:
        raise ConfigError(
            "CONFIG_SELECTOR_INVALID",
            f"CSS 选择器无效：{selector}",
            hint=explain("CONFIG_SELECTOR_INVALID"),
            detail=str(exc)[:160],
        ) from exc


def load_config(path: str | Path) -> AppConfig:
    source = Path(path).expanduser().resolve()
    data = _read_yaml(source)
    portal_data = _mapping(data, "portal")
    selectors_data = _mapping(data, "selectors")
    limits = data.get("limits", {})
    if not isinstance(limits, dict):
        raise ConfigError("CONFIG_INVALID", "limits 必须是映射")

    base_url = _text(portal_data, "base_url")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigError(
            "CONFIG_HOST_FORBIDDEN", f"base_url 必须是 loopback http(s) 地址：{base_url}"
        )
    if not is_loopback_host(parsed.hostname):
        raise ConfigError(
            "CONFIG_HOST_FORBIDDEN",
            f"base_url 不是 loopback：{parsed.hostname}",
            hint=explain("CONFIG_HOST_FORBIDDEN"),
        )
    allowed_raw = portal_data.get("allowed_hosts", ["127.0.0.1", "localhost", "::1"])
    if not isinstance(allowed_raw, list) or not all(
        isinstance(item, str) and item.strip() for item in allowed_raw
    ):
        raise ConfigError("CONFIG_INVALID", "allowed_hosts 必须是非空字符串列表")
    allowed_hosts = tuple(sorted({item.strip().lower() for item in allowed_raw}))
    try:
        policy = LoopbackPolicy(allowed_hosts)
        policy.validate(base_url)
    except FetchError as exc:
        raise ConfigError(
            "CONFIG_HOST_FORBIDDEN",
            f"门户地址未通过 loopback 白名单校验：{exc.message}",
            hint=explain("CONFIG_HOST_FORBIDDEN"),
        ) from exc
    if parsed.hostname.lower() not in policy.allowed_hosts:
        raise ConfigError(
            "CONFIG_HOST_FORBIDDEN", "base_url host 必须出现在 allowed_hosts 中"
        )
    list_path = _text(portal_data, "list_path", "/notices")
    if not list_path.startswith("/") or list_path.startswith("//"):
        raise ConfigError("CONFIG_INVALID", "list_path 必须是以单个 / 开头的站内路径")

    selectors: dict[str, str] = {}
    for key in SELECTOR_KEYS:
        selector = _text(selectors_data, key)
        _validate_selector(selector)
        selectors[key] = selector

    portal = PortalConfig(
        base_url=base_url.rstrip("/"),
        list_path=list_path,
        allowed_hosts=allowed_hosts,
        timeout_seconds=_positive_float(portal_data, "timeout_seconds", 5.0),
        page_size=_int(portal_data, "page_size", 3, 1, 100),
        max_pages=_int(portal_data, "max_pages", 10, 1, 100),
        user_agent=_text(portal_data, "user_agent", "tender-anchor-local/1.0"),
        max_response_bytes=_int(
            limits, "max_response_bytes", 1_048_576, 1024, 10_485_760
        ),
        max_attachment_bytes=_int(
            limits, "max_attachment_bytes", 524_288, 1024, 10_485_760
        ),
    )
    return AppConfig(
        portal=portal,
        selectors=SelectorsConfig(**selectors),
        source_path=str(source),
    )


def _decimal(value: Any, key: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ConfigError("RULE_CONFIG_INVALID", f"规则项 {key!r} 必须是数字")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ConfigError("RULE_CONFIG_INVALID", f"规则项 {key!r} 不是数字") from exc
    if result < 0:
        raise ConfigError("RULE_CONFIG_INVALID", f"规则项 {key!r} 不得为负")
    return result


def load_rules(path: str | Path) -> RuleSettings:
    source = Path(path).expanduser().resolve()
    data = _read_yaml(source)
    required = data.get("required_fields")
    if not isinstance(required, list) or not required or not all(
        isinstance(item, str) and item.strip() for item in required
    ):
        raise ConfigError(
            "RULE_CONFIG_INVALID", "required_fields 必须是非空字符串列表"
        )
    minimum = _decimal(data.get("min_budget", "1000"), "min_budget")
    maximum = _decimal(data.get("max_budget", "500000000"), "max_budget")
    if minimum >= maximum:
        raise ConfigError("RULE_CONFIG_INVALID", "min_budget 必须小于 max_budget")
    return RuleSettings(
        required_fields=tuple(item.strip() for item in required),
        min_budget=minimum,
        max_budget=maximum,
        source_path=str(source),
    )
