import json
from pathlib import Path

import pytest

from tender_anchor.bond_intake import intake_bond_case, read_bond_case
from tender_anchor.export import export_all
from tender_anchor.models import CorrectionRecord, NoticeRecord
from tender_anchor.store import Store

FIXTURES = Path(__file__).parents[1] / "fixtures" / "bonds"


def _intake(store, name):
    return intake_bond_case(store, read_bond_case(FIXTURES / name))


def _contract(result, contract_id):
    return next(item for item in result["contracts"] if item["contract_id"] == contract_id)


def _bid(contract, bid_id):
    return next(item for item in contract["bids"] if item["bid_id"] == bid_id)


def _occupancy(store, case_id, contract_id):
    """Net occupation per instrument from the latest persisted ledger."""
    totals = {}
    for row in store.get_bond_ledger(case_id, contract_id):
        if row["action"] in ("posted", "allocate", "replace"):
            totals[row["instrument_id"]] = (
                totals.get(row["instrument_id"], 0) + int(row["amount"].replace(".", ""))
            )
    return totals


def _make_notice(notice_id, deadline, budget):
    return NoticeRecord(
        notice_id=notice_id,
        kind="tender",
        kind_label="招标公告",
        title="示例招标公告",
        project_code="YC-TEST-9001",
        region="示例市测试片区",
        purchaser="示例采购单位",
        agency="示例代理机构",
        publish_at="2026-11-01T00:00:00+08:00",
        deadline_at=deadline,
        budget_amount=budget,
        budget_currency="CNY",
        award_amount=None,
        winner="",
        related_notice_id="",
        content_excerpt="示例内容",
        source_url=f"http://127.0.0.1:5810/notices/{notice_id}",
        source_hash=f"hash-{notice_id}",
    )


def _seed_notices(store):
    with store.transaction():
        store.upsert_notice(
            _make_notice("TA-2026-0001", "2026-12-01T09:30:00+08:00", "1280000.00"),
            "run-1", {}, [],
        )
        store.upsert_notice(
            _make_notice("TA-2026-0006", "2026-12-08T10:00:00+08:00", "1500000.00"),
            "run-1", {}, [],
        )


def _extension_correction():
    return CorrectionRecord(
        "TA-2026-0007", "TA-2026-0006", "2026-11-12T00:00:00+08:00",
        ({
            "field": "deadline_at",
            "label": "截止时间",
            "raw_value": "2026年12月12日 10:00",
            "normalized_value": "2026-12-12T10:00:00+08:00",
        },),
        "http://127.0.0.1:5810/notices/TA-2026-0007",
    )


