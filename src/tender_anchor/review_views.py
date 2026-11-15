"""HTML builders for the read-only local review console."""

from __future__ import annotations

import html
import json
from typing import Any
from urllib.parse import quote

KIND_LABELS = {"tender": "招标", "correction": "更正", "result": "结果", "other": "其他"}
STYLE = """
:root{--ink:#20262e;--muted:#65717e;--line:#d9e0e6;--accent:#0d6b5c;--bg:#f5f7f4;--warn:#9a5b00;--bad:#a32929}
*{box-sizing:border-box}body{margin:0;font-family:"Segoe UI","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--ink)}
header{background:#123f37;color:#fff;padding:14px 24px}header a{color:#d9f2e8;margin-right:18px;text-decoration:none}
header strong{font-size:19px;margin-right:26px}main{max-width:1180px;margin:0 auto;padding:22px}
h1{font-size:24px}h2{font-size:18px;margin-top:26px}.muted{color:var(--muted);font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0}
.card{background:#fff;border:1px solid var(--line);border-radius:8px;padding:14px}.card b{display:block;font-size:25px}
table{border-collapse:collapse;width:100%;background:#fff;margin:10px 0 18px}th,td{border:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top;font-size:14px}
th{background:#eaf0ec;white-space:nowrap}a{color:var(--accent)}code{font-size:12px;word-break:break-all}
.sev-error{color:var(--bad);font-weight:700}.sev-warning{color:var(--warn);font-weight:700}
form.filters{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}input,select,button{padding:6px 9px;border:1px solid var(--line);border-radius:5px;background:#fff}
pre{background:#fff;border:1px solid var(--line);padding:10px;overflow:auto;white-space:pre-wrap}
footer{max-width:1180px;margin:10px auto;padding:12px 22px;color:var(--muted);font-size:12px}
"""


def e(value: Any) -> str:
    if value is None:
        return "—"
    return html.escape(str(value), quote=True)


def _notice_link(notice_id: str, label: str | None = None) -> str:
    return f'<a href="/notices/{quote(notice_id)}">{e(label or notice_id)}</a>'


def layout(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} · 标讯锚复核台</title><style>{STYLE}</style></head><body>
<header><strong>标讯锚 · 本地复核台</strong>
<a href="/">概览</a><a href="/notices">标讯</a><a href="/review">复核队列</a>
<a href="/runs">运行台账</a><a href="/audit">审计</a>
<span style="float:right;font-size:12px">只读 · 离线</span></header>
<main><h1>{e(title)}</h1>{body}</main>
<footer>只读复核台 · 不提供写入、投标、支付或外部网络功能 · 所有素材均为虚构数据</footer>
</body></html>"""


def dashboard_body(
    stats: dict[str, Any],
    reviews: list[dict[str, Any]],
    notices: list[dict[str, Any]] | None = None,
) -> str:
    kinds = stats.get("kinds", {})
    cards = [
        ("标讯", stats.get("notices", 0)),
        ("招标", kinds.get("tender", 0)),
        ("更正", kinds.get("correction", 0)),
        ("结果", kinds.get("result", 0)),
        ("附件成功", stats.get("attachments_stored", 0)),
        ("附件失败", stats.get("attachments_failed", 0)),
        ("未处理复核项", stats.get("open_reviews", 0)),
        ("运行次数", stats.get("runs", 0)),
    ]
    card_html = "".join(f'<div class="card">{e(label)}<b>{value}</b></div>' for label, value in cards)
    rows = "".join(
        f"<tr><td>{_notice_link(item['notice_id'])}</td><td>{e(item['kind_label'])}</td>"
        f"<td>{e(item['title'])}</td><td>{e(item['region'])}</td>"
        f"<td>{e(item['rule_code'])}</td></tr>"
        for item in reviews[:8]
    )
    latest = stats.get("latest_run") or {}
    latest_html = (
        f"<p>最近运行：<code>{e(latest.get('run_id'))}</code> · 状态 {e(latest.get('status'))}"
        f" · 标讯 {e(latest.get('notices_seen'))} 条 · 附件失败 {e(latest.get('attachments_failed'))}</p>"
        if latest else "<p class='muted'>尚无运行记录。</p>"
    )
    recent_rows = "".join(
        f"<tr><td>{_notice_link(item['notice_id'])}</td><td>{e(item['kind_label'])}</td>"
        f"<td>{e(item['title'])}</td><td>{e(item['budget_amount'])}</td>"
        f"<td>{e(item['deadline_at'])}</td></tr>"
        for item in (notices or [])[:6]
    )
    body = (
        f'<div class="cards">{card_html}</div>{latest_html}'
        "<h2>最近标讯</h2>"
        "<table><tr><th>编号</th><th>类型</th><th>标题</th><th>预算</th><th>截止时间</th></tr>"
        f"{recent_rows or '<tr><td colspan=5>暂无标讯</td></tr>'}</table>"
        "<h2>最新未处理复核项</h2>"
        "<table><tr><th>公告编号</th><th>类型</th><th>标题</th><th>地区</th><th>规则</th></tr>"
        f"{rows or '<tr><td colspan=5>无未处理项</td></tr>'}</table>"
        '<p><a href="/notices">查看全部标讯</a> · <a href="/export/notices.json">导出 JSON</a>'
        ' · <a href="/export/notices.csv">导出 CSV</a></p>'
    )
    return body


def notices_body(notices: list[dict[str, Any]], kind: str, query: str) -> str:
    options = "".join(
        f'<option value="{value}"{" selected" if kind == value else ""}>{e(label)}</option>'
        for value, label in [("", "全部类型"), ("tender", "招标公告"), ("correction", "更正公告"), ("result", "结果公告")]
    )
    rows = "".join(
        f"<tr><td>{_notice_link(item['notice_id'])}</td><td>{e(item['kind_label'])}</td>"
        f"<td>{e(item['title'])}</td><td>{e(item['region'])}</td>"
        f"<td>{e(item['budget_amount'])}</td><td>{e(item['deadline_at'])}</td>"
        f"<td>{e(item['revision'])}</td></tr>"
        for item in notices
    )
    body = f"""<form class="filters" method="get" action="/notices">
