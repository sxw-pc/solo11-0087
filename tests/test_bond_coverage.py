"""Cross-bid security: evidence, deadlines, contracts and the allocator."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tender_anchor.bond_coverage import (
    FIXED_PLEDGE, REALLOCATION, allocate, build_views, effective_deadline,
)
from tender_anchor.bond_intake import preview_bond_case, read_bond_action, read_bond_case
from tender_anchor.bond_service import (
    COVERED, SHORTFALL, apply_reallocation, intake_bond_case,
)
from tender_anchor.bond_store import BondStore, canonical_case_id

from bond_support import (
    GREEDY, NO_AUTH, NO_VALIDITY, OVER_FACE, REALLOC, SHARED, MISSING_NOTICE,
    seed_extension, seed_store, status_map,
)


def test_effective_deadline_uses_latest_captured_correction(tmp_path):
    store = seed_store(tmp_path / "db.sqlite3")
    deadline, sources = effective_deadline(store, "TA-2026-0006")
    assert deadline == "2026-12-08T10:00:00+08:00"
    assert sources == []
    seed_extension(store)
    deadline, sources = effective_deadline(store, "TA-2026-0006")
    assert deadline == "2026-12-12T10:00:00+08:00"
    assert sources == ["TA-2026-0007"]
    assert effective_deadline(store, "TA-2026-9999") == (None, [])
    store.close()


def test_g01_supports_bid004_before_extension_but_not_after(tmp_path):
    case = read_bond_case(SHARED)
    store = seed_store(tmp_path / "db.sqlite3")
    bids, views = build_views(store, case)
    bid004 = next(bid for bid in bids if bid.bid_id == "BID-004")
    assert bid004.effective_deadline == "2026-12-08T10:00:00+08:00"
    assert "BID-004" in views["G-01"].usable_for
    seed_extension(store)
    bids, views = build_views(store, case)
    bid004 = next(bid for bid in bids if bid.bid_id == "BID-004")
    assert bid004.effective_deadline == "2026-12-12T10:00:00+08:00"
    assert "BID-004" not in views["G-01"].usable_for
    assert views["G-01"].blocked_reason == "expires_before_effective_deadline"
    assert "BID-004" in views["G-02"].usable_for
    store.close()


def test_guarantee_without_validity_is_evidence_pending(tmp_path):
    case = read_bond_case(NO_VALIDITY)
    store = seed_store(tmp_path / "db.sqlite3")
    _bids, views = build_views(store, case)
    assert views["G-NOVALID"].evidence_status == "evidence_pending"
    assert views["G-NOVALID"].blocked_reason == "guarantee_validity_missing"
    assert views["G-NOVALID"].usable_for == frozenset()
    store.close()


def test_missing_notice_keeps_bid_unverifiable_not_guessed(tmp_path):
    case = read_bond_case(MISSING_NOTICE)
    store = seed_store(tmp_path / "db.sqlite3")
    result = intake_bond_case(store, case)
    row = result["bids"][0]
    assert row["envelope_status"] == "unverifiable"
    assert row["security_status"] == "evidence_pending"
    assert row["reason"] == "notice_evidence_missing"
    store.close()


def test_fixed_contract_covers_bid001_and_leaves_bid004_pending(tmp_path):
    case = read_bond_case(SHARED)
    store = seed_store(tmp_path / "db.sqlite3")
    seed_extension(store)
    result = intake_bond_case(store, case)
    assert status_map(result) == {"BID-001": COVERED, "BID-004": SHORTFALL}
    bid004 = next(row for row in result["bids"] if row["bid_id"] == "BID-004")
    assert bid004["covered_amount"] == "10000.00"
    assert all(item["instrument_id"] != "G-01" for item in bid004["allocations"])
    instruments = {row["instrument_id"]: row for row in result["instruments"]}
    assert instruments["CASH-01"]["reserved_amount"] == "20000.00"
    assert instruments["CASH-01"]["free_amount"] == "0.00"
    assert instruments["G-01"]["reserved_amount"] == "5000.00"
    assert instruments["G-02"]["free_amount"] == "10000.00"
    store.close()


def test_fixed_contract_never_moves_submitted_cash_silently(tmp_path):
    case = read_bond_case(SHARED)
    store = seed_store(tmp_path / "db.sqlite3")
    seed_extension(store)
    intake_bond_case(store, case)
    ledger = BondStore(store).ledger(canonical_case_id(case))
    cash_rows = [row for row in ledger if row["instrument_id"] == "CASH-01"]
    assert len(cash_rows) == 1
    assert cash_rows[0]["event_type"] == "pledge"
    assert cash_rows[0]["bid_id"] == "BID-001"
    assert cash_rows[0]["amount"] == "20000.00"
    store.close()


def test_nominal_total_is_not_treated_as_usable_capacity(tmp_path):
    case = read_bond_case(SHARED)
    store = seed_store(tmp_path / "db.sqlite3")
    seed_extension(store)
    preview = preview_bond_case(store, case)
    g01 = next(item for item in preview["instruments"] if item["instrument_id"] == "G-01")
    assert g01["usable_for_bids"] == ["BID-001"]
    assert g01["evidence_status"] == "verified"
    result = intake_bond_case(store, case)
    total_face = sum(Decimal(row["face_amount"]) for row in result["instruments"])
    total_free = sum(Decimal(row["free_amount"]) for row in result["instruments"])
    assert total_face == Decimal("60000.00")
    assert total_free == Decimal("35000.00")
    store.close()


def test_authorized_reallocation_covers_both_bids_atomically(tmp_path):
    case = read_bond_case(SHARED)
    action = read_bond_action(REALLOC)
    store = seed_store(tmp_path / "db.sqlite3")
    seed_extension(store)
    intake_bond_case(store, case)
    result = apply_reallocation(store, case, action)
    assert status_map(result) == {"BID-001": COVERED, "BID-004": COVERED}
    allocations = {
        row["bid_id"]: {a["instrument_id"]: a["amount"] for a in row["allocations"]}
        for row in result["bids"]
    }
    assert allocations["BID-001"] == {"G-01": "25000.00"}
    assert allocations["BID-004"] == {"CASH-01": "20000.00", "G-02": "8000.00"}
    instruments = {row["instrument_id"]: row for row in result["instruments"]}
    for row in instruments.values():
        assert Decimal(row["reserved_amount"]) <= Decimal(row["face_amount"])
    assert instruments["G-01"]["reserved_amount"] == "25000.00"
    assert instruments["G-02"]["reserved_amount"] == "8000.00"
    assert instruments["CASH-01"]["reserved_amount"] == "20000.00"
    store.close()


def test_reallocation_uses_g01_for_bid004_is_rejected_after_extension(tmp_path):
    case = read_bond_case(SHARED)
    store = seed_store(tmp_path / "db.sqlite3")
    seed_extension(store)
    intake_bond_case(store, case)
    action = read_bond_action(REALLOC)
    action["action_id"] = "ACT-BAD-G1"
    action["allocations"] = [
        {"bid_id": "BID-001", "instrument_id": "G-01", "amount": "25000.00"},
        {"bid_id": "BID-004", "instrument_id": "G-01", "amount": "5000.00"},
        {"bid_id": "BID-004", "instrument_id": "CASH-01", "amount": "20000.00"},
        {"bid_id": "BID-004", "instrument_id": "G-02", "amount": "3000.00"},
    ]
    with pytest.raises(ValueError, match="does not support"):
        apply_reallocation(store, case, action)
    assert BondStore(store).get_action(canonical_case_id(case), "ACT-BAD-G1") is None
    store.close()


def test_over_face_reallocation_is_rejected(tmp_path):
    case = read_bond_case(SHARED)
    action = read_bond_action(OVER_FACE)
    store = seed_store(tmp_path / "db.sqlite3")
    seed_extension(store)
    intake_bond_case(store, case)
    with pytest.raises(ValueError, match="face amount"):
        apply_reallocation(store, case, action)
    store.close()


def test_reallocation_requires_explicit_authorization():
    with pytest.raises(ValueError, match="authorization"):
        read_bond_action(NO_AUTH)


def test_allocator_finds_split_that_bid_order_greedy_misses(tmp_path):
    case = read_bond_case(GREEDY)
    store = seed_store(tmp_path / "db.sqlite3")
    bids, views = build_views(store, case)
    solution = allocate(bids, views, {})
    assert solution is not None
    assigned = {
        bid_id: {instrument: str(amount) for instrument, amount in items.items()}
        for bid_id, items in solution.items() if items
    }
    # A bid-order cash-first greedy fills CASH-G2 for BID-201, then BID-202 has
    # no capacity left and the joint case is wrongly declared infeasible.
    assert assigned["BID-201"] == {"CASH-G1": "10000.00", "CASH-G2": "5000.00"}
    assert assigned["BID-202"] == {"CASH-G2": "5000.00"}
    store.close()


def test_allocator_returns_none_when_joint_cover_is_impossible(tmp_path):
    case = read_bond_case(SHARED)
    store = seed_store(tmp_path / "db.sqlite3")
    seed_extension(store)
    bids, views = build_views(store, case)
    blocked = {
        "CASH-01": Decimal("20000.00"), "G-01": Decimal("30000.00"),
        "G-02": Decimal("10000.00"),
    }
    assert allocate(bids, views, blocked) is None
    store.close()


def test_contract_modes_produce_different_qualification(tmp_path):
    case = read_bond_case(SHARED)
    store = seed_store(tmp_path / "db.sqlite3")
    seed_extension(store)
    fixed = intake_bond_case(store, case)
    assert status_map(fixed) == {"BID-001": COVERED, "BID-004": SHORTFALL}
    reallocated = apply_reallocation(store, case, read_bond_action(REALLOC))
    assert status_map(reallocated) == {"BID-001": COVERED, "BID-004": COVERED}
    assert fixed["contract_mode"] == FIXED_PLEDGE
    assert reallocated["contract_mode"] == REALLOCATION
    store.close()
