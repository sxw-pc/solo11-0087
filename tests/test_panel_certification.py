"""Certification tests for the two ex-ante panel commitment contracts."""

from __future__ import annotations

import copy
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from tender_anchor.panel_attendance import (
    attendance_fingerprint, joint_absence_witnesses, preview_attendance,
    read_attendance, validate_for_certification,
)
from tender_anchor.panel_commit import (
    ALPHA, DEFAULT_THRESHOLD, certify_commitments, hoeffding_upper_bound,
    render_certificate_text,
)
from tender_anchor.panel_schedule import (
    evaluate_day, preview_schedule, read_schedule_case, schedule_fingerprint,
)

ROOT = Path(__file__).resolve().parents[1]
PANEL = ROOT / "fixtures" / "panel"
CASE_PATH = PANEL / "schedule.json"
CONFLICT_PATH = PANEL / "schedule-conflict.json"
ATTENDANCE_PATH = PANEL / "attendance-days.json"


@pytest.fixture
def bundle():
    case = read_schedule_case(CASE_PATH)
    evidence = read_attendance(ATTENDANCE_PATH)
    preview = preview_schedule(case)
    return case, preview, evidence


def _contract(certificate: dict, contract_id: str) -> dict:
    rows = [row for row in certificate["contracts"] if row["contract_id"] == contract_id]
    assert len(rows) == 1
    return rows[0]


def _witness(contract_row: dict, absent: list[str]) -> dict:
    rows = [row for row in contract_row["witnesses"]
            if row["absent_reviewers"] == absent]
    assert len(rows) == 1
    return rows[0]


# ---------------------------------------------------------------------------
# Main fixture: the two contracts reach different conclusions on equal inputs
# ---------------------------------------------------------------------------

def test_main_fixture_contracts_diverge(bundle):
    case, preview, evidence = bundle
    certificate = certify_commitments(case, preview, evidence)
    assert certificate["status"] == "held"
    assert certificate["n_days"] == 256
    assert certificate["alpha"] == ALPHA == 0.025
    assert certificate["threshold"] == DEFAULT_THRESHOLD == 0.15

    frozen = _contract(certificate, "frozen_signed")
    replan = _contract(certificate, "replan_draft")
    assert frozen["decision"] == "hold"
    assert replan["decision"] == "certify"
    assert frozen["failures"] == 32 and replan["failures"] == 4
    assert frozen["failure_rate"] == pytest.approx(32 / 256)
    assert replan["failure_rate"] == pytest.approx(4 / 256)


def test_hoeffding_bounds_match_specification(bundle):
    case, preview, evidence = bundle
    certificate = certify_commitments(case, preview, evidence)
    expected_margin = math.sqrt(math.log(40) / 512)
    assert expected_margin == pytest.approx(0.08488134473378872)
    for row in certificate["contracts"]:
        assert row["alpha"] == pytest.approx(0.025)
        assert row["hoeffding_margin"] == pytest.approx(expected_margin)
        assert row["risk_upper_bound"] == pytest.approx(
            min(1.0, row["failures"] / 256 + expected_margin))
    assert _contract(certificate, "frozen_signed")["risk_upper_bound"] == pytest.approx(0.20988134473378872)
    assert _contract(certificate, "replan_draft")["risk_upper_bound"] == pytest.approx(0.10050634473378872)
    # direct bound helper agrees
    assert hoeffding_upper_bound(32, 256) == pytest.approx(0.20988134473378872)
    assert hoeffding_upper_bound(4, 256) == pytest.approx(0.10050634473378872)
    assert hoeffding_upper_bound(256, 256) == 1.0


