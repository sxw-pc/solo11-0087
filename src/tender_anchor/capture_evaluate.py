"""Per-contract evaluation: units, defects, and coverage certificates."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .capture_identity import (
    EXPECTED_UNITS,
    assess_aliases,
    canonical_units,
    public_units,
)
from .coverage_certificate import ALPHA, CoverageError, as_fraction, make_certificate

_SIZE_PATTERN = re.compile(r"(\d{1,6})\s*\+\s*(\d{1,6})")
_MODEL_TOKENS = ("srs", "without-replacement", "无放回")


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _model_sizes(model: str) -> tuple[int, int] | None:
    match = _SIZE_PATTERN.search(model)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _model_authorized(model: Any) -> bool:
    if not isinstance(model, str):
        return False
    lowered = model.lower()
    return "independent" in lowered and any(token in lowered for token in _MODEL_TOKENS)


def evaluate_contract(
    panel: Mapping[str, Any],
    contract: Mapping[str, Any],
    by_portal: Mapping[str, Mapping[str, str]],
    conflicts: list[str],
    stale: list[dict[str, str]],
    bindings_declared: bool,
) -> dict[str, Any]:
    """Evaluate one contract and return its certificate plus unit evidence."""
    contract_id = str(contract["contract_id"])
    unit = contract.get("unit")
    model = contract.get("sampling_model")
    threshold_value = contract.get("minimum_coverage", panel.get("coverage_threshold"))
    defects: list[dict[str, Any]] = []
    if unit != EXPECTED_UNITS.get(contract_id):
        defects.append(
            {
                "code": "unit_contract_mismatch",
                "expected": EXPECTED_UNITS.get(contract_id),
                "actual": unit,
            }
        )
    if not _nonempty_str(model):
        defects.append({"code": "sampling_model_missing"})
    elif not _model_authorized(model):
        defects.append({"code": "sampling_model_unauthorized", "sampling_model": model})
    sample_a, sample_b = len(by_portal["portal_a"]), len(by_portal["portal_b"])
    declared = _model_sizes(model) if isinstance(model, str) else None
    if declared is not None and declared != (sample_a, sample_b):
        defects.append(
            {
                "code": "capture_round_mismatch",
                "declared": list(declared),
                "observed": [sample_a, sample_b],
            }
        )
    try:
        threshold = as_fraction(threshold_value if threshold_value is not None else "3/4")
    except CoverageError:
        defects.append({"code": "coverage_threshold_invalid", "value": threshold_value})
        threshold = as_fraction("3/4")
    if not 0 < threshold < 1:
        defects.append({"code": "coverage_threshold_invalid", "value": threshold_value})
        threshold = as_fraction("3/4")

    conflict_set = set(conflicts)
    stale_set = {(item["portal"], item["notice_id"]) for item in stale}
    a = {
        notice_id: source_hash
        for notice_id, source_hash in by_portal["portal_a"].items()
        if notice_id not in conflict_set
        and ("portal_a", notice_id) not in stale_set
    }
    b = {
        notice_id: source_hash
        for notice_id, source_hash in by_portal["portal_b"].items()
        if notice_id not in conflict_set
        and ("portal_b", notice_id) not in stale_set
    }
    if conflicts:
        defects.append({"code": "version_hash_conflict", "notice_ids": conflicts})
    if stale:
        defects.append(
            {
                "code": "stale_version_binding",
                "receipts": [dict(item) for item in stale],
            }
        )
    if bindings_declared and (not a or not b):
        defects.append({"code": "capture_round_empty_after_binding"})

    if contract_id == "public_id_records":
        overlap, union = public_units(a, b)
        alias_evidence = {
            "applicable": False,
            "attested_receipts": len(panel.get("alias_receipts", [])),
            "admissible_receipts": 0,
            "isolated_receipts": 0,
            "admissible_receipt_ids": [],
            "isolated": [],
        }
    else:
        alias_evidence = assess_aliases(
            panel, contract, by_portal, conflict_set, stale_set
        )
        defects.extend(alias_evidence.pop("_defects"))
        admissible = alias_evidence.pop("_admissible")
        overlap, union, cycles = canonical_units(a, b, admissible)
        for cycle in cycles:
            alias_evidence["isolated"].append(cycle)
            defects.append({"code": "alias_cycle", **cycle})
        alias_evidence["isolated"].sort(key=lambda item: str(item["receipt_id"]))
        alias_evidence["isolated_receipts"] = len(alias_evidence["isolated"])
        alias_evidence["admissible_receipts"] = len(admissible)
        alias_evidence["admissible_receipt_ids"] = sorted(
            alias["receipt_id"] for alias in admissible
        )

    certificate = make_certificate(
        contract_id=contract_id,
        unit=unit,
        sampling_model=model,
        m=overlap,
        U=union,
        sample_a=sample_a,
        sample_b=sample_b,
        threshold=threshold,
        defects=defects,
        alpha=ALPHA,
    )
    certificate.update(
        {
            "alias_evidence": alias_evidence,
            "conflict_notice_ids": conflicts,
            "stale_receipts": stale,
            "version_bindings_declared": bindings_declared,
            # Conditional claim: only about this contract's own unit, under the
            # declared independent SRSWOR model and a stable population.
            "claims_scope": {
                "unit": unit,
                "population": f"stable {unit} population",
                "conditional_on": "declared independent SRSWOR design",
                "sampling_independence_proven_by_observation": False,
            },
        }
    )
    return certificate
