from pathlib import Path

from tender_anchor.app_paths import DEFAULT_CONFIG
from tender_anchor.config import load_config
from tender_anchor.lot_preview import preview_lots


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "lot_table"
SELECTORS = load_config(DEFAULT_CONFIG).selectors


def _preview(name, notice_id):
    return preview_lots((FIXTURE / name).read_text(encoding="utf-8"),
                        f"http://127.0.0.1:5810/notices/{notice_id}", SELECTORS)


def test_simple_lot_preview_has_two_structured_rows():
    result = _preview("simple.html", "TA-LOT-100")
    assert result["notice_id"] == "TA-LOT-100"
    assert result["kind"] == "tender"
    assert [(row["lot_id"], row["item"], row["amount_raw"])
            for row in result["rows"]] == [
                ("LOT-A", "终端", "100元"), ("LOT-B", "控制器", "200元")]
    assert result["skipped_rows"] == []


def test_rowspan_correction_is_detected_as_incomplete_preview():
    result = _preview("rowspan-correction.html", "TA-LOT-101")
    assert result["kind"] == "correction"
    assert result["related_notice_id"] == "TA-LOT-100"
    assert result["skipped_rows"] == [2, 4]
    assert [(row["lot_id"], row["item"]) for row in result["rows"]] == [
        ("LOT-A", "终端"), ("LOT-B", "控制器")]


def test_partial_correction_keeps_original_notice_link():
    result = _preview("partial-correction.html", "TA-LOT-102")
    assert result["related_notice_id"] == "TA-LOT-100"
    assert result["skipped_rows"] == [2]
    assert [(row["lot_id"], row["item"]) for row in result["rows"]] == [
        ("LOT-A", "终端")]
