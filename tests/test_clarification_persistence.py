"""Persistence, idempotency, audit, and export tests for clarification parity."""

import json
import subprocess
import sys
from pathlib import Path

from clarification_helpers import BASE_FIXTURE, ROOT, clarify, load
from tender_anchor.clarification_intake import case_key
from tender_anchor.export import build_parity_export, export_parity
from tender_anchor.store import Store


def test_baseline_intake_persists_evidence_chain_and_two_contract_decisions(crawled):
    _, paths, _ = crawled
    case = load("base")
    with Store(paths.db_path) as store:
        result = clarify(store, case)
        assert result["outcome"] == "inserted"
        assert store.count("clarification_cases") == 1
        assert store.count("clarification_answers") == 2
        assert store.count("clarification_access") == 2
        assert store.count("parity_certifications") == 2
        decisions = result["decisions"]
        assert decisions["public_open"]["status"] == "certified"
        assert decisions["per_person_receipt"]["status"] == "pending"
        bundle = store.get_clarification_bundle(case_key(case))
        assert bundle["deadline_basis"] == "correction:TA-2026-0003"
        assert bundle["deadline_at"] == "2026-12-05T09:30:00+08:00"
        cert_modes = {row["contract_mode"] for row in bundle["certifications"]}
        assert cert_modes == {"public_open", "per_person_receipt"}


def test_reimport_identical_evidence_replays_first_decision(crawled):
    _, paths, _ = crawled
    case = load("base")
    with Store(paths.db_path) as store:
        first = clarify(store, case)
        first_rows = store.get_parity_certifications(case_key(case))
        second = clarify(store, case)
        second_rows = store.get_parity_certifications(case_key(case))
        assert second["outcome"] == "unchanged"
        assert first_rows == second_rows
        assert first["decisions"] == second["decisions"]
        assert store.count("parity_certifications") == 2
        events = store.get_audit_events(limit=50)
        assert sum(1 for e in events if e["event_type"] == "CLARIFICATION_INTAKE") == 2


def test_completed_receipts_flip_pending_to_certified(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        baseline = clarify(store, load("base"))
        assert baseline["decisions"]["per_person_receipt"]["status"] == "pending"
        completed = load("public_receipt_complete")
        assert case_key(completed) != case_key(load("base"))
        result = clarify(store, completed)
        assert result["outcome"] == "inserted"
        assert result["decisions"]["per_person_receipt"]["status"] == "certified"
        assert store.count("clarification_cases") == 2


def test_export_roundtrip_keeps_the_same_result(crawled):
    _, paths, _ = crawled
    case = load("base")
    with Store(paths.db_path) as store:
        clarify(store, case)
        exported = export_parity(store, paths)
        payload = build_parity_export(store)
    assert exported["case_count"] == 1
    target = paths.root / exported["parity_json_path"]
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk == payload
    item = on_disk["clarification_cases"][0]
    statuses = {c["contract_mode"]: c["status"] for c in item["certifications"]}
    assert statuses == {"public_open": "certified", "per_person_receipt": "pending"}
    assert {a["answer_id"] for a in item["answers"]} == {"A-PRIVATE", "A-PUBLIC"}
    assert {e["event_id"] for e in item["access_events"]} == {"E-A", "E-B"}


def test_unverifiable_case_is_persisted_without_certified_status(crawled):
    _, paths, _ = crawled
    case = load("notice_missing")
    with Store(paths.db_path) as store:
        result = clarify(store, case)
        for decision in result["decisions"].values():
            assert decision["status"] == "unverifiable"
        rows = store.get_parity_certifications(case_key(case))
        assert all(row["status"] == "unverifiable" for row in rows)


def test_audit_events_cover_each_contract_mode(crawled):
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        clarify(store, load("base"))
        events = store.get_audit_events(limit=50)
        targets = {e["target"] for e in events if e["event_type"] == "CLARIFICATION_PARITY"}
        key = case_key(load("base"))
        assert f"{key}#public_open" in targets
        assert f"{key}#per_person_receipt" in targets


def test_cli_clarify_command_runs_offline(crawled):
    _, paths, _ = crawled
    import os
    import sys as _sys

    # Reuse the current interpreter's import paths inside the child so the
    # CLI entry works regardless of how the test harness was launched.
    path_blob = os.pathsep.join(p for p in _sys.path if p)
    bootstrap = (
        "import sys; from tender_anchor.__main__ import main; "
        "raise SystemExit(main(sys.argv[1:]))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = path_blob
    proc = subprocess.run(
        [_sys.executable, "-B", "-c", bootstrap, "clarify",
         "--data-dir", str(paths.root), "--case-file", str(BASE_FIXTURE)],
        capture_output=True, text=True, env=env, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    decisions = payload["clarification"]["decisions"]
    assert decisions["public_open"]["status"] == "certified"
    assert Path(paths.root, payload["export"]["parity_json_path"]).is_file()


def test_supplementing_a_real_receipt_keeps_private_draft_unbinding(crawled):
    """The private draft must never start a participant's preparation clock."""
    _, paths, _ = crawled
    with Store(paths.db_path) as store:
        result = clarify(store, load("public_receipt_complete"))
        decision = result["decisions"]["per_person_receipt"]
        rows = {row["bidder_id"]: row for row in decision["participants"]}
        assert rows["supplier-a"]["public_receipt_at"] == "2026-11-25T14:00:00+08:00"
        bundle = store.get_clarification_bundle(case_key(load("public_receipt_complete")))
        private_events = [
            e for e in bundle["access_events"] if e["answer_id"] == "A-PRIVATE"
        ]
        assert private_events and all(
            e["bidder_id"] == "supplier-a" for e in private_events
        )
