"""Resolve an effective, traceable lot list across original and corrections.

Two pre-declared completeness contracts are supported:

``delta_lots``
    A correction replaces only the ``(lot_id, item)`` entries it lists.
    Original entries the correction does not mention remain effective.

``replace_lots``
    A correction table is the complete new list. Original entries it does
    not list are withdrawn.

A correction is applied only when ``extract_notice`` evidence proves it
belongs to the original notice: it is a correction, its ``related_notice_id``
points at the original, and both documents declare the same project code.
The effective list is recomputed from the supplied HTML on every call, so a
changed correction or changed cross-row evidence cannot reuse old results.
"""

from __future__ import annotations

from .errors import AppError
from .lot_preview import preview_lots
from .normalize import stable_hash

POLICIES = ("delta_lots", "replace_lots")


def _source_summary(preview: dict) -> dict:
    return {
        "notice_id": preview["notice_id"],
        "kind": preview["kind"],
        "project_code": preview["project_code"],
        "related_notice_id": preview["related_notice_id"],
        "source_url": preview["source_url"],
        "html_sha256": preview["html_sha256"],
        "evidence_sha256": preview["evidence_sha256"],
    }


def _snapshot(row: dict, notice_id: str) -> dict:
    return {
        "lot_id": row["lot_id"],
        "item": row["item"],
        "amount_raw": row["amount_raw"],
        "amount_normalized": row["amount_normalized"],
        "source_notice_id": notice_id,
    }


def _effective_row(row: dict, preview: dict, origin: str,
                   supersedes: dict | None) -> dict:
    return {
        "lot_id": row["lot_id"],
        "item": row["item"],
        "amount_raw": row["amount_raw"],
        "amount_normalized": row["amount_normalized"],
        "row_index": row["row_index"],
        "raw_text": row["raw_text"],
        "origin": origin,
        "source_notice_id": preview["notice_id"],
        "source_kind": preview["kind"],
        "source_url": preview["source_url"],
        "html_sha256": preview["html_sha256"],
        "evidence_sha256": preview["evidence_sha256"],
        "supersedes": supersedes,
    }


def _conflict(row: dict, preview: dict, prior_notice_id: str) -> dict:
    return {
        "row_index": row["row_index"],
        "code": "LOT_DUPLICATE_KEY",
        "message": f"分项键 ({row['lot_id']}, {row['item']}) 已由 "
                   f"{prior_notice_id} 提供，拒绝后到的同键行",
        "lot_id": row["lot_id"],
        "item": row["item"],
        "amount_raw": row["amount_raw"],
        "source_notice_id": preview["notice_id"],
        "html_sha256": preview["html_sha256"],
        "evidence_sha256": preview["evidence_sha256"],
    }


def _identity_problem(original: dict, correction: dict) -> tuple[str, str] | None:
    if original["kind"] == "correction":
        return (
            "LOT_IDENTITY_CONFLICT",
            f"基准公告 {original['notice_id']} 本身是更正公告，不能作为原公告",
        )
    if correction["kind"] != "correction":
        return (
            "LOT_IDENTITY_NOT_CORRECTION",
            f"公告 {correction['notice_id']} 类型为 {correction['kind']}，不是更正公告",
        )
    if not correction["related_notice_id"]:
        return (
            "LOT_IDENTITY_UNVERIFIED",
            f"更正公告 {correction['notice_id']} 缺少关联原公告，无法验明归属",
        )
    if correction["related_notice_id"] != original["notice_id"]:
        return (
            "LOT_IDENTITY_CONFLICT",
            f"更正公告 {correction['notice_id']} 关联 "
            f"{correction['related_notice_id']}，与原公告 {original['notice_id']} 不符",
        )
    if not original["project_code"] or not correction["project_code"]:
        return (
            "LOT_IDENTITY_UNVERIFIED",
            "原公告或更正公告缺少项目编号，无法验明同一项目",
        )
    if correction["project_code"] != original["project_code"]:
        return (
            "LOT_IDENTITY_CONFLICT",
            f"更正公告项目 {correction['project_code']} 与原公告项目 "
            f"{original['project_code']} 不符",
        )
    return None


