"""Cross-bid security coverage evidence, validity, and joint allocation.

The helpers here read notices and corrections collected by the crawl, derive
each bid's effective deadline, and allocate divisible security instruments
across bids as one joint problem (never a per-bid cash-first greedy). Missing
notice or instrument evidence yields ``unverifiable`` decisions; the engine
never guesses eligibility.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from decimal import Decimal
from typing import Any

from .bid_intake import evaluate_bid
from .normalize import format_amount, stable_hash


def _cents(value: object) -> int:
    return int((Decimal(str(value)) * 100).to_integral_value())


def _fmt(cents: int) -> str:
    return format_amount(Decimal(cents) / 100) or "0.00"


def gather_evidence(store: Any, case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Collect the notice rows and corrections every bid depends on."""
    evidence: dict[str, dict[str, Any]] = {}
    for bid in case["bids"]:
        notice_id = bid["notice_id"]
        if notice_id not in evidence:
            evidence[notice_id] = {
                "notice": store.get_notice(notice_id),
                "corrections": store.get_corrections(notice_id),
            }
    return evidence


def evidence_fingerprint(evidence: dict[str, dict[str, Any]]) -> str:
    """Hash the store-side evidence so its changes force re-evaluation."""
    trimmed = {}
    for notice_id, item in sorted(evidence.items()):
        notice = item["notice"]
        trimmed[notice_id] = {
            "notice": None if notice is None else {
                key: notice[key]
                for key in ("kind", "deadline_at", "budget_amount", "source_hash")
            },
            "corrections": [
                {
                    "correction_notice_id": row["correction_notice_id"],
                    "original_notice_id": row["original_notice_id"],
                    "published_at": row["published_at"],
                    "changes": row["changes"],
                }
                for row in item["corrections"]
            ],
        }
    return stable_hash(trimmed)


def _effective_terms(
    notice: dict[str, Any] | None, corrections: list[dict[str, Any]]
) -> dict[str, Any]:
    """Deadline after applying captured corrections, plus evidence problems."""
    if notice is None:
        return {"deadline_at": None, "deadline_source": "", "problems": ["notice_missing"]}
    problems: list[str] = []
    if notice["kind"] != "tender":
        problems.append("not_tender")
    deadline = notice["deadline_at"]
    source = "notice"
    if notice["kind"] == "tender" and not deadline:
        problems.append("notice_terms_missing")
    ordered = sorted(
        corrections,
        key=lambda row: (row["published_at"] or "", row["correction_notice_id"]),
    )
    for correction in ordered:
        for change in correction["changes"]:
            if change.get("field") != "deadline_at":
                continue
            value = (change.get("normalized_value") or "").strip()
            if not value:
                problems.append("correction_deadline_unparsed")
            else:
                deadline = value
                source = f"correction:{correction['correction_notice_id']}"
    return {"deadline_at": deadline, "deadline_source": source, "problems": problems}


def _validity(instrument: dict[str, Any], deadline_at: str | None) -> str:
    if instrument["kind"] == "cash":
        return "valid"
    valid_until = instrument.get("valid_until")
    if not valid_until or not deadline_at:
        return "unknown"
    sooner = datetime.fromisoformat(valid_until)
    return "valid" if sooner >= datetime.fromisoformat(deadline_at) else "expired"


