"""Shared site-visit fleet settlement for a synthetic tender notice.

The offline intake behind :func:`solo_visit_quotes` only priced each sponsor
travelling alone.  This module additionally searches the minimum-cost legal
vehicle fleet that covers every sponsor visit obligation and settles the
common service cost under two pre-authorized billing contracts.

Everything here runs locally without any map service:

* every legal trip starts and ends at the depot ``D`` and accumulates cost on
  the *directed* road edges it actually drives (driving the same edge in two
  trips is billed twice);
* each sponsor stop is visited exactly once, a vehicle carries at most
  ``vehicle_capacity`` sponsors, and the exact optimum is searched by
  set-partition dynamic programming over every sponsor coalition;
* the cheapest fleet ``c(S)`` is recomputed independently for every
  sub-coalition ``S`` (including ``c(empty) = 0``); a grand-coalition route is
  never shortened and presented as a sub-coalition optimum;
* ``equal_share`` splits ``c(grand)`` in whole cents with a stable residual
  rule, while ``marginal_share`` averages each sponsor's marginal fleet cost
  across every join permutation (the Shapley value of the coalition game);
* invoices are issued only when every bill is non-negative, the batch totals
  exactly ``c(grand)`` in cents, and no bill exceeds the sponsor's solo upper
  bound.  Otherwise the contract is reported as unissuable with a reason
  instead of silently truncating an invoice.

All notices, units and routes are fictional practice data; no real party is
billed.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import sys
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

from .mock_portal.fixtures import get_notice

MAX_SPONSORS = 6
MAX_CAPACITY = 3
KNOWN_RULES = ("equal_sponsor", "coalition_marginal")

# Reuse is only valid while the notice identity, the visited stops and the
# directed road network stay identical; the fingerprint binds all three.
_FLEET_CACHE: dict[str, "FleetModel"] = {}


def _whole_distance(value: object) -> int | None:
    """Return a non-negative whole edge weight, or None when unacceptable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number < 0 or not number.is_integer():
        return None
    return int(number)


def validate_intake(data: dict) -> tuple[dict, dict]:
    """Validate a raw intake document and return (notice, normalized intake)."""
    if not isinstance(data, dict):
        raise ValueError("site visit intake must be a JSON object")

    notice_id = data.get("notice_id")
    if not isinstance(notice_id, str) or not notice_id:
        raise ValueError("notice_id is required")
    notice = get_notice(notice_id)
    if notice is None or notice.get("kind") != "tender":
        raise ValueError("site visits require a known tender notice")

    depot = data.get("depot")
    if not isinstance(depot, str) or not depot:
        raise ValueError("depot is required")

    capacity = data.get("vehicle_capacity")
    if (
        isinstance(capacity, bool)
        or not isinstance(capacity, int)
        or not 1 <= capacity <= MAX_CAPACITY
    ):
        raise ValueError("vehicle_capacity must be an integer between 1 and 3")

    raw_sponsors = data.get("sponsors")
    if not isinstance(raw_sponsors, list) or not raw_sponsors:
        raise ValueError("sponsors must be a non-empty list")
    if len(raw_sponsors) > MAX_SPONSORS:
        raise ValueError(f"at most {MAX_SPONSORS} sponsors are supported")

    sponsors: list[dict] = []
    seen_ids: set[str] = set()
    seen_stops: set[str] = set()
    for row in raw_sponsors:
        if not isinstance(row, dict):
            raise ValueError("each sponsor must be an object")
        sponsor_id = row.get("sponsor_id")
        stop = row.get("stop")
        units = row.get("units")
        if not isinstance(sponsor_id, str) or not sponsor_id or sponsor_id in seen_ids:
            raise ValueError("sponsor_id is missing or duplicated")
        if (
            not isinstance(stop, str)
            or not stop
            or stop == depot
            or stop in seen_stops
        ):
            raise ValueError("sponsor stop is invalid or duplicated")
        if isinstance(units, bool) or not isinstance(units, int) or units < 0:
            raise ValueError("sponsor units must be a non-negative integer")
        seen_ids.add(sponsor_id)
        seen_stops.add(stop)
        sponsors.append(
            {"sponsor_id": sponsor_id, "stop": stop, "units": units}
        )

    raw_distance = data.get("distance")
    if not isinstance(raw_distance, dict) or not raw_distance:
        raise ValueError("directed distance network is required")
    distance: dict[str, dict[str, int]] = {}
    for node, row in raw_distance.items():
        if not isinstance(node, str) or not isinstance(row, dict):
            raise ValueError("distance rows must be objects keyed by node")
        outgoing: dict[str, int] = {}
        for neighbour, edge in row.items():
            if not isinstance(neighbour, str):
                raise ValueError("distance edge endpoints must be text")
            weight = _whole_distance(edge)
            if weight is None:
                raise ValueError(
                    f"distance {node}->{neighbour} must be a non-negative whole number"
                )
            outgoing[neighbour] = weight
        distance[node] = outgoing

    required_nodes = {depot} | {sponsor["stop"] for sponsor in sponsors}
    for node in required_nodes:
        if node not in distance:
            raise ValueError(f"directed distance row missing for node {node}")
        for neighbour in required_nodes:
            if node != neighbour and neighbour not in distance[node]:
                raise ValueError(f"directed edge missing: {node}->{neighbour}")

    contracts = data.get("contracts")
    if not isinstance(contracts, dict) or not contracts:
        raise ValueError("at least one pre-authorized contract is required")
    for contract_id, conf in contracts.items():
        if (
            not isinstance(contract_id, str)
            or not isinstance(conf, dict)
            or conf.get("charge_rule") not in KNOWN_RULES
        ):
            raise ValueError(f"contract {contract_id} uses an unknown charge rule")

    network_version = data.get("network_version")
    if network_version is not None and not isinstance(network_version, str):
        raise ValueError("network_version must be text when provided")

    normalized = {
        "notice_id": notice_id,
        "depot": depot,
        "capacity": capacity,
        "sponsors": sponsors,
        "distance": distance,
        "contracts": contracts,
        "network_version": network_version,
    }
    return notice, normalized


