"""Stable, explainable error types used across 标讯锚."""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base error carrying a machine-readable reason code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str = "",
        **context: Any,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.context = {key: str(value) for key, value in context.items()}

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.hint:
            payload["hint"] = self.hint
        if self.context:
            payload["context"] = dict(sorted(self.context.items()))
        return payload

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code}] {self.message}"


class ConfigError(AppError):
    """Invalid or unsafe configuration."""


class FetchError(AppError):
    """URL policy, transport, or HTTP error."""


class ParseError(AppError):
    """HTML could not be interpreted using the configured selectors."""


class NormalizeError(AppError):
    """A raw value could not be converted to a canonical value."""


class AttachmentError(AppError):
    """Attachment name, size, or storage error."""


class StoreError(AppError):
    """SQLite persistence error."""


class ExportError(AppError):
    """Export path or serialization error."""


class PipelineError(AppError):
    """Fatal pipeline orchestration error."""


ERROR_CATALOG: dict[str, str] = {
    "CONFIG_NOT_FOUND": "配置文件不存在或不可读。",
    "CONFIG_INVALID": "配置结构不符合约定。",
    "CONFIG_HOST_FORBIDDEN": "配置中包含非 loopback 地址。",
    "CONFIG_SELECTOR_INVALID": "CSS 选择器语法无效。",
    "CONFIG_VALUE_OUT_OF_RANGE": "配置数值超出允许范围。",
    "FETCH_URL_INVALID": "目标 URL 不合法或包含凭据。",
    "FETCH_SCHEME_BLOCKED": "仅允许 http/https 且仅限 loopback。",
    "FETCH_HOST_BLOCKED": "目标 host 不在 loopback 白名单中。",
    "FETCH_REDIRECT_BLOCKED": "不允许跟随重定向。",
    "FETCH_TIMEOUT": "请求超时。",
    "FETCH_CONNECTION_ERROR": "无法连接本机 mock 门户。",
    "FETCH_TOO_LARGE": "响应体超过配置上限。",
    "FETCH_DECODE_ERROR": "响应内容无法按声明编码解码。",
    "PARSE_LIST_EMPTY": "列表页没有匹配到任何标讯条目。",
    "PARSE_LIST_ITEM": "单个列表条目缺少必要元素。",
    "PARSE_TITLE_MISSING": "详情页缺少标题。",
    "PARSE_META_EMPTY": "详情页缺少元数据表。",
    "PARSE_NOTICE_ID_MISSING": "无法识别公告编号。",
    "PARSE_ATTACHMENT_INVALID": "附件链接缺少有效文件名。",
    "NORMALIZE_AMOUNT": "金额无法解析。",
    "NORMALIZE_DATE": "日期或时间无法解析。",
    "ATTACHMENT_INVALID_NAME": "附件文件名不安全。",
    "ATTACHMENT_TOO_LARGE": "附件超过配置大小上限。",
    "ATTACHMENT_WRITE_ERROR": "附件写入本地数据目录失败。",
    "STORE_INIT": "数据库初始化失败。",
    "STORE_QUERY": "数据库查询失败。",
    "PATH_ESCAPE": "输出路径越出数据目录。",
    "EXPORT_WRITE": "导出文件写入失败。",
    "RULE_CONFIG_INVALID": "复核规则配置无效。",
    "ATTRIBUTION_CONFIG_INVALID": "本地显式归属清单配置无效。",
    "PIPELINE_NO_NOTICES": "采集流程未获得任何标讯。",
    "PIPELINE_FATAL": "采集流程发生不可恢复错误。",
}


def explain(code: str) -> str:
    """Return the documented Chinese explanation for a reason code."""
    return ERROR_CATALOG.get(code, "未登记的稳定错误码。")
