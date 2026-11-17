"""Loading and validation of the local explicit attribution list."""

from __future__ import annotations

from pathlib import Path

import yaml

from .errors import ConfigError
from .models import AttributionEntry, AttributionList


def load_attribution(path: str | Path) -> AttributionList:
    """Load the local explicit attribution list (strict-ledger evidence)."""
    source = Path(path).expanduser().resolve()
    try:
        raw = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            "CONFIG_NOT_FOUND", f"归属清单不存在或不可读：{source}", path=str(source)
        ) from exc
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(
            "ATTRIBUTION_CONFIG_INVALID", f"归属清单 YAML 语法错误：{source}"
        ) from exc
    if not isinstance(data, dict):
        raise ConfigError("ATTRIBUTION_CONFIG_INVALID", "归属清单根节点必须是映射")
    items = data.get("entries") or []
    if not isinstance(items, list):
        raise ConfigError("ATTRIBUTION_CONFIG_INVALID", "entries 必须是列表")
    entries: list[AttributionEntry] = []
    seen: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ConfigError("ATTRIBUTION_CONFIG_INVALID", f"第 {index} 条必须是映射")
        result_id = str(item.get("result_notice_id") or "").strip()
        tender_id = str(item.get("tender_notice_id") or "").strip()
        if not result_id or not tender_id:
            raise ConfigError(
                "ATTRIBUTION_CONFIG_INVALID",
                f"第 {index} 条缺少 result_notice_id 或 tender_notice_id",
            )
        if result_id in seen:
            raise ConfigError(
                "ATTRIBUTION_CONFIG_INVALID", f"结果公告 {result_id} 在清单中重复"
            )
        seen.add(result_id)
        entries.append(AttributionEntry(result_id, tender_id))
    return AttributionList(tuple(entries), str(source))