def test_per_pattern_witnesses_classify_every_sample_day(bundle):
    case, preview, evidence = bundle
    certificate = certify_commitments(case, preview, evidence)
    for row in certificate["contracts"]:
        assert sum(witness["days"] for witness in row["witnesses"]) == 256
        assert len(row["witnesses"]) == 3

    frozen = _contract(certificate, "frozen_signed")
    replan = _contract(certificate, "replan_draft")

    # all-present days always work; frozen keeps its signed R1/R2 seats
    all_present_f = _witness(frozen, [])
    all_present_r = _witness(replan, [])
    assert all_present_f["feasible"] and all_present_r["feasible"]
    assert all_present_f["assignments"] == {"LOT-A": ["R1", "R2"], "LOT-B": ["R4", "R5"]}

    # R4 alone: frozen cannot move signed LOT-A seats, LOT-B keeps only R5
    r4_frozen = _witness(frozen, ["R4"])
    assert r4_frozen["days"] == 28 and not r4_frozen["feasible"]
    assert r4_frozen["reason"]["code"] == "no_feasible_seat_set"
    assert r4_frozen["reason"]["lot_id"] == "LOT-B"
    assert r4_frozen["assignments"] is None

    # R4 alone: replan rebuilds both lots with two distinct eligible reviewers.
    # The task statement illustrates A=[R2,R3], B=[R1,R5]; the deterministic
    # solver may emit any legal witness, so assert the binding constraints and
    # additionally assert the illustrative witness is itself feasible.
    r4_replan = _witness(replan, ["R4"])
    assert r4_replan["days"] == 28 and r4_replan["feasible"]
    assignments = r4_replan["assignments"]
    assert set(assignments) == {"LOT-A", "LOT-B"}
    eligibility = {r["reviewer_id"]: set(r["eligible_lots"]) for r in case["reviewers"]}
    for lot_id, group in assignments.items():
        assert len(group) == 2 and len(set(group)) == 2
        assert all(lot_id in eligibility[name] for name in group)
    used = [name for group in assignments.values() for name in group]
    assert len(used) == len(set(used)) == 4
    assert "R4" not in used
    # the deterministic solver must return the same legal witness on repeat
    repeat = evaluate_day(
        case, preview, {"R4"},
        next(row for row in case["contracts"] if row["contract_id"] == "replan_draft"))
    assert repeat["feasible"]
    assert repeat["assignments"] == assignments

    # the task's illustrative replanning A=[R2,R3], B=[R1,R5] is itself a
    # constraint-satisfying assignment (distinct eligible reviewers, capacity 1)
    illustrative = {"LOT-A": {"R2", "R3"}, "LOT-B": {"R1", "R5"}}
    flat = {name for group in illustrative.values() for name in group}
    assert len(flat) == 4 and "R4" not in flat
    for lot_id, group in illustrative.items():
        seats = next(lot["seats"] for lot in case["lots"] if lot["lot_id"] == lot_id)
        assert len(group) == seats
        for name in group:
            reviewer = next(r for r in case["reviewers"] if r["reviewer_id"] == name)
            assert lot_id in reviewer["eligible_lots"]

    # joint R4/R5 absence: both contracts fail the same four days
    for row in (frozen, replan):
        joint = _witness(row, ["R4", "R5"])
        assert joint["days"] == 4 and not joint["feasible"]
        assert joint["day_ids"] == ["D001", "D236", "D243", "D250"]


def test_frozen_contract_never_moves_signed_lot_a_seats(bundle):
    case, preview, evidence = bundle
    frozen = next(row for row in case["contracts"] if row["contract_id"] == "frozen_signed")
    # every feasible frozen day keeps the exact signed LOT-A group
    for absent in (set(), {"R4"}, {"R5"}, {"R4", "R5"}):
        outcome = evaluate_day(case, preview, absent, frozen)
        if outcome["feasible"]:
            assert outcome["assignments"]["LOT-A"] == ["R1", "R2"]
        else:
            assert outcome["assignments"] is None
    # an absent signed reviewer is a traceable locked-seat failure, not a replan
    outcome = evaluate_day(case, preview, {"R1"}, frozen)
    assert not outcome["feasible"]
    assert outcome["reason"]["code"] == "locked_reviewer_absent"
    assert outcome["reason"]["lot_id"] == "LOT-A"


def test_locked_group_failures_are_reported_without_replanning(bundle):
    case, preview, _ = bundle
    frozen = next(row for row in case["contracts"] if row["contract_id"] == "frozen_signed")
    incompatible = copy.deepcopy(case)
    incompatible["incompatibilities"] = [["R1", "R2"]]
    outcome = evaluate_day(incompatible, preview, set(), frozen)
    assert not outcome["feasible"]
    assert outcome["reason"]["code"] == "locked_incompatible_pair"

    ineligible = copy.deepcopy(case)
    ineligible["reviewers"][0]["eligible_lots"] = ["LOT-B"]
    outcome = evaluate_day(ineligible, preview, set(), frozen)
    assert not outcome["feasible"]
    assert outcome["reason"]["code"] == "locked_reviewer_ineligible"