def network_fingerprint(normalized: dict) -> str:
    """Hash the notice identity, stops, capacity and full directed network."""
    payload = {
        "notice_id": normalized["notice_id"],
        "depot": normalized["depot"],
        "vehicle_capacity": normalized["capacity"],
        # Sponsor order is intentionally not sorted: the cached model's
        # invoice and witness presentation follows the intake order, so a
        # reordered intake must not silently reuse another presentation.
        "sponsors": [
            (row["sponsor_id"], row["stop"], row["units"])
            for row in normalized["sponsors"]
        ],
        "distance": {
            node: dict(sorted(row.items()))
            for node, row in sorted(normalized["distance"].items())
        },
        "network_version": normalized["network_version"],
    }
    blob = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class FleetModel:
    """Exact minimum-cost legal fleet for every sponsor coalition."""

    def __init__(self, normalized: dict, digest: str):
        self.notice_id = normalized["notice_id"]
        self.depot = normalized["depot"]
        self.capacity = normalized["capacity"]
        self.sponsors = normalized["sponsors"]
        self.distance = normalized["distance"]
        self.digest = digest
        self.sponsor_ids = [row["sponsor_id"] for row in self.sponsors]
        self.stop_of = {row["sponsor_id"]: row["stop"] for row in self.sponsors}
        self.n = len(self.sponsors)
        self.full_mask = (1 << self.n) - 1
        self._build_legal_trips()
        self._solve_coalitions()
        self.grand_cost = self.coalition_cost[self.full_mask]
        if self.grand_cost <= 0:
            raise ValueError("zero-cost fleet cannot be issued as a billable service")

    # -- exact search -------------------------------------------------------

    def _route_cost(self, sponsor_sequence: tuple[str, ...]) -> int:
        nodes = [self.depot]
        nodes.extend(self.stop_of[sponsor_id] for sponsor_id in sponsor_sequence)
        nodes.append(self.depot)
        return sum(
            self.distance[a][b] for a, b in zip(nodes, nodes[1:])
        )

    def _build_legal_trips(self) -> None:
        """Cheapest legal trip (depot loop, capacity respected) per subset."""
        self.trips: dict[int, tuple[int, tuple[str, ...]]] = {}
        for size in range(1, self.capacity + 1):
            for combo in itertools.combinations(range(self.n), size):
                mask = sum(1 << index for index in combo)
                best_cost: int | None = None
                best_sequence: tuple[str, ...] | None = None
                for permutation in itertools.permutations(combo):
                    sequence = tuple(self.sponsor_ids[i] for i in permutation)
                    cost = self._route_cost(sequence)
                    if (
                        best_cost is None
                        or cost < best_cost
                        or (cost == best_cost and sequence < best_sequence)
                    ):
                        best_cost = cost
                        best_sequence = sequence
                self.trips[mask] = (best_cost, best_sequence)  # type: ignore[assignment]

    def _solve_coalitions(self) -> None:
        """Set-partition DP; each coalition is optimized independently."""
        trips = self.trips

        @lru_cache(maxsize=None)
        def best(mask: int) -> tuple[int, tuple[int, ...]]:
            if mask == 0:
                return 0, ()
            best_key: tuple[int, int, tuple[int, ...]] | None = None
            sub = mask
            while sub:
                if sub in trips:
                    tail_cost, tail_parts = best(mask ^ sub)
                    parts = tuple(sorted((sub,) + tail_parts))
                    key = (trips[sub][0] + tail_cost, len(parts), parts)
                    if best_key is None or key < best_key:
                        best_key = key
                sub = (sub - 1) & mask
            # Every non-empty coalition is coverable because a legal trip
            # exists for every singleton (capacity >= 1).
            return best_key[0], best_key[2]  # type: ignore[index]

        self.coalition_cost: dict[int, int] = {}
        self.coalition_fleet: dict[int, tuple[int, ...]] = {}
        for mask in range(self.full_mask + 1):
            cost, parts = best(mask)
            self.coalition_cost[mask] = cost
            self.coalition_fleet[mask] = parts

    # -- witnesses ----------------------------------------------------------

    def trip_view(self, trip_mask: int, trip_id: str) -> dict:
        cost, sequence = self.trips[trip_mask]
        route = [self.depot]
        route.extend(self.stop_of[sponsor_id] for sponsor_id in sequence)
        route.append(self.depot)
        legs = [
            {
                "from": a,
                "to": b,
                "distance": self.distance[a][b],
            }
            for a, b in zip(route, route[1:])
        ]
        return {
            "trip_id": trip_id,
            "sponsors": list(sequence),
            "route": route,
            "legs": legs,
            "seats_used": len(sequence),
            "capacity": self.capacity,
            "mileage": cost,
            "cost": cost,
        }

    def _members(self, mask: int) -> list[str]:
        return [
            self.sponsor_ids[i]
            for i in range(self.n)
            if mask & (1 << i)
        ]

    def coalition_view(self, mask: int) -> dict:
        fleet = [
            self.trip_view(trip_mask, f"T{index + 1}")
            for index, trip_mask in enumerate(self.coalition_fleet[mask])
        ]
        members = self._members(mask)
        return {
            "coalition_id": "|".join(members) if members else "empty",
            "members": members,
            "cost": self.coalition_cost[mask],
            "fleet": fleet,
            "covers_each_member_once": self._covers_once(mask, fleet),
        }

    def _covers_once(self, mask: int, fleet: list[dict]) -> bool:
        served: list[str] = []
        for trip in fleet:
            if trip["route"][0] != self.depot or trip["route"][-1] != self.depot:
                return False
            if trip["seats_used"] > trip["capacity"]:
                return False
            served.extend(trip["sponsors"])
        return sorted(served) == sorted(self._members(mask)) and len(served) == len(
            set(served)
        )

    def solo_cost(self, sponsor_id: str) -> int:
        index = self.sponsor_ids.index(sponsor_id)
        return self.coalition_cost[1 << index]


