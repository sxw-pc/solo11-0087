"""Local intake of synthetic bid security instruments and bid references."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .bid_intake import evaluate_bid
from .bond_coverage import _amount, build_views
from .store import Store


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
    """Show captured bid decisions and verified security capacity.

    The preview never pledges capacity. Face amounts are reported per
    instrument alongside evidence status and the bid ids the instrument can
    actually back, so the nominal total is never mistaken for usable security.
    """
    bids, views = build_views(store, case)
    instruments = []
    for raw in case["instruments"]:
        view = views[raw["instrument_id"]]
        instruments.append(
            {
                **raw,
                "evidence_status": view.evidence_status,
                "blocked_reason": view.blocked_reason,
                "usable_for_bids": sorted(view.usable_for),
            }
        )
    return {
        "status": "evidence_ready",
        "bids": [evaluate_bid(store.get_notice(bid["notice_id"]), bid)
                 for bid in case["bids"]],
        "instruments": instruments,
        "total_security": str(sum((_amount(item["amount"]) for item in case["instruments"]), Decimal("0"))),
        "coverage_bids": [
            {
                "bid_id": bid.bid_id,
                "effective_deadline_at": bid.effective_deadline,
                "correction_sources": bid.correction_sources,
                "envelope_status": bid.envelope_status,
            }
            for bid in bids
        ],
    }


def read_bond_action(path: str | Path) -> dict[str, Any]:
    """Validate the shape of an explicit pre-authorized reallocation action."""
    action = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(action, dict) or action.get("version") != 1:
        raise ValueError("bond action requires version 1")
    if not action.get("authorization"):
        raise ValueError("bond action requires explicit supplier authorization")
    if not all(action.get(key) for key in
               ("action_id", "action_type", "contract_mode")):
        raise ValueError("bond action is missing identity fields")
    if not isinstance(action.get("allocations"), list) or not action["allocations"]:
        raise ValueError("bond action requires an allocations list")
    pairs: set[tuple[str, str]] = set()
    for row in action["allocations"]:
        if not isinstance(row, dict) or not all(
            row.get(key) for key in ("bid_id", "instrument_id", "amount")
        ):
            raise ValueError("bond action has incomplete allocation")
        pair = (row["bid_id"], row["instrument_id"])
        if pair in pairs:
            raise ValueError("duplicate allocation pair in action")
        pairs.add(pair)
        _amount(row["amount"])
    return action