def test_contract_structure_is_validated(tmp_path):
    base = json.loads(CASE_PATH.read_text(encoding="utf-8"))
    mutations = (
        lambda c: c["contracts"][0].__setitem__("locked_lots", ["LOT-X"]),
        lambda c: (c["contracts"][0].__setitem__("required_lots", ["LOT-A"]),
                   c["contracts"][0].__setitem__("locked_lots", ["LOT-B"])),
        lambda c: c.__setitem__("contracts", []),
    )
    for mutate in mutations:
        broken = copy.deepcopy(base)
        mutate(broken)
        path = tmp_path / "broken.json"
        path.write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(ValueError, match="contract|locked"):
            read_schedule_case(path)


def test_replan_enforces_capacity_eligibility_and_incompatibility(bundle):
    case, preview, evidence = bundle
    replan = next(row for row in case["contracts"] if row["contract_id"] == "replan_draft")
    outcome = evaluate_day(case, preview, {"R4"}, replan)
    assert outcome["feasible"]
    for lot_id, group in outcome["assignments"].items():
        seats = next(lot["seats"] for lot in case["lots"] if lot["lot_id"] == lot_id)
        assert len(group) == seats and len(set(group)) == seats
        for reviewer_id in group:
            reviewer = next(r for r in case["reviewers"] if r["reviewer_id"] == reviewer_id)
            assert lot_id in reviewer["eligible_lots"]
    used = [name for group in outcome["assignments"].values() for name in group]
    assert len(used) == len(set(used))  # capacity one per reviewer


# ---------------------------------------------------------------------------
# Conflict fixture: extra incompatibilities flip replan on the 28 R4 days
# ---------------------------------------------------------------------------

def test_conflict_fixture_flips_replan_to_hold(bundle):
    case, preview, evidence = bundle
    conflict = read_schedule_case(CONFLICT_PATH)
    assert conflict["incompatibilities"] == [["R1", "R3"], ["R2", "R3"]]
    conflict_preview = preview_schedule(conflict)
    # first-fit preview itself stays unsigned
    assert conflict_preview["commitment_decision"] is None

    replan = next(row for row in conflict["contracts"] if row["contract_id"] == "replan_draft")
    # R4-only day: single-reviewer eligibility suggests four seats, but R3 can
    # never join a legal LOT-A group, so every required lot cannot be filled
    outcome = evaluate_day(conflict, conflict_preview, {"R4"}, replan)
    assert not outcome["feasible"]
    assert outcome["reason"]["lot_id"] == "LOT-A"

    certificate = certify_commitments(conflict, conflict_preview, evidence)
    assert certificate["status"] == "held"
    frozen = _contract(certificate, "frozen_signed")
    replan_row = _contract(certificate, "replan_draft")
    assert frozen["failures"] == replan_row["failures"] == 32
    assert frozen["decision"] == replan_row["decision"] == "hold"
    assert replan_row["risk_upper_bound"] == pytest.approx(0.20988134473378872)
    assert not _witness(replan_row, ["R4"])["feasible"]
    assert not _witness(replan_row, ["R4", "R5"])["feasible"]
    assert _witness(replan_row, [])["feasible"]
    # schedule fingerprint must distinguish the changed incompatibility input
    assert schedule_fingerprint(conflict) != schedule_fingerprint(case)


# ---------------------------------------------------------------------------
# The 4/256 plug-in share is not itself a risk bound
# ---------------------------------------------------------------------------

