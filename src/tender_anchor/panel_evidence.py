"""Idempotent evidence persistence: cases, reviewers, bids, ballots, disclosures.

Every evidence row stores a content hash.  Re-importing the same identity with
the same hash is a no-op; re-importing it with a different hash raises a stable
conflict.  The case's notice, round, locked scoring rule, and initial review
time are immutable once first seen.
"""

from __future__ import annotations

import json
from typing import Any

from .errors import PanelError
from .normalize import now_iso, stable_hash
from .panel_case import record_hash
from .store import Store


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def conflict(case_id: str, label: str, identity: str) -> None:
    raise PanelError(
        "PANEL_EVIDENCE_CONFLICT",
        f"{label} {identity} was re-imported with different content",
        case_id=case_id,
        record=identity,
    )


def _upsert(
    store: Store,
    *,
    table: str,
    identity_columns: tuple[str, ...],
    identity: tuple[Any, ...],
    digest: str,
    insert: tuple[Any, ...],
    case_id: str,
    label: str,
) -> bool:
    where = " AND ".join(f"{column}=?" for column in identity_columns)
    row = store.connection.execute(
        f"SELECT payload_hash FROM {table} WHERE {where}", identity
    ).fetchone()
    if row is not None:
        if row["payload_hash"] != digest:
            conflict(case_id, label, str(identity[-1]))
        return False
    store.connection.execute(
        f"INSERT INTO {table} VALUES ({', '.join('?' for _ in insert)})", insert
    )
    return True


def _store_case(store: Store, case: dict[str, Any]) -> None:
    scoring = _json(case["scoring"])
    row = store.connection.execute(
        "SELECT notice_id, round_id, scoring_json, initial_review_at FROM panel_cases "
        "WHERE case_id=?", (case["case_id"],),
    ).fetchone()
    now = now_iso()
    if row is None:
        store.connection.execute(
            "INSERT INTO panel_cases(case_id, notice_id, round_id, scoring_json, "
            "initial_review_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (case["case_id"], case["notice_id"], case["round_id"], scoring,
             case["initial_review_at"], now, now),
        )
    elif (row["notice_id"], row["round_id"], row["scoring_json"],
          row["initial_review_at"]) != (
              case["notice_id"], case["round_id"], scoring, case["initial_review_at"]):
        raise PanelError(
            "PANEL_SCOPE_MISMATCH",
            f"case {case['case_id']} was imported with another round, notice, or rule",
        )


def _store_reviewers(store: Store, case: dict[str, Any]) -> None:
    case_id = case["case_id"]
    for reviewer in case["reviewers"]:
        row = store.connection.execute(
            "SELECT role, available FROM panel_reviewers WHERE case_id=? AND reviewer_id=?",
            (case_id, reviewer["reviewer_id"]),
        ).fetchone()
        if row is None:
            store.connection.execute(
                "INSERT INTO panel_reviewers VALUES (?, ?, ?, ?)",
                (case_id, reviewer["reviewer_id"], reviewer["role"],
                 int(reviewer["available"])),
            )
        elif (row["role"], bool(row["available"])) != (reviewer["role"], reviewer["available"]):
            conflict(case_id, "reviewer", reviewer["reviewer_id"])


def store_evidence(store: Store, case: dict[str, Any]) -> str:
    """Insert-or-confirm all evidence; return a stable evidence hash."""
    case_id = case["case_id"]
    _store_case(store, case)
    _store_reviewers(store, case)
    for bid in case["bids"]:
        digest = record_hash(bid)
        _upsert(
            store, table="panel_bids",
            identity_columns=("case_id", "bid_id"), identity=(case_id, bid["bid_id"]),
            digest=digest,
            insert=(case_id, bid["bid_id"], bid["notice_id"], bid["bidder_id"],
                    bid["received_at"], bid["amount"], digest),
            case_id=case_id, label="bid",
        )
    for ballot in case["ballots"]:
        digest = record_hash({key: ballot[key] for key in
                              ("ballot_id", "reviewer_id", "bid_id", "score", "at")})
        _upsert(
            store, table="panel_ballots",
            identity_columns=("case_id", "ballot_id"), identity=(case_id, ballot["ballot_id"]),
            digest=digest,
            insert=(case_id, ballot["ballot_id"], ballot["reviewer_id"], ballot["bid_id"],
                    ballot["score"], ballot["at"], digest),
            case_id=case_id, label="ballot",
        )
    for disclosure in case["disclosures"]:
        digest = record_hash(disclosure)
        _upsert(
            store, table="panel_disclosures",
            identity_columns=("case_id", "disclosure_id"),
            identity=(case_id, disclosure["disclosure_id"]),
            digest=digest,
            insert=(case_id, disclosure["disclosure_id"], disclosure["reviewer_id"],
                    disclosure["bid_id"], disclosure["kind"], disclosure["at"], digest),
            case_id=case_id, label="disclosure",
        )
    for contract in case["contracts"]:
        digest = record_hash(contract)
        _upsert(
            store, table="panel_contracts",
            identity_columns=("case_id", "contract_id"),
            identity=(case_id, contract["contract_id"]),
            digest=digest,
            insert=(case_id, contract["contract_id"], _json(contract), digest),
            case_id=case_id, label="contract",
        )
    evidence = {
        "reviewers": case["reviewers"],
        "bids": sorted(bid["bid_id"] for bid in case["bids"]),
        "ballots": sorted(ballot["ballot_id"] for ballot in case["ballots"]),
        "disclosures": sorted(item["disclosure_id"] for item in case["disclosures"]),
        "contracts": sorted(item["contract_id"] for item in case["contracts"]),
    }
    digest = stable_hash(evidence)
    store.connection.execute(
        "UPDATE panel_cases SET evidence_hash=?, updated_at=? WHERE case_id=?",
        (digest, now_iso(), case_id),
    )
    return digest
