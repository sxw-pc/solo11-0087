"""Isolated tests for the randomized ex-ante audit policy issuance."""
import json
from fractions import Fraction as F
from pathlib import Path

import pytest

from tender_anchor import claim_audit_intake as intake
from tender_anchor.claim_audit_intake import issue_audit_policies, prior_audit_preview

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "fixtures/claim_audit/intake.json"
WITNESS = ROOT / "fixtures/claim_audit/witness.json"


def fixture_data(**mutations):
    data = json.loads(FIXTURE.read_text())
    data.update(mutations)
    return data


def test_deterrence_policy_matches_fixed_fraction_witness():
    report = issue_audit_policies(fixture_data())
    witness = json.loads(WITNESS.read_text())
    det = report["contracts"]["deterrence_cover"]
    assert det["issued"] is True
    # report prints canonical reduced fractions; the witness stores the same values
    assert {k: F(v) for k, v in det["policy"].items()} == {
        k: F(v) for k, v in witness["lottery"].items()
    }
    assert det["policy"]["cross-check"] == "13/23"  # 65/115 reduced
    assert {k: F(v) for k, v in det["certificate"]["adversary"].items()} == {
        k: F(v) for k, v in witness["adversary"].items()
    }
    assert det["worst_undetected_loss"] == witness["optimal_worst_loss"] == "504/115"
    cert = det["certificate"]
    assert cert["upper_bound"] == cert["lower_bound"] == "504/115"
    assert F(cert["upper_bound"]) < F(det["threshold"])
    assert all(c["residual_loss"] == "504/115" for c in det["per_claim"])


def test_recovery_pure_cross_check_misses_claim_c():
    report = issue_audit_policies(fixture_data())
    rec = report["contracts"]["recovery_yield"]
    assert rec["issued"] is True
    assert rec["policy"] == {"direct-A": "0", "direct-B": "0", "direct-C": "0", "cross-check": "1"}
    assert rec["prior_expected_prevented"] == "138/25"
    per_claim = {c["claim_id"]: c for c in rec["per_claim"]}
    assert per_claim["claim-C"]["detection_rate"] == "0"
    assert per_claim["claim-C"]["residual_loss"] == "7"
    assert rec["worst_undetected_loss"] == "7"
    assert F(rec["worst_undetected_loss"]) > F(
        fixture_data()["authorized_contracts"]["deterrence_cover"]["max_worst_loss"]
    )
    # the legacy deterministic preview inherits the same blind spot
    assert prior_audit_preview(fixture_data()) == {
        "action_id": "cross-check",
        "expected_prevented": "5.520",
    }
    assert report["legacy_deterministic_preview"]["worst_undetected_loss"] == "7"


def test_adversary_picks_worst_claim_after_seeing_policy():
    data = fixture_data()
    report = issue_audit_policies(data)
    rec = report["contracts"]["recovery_yield"]
    priors = {c["claim_id"]: F(c["prior"]) for c in data["award_claims"]}
    residuals = {c["claim_id"]: F(c["residual_loss"]) for c in rec["per_claim"]}
    worst = max(residuals.values())
    prior_average = sum(priors[k] * residuals[k] for k in residuals)
    # the adversary picks claim-C (7), not the prior-weighted average (139/50)
    assert worst == F(7) == residuals["claim-C"]
    assert prior_average == F(139, 50) < worst
    # the deterrence adversary mixture keeps every pure action at the bound
    det = report["contracts"]["deterrence_cover"]
    q = {k: F(v) for k, v in det["certificate"]["adversary"].items()}
    claims = {c["claim_id"]: c for c in data["award_claims"]}
    for action in data["audit_actions"]:
        exposure = sum(
            q[cid] * F(claims[cid]["suspected_loss"]) * (1 - F(action["detection"][cid]))
            for cid in claims
        )
        assert exposure >= F(504, 115)