def test_fixed_contract_freezes_posted_and_reports_shortfall(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        result = _intake(store, "shared_security.json")
        assert result["status"] == "evaluated"
        fixed = _contract(result, "FIXED-2026-11")
        assert fixed["joint_status"] == "pending_security"
        first = _bid(fixed, "BID-001")
        assert first["security_status"] == "covered"
        assert first["covered_security"] == "25000.00"
        assert first["qualified"] is True
        assert first["effective_deadline_at"] == "2026-12-05T09:30:00+08:00"
        assert first["deadline_source"] == "correction:TA-2026-0003"
        second = _bid(fixed, "BID-004")
        assert second["security_status"] == "pending_security"
        assert second["security_reason"] == "insufficient_valid_instruments"
        assert second["covered_security"] == "10000.00"
        assert second["shortfall"] == "18000.00"
        assert second["qualified"] is False
        assert second["effective_deadline_at"] == "2026-12-12T10:00:00+08:00"
        assert second["deadline_source"] == "correction:TA-2026-0007"
        # The fixed contract may not silently move BID-001's posted cash, and
        # G-01 (expiring 12-10) cannot serve the extended 12-12 deadline.
        assert fixed["allocations"] == [
            {"action": "posted", "bid_id": "BID-001", "instrument_id": "CASH-01",
             "amount": "20000.00", "source": "P-BID001-CASH"},
            {"action": "posted", "bid_id": "BID-001", "instrument_id": "G-01",
             "amount": "5000.00", "source": "P-BID001-G1"},
            {"action": "allocate", "bid_id": "BID-004", "instrument_id": "G-02",
             "amount": "10000.00", "source": "FIXED-2026-11"},
        ]
        assert _occupancy(store, "CASE-SHARED-2026", "FIXED-2026-11") == {
            "CASH-01": 2000000, "G-01": 500000, "G-02": 1000000,
        }
        open_items = store.get_review_items(status="open", notice_id="TA-2026-0006")
        assert [item["rule_code"] for item in open_items] == [
            "BOND_SECURITY_SHORTFALL:CASE-SHARED-2026:FIXED-2026-11"
        ]
        assert "18000.00" in open_items[0]["message"]


def test_redistribution_contract_replaces_atomically(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        result = _intake(store, "shared_security.json")
        realloc = _contract(result, "REALLOC-2026-11")
        assert realloc["joint_status"] == "eligible"
        assert all(bid["qualified"] for bid in realloc["bids"])
        assert realloc["allocations"] == [
            {"action": "release", "bid_id": "BID-001", "instrument_id": "CASH-01",
             "amount": "20000.00", "source": "P-BID001-CASH"},
            {"action": "release", "bid_id": "BID-001", "instrument_id": "G-01",
             "amount": "5000.00", "source": "P-BID001-G1"},
            {"action": "replace", "bid_id": "BID-001", "instrument_id": "G-01",
             "amount": "25000.00", "source": "REALLOC-2026-11"},
            {"action": "replace", "bid_id": "BID-004", "instrument_id": "CASH-01",
             "amount": "20000.00", "source": "REALLOC-2026-11"},
            {"action": "replace", "bid_id": "BID-004", "instrument_id": "G-02",
             "amount": "8000.00", "source": "REALLOC-2026-11"},
        ]
        # No instrument is ever occupied beyond its face value.
        assert _occupancy(store, "CASE-SHARED-2026", "REALLOC-2026-11") == {
            "G-01": 2500000, "CASH-01": 2000000, "G-02": 800000,
        }
        # Every release traces back to a pledge posted in the case file.
        case = read_bond_case(FIXTURES / "shared_security.json")
        pledges = {row["pledge_id"]: row for row in case["pledges"]}
        for entry in realloc["allocations"]:
            if entry["action"] == "release":
                assert pledges[entry["source"]]["amount"] == entry["amount"]
        # The replacement leaves no shortfall review item for this contract.
        open_items = store.get_review_items(status="open", notice_id="TA-2026-0006")
        assert all("REALLOC-2026-11" not in item["rule_code"] for item in open_items)


def test_extension_evidence_flips_g01_availability(tmp_path):
    store = Store(tmp_path / "data" / "test.sqlite3")
    _seed_notices(store)
    first = _intake(store, "shared_security.json")
    fixed = _contract(first, "FIXED-2026-11")
    # Before the correction is captured, G-01 (valid until 12-10) covers the
    # original 12-08 deadline and the joint plan is eligible.
    assert fixed["joint_status"] == "eligible"
    assert _bid(fixed, "BID-004")["security_status"] == "covered"
    assert _bid(fixed, "BID-004")["deadline_source"] == "notice"
    assert [
        (row["instrument_id"], row["amount"]) for row in fixed["allocations"]
        if row["action"] == "allocate"
    ] == [("G-01", "25000.00"), ("G-02", "3000.00")]
    replayed = _intake(store, "shared_security.json")
    assert replayed["status"] == "replayed"
    assert replayed["contracts"] == first["contracts"]
    assert store.latest_bond_ledger_version("CASE-SHARED-2026", "FIXED-2026-11") == 1
    # Capturing correction TA-2026-0007 extends the deadline past G-01's validity.
    with store.transaction():
        store.upsert_correction(_extension_correction(), "run-2")
    extended = _intake(store, "shared_security.json")
    assert extended["status"] == "evaluated"
    fixed_after = _contract(extended, "FIXED-2026-11")
    assert fixed_after["joint_status"] == "pending_security"
    bid4 = _bid(fixed_after, "BID-004")
    assert bid4["shortfall"] == "18000.00"
    assert bid4["deadline_source"] == "correction:TA-2026-0007"
    assert [
        (row["instrument_id"], row["amount"]) for row in fixed_after["allocations"]
        if row["action"] == "allocate"
    ] == [("G-02", "10000.00")]
    # The pre-extension ledger stays on record as version 1.
    assert store.latest_bond_ledger_version("CASE-SHARED-2026", "FIXED-2026-11") == 2
    old = store.get_bond_ledger("CASE-SHARED-2026", "FIXED-2026-11", version=1)
    assert [row["instrument_id"] for row in old if row["action"] == "allocate"] == ["G-01", "G-02"]
    open_items = store.get_review_items(status="open", notice_id="TA-2026-0006")
    assert any(item["rule_code"].startswith("BOND_SECURITY_SHORTFALL") for item in open_items)
    # Removing the correction restores the original terms and resolves the item.
    with store.transaction():
        store.connection.execute(
            "DELETE FROM corrections WHERE correction_notice_id='TA-2026-0007'"
        )
    restored = _intake(store, "shared_security.json")
    assert _contract(restored, "FIXED-2026-11")["joint_status"] == "eligible"
    resolved = store.get_review_items(status="resolved", notice_id="TA-2026-0006")
    assert any(item["rule_code"].startswith("BOND_SECURITY_SHORTFALL") for item in resolved)
    store.close()


def test_replay_and_conflict(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        first = _intake(store, "shared_security.json")
        second = _intake(store, "shared_security.json")
        assert first["status"] == "evaluated"
        assert second["status"] == "replayed"
        assert second["contracts"] == first["contracts"]
        assert second["evidence_hash"] == first["evidence_hash"]
        assert store.count("bond_allocations") == 8
        case = json.loads((FIXTURES / "shared_security.json").read_text(encoding="utf-8"))
        changed = {**case, "bids": [dict(row) for row in case["bids"]]}
        changed["bids"][1]["required_security"] = "29000.00"
        with pytest.raises(ValueError, match="different content"):
            intake_bond_case(store, changed)
        other = json.loads((FIXTURES / "shared_security.json").read_text(encoding="utf-8"))
        other["case_id"] = "CASE-OTHER-2026"
        other["contracts"][1]["moves"][0]["amount"] = "24000.00"
        with pytest.raises(ValueError, match="different terms"):
            intake_bond_case(store, other)
        assert store.get_bond_case("CASE-OTHER-2026") is None


def test_overdraw_rejected_atomically(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        with pytest.raises(ValueError, match="face value"):
            _intake(store, "realloc_overdraw.json")
        assert store.get_bond_case("CASE-OVERDRAW-2026") is None
        assert store.count("bond_decisions") == 0
        assert store.count("bond_allocations") == 0


def test_uncovered_submitted_bid_rejected(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        with pytest.raises(ValueError, match="uncovered"):
            _intake(store, "realloc_uncovered.json")
        assert store.get_bond_case("CASE-UNCOVERED-2026") is None
        assert store.count("bond_allocations") == 0


def test_expired_instrument_move_rejected(crawled, tmp_path):
    _, paths, _ = crawled
    case = json.loads((FIXTURES / "shared_security.json").read_text(encoding="utf-8"))
    case["case_id"] = "CASE-EXPIRED-2026"
    case["contracts"] = [{
        "contract_id": "REALLOC-EXP-01",
        "kind": "redistribution",
        "authorized_by": "supplier-a",
        "moves": [
            {"bid_id": "BID-001", "instrument_id": "CASH-01", "amount": "20000.00"},
            {"bid_id": "BID-001", "instrument_id": "G-01", "amount": "5000.00"},
            {"bid_id": "BID-004", "instrument_id": "G-01", "amount": "25000.00"},
            {"bid_id": "BID-004", "instrument_id": "G-02", "amount": "3000.00"},
        ],
    }]
    path = tmp_path / "expired.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    with Store(paths.db_path) as store:
        with pytest.raises(ValueError, match="not valid"):
            intake_bond_case(store, read_bond_case(path))
        assert store.get_bond_case("CASE-EXPIRED-2026") is None


def test_missing_evidence_is_pending_not_guessed(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        result = _intake(store, "missing_evidence.json")
        fixed = _contract(result, "FIXED-GAP-01")
        assert fixed["joint_status"] == "unverifiable"
        unknown_notice = _bid(fixed, "BID-007")
        assert unknown_notice["security_status"] == "unverifiable"
        assert unknown_notice["security_reason"] == "notice_missing"
        assert unknown_notice["covered_security"] is None
        assert unknown_notice["shortfall"] is None
        assert unknown_notice["qualified"] is False
        unknown_guarantee = _bid(fixed, "BID-008")
        assert unknown_guarantee["security_status"] == "unverifiable"
        assert unknown_guarantee["security_reason"] == "posted_instrument_validity_unknown"
        # No allocation is planned on unverifiable evidence.
        assert fixed["allocations"] == [
            {"action": "posted", "bid_id": "BID-008", "instrument_id": "G-09",
             "amount": "9000.00", "source": "P-BID008-G9"},
        ]
        open_items = store.get_review_items(status="open")
        bond_items = [item for item in open_items if item["rule_code"].startswith("BOND_SECURITY_UNVERIFIABLE")]
        assert {item["notice_id"] for item in bond_items} == {"TA-2026-9999", "TA-2026-0001"}


def test_free_case_allocates_jointly_not_greedily(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        result = _intake(store, "cross_bid_free.json")
        fixed = _contract(result, "FIXED-FREE-01")
        assert fixed["joint_status"] == "eligible"
        # A cash-first per-bid greedy would strand BID-004; the joint plan
        # instead reserves G-01 for the only bid it can still serve.
        assert fixed["allocations"] == [
            {"action": "allocate", "bid_id": "BID-004", "instrument_id": "CASH-01",
             "amount": "20000.00", "source": "FIXED-FREE-01"},
            {"action": "allocate", "bid_id": "BID-001", "instrument_id": "G-01",
             "amount": "25000.00", "source": "FIXED-FREE-01"},
            {"action": "allocate", "bid_id": "BID-004", "instrument_id": "G-02",
             "amount": "8000.00", "source": "FIXED-FREE-01"},
        ]
        assert _occupancy(store, "CASE-FREE-2026", "FIXED-FREE-01") == {
            "CASH-01": 2000000, "G-01": 2500000, "G-02": 800000,
        }


def test_export_reflects_pledge_changes(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        _intake(store, "shared_security.json")
        result = export_all(store, paths)
    payload = json.loads((paths.exports / "bonds.json").read_text(encoding="utf-8"))
    assert result["bonds_json_path"] == "exports/bonds.json"
    assert payload["case_count"] == 1
    case = payload["cases"][0]
    assert case["case_id"] == "CASE-SHARED-2026"
    contracts = {item["contract_id"]: item for item in case["contracts"]}
    assert contracts["FIXED-2026-11"]["joint_status"] == "pending_security"
    assert contracts["REALLOC-2026-11"]["joint_status"] == "eligible"
    fixed_bid4 = next(
        row for row in contracts["FIXED-2026-11"]["decisions"] if row["bid_id"] == "BID-004"
    )
    assert fixed_bid4["shortfall"] == "18000.00"
    actions = [row["action"] for row in contracts["REALLOC-2026-11"]["ledger"]]
    assert actions == ["release", "release", "replace", "replace", "replace"]


def test_bond_case_contract_validation(tmp_path):
    case = json.loads((FIXTURES / "shared_security.json").read_text(encoding="utf-8"))

    def write_and_read(mutated):
        path = tmp_path / "case.json"
        path.write_text(json.dumps(mutated), encoding="utf-8")
        return read_bond_case(path)

    missing_id = {key: value for key, value in case.items() if key != "case_id"}
    with pytest.raises(ValueError, match="case id is invalid"):
        write_and_read(missing_id)
    duplicated = json.loads(json.dumps(case))
    duplicated["contracts"].append(dict(case["contracts"][0]))
    with pytest.raises(ValueError, match="duplicate contract id"):
        write_and_read(duplicated)
    unauthorized = json.loads(json.dumps(case))
    unauthorized["contracts"][1]["authorized_by"] = "supplier-b"
    with pytest.raises(ValueError, match="not authorized"):
        write_and_read(unauthorized)
    unknown_move = json.loads(json.dumps(case))
    unknown_move["contracts"][1]["moves"][0]["instrument_id"] = "G-99"
    with pytest.raises(ValueError, match="unknown bid or instrument"):
        write_and_read(unknown_move)
    over_pledged = json.loads(json.dumps(case))
    over_pledged["pledges"][0]["amount"] = "21000.00"
    with pytest.raises(ValueError, match="face value"):
        write_and_read(over_pledged)
    bad_kind = json.loads(json.dumps(case))
    bad_kind["contracts"][0]["kind"] = "open_pledge"
    with pytest.raises(ValueError, match="unsupported contract kind"):
        write_and_read(bad_kind)