def _money(cents: int) -> str:
    return f"{cents / 100:.2f}"


def _invoice(sponsor_id: str, cents: int, upper_cents: int) -> dict:
    return {
        "sponsor_id": sponsor_id,
        "amount_cents": cents,
        "amount": _money(cents),
        "solo_upper_cents": upper_cents,
        "within_solo_upper": cents <= upper_cents,
    }


def _equal_share(model: FleetModel) -> dict:
    """Split c(grand) in cents; residual cents go to lowest sponsor_ids."""
    sponsor_ids = model.sponsor_ids
    grand_cents = model.grand_cost * 100
    quotient, residual = divmod(grand_cents, len(sponsor_ids))
    residual_owners = set(sorted(sponsor_ids)[:residual])
    proposed = {
        sponsor_id: quotient + (1 if sponsor_id in residual_owners else 0)
        for sponsor_id in sponsor_ids
    }
    return _finalize_contract(
        model,
        "equal_sponsor",
        proposed,
        {
            "residual_cents": residual,
            "residual_assigned_to": sorted(residual_owners),
            "allocation_rule": "quotient in cents per sponsor, residual by sponsor_id order",
        },
    )


def _marginal_share(model: FleetModel) -> dict:
    """Average marginal fleet cost across every join permutation (Shapley)."""
    n = model.n
    denominator = math.factorial(n)
    exact_shares: dict[str, Fraction] = {
        sponsor_id: Fraction(0) for sponsor_id in model.sponsor_ids
    }
    contributions: dict[str, list[dict]] = {
        sponsor_id: [] for sponsor_id in model.sponsor_ids
    }
    for index, sponsor_id in enumerate(model.sponsor_ids):
        bit = 1 << index
        for without_mask in range(model.full_mask + 1):
            if without_mask & bit:
                continue
            with_mask = without_mask | bit
            size = without_mask.bit_count()
            # Permutations where exactly this coalition precedes the sponsor.
            permutations = math.factorial(size) * math.factorial(n - 1 - size)
            marginal = (
                model.coalition_cost[with_mask]
                - model.coalition_cost[without_mask]
            )
            exact_shares[sponsor_id] += Fraction(
                permutations * marginal, denominator
            )
            contributions[sponsor_id].append(
                {
                    "coalition_without": model._members(without_mask),
                    "coalition_with": model._members(with_mask),
                    "marginal_cost": marginal,
                    "join_permutations": permutations,
                    "permutation_weight_fraction": f"{permutations}/{denominator}",
                }
            )

    proposed_cents: dict[str, int] = {}
    fractional: list[str] = []
    for sponsor_id, share in exact_shares.items():
        scaled = share * 100
        if scaled.denominator != 1:
            fractional.append(sponsor_id)
        else:
            proposed_cents[sponsor_id] = scaled.numerator

    report = {
        "charge_rule": "coalition_marginal",
        "join_permutation_count": denominator,
        "marginal_contributions": contributions,
    }
    if fractional:
        report.update(
            {
                "status": "unissuable",
                "reasons": [
                    "marginal shares are not whole-cent amounts: "
                    + ",".join(sorted(fractional))
                ],
                "common_cost_cents": model.grand_cost * 100,
                "batch_total_cents": None,
                "cent_conservation": False,
                "proposed_invoices": [
                    {
                        "sponsor_id": sponsor_id,
                        "exact_share_fraction": str(
                            exact_shares[sponsor_id]
                        ),
                    }
                    for sponsor_id in model.sponsor_ids
                ],
                "invoices": None,
            }
        )
        return report

    report.update(
        _finalize_contract(model, "coalition_marginal", proposed_cents, {})
    )
    return report