def test_detection_matrix_change_recomputes_policy_and_certificate():
    data = fixture_data()
    for action in data["audit_actions"]:
        if action["action_id"] == "cross-check":
            action["detection"]["claim-C"] = "1"
    report = issue_audit_policies(data)
    det = report["contracts"]["deterrence_cover"]
    # recomputed from scratch, not the canned fixture witness
    assert det["policy"] == {"direct-A": "1/41", "direct-B": "0", "direct-C": "0", "cross-check": "40/41"}
    assert det["worst_undetected_loss"] == "72/41" != "504/115"
    cert = det["certificate"]
    assert cert["upper_bound"] == cert["lower_bound"] == "72/41"
    assert cert["adversary"] == {"claim-A": "32/41", "claim-B": "9/41", "claim-C": "0"}
    assert det["issued"] is True


def test_prior_change_recomputes_recovery_choice():
    data = fixture_data()
    data["award_claims"][0]["prior"] = "0.20"
    data["award_claims"][1]["prior"] = "0.20"
    data["award_claims"][2]["prior"] = "0.60"
    rec = issue_audit_policies(data)["contracts"]["recovery_yield"]
    assert rec["policy"]["direct-C"] == "1"
    assert rec["prior_expected_prevented"] == "21/5"


def test_lottery_probability_conservation():
    report = issue_audit_policies(fixture_data())
    for name in ("deterrence_cover", "recovery_yield"):
        contract = report["contracts"][name]
        probs = [F(p) for p in contract["policy"].values()]
        assert sum(probs) == 1 and all(p >= 0 for p in probs)
        for entry in contract["per_claim"]:
            assert 0 <= F(entry["detection_rate"]) <= 1
            assert F(entry["residual_loss"]) >= 0
    adversary = [F(q) for q in report["contracts"]["deterrence_cover"]["certificate"]["adversary"].values()]
    assert sum(adversary) == 1 and all(q >= 0 for q in adversary)


def test_refusal_when_no_issuable_solution_exists():
    data = fixture_data()
    data["authorized_contracts"]["deterrence_cover"]["max_worst_loss"] = "4"
    data["authorized_contracts"]["recovery_yield"]["min_expected_prevented"] = "6"
    report = issue_audit_policies(data)
    det = report["contracts"]["deterrence_cover"]
    rec = report["contracts"]["recovery_yield"]
    assert det["issued"] is False and det["reason"] == "optimal_worst_loss_exceeds_cap"
    assert det["worst_undetected_loss"] == "504/115"  # optimum reported, rule not weakened
    assert det["threshold"] == "4"
    assert rec["issued"] is False and rec["reason"] == "expected_prevented_below_floor"
    assert rec["prior_expected_prevented"] == "138/25"
    # each contract decides independently: tightening only one refuses only one
    solo = fixture_data()
    solo["authorized_contracts"]["deterrence_cover"]["max_worst_loss"] = "4"
    solo_report = issue_audit_policies(solo)
    assert solo_report["contracts"]["deterrence_cover"]["issued"] is False
    assert solo_report["contracts"]["recovery_yield"]["issued"] is True


def test_unknown_or_non_result_notice_is_refused():
    unknown = issue_audit_policies(fixture_data(notice_id="TA-9999-0000"))
    assert unknown["status"] == "refused"
    assert unknown["reason"] == "result_notice_missing_or_untrusted"
    assert all(not c["issued"] for c in unknown["contracts"].values())
    tender = issue_audit_policies(fixture_data(notice_id="TA-2026-0001"))
    assert tender["status"] == "refused"


def test_untrusted_local_notice_content_is_refused(monkeypatch):
    for fake in (
        lambda notice_id: None,
        lambda notice_id: {"notice_id": "TA-2026-0008", "kind": "tender"},
        lambda notice_id: {"notice_id": "TA-2026-0007", "kind": "result"},
        lambda notice_id: "not-a-notice",
    ):
        monkeypatch.setattr(intake, "get_notice", fake)
        report = issue_audit_policies(fixture_data())
        assert report["status"] == "refused"
        assert all(not c["issued"] for c in report["contracts"].values())
    monkeypatch.undo()


