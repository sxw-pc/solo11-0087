"""Exact one-sided hypergeometric coverage certificates for capture overlap.

The certificate assumes the contract's a-priori model: two independent simple
random samples without replacement (SRSWOR) of fixed sizes ``n_a`` and ``n_b``
from an unknown stable population of ``N`` units, of which ``m`` were seen in
both captures and ``U`` distinct units were seen at least once.

For a fixed ``N`` the recapture count ``M`` is hypergeometric.  The one-sided
95% population upper bound is the largest ``N`` whose tail probability
``P_N(M >= m)`` is still at least ``ALPHA``.  The coverage lower bound is then
``U / N``.  No Chapman point estimate or ``m / (n_a + n_b)`` shortcut is used.
"""

from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from math import comb
from typing import Any, Iterable

ALPHA = Fraction(1, 20)
DEFAULT_THRESHOLD = Fraction(3, 4)


class CoverageError(ValueError):
    """Raised when a coverage bound cannot be inverted from the evidence."""


def as_fraction(value: int | float | str | Fraction) -> Fraction:
    """Convert a declared threshold to an exact rational without float drift."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):
        raise CoverageError("boolean is not a valid probability threshold")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        return Fraction(str(value))
    if isinstance(value, str):
        try:
            return Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise CoverageError(f"invalid probability threshold: {value!r}") from exc
    raise CoverageError(f"invalid probability threshold: {value!r}")


def hypergeometric_tail(
    population: int,
    sample_a: int,
    sample_b: int,
    overlap: int,
) -> Fraction:
    """Return the exact ``P(M >= overlap)`` for a hypergeometric recapture count."""
    if population < 0 or sample_a < 0 or sample_b < 0:
        raise CoverageError("population and sample sizes must be non-negative")
    if sample_a > population or sample_b > population:
        raise CoverageError("sample size exceeds the candidate population")
    if overlap < 0:
        raise CoverageError("overlap must be non-negative")
    support_low = max(0, sample_a + sample_b - population)
    support_high = min(sample_a, sample_b)
    if overlap <= support_low:
        return Fraction(1)
    if overlap > support_high:
        return Fraction(0)
    numerator = sum(
        comb(sample_a, k) * comb(population - sample_a, sample_b - k)
        for k in range(overlap, support_high + 1)
    )
    return Fraction(numerator, comb(population, sample_b))


def invert_population_upper(
    *,
    overlap: int,
    seen_union: int,
    sample_a: int,
    sample_b: int,
    alpha: Fraction = ALPHA,
) -> dict[str, Any]:
    """Invert ``P_N(M >= m) >= alpha`` to the largest admissible population.

    The search starts at ``N = U`` (where the observed union is the whole
    population and the tail is 1) and stops at the first integer whose tail
    falls strictly below ``alpha``.  The previous integer is ``N_upper``.
    """
    if alpha <= 0 or alpha >= 1:
        raise CoverageError("alpha must lie strictly between 0 and 1")
    if overlap <= 0:
        raise CoverageError("no recaptures: the population upper bound is unbounded")
    if seen_union < max(sample_a, sample_b):
        raise CoverageError("seen union is smaller than a capture round")
    candidate = seen_union
    while hypergeometric_tail(candidate, sample_a, sample_b, overlap) >= alpha:
        candidate += 1
        if candidate > 1_000_000:  # pragma: no cover - defensive stop
            raise CoverageError("population upper bound did not converge")
    upper = candidate - 1
    tail_upper = hypergeometric_tail(upper, sample_a, sample_b, overlap)
    tail_next = hypergeometric_tail(upper + 1, sample_a, sample_b, overlap)
    return {
        "N_upper": upper,
        "tail_at_upper": tail_upper,
        "tail_at_upper_plus_one": tail_next,
        "tail_at_upper_meets_alpha": tail_upper >= alpha,
        "tail_at_upper_plus_one_below_alpha": tail_next < alpha,
    }


def canonical_digest(payload: Any) -> str:
    """Return a reorder-stable SHA-256 digest of a JSON-compatible payload."""
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _rounded(value: Fraction) -> float:
    return round(float(value), 9)


def make_certificate(
    *,
    contract_id: str,
    unit: str | None,
    sampling_model: str | None,
    m: int,
    U: int,
    sample_a: int,
    sample_b: int,
    threshold: Fraction,
    defects: Iterable[dict[str, Any]],
    alpha: Fraction = ALPHA,
) -> dict[str, Any]:
    """Build one contract's certificate from exact recapture evidence."""
    fatal = sorted(
        (dict(item) for item in defects), key=lambda item: str(item.get("code", ""))
    )
    reasons = sorted({str(item.get("code", "unspecified")) for item in fatal})
    payload: dict[str, Any] = {
        "contract_id": contract_id,
        "unit": unit,
        "sampling_model": sampling_model,
        "m": m,
        "U": U,
        "sample_a": sample_a,
        "sample_b": sample_b,
        "threshold": float(threshold),
        "alpha": _rounded(alpha),
        "confidence": _rounded(1 - alpha),
        "defects": fatal,
    }
    if m <= 0:
        payload.update(
            {
                "N_upper": None,
                "coverage_lower_bound": None,
                "coverage_lower_bound_fraction": None,
                "inversion_witness": None,
                "decision": "reject",
                "conclusion": "reject",
                "certificate_issued": False,
                "can_claim_threshold_coverage": False,
                "reason_codes": sorted(set(reasons) | {"no_recaptures"}),
            }
        )
        return payload
    inversion = invert_population_upper(
        overlap=m,
        seen_union=U,
        sample_a=sample_a,
        sample_b=sample_b,
        alpha=alpha,
    )
    bound = Fraction(U, inversion["N_upper"])
    witness = {
        "method": "exact_hypergeometric_tail_inversion",
        "one_sided": True,
        "N_upper": inversion["N_upper"],
        "tail_probability_at_N_upper": _rounded(inversion["tail_at_upper"]),
        "N_upper_plus_one": inversion["N_upper"] + 1,
        "tail_probability_at_N_upper_plus_one": _rounded(
            inversion["tail_at_upper_plus_one"]
        ),
        "tail_at_N_upper_meets_alpha": inversion["tail_at_upper_meets_alpha"],
        "tail_at_N_upper_plus_one_below_alpha": inversion[
            "tail_at_upper_plus_one_below_alpha"
        ],
    }
    if fatal:
        decision = "reject"
        reasons.append("invalid_evidence")
    elif bound < threshold:
        decision = "hold"
        reasons.append("coverage_below_threshold")
    else:
        decision = "certify"
    payload.update(
        {
            "N_upper": inversion["N_upper"],
            "coverage_lower_bound": _rounded(bound),
            "coverage_lower_bound_fraction": f"{U}/{inversion['N_upper']}",
            "inversion_witness": witness,
            "decision": decision,
            "conclusion": decision,
            "certificate_issued": decision == "certify",
            "can_claim_threshold_coverage": decision == "certify",
            "reason_codes": sorted(set(reasons)),
        }
    )
    return payload
