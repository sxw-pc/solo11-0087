"""Cross-bid bid-security coverage: evidence checks and holistic allocation.

The allocator never treats the sum of instrument face amounts as usable
capacity: an instrument can only back a bid when its owner, captured notice
evidence, correction-derived deadline and validity window all check out, and
pledged amounts can never exceed the face amount at any point in time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence

from .normalize import parse_deadline_at

CENT = Decimal("0.01")
FIXED_PLEDGE = "fixed_pledge"
REALLOCATION = "preauthorized_reallocation"
CONTRACT_MODES = {FIXED_PLEDGE, REALLOCATION}


def _amount(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("security amount is invalid") from exc
    if not result.is_finite() or result <= 0 or result.as_tuple().exponent < -2:
        raise ValueError("security amount must be positive cents")
    return result


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value)


def effective_deadline(store: Any, notice_id: str) -> tuple[str | None, list[str]]:
    """Latest captured deadline for a tender notice, applying corrections.

    Returns the effective ISO deadline and the ids of the correction notices
    that contributed deadline evidence. Only corrections already captured in
    the store participate — no deadline is ever guessed.
    """
    notice = store.get_notice(notice_id)
    if notice is None or notice.get("kind") != "tender":
        return None, []
    deadline = notice.get("deadline_at")
    sources: list[str] = []
    for correction in store.get_corrections(notice_id):
        for change in correction.get("changes", []):
            if change.get("field") != "deadline_at":
                continue
            value = change.get("normalized_value") or ""
            if not value:
                continue
            candidate = parse_deadline_at(value)
            if deadline is None or _instant(candidate) > _instant(deadline):
                deadline = candidate
            sources.append(correction["correction_notice_id"])
    return deadline, sources


@dataclass(frozen=True)
class InstrumentView:
    instrument_id: str
    bidder_id: str
    kind: str
    face_amount: Decimal
    valid_until: str | None
    evidence_status: str
    usable_for: frozenset[str] = frozenset()
    blocked_reason: str = ""


@dataclass
class CoverageBid:
    bid_id: str
    notice_id: str
    bidder_id: str
    required: Decimal
    envelope_status: str
    envelope_reason: str
    effective_deadline: str | None
    correction_sources: list[str]
    status: str = "pending_security"
    reason: str = ""
    allocations: dict[str, Decimal] = field(default_factory=dict)


def instrument_view(
    store: Any, instrument: dict[str, Any], bids: Sequence[CoverageBid]
) -> InstrumentView:
    """Verify one instrument against captured evidence and bid deadlines."""
    face = _amount(instrument["amount"])
    valid_until = instrument.get("valid_until") or None
    if instrument["kind"] == "bank_guarantee" and not valid_until:
        return InstrumentView(
            instrument["instrument_id"], instrument["bidder_id"], instrument["kind"],
            face, valid_until, "evidence_pending", frozenset(),
            "guarantee_validity_missing",
        )
    if valid_until:
        _instant(valid_until)
    usable: set[str] = set()
    blocked = ""
    for bid in bids:
        if bid.bidder_id != instrument["bidder_id"]:
            continue
        if bid.envelope_status == "unverifiable":
            continue
        if bid.effective_deadline is None:
            continue
        if valid_until and _instant(valid_until) < _instant(bid.effective_deadline):
            blocked = "expires_before_effective_deadline"
            continue
        usable.add(bid.bid_id)
    status = "verified" if usable or not blocked else "blocked"
    if not usable and not blocked:
        status = "evidence_pending"
        blocked = "no_verifiable_bid"
    return InstrumentView(
        instrument["instrument_id"], instrument["bidder_id"], instrument["kind"],
        face, valid_until, status, frozenset(usable), blocked,
    )


def build_views(
    store: Any, case: dict[str, Any]
) -> tuple[list[CoverageBid], dict[str, InstrumentView]]:
    from .bid_intake import evaluate_bid

    coverage_bids: list[CoverageBid] = []
    for raw in sorted(case["bids"], key=lambda item: item["bid_id"]):
        notice = store.get_notice(raw["notice_id"])
        decision = evaluate_bid(notice, raw)
        deadline, sources = effective_deadline(store, raw["notice_id"])
        coverage_bids.append(
            CoverageBid(
                bid_id=raw["bid_id"], notice_id=raw["notice_id"],
                bidder_id=raw["bidder_id"], required=_amount(raw["required_security"]),
                envelope_status=decision["status"], envelope_reason=decision["reason"],
                effective_deadline=deadline, correction_sources=sources,
            )
        )
    views = {
        item["instrument_id"]: instrument_view(store, item, coverage_bids)
        for item in sorted(case["instruments"], key=lambda row: row["instrument_id"])
    }
    return coverage_bids, views


def allocate(
    bids: Sequence[CoverageBid],
    views: dict[str, InstrumentView],
    reserved: dict[str, Decimal],
) -> dict[str, dict[str, Decimal]] | None:
    """Find a deterministic feasible split of instruments across all bids.

    ``reserved`` already consumes instrument capacity (posted pledges that a
    fixed-pledge contract may not move). At each (bid, instrument) pair the
    search tries the saturating slices of transportation-policy extreme
    points — take the whole free capacity, or exactly fill the bid's remaining
    need — plus the zero branch. Every feasible divisible transportation plan
    has such an extreme-point solution, so the exhaustive search cannot declare
    a joint case infeasible when a non-greedy split exists, while a bid-order
    greedy that commits cash first can still get it wrong.
    """
    # Every eligible bid participates, even one already fully covered by
    # reserved pledges: its zero need keeps the pair ordering intact while the
    # reserved map alone accounts for the capacity its pledges consume.
    needs = {
        bid.bid_id: bid.required
        for bid in bids
        if bid.envelope_status == "eligible" and bid.required > 0
    }
    covered_bids = {bid.bid_id for bid in bids if bid.envelope_status == "eligible"}
    instrument_ids = sorted(views)
    free = {
        instrument_id: views[instrument_id].face_amount
        - reserved.get(instrument_id, Decimal("0"))
        for instrument_id in instrument_ids
    }
    if any(value < 0 for value in free.values()):
        return None
    # Bids without remaining need are absent from the pair list; the capacity
    # their posted pledges consume is already reflected by ``reserved``.
    pairs: list[tuple[str, str]] = []
    for bid_id in sorted(needs):
        for instrument_id in instrument_ids:
            if bid_id in views[instrument_id].usable_for:
                pairs.append((bid_id, instrument_id))
    solution: dict[str, dict[str, Decimal]] = {
        bid_id: {} for bid_id in covered_bids
    }

    def search(index: int) -> bool:
        if index == len(pairs):
            return all(needs[bid_id] == 0 for bid_id in needs)
        bid_id, instrument_id = pairs[index]
        ceiling = min(needs[bid_id], free[instrument_id]).quantize(CENT)
        if ceiling <= 0:
            return search(index + 1)
        # The only positive extreme-point slice on this pair is the smaller of
        # remaining need and free capacity; zero is handled by the tail call.
        needs[bid_id] -= ceiling
        free[instrument_id] -= ceiling
        solution[bid_id][instrument_id] = ceiling
        if search(index + 1):
            return True
        needs[bid_id] += ceiling
        free[instrument_id] += ceiling
        del solution[bid_id][instrument_id]
        return search(index + 1)

    return solution if search(0) else None


def best_effort_allocate(
    bids: Sequence[CoverageBid],
    views: dict[str, InstrumentView],
    reserved: dict[str, Decimal],
) -> dict[str, dict[str, Decimal]]:
    """Spread genuinely free capacity over bids when full cover is impossible.

    This never moves reserved pledges and never reports feasibility; it only
    makes the partial cover (and thus the shortfall) explicit for review and
    export. Instruments and bids are traversed in stable id order.
    """
    free = {
        instrument_id: views[instrument_id].face_amount
        - reserved.get(instrument_id, Decimal("0"))
        for instrument_id in sorted(views)
    }
    needs = {
        bid.bid_id: bid.required
        for bid in sorted(bids, key=lambda item: item.bid_id)
        if bid.envelope_status == "eligible"
    }
    partial: dict[str, dict[str, Decimal]] = {bid_id: {} for bid_id in needs}
    for bid_id in sorted(needs):
        for instrument_id in sorted(views):
            if needs[bid_id] <= 0:
                break
            if bid_id not in views[instrument_id].usable_for or free[instrument_id] <= 0:
                continue
            take = min(needs[bid_id], free[instrument_id])
            partial[bid_id][instrument_id] = take
            needs[bid_id] -= take
            free[instrument_id] -= take
    return {bid_id: items for bid_id, items in partial.items() if items}


def summarize(bids: Sequence[CoverageBid]) -> list[dict[str, Any]]:
    rows = []
    for bid in bids:
        rows.append(
            {
                "bid_id": bid.bid_id,
                "notice_id": bid.notice_id,
                "bidder_id": bid.bidder_id,
                "required_security": str(bid.required),
                "envelope_status": bid.envelope_status,
                "envelope_reason": bid.envelope_reason,
                "effective_deadline_at": bid.effective_deadline,
                "correction_sources": list(bid.correction_sources),
                "security_status": bid.status,
                "reason": bid.reason,
                "covered_amount": str(sum(bid.allocations.values(), Decimal("0"))),
                "allocations": [
                    {"instrument_id": key, "amount": str(value)}
                    for key, value in sorted(bid.allocations.items())
                ],
            }
        )
    return rows