def _synthetic_case_and_evidence(tmp_path: Path, failures: int):
    """Two lots, five reviewers, exactly ``failures`` infeasible i.i.d. days.

    On a joint R3/R4 absence day only R1, R2, R5 remain for four seats, so no
    feasible assignment exists; all-present days (and single absences) fill
    cleanly.
    """
    case = {
        "version": 1,
        "notice_id": "TA-TEST-1",
        "lots": [
            {"lot_id": "LOT-A", "seats": 2, "priority": "normal", "draft_reviewers": ["R1", "R2"]},
            {"lot_id": "LOT-B", "seats": 2, "priority": "urgent", "draft_reviewers": []},
        ],
        "reviewers": [
            {"reviewer_id": "R1", "eligible_lots": ["LOT-A", "LOT-B"], "capacity": 1},
            {"reviewer_id": "R2", "eligible_lots": ["LOT-A", "LOT-B"], "capacity": 1},
            {"reviewer_id": "R3", "eligible_lots": ["LOT-A"], "capacity": 1},
            {"reviewer_id": "R4", "eligible_lots": ["LOT-B"], "capacity": 1},
            {"reviewer_id": "R5", "eligible_lots": ["LOT-B"], "capacity": 1},
        ],
        "incompatibilities": [],
        "contracts": [
            {"contract_id": "replan_draft", "locked_lots": [],
             "required_lots": ["LOT-A", "LOT-B"]},
        ],
        "absence_scenarios": [],
    }
    days = [{"day_id": f"D{i + 1:03d}", "absent_reviewers": ["R3", "R4"]}
            for i in range(failures)]
    days += [{"day_id": f"D{i + 1:03d}", "absent_reviewers": []}
             for i in range(failures, 256)]
    evidence = {
        "version": 1,
        "cohort_id": "SYNTHETIC-256",
        "reviewer_ids": ["R1", "R2", "R3", "R4", "R5"],
        "sampling_contract": "256 independent local evaluation days declared ex ante",
        "observed_pattern_counts": {},
        "days": days,
    }
    return case, evidence


def test_plugin_share_below_threshold_still_holds_when_hoeffding_bound_exceeds_it(tmp_path):
    case, evidence = _synthetic_case_and_evidence(tmp_path, failures=4)
    preview = preview_schedule(case)
    # 4/256 = 0.015625 would pass a 2% plug-in threshold, but the one-sided
    # Hoeffding bound 0.1005 exceeds it, so certification must be refused.
    certificate = certify_commitments(case, preview, evidence, threshold=0.02)
    row = _contract(certificate, "replan_draft")
    assert row["failures"] == 4
    assert row["failure_rate"] == pytest.approx(0.015625)
    assert row["failure_rate"] < 0.02 < row["risk_upper_bound"]
    assert row["decision"] == "hold"
    assert certificate["status"] == "held"


def test_synthetic_evidence_certifies_under_the_real_15_percent_gate(tmp_path):
    case, evidence = _synthetic_case_and_evidence(tmp_path, failures=4)
    certificate = certify_commitments(case, preview_schedule(case), evidence)
    assert _contract(certificate, "replan_draft")["decision"] == "certify"
    assert certificate["status"] == "certified"


# ---------------------------------------------------------------------------
# Joint absences must not be split into independent marginals
# ---------------------------------------------------------------------------

def test_independent_marginal_product_misestimates_joint_absence(bundle):
    case, preview, evidence = bundle
    witnesses = joint_absence_witnesses(evidence)
    joint = next(row for row in witnesses if row["absent_reviewers"] == ["R4", "R5"])
    # observed same-day joint absence is 4 days (1.5625%); multiplying the
    # marginal absence rates predicts half a day (0.195%) — an 8x under-estimate
    assert joint["days"] == 4
    assert joint["observed_share"] == pytest.approx(0.015625)
    assert joint["independent_marginal_expected_days"] == pytest.approx(0.5)
    r4_only = next(row for row in witnesses if row["absent_reviewers"] == ["R4"])
    assert r4_only["independent_marginal_expected_days"] == pytest.approx(31.5)
    # the certificate retains the joint counterfactual instead of multiplying
    certificate = certify_commitments(case, preview, evidence)
    replan = _contract(certificate, "replan_draft")
    joint_witness = _witness(replan, ["R4", "R5"])
    assert joint_witness["independent_marginal_expected_days"] == pytest.approx(0.5)
    assert joint_witness["days"] == 4
    # those four joint days drive all replan failures; treating them as
    # independent events would predict 0.5 failure-days and mis-certify
    assert replan["failures"] == 4


