"""Selector-driven extraction of synthetic tender list and detail pages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from .config import SelectorsConfig
from .errors import ParseError
from .normalize import clean_text, excerpt

NOTICE_ID_RE = re.compile(r"/notices/([^/?#]+)")
VALID_NOTICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,64}$")
KIND_MAP = {
    "招标公告": "tender",
    "采购公告": "tender",
    "更正公告": "correction",
    "变更公告": "correction",
    "结果公告": "result",
    "中标公告": "result",
    "成交公告": "result",
}
META_LABELS = {
    "notice_id": "公告编号",
    "kind_label": "公告类型",
    "project_code": "项目编号",
    "region": "地区",
    "purchaser": "采购单位",
    "agency": "代理机构",
    "publish_raw": "发布时间",
    "deadline_raw": "截止时间",
    "budget_raw": "预算金额",
    "award_amount_raw": "成交金额",
    "winner": "成交供应商",
    "related_notice_id": "关联原公告",
}


@dataclass(frozen=True)
class ListEntry:
    notice_id: str
    title: str
    kind: str
    kind_label: str
    publish_date: str
    detail_url: str


@dataclass(frozen=True)
class ListPage:
    entries: tuple[ListEntry, ...]
    next_url: str | None
    errors: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class AttachmentRef:
    filename: str
    url: str


@dataclass(frozen=True)
class CorrectionChange:
    field: str
    label: str
    new_value: str


@dataclass(frozen=True)
class FieldSourceValue:
    raw_value: str
    extractor: str
    selector: str


@dataclass(frozen=True)
class ExtractedNotice:
    notice_id: str
    kind: str
    kind_label: str
    title: str
    project_code: str
    region: str
    purchaser: str
    agency: str
    publish_raw: str
    deadline_raw: str
    budget_raw: str
    award_amount_raw: str
    winner: str
    related_notice_id: str
    content_excerpt: str
    attachments: tuple[AttachmentRef, ...]
    correction_changes: tuple[CorrectionChange, ...]
    source_url: str
    sources: dict[str, FieldSourceValue] = field(default_factory=dict)


def notice_id_from_url(url: str) -> str:
    match = NOTICE_ID_RE.search(urlsplit(url).path)
    return unquote(match.group(1)) if match else ""


def classify_kind(label: str) -> str:
    return KIND_MAP.get(clean_text(label), "other")


def _meta_values(soup: BeautifulSoup, selector: str) -> dict[str, str]:
    rows = soup.select(selector)
    if not rows:
        raise ParseError(
            "PARSE_META_EMPTY",
            "详情页未匹配到元数据行",
            hint="检查 selectors.meta_row 是否指向 th/td 行。",
        )
    values: dict[str, str] = {}
    for row in rows:
        head = row.find("th")
        cell = row.find("td")
        if head is None or cell is None:
            continue
        label = clean_text(head.get_text(" ", strip=True)).rstrip("：:")
        value = clean_text(cell.get_text(" ", strip=True))
        if label and label not in values:
            values[label] = value
    return values


def parse_list_page(
    html: str, page_url: str, selectors: SelectorsConfig
) -> ListPage:
    soup = BeautifulSoup(html, "html.parser")
    nodes = soup.select(selectors.list_item)
    if not nodes:
        raise ParseError(
            "PARSE_LIST_EMPTY",
            "列表页没有匹配到标讯条目",
            hint="检查 selectors.list_item 与页面结构是否一致。",
            url=page_url,
        )
    entries: list[ListEntry] = []
    errors: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, node in enumerate(nodes, start=1):
        link = node.select_one(selectors.list_link)
        href = link.get("href", "").strip() if link else ""
        title_node = node.select_one(selectors.list_title)
        title = clean_text(title_node.get_text(" ", strip=True)) if title_node else ""
        if not href or not title:
            errors.append(
                {
                    "code": "PARSE_LIST_ITEM",
                    "message": f"第 {index} 个列表条目缺少链接或标题",
                    "url": page_url,
                }
            )
            continue
        detail_url = urljoin(page_url, href)
        notice_id = notice_id_from_url(detail_url) or clean_text(
            node.get("data-notice-id", "")
        )
        if not VALID_NOTICE_ID_RE.match(notice_id):
            errors.append(
                {
                    "code": "PARSE_NOTICE_ID_MISSING",
                    "message": f"第 {index} 个列表条目无法识别公告编号",
                    "url": detail_url,
                }
            )
            continue
        if notice_id in seen:
            continue
        seen.add(notice_id)
        date_node = node.select_one(selectors.list_date)
        kind_node = node.select_one(selectors.list_kind)
        kind_label = (
            clean_text(kind_node.get_text(" ", strip=True)) if kind_node else ""
        )
        entries.append(
            ListEntry(
                notice_id=notice_id,
                title=title,
                kind=classify_kind(kind_label),
                kind_label=kind_label,
                publish_date=clean_text(
                    date_node.get_text(" ", strip=True) if date_node else ""
                ),
                detail_url=detail_url,
            )
        )
    next_node = soup.select_one(selectors.next_page)
    next_url = (
        urljoin(page_url, next_node.get("href", "").strip())
        if next_node and next_node.get("href", "").strip()
        else None
    )
    return ListPage(tuple(entries), next_url, tuple(errors))


def _attachment_refs(
    soup: BeautifulSoup, page_url: str, selector: str
) -> tuple[AttachmentRef, ...]:
    refs: list[AttachmentRef] = []
    seen: set[str] = set()
    for link in soup.select(selector):
        href = str(link.get("href", "")).strip()
        if not href:
            continue
        url = urljoin(page_url, href)
        if url in seen:
            continue
        filename = clean_text(link.get_text(" ", strip=True))
        if not filename:
            filename = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        if not filename:
            continue
        seen.add(url)
        refs.append(AttachmentRef(filename=filename, url=url))
    return tuple(refs)


def extract_notice(
    html: str, page_url: str, selectors: SelectorsConfig
) -> ExtractedNotice:
    soup = BeautifulSoup(html, "html.parser")
    title_node = soup.select_one(selectors.detail_title)
    title = clean_text(title_node.get_text(" ", strip=True)) if title_node else ""
    if not title:
        raise ParseError(
            "PARSE_TITLE_MISSING",
            "详情页未匹配到标题",
            hint="检查 selectors.detail_title 与页面结构。",
            url=page_url,
        )
    meta = _meta_values(soup, selectors.meta_row)
    notice_id = clean_text(meta.get(META_LABELS["notice_id"], ""))
    if not notice_id:
        notice_id = notice_id_from_url(page_url)
    if not VALID_NOTICE_ID_RE.match(notice_id):
        raise ParseError(
            "PARSE_NOTICE_ID_MISSING",
            "详情页元数据缺少合法公告编号",
            url=page_url,
        )
    kind_label = clean_text(meta.get(META_LABELS["kind_label"], "其他")) or "其他"
    related = clean_text(meta.get(META_LABELS["related_notice_id"], ""))
    if related in {"-", "—", "无", "N/A"}:
        related = ""
    body_node = soup.select_one(selectors.body)
    body_text = body_node.get_text(" ", strip=True) if body_node else ""
    changes: list[CorrectionChange] = []
    for row in soup.select(selectors.correction_diff):
        field_name = clean_text(str(row.get("data-field", "")))
        if not field_name:
            continue
        cells = row.find_all("td")
        label_node = row.find("th")
        label = (
            clean_text(label_node.get_text(" ", strip=True))
            if label_node is not None
            else field_name
        )
        value = clean_text(cells[-1].get_text(" ", strip=True)) if cells else ""
        changes.append(CorrectionChange(field=field_name, label=label, new_value=value))

    sources: dict[str, FieldSourceValue] = {
        "title": FieldSourceValue(title, "css_text", selectors.detail_title),
    }
    for field_name, label in META_LABELS.items():
        raw_value = clean_text(meta.get(label, ""))
        if raw_value:
            sources[field_name] = FieldSourceValue(
                raw_value, "meta_table", f"{selectors.meta_row} [{label}]")
    if body_text:
        sources["content_excerpt"] = FieldSourceValue(
            excerpt(body_text), "css_text", selectors.body)
    return ExtractedNotice(
        notice_id=notice_id,
        kind=classify_kind(kind_label),
        kind_label=kind_label,
        title=title,
        project_code=clean_text(meta.get(META_LABELS["project_code"], "")),
        region=clean_text(meta.get(META_LABELS["region"], "")),
        purchaser=clean_text(meta.get(META_LABELS["purchaser"], "")),
        agency=clean_text(meta.get(META_LABELS["agency"], "")),
        publish_raw=clean_text(meta.get(META_LABELS["publish_raw"], "")),
        deadline_raw=clean_text(meta.get(META_LABELS["deadline_raw"], "")),
        budget_raw=clean_text(meta.get(META_LABELS["budget_raw"], "")),
        award_amount_raw=clean_text(meta.get(META_LABELS["award_amount_raw"], "")),
        winner=clean_text(meta.get(META_LABELS["winner"], "")),
        related_notice_id=related,
        content_excerpt=excerpt(body_text),
        attachments=_attachment_refs(soup, page_url, selectors.attachments),
        correction_changes=tuple(changes),
        source_url=page_url,
        sources=sources,
    )