def test_duplicate_ids_are_rejected():
    dup_claim = fixture_data()
    dup_claim["award_claims"] = dup_claim["award_claims"] + [dict(dup_claim["award_claims"][0])]
    with pytest.raises(ValueError):
        issue_audit_policies(dup_claim)
    dup_action = fixture_data()
    dup_action["audit_actions"] = dup_action["audit_actions"] + [dict(dup_action["audit_actions"][0])]
    with pytest.raises(ValueError):
        issue_audit_policies(dup_action)


def test_out_of_range_or_unbalanced_figures_are_rejected():
    def with_claim(index, key, value):
        data = fixture_data()
        data["award_claims"][index][key] = value
        return data

    with pytest.raises(ValueError):
        issue_audit_policies(with_claim(0, "suspected_loss", "-1"))
    with pytest.raises(ValueError):
        issue_audit_policies(with_claim(1, "prior", "-0.1"))
    unbalanced = with_claim(2, "prior", "0.9")
    with pytest.raises(ValueError):
        issue_audit_policies(unbalanced)
    for bad_rate in ("1.5", "-0.2"):
        data = fixture_data()
        data["audit_actions"][3]["detection"]["claim-C"] = bad_rate
        with pytest.raises(ValueError):
            issue_audit_policies(data)
    too_many = fixture_data()
    too_many["award_claims"] = too_many["award_claims"] * 2  # also duplicates ids
    with pytest.raises(ValueError):
        issue_audit_policies(too_many)


def test_input_reordering_keeps_optimum_and_certificate():
    reordered = fixture_data()
    reordered["award_claims"] = list(reversed(reordered["award_claims"]))
    reordered["audit_actions"] = list(reversed(reordered["audit_actions"]))
    base = issue_audit_policies(fixture_data())
    other = issue_audit_policies(reordered)
    for name in ("deterrence_cover", "recovery_yield"):
        first, second = base["contracts"][name], other["contracts"][name]
        assert first["policy"] == second["policy"]
        assert first["worst_undetected_loss"] == second["worst_undetected_loss"]
        assert first["prior_expected_prevented"] == second["prior_expected_prevented"]
        assert first["certificate"] == second["certificate"]
        keyed = lambda c: {e["claim_id"]: e for e in c["per_claim"]}
        assert keyed(first) == keyed(second)


def test_certificate_is_offline_recomputable():
    data = fixture_data()
    report = issue_audit_policies(data)
    claims = {c["claim_id"]: c for c in data["award_claims"]}
    actions = {a["action_id"]: a for a in data["audit_actions"]}

    def residual(action_id, claim_id):
        return F(claims[claim_id]["suspected_loss"]) * (1 - F(actions[action_id]["detection"][claim_id]))

    det = report["contracts"]["deterrence_cover"]
    policy = {k: F(v) for k, v in det["policy"].items()}
    upper = max(
        sum(prob * residual(aid, cid) for aid, prob in policy.items()) for cid in claims
    )
    adversary = {k: F(v) for k, v in det["certificate"]["adversary"].items()}
    lower = min(
        sum(prob * residual(aid, cid) for cid, prob in adversary.items()) for aid in actions
    )
    assert upper == lower == F(det["certificate"]["upper_bound"]) == F(det["worst_undetected_loss"])

    rec = report["contracts"]["recovery_yield"]
    per_action = {
        aid: sum(
            F(c["prior"]) * F(c["suspected_loss"]) * F(actions[aid]["detection"][c["claim_id"]])
            for c in data["award_claims"]
        )
        for aid in actions
    }
    assert max(per_action.values()) == F(rec["certificate"]["upper_bound"])
    assert F(rec["prior_expected_prevented"]) == max(per_action.values())
    assert rec["policy"] == {aid: "1" if gained == max(per_action.values()) else "0"
                           for aid, gained in per_action.items()}


def test_main_prints_issuance_report(capsys):
    assert intake.main(["--fixture", str(FIXTURE)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "evaluated"
    assert report["notice"] == {"notice_id": "TA-2026-0008", "kind": "result"}
    assert report["contracts"]["deterrence_cover"]["issued"] is True
    assert report["contracts"]["recovery_yield"]["issued"] is True
    assert "fictional" in report["disclaimer"]