# ---------------------------------------------------------------------------
# Rejection gates: never fabricate a low-risk figure
# ---------------------------------------------------------------------------

def test_missing_sampling_contract_is_rejected(bundle):
    case, preview, evidence = bundle
    broken = copy.deepcopy(evidence)
    del broken["sampling_contract"]
    certificate = certify_commitments(case, preview, broken)
    assert certificate["status"] == "rejected"
    assert "sampling_model_undeclared" in certificate["rejection_reasons"]
    assert certificate["contracts"] == []
    # the rejected payload still carries bound input fingerprints for tracing
    assert certificate["fingerprints"]["schedule"].startswith("sha256:")
    assert certificate["digest"].startswith("sha256:")


def test_non_independent_sampling_model_is_rejected(bundle):
    case, preview, evidence = bundle
    broken = copy.deepcopy(evidence)
    broken["sampling_contract"] = "clustered repeated days, unknown dependence"
    certificate = certify_commitments(case, preview, broken)
    assert certificate["status"] == "rejected"
    assert "sampling_model_not_independent_days" in certificate["rejection_reasons"]


def test_duplicate_sample_day_is_rejected(bundle):
    case, preview, evidence = bundle
    duplicated = copy.deepcopy(evidence)
    duplicated["days"] = evidence["days"] + [evidence["days"][0]]
    reasons = validate_for_certification(duplicated, case)
    assert "duplicate_sample_day" in reasons
    certificate = certify_commitments(case, preview, duplicated)
    assert certificate["status"] == "rejected"
    assert "duplicate_sample_day" in certificate["rejection_reasons"]


def test_reviewer_identity_conflict_is_rejected(bundle):
    case, preview, evidence = bundle
    broken = copy.deepcopy(evidence)
    broken["reviewer_ids"] = ["R1", "R2", "R3", "R4", "RX"]
    certificate = certify_commitments(case, preview, broken)
    assert certificate["status"] == "rejected"
    assert "reviewer_identity_mismatch" in certificate["rejection_reasons"]


def test_missing_days_and_contract_gates(bundle, tmp_path):
    case, preview, evidence = bundle
    no_days = copy.deepcopy(evidence)
    no_days["days"] = []
    certificate = certify_commitments(case, preview, no_days)
    assert certificate["status"] == "rejected"
    assert "sample_days_missing" in certificate["rejection_reasons"]

    no_contracts = copy.deepcopy(case)
    no_contracts["contracts"] = []
    certificate = certify_commitments(case, preview, evidence)
    valid = certificate["digest"]
    rejected = certify_commitments(no_contracts, preview, evidence)
    assert rejected["status"] == "rejected"
    assert "contracts_undeclared" in rejected["rejection_reasons"]
    assert valid  # the valid baseline certificate exists


def test_prior_fingerprint_mismatch_blocks_certificate_replay(bundle):
    case, preview, evidence = bundle
    certificate = certify_commitments(case, preview, evidence)
    stale = {"schedule": certificate["fingerprints"]["schedule"],
             "attendance": "sha256:" + "0" * 64}
    rejected = certify_commitments(case, preview, evidence, prior_fingerprints=stale)
    assert rejected["status"] == "rejected"
    assert "fingerprint_changed:attendance" in rejected["rejection_reasons"]
    # matching prior fingerprints re-issue the identical certificate
    replayed = certify_commitments(case, preview, evidence,
                                   prior_fingerprints=certificate["fingerprints"])
    assert replayed["digest"] == certificate["digest"]


# ---------------------------------------------------------------------------
# Determinism, digest binding and stable summaries
# ---------------------------------------------------------------------------

def test_replay_is_byte_stable_and_digest_binds_content(bundle):
    case, preview, evidence = bundle
    first = certify_commitments(case, preview, evidence)
    second = certify_commitments(case, preview, evidence)
    assert json.dumps(first, sort_keys=True, ensure_ascii=False) == \
        json.dumps(second, sort_keys=True, ensure_ascii=False)
    text_first = render_certificate_text(first)
    assert text_first == render_certificate_text(second)
    assert first["digest"] in text_first
    # threshold and alpha participate in the binding
    other = certify_commitments(case, preview, evidence, threshold=0.10)
    assert other["digest"] != first["digest"]
    other_alpha = certify_commitments(case, preview, evidence, alpha=0.05)
    assert other_alpha["digest"] != first["digest"]


