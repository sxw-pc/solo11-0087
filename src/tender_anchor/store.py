"""SQLite persistence with provenance, idempotency, and review state."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from .app_paths import ensure_dir
from .errors import StoreError
from .models import (
    AttachmentRecord, AttributionRecord, CorrectionRecord, FieldSourceRecord,
    NoticeRecord, RoundCandidateRecord,
)
from .normalize import now_iso
from .schema import SCHEMA_SQL
from .store_queries import StoreQueriesMixin


class Store(StoreQueriesMixin):
    """A thin, deterministic SQLite access layer."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        ensure_dir(self.db_path.parent)
        try:
            self.connection = sqlite3.connect(str(self.db_path))
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.executescript(SCHEMA_SQL)
            self.connection.commit()
        except sqlite3.Error as exc:
            raise StoreError("STORE_INIT", f"数据库初始化失败：{exc}") from exc

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            yield
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def start_run(self, run_id: str, portal_base: str, config_hash: str) -> None:
        self.connection.execute(
            "INSERT INTO runs(run_id, started_at, status, portal_base, config_hash) "
            "VALUES (?, ?, 'running', ?, ?)",
            (run_id, now_iso(), portal_base, config_hash),
        )

    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        stats: dict[str, int],
        errors: Sequence[dict[str, Any]],
    ) -> None:
        self.connection.execute(
            "UPDATE runs SET finished_at=?, status=?, pages_fetched=?, notices_seen=?, "
            "notices_inserted=?, notices_updated=?, notices_unchanged=?, "
            "attachments_ok=?, attachments_failed=?, review_open=?, errors_json=? "
            "WHERE run_id=?",
            (
                now_iso(),
                status,
                stats.get("pages_fetched", 0),
                stats.get("notices_seen", 0),
                stats.get("notices_inserted", 0),
                stats.get("notices_updated", 0),
                stats.get("notices_unchanged", 0),
                stats.get("attachments_ok", 0),
                stats.get("attachments_failed", 0),
                stats.get("review_open", 0),
                json.dumps(list(errors), ensure_ascii=False, sort_keys=True),
                run_id,
            ),
        )

    def audit(self, run_id: str, event_type: str, target: str, detail: str) -> None:
        self.connection.execute(
            "INSERT INTO audit_events(run_id, event_type, target, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, event_type, target[:500], detail[:500], now_iso()),
        )

    def upsert_notice(
        self,
        notice: NoticeRecord,
        run_id: str,
        payload: dict[str, Any],
        sources: Sequence[FieldSourceRecord],
    ) -> str:
        now = now_iso()
        row = self.connection.execute(
            "SELECT source_hash, revision, first_seen_run FROM notices WHERE notice_id=?",
            (notice.notice_id,),
        ).fetchone()
        values = (
            notice.kind, notice.kind_label, notice.title, notice.project_code,
            notice.region, notice.purchaser, notice.agency, notice.publish_at,
            notice.deadline_at, notice.budget_amount, notice.budget_currency,
            notice.award_amount, notice.winner, notice.related_notice_id,
            notice.content_excerpt, notice.source_url, notice.source_hash,
        )
        if row is None:
            self.connection.execute(
                "INSERT INTO notices(notice_id, kind, kind_label, title, project_code, "
                "region, purchaser, agency, publish_at, deadline_at, budget_amount, "
                "budget_currency, award_amount, winner, related_notice_id, content_excerpt, "
                "source_url, source_hash, first_seen_run, last_seen_run, revision, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (notice.notice_id, *values, run_id, run_id, now),
            )
            outcome = "inserted"
        elif row["source_hash"] == notice.source_hash:
            self.connection.execute(
                "UPDATE notices SET last_seen_run=?, updated_at=? WHERE notice_id=?",
                (run_id, now, notice.notice_id),
            )
            outcome = "unchanged"
        else:
            self.connection.execute(
                "UPDATE notices SET kind=?, kind_label=?, title=?, project_code=?, region=?, "
                "purchaser=?, agency=?, publish_at=?, deadline_at=?, budget_amount=?, "
                "budget_currency=?, award_amount=?, winner=?, related_notice_id=?, "
                "content_excerpt=?, source_url=?, source_hash=?, last_seen_run=?, "
                "revision=revision+1, updated_at=? WHERE notice_id=?",
                (*values, run_id, now, notice.notice_id),
            )
            outcome = "updated"
        if outcome != "unchanged":
            self.connection.execute(
                "INSERT OR IGNORE INTO notice_revisions(notice_id, run_id, source_hash, "
                "payload_json, fetched_at) VALUES (?, ?, ?, ?, ?)",
                (notice.notice_id, run_id, notice.source_hash,
                 json.dumps(payload, ensure_ascii=False, sort_keys=True), now),
            )
            self._replace_sources(notice.notice_id, sources, now)
        return outcome

    def _replace_sources(
        self, notice_id: str, sources: Sequence[FieldSourceRecord], now: str
    ) -> None:
        names = [item.field_name for item in sources]
        if names:
            placeholders = ",".join("?" for _ in names)
            self.connection.execute(
                f"DELETE FROM field_sources WHERE notice_id=? AND field_name NOT IN ({placeholders})",
                (notice_id, *names),
            )
        else:
            self.connection.execute("DELETE FROM field_sources WHERE notice_id=?", (notice_id,))
        for source in sources:
            self.connection.execute(
                "INSERT INTO field_sources(notice_id, field_name, raw_value, normalized_value, "
                "extractor, selector, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(notice_id, field_name) DO UPDATE SET raw_value=excluded.raw_value, "
                "normalized_value=excluded.normalized_value, extractor=excluded.extractor, "
                "selector=excluded.selector, updated_at=excluded.updated_at",
                (notice_id, source.field_name, source.raw_value, source.normalized_value,
                 source.extractor, source.selector, now),
            )

    def archive_snapshot(
        self, notice_id: str, source_url: str, digest: str, stored_path: str, size: int
    ) -> bool:
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO raw_snapshots(notice_id, source_url, sha256, stored_path, "
            "byte_size, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
            (notice_id, source_url, digest, stored_path, size, now_iso()),
        )
        return cursor.rowcount > 0

    def upsert_attachment(self, attachment: AttachmentRecord, run_id: str) -> None:
        self.connection.execute(
            "INSERT INTO attachments(notice_id, filename, source_url, sha256, size_bytes, "
            "stored_path, content_type, status, error_code, fetched_at, updated_run) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(notice_id, source_url) DO UPDATE SET filename=excluded.filename, "
            "sha256=excluded.sha256, size_bytes=excluded.size_bytes, "
            "stored_path=excluded.stored_path, content_type=excluded.content_type, "
            "status=excluded.status, error_code=excluded.error_code, "
            "fetched_at=excluded.fetched_at, updated_run=excluded.updated_run",
            (attachment.notice_id, attachment.filename, attachment.source_url,
             attachment.sha256, attachment.size_bytes, attachment.stored_path,
             attachment.content_type, attachment.status, attachment.error_code,
             now_iso(), run_id),
        )

    def upsert_correction(self, correction: CorrectionRecord, run_id: str) -> None:
        self.connection.execute(
            "INSERT INTO corrections(correction_notice_id, original_notice_id, published_at, "
            "changes_json, source_url, first_seen_run, last_seen_run, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(correction_notice_id) DO UPDATE SET "
            "original_notice_id=excluded.original_notice_id, published_at=excluded.published_at, "
            "changes_json=excluded.changes_json, source_url=excluded.source_url, "
            "last_seen_run=excluded.last_seen_run, updated_at=excluded.updated_at",
            (correction.correction_notice_id, correction.original_notice_id,
             correction.published_at,
             json.dumps(list(correction.changes), ensure_ascii=False, sort_keys=True),
             correction.source_url, run_id, run_id, now_iso()),
        )

    def sync_attributions(
        self,
        candidates: Sequence[RoundCandidateRecord],
        attributions: Sequence[AttributionRecord],
        run_id: str,
    ) -> None:
        """Idempotently persist round candidates and attribution evidence."""
        now = now_iso()
        current = {(item.result_notice_id, item.tender_notice_id) for item in candidates}
        for result_id in sorted({item.result_notice_id for item in attributions}):
            rows = self.connection.execute(
                "SELECT tender_notice_id FROM round_candidates WHERE result_notice_id=?",
                (result_id,),
            ).fetchall()
            for row in rows:
                if (result_id, row["tender_notice_id"]) not in current:
                    self.connection.execute(
                        "DELETE FROM round_candidates WHERE result_notice_id=? "
                        "AND tender_notice_id=?", (result_id, row["tender_notice_id"]),
                    )
        for item in candidates:
            self.connection.execute(
                "INSERT INTO round_candidates(result_notice_id, tender_notice_id, "
                "same_project_code, same_purchaser, publish_order_ok, corrections_ok, "
                "eligible, first_seen_run, last_seen_run) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(result_notice_id, tender_notice_id) DO UPDATE SET "
                "same_project_code=excluded.same_project_code, "
                "same_purchaser=excluded.same_purchaser, "
                "publish_order_ok=excluded.publish_order_ok, "
                "corrections_ok=excluded.corrections_ok, eligible=excluded.eligible, "
                "last_seen_run=excluded.last_seen_run",
                (item.result_notice_id, item.tender_notice_id,
                 int(item.same_project_code), int(item.same_purchaser),
                 int(item.publish_order_ok), int(item.corrections_ok),
                 int(item.eligible), run_id, run_id),
            )
        for item in attributions:
            self.connection.execute(
                "INSERT INTO attributions(result_notice_id, declared_notice_id, "
                "listed_notice_id, inferred_notice_id, chosen_notice_id, evidence_level, "
                "status, reason_code, strict_status, auxiliary_status, detail, "
                "first_seen_run, last_seen_run, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(result_notice_id) DO UPDATE SET "
                "declared_notice_id=excluded.declared_notice_id, "
                "listed_notice_id=excluded.listed_notice_id, "
                "inferred_notice_id=excluded.inferred_notice_id, "
                "chosen_notice_id=excluded.chosen_notice_id, "
                "evidence_level=excluded.evidence_level, status=excluded.status, "
                "reason_code=excluded.reason_code, strict_status=excluded.strict_status, "
                "auxiliary_status=excluded.auxiliary_status, detail=excluded.detail, "
                "last_seen_run=excluded.last_seen_run, updated_at=excluded.updated_at",
                (item.result_notice_id, item.declared_notice_id, item.listed_notice_id,
                 item.inferred_notice_id, item.chosen_notice_id, item.evidence_level,
                 item.status, item.reason_code, item.strict_status,
                 item.auxiliary_status, item.detail, run_id, run_id, now),
            )

    def sync_review_items(self, items: Sequence[dict[str, str]], run_id: str) -> int:
        current = {(item["notice_id"], item["rule_code"]) for item in items}
        open_rows = self.connection.execute(
            "SELECT notice_id, rule_code FROM review_items WHERE status='open'"
        ).fetchall()
        for row in open_rows:
            if (row["notice_id"], row["rule_code"]) not in current:
                self.connection.execute(
                    "UPDATE review_items SET status='resolved', resolved_run=? "
                    "WHERE notice_id=? AND rule_code=?",
                    (run_id, row["notice_id"], row["rule_code"]),
                )
        now = now_iso()
        for item in sorted(items, key=lambda value: (value["notice_id"], value["rule_code"])):
            self.connection.execute(
                "INSERT INTO review_items(notice_id, rule_code, severity, message, field_name, "
                "status, created_run, last_seen_run) VALUES (?, ?, ?, ?, ?, 'open', ?, ?) "
                "ON CONFLICT(notice_id, rule_code) DO UPDATE SET severity=excluded.severity, "
                "message=excluded.message, field_name=excluded.field_name, status='open', "
                "last_seen_run=excluded.last_seen_run, resolved_run=NULL",
                (item["notice_id"], item["rule_code"], item["severity"], item["message"],
                 item.get("field_name", ""), run_id, now),
            )
        return len(current)
