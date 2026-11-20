"""Read synthetic reviewer attendance evidence without risk certification.

The original read-only preview is preserved unchanged.  Certification support
adds:

* :func:`attendance_fingerprint` – a content digest that survives a reorder of
  the sample days but changes whenever the evidence itself changes;
* :func:`validate_for_certification` – the ex-ante gates a certificate depends
  on (declared i.i.d. sampling model, unique sample days, reviewer identity
  agreement with the schedule case);
* :func:`joint_absence_witnesses` – the per joint-absence-pattern evidence
  block, including the product-of-marginals counterfactual that demonstrates
  why same-day joint absences must never be split into independent events.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def read_attendance(path: str | Path) -> dict[str, Any]:
    evidence = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(evidence, dict) or evidence.get("version") != 1:
        raise ValueError("attendance requires version 1")
    reviewer_ids = evidence.get("reviewer_ids")
    days = evidence.get("days")
    if (not isinstance(reviewer_ids, list) or not reviewer_ids
            or len(set(reviewer_ids)) != len(reviewer_ids)
            or not isinstance(days, list) or not days):
        raise ValueError("attendance reviewer ids or days invalid")
    seen = set()
    for day in days:
        if not isinstance(day, dict) or not day.get("day_id") or day["day_id"] in seen:
            raise ValueError("attendance day identity invalid")
        seen.add(day["day_id"])
        absent = day.get("absent_reviewers")
        if not isinstance(absent, list) or len(set(absent)) != len(absent) or not set(absent) <= set(reviewer_ids):
            raise ValueError("attendance absence invalid")
    counts = evidence.get("observed_pattern_counts")
    if counts is not None:
        actual = {"all_present": 0, "R4_only": 0, "R4_R5": 0}
        for day in days:
            absent = set(day["absent_reviewers"])
            if not absent:
                actual["all_present"] += 1
            elif absent == {"R4"}:
                actual["R4_only"] += 1
            elif absent == {"R4", "R5"}:
                actual["R4_R5"] += 1
        if counts != actual or sum(actual.values()) != len(days):
            raise ValueError("attendance pattern count mismatch")
    return evidence


def preview_attendance(evidence: dict[str, Any]) -> dict[str, Any]:
    patterns: dict[tuple[str, ...], int] = {}
    for day in evidence["days"]:
        key = tuple(sorted(day["absent_reviewers"]))
        patterns[key] = patterns.get(key, 0) + 1
    return {
        "cohort_id": evidence["cohort_id"],
        "n_days": len(evidence["days"]),
        "absence_patterns": [{"absent_reviewers": list(key), "days": count}
                             for key, count in sorted(patterns.items())],
        "risk_bounds": None,
    }


def attendance_fingerprint(evidence: dict[str, Any]) -> str:
    """SHA-256 over the certification-relevant attendance content.

    Days are canonicalised by ``day_id`` so reordering the 256 unchanged sample
    days yields the same fingerprint (the certificate is replayed and
    re-verified, but its risk figures cannot move), while any changed day,
    absence set or reviewer cohort changes the digest.
    """
    content = {
        "version": evidence.get("version"),
        "cohort_id": evidence.get("cohort_id"),
        "reviewer_ids": list(evidence.get("reviewer_ids", [])),
        "sampling_contract": evidence.get("sampling_contract"),
        "days": [
            {"day_id": day["day_id"], "absent_reviewers": sorted(day["absent_reviewers"])}
            for day in sorted(evidence.get("days", []), key=lambda row: row["day_id"])
        ],
    }
    from .panel_schedule import canonical_json
    digest = hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def validate_for_certification(evidence: dict[str, Any], case: dict[str, Any]) -> list[str]:
    """Return traceable rejection reasons; an empty list certifies the gates."""
    reasons: list[str] = []
    sampling = evidence.get("sampling_contract")
    if not isinstance(sampling, str) or not sampling.strip():
        reasons.append("sampling_model_undeclared")
    elif "independent" not in sampling.lower():
        reasons.append("sampling_model_not_independent_days")
    reviewer_ids = evidence.get("reviewer_ids")
    if not isinstance(reviewer_ids, list) or set(reviewer_ids) != {r["reviewer_id"] for r in case["reviewers"]}:
        reasons.append("reviewer_identity_mismatch")
    days = evidence.get("days")
    if not isinstance(days, list) or not days:
        reasons.append("sample_days_missing")
        return reasons
    day_ids: list[Any] = []
    for day in days:
        if not isinstance(day, dict) or not day.get("day_id"):
            reasons.append("sample_day_identity_invalid")
            continue
        day_ids.append(day["day_id"])
        absent = day.get("absent_reviewers")
        if (not isinstance(absent, list) or len(set(absent)) != len(absent)
                or not set(absent) <= set(reviewer_ids or [])):
            reasons.append("sample_day_absence_invalid")
    if len(day_ids) != len(set(day_ids)):
        reasons.append("duplicate_sample_day")
    if "observed_pattern_counts" in evidence and not isinstance(
            evidence.get("observed_pattern_counts"), dict):
        reasons.append("observed_pattern_counts_invalid")
    # de-duplicate while preserving deterministic first-seen order
    return list(dict.fromkeys(reasons))


def joint_absence_witnesses(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    """Group the sample by joint absence pattern, retaining joint evidence.

    For every pattern the block reports the observed day count and the count
    that a product-of-marginals model would predict, so the certificate can
    show why joint absences are evaluated jointly rather than multiplied.
    """
    days = evidence["days"]
    n_days = len(days)
    pattern_counts: dict[tuple[str, ...], int] = {}
    pattern_day_ids: dict[tuple[str, ...], list[str]] = {}
    reviewer_ids = list(evidence["reviewer_ids"])
    marginal_absences = {reviewer_id: 0 for reviewer_id in reviewer_ids}
    for day in days:
        key = tuple(sorted(day["absent_reviewers"]))
        pattern_counts[key] = pattern_counts.get(key, 0) + 1
        pattern_day_ids.setdefault(key, []).append(day["day_id"])
        for reviewer_id in key:
            marginal_absences[reviewer_id] += 1
    witnesses: list[dict[str, Any]] = []
    for key in sorted(pattern_counts):
        marginal_product = 1.0
        for reviewer_id in reviewer_ids:
            p = marginal_absences[reviewer_id] / n_days
            marginal_product *= p if reviewer_id in key else (1.0 - p)
        witnesses.append({
            "absent_reviewers": list(key),
            "days": pattern_counts[key],
            "day_ids": sorted(pattern_day_ids[key]),
            "observed_share": pattern_counts[key] / n_days,
            "independent_marginal_expected_days": marginal_product * n_days,
        })
    return witnesses


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Preview local reviewer attendance evidence")
    parser.add_argument("--evidence", required=True)
    args = parser.parse_args()
    print(json.dumps(preview_attendance(read_attendance(args.evidence)),
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
