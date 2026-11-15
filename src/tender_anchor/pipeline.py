"""End-to-end crawl, normalize, persist, review, and export pipeline."""

from __future__ import annotations

from .app_paths import DataPaths, make_run_id
from .attachments import AttachmentStore
from .config import AppConfig, RuleSettings
from .errors import AppError, PipelineError
from .export import export_all, write_run_report
from .fetch import LoopbackPolicy, SafeFetcher
from .models import AttachmentRecord, CorrectionRecord, CrawlSummary, NoticeRecord
from .normalize import now_iso
from .pipeline_helpers import add_error, crawl_index, process_entry
from .rules import apply_rules
from .store import Store


def run_crawl(config: AppConfig, rules: RuleSettings, paths: DataPaths) -> CrawlSummary:
    """Run one deterministic crawl against the configured local portal."""
    run_id = make_run_id(config.fingerprint())
    summary = CrawlSummary(run_id=run_id, status="running", started_at=now_iso())
    store = Store(paths.db_path)
    policy = LoopbackPolicy(config.portal.allowed_hosts)
    fetcher = SafeFetcher(
        policy,
        timeout_seconds=config.portal.timeout_seconds,
        max_bytes=config.portal.max_response_bytes,
        user_agent=config.portal.user_agent,
        audit=lambda kind, target, detail: store.audit(run_id, kind, target, detail),
    )
    attachment_store = AttachmentStore(
        paths.root, fetcher, max_bytes=config.portal.max_attachment_bytes
    )
    notices: list[NoticeRecord] = []
    attachments: list[AttachmentRecord] = []
    corrections: list[CorrectionRecord] = []
    try:
        with store.transaction():
            store.start_run(run_id, config.portal.base_url, config.fingerprint())
        entries = crawl_index(config, fetcher, paths, store, run_id, summary)
        summary.notices_seen = len(entries)
        for entry in entries:
            try:
                notice, item_attachments, correction = process_entry(
                    entry,
                    config=config,
                    fetcher=fetcher,
                    attachment_store=attachment_store,
                    store=store,
                    paths=paths,
                    run_id=run_id,
                    summary=summary,
                )
            except AppError as exc:
                add_error(summary.errors, exc.code, exc.message, entry.detail_url)
                continue
            notices.append(notice)
            attachments.extend(item_attachments)
            if correction is not None:
                corrections.append(correction)
        findings = apply_rules(notices, attachments, corrections, rules)
        with store.transaction():
            summary.review_open = store.sync_review_items(
                [finding.to_store_dict() for finding in findings], run_id
            )
        summary.errors.sort(
            key=lambda item: (item["code"], item["target"], item["message"])
        )
        summary.status = "completed" if not summary.errors else "completed_with_errors"
        summary.finished_at = now_iso()
        with store.transaction():
            store.finish_run(
                run_id,
                status=summary.status,
                stats=summary.to_dict(),
                errors=summary.errors,
            )
        summary.exports = export_all(store, paths)
        write_run_report(paths, summary.to_dict())
        return summary
    except Exception as exc:
        summary.status = "failed"
        summary.finished_at = now_iso()
        if isinstance(exc, AppError):
            add_error(summary.errors, exc.code, exc.message, "pipeline")
        else:
            add_error(summary.errors, "PIPELINE_FATAL", str(exc), "pipeline")
        summary.errors.sort(
            key=lambda item: (item["code"], item["target"], item["message"])
        )
        with store.transaction():
            store.finish_run(
                run_id,
                status="failed",
                stats=summary.to_dict(),
                errors=summary.errors,
            )
        if isinstance(exc, AppError):
            raise
        raise PipelineError("PIPELINE_FATAL", f"采集失败：{exc}") from exc
    finally:
        fetcher.close()
        store.close()
