import json
import sqlite3
from pathlib import Path

import pytest

from tender_anchor.capture_overlap import (
    certificate_is_current,
    preview_overlap,
    read_capture_panel,
    read_version_bindings,
)

PANEL = Path(__file__).resolve().parents[1] / "fixtures" / "coverage" / "two-portals.json"


def contract(report, contract_id):
    return next(
        item for item in report["contract_results"] if item["contract_id"] == contract_id
    )


def write_panel(tmp_path, payload, name="panel.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def load_payload():
    return json.loads(PANEL.read_text(encoding="utf-8"))


def test_two_portal_receipts_preserve_distinct_identity_contract_evidence():
    panel = read_capture_panel(PANEL)
    report = preview_overlap(panel)
    assert report["cohort_id"] == "CAPTURE-2026-PANEL"
    assert report["portal_a_count"] == 100
    assert report["portal_b_count"] == 100
    assert report["exact_id_overlap"] == 40
    assert report["attested_aliases"] == 20
    assert report["contracts"] == ["operator_canonical_notices", "public_id_records"]


def test_public_id_contract_holds_at_exact_hypergeometric_upper_bound():
    report = preview_overlap(read_capture_panel(PANEL))
    public = contract(report, "public_id_records")
    assert (public["m"], public["U"], public["N_upper"]) == (40, 160, 301)
    assert public["coverage_lower_bound_fraction"] == "160/301"
    assert public["coverage_lower_bound"] == pytest.approx(160 / 301, abs=1e-9)
    witness = public["inversion_witness"]
    assert witness["tail_probability_at_N_upper"] == pytest.approx(0.052140334, abs=1e-9)
    assert witness["tail_probability_at_N_upper_plus_one"] == pytest.approx(
        0.049205819, abs=1e-9
    )
    assert witness["tail_at_N_upper_meets_alpha"] is True
    assert witness["tail_at_N_upper_plus_one_below_alpha"] is True
    assert public["decision"] == "hold"
    assert public["can_claim_threshold_coverage"] is False
    assert report["coverage_decision"]["public_id_records"]["decision"] == "hold"


def test_operator_contract_certifies_only_after_authorized_aliases_merge():
    report = preview_overlap(read_capture_panel(PANEL))
    canonical = contract(report, "operator_canonical_notices")
    assert (canonical["m"], canonical["U"], canonical["N_upper"]) == (60, 140, 185)
    assert canonical["coverage_lower_bound_fraction"] == "140/185"
    assert canonical["coverage_lower_bound"] == pytest.approx(140 / 185, abs=1e-9)
    witness = canonical["inversion_witness"]
    assert witness["tail_probability_at_N_upper"] == pytest.approx(0.053388540, abs=1e-9)
    assert witness["tail_probability_at_N_upper_plus_one"] == pytest.approx(
        0.045226241, abs=1e-9
    )
    assert canonical["decision"] == "certify"
    assert canonical["can_claim_threshold_coverage"] is True
    evidence = canonical["alias_evidence"]
    assert evidence["trusted_alias_issuer"] == "local-mock-operator"
    assert evidence["admissible_receipts"] == 20
    assert evidence["isolated_receipts"] == 0
    assert evidence["admissible_receipt_ids"][0] == "ALIAS-041"
    public = contract(report, "public_id_records")
    assert public["alias_evidence"]["applicable"] is False
    assert public["alias_evidence"]["admissible_receipts"] == 0


def test_contracts_never_mix_population_units():
    report = preview_overlap(read_capture_panel(PANEL))
    canonical = contract(report, "operator_canonical_notices")
    public = contract(report, "public_id_records")
    assert canonical["unit"] == "operator_canonical_notice"
    assert public["unit"] == "public_id"
    assert canonical["U"] == public["U"] - 20
    assert canonical["N_upper"] != public["N_upper"]
    assert canonical["coverage_lower_bound"] != public["coverage_lower_bound"]


def test_same_hash_without_operator_receipt_never_merges_units(tmp_path):
    payload = load_payload()
    payload["alias_receipts"] = []
    report = preview_overlap(read_capture_panel(write_panel(tmp_path, payload)))
    canonical = contract(report, "operator_canonical_notices")
    # TA-041/MIR-041 share a hash yet remain two public IDs without a receipt.
    assert (canonical["m"], canonical["U"]) == (40, 160)
    assert canonical["decision"] == "hold"
    assert canonical["can_claim_threshold_coverage"] is False


def test_same_id_with_different_hash_is_conflicted_not_counted(tmp_path):
    payload = load_payload()
    for row in payload["captures"]["portal_b"]:
        if row["notice_id"] == "TA-001":
            row["source_hash"] = "f" * 64
    report = preview_overlap(read_capture_panel(write_panel(tmp_path, payload)))
    assert report["exact_id_overlap"] == 39
    for contract_id in ("public_id_records", "operator_canonical_notices"):
        result = contract(report, contract_id)
        assert result["conflict_notice_ids"] == ["TA-001"]
        assert result["decision"] == "reject"
        assert "version_hash_conflict" in result["reason_codes"]


def test_one_a_id_reused_by_two_b_aliases_is_isolated(tmp_path):
    payload = load_payload()
    source_a = {
        item["notice_id"]: item["source_hash"] for item in payload["captures"]["portal_a"]
    }
    # MIR-061 is otherwise unaliased, so giving it TA-041's bytes keeps every
    # receipt structurally valid while making TA-041 the shared A endpoint.
    for row in payload["captures"]["portal_b"]:
        if row["notice_id"] == "MIR-061":
            row["source_hash"] = source_a["TA-041"]
    payload["alias_receipts"].append(
        {
            "receipt_id": "ALIAS-999",
            "issuer": "local-mock-operator",
            "portal_a_id": "TA-041",
            "portal_b_id": "MIR-061",
        }
    )
    panel = read_capture_panel(write_panel(tmp_path, payload))
    report = preview_overlap(panel)
    canonical = contract(report, "operator_canonical_notices")
    assert canonical["decision"] == "reject"
    assert canonical["alias_evidence"]["admissible_receipts"] == 19
    assert canonical["alias_evidence"]["isolated_receipts"] == 2
    isolated = {
        item["receipt_id"]: item["reasons"]
        for item in canonical["alias_evidence"]["isolated"]
    }
    assert "alias_portal_a_reused" in isolated["ALIAS-041"]
    assert "alias_portal_a_reused" in isolated["ALIAS-999"]
    # The 19 clean aliases still count; the two ambiguous ones never enter m.
    assert (canonical["m"], canonical["U"]) == (59, 141)
    assert canonical["can_claim_threshold_coverage"] is False


def test_alias_receipt_must_link_matching_stored_content(tmp_path):
    panel = load_payload()
    panel["alias_receipts"][0]["portal_b_id"] = "MIR-061"
    path = write_panel(tmp_path, panel, "invalid.json")
    with pytest.raises(ValueError, match="alias"):
        read_capture_panel(path)


def test_repeated_alias_receipt_id_is_rejected(tmp_path):
    panel = load_payload()
    panel["alias_receipts"].append(dict(panel["alias_receipts"][0], portal_b_id="MIR-061"))
    path = write_panel(tmp_path, panel, "repeated.json")
    with pytest.raises(ValueError, match="alias"):
        read_capture_panel(path)


def test_missing_sampling_model_blocks_certificate(tmp_path):
    payload = load_payload()
    for item in payload["contracts"]:
        if item["contract_id"] == "public_id_records":
            item.pop("sampling_model")
    report = preview_overlap(read_capture_panel(write_panel(tmp_path, payload)))
    public = contract(report, "public_id_records")
    assert public["decision"] == "reject"
    assert public["certificate_issued"] is False
    assert "sampling_model_missing" in public["reason_codes"]


def test_untrusted_alias_authority_isolates_every_receipt(tmp_path):
    payload = load_payload()
    for item in payload["contracts"]:
        if item["contract_id"] == "operator_canonical_notices":
            item["trusted_alias_issuer"] = "unrelated-operator"
    report = preview_overlap(read_capture_panel(write_panel(tmp_path, payload)))
    canonical = contract(report, "operator_canonical_notices")
    assert canonical["decision"] == "reject"
    assert canonical["m"] == 40
    assert canonical["alias_evidence"]["admissible_receipts"] == 0
    assert canonical["alias_evidence"]["isolated_receipts"] == 20


def test_capture_order_and_alias_order_do_not_change_results(tmp_path):
    original = preview_overlap(read_capture_panel(PANEL))
    payload = load_payload()
    payload["captures"]["portal_a"].reverse()
    payload["captures"]["portal_b"] = payload["captures"]["portal_b"][::-1]
    payload["alias_receipts"].reverse()
    payload["contracts"].reverse()
    shuffled = preview_overlap(read_capture_panel(write_panel(tmp_path, payload)))
    assert shuffled == original
    assert shuffled["source_digest"] == original["source_digest"]
    assert shuffled["evidence_digest"] == original["evidence_digest"]


def test_rerun_is_byte_stable():
    panel = read_capture_panel(PANEL)
    first = preview_overlap(panel)
    second = preview_overlap(panel)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["evidence_digest"] == second["evidence_digest"]


def _bindings_from(panel):
    return {
        row["notice_id"]: row["source_hash"]
        for portal in ("portal_a", "portal_b")
        for row in panel["captures"][portal]
    }


def test_version_binding_change_forces_recertification(tmp_path):
    panel = read_capture_panel(PANEL)
    cached = preview_overlap(panel, version_bindings=_bindings_from(panel))
    assert certificate_is_current(cached, panel, version_bindings=_bindings_from(panel))
    rebound = dict(_bindings_from(panel), **{"TA-041": "a" * 64})
    assert not certificate_is_current(cached, panel, version_bindings=rebound)
    report = preview_overlap(panel, version_bindings=rebound)
    stale = report["contract_results"][0]["stale_receipts"]
    assert {"portal": "portal_a", "notice_id": "TA-041", "reason": "version_changed"} in stale
    for result in report["contract_results"]:
        assert result["decision"] == "reject"
        assert "stale_version_binding" in result["reason_codes"]


def test_capture_round_mismatch_rejects_contract(tmp_path):
    payload = load_payload()
    for item in payload["contracts"]:
        if item["contract_id"] == "public_id_records":
            item["sampling_model"] = "independent SRSWOR 90+100 over stable public-ID records"
    report = preview_overlap(read_capture_panel(write_panel(tmp_path, payload)))
    public = contract(report, "public_id_records")
    assert public["decision"] == "reject"
    assert "capture_round_mismatch" in public["reason_codes"]
    assert public["certificate_issued"] is False


def test_canonical_units_marks_a_redundant_alias_chain_as_cycle():
    from tender_anchor.capture_identity import canonical_units

    overlap, union, cycles = canonical_units(
        {"X1": "h"},
        {"Y1": "h"},
        [
            {"receipt_id": "ALIAS-1", "portal_a_id": "X1", "portal_b_id": "Y1"},
            {"receipt_id": "ALIAS-2", "portal_a_id": "X1", "portal_b_id": "Y1"},
        ],
    )
    assert (overlap, union) == (1, 1)
    assert cycles == [{"receipt_id": "ALIAS-2", "reason": "alias_cycle"}]


def test_version_bindings_read_from_store(tmp_path):
    panel = read_capture_panel(PANEL)
    bindings = _bindings_from(panel)
    db_path = tmp_path / "store.sqlite3"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE notices(notice_id TEXT, source_hash TEXT)")
    connection.executemany("INSERT INTO notices VALUES (?, ?)", bindings.items())
    connection.commit()
    connection.close()
    loaded = read_version_bindings(db_path)
    assert loaded == {key: value.lower() for key, value in bindings.items()}
    report = preview_overlap(panel, version_bindings=loaded)
    assert report["coverage_decision"]["operator_canonical_notices"]["decision"] == "certify"
