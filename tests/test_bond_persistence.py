"""Cross-bid security: ledger invariants, replay, conflicts and persistence."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from tender_anchor.bond_intake import read_bond_action, read_bond_case
from tender_anchor.bond_service import (
    COVERED, INELIGIBLE, apply_reallocation, intake_bond_case,
)
from tender_anchor.bond_store import BondStore, canonical_case_id
from tender_anchor.store import Store

from bond_support import (
    NO_VALIDITY, REALLOC, SHARED, seed_extension, seed_store,
)


def test_replacement_ledger_never_uncovers_a_bid_or_exceeds_face(tmp_path):
    case = read_bond_case(SHARED)
    action = read_bond_action(REALLOC)
    store = seed_store(tmp_path / "db.sqlite3")
    seed_extension(store)
    intake = intake_bond_case(store, case)
    result = apply_reallocation(store, case, action)
    face = {"CASH-01": Decimal("20000.00"), "G-01": Decimal("30000.00"),
            "G-02": Decimal("10000.00")}
    # Replay begins from the intake pledges already on the ledger.
    reserved: dict[str, Decimal] = {}
    covered: dict[str, Decimal] = {}
    for step in intake["ledger"]:
        reserved[step["instrument_id"]] = (
            reserved.get(step["instrument_id"], Decimal("0"))
            + Decimal(step["amount"])
        )
        covered[step["bid_id"]] = (
            covered.get(step["bid_id"], Decimal("0")) + Decimal(step["amount"])
        )
    replacement_only = [
        row for row in result["ledger"] if row["action_id"] == action["action_id"]
    ]
    for step in replacement_only:
        sign = Decimal("1") if step["event_type"] == "pledge" else Decimal("-1")
        reserved[step["instrument_id"]] = (
            reserved.get(step["instrument_id"], Decimal("0"))
            + sign * Decimal(step["amount"])
        )
        covered[step["bid_id"]] = (
            covered.get(step["bid_id"], Decimal("0"))
            + sign * Decimal(step["amount"])
        )
        assert all(value <= face[key] for key, value in reserved.items())
        assert all(value >= 0 for value in reserved.values())
        # BID-001 is a formally submitted bid: it must stay fully covered.
        assert covered.get("BID-001", Decimal("0")) >= Decimal("25000.00")
    assert reserved == {"CASH-01": Decimal("20000.00"), "G-01": Decimal("25000.00"),
                        "G-02": Decimal("8000.00")}
    store.close()


def test_intake_replays_same_result_and_conflicts_on_changed_evidence(tmp_path):
    case = read_bond_case(SHARED)
    store = seed_store(tmp_path / "db.sqlite3")
    seed_extension(store)
    first = intake_bond_case(store, case)
    second = intake_bond_case(store, case)
    assert second["bids"] == first["bids"]
    assert second["ledger"] == first["ledger"]
    bond_store = BondStore(store)
    case_id = canonical_case_id(case)
    assert len(bond_store.ledger(case_id)) == 2
    changed = json.loads(SHARED.read_text(encoding="utf-8"))
    changed["bids"][1]["required_security"] = "99999.00"
    with pytest.raises(ValueError, match="different evidence"):
        intake_bond_case(store, changed)
    store.close()


def test_reallocation_is_idempotent_and_conflicts_on_same_id(tmp_path):
    case = read_bond_case(SHARED)
    action = read_bond_action(REALLOC)
    store = seed_store(tmp_path / "db.sqlite3")
    seed_extension(store)
    intake_bond_case(store, case)
    first = apply_reallocation(store, case, action)
    ledger_len = len(first["ledger"])
    second = apply_reallocation(store, case, action)
    assert second["bids"] == first["bids"]
    assert len(second["ledger"]) == ledger_len
    changed = dict(action)
    changed["allocations"] = [
        {"bid_id": "BID-001", "instrument_id": "G-01", "amount": "25000.00"},
        {"bid_id": "BID-004", "instrument_id": "CASH-01", "amount": "20000.00"},
        {"bid_id": "BID-004", "instrument_id": "G-02", "amount": "8000.01"},
    ]
    with pytest.raises(ValueError, match="different terms"):
        apply_reallocation(store, case, changed)
    store.close()


def test_persisted_decisions_survive_store_reopen(tmp_path):
    case = read_bond_case(SHARED)
    action = read_bond_action(REALLOC)
    db_path = tmp_path / "db.sqlite3"
    store = seed_store(db_path)
    seed_extension(store)
    intake_bond_case(store, case)
    apply_reallocation(store, case, action)
    store.close()
    with Store(db_path) as reopened:
        bond_store = BondStore(reopened)
        decisions = {
            row["bid_id"]: row["security_status"]
            for row in bond_store.list_bid_decisions(canonical_case_id(case))
        }
        assert decisions == {"BID-001": COVERED, "BID-004": COVERED}
        assert reopened.review_count("open") == 0
        events = reopened.connection.execute(
            "SELECT action_id, action_type FROM bond_events ORDER BY event_seq"
        ).fetchall()
        assert [row["action_type"] for row in events] == [
            "intake_fixed_pledge", "preauthorized_reallocation"
        ]


def test_rejected_envelope_bid_is_marked_ineligible(tmp_path):
    case = json.loads(SHARED.read_text(encoding="utf-8"))
    case["bids"][0]["received_at"] = "2026-12-20T09:00:00+08:00"
    store = seed_store(tmp_path / "db.sqlite3")
    seed_extension(store)
    result = intake_bond_case(store, case)
    row = next(item for item in result["bids"] if item["bid_id"] == "BID-001")
    assert row["envelope_status"] == "rejected"
    assert row["security_status"] == INELIGIBLE
    store.close()


def test_bond_exports_take_persisted_state(tmp_path):
    from tender_anchor.export import build_bond_export

    case = read_bond_case(SHARED)
    store = seed_store(tmp_path / "db.sqlite3")
    seed_extension(store)
    intake_bond_case(store, case)
    apply_reallocation(store, case, read_bond_action(REALLOC))
    payload = build_bond_export(store)
    assert payload["case_count"] == 1
    document = payload["cases"][0]
    statuses = {row["bid_id"]: row["security_status"] for row in document["bids"]}
    assert statuses == {"BID-001": COVERED, "BID-004": COVERED}
    assert any(row["event_type"] == "release" for row in document["pledge_ledger"])
    store.close()


def test_guarantee_without_validity_keeps_bid_pending_on_intake(tmp_path):
    case = read_bond_case(NO_VALIDITY)
    store = seed_store(tmp_path / "db.sqlite3")
    result = intake_bond_case(store, case)
    row = result["bids"][0]
    assert row["security_status"] == "pending_security"
    instrument = result["instruments"][0]
    assert instrument["evidence_status"] == "evidence_pending"
    assert instrument["reserved_amount"] == "0.00"
    store.close()


def test_cli_bond_workflow_runs_offline(tmp_path, monkeypatch):
    from tender_anchor.__main__ import main

    data_dir = tmp_path / "data"
    store = seed_store(data_dir / "tender_anchor.sqlite3")
    seed_extension(store)
    store.close()
    monkeypatch.chdir(tmp_path)
    rc = main([
        "bond-intake", "--data-dir", str(data_dir), "--bond-case", str(SHARED),
    ])
    assert rc == 0
    rc = main([
        "bond-reallocate", "--data-dir", str(data_dir),
        "--bond-case", str(SHARED), "--action", str(REALLOC),
    ])
    assert rc == 0
    with Store(data_dir / "tender_anchor.sqlite3") as reopened:
        bond_store = BondStore(reopened)
        decisions = {
            row["bid_id"]: row["security_status"]
            for row in bond_store.list_bid_decisions(
                canonical_case_id(read_bond_case(SHARED))
            )
        }
        assert decisions == {"BID-001": COVERED, "BID-004": COVERED}
        assert (data_dir / "exports" / "bond_coverage.csv").is_file()


def test_bond_review_sync_does_not_touch_crawl_review_items(tmp_path):
    case = read_bond_case(SHARED)
    store = seed_store(tmp_path / "db.sqlite3")
    seed_extension(store)
    store.sync_review_items(
        [{"notice_id": "TA-2026-0005", "rule_code": "MISSING_BUDGET_AMOUNT",
          "severity": "warning", "message": "缺少预算", "field_name": "budget_amount"}],
        "run-crawl",
    )
    intake_bond_case(store, case)
    crawl_item = store.get_review_items(notice_id="TA-2026-0005")[0]
    assert crawl_item["rule_code"] == "MISSING_BUDGET_AMOUNT"
    assert crawl_item["status"] == "open"
    bond_items = store.get_review_items(notice_id="TA-2026-0006")
    assert any(item["rule_code"] == "BOND_SECURITY_SHORTFALL" for item in bond_items)
    apply_reallocation(store, case, read_bond_action(REALLOC))
    assert store.get_review_items(notice_id="TA-2026-0005")[0]["status"] == "open"
    assert store.get_review_items(
        status="open", notice_id="TA-2026-0006"
    ) == []
    store.close()
