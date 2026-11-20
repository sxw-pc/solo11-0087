"""Command line entry point for 标讯锚."""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

from werkzeug.serving import make_server

from .app_paths import (
    DEFAULT_CONFIG, DEFAULT_DATA, DEFAULT_RULES, DEMO_DATA, ensure_layout,
)
from .bid_intake import intake_bids, read_bids
from .bond_intake import read_bond_action, read_bond_case, preview_bond_case
from .bond_service import apply_reallocation, intake_bond_case
from .config import load_config, load_rules
from .errors import AppError
from .export import export_all
from .mock_portal.app import create_mock_app
from .pipeline import run_crawl
from .store import Store
from .web import create_review_app


def _emit(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _portal_address(config) -> tuple[str, int]:
    parsed = urlsplit(config.portal.base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return host, port


def _crawl(config_path: str, rules_path: str, data_dir: Path) -> dict:
    config = load_config(config_path)
    rules = load_rules(rules_path)
    paths = ensure_layout(data_dir)
    summary = run_crawl(config, rules, paths)
    return summary.to_dict()


def _demo(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    rules = load_rules(args.rules)
    host, port = _portal_address(config)
    server = make_server(host, port, create_mock_app(), threaded=True)
    thread = threading.Thread(target=server.serve_forever, name="mock-portal", daemon=True)
    thread.start()
    try:
        paths = ensure_layout(Path(args.data_dir))
        summary = run_crawl(config, rules, paths)
        _emit(
            {
                "demo": "completed",
                "portal": config.portal.base_url,
                "data_dir": str(paths.root),
                "summary": summary.to_dict(),
                "next": f"python -m tender_anchor serve-review --data-dir {paths.root}",
            }
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
    return 0


def _serve_portal(args: argparse.Namespace) -> int:
    app = create_mock_app()
    print(f"mock portal: http://127.0.0.1:{args.port}", flush=True)
    make_server("127.0.0.1", args.port, app, threaded=True).serve_forever()
    return 0


def _serve_review(args: argparse.Namespace) -> int:
    data_dir = Path(args.data_dir).expanduser().resolve()
    paths = ensure_layout(data_dir)
    app = create_review_app(paths.db_path, data_dir=paths.root)
    print(f"review console: http://127.0.0.1:{args.port}", flush=True)
    make_server("127.0.0.1", args.port, app, threaded=True).serve_forever()
    return 0


def _export(args: argparse.Namespace) -> int:
    paths = ensure_layout(Path(args.data_dir))
    with Store(paths.db_path) as store:
        result = export_all(store, paths)
    _emit({"exported": True, **result})
    return 0


def _bid_intake(args: argparse.Namespace) -> int:
    paths = ensure_layout(Path(args.data_dir))
    with Store(paths.db_path) as store:
        decisions = intake_bids(store, read_bids(args.bids))
    _emit({"bids": decisions})
    return 0


def _bond_preview(args: argparse.Namespace) -> int:
    paths = ensure_layout(Path(args.data_dir))
    case = read_bond_case(args.bond_case)
    with Store(paths.db_path) as store:
        _emit(preview_bond_case(store, case))
    return 0


def _bond_intake(args: argparse.Namespace) -> int:
    paths = ensure_layout(Path(args.data_dir))
    case = read_bond_case(args.bond_case)
    with Store(paths.db_path) as store:
        result = intake_bond_case(store, case)
        export_all(store, paths)
    _emit(result)
    return 0


def _bond_reallocate(args: argparse.Namespace) -> int:
    paths = ensure_layout(Path(args.data_dir))
    case = read_bond_case(args.bond_case)
    action = read_bond_action(args.action)
    with Store(paths.db_path) as store:
        result = apply_reallocation(store, case, action)
        export_all(store, paths)
    _emit(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tender_anchor",
        description="标讯锚：纯本地、可重复运行的标讯采集与复核工作台",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="启动内置 mock 门户并跑通完整演示")
    demo.add_argument("--crawl", action="store_true", help="执行列表、详情、附件采集")
    demo.add_argument("--config", default=str(DEFAULT_CONFIG))
    demo.add_argument("--rules", default=str(DEFAULT_RULES))
    demo.add_argument("--data-dir", default=str(DEMO_DATA))

    crawl = sub.add_parser("crawl", help="采集已启动的本机 mock 门户")
    crawl.add_argument("--config", default=str(DEFAULT_CONFIG))
    crawl.add_argument("--rules", default=str(DEFAULT_RULES))
    crawl.add_argument("--data-dir", default=str(DEFAULT_DATA))

    portal = sub.add_parser("serve-portal", help="启动 loopback mock 标讯门户")
    portal.add_argument("--port", type=int, default=5810)

    review = sub.add_parser("serve-review", help="启动 127.0.0.1 只读复核台")
    review.add_argument("--data-dir", default=str(DEFAULT_DATA))
    review.add_argument("--port", type=int, default=5811)

    export = sub.add_parser("export", help="从已有 SQLite 重新生成 JSON/CSV")
    export.add_argument("--data-dir", default=str(DEFAULT_DATA))

    bids = sub.add_parser("bid-intake", help="核对虚构投标信封与已采集公告")
    bids.add_argument("--data-dir", default=str(DEFAULT_DATA))
    bids.add_argument("--bids", required=True)

    bond_preview = sub.add_parser("bond-preview", help="预览保证工具证据与可用范围，不占用额度")
    bond_preview.add_argument("--data-dir", default=str(DEFAULT_DATA))
    bond_preview.add_argument("--bond-case", required=True)

    bond_intake = sub.add_parser("bond-intake", help="按固定质押合同受理跨标保证案件")
    bond_intake.add_argument("--data-dir", default=str(DEFAULT_DATA))
    bond_intake.add_argument("--bond-case", required=True)

    bond_reallocate = sub.add_parser(
        "bond-reallocate", help="凭供应商预授权原子替换质押并持久留痕"
    )
    bond_reallocate.add_argument("--data-dir", default=str(DEFAULT_DATA))
    bond_reallocate.add_argument("--bond-case", required=True)
    bond_reallocate.add_argument("--action", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            if not args.crawl:
                parser.error("demo 需要配合 --crawl 执行完整采集")
            return _demo(args)
        if args.command == "crawl":
            _emit(_crawl(args.config, args.rules, Path(args.data_dir)))
            return 0
        if args.command == "serve-portal":
            return _serve_portal(args)
        if args.command == "serve-review":
            return _serve_review(args)
        if args.command == "export":
            return _export(args)
        if args.command == "bid-intake":
            return _bid_intake(args)
        if args.command == "bond-preview":
            return _bond_preview(args)
        if args.command == "bond-intake":
            return _bond_intake(args)
        if args.command == "bond-reallocate":
            return _bond_reallocate(args)
    except AppError as exc:
        _emit({"error": exc.to_dict()})
        return 2
    except (OSError, ValueError) as exc:
        _emit({"error": {"code": "PIPELINE_FATAL", "message": str(exc)}})
        return 2
    parser.error("未知命令")
    return 2


if __name__ == "__main__":
    sys.exit(main())
