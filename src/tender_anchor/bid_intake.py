"""Offline intake of synthetic bid envelopes against captured tender facts."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .store import Store


def _instant(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("bid timestamp must have an offset")
    return result


def _money(value: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("bid amount is invalid") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("bid amount must be positive and finite")
    return amount.quantize(Decimal("0.01"))


def read_bids(path: str | Path) -> list[dict[str, str]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("bids"), list):
        raise ValueError("bid document must have version 1 and bids list")
    result = []
    ids = set()
    for row in raw["bids"]:
        if not isinstance(row, dict):
            raise ValueError("bid must be an object")
        item = {name: str(row[name]) for name in
                ("bid_id", "notice_id", "bidder_id", "received_at", "amount")}
        if not all(item.values()) or item["bid_id"] in ids:
            raise ValueError("bid fields must be nonempty and ids unique")
        ids.add(item["bid_id"])
        _instant(item["received_at"])
        _money(item["amount"])
        result.append(item)
    return sorted(result, key=lambda bid: bid["bid_id"])


def evaluate_bid(notice: dict[str, Any] | None, bid: dict[str, str]) -> dict[str, str]:
    status = "unverifiable"
    reason = "notice_missing"
    if notice is not None:
        reason = "not_tender"
        if notice["kind"] == "tender":
            reason = "notice_terms_missing"
            if notice.get("deadline_at") and notice.get("budget_amount"):
                if _instant(bid["received_at"]) > _instant(notice["deadline_at"]):
                    status, reason = "rejected", "after_notice_deadline"
                elif _money(bid["amount"]) > _money(notice["budget_amount"]):
                    status, reason = "rejected", "above_notice_budget"
                else:
                    status, reason = "eligible", "base_notice_terms"
    return {**bid, "status": status, "reason": reason,
            "notice_source_hash": notice["source_hash"] if notice else ""}


def intake_bids(store: Store, bids: list[dict[str, str]]) -> list[dict[str, str]]:
    store.connection.execute(
        "CREATE TABLE IF NOT EXISTS bid_submissions ("
        "bid_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, decision_json TEXT NOT NULL)"
    )
    output = []
    for bid in bids:
        decision = evaluate_bid(store.get_notice(bid["notice_id"]), bid)
        payload = json.dumps(bid, ensure_ascii=False, sort_keys=True)
        existing = store.connection.execute(
            "SELECT payload_json, decision_json FROM bid_submissions WHERE bid_id=?",
            (bid["bid_id"],),
        ).fetchone()
        if existing and existing["payload_json"] != payload:
            raise ValueError("bid id was used with different terms")
        if existing:
            output.append(json.loads(existing["decision_json"]))
        else:
            store.connection.execute(
                "INSERT INTO bid_submissions VALUES (?, ?, ?)",
                (bid["bid_id"], payload, json.dumps(decision, ensure_ascii=False, sort_keys=True)),
            )
            output.append(decision)
    store.connection.commit()
    return output
