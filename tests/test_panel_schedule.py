import json
from pathlib import Path

import pytest

from tender_anchor.panel_commit import preview_commitment
from tender_anchor.panel_attendance import preview_attendance, read_attendance
from tender_anchor.panel_schedule import preview_schedule, read_schedule_case


CASE = Path(__file__).resolve().parents[1] / "fixtures" / "panel" / "schedule.json"


def test_first_fit_preview_shows_unfilled_second_lot():
    case = read_schedule_case(CASE)
    result = preview_schedule(case)
    assert result["notice_id"] == "TA-2026-0001"
    assert result["assignments"] == {"LOT-A": ["R1", "R2"], "LOT-B": ["R4", "R5"]}
    assert result["unfilled"] == []
    assert result["commitment_decision"] is None
    commitment = preview_commitment(case, result)
    assert commitment["initial_assignments"] == result["assignments"]
    assert commitment["commitment_decision"] is None
    assert [row["scenario_id"] for row in case["absence_scenarios"]] == ["R4-unavailable", "R5-unavailable"]
    assert [row["contract_id"] for row in case["contracts"]] == ["frozen_signed", "replan_draft"]


def test_invalid_capacity_is_rejected(tmp_path):
    case = json.loads(CASE.read_text(encoding="utf-8"))
    case["reviewers"][0]["capacity"] = -1
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(case), encoding="utf-8")
    with pytest.raises(ValueError, match="capacity"):
        read_schedule_case(path)


def test_pair_constraints_are_preserved_but_not_certified():
    case = read_schedule_case(CASE.with_name("schedule-conflict.json"))
    assert case["incompatibilities"] == [["R1", "R3"], ["R2", "R3"]]
    assert preview_schedule(case)["commitment_decision"] is None


def test_attendance_retains_joint_absence_evidence_without_claiming_risk():
    evidence = read_attendance(CASE.with_name("attendance-days.json"))
    preview = preview_attendance(evidence)
    assert preview["n_days"] == 256
    assert preview["absence_patterns"] == [
        {"absent_reviewers": [], "days": 224},
        {"absent_reviewers": ["R4"], "days": 28},
        {"absent_reviewers": ["R4", "R5"], "days": 4},
    ]
    assert preview["risk_bounds"] is None
