"""Read-only preview of reviewer seats for a captured synthetic tender.

The first-fit preview remains an unsigned, non-binding view.  The additional
helpers in this module power the ex-ante commitment certification in
:mod:`tender_anchor.panel_commit`:

* :func:`schedule_fingerprint` binds the schedule content so a certificate can
  never be replayed against changed eligibility, capacity, incompatibility or
  contract inputs;
* :func:`evaluate_day` solves one concrete evaluation day under one ex-ante
  contract, honouring per-lot seats, reviewer eligibility, per-reviewer
  capacity, pairwise incompatibilities and same-day absences.
"""

from __future__ import annotations

import hashlib
import itertools
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
            or len(set(lot_ids)) != len(lot_ids) or len(set(reviewer_ids)) != len(reviewers)):
        raise ValueError("lot or reviewer identity is invalid")
    lot_id_set = set(lot_ids)
    reviewer_id_set = set(reviewer_ids)
    for lot in lots:
        if not isinstance(lot.get("seats"), int) or isinstance(lot["seats"], bool) or lot["seats"] < 1:
            raise ValueError("lot seats must be positive")
    for reviewer in reviewers:
        if not isinstance(reviewer.get("eligible_lots"), list) or not set(reviewer["eligible_lots"]) <= lot_id_set:
            raise ValueError("reviewer eligibility is invalid")
        if not isinstance(reviewer.get("capacity"), int) or isinstance(reviewer["capacity"], bool) or reviewer["capacity"] < 0:
            raise ValueError("reviewer capacity is invalid")
    if not isinstance(case.get("incompatibilities", []), list) or not isinstance(case.get("absence_scenarios", []), list):
        raise ValueError("schedule constraints are invalid")
    for pair in case.get("incompatibilities", []):
        if (not isinstance(pair, list) or len(pair) != 2 or pair[0] == pair[1]
                or not set(pair) <= reviewer_id_set):
            raise ValueError("reviewer incompatibility is invalid")
    for scenario in case.get("absence_scenarios", []):
        if (not isinstance(scenario, dict) or not scenario.get("scenario_id")
                or not isinstance(scenario.get("reviewer_ids"), list)
                or not set(scenario["reviewer_ids"]) <= reviewer_id_set):
            raise ValueError("reviewer absence scenario is invalid")
    if "contracts" in case:
        _validate_contracts(case["contracts"], lot_id_set)
    return case


def _validate_contracts(contracts: object, lot_id_set: set[str]) -> None:
    if not isinstance(contracts, list) or not contracts:
        raise ValueError("schedule contracts must be a non-empty list")
    contract_ids: set[str] = set()
    for contract in contracts:
        if not isinstance(contract, dict) or not contract.get("contract_id"):
            raise ValueError("contract identity is invalid")
        contract_id = contract["contract_id"]
        if contract_id in contract_ids:
            raise ValueError("contract identity is duplicated")
        contract_ids.add(contract_id)
        for key in ("locked_lots", "required_lots"):
            value = contract.get(key)
            if not isinstance(value, list) or not set(value) <= lot_id_set:
                raise ValueError(f"contract {key} are invalid")
        if not contract["required_lots"]:
            raise ValueError("contract required_lots are invalid")
        if not set(contract["locked_lots"]) <= set(contract["required_lots"]):
            raise ValueError("locked lots must be part of required lots")
        if len(set(contract["locked_lots"])) != len(contract["locked_lots"]) \
                or len(set(contract["required_lots"])) != len(contract["required_lots"]):
            raise ValueError("contract lot references are duplicated")


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