def _finalize_contract(
    model: FleetModel,
    charge_rule: str,
    proposed_cents: dict[str, int],
    extra: dict,
) -> dict:
    grand_cents = model.grand_cost * 100
    upper = {
        sponsor_id: model.solo_cost(sponsor_id) * 100
        for sponsor_id in model.sponsor_ids
    }
    reasons: list[str] = []
    negative = [
        sponsor_id
        for sponsor_id, cents in proposed_cents.items()
        if cents < 0
    ]
    if negative:
        reasons.append("negative invoice for " + ",".join(sorted(negative)))
    violations = [
        sponsor_id
        for sponsor_id, cents in proposed_cents.items()
        if cents > upper[sponsor_id]
    ]
    if violations:
        reasons.append(
            "invoice exceeds solo visit upper bound for "
            + ",".join(sorted(violations))
        )
    total = sum(proposed_cents.values())
    conservation = total == grand_cents
    if not conservation:
        reasons.append(
            f"batch total {total} cents does not equal common cost {grand_cents}"
        )

    proposed_invoices = [
        _invoice(sponsor_id, proposed_cents[sponsor_id], upper[sponsor_id])
        for sponsor_id in model.sponsor_ids
    ]
    issuable = not reasons
    report = {
        "charge_rule": charge_rule,
        "status": "issuable" if issuable else "unissuable",
        "reasons": reasons,
        "common_cost_cents": grand_cents,
        "batch_total_cents": total,
        "cent_conservation": conservation,
        "proposed_invoices": proposed_invoices,
        "invoices": proposed_invoices if issuable else None,
    }
    report.update(extra)
    return report


