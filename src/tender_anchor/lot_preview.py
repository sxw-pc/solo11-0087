"""Preview simple lot rows from a locally captured tender detail page."""

from __future__ import annotations

from bs4 import BeautifulSoup

from .extract import extract_notice


def preview_lots(html: str, page_url: str, selectors) -> dict:
    notice = extract_notice(html, page_url, selectors)
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    skipped = []
    for index, row in enumerate(soup.select("table.lot-lines > tbody > tr"), start=1):
        cells = row.find_all("td", recursive=False)
        if len(cells) != 3:
            skipped.append(index)
            continue
        values = [cell.get_text(" ", strip=True) for cell in cells]
        rows.append({"lot_id": values[0], "item": values[1],
                     "amount_raw": values[2], "row_index": index})
    return {"notice_id": notice.notice_id, "kind": notice.kind,
            "related_notice_id": notice.related_notice_id,
            "source_url": notice.source_url,
            "rows": rows, "skipped_rows": skipped}
