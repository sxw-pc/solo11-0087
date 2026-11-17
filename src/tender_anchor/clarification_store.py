"""Idempotent persistence of clarification evidence and parity certifications."""

from __future__ import annotations

import json
from typing import Any

from .clarification_intake import case_fingerprint, case_key
from .normalize import now_iso, stable_hash
from .parity import CONTRACT_MODES, adjudicate
from .store import Store

CLARIFY_RUN_PREFIX = "clarify"


class ClarificationConflict(ValueError):
    """Raised when re-imported evidence contradicts a recorded case."""


def make_clarify_run_id(case_hash: str) -> str:
    return f"{CLARIFY_RUN_PREFIX}-{now_iso().replace('-', '').replace(':', '')}-{case_hash[:6]}"


def ensure_run(store: Store, run_id: str, case_hash: str) -> None:
    """Record the offline intake run without touching crawl run bookkeeping."""
    existing = store.connection.execute(
        "SELECT 1 FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()
    if existing is None:
        with store.transaction():
            store.connection.execute(
                "INSERT INTO runs(run_id, started_at, finished_at, status, portal_base, "
                "config_hash) VALUES (?, ?, ?, 'completed', 'offline://clarification-intake', ?)",
                (run_id, now_iso(), now_iso(), case_hash),
            )


def _decision_hash(decision: dict[str, Any]) -> str:
    return stable_hash(decision)


def record_case(store: Store, case: dict[str, Any], run_id: str) -> dict[str, str]:
    """Insert or reconcile one clarification case; never rewrite evidence.

    Returns ``{"outcome": inserted|unchanged|updated}``. A changed payload is
    allowed only while no certification has been issued for the case.
    """
    key = case_key(case)
    fingerprint = case_fingerprint(case)
    now = now_iso()
    existing = store.connection.execute(
        "SELECT case_hash FROM clarification_cases WHERE case_key=?", (key,)
    ).fetchone()

    if existing is not None:
        # Any final outcome (certified/rejected) freezes the evidence chain.
        # Provisional rows (pending/unverifiable) may still be corrected by a
        # fresh import; a genuinely new supplement uses a new case key.
        issued = store.connection.execute(
            "SELECT COUNT(*) AS n FROM parity_certifications "
            "WHERE case_key=? AND status IN ('certified', 'rejected')", (key,)
        ).fetchone()["n"]
        if existing["case_hash"] != fingerprint and issued:
            raise ClarificationConflict(
                "clarification evidence changed after certification; "
                "the issued certification must not be rewritten"
            )
        outcome = "unchanged" if existing["case_hash"] == fingerprint else "updated"
    else:
        outcome = "inserted"

    notice = store.get_notice(case["notice_id"])
    corrections = store.get_corrections(case["notice_id"]) if notice is not None else []
    deadline = adjudicate(case, notice or None, corrections, CONTRACT_MODES[0])["deadline"]
    deadline_basis = deadline["basis"] if deadline else ""
    deadline_at = deadline["deadline_at"] if deadline else ""

    with store.transaction():
        store.connection.execute(
            "INSERT INTO clarification_cases(case_key, notice_id, question_id, questioner, "
            "participants_json, case_hash, payload_json, deadline_basis, deadline_at, "
            "first_seen_run, last_seen_run, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(case_key) DO UPDATE SET participants_json=excluded.participants_json, "
            "case_hash=excluded.case_hash, payload_json=excluded.payload_json, "
            "deadline_basis=excluded.deadline_basis, deadline_at=excluded.deadline_at, "
            "last_seen_run=excluded.last_seen_run, updated_at=excluded.updated_at",
            (
                key, case["notice_id"], case["question_id"], case["questioner"],
                json.dumps(case.get("participants", []), ensure_ascii=False),
                fingerprint,
                json.dumps(case, ensure_ascii=False, sort_keys=True),
                deadline_basis, deadline_at,
                run_id, run_id, now,
            ),
        )
        store.connection.execute(
            "DELETE FROM clarification_answers WHERE case_key=?", (key,)
        )
        store.connection.execute(
            "DELETE FROM clarification_access WHERE case_key=?", (key,)
        )
        for answer in case["answers"]:
            store.connection.execute(
                "INSERT INTO clarification_answers(case_key, answer_id, visibility, recipient, "
                "term_id, published_at, redacted_questioner, content_sha256, content) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key, answer["answer_id"], answer["visibility"],
                    answer.get("recipient", ""), answer.get("term_id", ""),
                    answer["at"], 1 if answer.get("redacted_questioner") is True else 0,
                    answer["content_sha256"], answer["content"],
                ),
            )
        for event in case["access_events"]:
            digest = next(
                (row["content_sha256"] for row in case["answers"]
                 if row["answer_id"] == event["answer_id"]),
                "",
            )
            store.connection.execute(
                "INSERT INTO clarification_access(case_key, event_id, answer_id, bidder_id, "
                "accessed_at, content_sha256) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    key, event["event_id"], event["answer_id"], event["bidder_id"],
                    event["at"], digest,
                ),
            )
    return {"case_key": key, "outcome": outcome, "case_hash": fingerprint}


