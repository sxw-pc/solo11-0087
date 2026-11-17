"""Read-only query mixin for :mod:`tender_anchor.store`."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .errors import StoreError


class StoreQueriesMixin:
    connection: sqlite3.Connection

    def count(self, table: str) -> int:
        allowed = {
            "notices", "attachments", "corrections", "notice_revisions",
            "raw_snapshots", "review_items", "runs", "field_sources", "audit_events",
            "clarification_cases", "clarification_answers", "clarification_access",
            "parity_certifications", "bid_submissions",
        }
        if table not in allowed:
            raise StoreError("STORE_QUERY", f"不允许的统计表：{table}")
        return int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def attachment_counts(self) -> tuple[int, int]:
        rows = self.connection.execute(
            "SELECT status, COUNT(*) AS n FROM attachments GROUP BY status"
        ).fetchall()
        values = {row["status"]: int(row["n"]) for row in rows}
        return values.get("stored", 0), values.get("failed", 0)

    def review_count(self, status: str = "open") -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) FROM review_items WHERE status=?", (status,)
        ).fetchone()
        return int(row[0])

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM runs ORDER BY started_at DESC, run_id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    def latest_run(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM runs ORDER BY started_at DESC, run_id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def list_notices(
        self, kind: str | None = None, query: str | None = None
    ) -> list[dict[str, Any]]:
        clauses, args = [], []
        if kind:
            clauses.append("kind=?")
            args.append(kind)
        if query:
            clauses.append("(title LIKE ? OR notice_id LIKE ? OR project_code LIKE ?)")
            token = f"%{query}%"
            args.extend([token, token, token])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            f"SELECT * FROM notices{where} ORDER BY notice_id", args
        ).fetchall()
        return [dict(row) for row in rows]

    def get_notice(self, notice_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM notices WHERE notice_id=?", (notice_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_attachments(self, notice_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM attachments WHERE notice_id=? ORDER BY filename, source_url",
            (notice_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_field_sources(self, notice_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM field_sources WHERE notice_id=? ORDER BY field_name",
            (notice_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_corrections(self, notice_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM corrections WHERE correction_notice_id=? OR original_notice_id=? "
            "ORDER BY correction_notice_id", (notice_id, notice_id),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["changes"] = json.loads(item.pop("changes_json"))
            result.append(item)
        return result

    def get_review_items(
        self, *, status: str | None = None, notice_id: str | None = None
    ) -> list[dict[str, Any]]:
        clauses, args = [], []
        if status:
            clauses.append("r.status=?")
            args.append(status)
        if notice_id:
            clauses.append("r.notice_id=?")
            args.append(notice_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(
            "SELECT r.*, n.title, n.kind_label, n.region FROM review_items r "
            f"LEFT JOIN notices n ON n.notice_id=r.notice_id{where} "
            "ORDER BY r.notice_id, r.rule_code", args,
        ).fetchall()
        return [dict(row) for row in rows]

    def get_audit_events(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM audit_events ORDER BY event_id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    def get_snapshots(self, notice_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM raw_snapshots WHERE notice_id=? ORDER BY sha256", (notice_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def get_bundle(self, notice_id: str) -> dict[str, Any] | None:
        notice = self.get_notice(notice_id)
        if notice is None:
            return None
        return {
            "notice": notice,
            "attachments": self.get_attachments(notice_id),
            "corrections": self.get_corrections(notice_id),
            "field_sources": self.get_field_sources(notice_id),
            "review_items": self.get_review_items(notice_id=notice_id),
            "snapshots": self.get_snapshots(notice_id),
        }

    def list_clarification_cases(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM clarification_cases ORDER BY case_key"
        ).fetchall()
        return [dict(row) for row in rows]

    def get_clarification_case(self, case_key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM clarification_cases WHERE case_key=?", (case_key,)
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["participants"] = json.loads(item.pop("participants_json"))
        item["payload"] = json.loads(item.pop("payload_json"))
        item["answers"] = [
            dict(answer) for answer in self.connection.execute(
                "SELECT answer_id, visibility, recipient, term_id, published_at, "
                "redacted_questioner, content_sha256, content FROM clarification_answers "
                "WHERE case_key=? ORDER BY published_at, answer_id", (case_key,)
            ).fetchall()
        ]
        item["access_events"] = [
            dict(event) for event in self.connection.execute(
                "SELECT event_id, answer_id, bidder_id, accessed_at, content_sha256 "
                "FROM clarification_access WHERE case_key=? ORDER BY accessed_at, event_id",
                (case_key,),
            ).fetchall()
        ]
        return item

    def get_parity_certifications(self, case_key: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM parity_certifications WHERE case_key=? "
            "ORDER BY contract_mode", (case_key,)
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["decision"] = json.loads(item.pop("decision_json"))
            result.append(item)
        return result

    def get_clarification_bundle(self, case_key: str) -> dict[str, Any] | None:
        case = self.get_clarification_case(case_key)
        if case is None:
            return None
        case["certifications"] = self.get_parity_certifications(case_key)
        return case

    def stats(self) -> dict[str, Any]:
        kinds = {
            row["kind"]: int(row["n"])
            for row in self.connection.execute(
                "SELECT kind, COUNT(*) AS n FROM notices GROUP BY kind ORDER BY kind"
            ).fetchall()
        }
        stored, failed = self.attachment_counts()
        return {
            "notices": self.count("notices"),
            "kinds": kinds,
            "attachments_stored": stored,
            "attachments_failed": failed,
            "corrections": self.count("corrections"),
            "open_reviews": self.review_count("open"),
            "runs": self.count("runs"),
            "latest_run": self.latest_run(),
        }


class ReadOnlyStore(StoreQueriesMixin):
    """Read-only query facade used by the local review console."""

    def __init__(self, db_path: Path | str) -> None:
        path = Path(db_path).expanduser().resolve()
        if not path.is_file():
            raise StoreError("STORE_QUERY", f"数据库不存在：{path}")
        self.connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA query_only = ON")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ReadOnlyStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