def test_reordering_sample_days_recertifies_with_identical_risk(bundle):
    case, preview, evidence = bundle
    first = certify_commitments(case, preview, evidence)
    reordered = copy.deepcopy(evidence)
    reordered["days"] = list(reversed(reordered["days"]))
    # the attendance fingerprint is content-based and order-independent
    assert attendance_fingerprint(reordered) == attendance_fingerprint(evidence)
    recertified = certify_commitments(case, preview, reordered)
    assert recertified["digest"] == first["digest"]
    for before, after in zip(first["contracts"], recertified["contracts"]):
        assert after["failures"] == before["failures"]
        assert after["risk_upper_bound"] == before["risk_upper_bound"]
        assert after["decision"] == before["decision"]


def test_changed_inputs_change_fingerprints_and_conclusions(bundle):
    case, preview, evidence = bundle
    baseline = certify_commitments(case, preview, evidence)
    mutated_schedule = copy.deepcopy(case)
    mutated_schedule["reviewers"][2]["eligible_lots"] = []  # R3 loses LOT-A
    mutated = certify_commitments(mutated_schedule, preview_schedule(mutated_schedule), evidence)
    assert mutated["fingerprints"]["schedule"] != baseline["fingerprints"]["schedule"]
    assert mutated["digest"] != baseline["digest"]

    mutated_evidence = copy.deepcopy(evidence)
    mutated_evidence["days"][0]["absent_reviewers"] = []
    recertified = certify_commitments(case, preview, mutated_evidence)
    assert recertified["fingerprints"]["attendance"] != baseline["fingerprints"]["attendance"]


def test_coverage_scope_is_explicitly_limited(bundle):
    case, preview, evidence = bundle
    certificate = certify_commitments(case, preview, evidence)
    scope = certificate["coverage_scope"].lower()
    assert "i.i.d." in scope or "i.i.d" in scope
    assert "ex-ante" in scope
    assert "does not prove" in scope


# ---------------------------------------------------------------------------
# Original previews remain available and unsigned
# ---------------------------------------------------------------------------

def test_legacy_previews_remain_unsigned(bundle):
    case, preview, evidence = bundle
    assert preview["assignments"] == {"LOT-A": ["R1", "R2"], "LOT-B": ["R4", "R5"]}
    assert preview["unfilled"] == []
    assert preview["commitment_decision"] is None
    attendance_preview = preview_attendance(evidence)
    assert attendance_preview["absence_patterns"] == [
        {"absent_reviewers": [], "days": 224},
        {"absent_reviewers": ["R4"], "days": 28},
        {"absent_reviewers": ["R4", "R5"], "days": 4},
    ]
    assert attendance_preview["risk_bounds"] is None


def test_cli_emits_certificate_and_schedule_preview(bundle):
    env = {"PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    schedule = subprocess.run(
        [sys.executable, "-B", "-m", "tender_anchor.panel_schedule",
         "--case", str(CASE_PATH)],
        cwd=ROOT, capture_output=True, text=True, env=env, check=True)
    payload = json.loads(schedule.stdout)
    assert payload["commitment_decision"] is None

    commit = subprocess.run(
        [sys.executable, "-B", "-m", "tender_anchor.panel_commit",
         "--case", str(CASE_PATH), "--evidence", str(ATTENDANCE_PATH)],
        cwd=ROOT, capture_output=True, text=True, env=env, check=True)
    certificate = json.loads(commit.stdout)
    assert certificate["status"] == "held"
    assert {row["contract_id"] for row in certificate["contracts"]} == {
        "frozen_signed", "replan_draft"}

    text = subprocess.run(
        [sys.executable, "-B", "-m", "tender_anchor.panel_commit",
         "--case", str(CASE_PATH), "--evidence", str(ATTENDANCE_PATH), "--text"],
        cwd=ROOT, capture_output=True, text=True, env=env, check=True)
    assert "contract=frozen_signed" in text.stdout
    assert "contract=replan_draft" in text.stdout
    assert "decision=hold" in text.stdout and "decision=certify" in text.stdout
