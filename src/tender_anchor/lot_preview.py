"""Preview structured lot rows and their cross-row evidence.

The outer ``table.lot-lines`` table is read as a bounded logical grid:
``rowspan`` cells from earlier rows fill the same logical column in later
rows, so a lot cell covering two detail rows attributes both items to that
lot. Nested tables (for example annotation tables inside an amount cell) are
never treated as lot rows and never contribute text to the outer cell.
"""

from __future__ import annotations

from bs4 import BeautifulSoup, NavigableString

from .errors import NormalizeError
from .extract import extract_notice
from .normalize import (
    clean_text, format_amount, parse_amount, sha256_bytes, stable_hash,
)

COLUMNS = ("lot_id", "item", "amount")
LOT_TABLE_SELECTOR = "table.lot-lines"
NESTED_SKIP = {"table", "script", "style"}


def _cell_text(node) -> str:
    """Text owned by a cell, excluding any nested table/script/style."""
    parts: list[str] = []
    for child in node.children:
        name = getattr(child, "name", None)
        if name in NESTED_SKIP:
            continue
        if name == "br":
            parts.append(" ")
        elif isinstance(child, NavigableString):
            parts.append(str(child))
        else:
            parts.append(_cell_text(child))
    return clean_text("".join(parts))


def _span(raw) -> int | None:
    if raw is None or not str(raw).strip():
        return 1
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 1 else None


def _outer_row_groups(soup: BeautifulSoup) -> list[list]:
    """Rows directly owned by each outer lot table section.

    Each ``tbody`` (or the bare table) is its own bounded group because HTML
    ``rowspan`` never crosses a table section.
    """
    groups = []
    for table in soup.select(LOT_TABLE_SELECTOR):
        if table.find_parent("table") is not None:
            continue
        scopes = table.find_all("tbody", recursive=False) or [table]
        for scope in scopes:
            rows = scope.find_all("tr", recursive=False)
            if rows:
                groups.append(rows)
    return groups


def _table_grid(rows: list) -> tuple[list[list], list[dict], dict[int, tuple]]:
    """Map one outer table's physical cells onto its bounded logical grid.

    Returns raw ``td`` lists, per-row ``{column: record}`` grid, and
    row-level structural rejections keyed by zero-based local row index.
    """
    count = len(rows)
    raw_cells = [[td for td in row.find_all("td", recursive=False)] for row in rows]
    active: dict[int, dict] = {}
    grid: list[dict[int, dict]] = []
    structural: dict[int, tuple[str, str]] = {}
    for index, cells in enumerate(raw_cells):
        logical: dict[int, dict] = {}
        for column, pending in active.items():
            logical[column] = pending["record"]
        column = 0
        carried: list[tuple[int, int, int, dict]] = []
        for td in cells:
            while column in logical:
                column += 1
            rowspan = _span(td.get("rowspan"))
            colspan = _span(td.get("colspan"))
            if rowspan is None:
                structural.setdefault(index, (
                    "LOT_ROWSPAN_OUT_OF_BOUNDS",
                    f"外层第 {index + 1} 行 rowspan={td.get('rowspan')!r} 不是"
                    "合法正整数",
                ))
                rowspan = 1
            elif index + rowspan > count:
                structural.setdefault(index, (
                    "LOT_ROWSPAN_OUT_OF_BOUNDS",
                    f"外层第 {index + 1} 行 rowspan={rowspan} 超出本表 "
                    f"{count} 行边界",
                ))
                # Never carry an out-of-bounds span into later rows: those
                # rows must be judged on their own cells, not inherit a
                # rejected lot id.
                rowspan = 1
            if colspan is None or colspan != 1:
                structural.setdefault(index, (
                    "LOT_ROW_SHAPE",
                    f"外层第 {index + 1} 行包含跨列单元格 "
                    f"colspan={td.get('colspan')!r}，不是单列分项",
                ))
                colspan = colspan if colspan and colspan >= 1 else 1
            record = {
                "text": _cell_text(td),
                "origin_row": index,
            }
            for spanned in range(column, column + colspan):
                logical[spanned] = record
            if rowspan > 1:
                carried.append((column, colspan, rowspan, record))
            column += colspan
        next_active: dict[int, dict] = {}
        for spanned, pending in active.items():
            remaining = pending["remaining"] - 1
            if remaining > 0:
                next_active[spanned] = {
                    "record": pending["record"], "remaining": remaining,
                }
        for start, span, rowspan, record in carried:
            for spanned in range(start, start + span):
                next_active[spanned] = {
                    "record": record, "remaining": rowspan - 1,
                }
        active = next_active
        grid.append(logical)
    return raw_cells, grid, structural


def _logical_grid(groups: list[list]) -> tuple[list[list], list[dict], dict[int, tuple]]:
    """Combine per-table grids using document-wide outer row positions."""
    raw_cells: list[list] = []
    grid: list[dict] = []
    structural: dict[int, tuple[str, str]] = {}
    offset = 0
    for rows in groups:
        table_raw, table_grid, table_structural = _table_grid(rows)
        raw_cells.extend(table_raw)
        grid.extend(table_grid)
        for local, reason in table_structural.items():
            structural[offset + local] = reason
        offset += len(rows)
    return raw_cells, grid, structural


