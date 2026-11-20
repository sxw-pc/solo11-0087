"""Planning and validation for pre-authorized pledge reallocation.

The replacement is expressed as an ordered ledger plan. Every intermediate
state keeps each formally submitted bid fully covered and every instrument at
or below its face amount; a target plan that cannot satisfy those invariants is
rejected before any ledger row is written.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .bond_coverage import FIXED_PLEDGE, REALLOCATION
from .bond_store import BondStore, canonical_case_id


def validate_action(action: dict[str, Any], case: dict[str, Any]) -> None:
    if not isinstance(action, dict) or action.get("version") != 1:
        raise ValueError("bond action requires version 1")
    if not action.get("authorization"):
        raise ValueError("reallocation requires explicit supplier authorization")
    if not all(action.get(key) for key in ("action_id", "action_type", "contract_mode")):
        raise ValueError("bond action is missing identity fields")
    if action["contract_mode"] not in (FIXED_PLEDGE, REALLOCATION):
        raise ValueError("unsupported bond contract mode")
    bid_ids = {item["bid_id"] for item in case["bids"]}
    instrument_ids = {item["instrument_id"] for item in case["instruments"]}
    allocations = action.get("allocations")
    if not isinstance(allocations, list) or not allocations:
        raise ValueError("reallocation requires an allocations list")
    seen_pairs: set[tuple[str, str]] = set()
    for row in allocations:
        if not isinstance(row, dict) or not all(
            row.get(key) for key in ("bid_id", "instrument_id", "amount")
        ):
            raise ValueError("allocation row is incomplete")
        if row["bid_id"] not in bid_ids or row["instrument_id"] not in instrument_ids:
            raise ValueError("allocation references unknown bid or instrument")
        amount = Decimal(str(row["amount"]))
        if not amount.is_finite() or amount <= 0:
            raise ValueError("allocation amount must be positive")
        pair = (row["bid_id"], row["instrument_id"])
        if pair in seen_pairs:
            raise ValueError("duplicate allocation pair")
        seen_pairs.add(pair)


def plan_replacement(
    case: dict[str, Any], action: dict[str, Any], bids, views,
    bond_store: BondStore,
) -> list[dict[str, Any]]:
    """Build an ordered replacement that never uncovers a submitted bid.

    A fixed-point loop alternates feasible target pledges with releases of
    surplus legacy slices. Adds always precede the release that frees their
    room, so no intermediate ledger state uncovers a submitted bid or exceeds
    an instrument face amount; an impossible plan is rejected up front.
    """
    case_id = canonical_case_id(case)
    required = {item["bid_id"]: Decimal(str(item["required_security"]))
                for item in case["bids"]}
    target: dict[str, dict[str, Decimal]] = {}
    for row in action["allocations"]:
        target.setdefault(row["bid_id"], {})[row["instrument_id"]] = Decimal(str(row["amount"]))
    eligible_bids = {bid.bid_id for bid in bids if bid.envelope_status == "eligible"}
    for bid_id, items in target.items():
        if bid_id not in eligible_bids:
            raise ValueError("reallocation targets an ineligible bid")
        if sum(items.values(), Decimal("0")) != required[bid_id]:
            raise ValueError("reallocation must exactly cover required security")
        for instrument_id in items:
            if bid_id not in views[instrument_id].usable_for:
                raise ValueError("instrument evidence does not support the target bid")
    steps: list[dict[str, Any]] = []
    reserved = dict(bond_store.instrument_reserved(case_id))
    current = bond_store.posted_pledges(case_id)
    face = {view.instrument_id: view.face_amount for view in views.values()}
    required_by_bid = {bid.bid_id: bid.required for bid in bids}
    submitted = {bid_id for (bid_id, _instrument) in current}

    def check_state(covered: dict[str, Decimal]) -> None:
        for instrument_id, amount in reserved.items():
            if amount > face[instrument_id] or amount < 0:
                raise ValueError("reallocation violates instrument capacity")
        # Only formally submitted bids must stay fully covered at every step;
        # a new bid may legitimately hold zero cover until its pledges land.
        for bid_id in submitted:
            if covered.get(bid_id, Decimal("0")) < required_by_bid[bid_id]:
                raise ValueError("reallocation leaves a submitted bid without cover")

    covered: dict[str, Decimal] = {}
    for (bid_id, _instrument_id), amount in current.items():
        covered[bid_id] = covered.get(bid_id, Decimal("0")) + amount
    check_state(covered)
    # Only the delta over legacy pledges retained on the same pair is new.
    remaining = {
        bid_id: {
            instrument_id: max(
                amount - current.get((bid_id, instrument_id), Decimal("0")),
                Decimal("0"),
            )
            for instrument_id, amount in items.items()
        }
        for bid_id, items in target.items()
    }
    released: dict[tuple[str, str], Decimal] = {}
    progress = True
    while progress:
        progress = False
        for bid_id in sorted(remaining, reverse=True):
            for instrument_id in sorted(remaining[bid_id]):
                need = remaining[bid_id][instrument_id]
                while need > 0:
                    room = face[instrument_id] - reserved.get(instrument_id, Decimal("0"))
                    if room <= 0:
                        break
                    slice_amount = min(need, room)
                    steps.append(
                        {"event_type": "pledge", "bid_id": bid_id,
                         "instrument_id": instrument_id, "amount": slice_amount,
                         "pledge_id": None, "source_action_id": None}
                    )
                    reserved[instrument_id] = reserved.get(instrument_id, Decimal("0")) + slice_amount
                    covered[bid_id] = covered.get(bid_id, Decimal("0")) + slice_amount
                    need -= slice_amount
                    progress = True
                remaining[bid_id][instrument_id] = need
                check_state(covered)
        for (bid_id, instrument_id), legacy in sorted(current.items()):
            wanted = target.get(bid_id, {}).get(instrument_id, Decimal("0"))
            releasable = legacy - wanted - released.get((bid_id, instrument_id), Decimal("0"))
            if releasable <= 0:
                continue
            if covered.get(bid_id, Decimal("0")) - releasable < required_by_bid[bid_id]:
                continue
            steps.append(
                {"event_type": "release", "bid_id": bid_id,
                 "instrument_id": instrument_id, "amount": releasable,
                 "pledge_id": None, "source_action_id": "intake"}
            )
            reserved[instrument_id] -= releasable
            covered[bid_id] -= releasable
            released[(bid_id, instrument_id)] = (
                released.get((bid_id, instrument_id), Decimal("0")) + releasable
            )
            progress = True
            check_state(covered)
    outstanding = sum(
        amount for items in remaining.values() for amount in items.values()
    )
    if outstanding != 0:
        raise ValueError("reallocation would exceed instrument face amount")
    for (bid_id, instrument_id), legacy in current.items():
        if released.get((bid_id, instrument_id), Decimal("0")) != max(
            legacy - target.get(bid_id, {}).get(instrument_id, Decimal("0")),
            Decimal("0"),
        ):
            raise ValueError("reallocation leaves a submitted bid without cover")
    check_state(covered)
    return steps
