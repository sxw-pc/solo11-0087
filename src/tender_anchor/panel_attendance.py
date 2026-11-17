"""Read synthetic reviewer attendance evidence without risk certification."""

from __future__ import annotations

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
