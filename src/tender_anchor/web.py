"""Read-only Flask review console for locally stored tender data."""

from __future__ import annotations

from pathlib import Path

from flask import Flask, abort, g, request, send_file

from .app_paths import ensure_within
from .errors import StoreError
from .review_views import (
    audit_body, bond_body, dashboard_body, detail_body, e, layout, notices_body,
    review_body, runs_body,
)
from .bond_service import bond_case_summary
from .store_queries import ReadOnlyStore


def create_review_app(db_path: Path | str, data_dir: Path | str | None = None) -> Flask:
    database = Path(db_path).expanduser().resolve()
    if not database.is_file():
        raise StoreError("STORE_QUERY", f"数据库不存在，请先执行采集：{database}")
    data_root = Path(data_dir).expanduser().resolve() if data_dir else database.parent
    app = Flask(__name__)
    app.config.update(JSON_AS_ASCII=False, MAX_CONTENT_LENGTH=64 * 1024)

    def reader() -> ReadOnlyStore:
        if "review_store" not in g:
            g.review_store = ReadOnlyStore(database)
        return g.review_store

    @app.teardown_appcontext
    def close_reader(_: BaseException | None) -> None:
        store = g.pop("review_store", None)
        if store is not None:
            store.close()

    @app.after_request
    def security_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"
        )
        return response

    @app.get("/")
    def dashboard():
        store = reader()
        body = dashboard_body(
            store.stats(), store.get_review_items(status="open"), store.list_notices()
        )
        return layout("概览", body)

    @app.get("/notices")
    def notices():
        kind = request.args.get("kind", "").strip()
        query = request.args.get("q", "").strip()[:100]
        if kind not in {"", "tender", "correction", "result", "other"}:
            abort(400, description="未知公告类型")
        items = reader().list_notices(kind=kind or None, query=query or None)
        return layout("标讯列表", notices_body(items, kind, query))

    @app.get("/notices/<notice_id>")
    def notice_detail(notice_id: str):
        bundle = reader().get_bundle(notice_id)
        if bundle is None:
            abort(404, description="公告不存在")
        return layout(bundle["notice"]["title"], detail_body(bundle))

    @app.get("/review")
    def review():
        status = request.args.get("status", "open").strip()
        if status not in {"open", "resolved", ""}:
            abort(400, description="未知复核状态")
        items = reader().get_review_items(status=status or None)
        return layout("复核队列", review_body(items, status))

    @app.get("/runs")
    def runs():
        return layout("运行台账", runs_body(reader().list_runs(limit=40)))

    @app.get("/audit")
    def audit():
        return layout("审计事件", audit_body(reader().get_audit_events(limit=100)))

    @app.get("/bonds")
    def bonds():
        store = reader()
        case_ids = [
            row["case_id"]
            for row in store.connection.execute(
                "SELECT case_id FROM bond_cases ORDER BY case_id"
            ).fetchall()
        ]
        cases = [summary for summary in (bond_case_summary(store, case_id)
                                         for case_id in case_ids) if summary]
        return layout("跨标保证覆盖", bond_body(cases))

    def _export_path(filename: str) -> Path:
        path = ensure_within(
            data_root, data_root / "exports" / filename, label="review export"
        )
        if not path.is_file():
            abort(404, description="导出文件尚未生成")
        return path

    @app.get("/export/notices.json")
    def export_json():
        return send_file(
            _export_path("notices.json"),
            mimetype="application/json",
            as_attachment=True,
            download_name="notices.json",
        )

    @app.get("/export/notices.csv")
    def export_csv():
        return send_file(
            _export_path("notices.csv"),
            mimetype="text/csv",
            as_attachment=True,
            download_name="notices.csv",
        )

    @app.get("/export/bond_coverage.json")
    def export_bond_json():
        return send_file(
            _export_path("bond_coverage.json"),
            mimetype="application/json",
            as_attachment=True,
            download_name="bond_coverage.json",
        )

    @app.get("/export/bond_coverage.csv")
    def export_bond_csv():
        return send_file(
            _export_path("bond_coverage.csv"),
            mimetype="text/csv",
            as_attachment=True,
            download_name="bond_coverage.csv",
        )

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(500)
    def error_page(error):
        code = getattr(error, "code", 500)
        description = getattr(error, "description", "请求失败")
        body = (
            f"<p>状态码：{int(code)}</p><p>{e(description)}</p>"
            '<p><a href="/">返回概览</a></p>'
        )
        return (
            f"<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            f"<title>复核台错误</title></head><body><h1>请求未完成</h1>{body}</body></html>",
            code,
        )

    return app
