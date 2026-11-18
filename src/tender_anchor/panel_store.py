"""Persistent, idempotent panel reviews, awards, corrections, and audit.

The signed first review is version 1 and is never overwritten.  A late
disclosure is stored as its own evidence row and can only yield a *new* review
version through an authorised contract.  Re-importing identical evidence
replays the stored reviews; re-using an identity with different content raises
a stable conflict.  A captured notice correction that changes eligibility is
recorded as a distinct ``notice_update`` version rather than rewriting v1.
"""

from __future__ import annotations

import json
from typing import Any

from .normalize import now_iso, stable_hash
from .panel_case import case_hash
from .panel_evidence import _json, store_evidence
from .panel_rules import evaluate_case
from .store import Store


def review_fingerprint(case: dict[str, Any], review: dict[str, Any]) -> str:
    """Content hash of one review, independent of surrounding case evidence."""
    return stable_hash(
        {
            "case": case_hash(case),
            "stage": review["stage"],
            "contract_id": review["contract_id"],
            "known": review["known_disclosure_ids"],
            "effective_at": review["effective_at"],
            "votes": [
                [item["ballot_id"], item["included"], item["exclusion_reason"]]
                for item in review["votes"]
            ],
            "bids": [
                [row["bid_id"], row["status"], row["mean_exact"],
                 row["included_ballot_ids"]]
                for row in review["bids"]
            ],
            "winner": review["winner_bid_id"],
            "issues": review["issues"],
        }
    )


def _existing_review(store: Store, case_id: str, fingerprint: str) -> Any:
    return store.connection.execute(
        "SELECT * FROM panel_reviews WHERE case_id=? AND fingerprint=?",
        (case_id, fingerprint),
    ).fetchone()


def _next_version(store: Store, case_id: str) -> int:
    row = store.connection.execute(
        "SELECT COALESCE(MAX(version), 0) FROM panel_reviews WHERE case_id=?", (case_id,)
    ).fetchone()
    return int(row[0]) + 1


def _store_review(
    store: Store,
    case: dict[str, Any],
    review: dict[str, Any],
    evidence_hash: str,
    *,
    stage: str | None = None,
) -> tuple[int, bool]:
    review = {**review, "stage": stage or review["stage"]}
    fingerprint = review_fingerprint(case, review)
    row = _existing_review(store, case["case_id"], fingerprint)
    if row is not None:
        return int(row["review_id"]), False
    version = _next_version(store, case["case_id"])
    summary = {**review, "evidence_hash": evidence_hash}
    cursor = store.connection.execute(
        "INSERT INTO panel_reviews(case_id, version, stage, contract_id, status, reason, "
        "fingerprint, winner_bid_id, winner_bidder_id, known_disclosure_ids_json, "
        "summary_json, created_at, effective_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (case["case_id"], version, review["stage"], review["contract_id"], review["status"],
         review["reason"], fingerprint, review["winner_bid_id"], review["winner_bidder_id"],
         _json(review["known_disclosure_ids"]), _json(summary), now_iso(),
         review["effective_at"]),
    )
    review_id = int(cursor.lastrowid)
    for vote in review["votes"]:
        store.connection.execute(
            "INSERT INTO panel_review_votes(review_id, bid_id, reviewer_id, ballot_id, "
            "score, included, exclusion_reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (review_id, vote["bid_id"], vote["reviewer_id"], vote["ballot_id"],
             vote["score"], int(vote["included"]), vote["exclusion_reason"]),
        )
    store.audit(
        f"panel:{case['case_id']}", f"PANEL_{review['stage'].upper()}_RECORDED",
        f"{case['case_id']}#v{version}",
        f"status={review['status']} winner={review['winner_bidder_id']} "
        f"contract={review['contract_id'] or 'none'}",
    )
    return review_id, True


def _review_row(store: Store, review_id: int) -> Any:
    return store.connection.execute(
        "SELECT * FROM panel_reviews WHERE review_id=?", (review_id,)
    ).fetchone()


