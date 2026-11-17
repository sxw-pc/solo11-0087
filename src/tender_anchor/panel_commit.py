"""Commitment preview for a tender reviewer schedule.

The current source carries the binding and contingency inputs but does not
certify that a schedule can survive them.
"""

from __future__ import annotations

from typing import Any


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
