"""Crawl helpers shared by the pipeline orchestrator."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlencode

from .app_paths import DataPaths, ensure_within, safe_filename, write_bytes_if_absent
from .attachments import AttachmentStore
from .config import AppConfig
from .errors import AppError, PipelineError
from .extract import ExtractedNotice, ListEntry, extract_notice, parse_list_page
from .fetch import SafeFetcher
from .models import (
    AttachmentRecord, CorrectionRecord, CrawlSummary, FieldSourceRecord, NoticeRecord,
)
from .normalize import (
    clean_text, format_amount, parse_amount, parse_deadline_at, parse_publish_at,
)
from .store import Store

SOURCE_FIELD_MAP = {
    "notice_id": "notice_id",
    "title": "title",
    "kind_label": "kind_label",
    "project_code": "project_code",
    "region": "region",
    "purchaser": "purchaser",
    "agency": "agency",
    "publish_raw": "publish_at",
    "deadline_raw": "deadline_at",
    "budget_raw": "budget_amount",
    "award_amount_raw": "award_amount",
    "winner": "winner",
    "related_notice_id": "related_notice_id",
    "content_excerpt": "content_excerpt",
}
NORMALIZED_FIELDS = {"publish_at", "deadline_at", "budget_amount", "award_amount"}


def add_error(errors: list[dict[str, str]], code: str, message: str, target: str) -> None:
    errors.append({"code": code, "message": message, "target": target})


def archive_bytes(paths: DataPaths, relative: Path, content: bytes) -> str:
    destination = ensure_within(paths.root, paths.root / relative, label="raw archive")
    write_bytes_if_absent(destination, content)
    return relative.as_posix()


def list_url(config: AppConfig, page: int) -> str:
    query = urlencode({"page": page, "page_size": config.portal.page_size})
    return f"{config.portal.base_url}{config.portal.list_path}?{query}"


def crawl_index(
    config: AppConfig,
    fetcher: SafeFetcher,
    paths: DataPaths,
    store: Store,
    run_id: str,
    summary: CrawlSummary,
) -> list[ListEntry]:
    entries: dict[str, ListEntry] = {}
    page_url = list_url(config, 1)
    seen_urls: set[str] = set()
    for page in range(1, config.portal.max_pages + 1):
        if page_url in seen_urls:
            raise PipelineError("PIPELINE_FATAL", f"分页出现循环：{page_url}")
        seen_urls.add(page_url)
        response = fetcher.fetch(page_url)
        raw_path = archive_bytes(
            paths,
            Path("raw") / "list" / f"page-{page:03d}-{response.sha256[:12]}.html",
            response.content,
        )
        store.audit(run_id, "LIST_ARCHIVED", page_url, raw_path)
        parsed = parse_list_page(response.text, page_url, config.selectors)
        summary.pages_fetched += 1
        for item in parsed.errors:
            add_error(
                summary.errors,
                item.get("code", "PARSE_LIST_ITEM"),
                item.get("message", ""),
                item.get("url", page_url),
            )
        for entry in parsed.entries:
            entries.setdefault(entry.notice_id, entry)
        if not parsed.next_url:
            break
        if page == config.portal.max_pages:
            raise PipelineError(
                "PIPELINE_FATAL",
                f"达到最大页数 {config.portal.max_pages}，仍有下一页",
            )
        page_url = parsed.next_url
    if not entries:
        raise PipelineError("PIPELINE_NO_NOTICES", "列表页未产生任何公告")
    return [entries[key] for key in sorted(entries)]


def safe_value(
    field_name: str,
    raw: str,
    parser,
    errors: list[dict[str, str]],
    target: str,
) -> str | None:
    if not clean_text(raw):
        return None
    try:
        return parser(raw)
    except AppError as exc:
        add_error(errors, exc.code, exc.message, f"{target}#{field_name}")
        return None


def build_sources(
    extracted: ExtractedNotice, normalized: dict[str, str | None]
) -> list[FieldSourceRecord]:
    result = []
    for source_key, source in sorted(extracted.sources.items()):
        field_name = SOURCE_FIELD_MAP.get(source_key, source_key)
        normalized_value = normalized.get(field_name)
        if field_name not in NORMALIZED_FIELDS:
            normalized_value = source.raw_value
        result.append(
            FieldSourceRecord(
                field_name=field_name,
                raw_value=source.raw_value,
                normalized_value=normalized_value or "",
                extractor=source.extractor,
                selector=source.selector,
            )
        )
    return result


def normalize_correction_changes(
    extracted: ExtractedNotice, errors: list[dict[str, str]]
) -> tuple[dict[str, str], ...]:
    changes = []
    for change in extracted.correction_changes:
        value = change.new_value
        if change.field == "budget_amount":
            parsed = safe_value(
                change.field, value, parse_amount, errors, extracted.notice_id
            )
            value = format_amount(parsed) or ""
        elif change.field == "deadline_at":
            parsed = safe_value(
                change.field, value, parse_deadline_at, errors, extracted.notice_id
            )
            value = parsed or ""
        changes.append(
            {
                "field": change.field,
                "label": change.label,
                "raw_value": change.new_value,
                "normalized_value": value,
            }
        )
    return tuple(changes)


def process_entry(
    entry: ListEntry,
    *,
    config: AppConfig,
    fetcher: SafeFetcher,
    attachment_store: AttachmentStore,
    store: Store,
    paths: DataPaths,
    run_id: str,
    summary: CrawlSummary,
) -> tuple[NoticeRecord, list[AttachmentRecord], CorrectionRecord | None]:
    response = fetcher.fetch(entry.detail_url)
    extracted = extract_notice(response.text, response.url, config.selectors)
    normalized: dict[str, str | None] = {
        "publish_at": safe_value(
            "publish_at", extracted.publish_raw, parse_publish_at, summary.errors,
            extracted.notice_id,
        ),
        "deadline_at": safe_value(
            "deadline_at", extracted.deadline_raw, parse_deadline_at, summary.errors,
            extracted.notice_id,
        ),
        "budget_amount": safe_value(
            "budget_amount", extracted.budget_raw, parse_amount, summary.errors,
            extracted.notice_id,
        ),
        "award_amount": safe_value(
            "award_amount", extracted.award_amount_raw, parse_amount, summary.errors,
            extracted.notice_id,
        ),
    }
    normalized["budget_amount"] = format_amount(normalized["budget_amount"])
    normalized["award_amount"] = format_amount(normalized["award_amount"])
    relative = archive_bytes(
        paths,
        Path("raw") / safe_filename(extracted.notice_id) / f"{response.sha256[:16]}.html",
        response.content,
    )
    notice = NoticeRecord(
        notice_id=extracted.notice_id,
        kind=extracted.kind,
        kind_label=extracted.kind_label,
        title=extracted.title,
        project_code=extracted.project_code,
        region=extracted.region,
        purchaser=extracted.purchaser,
        agency=extracted.agency,
        publish_at=normalized["publish_at"],
        deadline_at=normalized["deadline_at"],
        budget_amount=normalized["budget_amount"],
        budget_currency="CNY",
        award_amount=normalized["award_amount"],
        winner=extracted.winner,
        related_notice_id=extracted.related_notice_id,
        content_excerpt=extracted.content_excerpt,
        source_url=response.url,
        source_hash=response.sha256,
    )
    attachments: list[AttachmentRecord] = []
    for ref in extracted.attachments:
        outcome = attachment_store.download(extracted.notice_id, ref)
        attachments.append(
            AttachmentRecord(
                notice_id=extracted.notice_id,
                filename=outcome.filename,
                source_url=outcome.source_url,
                sha256=outcome.sha256,
                size_bytes=outcome.size_bytes,
                stored_path=outcome.stored_path,
                content_type=outcome.content_type,
                status=outcome.status,
                error_code=outcome.error_code,
            )
        )
    correction = None
    if extracted.kind == "correction":
        correction = CorrectionRecord(
            correction_notice_id=extracted.notice_id,
            original_notice_id=extracted.related_notice_id,
            published_at=normalized["publish_at"],
            changes=normalize_correction_changes(extracted, summary.errors),
            source_url=response.url,
        )
    payload = {
        "notice": asdict(notice),
        "attachments": [asdict(item) for item in attachments],
        "correction_changes": list(correction.changes) if correction else [],
        "raw_sources": {
            key: asdict(value) for key, value in sorted(extracted.sources.items())
        },
    }
    with store.transaction():
        outcome = store.upsert_notice(
            notice, run_id, payload, build_sources(extracted, normalized)
        )
        store.archive_snapshot(
            notice.notice_id, response.url, response.sha256, relative, len(response.content)
        )
        for attachment in attachments:
            store.upsert_attachment(attachment, run_id)
        if correction is not None:
            store.upsert_correction(correction, run_id)
    if outcome == "inserted":
        summary.notices_inserted += 1
    elif outcome == "updated":
        summary.notices_updated += 1
    else:
        summary.notices_unchanged += 1
    summary.attachments_ok += sum(item.status == "stored" for item in attachments)
    summary.attachments_failed += sum(item.status != "stored" for item in attachments)
    return notice, attachments, correction
