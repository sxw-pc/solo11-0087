"""Loopback-only Flask mock tender portal."""

from __future__ import annotations

from flask import Flask, abort, jsonify, render_template_string, request, send_from_directory

from .fixtures import ATTACHMENT_DIR, load_fixtures

STYLE = """
:root{color-scheme:light;--ink:#1f2933;--muted:#5c6b78;--line:#d8dee5;--accent:#0b6b5b;--paper:#f7f5ef}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:"Segoe UI","Microsoft YaHei",sans-serif;line-height:1.65}
header,main,footer{max-width:980px;margin:auto;padding:18px 24px}header{border-bottom:2px solid var(--accent)}
h1{font-size:26px;line-height:1.3;margin:8px 0 14px}a{color:var(--accent)}
.notice-list{list-style:none;padding:0;margin:0;display:grid;gap:12px}.notice-item{background:#fff;border:1px solid var(--line);padding:14px 16px;border-radius:8px}
.notice-item .notice-title{font-weight:650;font-size:17px}.notice-date,.notice-kind,.notice-id{color:var(--muted);margin-left:10px;font-size:13px}
.pagination{margin-top:18px;display:flex;gap:16px}.notice-meta,.correction-diff{border-collapse:collapse;width:100%;background:#fff;margin:16px 0}
.notice-meta th,.notice-meta td,.correction-diff th,.correction-diff td{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top}
.notice-meta th,.correction-diff th{width:150px;background:#eef2ef}.notice-body{background:#fff;border:1px solid var(--line);padding:14px 18px;border-radius:8px}
.notice-body p{margin:0 0 10px}.disclaimer{color:var(--muted);font-size:13px}.badge{display:inline-block;background:#e5f2ed;color:#205c4f;border-radius:999px;padding:1px 9px;font-size:12px}
footer{color:var(--muted);font-size:12px;border-top:1px solid var(--line);margin-top:28px}
"""

