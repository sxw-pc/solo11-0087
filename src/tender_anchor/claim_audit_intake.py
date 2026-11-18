"""Randomized ex-ante audit policy issuance for a synthetic result notice.

Synthetic threat model: at most one claim per round is fraudulent; the
adversary sees the audit lottery but not the sampled action; exactly one
authorized action is executed per round.  Two independent contracts are
evaluated against the same verified result notice:

- ``deterrence_cover`` minimizes the worst undetected loss over claims
  (minimax against an adversary that picks the worst claim after seeing
  the lottery), certified by an exact primal/dual pair of rational linear
  programs whose bounds must coincide;
- ``recovery_yield`` maximizes the prior-expected prevented loss,
  certified by the per-action expectations (a lottery is a convex
  combination of pure actions, so none can beat the best pure action).

All notices, claims and losses are a fictional exercise; nothing here is a
real-world anti-fraud guarantee.
"""
from __future__ import annotations

import argparse
import json
from decimal import Decimal
from fractions import Fraction
from itertools import combinations
from pathlib import Path
from typing import Any

from .mock_portal.fixtures import get_notice

MAX_CLAIMS = 4
MAX_ACTIONS = 5

_DISCLAIMER = (
    "synthetic exercise: all notices, claims and losses are fictional; "
    "no real-world anti-fraud guarantee is claimed"
)
_CONTRACT_NAMES = ("deterrence_cover", "recovery_yield")
_SAMPLING_ASSUMPTIONS = (
    "one_fraud_claim_at_a_time",
    "one_audit",
    "audit_detects_targeted_fraud",
)


def prior_audit_preview(data:dict)->dict:
    notice=get_notice(data["notice_id"])
    if notice is None or notice["kind"]!="result":
        raise ValueError("audit needs a synthetic result notice")
    scores={a["action_id"]:sum(Decimal(c["prior"])*Decimal(c["suspected_loss"])*
                                Decimal(a["detection"][c["claim_id"]]) for c in data["award_claims"])
            for a in data["audit_actions"]}
    chosen=max(scores,key=lambda action_id:(scores[action_id],action_id))
    return {"action_id":chosen,"expected_prevented":str(scores[chosen])}