def _overlay(rows: list[dict], correction: dict) -> tuple[list[dict], list[dict]]:
    """Apply one correction's accepted rows under the delta contract."""
    result = [dict(row) for row in rows]
    rejected: list[dict] = []
    for row in correction["rows"]:
        key = (row["lot_id"], row["item"])
        existing_index = next(
            (index for index, item in enumerate(result)
             if (item["lot_id"], item["item"]) == key),
            None,
        )
        existing = result[existing_index] if existing_index is not None else None
        if existing is not None and existing["origin"] == "correction":
            rejected.append(
                _conflict(row, correction, existing["source_notice_id"])
            )
            continue
        supersedes = (
            _snapshot(existing, existing["source_notice_id"])
            if existing is not None else None
        )
        replacement = _effective_row(row, correction, "correction", supersedes)
        if existing_index is not None:
            result[existing_index] = replacement
            continue
        last_same_lot = max(
            (index for index, item in enumerate(result)
             if item["lot_id"] == row["lot_id"]),
            default=len(result) - 1,
        )
        result.insert(last_same_lot + 1, replacement)
    return result, rejected


def _replace_contract(corrections: list[dict]) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    rejected: list[dict] = []
    for correction in corrections:
        for row in correction["rows"]:
            key = (row["lot_id"], row["item"])
            existing = next(
                (item for item in rows if (item["lot_id"], item["item"]) == key),
                None,
            )
            if existing is not None:
                rejected.append(
                    _conflict(row, correction, existing["source_notice_id"])
                )
                continue
            rows.append(_effective_row(row, correction, "correction", None))
    return rows, rejected


def _rejections(preview: dict) -> list[dict]:
    return [dict(item) for item in preview["rejected"]]


def build_effective_lots(
    original_preview: dict,
    correction_previews: list[dict],
    *,
    policy: str = "delta_lots",
) -> dict:
    """Combine validated previews into a deterministically ordered list."""
    if policy not in POLICIES:
        raise AppError(
            "LOT_POLICY_INVALID",
            f"未知的更正完整性合同：{policy!r}",
            hint=f"允许的合同：{', '.join(POLICIES)}。",
            policy=str(policy),
        )
    original_rows = [
        _effective_row(row, original_preview, "original", None)
        for row in original_preview["rows"]
    ]
    rejected = _rejections(original_preview)
    excluded: list[dict] = []
    corrections: list[dict] = []
    for preview in sorted(correction_previews, key=lambda item: item["notice_id"]):
        problem = _identity_problem(original_preview, preview)
        if problem is not None:
            code, message = problem
            excluded.append(
                {
                    "notice_id": preview["notice_id"],
                    "code": code,
                    "message": message,
                    "source": _source_summary(preview),
                }
            )
            continue
        corrections.append(preview)
        rejected.extend(_rejections(preview))

    if policy == "delta_lots":
        rows = [dict(row) for row in original_rows]
        for correction in corrections:
            rows, conflicts = _overlay(rows, correction)
            rejected.extend(conflicts)
        withdrawn: list[dict] = []
    elif corrections:
        rows, conflicts = _replace_contract(corrections)
        rejected.extend(conflicts)
        published = {(row["lot_id"], row["item"]) for row in rows}
        withdrawn = [
            _snapshot(row, original_preview["notice_id"])
            for row in original_rows
            if (row["lot_id"], row["item"]) not in published
        ]
    else:
        # No correction table passed identity checks, so there is no
        # complete replacement list to adopt. Keep the verified original.
        rows = [dict(row) for row in original_rows]
        withdrawn = []

    rejected.sort(
        key=lambda item: (item.get("source_notice_id", ""),
                          item["row_index"], item["code"])
    )
    sources = [_source_summary(original_preview)]
    sources.extend(_source_summary(preview) for preview in corrections)
    payload = {
        "policy": policy,
        "original_notice_id": original_preview["notice_id"],
        "sources": sources,
        "excluded_sources": excluded,
        "rows": rows,
        "withdrawn": withdrawn,
        "rejected": rejected,
        "counts": {
            "effective": len(rows),
            "withdrawn": len(withdrawn),
            "rejected": len(rejected),
            "excluded_sources": len(excluded),
        },
    }
    payload["fingerprint"] = stable_hash(payload)
    return payload


def resolve_effective_lots(
    original_html: str,
    original_url: str,
    corrections: list[tuple[str, str]],
    selectors,
    *,
    policy: str = "delta_lots",
) -> dict:
    """Read inputs fresh and recompute the effective lot list."""
    original_preview = preview_lots(original_html, original_url, selectors)
    correction_previews = [
        preview_lots(html, url, selectors) for html, url in corrections
    ]
    return build_effective_lots(
        original_preview, correction_previews, policy=policy
    )