BASE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} · 示例标讯门户</title><style>""" + STYLE + """</style></head>
<body><header><strong>示例标讯门户</strong><span class="badge">离线 · 虚构数据</span>
<p class="disclaimer">{{ disclaimer }}</p></header><main>{{ body|safe }}</main>
<footer>标讯锚 mock portal · 仅监听 loopback · 全部单位、项目与公告均为合成数据</footer></body></html>"""

LIST_BODY = """
<p>共 {{ total }} 条标讯，第 {{ page }} / {{ pages }} 页。</p>
<ul class="notice-list">
{% for notice in notices %}
<li class="notice-item kind-{{ notice.kind }}" data-kind="{{ notice.kind }}">
<a class="notice-link" href="{{ url_for('detail', notice_id=notice.notice_id) }}"><span class="notice-title">{{ notice.title }}</span></a>
<span class="notice-date">{{ notice.publish_date }}</span>
<span class="notice-kind">{{ notice.kind_label }}</span>
<span class="notice-id">{{ notice.notice_id }}</span>
</li>
{% endfor %}
</ul>
<nav class="pagination">
{% if page > 1 %}<a class="previous-page" href="{{ url_for('notice_list', page=page-1, page_size=page_size) }}">上一页</a>{% endif %}
{% if page < pages %}<a class="next-page" href="{{ url_for('notice_list', page=page+1, page_size=page_size) }}">下一页</a>{% endif %}
<a href="{{ url_for('notice_list', page=1, page_size=page_size) }}">首页</a>
</nav>
"""

DETAIL_BODY = """
<article class="notice-detail">
<h1 class="notice-title">{{ notice.title }}</h1>
<table class="notice-meta">
<tr><th>公告编号</th><td>{{ notice.notice_id }}</td></tr>
<tr><th>公告类型</th><td>{{ notice.kind_label }}</td></tr>
<tr><th>项目编号</th><td>{{ notice.project_code }}</td></tr>
<tr><th>地区</th><td>{{ notice.region }}</td></tr>
<tr><th>采购单位</th><td>{{ notice.purchaser }}</td></tr>
<tr><th>代理机构</th><td>{{ notice.agency }}</td></tr>
<tr><th>发布时间</th><td>{{ notice.publish_date }}</td></tr>
{% if notice.deadline_raw %}<tr><th>截止时间</th><td>{{ notice.deadline_raw }}</td></tr>{% endif %}
{% if notice.budget_raw %}<tr><th>预算金额</th><td>{{ notice.budget_raw }}</td></tr>{% endif %}
{% if notice.award_amount_raw %}<tr><th>成交金额</th><td>{{ notice.award_amount_raw }}</td></tr>{% endif %}
{% if notice.winner %}<tr><th>成交供应商</th><td>{{ notice.winner }}</td></tr>{% endif %}
{% if notice.correction %}<tr><th>关联原公告</th><td>{{ notice.correction.original_notice_id }}</td></tr>{% endif %}
{% if notice.result and notice.result.original_notice_id %}<tr><th>关联原公告</th><td>{{ notice.result.original_notice_id }}</td></tr>{% endif %}
</table>
<div class="notice-body"><p>{{ notice.summary }}</p><p>本页面为离线评测生成的合成公告，不构成任何采购邀请。</p></div>
{% if notice.correction %}
<h2>更正事项</h2>
<table class="correction-diff">
{% for change in notice.correction.changes %}
<tr data-field="{{ change.field }}"><th>{{ change.label }}</th><td>{{ change.value }}</td></tr>
{% endfor %}
</table>
{% endif %}
<section class="attachments"><h2>附件</h2>
{% if notice.attachments %}<ul>{% for filename in notice.attachments %}
<li><a class="attachment-link" href="{{ url_for('attachment', filename=filename) }}">{{ filename }}</a></li>
{% endfor %}</ul>{% else %}<p>无附件。</p>{% endif %}
</section>
<p><a class="back-link" href="{{ url_for('notice_list', page=1) }}">返回列表</a></p>
</article>
"""


def _page_number(name: str, default: int, maximum: int | None = None) -> int:
    raw = request.args.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        abort(400, description=f"{name} 必须是整数")
    if value < 1 or (maximum is not None and value > maximum):
        abort(400, description=f"{name} 超出允许范围")
    return value


def create_mock_app(catalog: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(JSON_AS_ASCII=False, MAX_CONTENT_LENGTH=1024 * 1024)

    def current_catalog() -> dict:
        return catalog if catalog is not None else load_fixtures()

    @app.before_request
    def only_loopback() -> None:
        if request.remote_addr not in {"127.0.0.1", "::1", "localhost"}:
            abort(403, description="mock portal 只允许 loopback 访问")

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'none'"
        )
        return response

    @app.get("/")
    @app.get("/notices")
    def notice_list():
        catalog = current_catalog()
        page_size = _page_number("page_size", 3, 20)
        ordered = [catalog["notices"][key] for key in catalog["order"]]
        total = len(ordered)
        pages = max(1, (total + page_size - 1) // page_size)
        page = _page_number("page", 1, pages)
        start = (page - 1) * page_size
        body = render_template_string(
            LIST_BODY,
            notices=ordered[start : start + page_size],
            total=total,
            page=page,
            pages=pages,
            page_size=page_size,
        )
        return render_template_string(
            BASE,
            title="标讯列表",
            disclaimer=catalog["disclaimer"],
            body=body,
        )

    @app.get("/notices/<notice_id>")
    def detail(notice_id: str):
        catalog = current_catalog()
        notice = catalog["notices"].get(notice_id)
        if notice is None:
            abort(404, description="公告不存在")
        return render_template_string(
            BASE,
            title=notice["title"],
            disclaimer=catalog["disclaimer"],
            body=render_template_string(DETAIL_BODY, notice=notice),
        )

    @app.get("/attachments/<path:filename>")
    def attachment(filename: str):
        if filename not in current_catalog()["attachments"]:
            abort(404, description="附件不存在")
        return send_from_directory(ATTACHMENT_DIR, filename, as_attachment=True)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "mode": "offline-mock",
                        "fixtures": len(current_catalog()["order"])})

    return app
