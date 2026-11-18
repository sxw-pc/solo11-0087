"""HTML builders for the read-only panel review console pages."""

from __future__ import annotations

import html
import json
from typing import Any
from urllib.parse import quote


def _e(value: Any) -> str:
    if value is None:
        return "—"
    return html.escape(str(value), quote=True)


def _notice_link(notice_id: str) -> str:
    return f'<a href="/notices/{quote(notice_id)}">{_e(notice_id)}</a>'


def _rows_table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{_e(item)}</th>" for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    return (
        f"<table><tr>{head}</tr>"
        f"{body or f'<tr><td colspan={len(headers)}>无</td></tr>'}</table>"
    )


def panels_body(cases: list[dict[str, Any]]) -> str:
    rows = [
        [
            f'<a href="/panels/{quote(item["case_id"])}">{_e(item["case_id"])}</a>',
            _notice_link(item["notice_id"]),
            _e(item["round_id"]),
            _e(item.get("award_status")),
            _e(item.get("award_stage")),
            _e(item.get("winner_bidder_id")),
            _e(item.get("winning_mean")),
        ]
        for item in cases
    ]
    return _rows_table(
        ["评审案件", "公告", "轮次", "状态", "裁定阶段", "获胜投标人", "技术均分"], rows
    )


def panel_detail_body(bundle: dict[str, Any]) -> str:
    case = bundle["case"]
    award = bundle["award"] or {}
    meta = [
        ["评审案件", _e(case["case_id"])],
        ["公告", _notice_link(case["notice_id"])],
        ["采购轮次", _e(case["round_id"])],
        ["首评时点", _e(case["initial_review_at"])],
        ["计分规则", _e(case["scoring_json"])],
        ["证据哈希", f"<code>{_e(case['evidence_hash'])}</code>"],
        ["最终状态", _e(award.get("status"))],
        ["最终获胜", _e(award.get("winner_bidder_id"))],
        ["技术均分", _e(award.get("winning_mean"))],
        ["裁定阶段", _e(award.get("stage"))],
        ["授权合同", _e(award.get("contract_id"))],
    ]
    reviews = []
    for item in bundle["reviews"]:
        summary = json.loads(item["summary_json"])
        rows = "<br>".join(
            f"{_e(row['bid_id'])}@{_e(row.get('display_mean'))} "
            f"[{_e(','.join(row.get('included_reviewer_ids', [])))}]"
            for row in summary.get("bids", [])
        )
        reviews.append(
            f"<tr><td>v{_e(item['version'])}</td><td>{_e(item['stage'])}</td>"
            f"<td>{_e(item['contract_id'])}</td><td>{_e(item['status'])}</td>"
            f"<td>{_e(item['reason'])}</td><td>{_e(item['winner_bidder_id'])}</td>"
            f"<td>{rows}</td></tr>"
        )
    review_table = (
        "<table><tr><th>版本</th><th>阶段</th><th>合同</th><th>状态</th><th>原因</th>"
        f"<th>获胜</th><th>技术票</th></tr>{''.join(reviews)}</table>"
    )
    votes = [
        [
            _e(item["review_id"]), _e(item["bid_id"]), _e(item["reviewer_id"]),
            _e(item["ballot_id"]), _e(item["score"]),
            "计入" if item["included"] else f"剔除（{_e(item['exclusion_reason'])}）",
        ]
        for item in bundle["votes"]
    ]
    corrections = [
        [
            _e(item["review_id"]), _e(item["prior_review_id"]),
            _e(item["from_winner_bidder_id"]), _e(item["to_winner_bidder_id"]),
            _e(item["reason"]), _e(item["created_at"]),
        ]
        for item in bundle["corrections"]
    ]
    return (
        _rows_table(["字段", "值"], meta)
        + "<h2>评审版本</h2>" + review_table
        + "<h2>逐票明细</h2>" + _rows_table(
            ["评审", "投标", "评委", "票", "分数", "有效"], votes
        )
        + "<h2>授标更正</h2>" + _rows_table(
            ["评审", "原评审", "原获胜", "新获胜", "合同", "时间"], corrections
        )
        + '<p><a href="/panels">返回评审列表</a></p>'
    )