def canonical_json(value: Any) -> str:
    """Stable JSON text used for fingerprints and certificate digests."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def schedule_fingerprint(case: dict[str, Any]) -> str:
    """SHA-256 over the certification-relevant schedule content.

    The hash is order-independent for list identities that carry no intrinsic
    order (reviewer ids are sorted inside each row's declared structure); the
    lot, reviewer and contract declarations themselves are hashed as captured.
    Replaying a certificate against changed eligibility, capacity,
    incompatibility or contract inputs therefore yields a different fingerprint.
    """
    content = {
        "version": case.get("version"),
        "notice_id": case.get("notice_id"),
        "lots": case.get("lots"),
        "reviewers": case.get("reviewers"),
        "incompatibilities": case.get("incompatibilities", []),
        "contracts": case.get("contracts", []),
        "absence_scenarios": case.get("absence_scenarios", []),
    }
    digest = hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _incompatibility_set(case: dict[str, Any]) -> set[frozenset[str]]:
    return {frozenset(pair) for pair in case.get("incompatibilities", [])}


def evaluate_day(case: dict[str, Any], schedule_preview: dict[str, Any],
                 absent_reviewers: set[str] | list[str], contract: dict[str, Any]
                 ) -> dict[str, Any]:
    """Solve one evaluation day under one ex-ante contract.

    Locked lots keep their signed first-fit seats and only fail when a locked
    reviewer is absent or the locked group is otherwise invalid.  Unlocked
    required lots are backfilled by deterministic backtracking over eligible,
    present reviewers while enforcing distinct seats, per-reviewer capacity and
    pairwise incompatibilities.

    Returns ``{"feasible": bool, "assignments": ..., "reason": ...}`` where the
    reason is a traceable failure witness rather than an exception.
    """
    lot_order = [lot["lot_id"] for lot in case["lots"]]
    lots = {lot["lot_id"]: lot for lot in case["lots"]}
    eligibility = {r["reviewer_id"]: set(r["eligible_lots"]) for r in case["reviewers"]}
    capacity = {r["reviewer_id"]: r["capacity"] for r in case["reviewers"]}
    usage = {reviewer_id: 0 for reviewer_id in capacity}
    absent = set(absent_reviewers)
    incompat = _incompatibility_set(case)
    locked_set = set(contract.get("locked_lots", []))
    required = list(contract["required_lots"])
    assignments: dict[str, list[str]] = {}

    def pairwise_compatible(group: list[str] | tuple[str, ...]) -> bool:
        return all(frozenset(pair) not in incompat for pair in itertools.combinations(group, 2))

    def failure(lot_id: str | None, code: str, chosen: list[str] | None = None) -> dict[str, Any]:
        return {
            "feasible": False,
            "assignments": None,
            "reason": {
                "code": code,
                "lot_id": lot_id,
                "absent_reviewers": sorted(absent),
                "locked_assignments": chosen,
            },
        }

    # Signed seats cannot be moved: validate the locked preview groups as-is.
    for lot_id in (lid for lid in lot_order if lid in locked_set):
        chosen = list(schedule_preview.get("assignments", {}).get(lot_id, []))
        lot = lots[lot_id]
        if len(chosen) != lot["seats"] or len(set(chosen)) != len(chosen):
            return failure(lot_id, "locked_seat_set_invalid", chosen)
        for reviewer_id in chosen:
            if reviewer_id in absent:
                return failure(lot_id, "locked_reviewer_absent", chosen)
            if lot_id not in eligibility.get(reviewer_id, set()):
                return failure(lot_id, "locked_reviewer_ineligible", chosen)
            if usage[reviewer_id] + 1 > capacity[reviewer_id]:
                return failure(lot_id, "locked_capacity_exceeded", chosen)
        if not pairwise_compatible(chosen):
            return failure(lot_id, "locked_incompatible_pair", chosen)
        assignments[lot_id] = chosen
        for reviewer_id in chosen:
            usage[reviewer_id] += 1

    open_lots = [lot_id for lot_id in required if lot_id not in assignments]

    def backtrack(index: int) -> dict[str, list[str]] | None:
        if index == len(open_lots):
            return {}
        lot_id = open_lots[index]
        seats = lots[lot_id]["seats"]
        candidates = sorted(
            reviewer_id for reviewer_id in capacity
            if reviewer_id not in absent
            and lot_id in eligibility[reviewer_id]
            and usage[reviewer_id] < capacity[reviewer_id]
        )
        for combo in itertools.combinations(candidates, seats):
            if not pairwise_compatible(combo):
                continue
            for reviewer_id in combo:
                usage[reviewer_id] += 1
            rest = backtrack(index + 1)
            if rest is not None:
                filled = {lot_id: list(combo)}
                filled.update(rest)
                return filled
            for reviewer_id in combo:
                usage[reviewer_id] -= 1
        return None

    filled = backtrack(0)
    if filled is None:
        blocker = open_lots[0] if open_lots else None
        return failure(blocker, "no_feasible_seat_set")
    assignments.update(filled)
    ordered = {lot_id: assignments[lot_id] for lot_id in lot_order if lot_id in assignments}
    return {"feasible": True, "assignments": ordered, "reason": None}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Preview local tender review seats")
    parser.add_argument("--case", required=True)
    args = parser.parse_args()
    print(json.dumps(preview_schedule(read_schedule_case(args.case)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