def _bid_contexts(case: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    contexts = {}
    for bid in case["bids"]:
        item = evidence[bid["notice_id"]]
        contexts[bid["bid_id"]] = {
            "envelope": evaluate_bid(item["notice"], bid),
            **_effective_terms(item["notice"], item["corrections"]),
        }
    return contexts


def _max_flow(
    needs: dict[str, int], caps: dict[str, int], usable: set[tuple[str, str]]
) -> dict[tuple[str, str], int]:
    """Edmonds-Karp over source→instrument→bid→sink with deterministic order.

    Instruments are relaxed least-flexible-first so the joint solution prefers
    instruments fewer bids can use, instead of consuming cash in bid order.
    """
    bids = sorted(needs)
    flexibility = {inst: sum((inst, bid) in usable for bid in bids) for inst in caps}
    insts = sorted(caps, key=lambda inst: (flexibility[inst], inst))
    inf = sum(needs.values()) or 1
    residual: dict[Any, dict[Any, int]] = {}

    def link(u: Any, v: Any, capacity: int) -> None:
        residual.setdefault(u, {})[v] = residual.get(u, {}).get(v, 0) + capacity
        residual.setdefault(v, {}).setdefault(u, 0)

    for inst in insts:
        link("SRC", ("i", inst), caps[inst])
    for inst, bid in sorted(usable):
        link(("i", inst), ("b", bid), inf)
    for bid in bids:
        link(("b", bid), "SNK", needs[bid])
    adjacency: dict[Any, list[Any]] = {"SRC": [("i", inst) for inst in insts]}
    for inst in insts:
        adjacency[("i", inst)] = ["SRC"] + [("b", bid) for bid in bids if (inst, bid) in usable]
    for bid in bids:
        adjacency[("b", bid)] = [("i", inst) for inst in insts if (inst, bid) in usable] + ["SNK"]
    adjacency["SNK"] = [("b", bid) for bid in bids]
    while True:
        prev: dict[Any, Any] = {"SRC": None}
        queue: deque[Any] = deque(["SRC"])
        while queue and "SNK" not in prev:
            node = queue.popleft()
            for nxt in adjacency.get(node, []):
                if nxt not in prev and residual.get(node, {}).get(nxt, 0) > 0:
                    prev[nxt] = node
                    queue.append(nxt)
        if "SNK" not in prev:
            break
        aug, node = inf, "SNK"
        while prev[node] is not None:
            aug = min(aug, residual[prev[node]][node])
            node = prev[node]
        node = "SNK"
        while prev[node] is not None:
            parent = prev[node]
            residual[parent][node] -= aug
            residual[node][parent] = residual.get(node, {}).get(parent, 0) + aug
            node = parent
    flows = {}
    for inst, bid in usable:
        used = residual.get(("b", bid), {}).get(("i", inst), 0)
        if used:
            flows[(inst, bid)] = used
    return flows


def _entry(action: str, bid_id: str, instrument_id: str, amount: str, source: str) -> dict[str, str]:
    return {
        "action": action,
        "bid_id": bid_id,
        "instrument_id": instrument_id,
        "amount": amount,
        "source": source,
    }


def _posted_entries(pledges: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        _entry("posted", p["bid_id"], p["instrument_id"], _fmt(_cents(p["amount"])), p["pledge_id"])
        for p in pledges
    ]


def _posted_state(
    case: dict[str, Any], contexts: dict[str, Any]
) -> tuple[dict[str, int], set[str], dict[str, int]]:
    """Valid posted coverage per bid, bids with unverifiable pledges, occupancy."""
    instruments = {item["instrument_id"]: item for item in case["instruments"]}
    valid_posted = {bid["bid_id"]: 0 for bid in case["bids"]}
    unknown_posted: set[str] = set()
    occupancy: dict[str, int] = {}
    for pledge in case["pledges"]:
        instrument = instruments[pledge["instrument_id"]]
        amount = _cents(pledge["amount"])
        inst_id = instrument["instrument_id"]
        occupancy[inst_id] = occupancy.get(inst_id, 0) + amount
        ctx = contexts[pledge["bid_id"]]
        if ctx["problems"]:
            continue
        state = _validity(instrument, ctx["deadline_at"])
        if state == "valid":
            valid_posted[pledge["bid_id"]] += amount
        elif state == "unknown":
            unknown_posted.add(pledge["bid_id"])
    return valid_posted, unknown_posted, occupancy


def _decision(
    bid: dict[str, Any],
    ctx: dict[str, Any],
    covered_cents: int,
    unknown_posted: set[str],
    unknown_spare: list[str],
    extra_problems: tuple[str, ...] = (),
) -> dict[str, Any]:
    required = _cents(bid["required_security"])
    problems = list(ctx["problems"]) + list(extra_problems)
    if bid["bid_id"] in unknown_posted:
        problems.append("posted_instrument_validity_unknown")
    covered_out: str | None = _fmt(covered_cents)
    shortfall_out: str | None = _fmt(max(0, required - covered_cents))
    if problems:
        status, reason = "unverifiable", problems[0]
        covered_out = shortfall_out = None
    elif required <= covered_cents:
        status, reason = "covered", ""
    elif unknown_spare:
        status, reason, shortfall_out = "unverifiable", "instrument_validity_unknown", None
    else:
        status, reason = "pending_security", "insufficient_valid_instruments"
    envelope = ctx["envelope"]
    return {
        "bid_id": bid["bid_id"],
        "notice_id": bid["notice_id"],
        "bidder_id": bid["bidder_id"],
        "envelope_status": envelope["status"],
        "envelope_reason": envelope["reason"],
        "effective_deadline_at": ctx["deadline_at"] or "",
        "deadline_source": ctx["deadline_source"],
        "required_security": _fmt(required),
        "covered_security": covered_out,
        "shortfall": shortfall_out,
        "security_status": status,
        "security_reason": reason,
        "qualified": envelope["status"] == "eligible" and status == "covered",
    }


def joint_status(decisions: list[dict[str, Any]]) -> str:
    if any(
        item["security_status"] == "unverifiable" or item["envelope_status"] == "unverifiable"
        for item in decisions
    ):
        return "unverifiable"
    if all(item["qualified"] for item in decisions):
        return "eligible"
    if any(item["security_status"] == "pending_security" for item in decisions):
        return "pending_security"
    return "ineligible"
