"""Canonical amount, time, and hashing helpers for the domain."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from .errors import NormalizeError, explain

try:  # Windows may not ship the IANA database; +08:00 is equivalent.
    from zoneinfo import ZoneInfo

    SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # pragma: no cover - fallback exercised only on minimal hosts
    SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

_CENT = Decimal("0.01")
_NUMBER_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>亿元|万元|亿元|亿|万|元)?")
_DATE_RE = re.compile(
    r"(?P<year>\d{4})\s*(?:年|[-/.])\s*(?P<month>\d{1,2})"
    r"\s*(?:月|[-/.])\s*(?P<day>\d{1,2})\s*日?"
)
_TIME_RE = re.compile(
    r"(?:^|[\sT])(?P<hour>\d{1,2})\s*(?::|时|：)\s*(?P<minute>\d{1,2})"
    r"(?:\s*(?::|分|：)\s*(?P<second>\d{1,2})\s*秒?)?"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean_text(value: str | None) -> str:
    """Collapse HTML whitespace without changing meaningful punctuation."""
    if value is None:
        return ""
    text = value.replace(" ", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def now_shanghai() -> datetime:
    return datetime.now(tz=SHANGHAI_TZ)


def to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ).isoformat(timespec="seconds")


def now_iso() -> str:
    return to_iso(now_shanghai())


def parse_amount(raw: str | None) -> Decimal:
    """Parse common Chinese tender amount forms into a two-decimal Decimal.

    Supported examples: ``￥1,280,000.00元``, ``135万元``, ``1.5亿元``.
    A missing number is a stable ``NORMALIZE_AMOUNT`` error.
    """
    if raw is None or not str(raw).strip():
        raise NormalizeError(
            "NORMALIZE_AMOUNT", "金额为空", hint=explain("NORMALIZE_AMOUNT")
        )
    text = str(raw).replace(",", "").replace("，", "").replace(" ", "")
    match = _NUMBER_RE.search(text)
    if not match:
        raise NormalizeError(
            "NORMALIZE_AMOUNT",
            f"金额中没有可识别数字：{clean_text(raw)[:80]}",
            hint=explain("NORMALIZE_AMOUNT"),
            raw=str(raw)[:120],
        )
    try:
        amount = Decimal(match.group("num"))
    except InvalidOperation as exc:  # pragma: no cover - regex already guards
        raise NormalizeError("NORMALIZE_AMOUNT", f"金额无效：{raw}") from exc
    unit = match.group("unit") or ""
    if unit in {"万", "万元"}:
        amount *= Decimal("10000")
    elif unit in {"亿", "亿元"}:
        amount *= Decimal("100000000")
    if amount <= 0:
        raise NormalizeError(
            "NORMALIZE_AMOUNT", "金额必须大于零", raw=str(raw)[:120]
        )
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def format_amount(amount: Decimal | str | None) -> str | None:
    if amount is None:
        return None
    return str(Decimal(str(amount)).quantize(_CENT, rounding=ROUND_HALF_UP))


def _parse_date_parts(raw: str | None) -> tuple[date, re.Match[str], str]:
    if raw is None or not str(raw).strip():
        raise NormalizeError(
            "NORMALIZE_DATE", "日期为空", hint=explain("NORMALIZE_DATE")
        )
    text = clean_text(str(raw))
    match = _DATE_RE.search(text)
    if not match:
        raise NormalizeError(
            "NORMALIZE_DATE",
            f"日期中没有可识别内容：{text[:80]}",
            hint=explain("NORMALIZE_DATE"),
            raw=text[:120],
        )
    try:
        parsed = date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError as exc:
        raise NormalizeError(
            "NORMALIZE_DATE", f"日期不存在：{text[:80]}", raw=text[:120]
        ) from exc
    return parsed, match, text[match.end() :]


def parse_datetime(
    raw: str | None,
    *,
    default_time: time = time(23, 59, 59),
    require_time: bool = False,
) -> datetime:
    """Parse supported date-time forms and attach Asia/Shanghai semantics."""
    parsed_date, _, remainder = _parse_date_parts(raw)
    match = _TIME_RE.search(" " + remainder.lstrip())
    if not match:
        if require_time:
            raise NormalizeError(
                "NORMALIZE_DATE",
                f"日期缺少时间：{clean_text(raw)[:80]}",
                raw=str(raw)[:120],
            )
        clock = default_time
    else:
        try:
            clock = time(
                int(match.group("hour")),
                int(match.group("minute")),
                int(match.group("second") or 0),
            )
        except ValueError as exc:
            raise NormalizeError(
                "NORMALIZE_DATE", f"时间不存在：{clean_text(raw)[:80]}"
            ) from exc
    return datetime.combine(parsed_date, clock, tzinfo=SHANGHAI_TZ)


def parse_publish_at(raw: str | None) -> str:
    return to_iso(parse_datetime(raw, default_time=time(0, 0, 0)))


def parse_deadline_at(raw: str | None) -> str:
    return to_iso(parse_datetime(raw, default_time=time(23, 59, 59)))


def parse_award_at(raw: str | None) -> str:
    return to_iso(parse_datetime(raw, default_time=time(0, 0, 0)))


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def stable_hash(value: Any) -> str:
    """Hash JSON-compatible values (or bytes) deterministically."""
    payload = value if isinstance(value, bytes) else canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def excerpt(text: str, limit: int = 600) -> str:
    value = clean_text(text)
    return value if len(value) <= limit else value[: limit - 1] + "…"