def certify_case(store: Store, case: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Adjudicate both contract modes and preserve the first certification.

    Re-imports of identical evidence replay the stored decision. If the store's
    correction evidence has changed the outcome, the conflict is surfaced
    instead of silently overwriting the issued record.
    """
    key = case_key(case)
    notice = store.get_notice(case["notice_id"])
    corrections = store.get_corrections(case["notice_id"]) if notice is not None else []
    evidence_hash = case_fingerprint(case)
    now = now_iso()
    results: dict[str, dict[str, Any]] = {}

    with store.transaction():
        for mode in CONTRACT_MODES:
            decision = adjudicate(case, notice, corrections, mode)
            decision_hash = _decision_hash(decision)
            existing = store.connection.execute(
                "SELECT decision_hash, decision_json, status FROM parity_certifications "
                "WHERE case_key=? AND contract_mode=?", (key, mode)
            ).fetchone()
            if existing is not None:
                if existing["decision_hash"] == decision_hash:
                    results[mode] = json.loads(existing["decision_json"])
                    continue
                if existing["status"] in ("certified", "rejected"):
                    raise ClarificationConflict(
                        f"parity decision for {key}/{mode} changed after issuance"
                    )
                # Provisional outcomes (pending/unverifiable) track new evidence.
                store.connection.execute(
                    "UPDATE parity_certifications SET status=?, reason=?, "
                    "decision_hash=?, decision_json=?, evidence_hash=? "
                    "WHERE case_key=? AND contract_mode=?",
                    (
                        decision["status"], decision["reason"], decision_hash,
                        json.dumps(decision, ensure_ascii=False, sort_keys=True),
                        evidence_hash, key, mode,
                    ),
                )
                results[mode] = decision
                continue
            store.connection.execute(
                "INSERT INTO parity_certifications(case_key, contract_mode, status, reason, "
                "decision_hash, decision_json, evidence_hash, created_run, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key, mode, decision["status"], decision["reason"], decision_hash,
                    json.dumps(decision, ensure_ascii=False, sort_keys=True),
                    evidence_hash, run_id, now,
                ),
            )
            results[mode] = decision
    return results


def intake_clarification(store: Store, case: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Full offline intake: evidence chain, adjudication, audit, persistence."""
    key = case_key(case)
    fingerprint = case_fingerprint(case)
    ensure_run(store, run_id, fingerprint)
    recorded = record_case(store, case, run_id)
    decisions = certify_case(store, case, run_id)
    with store.transaction():
        for mode, decision in decisions.items():
            store.audit(
                run_id,
                "CLARIFICATION_PARITY",
                f"{key}#{mode}",
                f"{decision['status']}:{decision['reason']}",
            )
        store.audit(
            run_id,
            "CLARIFICATION_INTAKE",
            key,
            f"{recorded['outcome']}:{fingerprint[:12]}",
        )
    return {
        "case_key": key,
        "outcome": recorded["outcome"],
        "case_hash": fingerprint,
        "decisions": decisions,
    }
