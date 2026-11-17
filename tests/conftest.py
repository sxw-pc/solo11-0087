from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tender_anchor.app_paths import ensure_layout  # noqa: E402
from tender_anchor.config import load_rules  # noqa: E402
from tender_anchor.mock_portal.app import create_mock_app  # noqa: E402
from tender_anchor.pipeline import run_crawl  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def portal_server():
    from werkzeug.serving import make_server

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    servers = []

    def start(app):
        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        for _ in range(50):
            try:
                if requests.get(url + "/health", timeout=1).status_code == 200:
                    break
            except requests.RequestException:
                pass
            thread.join(timeout=0.05)
        servers.append((server, thread))
        return url

    yield start
    for server, thread in servers:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def portal_url(portal_server):
    return portal_server(create_mock_app())


@pytest.fixture
def config_builder(tmp_path):
    counter = {"value": 0}

    def build(base_url: str):
        counter["value"] += 1
        text = (FIXTURES / "config_valid.yaml").read_text(encoding="utf-8")
        text = text.replace("http://127.0.0.1:5810", base_url.rstrip("/"))
        path = tmp_path / f"sources-{counter['value']}.yaml"
        path.write_text(text, encoding="utf-8")
        from tender_anchor.config import load_config

        return load_config(path)

    return build


@pytest.fixture
def rules():
    return load_rules(ROOT / "config" / "rules.yaml")


@pytest.fixture
def expected_notices():
    return json.loads((FIXTURES / "expected_notices.json").read_text(encoding="utf-8"))


@pytest.fixture
def crawled(tmp_path, portal_url, config_builder, rules):
    config = config_builder(portal_url)
    paths = ensure_layout(tmp_path / "data")
    summary = run_crawl(config, rules, paths)
    return summary, paths, config
