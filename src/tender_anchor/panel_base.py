"""Shared primitives for offline panel validation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import PanelError

SCORING_RULE = "budget_gate_mean_then_price"
REVIEWER_ROLES = ("initial", "alternate")


def parse_time(value: object, label: str = "panel event time") -> datetime:
    if not isinstance(value, str):
        raise PanelError("PANEL_CASE_INVALID", f"{label} must be text")
    try:
        instant = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PanelError("PANEL_CASE_INVALID", f"{label} is invalid") from exc
    if instant.tzinfo is None:
        raise PanelError("PANEL_CASE_INVALID", f"{label} requires timezone")
    return instant


def text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PanelError("PANEL_CASE_INVALID", f"{label} must be nonempty text")
    return value


def amount(value: object, label: str) -> str:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise PanelError("PANEL_CASE_INVALID", f"{label} is invalid") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise PanelError("PANEL_CASE_INVALID", f"{label} must be positive and finite")
    return str(value)


def objects(case: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = case.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise PanelError("PANEL_CASE_INVALID", f"panel case requires {key} objects")
    return value


def index(items: list[dict[str, Any]], key: str, label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        identity = text(item.get(key), f"{label} {key}")
        if identity in result:
            raise PanelError("PANEL_CASE_INVALID", f"{label} identity is invalid: {identity}")
        result[identity] = item
    return result