def settle_site_visits(data: dict) -> dict:
    """Build the full fleet-and-billing settlement report for one intake."""
    notice, normalized = validate_intake(data)
    digest = network_fingerprint(normalized)
    model = _FLEET_CACHE.get(digest)
    if model is None:
        model = FleetModel(normalized, digest)
        _FLEET_CACHE[digest] = model

    network_nodes = sorted(set(normalized["distance"]))
    directed_edge_count = sum(
        1
        for node in network_nodes
        for neighbour in normalized["distance"][node]
        if neighbour != node
    )
    grand_view = model.coalition_view(model.full_mask)
    contracts: dict[str, dict] = {}
    for contract_id, conf in normalized["contracts"].items():
        if conf["charge_rule"] == "equal_sponsor":
            contracts[contract_id] = _equal_share(model)
        else:
            contracts[contract_id] = _marginal_share(model)

    return {
        "status": "issuable"
        if all(conf["status"] == "issuable" for conf in contracts.values())
        else "unissuable",
        "notice": {
            "notice_id": notice["notice_id"],
            "kind": notice["kind"],
            "title": notice.get("title"),
        },
        "network": {
            "depot": model.depot,
            "vehicle_capacity": model.capacity,
            "nodes": network_nodes,
            "directed_edge_count": directed_edge_count,
            "network_version": normalized["network_version"],
            "digest_sha256": digest,
        },
        "sponsors": [
            {
                "sponsor_id": row["sponsor_id"],
                "stop": row["stop"],
                "units": row["units"],
                "solo_cost": model.solo_cost(row["sponsor_id"]),
                "solo_cost_cents": model.solo_cost(row["sponsor_id"]) * 100,
            }
            for row in normalized["sponsors"]
        ],
        "solo_total_cost": sum(
            model.solo_cost(sponsor_id) for sponsor_id in model.sponsor_ids
        ),
        "grand_coalition": grand_view,
        "coalitions": [
            model.coalition_view(mask)
            for mask in range(model.full_mask + 1)
        ],
        "contracts": contracts,
        "disclaimer": (
            "fictional synthetic tender exercise; no real party is billed"
        ),
    }


def solo_visit_quotes(data: dict) -> list[dict]:
    """Backward-compatible solo travel quotes, still gated on a real tender."""
    _, normalized = validate_intake(data)
    digest = network_fingerprint(normalized)
    model = _FLEET_CACHE.get(digest)
    if model is None:
        model = FleetModel(normalized, digest)
        _FLEET_CACHE[digest] = model
    return [
        {
            "sponsor_id": row["sponsor_id"],
            "solo_cost": model.solo_cost(row["sponsor_id"]),
        }
        for row in normalized["sponsors"]
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Settle the fictional shared site-visit fleet"
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("fixtures/site_visits/intake.json"),
    )
    args = parser.parse_args(argv)
    data = json.loads(args.fixture.read_text(encoding="utf-8"))
    try:
        report = settle_site_visits(data)
    except ValueError as exc:
        print(
            json.dumps(
                {"status": "rejected", "reason": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
