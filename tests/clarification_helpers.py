"""Shared helpers for clarification parity tests."""

from __future__ import annotations

from pathlib import Path

from tender_anchor.clarification_intake import (
    case_fingerprint,
    read_clarification_case,
)
from tender_anchor.clarification_store import intake_clarification, make_clarify_run_id
from tender_anchor.store import Store

ROOT = Path(__file__).parents[1]
CASES = ROOT / "fixtures/clarifications/cases"
BASE_FIXTURE = ROOT / "fixtures/clarifications/case.json"


def load(name: str) -> dict:
    if name == "base":
        return read_clarification_case(BASE_FIXTURE)
    return read_clarification_case(CASES / f"{name}.json")


def clarify(store: Store, case: dict) -> dict:
    return intake_clarification(
        store, case, make_clarify_run_id(case_fingerprint(case))
    )