def _record_correction(
    store: Store, case: dict[str, Any], review: dict[str, Any], review_id: int,
    prior_id: int | None, prior_winner: str,
) -> None:
    """Emit one correction row when this review changes the *published* winner."""
    if review["winner_bidder_id"] == prior_winner:
        return
    if not review["winner_bidder_id"]:
        return
    row = store.connection.execute(
        "SELECT correction_id FROM panel_corrections WHERE case_id=? AND review_id=?",
        (case["case_id"], review_id),
    ).fetchone()
    if row is not None:
        return
    evidence = {
        "review_id": review_id,
        "stage": review["stage"],
        "contract_id": review["contract_id"],
        "known_disclosure_ids": review["known_disclosure_ids"],
        "late_disclosure_ids": review["late_disclosure_ids"],
        "missing_replacements": review["missing_replacements"],
        "issues": review["issues"],
    }
    store.connection.execute(
        "INSERT INTO panel_corrections(case_id, review_id, prior_review_id, "
        "from_winner_bidder_id, to_winner_bidder_id, reason, evidence_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (case["case_id"], review_id, prior_id, prior_winner, review["winner_bidder_id"],
         review["contract_id"], _json(evidence), now_iso()),
    )
    store.audit(
        f"panel:{case['case_id']}", "PANEL_AWARD_CORRECTED",
        f"{case['case_id']}#review-{review_id}",
        f"from={prior_winner} to={review['winner_bidder_id']} "
        f"contract={review['contract_id']}",
    )


def _store_award(store: Store, case: dict[str, Any], award: dict[str, Any],
                 review_id: int, version: int) -> None:
    store.connection.execute(
        "INSERT INTO panel_awards(case_id, notice_id, round_id, review_id, review_version, "
        "stage, contract_id, status, winner_bid_id, winner_bidder_id, winning_mean, "
        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(case_id) DO UPDATE SET review_id=excluded.review_id, "
        "review_version=excluded.review_version, stage=excluded.stage, "
        "contract_id=excluded.contract_id, status=excluded.status, "
        "winner_bid_id=excluded.winner_bid_id, winner_bidder_id=excluded.winner_bidder_id, "
        "winning_mean=excluded.winning_mean, updated_at=excluded.updated_at",
        (case["case_id"], case["notice_id"], case["round_id"], review_id, version,
         award["stage"], award["contract_id"], award["status"], award["winner_bid_id"],
         award["winner_bidder_id"], award["winning_mean"], now_iso()),
    )


def record_panel_case(store: Store, case: dict[str, Any], notice: dict[str, Any]) -> dict[str, Any]:
    """Validate, persist, and evaluate a case atomically; replay if unchanged."""
    evaluation = evaluate_case(case, notice)
    with store.transaction():
        evidence_hash = store_evidence(store, case)
        initial_prior = store.connection.execute(
            "SELECT review_id, winner_bidder_id, fingerprint FROM panel_reviews "
            "WHERE case_id=? AND stage='initial' ORDER BY version LIMIT 1",
            (case["case_id"],),
        ).fetchone()
        published = store.connection.execute(
            "SELECT review_id, winner_bidder_id FROM panel_awards WHERE case_id=?",
            (case["case_id"],),
        ).fetchone()
        notice_changed = (
            initial_prior is not None
            and initial_prior["fingerprint"] != review_fingerprint(case, evaluation["initial"])
        )
        recorded: list[dict[str, Any]] = []
        for review in evaluation["reviews"]:
            stage = "notice_update" if (
                review["stage"] == "initial" and notice_changed
            ) else review["stage"]
            review_id, created = _store_review(
                store, case, review, evidence_hash, stage=stage
            )
            row = _review_row(store, review_id)
            recorded.append(
                {
                    "review_id": int(review_id),
                    "version": int(row["version"]),
                    "created": created,
                    "review": {**review, "stage": stage},
                }
            )
        # Corrections compare against whatever award was last published; on a
        # first import that baseline is the signed first review.  Only the one
        # review that actually determines this import's award can correct it.
        baseline_winner = (
            published["winner_bidder_id"] if published is not None
            else recorded[0]["review"]["winner_bidder_id"]
        )
        prior_id = (
            int(published["review_id"]) if published is not None
            else recorded[0]["review_id"]
        )
        governing = next(
            (item for item in recorded if item["review"]["is_governing"]), recorded[0]
        )
        notice_review = next(
            (item for item in recorded if item["review"]["stage"] == "notice_update"),
            None,
        )
        if notice_review is not None and evaluation["final_award"]["stage"] == "pending":
            decider = governing
        elif governing["review"]["is_governing"]:
            decider = governing
        elif notice_review is not None:
            decider = notice_review
        else:
            decider = governing
        _record_correction(
            store, case, decider["review"], decider["review_id"],
            prior_id, baseline_winner,
        )
        _store_award(
            store, case, evaluation["final_award"],
            governing["review_id"], governing["version"],
        )
    return {**evaluation, "evidence_hash": evidence_hash, "recorded_reviews": recorded}


def load_panel_reviews(store: Store, case_id: str) -> list[dict[str, Any]]:
    rows = store.connection.execute(
        "SELECT * FROM panel_reviews WHERE case_id=? ORDER BY version", (case_id,)
    ).fetchall()
    return [{**dict(row), "summary": json.loads(row["summary_json"])} for row in rows]