def _fraction(value: Any, field: str) -> Fraction:
    """Parse an exact rational from JSON text/int/float; reject anything else."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{field} must be a decimal number")
    try:
        return Fraction(str(value)) if isinstance(value, float) else Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{field} must be a decimal number") from exc


def _load_model(data: Any) -> dict[str, Any]:
    """Validate the intake structure and convert every figure to Fraction."""
    if not isinstance(data, dict):
        raise ValueError("intake must be a JSON object")
    notice_id = data.get("notice_id")
    if not isinstance(notice_id, str) or not notice_id:
        raise ValueError("intake requires a notice_id")
    raw_claims = data.get("award_claims")
    if not isinstance(raw_claims, list) or not 1 <= len(raw_claims) <= MAX_CLAIMS:
        raise ValueError(f"intake requires 1..{MAX_CLAIMS} award claims")
    raw_actions = data.get("audit_actions")
    if not isinstance(raw_actions, list) or not 1 <= len(raw_actions) <= MAX_ACTIONS:
        raise ValueError(f"intake requires 1..{MAX_ACTIONS} audit actions")
    claims: list[dict[str, Any]] = []
    claim_ids: set[str] = set()
    prior_total = Fraction(0)
    for entry in raw_claims:
        if not isinstance(entry, dict):
            raise ValueError("claim must be an object")
        claim_id = entry.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id or claim_id in claim_ids:
            raise ValueError("claim identity is invalid")
        claim_ids.add(claim_id)
        loss = _fraction(entry.get("suspected_loss"), "suspected_loss")
        prior = _fraction(entry.get("prior"), "prior")
        if loss < 0:
            raise ValueError("suspected_loss must be non-negative")
        if prior < 0:
            raise ValueError("prior must be non-negative")
        prior_total += prior
        claims.append({"claim_id": claim_id, "loss": loss, "prior": prior})
    if prior_total != 1:
        raise ValueError("claim priors must sum to 1")
    actions: list[dict[str, Any]] = []
    action_ids: set[str] = set()
    for entry in raw_actions:
        if not isinstance(entry, dict):
            raise ValueError("audit action must be an object")
        action_id = entry.get("action_id")
        if not isinstance(action_id, str) or not action_id or action_id in action_ids:
            raise ValueError("audit action identity is invalid")
        action_ids.add(action_id)
        raw_detection = entry.get("detection")
        if not isinstance(raw_detection, dict) or set(raw_detection) != claim_ids:
            raise ValueError("detection must quote every claim exactly once")
        detection: dict[str, Fraction] = {}
        for claim_id, raw_rate in raw_detection.items():
            rate = _fraction(raw_rate, "detection rate")
            if not 0 <= rate <= 1:
                raise ValueError("detection rate must lie in [0, 1]")
            detection[claim_id] = rate
        actions.append({"action_id": action_id, "detection": detection})
    sampling = data.get("sampling_contract")
    if not isinstance(sampling, dict) or any(
        sampling.get(key) is not True for key in _SAMPLING_ASSUMPTIONS
    ):
        raise ValueError("sampling contract assumptions must hold")
    raw_contracts = data.get("authorized_contracts")
    if not isinstance(raw_contracts, dict) or set(raw_contracts) != set(_CONTRACT_NAMES):
        raise ValueError("authorized contracts must be deterrence_cover and recovery_yield")
    deterrence = raw_contracts["deterrence_cover"]
    recovery = raw_contracts["recovery_yield"]
    if not isinstance(deterrence, dict) or deterrence.get("objective") != "minimize_worst_undetected_loss":
        raise ValueError("deterrence_cover objective is invalid")
    if not isinstance(recovery, dict) or recovery.get("objective") != "maximize_prior_expected_prevented":
        raise ValueError("recovery_yield objective is invalid")
    cap = _fraction(deterrence.get("max_worst_loss"), "max_worst_loss")
    floor = _fraction(recovery.get("min_expected_prevented"), "min_expected_prevented")
    if cap < 0 or floor < 0:
        raise ValueError("contract thresholds must be non-negative")
    return {
        "notice_id": notice_id,
        "claims": claims,
        "actions": actions,
        "sampling": {key: sampling[key] for key in _SAMPLING_ASSUMPTIONS},
        "cap": cap,
        "floor": floor,
    }


def _trusted_result_notice(notice_id: str) -> dict[str, Any] | None:
    """Return the local notice only if it is a plausible synthetic result."""
    notice = get_notice(notice_id)
    if not isinstance(notice, dict):
        return None
    if notice.get("notice_id") != notice_id or notice.get("kind") != "result":
        return None
    return notice


def _solve_square(matrix: list[list[Fraction]], rhs: list[Fraction]) -> list[Fraction] | None:
    """Exact Gauss-Jordan elimination; None when the system is singular."""
    size = len(matrix)
    aug = [list(row) + [value] for row, value in zip(matrix, rhs)]
    for col in range(size):
        pivot = next((row for row in range(col, size) if aug[row][col] != 0), None)
        if pivot is None:
            return None
        aug[col], aug[pivot] = aug[pivot], aug[col]
        pivot_value = aug[col][col]
        aug[col] = [entry / pivot_value for entry in aug[col]]
        for row in range(size):
            if row != col and aug[row][col] != 0:
                factor = aug[row][col]
                aug[row] = [x - factor * y for x, y in zip(aug[row], aug[col])]
    return [aug[row][size] for row in range(size)]


def _vertex_lp(
    equalities: list[tuple[list[Fraction], Fraction]],
    inequalities: list[tuple[list[Fraction], Fraction]],
    n_vars: int,
    maximize: bool,
) -> tuple[Fraction, list[Fraction]]:
    """Solve a small LP over a nonempty polytope by exact bounded vertex search.

    The objective is the last variable; every vertex comes from the shared
    equalities plus enough tight inequalities, and the best feasible vertex
    is the exact rational optimum (no sampling involved).
    """
    best: tuple[Fraction, list[Fraction]] | None = None
    need = n_vars - len(equalities)
    for combo in combinations(range(len(inequalities)), need):
        matrix = [row for row, _ in equalities] + [inequalities[k][0] for k in combo]
        rhs = [value for _, value in equalities] + [inequalities[k][1] for k in combo]
        point = _solve_square(matrix, rhs)
        if point is None:
            continue
        if not all(
            sum(coef * x for coef, x in zip(row, point)) <= bound
            for row, bound in inequalities
        ):
            continue
        objective = point[-1]
        if best is None or (objective > best[0] if maximize else objective < best[0]):
            best = (objective, point)
    if best is None:
        raise ValueError("linear program has no feasible vertex")
    return best


def _minimax_program(
    claims: list[dict[str, Any]], actions: list[dict[str, Any]]
) -> tuple[Fraction, list[Fraction]]:
    """Primal: lottery p minimizing the worst residual claim loss t."""
    n = len(actions)
    max_loss = max(claim["loss"] for claim in claims)
    equalities = [([Fraction(1)] * n + [Fraction(0)], Fraction(1))]
    inequalities: list[tuple[list[Fraction], Fraction]] = []
    for j in range(n):
        row = [Fraction(0)] * (n + 1)
        row[j] = Fraction(-1)
        inequalities.append((row, Fraction(0)))
    for claim in claims:
        row = [-claim["loss"] * action["detection"][claim["claim_id"]] for action in actions]
        inequalities.append((row + [Fraction(-1)], -claim["loss"]))
    lower_t = [Fraction(0)] * (n + 1)
    lower_t[n] = Fraction(-1)
    inequalities.append((lower_t, Fraction(0)))
    upper_t = [Fraction(0)] * (n + 1)
    upper_t[n] = Fraction(1)
    inequalities.append((upper_t, max_loss))
    value, point = _vertex_lp(equalities, inequalities, n + 1, maximize=False)
    return value, point[:n]


def _adversary_program(
    claims: list[dict[str, Any]], actions: list[dict[str, Any]]
) -> tuple[Fraction, list[Fraction]]:
    """Dual: claim mixture q maximizing the least pure-action exposure s."""
    m = len(claims)
    max_loss = max(claim["loss"] for claim in claims)
    equalities = [([Fraction(1)] * m + [Fraction(0)], Fraction(1))]
    inequalities: list[tuple[list[Fraction], Fraction]] = []
    for i in range(m):
        row = [Fraction(0)] * (m + 1)
        row[i] = Fraction(-1)
        inequalities.append((row, Fraction(0)))
    for action in actions:
        row = [
            -claim["loss"] * (1 - action["detection"][claim["claim_id"]])
            for claim in claims
        ]
        inequalities.append((row + [Fraction(1)], Fraction(0)))
    lower_s = [Fraction(0)] * (m + 1)
    lower_s[m] = Fraction(-1)
    inequalities.append((lower_s, Fraction(0)))
    upper_s = [Fraction(0)] * (m + 1)
    upper_s[m] = Fraction(1)
    inequalities.append((upper_s, max_loss))
    value, point = _vertex_lp(equalities, inequalities, m + 1, maximize=True)
    return value, point[:m]


def _check_lottery(lottery: list[Fraction]) -> None:
    if any(prob < 0 for prob in lottery) or sum(lottery) != 1:
        raise RuntimeError("audit lottery is not a probability distribution")


def _policy_stats(
    claims: list[dict[str, Any]], actions: list[dict[str, Any]], policy: list[Fraction]
) -> tuple[list[dict[str, Any]], Fraction, Fraction]:
    """Per-claim detection rate and residual loss, prior expectation, worst."""
    per_claim = []
    for claim in claims:
        rate = sum(
            prob * action["detection"][claim["claim_id"]]
            for prob, action in zip(policy, actions)
        )
        per_claim.append(
            {
                "claim_id": claim["claim_id"],
                "detection_rate": rate,
                "residual_loss": claim["loss"] * (1 - rate),
            }
        )
    worst = max(entry["residual_loss"] for entry in per_claim)
    prevented = sum(
        claim["prior"] * (claim["loss"] - entry["residual_loss"])
        for claim, entry in zip(claims, per_claim)
    )
    return per_claim, prevented, worst


def _per_claim_report(per_claim: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "claim_id": entry["claim_id"],
            "detection_rate": str(entry["detection_rate"]),
            "residual_loss": str(entry["residual_loss"]),
        }
        for entry in per_claim
    ]


def _deterrence_report(
    claims: list[dict[str, Any]], actions: list[dict[str, Any]], cap: Fraction
) -> dict[str, Any]:
    """Minimax lottery with an exact primal/dual optimality certificate."""
    value, policy = _minimax_program(claims, actions)
    lower, adversary = _adversary_program(claims, actions)
    _check_lottery(policy)
    _check_lottery(adversary)
    per_claim, prevented, worst = _policy_stats(claims, actions, policy)
    if worst != value or lower != value:
        raise RuntimeError("minimax certificate bounds do not meet")
    for action in actions:
        exposure = sum(
            prob * claim["loss"] * (1 - action["detection"][claim["claim_id"]])
            for prob, claim in zip(adversary, claims)
        )
        if exposure < value:
            raise RuntimeError("adversary mixture does not certify the lower bound")
    issued = value <= cap
    if issued:
        reason = "optimal_worst_loss_within_cap"
        detail = f"certified worst undetected loss {value} is within the authorized cap {cap}"
    else:
        reason = "optimal_worst_loss_exceeds_cap"
        detail = (
            f"even the optimal lottery's worst undetected loss {value} exceeds the "
            f"authorized cap {cap}; refused instead of weakening the rule"
        )
    return {
        "objective": "minimize_worst_undetected_loss",
        "issued": issued,
        "reason": reason,
        "detail": detail,
        "threshold": str(cap),
        "policy": {
            action["action_id"]: str(prob) for action, prob in zip(actions, policy)
        },
        "per_claim": _per_claim_report(per_claim),
        "prior_expected_prevented": str(prevented),
        "worst_undetected_loss": str(worst),
        "certificate": {
            "kind": "exact_minimax_primal_dual",
            "upper_bound": str(value),
            "lower_bound": str(lower),
            "adversary": {
                claim["claim_id"]: str(prob) for claim, prob in zip(claims, adversary)
            },
            "optimal": True,
        },
    }


def _recovery_report(
    claims: list[dict[str, Any]], actions: list[dict[str, Any]], floor: Fraction
) -> dict[str, Any]:
    """Best prior-expected lottery; a convex combination cannot beat its vertex."""
    per_action = [
        sum(
            claim["prior"] * claim["loss"] * action["detection"][claim["claim_id"]]
            for claim in claims
        )
        for action in actions
    ]
    best = max(per_action)
    chosen = min(
        (index for index, gained in enumerate(per_action) if gained == best),
        key=lambda index: actions[index]["action_id"],
    )
    policy = [Fraction(1) if index == chosen else Fraction(0) for index in range(len(actions))]
    _check_lottery(policy)
    per_claim, prevented, worst = _policy_stats(claims, actions, policy)
    if prevented != best:
        raise RuntimeError("simplex certificate does not verify")
    issued = prevented >= floor
    if issued:
        reason = "expected_prevented_meets_floor"
        detail = f"prior expected prevented loss {prevented} meets the authorized floor {floor}"
    else:
        reason = "expected_prevented_below_floor"
        detail = (
            f"best achievable prior expected prevented loss {prevented} is below the "
            f"authorized floor {floor}; refused instead of weakening the rule"
        )
    return {
        "objective": "maximize_prior_expected_prevented",
        "issued": issued,
        "reason": reason,
        "detail": detail,
        "threshold": str(floor),
        "policy": {
            action["action_id"]: str(prob) for action, prob in zip(actions, policy)
        },
        "per_claim": _per_claim_report(per_claim),
        "prior_expected_prevented": str(prevented),
        "worst_undetected_loss": str(worst),
        "certificate": {
            "kind": "linear_objective_over_simplex",
            "per_action_expected_prevented": {
                action["action_id"]: str(gained)
                for action, gained in zip(actions, per_action)
            },
            "upper_bound": str(best),
            "achieved": str(prevented),
            "optimal": True,
        },
    }


def issue_audit_policies(data: dict) -> dict:
    """Evaluate both authorized contracts for one verified result notice."""
    model = _load_model(data)
    notice = _trusted_result_notice(model["notice_id"])
    if notice is None:
        return {
            "notice_id": model["notice_id"],
            "status": "refused",
            "reason": "result_notice_missing_or_untrusted",
            "contracts": {
                name: {"issued": False, "reason": "no_trusted_result_notice"}
                for name in _CONTRACT_NAMES
            },
            "disclaimer": _DISCLAIMER,
        }
    claims = model["claims"]
    actions = model["actions"]
    legacy = prior_audit_preview(data)
    legacy_action = next(a for a in actions if a["action_id"] == legacy["action_id"])
    legacy_worst = max(
        claim["loss"] * (1 - legacy_action["detection"][claim["claim_id"]])
        for claim in claims
    )
    return {
        "notice_id": model["notice_id"],
        "status": "evaluated",
        "notice": {"notice_id": notice["notice_id"], "kind": notice["kind"]},
        "threat_model": model["sampling"],
        "legacy_deterministic_preview": {
            "action_id": legacy["action_id"],
            "expected_prevented": legacy["expected_prevented"],
            "worst_undetected_loss": str(legacy_worst),
        },
        "contracts": {
            "deterrence_cover": _deterrence_report(claims, actions, model["cap"]),
            "recovery_yield": _recovery_report(claims, actions, model["floor"]),
        },
        "disclaimer": _DISCLAIMER,
    }


def main(argv=None)->int:
    p=argparse.ArgumentParser()
    p.add_argument("--fixture",type=Path,default=Path("fixtures/claim_audit/intake.json"))
    args=p.parse_args(argv)
    print(json.dumps(issue_audit_policies(json.loads(args.fixture.read_text(encoding="utf-8"))),sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