def _evidence(raw_cells: list[list], grid: list[dict]) -> str:
    payload = []
    for index, cells in enumerate(raw_cells, start=1):
        payload.append(
            {
                "row_index": index,
                "cells": [
                    {
                        "text": _cell_text(td),
                        "rowspan": clean_text(str(td.get("rowspan", "1") or "1")),
                        "colspan": clean_text(str(td.get("colspan", "1") or "1")),
                    }
                    for td in cells
                ],
                "logical": {
                    str(column): grid[index - 1][column]["text"]
                    for column in sorted(grid[index - 1])
                },
            }
        )
    return stable_hash(payload)


def _rejection(
    row_index: int, code: str, message: str, values: list[str],
    notice, html_sha256: str, evidence_sha256: str,
) -> dict:
    return {
        "row_index": row_index,
        "code": code,
        "message": message,
        "lot_id": clean_text(values[0]) if len(values) > 0 else "",
        "item": clean_text(values[1]) if len(values) > 1 else "",
        "amount_raw": clean_text(values[2]) if len(values) > 2 else "",
        "source_notice_id": notice.notice_id,
        "source_url": notice.source_url,
        "html_sha256": html_sha256,
        "evidence_sha256": evidence_sha256,
    }


def preview_lots(html: str, page_url: str, selectors) -> dict:
    """Return accepted rows, per-row evidence, and stable rejection reasons."""
    notice = extract_notice(html, page_url, selectors)
    soup = BeautifulSoup(html, "html.parser")
    raw_cells, grid, structural = _logical_grid(_outer_row_groups(soup))
    html_sha256 = sha256_bytes(html.encode("utf-8"))
    evidence_sha256 = _evidence(raw_cells, grid)

    accepted: list[dict] = []
    rejected: list[dict] = []
    seen: dict[tuple[str, str], int] = {}
    for index, logical in enumerate(grid):
        row_index = index + 1
        values = [
            logical[column]["text"] if column in logical else ""
            for column in range(len(COLUMNS))
        ]
        if index in structural:
            code, message = structural[index]
            rejected.append(_rejection(
                row_index, code, message, values, notice, html_sha256,
                evidence_sha256,
            ))
            continue
        missing = [COLUMNS[c] for c in range(len(COLUMNS)) if c not in logical]
        extra = sorted(c for c in logical if c >= len(COLUMNS))
        if missing or extra:
            detail = f"缺少列 {missing}" if missing else f"多余列 {extra}"
            rejected.append(_rejection(
                row_index, "LOT_ROW_SHAPE",
                f"外层第 {row_index} 行不是 lot/分项/金额 三列：{detail}",
                values, notice, html_sha256, evidence_sha256,
            ))
            continue
        lot_id, item, amount_raw = (
            clean_text(values[0]), clean_text(values[1]), clean_text(values[2])
        )
        if not lot_id or not item:
            rejected.append(_rejection(
                row_index, "LOT_ROW_KEY_EMPTY",
                f"外层第 {row_index} 行缺少 lot 编号或分项名称",
                values, notice, html_sha256, evidence_sha256,
            ))
            continue
        try:
            amount_normalized = format_amount(parse_amount(amount_raw))
        except NormalizeError as exc:
            rejected.append(_rejection(
                row_index, "LOT_AMOUNT_INVALID", exc.message, values, notice,
                html_sha256, evidence_sha256,
            ))
            continue
        key = (lot_id, item)
        if key in seen:
            rejected.append(_rejection(
                row_index, "LOT_DUPLICATE_KEY",
                f"分项键 ({lot_id}, {item}) 与外层第 {seen[key]} 行冲突",
                values, notice, html_sha256, evidence_sha256,
            ))
            continue
        seen[key] = row_index
        accepted.append(
            {
                "lot_id": lot_id,
                "item": item,
                "amount_raw": amount_raw,
                "amount_normalized": amount_normalized,
                "row_index": row_index,
                "raw_text": " | ".join(values),
                "raw_cells": values,
                "source_notice_id": notice.notice_id,
                "source_kind": notice.kind,
                "source_url": notice.source_url,
                "html_sha256": html_sha256,
                "evidence_sha256": evidence_sha256,
            }
        )
    rejected.sort(key=lambda item: (item["row_index"], item["code"]))
    return {
        "notice_id": notice.notice_id,
        "kind": notice.kind,
        "kind_label": notice.kind_label,
        "project_code": notice.project_code,
        "related_notice_id": notice.related_notice_id,
        "source_url": notice.source_url,
        "html_sha256": html_sha256,
        "evidence_sha256": evidence_sha256,
        "rows": accepted,
        "skipped_rows": sorted({item["row_index"] for item in rejected}),
        "rejected": rejected,
        "counts": {"accepted": len(accepted), "rejected": len(rejected)},
    }
