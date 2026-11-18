"""Fleet search and shared-cost billing tests for the fictional tender visit.

All notices, roads and money here are synthetic practice data; nothing is
billed to a real party.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tender_anchor.mock_portal.fixtures import get_notice
from tender_anchor.site_visit_intake import (
    MAX_SPONSORS,
    FleetModel,
    network_fingerprint,
    settle_site_visits,
    solo_visit_quotes,
    validate_intake,
)

ROOT = Path(__file__).parents[1]
FIXTURE_PATH = ROOT / "fixtures/site_visits/intake.json"
SRC = ROOT / "src"


def load_case() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def coalition_costs(report: dict) -> dict[str, int]:
    return {c["coalition_id"]: c["cost"] for c in report["coalitions"]}


def build_model(data: dict) -> FleetModel:
    _, normalized = validate_intake(data)
    return FleetModel(normalized, network_fingerprint(normalized))


# ---------------------------------------------------------------------------
# Baseline: solo 54 vs. shared minimum 37
# ---------------------------------------------------------------------------

def test_solo_quotes_link_real_synthetic_tender():
    data = load_case()
    assert get_notice(data["notice_id"])["kind"] == "tender"
    quotes = solo_visit_quotes(data)
    assert [q["sponsor_id"] for q in quotes] == ["s-A", "s-B", "s-C"]
    assert [q["solo_cost"] for q in quotes] == [20, 18, 16]
    assert sum(q["solo_cost"] for q in quotes) == 54


def test_joint_visit_route_has_real_distance_witness():
    data = load_case()
    road = data["distance"]
    ab = road["D"]["A"] + road["A"]["B"] + road["B"]["D"]
    c = road["D"]["C"] + road["C"]["D"]
    assert data["vehicle_capacity"] == 2
    assert (ab, c, ab + c) == (21, 16, 37)


def test_shared_fleet_replaces_54_solo_total_with_37_minimum():
    report = settle_site_visits(load_case())
    assert report["solo_total_cost"] == 54
    assert report["grand_coalition"]["cost"] == 37
    assert report["status"] == "issuable"
    fleet = report["grand_coalition"]["fleet"]
    assert [(t["route"], t["cost"]) for t in fleet] == [
        (["D", "A", "B", "D"], 21),
        (["D", "C", "D"], 16),
    ]
    assert sum(t["cost"] for t in fleet) == 37


def test_each_trip_lists_order_capacity_mileage_cost_and_legs():
    report = settle_site_visits(load_case())
    for trip in report["grand_coalition"]["fleet"]:
        assert trip["route"][0] == "D"
        assert trip["route"][-1] == "D"
        assert trip["seats_used"] <= trip["capacity"] == 2
        assert len(trip["legs"]) == len(trip["route"]) - 1
        assert sum(leg["distance"] for leg in trip["legs"]) == trip["mileage"]
        assert trip["mileage"] == trip["cost"]
        # Every charged edge exists as a real directed road edge.
        for leg in trip["legs"]:
            assert (
                load_case()["distance"][leg["from"]][leg["to"]]
                == leg["distance"]
            )


# ---------------------------------------------------------------------------
# Per-coalition exact search, never derived from the grand route
# ---------------------------------------------------------------------------

def test_every_coalition_gets_its_own_optimal_witness():
    report = settle_site_visits(load_case())
    expected = {
        "empty": 0,
        "s-A": 20,
        "s-B": 18,
        "s-C": 16,
        "s-A|s-B": 21,
        "s-A|s-C": 23,
        "s-B|s-C": 23,
        "s-A|s-B|s-C": 37,
    }
    assert coalition_costs(report) == expected
    for coalition in report["coalitions"]:
        # Independent legality: depot loop, capacity, exactly-once coverage.
        visited = [
            sponsor
            for trip in coalition["fleet"]
            for sponsor in trip["sponsors"]
        ]
        assert coalition["covers_each_member_once"]
        assert sorted(visited) == sorted(coalition["members"])
        assert len(visited) == len(set(visited))
        for trip in coalition["fleet"]:
            assert trip["route"][0] == trip["route"][-1] == report["network"]["depot"]
            assert trip["seats_used"] <= trip["capacity"]
        assert sum(trip["cost"] for trip in coalition["fleet"]) == coalition["cost"]


def test_coalition_routes_are_recomputed_when_road_edge_changes():
    data = load_case()
    before = settle_site_visits(data)
    assert coalition_costs(before)["s-A|s-B"] == 21

    changed = load_case()
    # Raising the A<->B directed edges makes the shared trip more expensive;
    # every coalition fleet is re-searched, not just the appendix table.
    changed["distance"]["A"]["B"] = 6
    changed["distance"]["B"]["A"] = 6
    changed["network_version"] = "edge-v2"
    after = settle_site_visits(changed)
    assert after["network"]["digest_sha256"] != before["network"]["digest_sha256"]
    costs = coalition_costs(after)
    # D->A->B->D = 10+6+9 = 25; singletons stay untouched.
    assert costs["s-A|s-B"] == 25
    assert costs["s-A"] == 20 and costs["s-B"] == 18
    assert costs["s-A|s-B|s-C"] == 41  # was 37
    # Marginal billing moves with the recomputed coalition game.
    marginal = after["contracts"]["marginal_share"]
    assert marginal["status"] == "issuable"
    assert [(i["sponsor_id"], i["amount_cents"])
            for i in marginal["proposed_invoices"]] == [
        ("s-A", 1500),
        ("s-B", 1400),
        ("s-C", 1200),
    ]
    assert marginal["proposed_invoices"] != before["contracts"]["marginal_share"]["proposed_invoices"]


def test_directed_asymmetry_picks_direction_sensitive_optimum():
    data = load_case()
    # Reverse the A<->B edge weights: outbound A->B cheap, return B->A dear.
    data["distance"]["A"]["B"] = 1
    data["distance"]["B"]["A"] = 8
    report = settle_site_visits(data)
    ab = next(c for c in report["coalitions"] if c["coalition_id"] == "s-A|s-B")
    # D->A->B->D = 10+1+9 = 20 beats D->B->A->D = 9+8+10 = 27.
    assert ab["fleet"][0]["route"] == ["D", "A", "B", "D"]
    assert ab["cost"] == 20


def test_capacity_three_allows_one_trip_capacity_two_forces_two():
    data = load_case()
    data["vehicle_capacity"] = 3
    report = settle_site_visits(data)
    grand = report["grand_coalition"]
    assert len(grand["fleet"]) == 1
    assert grand["fleet"][0]["seats_used"] == 3
    assert grand["fleet"][0]["capacity"] == 3

    data["vehicle_capacity"] = 1
    report = settle_site_visits(data)
    assert len(report["grand_coalition"]["fleet"]) == 3
    assert all(t["seats_used"] == 1 for t in report["grand_coalition"]["fleet"])
    assert report["grand_coalition"]["cost"] == 54


def test_each_sponsor_stop_is_visited_exactly_once_under_capacity_limit():
    data = load_case()
    data["vehicle_capacity"] = 3
    model = build_model(data)
    for mask in range(1, model.full_mask + 1):
        view = model.coalition_view(mask)
        served = [
            sponsor
            for trip in view["fleet"]
            for sponsor in trip["sponsors"]
        ]
        assert sorted(served) == sorted(view["members"])
        assert len(served) == len(set(served))
        for trip in view["fleet"]:
            assert trip["seats_used"] <= 3
            assert trip["route"][0] == trip["route"][-1] == model.depot


def test_duplicate_road_segments_are_billed_in_each_trip():
    # With capacity 1, C's depot loop is driven alone even though the grand
    # fleet with capacity 2 also uses depot roads: no segment is shared once.
    data = load_case()
    data["vehicle_capacity"] = 1
    report = settle_site_visits(data)
    total_legs = sum(
        leg["distance"]
        for trip in report["grand_coalition"]["fleet"]
        for leg in trip["legs"]
    )
    assert total_legs == report["grand_coalition"]["cost"] == 54


# ---------------------------------------------------------------------------
# Contracts: equal share and Shapley marginal share
# ---------------------------------------------------------------------------

def test_equal_share_bills_12_34_12_33_12_33_with_cent_conservation():
    report = settle_site_visits(load_case())
    contract = report["contracts"]["equal_share"]
    assert contract["status"] == "issuable"
    assert contract["cent_conservation"]
    assert contract["batch_total_cents"] == contract["common_cost_cents"] == 3700
    by_sponsor = {row["sponsor_id"]: row for row in contract["invoices"]}
    assert by_sponsor["s-A"]["amount_cents"] == 1234
    assert by_sponsor["s-B"]["amount_cents"] == 1233
    assert by_sponsor["s-C"]["amount_cents"] == 1233
    assert contract["residual_cents"] == 1
    assert contract["residual_assigned_to"] == ["s-A"]


def test_marginal_share_uses_all_permutations_not_one_delete_difference():
    report = settle_site_visits(load_case())
    contract = report["contracts"]["marginal_share"]
    assert contract["status"] == "issuable"
    assert contract["join_permutation_count"] == 6
    by_sponsor = {row["sponsor_id"]: row for row in contract["invoices"]}
    assert by_sponsor["s-A"]["amount_cents"] == 1300
    assert by_sponsor["s-B"]["amount_cents"] == 1200
    assert by_sponsor["s-C"]["amount_cents"] == 1200
    assert contract["batch_total_cents"] == 3700
    # Every coalition margin (c(S+i)-c(S)) appears with its permutation weight.
    # For 3 sponsors, coalitions S excluding i have sizes 0, 1 and 2.
    for sponsor_id, rows in contract["marginal_contributions"].items():
        assert len(rows) == 4  # one entry per coalition S not containing i
        assert sum(row["join_permutations"] for row in rows) == 6
        weights = {
            len(row["coalition_without"]): row["join_permutations"]
            for row in rows
        }
        assert weights == {0: 2, 1: 1, 2: 2}
        assert {len(row["coalition_with"]) for row in rows} == {1, 2, 3}
    # The naive "delete me from the grand coalition" shortcut gives different
    # numbers (37-c(BC)=14, 37-c(AC)=14, 37-c(AB)=16) and must not be used.
    assert by_sponsor["s-A"]["amount_cents"] != (37 - 23) * 100


def test_both_contracts_respect_solo_upper_bounds_and_non_negative_bills():
    report = settle_site_visits(load_case())
    for contract in report["contracts"].values():
        assert contract["status"] == "issuable"
        assert all(row["amount_cents"] >= 0 for row in contract["invoices"])
        assert all(row["within_solo_upper"] for row in contract["invoices"])
        for row in contract["invoices"]:
            assert row["amount_cents"] <= row["solo_upper_cents"]


def test_contract_unissuable_when_equal_share_breaks_solo_upper_bound():
    # One sponsor with a very cheap solo trip: equal share would overcharge
    # it, so no invoice batch is issued and conservation is reported honestly.
    data = load_case()
    data["distance"]["C"]["D"] = 2
    data["distance"]["D"]["C"] = 2  # s-C solo upper bound is now 4
    report = settle_site_visits(data)
    contract = report["contracts"]["equal_share"]
    assert contract["status"] == "unissuable"
    assert contract["invoices"] is None
    assert any("solo visit upper bound" in reason for reason in contract["reasons"])
    # The marginal contract can still be independently issuable.
    assert report["contracts"]["marginal_share"]["status"] == "issuable"


def test_residual_cents_follow_sponsor_id_order_not_input_order():
    data = load_case()
    data["sponsors"] = list(reversed(data["sponsors"]))
    report = settle_site_visits(data)
    contract = report["contracts"]["equal_share"]
    by_sponsor = {row["sponsor_id"]: row["amount_cents"] for row in contract["invoices"]}
    assert by_sponsor == {"s-A": 1234, "s-B": 1233, "s-C": 1233}
    # Invoice presentation follows the intake order even though the residual
    # is allocated by sponsor_id order.
    assert [row["sponsor_id"] for row in contract["invoices"]] == [
        "s-C",
        "s-B",
        "s-A",
    ]
    assert contract["residual_assigned_to"] == ["s-A"]


def test_fractional_marginal_share_is_unissuable_without_silent_truncation():
    # Symmetric network: c(singletons)=20, c(pairs)=23, c(ABC)=43.  Every
    # Shapley share is 43/3 units = 4300/3 cents, never a whole-cent amount.
    data = load_case()
    data["distance"] = {
        "D": {"A": 10, "B": 10, "C": 10},
        "A": {"D": 10, "B": 3, "C": 3},
        "B": {"D": 10, "A": 3, "C": 3},
        "C": {"D": 10, "A": 3, "B": 3},
    }
    report = settle_site_visits(data)
    assert coalition_costs(report) == {
        "empty": 0,
        "s-A": 20,
        "s-B": 20,
        "s-C": 20,
        "s-A|s-B": 23,
        "s-A|s-C": 23,
        "s-B|s-C": 23,
        "s-A|s-B|s-C": 43,
    }
    contract = report["contracts"]["marginal_share"]
    assert contract["status"] == "unissuable"
    assert contract["invoices"] is None
    assert any("whole-cent" in reason for reason in contract["reasons"])
    # Exact fractional shares are disclosed; nothing is truncated away.
    assert all(
        "/" in row["exact_share_fraction"]
        for row in contract["proposed_invoices"]
    )
    # Equal share is unaffected: 4300 cents splits 1434/1433/1433 under the
    # 2000-cent solo upper bound.
    equal = report["contracts"]["equal_share"]
    assert equal["status"] == "issuable"
    assert equal["batch_total_cents"] == 4300


# ---------------------------------------------------------------------------
# Notice identity / directed network / input validity gates
# ---------------------------------------------------------------------------

def test_unknown_and_non_tender_notices_are_rejected():
    unknown = load_case()
    unknown["notice_id"] = "TA-NOPE-9999"
    with pytest.raises(ValueError, match="known tender notice"):
        settle_site_visits(unknown)

    correction = load_case()
    correction["notice_id"] = "TA-2026-0003"  # kind == "correction"
    assert get_notice("TA-2026-0003")["kind"] != "tender"
    with pytest.raises(ValueError, match="known tender notice"):
        settle_site_visits(correction)


def test_duplicate_sponsor_ids_and_stops_are_rejected():
    data = load_case()
    data["sponsors"][1]["sponsor_id"] = "s-A"
    with pytest.raises(ValueError, match="duplicated"):
        settle_site_visits(data)

    data = load_case()
    data["sponsors"][2]["stop"] = "A"
    with pytest.raises(ValueError, match="invalid or duplicated"):
        settle_site_visits(data)


def test_negative_distance_and_negative_units_are_rejected():
    data = load_case()
    data["distance"]["A"]["B"] = -1
    with pytest.raises(ValueError, match="non-negative whole number"):
        settle_site_visits(data)

    data = load_case()
    data["sponsors"][0]["units"] = -2
    with pytest.raises(ValueError, match="non-negative integer"):
        settle_site_visits(data)


def test_missing_directed_edge_is_rejected_even_when_reverse_exists():
    data = load_case()
    del data["distance"]["A"]["B"]  # B->A still present: directionality matters
    with pytest.raises(ValueError, match="directed edge missing"):
        settle_site_visits(data)


def test_uncoverable_station_is_rejected_without_zero_price():
    # The stop exists nowhere in the directed network, so no legal fleet can
    # cover it; issuance must fail rather than quote a zero-cost trip.
    data = load_case()
    data["sponsors"].append(
        {"sponsor_id": "s-Z", "stop": "Z", "units": 1}
    )
    with pytest.raises(ValueError, match="directed (edge|distance row) missing"):
        settle_site_visits(data)


def test_input_reordering_keeps_same_optimum_and_reuses_identity():
    base = load_case()
    reordered = load_case()
    reordered["sponsors"] = list(reversed(reordered["sponsors"]))
    reordered["distance"] = {
        node: dict(sorted(row.items()))
        for node, row in sorted(reordered["distance"].items())
    }
    base_report = settle_site_visits(base)
    reordered_report = settle_site_visits(reordered)
    # Same notice, stops and directed road network -> identical optimum costs.
    # Coalition ids follow intake order, so compare on sorted member sets.
    def cost_by_members(report):
        return {
            tuple(sorted(c["members"])): c["cost"]
            for c in report["coalitions"]
        }

    assert cost_by_members(base_report) == cost_by_members(reordered_report)
    # The road-network digest still binds every edge and stop; presentation
    # order follows the intake rather than the first cached model.
    assert (
        base_report["network"]["digest_sha256"]
        != reordered_report["network"]["digest_sha256"]
    )
    assert [s["sponsor_id"] for s in reordered_report["sponsors"]] == [
        "s-C",
        "s-B",
        "s-A",
    ]
    assert [s["sponsor_id"] for s in base_report["sponsors"]] == [
        "s-A",
        "s-B",
        "s-C",
    ]


def test_identical_repeat_intake_reuses_cached_fleet_model():
    data = load_case()
    _, normalized = validate_intake(data)
    digest = network_fingerprint(normalized)
    from tender_anchor.site_visit_intake import _FLEET_CACHE

    first = settle_site_visits(data)
    assert digest in _FLEET_CACHE
    cached = _FLEET_CACHE[digest]
    second = settle_site_visits(json.loads(json.dumps(data)))
    assert _FLEET_CACHE[digest] is cached
    assert coalition_costs(first) == coalition_costs(second)


def test_digest_binds_notice_stops_and_network_version():
    base = load_case()
    _, normalized = validate_intake(base)
    base_digest = network_fingerprint(normalized)

    renamed = copy.deepcopy(normalized)
    renamed["notice_id"] = "TA-2026-0005"
    assert network_fingerprint(renamed) != base_digest

    stopped = copy.deepcopy(normalized)
    stopped["sponsors"][0]["stop"] = "B"
    assert network_fingerprint(stopped) != base_digest

    versioned = copy.deepcopy(normalized)
    versioned["network_version"] = "road-v2"
    assert network_fingerprint(versioned) != base_digest


def test_report_binds_notice_and_network_summary_for_offline_recheck():
    report = settle_site_visits(load_case())
    assert report["notice"]["notice_id"] == "TA-2026-0001"
    assert report["notice"]["kind"] == "tender"
    assert report["network"]["depot"] == "D"
    assert report["network"]["vehicle_capacity"] == 2
    assert report["network"]["directed_edge_count"] == 12
    assert len(report["network"]["digest_sha256"]) == 64
    assert report["disclaimer"]


def test_six_sponsors_capacity_three_stays_exact_and_complete():
    data = load_case()
    # Six sponsors on six stops with a symmetric directed grid.
    stops = ["A", "B", "C", "E", "F", "G"]
    nodes = ["D", *stops]
    distance = {node: {} for node in nodes}
    for a in nodes:
        for b in nodes:
            if a != b:
                distance[a][b] = 5 if "D" in (a, b) else 2
    data["sponsors"] = [
        {"sponsor_id": f"s-{stop}", "stop": stop, "units": 1}
        for stop in stops
    ]
    data["distance"] = distance
    data["vehicle_capacity"] = 3
    report = settle_site_visits(data)
    assert len(report["coalitions"]) == 2 ** 6
    model = build_model(data)
    assert model.full_mask == (1 << 6) - 1
    # Two capacity-3 trips; each trip D + 3 stops + D at 5+2+2+5 = 14.
    assert report["grand_coalition"]["cost"] == 28
    assert len(report["grand_coalition"]["fleet"]) == 2
    assert MAX_SPONSORS == 6


def test_more_than_six_sponsors_is_rejected():
    data = load_case()
    data["sponsors"] = [
        {"sponsor_id": f"s-{i}", "stop": f"S{i}", "units": 1}
        for i in range(7)
    ]
    with pytest.raises(ValueError, match="at most"):
        settle_site_visits(data)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def test_cli_emits_settlement_report(tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tender_anchor.site_visit_intake",
            "--fixture",
            str(FIXTURE_PATH),
        ],
        cwd=ROOT,
        env={
            "PYTHONPATH": str(SRC),
            "PYTHONDONTWRITEBYTECODE": "1",
            "NO_PROXY": "127.0.0.1,localhost,::1",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["grand_coalition"]["cost"] == 37
    assert report["contracts"]["equal_share"]["invoices"][0]["amount"] == "12.34"


def test_cli_rejects_invalid_fixture(tmp_path):
    bad = load_case()
    bad["notice_id"] = "TA-NOPE-9999"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tender_anchor.site_visit_intake",
            "--fixture",
            str(path),
        ],
        cwd=ROOT,
        env={
            "PYTHONPATH": str(SRC),
            "PYTHONDONTWRITEBYTECODE": "1",
            "NO_PROXY": "127.0.0.1,localhost,::1",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    payload = json.loads(proc.stderr)
    assert payload["status"] == "rejected"
    assert "tender notice" in payload["reason"]