<select name="kind">{options}</select>
<input name="q" value="{e(query)}" placeholder="标题 / 编号 / 项目编号">
<button type="submit">筛选</button><span class="muted">共 {len(notices)} 条</span></form>
<table><tr><th>编号</th><th>类型</th><th>标题</th><th>地区</th><th>预算</th><th>截止时间</th><th>版本</th></tr>
{rows or '<tr><td colspan="7">没有匹配记录</td></tr>'}</table>"""
    return body


def _rows_table(headers: list[str], rows: list[list[str]], empty: str = "无") -> str:
    head = "".join(f"<th>{e(item)}</th>" for item in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><tr>{head}</tr>{body or f'<tr><td colspan={len(headers)}>{e(empty)}</td></tr>'}</table>"


def detail_body(bundle: dict[str, Any]) -> str:
    notice = bundle["notice"]
    meta = [
        ["公告编号", e(notice["notice_id"])],
        ["公告类型", f"{e(notice['kind_label'])} ({e(notice['kind'])})"],
        ["项目编号", e(notice["project_code"])],
        ["地区", e(notice["region"])],
        ["采购单位", e(notice["purchaser"])],
        ["代理机构", e(notice["agency"])],
        ["发布时间", e(notice["publish_at"])],
        ["截止时间", e(notice["deadline_at"])],
        ["预算金额", e(notice["budget_amount"]) + f" {e(notice['budget_currency'])}"],
        ["成交金额", e(notice["award_amount"])],
        ["成交供应商", e(notice["winner"])],
        ["关联原公告", _notice_link(notice["related_notice_id"]) if notice["related_notice_id"] else "—"],
        ["内容摘要", e(notice["content_excerpt"])],
        ["来源 URL", f"<code>{e(notice['source_url'])}</code>"],
        ["来源哈希", f"<code>{e(notice['source_hash'])}</code>"],
        ["首次运行", e(notice["first_seen_run"])],
        ["最近运行", e(notice["last_seen_run"])],
        ["版本", e(notice["revision"])],
    ]
    attachments = [
        [e(item["filename"]), e(item["status"]), e(item["size_bytes"]), e(item["content_type"]),
         f"<code>{e(item['sha256'])}</code>", e(item["error_code"]), e(item["stored_path"])]
        for item in bundle["attachments"]
    ]
    corrections = [
        [e(item["correction_notice_id"]), e(item["original_notice_id"]), e(item["published_at"]),
         e(json.dumps(item["changes"], ensure_ascii=False))]
        for item in bundle["corrections"]
    ]
    sources = [
        [e(item["field_name"]), e(item["raw_value"]), e(item["normalized_value"]),
         e(item["extractor"]), e(item["selector"])]
        for item in bundle["field_sources"]
    ]
    quality = [
        [e(item["rule_code"]), f'<span class="sev-{e(item["severity"])}">{e(item["severity"])}</span>',
         e(item["message"]), e(item["status"])]
        for item in bundle["review_items"]
    ]
    snapshots = [
        [f"<code>{e(item['sha256'])}</code>", e(item["byte_size"]), e(item["stored_path"])]
        for item in bundle["snapshots"]
    ]
    return (
        _rows_table(["字段", "值"], meta)
        + "<h2>附件</h2>" + _rows_table(["文件名", "状态", "字节", "类型", "SHA-256", "错误码", "本地路径"], attachments)
        + "<h2>更正关系</h2>" + _rows_table(["更正公告", "原公告", "发布时间", "变更"], corrections)
        + "<h2>字段来源</h2>" + _rows_table(["字段", "原始值", "归一化", "抽取器", "选择器"], sources)
        + "<h2>质量复核</h2>" + _rows_table(["规则", "级别", "说明", "状态"], quality)
        + "<h2>原始留档</h2>" + _rows_table(["SHA-256", "字节", "路径"], snapshots)
        + '<p><a href="/notices">返回列表</a></p>'
    )


def review_body(items: list[dict[str, Any]], status: str) -> str:
    rows = [
        [_notice_link(item["notice_id"]), e(item["rule_code"]),
         f'<span class="sev-{e(item["severity"])}">{e(item["severity"])}</span>',
         e(item["title"]), e(item["message"]), e(item["field_name"]), e(item["status"])]
        for item in items
    ]
    return (
        f'<p>当前筛选：{e(status or "全部")} · 共 {len(items)} 项</p>'
        '<p><a href="/review?status=open">未处理</a> · <a href="/review?status=resolved">已解决</a>'
        ' · <a href="/review">全部</a></p>'
        + _rows_table(["公告编号", "规则", "级别", "标题", "说明", "字段", "状态"], rows)
    )


def runs_body(runs: list[dict[str, Any]]) -> str:
    rows = []
    for run in runs:
        try:
            errors = json.dumps(json.loads(run["errors_json"]), ensure_ascii=False, indent=2)
        except (TypeError, json.JSONDecodeError):
            errors = str(run["errors_json"])
        rows.append(
            f"<tr><td><code>{e(run['run_id'])}</code></td><td>{e(run['started_at'])}</td>"
            f"<td>{e(run['status'])}</td><td>{e(run['pages_fetched'])}</td>"
            f"<td>{e(run['notices_seen'])}</td><td>{e(run['notices_inserted'])}</td>"
            f"<td>{e(run['notices_updated'])}</td><td>{e(run['notices_unchanged'])}</td>"
            f"<td>{e(run['attachments_ok'])}</td><td>{e(run['attachments_failed'])}</td>"
            f"<td>{e(run['review_open'])}</td><td><details><summary>查看</summary>"
            f"<pre>{e(errors)}</pre></details></td></tr>"
        )
    head = ("<tr><th>运行</th><th>开始</th><th>状态</th><th>页数</th><th>发现</th><th>新增</th>"
            "<th>更新</th><th>未变</th><th>附件成功</th><th>附件失败</th><th>复核项</th><th>错误</th></tr>")
    return f"<table>{head}{''.join(rows) or '<tr><td colspan=12>尚无运行</td></tr>'}</table>"


def audit_body(events: list[dict[str, Any]]) -> str:
    rows = [
        [e(item["created_at"]), e(item["event_type"]), e(item["target"]), e(item["detail"])]
        for item in events
    ]
    return _rows_table(["时间", "事件", "目标", "详情"], rows)
