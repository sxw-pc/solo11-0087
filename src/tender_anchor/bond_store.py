"""Persistence for cross-bid security cases, pledges and atomic actions."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Sequence

from .bond_coverage import CoverageBid, InstrumentView
from .normalize import now_iso, stable_hash
from .store import Store


def canonical_case_id(case: dict[str, Any]) -> str:
    bid_ids = sorted(item["bid_id"] for item in case["bids"])
    return "CASE-" + "-".join(bid_id.replace("BID-", "") for bid_id in bid_ids)


def case_payload_hash(case: dict[str, Any]) -> str:
    return stable_hash(case)


def action_payload_hash(action: dict[str, Any]) -> str:
    return stable_hash(action)


class BondStore:
    """Idempotent, conflict-detecting ledger over the bond tables."""

    def __init__(self, store: Store) -> None:
        self.store = store

    @property
    def connection(self):
        return self.store.connection

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM bond_cases WHERE case_id=?", (case_id,)
        ).fetchone()
        return dict(row) if row else None

    def require_case(self, case_id: str) -> dict[str, Any]:
        row = self.get_case(case_id)
        if row is None:
            raise ValueError(f"unknown bond case: {case_id}")
        return row

    def check_case_payload(self, case_id: str, payload_hash: str) -> dict[str, Any] | None:
        existing = self.get_case(case_id)
        if existing is not None and existing["payload_hash"] != payload_hash:
            raise ValueError("bond case id was used with different evidence")
        return existing

    def upsert_case(self, case: dict[str, Any], contract_mode: str) -> str:
        case_id = canonical_case_id(case)
        payload_hash = case_payload_hash(case)
        now = now_iso()
        existing = self.get_case(case_id)
        if existing is None:
            self.connection.execute(
                "INSERT INTO bond_cases(case_id, payload_hash, payload_json, "
                "contract_mode, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (case_id, payload_hash, json.dumps(case, ensure_ascii=False, sort_keys=True),
                 contract_mode, now, now),
            )
            return "inserted"
        if existing["payload_hash"] != payload_hash:
            raise ValueError("bond case id was used with different evidence")
        self.connection.execute(
            "UPDATE bond_cases SET updated_at=? WHERE case_id=?", (now, case_id)
        )
        return "unchanged"

    def replace_bids(self, case_id: str, rows: Sequence[dict[str, Any]]) -> None:
        now = now_iso()
        self.connection.execute("DELETE FROM bond_bids WHERE case_id=?", (case_id,))
        for row in rows:
            self.connection.execute(
                "INSERT INTO bond_bids(case_id, bid_id, notice_id, bidder_id, "
                "required_security, payload_json, envelope_status, envelope_reason, "
                "security_status, decision_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (case_id, row["bid_id"], row["notice_id"], row["bidder_id"],
                 row["required_security"], json.dumps(row["payload"], ensure_ascii=False,
                                                      sort_keys=True),
                 row["envelope_status"], row["envelope_reason"], row["security_status"],
                 json.dumps(row["decision"], ensure_ascii=False, sort_keys=True), now),
            )

    def replace_instruments(
        self, case_id: str, bids: Sequence[CoverageBid],
        views: dict[str, InstrumentView], case: dict[str, Any],
    ) -> None:
        now = now_iso()
        self.connection.execute(
            "DELETE FROM bond_instruments WHERE case_id=?", (case_id,)
        )
        deadline_by_notice = {
            bid.notice_id: bid.effective_deadline
            for bid in bids if bid.effective_deadline
        }
        for raw in case["instruments"]:
            view = views[raw["instrument_id"]]
            deadlines = sorted(
                value for value in deadline_by_notice.values() if value
            )
            effective = max(deadlines) if deadlines else None
            self.connection.execute(
                "INSERT INTO bond_instruments(case_id, instrument_id, bidder_id, kind, "
                "face_amount, valid_until, payload_json, evidence_status, "
                "effective_deadline_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (case_id, view.instrument_id, view.bidder_id, view.kind,
                 str(view.face_amount), view.valid_until,
                 json.dumps(raw, ensure_ascii=False, sort_keys=True),
                 view.evidence_status, effective, now),
            )

    def ledger(self, case_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM bond_pledge_ledger WHERE case_id=? ORDER BY ledger_id",
            (case_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def posted_pledges(self, case_id: str) -> dict[tuple[str, str], Decimal]:
        result: dict[tuple[str, str], Decimal] = {}
        for row in self.ledger(case_id):
            if row["event_type"] != "pledge":
                continue
            key = (row["bid_id"], row["instrument_id"])
            result[key] = result.get(key, Decimal("0")) + Decimal(row["amount"])
        return result

    def net_pledges(self, case_id: str) -> dict[tuple[str, str], Decimal]:
        """Current cover per (bid, instrument) after applying releases."""
        result: dict[tuple[str, str], Decimal] = {}
        for row in self.ledger(case_id):
            key = (row["bid_id"], row["instrument_id"])
            sign = 1 if row["event_type"] == "pledge" else -1
            result[key] = result.get(key, Decimal("0")) + sign * Decimal(row["amount"])
        return {key: value for key, value in result.items() if value > 0}

    def instrument_reserved(self, case_id: str) -> dict[str, Decimal]:
        result: dict[str, Decimal] = {}
        for row in self.ledger(case_id):
            sign = 1 if row["event_type"] == "pledge" else -1
            result[row["instrument_id"]] = (
                result.get(row["instrument_id"], Decimal("0"))
                + sign * Decimal(row["amount"])
            )
        return {key: value for key, value in result.items() if value > 0}

    def append_event(
        self, case_id: str, action_id: str, seq: int, event_type: str,
        *, pledge_id: str | None, bid_id: str, instrument_id: str,
        amount: Decimal, source_action_id: str | None,
    ) -> None:
        self.connection.execute(
            "INSERT INTO bond_pledge_ledger(case_id, action_id, seq, event_type, "
            "pledge_id, bid_id, instrument_id, amount, source_action_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (case_id, action_id, seq, event_type, pledge_id, bid_id, instrument_id,
             str(amount), source_action_id, now_iso()),
        )

    def record_action(
        self, case_id: str, action: dict[str, Any], result: dict[str, Any]
    ) -> None:
        self.connection.execute(
            "INSERT INTO bond_events(case_id, action_id, action_type, contract_mode, "
            "payload_hash, payload_json, result_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (case_id, action["action_id"], action["action_type"],
             action["contract_mode"], action_payload_hash(action),
             json.dumps(action, ensure_ascii=False, sort_keys=True),
             json.dumps(result, ensure_ascii=False, sort_keys=True), now_iso()),
        )

    def get_action(self, case_id: str, action_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM bond_events WHERE case_id=? AND action_id=?",
            (case_id, action_id),
        ).fetchone()
        return dict(row) if row else None

    def check_action_payload(
        self, case_id: str, action: dict[str, Any]
    ) -> dict[str, Any] | None:
        existing = self.get_action(case_id, action["action_id"])
        if existing is not None and existing["payload_hash"] != action_payload_hash(action):
            raise ValueError("bond action id was used with different terms")
        return existing

    def list_bid_decisions(self, case_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM bond_bids WHERE case_id=? ORDER BY bid_id", (case_id,)
        ).fetchall()
        return [json.loads(row["decision_json"]) for row in rows]

    def list_instrument_rows(self, case_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM bond_instruments WHERE case_id=? ORDER BY instrument_id",
            (case_id,),
        ).fetchall()
        return [dict(row) for row in rows]
