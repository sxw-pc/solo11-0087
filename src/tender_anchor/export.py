"""Deterministic machine-readable exports and run reports."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from .app_paths import DataPaths, atomic_write_text, ensure_within, safe_filename
from .errors import ExportError
from .normalize import now_iso
from .store import Store

EXPORT_SCHEMA_VERSION = 1
CSV_COLUMNS = (
    "notice_id", "kind", "kind_label", "title", "project_code", "region",
    "purchaser", "agency", "publish_at", "deadline_at", "budget_amount",
    "budget_currency", "award_amount", "winner", "revision", "source_url",
    "source_sha256", "attachments_stored", "attachments_failed", "quality_codes",
)


def _notice_document(store: Store, notice: dict[str, Any]) -> dict[str, Any]:
    bundle = store.get_bundle(notice["notice_id"]) or {}
    attachments = []
    for item in bundle.get("attachments", []):
        attachments.append(
            {
                "filename": item["filename"],
                "source_url": item["source_url"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
                "content_type": item["content_type"],
                "status": item["status"],
                "error_code": item["error_code"],
                "stored_path": item["stored_path"],
            }
        )
    corrections = []
    for item in bundle.get("corrections", []):
        corrections.append(
            {
                "correction_notice_id": item["correction_notice_id"],
                "original_notice_id": item["original_notice_id"],
                "published_at": item["published_at"],
                "changes": item["changes"],
                "source_url": item["source_url"],
            }
        )
    return {
        "notice_id": notice["notice_id"],
        "kind": notice["kind"],
        "kind_label": notice["kind_label"],
        "title": notice["title"],
        "project_code": notice["project_code"],
        "region": notice["region"],
        "purchaser": notice["purchaser"],
        "agency": notice["agency"],
        "publish_at": notice["publish_at"],
        "deadline_at": notice["deadline_at"],
        "budget": {
            "amount": notice["budget_amount"],
            "currency": notice["budget_currency"],
        },
        "award": {"amount": notice["award_amount"], "winner": notice["winner"]},
        "related_notice_id": notice["related_notice_id"],
        "content_excerpt": notice["content_excerpt"],
        "source": {
            "url": notice["source_url"],
            "sha256": notice["source_hash"],
            "first_seen_run": notice["first_seen_run"],
            "last_seen_run": notice["last_seen_run"],
            "revision": notice["revision"],
        },
        "attachments": sorted(attachments, key=lambda item: (item["filename"], item["source_url"])),
        "corrections": sorted(corrections, key=lambda item: item["correction_notice_id"]),
        "field_sources": [
            {
                "field": item["field_name"],
                "raw_value": item["raw_value"],
                "normalized_value": item["normalized_value"],
                "extractor": item["extractor"],
                "selector": item["selector"],
            }
            for item in bundle.get("field_sources", [])
        ],
        "quality": [
            {
                "rule_code": item["rule_code"],
                "severity": item["severity"],
                "message": item["message"],
                "status": item["status"],
            }
            for item in bundle.get("review_items", [])
        ],
    }


def build_export(store: Store) -> dict[str, Any]:
    notices = [
        _notice_document(store, notice) for notice in store.list_notices()
    ]
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "generated_at": now_iso(),
        "notice_count": len(notices),
        "notices": notices,
    }


def _write_json(path: Path, value: Any) -> None:
    try:
        atomic_write_text(
            path,
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
    except OSError as exc:
        raise ExportError("EXPORT_WRITE", f"JSON 导出失败：{path}") from exc


def _write_csv(path: Path, store: Store) -> int:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    count = 0
    for notice in store.list_notices():
        attachments = store.get_attachments(notice["notice_id"])
        quality = store.get_review_items(notice_id=notice["notice_id"])
        writer.writerow(
            {
                "notice_id": notice["notice_id"],
                "kind": notice["kind"],
                "kind_label": notice["kind_label"],
                "title": notice["title"],
                "project_code": notice["project_code"],
                "region": notice["region"],
                "purchaser": notice["purchaser"],
                "agency": notice["agency"],
                "publish_at": notice["publish_at"] or "",
                "deadline_at": notice["deadline_at"] or "",
                "budget_amount": notice["budget_amount"] or "",
                "budget_currency": notice["budget_currency"],
                "award_amount": notice["award_amount"] or "",
                "winner": notice["winner"],
                "revision": notice["revision"],
                "source_url": notice["source_url"],
                "source_sha256": notice["source_hash"],
                "attachments_stored": sum(1 for item in attachments if item["status"] == "stored"),
                "attachments_failed": sum(1 for item in attachments if item["status"] != "stored"),
                "quality_codes": "|".join(sorted(item["rule_code"] for item in quality)),
            }
        )
        count += 1
    try:
        atomic_write_text(path, buffer.getvalue())
    except OSError as exc:
        raise ExportError("EXPORT_WRITE", f"CSV 导出失败：{path}") from exc
    return count


def export_all(store: Store, paths: DataPaths) -> dict[str, Any]:
    json_path = ensure_within(paths.root, paths.exports / "notices.json", label="export")
    csv_path = ensure_within(paths.root, paths.exports / "notices.csv", label="export")
    _write_json(json_path, build_export(store))
    csv_count = _write_csv(csv_path, store)
    return {
        "json_path": paths.relative(json_path),
        "csv_path": paths.relative(csv_path),
        "csv_rows": csv_count,
        "notice_count": store.count("notices"),
    }


def write_run_report(paths: DataPaths, summary: dict[str, Any]) -> str:
    run_id = safe_filename(str(summary.get("run_id", "unknown")))
    path = ensure_within(
        paths.root, paths.reports / f"{run_id}.json", label="run report"
    )
    _write_json(path, summary)
    return paths.relative(path)
