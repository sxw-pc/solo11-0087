"""Local intake of synthetic bid security instruments and bid references."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .bid_intake import evaluate_bid
from .store import Store


def _amount(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("security amount is invalid") from exc
    if not result.is_finite() or result <= 0 or result.as_tuple().exponent < -2:
        raise ValueError("security amount must be positive cents")
    return result


def read_bond_case(path: str | Path) -> dict[str, Any]:
    """Validate the shape of a local case without assigning collateral."""
    case = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(case, dict) or case.get("version") != 1:
        raise ValueError("bond case requires version 1")
    if not isinstance(case.get("bids"), list) or not isinstance(case.get("instruments"), list):
        raise ValueError("bond case requires bids and instruments")
    bid_ids: set[str] = set()
    for bid in case["bids"]:
        if not isinstance(bid, dict) or not all(bid.get(key) for key in
            ("bid_id", "notice_id", "bidder_id", "received_at", "amount", "required_security")):
            raise ValueError("bond case has incomplete bid")
        if bid["bid_id"] in bid_ids:
            raise ValueError("duplicate bid id")
        bid_ids.add(bid["bid_id"])
        _amount(bid["amount"])
        _amount(bid["required_security"])
        if datetime.fromisoformat(bid["received_at"]).tzinfo is None:
            raise ValueError("bid received_at needs timezone")
    instrument_ids: set[str] = set()
    for instrument in case["instruments"]:
        if not isinstance(instrument, dict) or not all(instrument.get(key) for key in
            ("instrument_id", "bidder_id", "kind", "amount")):
            raise ValueError("bond case has incomplete instrument")
        if instrument["instrument_id"] in instrument_ids:
            raise ValueError("duplicate instrument id")
        instrument_ids.add(instrument["instrument_id"])
        _amount(instrument["amount"])
        if instrument["kind"] not in ("cash", "bank_guarantee"):
            raise ValueError("unsupported instrument kind")
        if instrument.get("valid_until"):
            if datetime.fromisoformat(instrument["valid_until"]).tzinfo is None:
                raise ValueError("valid_until needs timezone")
    if not isinstance(case.get("pledges"), list):
        raise ValueError("bond case requires pledges")
    pledge_ids: set[str] = set()
    for pledge in case["pledges"]:
        if not isinstance(pledge, dict) or not all(pledge.get(key) for key in
            ("pledge_id", "bid_id", "instrument_id", "amount", "posted_at")):
            raise ValueError("bond case has incomplete pledge")
        if pledge["pledge_id"] in pledge_ids or pledge["bid_id"] not in bid_ids or pledge["instrument_id"] not in instrument_ids:
            raise ValueError("pledge identity is invalid")
        pledge_ids.add(pledge["pledge_id"])
        _amount(pledge["amount"])
        if datetime.fromisoformat(pledge["posted_at"]).tzinfo is None:
            raise ValueError("pledge posted_at needs timezone")
    return case


def preview_bond_case(store: Store, case: dict[str, Any]) -> dict[str, Any]:
    """Show captured bid decisions and raw security capacity, without pledging it."""
    bids = [evaluate_bid(store.get_notice(bid["notice_id"]), bid) for bid in case["bids"]]
    return {
        "status": "evidence_ready",
        "bids": bids,
        "instruments": case["instruments"],
        "total_security": str(sum((_amount(item["amount"]) for item in case["instruments"]), Decimal("0"))),
    }
