"""Read-only preview of reviewer seats for a captured synthetic tender."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_schedule_case(path: str | Path) -> dict[str, Any]:
    case = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(case, dict) or case.get("version") != 1 or not case.get("notice_id"):
        raise ValueError("schedule requires version 1 and notice_id")
    lots, reviewers = case.get("lots"), case.get("reviewers")
    if not isinstance(lots, list) or not lots or not isinstance(reviewers, list) or not reviewers:
        raise ValueError("schedule requires lots and reviewers")
    lot_ids = [row.get("lot_id") for row in lots if isinstance(row, dict)]
    reviewer_ids = [row.get("reviewer_id") for row in reviewers if isinstance(row, dict)]
    if (len(lot_ids) != len(lots) or len(reviewer_ids) != len(reviewers)
            or any(not x for x in lot_ids + reviewer_ids)
            or len(set(lot_ids)) != len(lot_ids) or len(set(reviewer_ids)) != len(reviewer_ids)):
        raise ValueError("lot or reviewer identity is invalid")
    for lot in lots:
        if not isinstance(lot.get("seats"), int) or isinstance(lot["seats"], bool) or lot["seats"] < 1:
            raise ValueError("lot seats must be positive")
    for reviewer in reviewers:
        if not isinstance(reviewer.get("eligible_lots"), list) or not set(reviewer["eligible_lots"]) <= set(lot_ids):
            raise ValueError("reviewer eligibility is invalid")
        if not isinstance(reviewer.get("capacity"), int) or isinstance(reviewer["capacity"], bool) or reviewer["capacity"] < 0:
            raise ValueError("reviewer capacity is invalid")
    if not isinstance(case.get("incompatibilities", []), list) or not isinstance(case.get("absence_scenarios", []), list):
        raise ValueError("schedule constraints are invalid")
    for pair in case.get("incompatibilities", []):
        if (not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]
                or not set(pair) <= set(reviewer_ids)):
            raise ValueError("reviewer incompatibility is invalid")
    for scenario in case.get("absence_scenarios", []):
        if (not isinstance(scenario, dict) or not scenario.get("scenario_id")
                or not isinstance(scenario.get("reviewer_ids"), list)
                or not set(scenario["reviewer_ids"]) <= set(reviewer_ids)):
            raise ValueError("reviewer absence scenario is invalid")
    return case


def preview_schedule(case: dict[str, Any]) -> dict[str, Any]:
    """Deterministic first-fit preview; no commitment or contingency certificate."""
    remaining = {r["reviewer_id"]: r["capacity"] for r in case["reviewers"]}
    assignment: dict[str, list[str]] = {}
    for lot in case["lots"]:
        eligible = sorted(r["reviewer_id"] for r in case["reviewers"]
                          if lot["lot_id"] in r["eligible_lots"] and remaining[r["reviewer_id"]] > 0)
        selected = eligible[:lot["seats"]]
        for reviewer_id in selected:
            remaining[reviewer_id] -= 1
        assignment[lot["lot_id"]] = selected
    return {
        "notice_id": case["notice_id"],
        "assignments": assignment,
        "unfilled": [lot["lot_id"] for lot in case["lots"]
                     if len(assignment[lot["lot_id"]]) < lot["seats"]],
        "commitment_decision": None,
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Preview local tender review seats")
    parser.add_argument("--case", required=True)
    args = parser.parse_args()
    print(json.dumps(preview_schedule(read_schedule_case(args.case)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
