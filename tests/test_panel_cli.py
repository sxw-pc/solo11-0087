import json
from pathlib import Path

import pytest

from tender_anchor.app_paths import ensure_layout
from tender_anchor.export import build_panel_export, export_panel
from tender_anchor.store import Store
from tender_anchor.web import create_review_app

from tests.panel_helpers import NOTICE, load


@pytest.fixture
def data_dir(tmp_path):
    paths = ensure_layout(tmp_path / "data")
    with Store(paths.db_path) as store:
        with store.transaction():
            store.start_run("run-panel", "http://127.0.0.1:5810", "cfg")
            store.upsert_notice(NOTICE, "run-panel", {"version": 1}, [])
    return paths


def test_cli_preview_then_record_and_export(data_dir, capsys):
    from tender_anchor import __main__ as cli

    case_path = str(Path(__file__).parents[1] / "fixtures/panel/case.json")
    assert cli.main(
        ["panel-intake", "--data-dir", str(data_dir.root), "--case", case_path]
    ) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "evidence_ready"
    assert preview["final_award"]["winner_bidder_id"] == "supplier-b"

    assert cli.main(
        ["panel-intake", "--data-dir", str(data_dir.root), "--case", case_path,
         "--record", "--export"]
    ) == 0
    recorded = json.loads(capsys.readouterr().out)
    assert recorded["status"] == "recorded"
    assert recorded["export"]["case_count"] == 1
    assert (data_dir.exports / "panel.json").is_file()


def test_export_document_is_deterministic_and_complete(data_dir):
    from tender_anchor.panel_intake import record_panel_case

    with Store(data_dir.db_path) as store:
        record_panel_case(store, load("case.json"))
        first = build_panel_export(store)
        second = build_panel_export(store)
        export_panel(store, data_dir)
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second
    case = first["cases"][0]
    assert [item["version"] for item in case["reviews"]] == [1, 2, 3]
    assert case["award"]["winner_bidder_id"] == "supplier-b"
    assert case["corrections"][0]["to_winner_bidder_id"] == "supplier-b"


def test_review_console_lists_and_shows_panel(data_dir):
    from tender_anchor.export import export_panel
    from tender_anchor.panel_intake import record_panel_case

    with Store(data_dir.db_path) as store:
        record_panel_case(store, load("case.json"))
        export_panel(store, data_dir)
    client = create_review_app(data_dir.db_path, data_dir=data_dir.root).test_client()
    listing = client.get("/panels")
    assert listing.status_code == 200
    assert "PANEL-TA-2026-0001-R1" in listing.get_data(as_text=True)
    detail = client.get("/panels/PANEL-TA-2026-0001-R1")
    assert detail.status_code == 200
    body = detail.get_data(as_text=True)
    assert "CONTRACT-WHOLE-ROUND" in body
    assert "supplier-b" in body
    assert client.get("/panels/UNKNOWN").status_code == 404
    assert client.get("/export/panel.json").status_code == 200


def test_cli_reports_conflict_as_error(data_dir, capsys):
    from tender_anchor import __main__ as cli
    from tender_anchor.panel_intake import record_panel_case

    case_path = str(Path(__file__).parents[1] / "fixtures/panel/case.json")
    with Store(data_dir.db_path) as store:
        record_panel_case(store, load("case.json"))
    raw = json.loads(Path(case_path).read_text(encoding="utf-8"))
    raw["ballots"][0]["score"] = 91
    changed = data_dir.root / "changed.json"
    changed.write_text(json.dumps(raw), encoding="utf-8")
    assert cli.main(
        ["panel-intake", "--data-dir", str(data_dir.root), "--case", str(changed),
         "--record"]
    ) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "PANEL_EVIDENCE_CONFLICT"
