"""Persistence-facing records shared by the pipeline, rules, and stores."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NoticeRecord:
    notice_id: str
    kind: str
    kind_label: str
    title: str
    project_code: str
    region: str
    purchaser: str
    agency: str
    publish_at: str | None
    deadline_at: str | None
    budget_amount: str | None
    budget_currency: str
    award_amount: str | None
    winner: str
    related_notice_id: str
    content_excerpt: str
    source_url: str
    source_hash: str


@dataclass(frozen=True)
class FieldSourceRecord:
    field_name: str
    raw_value: str
    normalized_value: str
    extractor: str
    selector: str


@dataclass(frozen=True)
class AttachmentRecord:
    notice_id: str
    filename: str
    source_url: str
    sha256: str | None
    size_bytes: int
    stored_path: str | None
    content_type: str
    status: str
    error_code: str | None


@dataclass(frozen=True)
class CorrectionRecord:
    correction_notice_id: str
    original_notice_id: str
    published_at: str | None
    changes: tuple[dict[str, str], ...]
    source_url: str


@dataclass(frozen=True)
class AttributionEntry:
    """One row of the local explicit attribution list (config-level evidence)."""

    result_notice_id: str
    tender_notice_id: str


@dataclass(frozen=True)
class AttributionList:
    entries: tuple[AttributionEntry, ...]
    source_path: str


@dataclass(frozen=True)
class RoundCandidateRecord:
    """Per (result, tender) eligibility evidence used by the inference path."""

    result_notice_id: str
    tender_notice_id: str
    same_project_code: bool
    same_purchaser: bool
    publish_order_ok: bool
    corrections_ok: bool
    eligible: bool


@dataclass(frozen=True)
class AttributionRecord:
    """Attribution conclusion for one result notice, with evidence level."""

    result_notice_id: str
    declared_notice_id: str
    listed_notice_id: str
    inferred_notice_id: str
    chosen_notice_id: str
    evidence_level: str
    status: str
    reason_code: str
    strict_status: str
    auxiliary_status: str
    detail: str


@dataclass
class CrawlSummary:
    run_id: str
    status: str
    started_at: str
    finished_at: str = ""
    pages_fetched: int = 0
    notices_seen: int = 0
    notices_inserted: int = 0
    notices_updated: int = 0
    notices_unchanged: int = 0
    attachments_ok: int = 0
    attachments_failed: int = 0
    review_open: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    exports: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "pages_fetched": self.pages_fetched,
            "notices_seen": self.notices_seen,
            "notices_inserted": self.notices_inserted,
            "notices_updated": self.notices_updated,
            "notices_unchanged": self.notices_unchanged,
            "attachments_ok": self.attachments_ok,
            "attachments_failed": self.attachments_failed,
            "review_open": self.review_open,
            "errors": self.errors,
            "exports": self.exports,
        }
