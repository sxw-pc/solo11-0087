"""Ex-ante commitment certification for a tender reviewer schedule.

Two business authorisations over the *same* inputs are evaluated separately:

* ``frozen_signed`` – seats of locked lots are already signed and cannot move;
  only unlocked lots may be backfilled from remaining reviewers on the day;
* ``replan_draft`` – every required lot stays an unsigned draft and may be
  re-planned jointly once the day's absences are observed.

Both contracts honour, per evaluation day, two distinct eligible reviewers per
required lot, per-reviewer capacity one, declared incompatibilities and the
joint absence set of that day.  They are deliberately never merged into a
single "fill as many seats as possible" mode.

Risk is a one-sided Hoeffding upper bound on the Bernoulli failure mean of the
declared i.i.d. evaluation days.  Coverage only holds under the caller's
ex-ante sampling contract, the unchanged day sample and the fixed contracts;
the observed sample never proves that future real days are independent.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .panel_attendance import (
    attendance_fingerprint, joint_absence_witnesses, read_attendance,
    validate_for_certification,
)
from .panel_schedule import (
    canonical_json, evaluate_day, preview_schedule, read_schedule_case,
    schedule_fingerprint,
)

ALPHA = 0.025
DEFAULT_THRESHOLD = 0.15


def preview_commitment(case: dict[str, Any], schedule_preview: dict[str, Any]) -> dict[str, Any]:
    if case["notice_id"] != schedule_preview["notice_id"]:
        raise ValueError("schedule notice mismatch")
    return {
        "notice_id": case["notice_id"],
        "initial_assignments": schedule_preview["assignments"],
        "contracts": [row["contract_id"] for row in case["contracts"]],
        "absence_scenarios": [row["scenario_id"] for row in case["absence_scenarios"]],
        "commitment_decision": None,
    }


def hoeffding_upper_bound(failures: int, n_days: int, alpha: float = ALPHA) -> float:
    """One-sided Hoeffding upper bound on a Bernoulli mean.

    ``min(1, x/n + sqrt(log(1/alpha)/(2n)))``; with ``alpha = .025`` and
    ``n = 256`` the additive margin is ``sqrt(log(40)/512)``.
    """
    if not 0 < alpha < 1:
        raise ValueError("alpha must lie strictly between 0 and 1")
    if not 0 <= failures <= n_days:
        raise ValueError("failure count must lie within the sample size")
    margin = math.sqrt(math.log(1.0 / alpha) / (2.0 * n_days))
    return min(1.0, failures / n_days + margin)


def certify_commitments(case: dict[str, Any], schedule_preview: dict[str, Any],
                        evidence: dict[str, Any], *,
                        threshold: float = DEFAULT_THRESHOLD,
                        alpha: float = ALPHA,
                        prior_fingerprints: dict[str, str] | None = None) -> dict[str, Any]:
    """Certify both ex-ante contracts against one captured attendance sample.

    Returns a traceable certificate.  Invalid inputs produce
    ``status == "rejected"`` with explicit reasons instead of a fabricated
    low-risk figure; otherwise every contract is independently ``certify`` or
    ``hold`` against the shared threshold under its own pre-allocated ``alpha``.
    """
    if not 0 < alpha < 1:
        raise ValueError("alpha must lie strictly between 0 and 1")
    if not 0 < threshold <= 1:
        raise ValueError("threshold must lie within (0, 1]")
    fingerprints = {
        "schedule": schedule_fingerprint(case),
        "attendance": attendance_fingerprint(evidence),
    }
    reasons = validate_for_certification(evidence, case)
    if not case.get("contracts"):
        reasons.append("contracts_undeclared")
    if schedule_preview.get("notice_id") != case.get("notice_id"):
        reasons.append("schedule_notice_mismatch")
    if prior_fingerprints is not None:
        for kind in ("schedule", "attendance"):
            if prior_fingerprints.get(kind) != fingerprints[kind]:
                reasons.append(f"fingerprint_changed:{kind}")

    n_days = len(evidence.get("days", [])) if isinstance(evidence.get("days"), list) else 0
    certificate: dict[str, Any] = {
        "status": "rejected" if reasons else None,
        "notice_id": case.get("notice_id"),
        "cohort_id": evidence.get("cohort_id"),
        "threshold": threshold,
        "alpha": alpha,
        "n_days": n_days,
        "sampling_contract": evidence.get("sampling_contract"),
        "coverage_scope": (
            "coverage holds only under the caller-declared i.i.d. evaluation-day "
            "model, the unchanged day sample and the two ex-ante fixed contracts; "
            "the observed sample does not prove future real-world independence"
        ),
        "fingerprints": fingerprints,
        "rejection_reasons": reasons,
        "joint_absence_witnesses": [],
        "contracts": [],
        "digest": None,
    }
    if reasons:
        certificate["digest"] = _digest(certificate)
        return certificate

    witnesses = joint_absence_witnesses(evidence)
    certificate["joint_absence_witnesses"] = witnesses
    any_hold = False
    for contract in case["contracts"]:
        pattern_witnesses: list[dict[str, Any]] = []
        failures = 0
        for pattern in witnesses:
            absent = set(pattern["absent_reviewers"])
            outcome = evaluate_day(case, schedule_preview, absent, contract)
            if not outcome["feasible"]:
                failures += pattern["days"]
            pattern_witnesses.append({
                "absent_reviewers": pattern["absent_reviewers"],
                "days": pattern["days"],
                "day_ids": pattern["day_ids"],
                "feasible": outcome["feasible"],
                "assignments": outcome["assignments"],
                "reason": outcome["reason"],
                "independent_marginal_expected_days":
                    pattern["independent_marginal_expected_days"],
            })
        bound = hoeffding_upper_bound(failures, n_days, alpha)
        decision = "certify" if bound <= threshold else "hold"
        any_hold = any_hold or decision == "hold"
        certificate["contracts"].append({
            "contract_id": contract["contract_id"],
            "locked_lots": list(contract["locked_lots"]),
            "required_lots": list(contract["required_lots"]),
            "failures": failures,
            "n_days": n_days,
            "failure_rate": failures / n_days,
            "alpha": alpha,
            "hoeffding_margin": math.sqrt(math.log(1.0 / alpha) / (2.0 * n_days)),
            "risk_upper_bound": bound,
            "threshold": threshold,
            "decision": decision,
            "witnesses": pattern_witnesses,
        })
    certificate["status"] = "held" if any_hold else "certified"
    certificate["digest"] = _digest(certificate)
    return certificate


def _digest(certificate: dict[str, Any]) -> str:
    payload = {key: value for key, value in certificate.items() if key != "digest"}
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def render_certificate_text(certificate: dict[str, Any]) -> str:
    """Stable, human-readable summary bound to the certificate digest."""
    lines = [
        f"notice_id={certificate.get('notice_id')}",
        f"cohort_id={certificate.get('cohort_id')}",
        f"status={certificate.get('status')}",
        f"n_days={certificate.get('n_days')}",
        f"threshold={certificate.get('threshold')}",
        f"alpha={certificate.get('alpha')}",
        f"sampling_contract={certificate.get('sampling_contract')}",
        f"fingerprint_schedule={certificate.get('fingerprints', {}).get('schedule')}",
        f"fingerprint_attendance={certificate.get('fingerprints', {}).get('attendance')}",
    ]
    for reason in certificate.get("rejection_reasons", []):
        lines.append(f"rejection_reason={reason}")
    for contract in certificate.get("contracts", []):
        lines.append(
            "contract={contract_id} failures={failures}/{n_days} alpha={alpha} "
            "bound={risk_upper_bound:.10f} threshold={threshold} decision={decision}".format(
                **contract)
        )
        for witness in contract["witnesses"]:
            absent = ",".join(witness["absent_reviewers"]) or "none"
            state = "feasible" if witness["feasible"] else "failed"
            lines.append(
                f"  pattern=({absent}) days={witness['days']} {state}"
            )
            if not witness["feasible"]:
                reason = witness["reason"] or {}
                lines.append(
                    f"    reason={reason.get('code')} lot={reason.get('lot_id')}"
                )
    lines.append(f"digest={certificate.get('digest')}")
    return "\n".join(lines) + "\n"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Certify ex-ante panel commitments against captured attendance days")
    parser.add_argument("--case", required=True, help="schedule case JSON")
    parser.add_argument("--evidence", required=True, help="attendance days JSON")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--alpha", type=float, default=ALPHA)
    parser.add_argument("--text", action="store_true", help="emit stable text summary instead of JSON")
    args = parser.parse_args()
    case = read_schedule_case(Path(args.case))
    evidence = read_attendance(Path(args.evidence))
    schedule_preview = {
        "notice_id": case["notice_id"],
        "assignments": preview_schedule(case)["assignments"],
    }
    certificate = certify_commitments(
        case, schedule_preview, evidence, threshold=args.threshold, alpha=args.alpha)
    if args.text:
        print(render_certificate_text(certificate), end="")
    else:
        print(json.dumps(certificate, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if certificate["status"] != "rejected" else 3)


if __name__ == "__main__":
    main()
