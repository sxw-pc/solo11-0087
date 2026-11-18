import json
from pathlib import Path

from tender_anchor import __main__ as cli
from tender_anchor.app_paths import DEFAULT_CONFIG
from tender_anchor.config import load_config
from tender_anchor.lot_effective import (
    build_effective_lots, resolve_effective_lots,
)
from tender_anchor.lot_preview import preview_lots

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "lot_table"
SELECTORS = load_config(DEFAULT_CONFIG).selectors
BASE = "http://127.0.0.1:5810/notices"


def _html(name):
    return (FIXTURE / name).read_text(encoding="utf-8")


def _preview(name, notice_id):
    return preview_lots(_html(name), f"{BASE}/{notice_id}", SELECTORS)


def _effective(policy, original="simple.html", original_id="TA-LOT-100",
               corrections=(("partial-correction.html", "TA-LOT-102"),)):
    return resolve_effective_lots(
        _html(original), f"{BASE}/{original_id}",
        [(_html(name), f"{BASE}/{notice_id}") for name, notice_id in corrections],
        SELECTORS, policy=policy,
    )


def _triples(result):
    return [(row["lot_id"], row["item"], row["amount_normalized"])
            for row in result["rows"]]


def test_delta_lots_keeps_unmentioned_original_rows():
    result = _effective("delta_lots")
    assert result["policy"] == "delta_lots"
    assert _triples(result) == [
        ("LOT-A", "终端", "110.00"),
        ("LOT-A", "安装", "40.00"),
        ("LOT-B", "控制器", "200.00"),
    ]
    by_key = {(row["lot_id"], row["item"]): row for row in result["rows"]}
    assert by_key[("LOT-A", "终端")]["origin"] == "correction"
    assert by_key[("LOT-A", "终端")]["supersedes"]["amount_normalized"] == "100.00"
    assert by_key[("LOT-A", "终端")]["supersedes"]["source_notice_id"] == "TA-LOT-100"
    assert by_key[("LOT-B", "控制器")]["origin"] == "original"
    assert by_key[("LOT-B", "控制器")]["source_notice_id"] == "TA-LOT-100"
    assert result["withdrawn"] == []
    assert result["excluded_sources"] == []


def test_replace_lots_withdraws_rows_absent_from_correction():
    result = _effective("replace_lots")
    assert _triples(result) == [
        ("LOT-A", "终端", "110.00"),
        ("LOT-A", "安装", "40.00"),
    ]
    assert [(row["lot_id"], row["item"]) for row in result["withdrawn"]] == [
        ("LOT-B", "控制器")]
    assert result["withdrawn"][0]["source_notice_id"] == "TA-LOT-100"
    assert all(row["origin"] == "correction" for row in result["rows"])


def test_neither_policy_emits_nested_annotation_or_misattributes_lot():
    for policy in ("delta_lots", "replace_lots"):
        result = _effective(policy)
        keys = [(row["lot_id"], row["item"]) for row in result["rows"]]
        amounts = [row["amount_raw"] + row["amount_normalized"]
                   for row in result["rows"]]
        assert not any("999" in amount for amount in amounts)
        assert ("LOT-B", "安装") not in keys
        assert all(lot != "LOT-B" or item == "控制器" for lot, item in keys)


def test_same_item_in_different_lots_is_not_merged():
    original = preview_lots(
        _html("simple.html").replace(
            "<td>控制器</td>", "<td>终端</td>"),
        f"{BASE}/TA-LOT-100", SELECTORS,
    )
    result = build_effective_lots(original, [], policy="delta_lots")
    assert [(row["lot_id"], row["item"]) for row in result["rows"]] == [
        ("LOT-A", "终端"), ("LOT-B", "终端")]
    assert len(result["rows"]) == 2


def test_identity_conflict_excludes_correction_source():
    result = _effective("delta_lots",
                        corrections=(("identity-conflict.html", "TA-LOT-107"),))
    assert [(source["notice_id"]) for source in result["sources"]] == ["TA-LOT-100"]
    assert result["excluded_sources"][0]["code"] == "LOT_IDENTITY_CONFLICT"
    assert _triples(result) == [
        ("LOT-A", "终端", "100.00"), ("LOT-B", "控制器", "200.00")]


def test_missing_or_foreign_related_notice_is_unverified_or_conflict():
    missing = _effective("delta_lots",
                         corrections=(("related-missing.html", "TA-LOT-108"),))
    assert missing["excluded_sources"][0]["code"] == "LOT_IDENTITY_UNVERIFIED"
    other = _effective("delta_lots",
                       corrections=(("related-other.html", "TA-LOT-109"),))
    assert other["excluded_sources"][0]["code"] == "LOT_IDENTITY_CONFLICT"
    assert _triples(other) == [
        ("LOT-A", "终端", "100.00"), ("LOT-B", "控制器", "200.00")]


def test_non_correction_notice_is_not_applied():
    result = _effective("delta_lots",
                        corrections=(("not-correction.html", "TA-LOT-110"),))
    assert result["excluded_sources"][0]["code"] == "LOT_IDENTITY_NOT_CORRECTION"
    assert _triples(result) == [
        ("LOT-A", "终端", "100.00"), ("LOT-B", "控制器", "200.00")]


def test_conflicting_keys_across_two_corrections_reject_the_later_row():
    first = _preview("partial-correction.html", "TA-LOT-102")
    second = _preview("replace-correction.html", "TA-LOT-103")
    result = build_effective_lots(
        _preview("simple.html", "TA-LOT-100"), [first, second],
        policy="delta_lots",
    )
    assert len(result["sources"]) == 3
    keys = [(row["lot_id"], row["item"]) for row in result["rows"]]
    assert keys.count(("LOT-A", "终端")) == 1
    duplicate = [row for row in result["rejected"]
                 if row["code"] == "LOT_DUPLICATE_KEY"
                 and row["source_notice_id"] == "TA-LOT-103"]
    assert {row["item"] for row in duplicate} == {"终端", "安装"}
    corrected = [row for row in result["rows"] if row["origin"] == "correction"]
    assert all(row["source_notice_id"] == "TA-LOT-102" for row in corrected)
    assert result["rows"][0]["amount_normalized"] == "110.00"


def test_invalid_amount_and_duplicate_rows_keep_other_valid_rows():
    amount = _preview("invalid-amount.html", "TA-LOT-106")
    assert [(row["lot_id"], row["item"]) for row in amount["rows"]] == [
        ("LOT-B", "控制器")]
    assert [(item["row_index"], item["code"]) for item in amount["rejected"]] == [
        (1, "LOT_AMOUNT_INVALID")]
    duplicate = _preview("duplicate-keys.html", "TA-LOT-105")
    assert [(row["lot_id"], row["item"]) for row in duplicate["rows"]] == [
        ("LOT-A", "终端"), ("LOT-B", "控制器")]
    assert duplicate["skipped_rows"] == [2]
    assert duplicate["rejected"][0]["code"] == "LOT_DUPLICATE_KEY"


def test_out_of_bounds_rowspan_is_rejected_without_poisoning_other_rows():
    result = _preview("outside-rowspan.html", "TA-LOT-104")
    assert result["skipped_rows"] == [1]
    assert result["rejected"][0]["code"] == "LOT_ROWSPAN_OUT_OF_BOUNDS"
    assert [(row["lot_id"], row["item"]) for row in result["rows"]] == [
        ("LOT-B", "控制器"), ("LOT-C", "传感器")]


def test_rerun_is_byte_stable():
    first = _effective("delta_lots")
    second = _effective("delta_lots")
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == \
        json.dumps(second, ensure_ascii=False, sort_keys=True)
    assert first["fingerprint"] == second["fingerprint"]


def test_changed_correction_source_recomputes_effective_list():
    baseline = _effective("delta_lots")
    changed_html = _html("partial-correction.html").replace("110元", "118元")
    changed = resolve_effective_lots(
        _html("simple.html"), f"{BASE}/TA-LOT-100",
        [(changed_html, f"{BASE}/TA-LOT-102")], SELECTORS, policy="delta_lots",
    )
    assert baseline["fingerprint"] != changed["fingerprint"]
    assert _triples(changed) == [
        ("LOT-A", "终端", "118.00"),
        ("LOT-A", "安装", "40.00"),
        ("LOT-B", "控制器", "200.00"),
    ]
    assert changed["sources"][1]["html_sha256"] != \
        baseline["sources"][1]["html_sha256"]


def test_changed_relation_source_recomputes_effective_list():
    baseline = _effective("delta_lots")
    retargeted = _html("partial-correction.html").replace(
        "<td>TA-LOT-100</td>", "<td>TA-LOT-999</td>")
    changed = resolve_effective_lots(
        _html("simple.html"), f"{BASE}/TA-LOT-100",
        [(retargeted, f"{BASE}/TA-LOT-102")], SELECTORS, policy="delta_lots",
    )
    assert changed["fingerprint"] != baseline["fingerprint"]
    assert changed["excluded_sources"][0]["code"] == "LOT_IDENTITY_CONFLICT"
    assert _triples(changed) == [
        ("LOT-A", "终端", "100.00"), ("LOT-B", "控制器", "200.00")]


def test_changed_row_evidence_recomputes_effective_list():
    baseline = _effective("delta_lots")
    # Same logical rows and amounts, but row 2 carries its own lot cell
    # instead of inheriting it from a rowspan. The effective quantities are
    # identical; the cross-row evidence and fingerprint must still change.
    flat_html = _html("partial-correction.html").replace(
        '<td rowspan="2">LOT-A</td><td>终端</td>',
        '<td>LOT-A</td><td>终端</td>',
    ).replace("<tr><td>安装</td>", "<tr><td>LOT-A</td><td>安装</td>")
    changed = resolve_effective_lots(
        _html("simple.html"), f"{BASE}/TA-LOT-100",
        [(flat_html, f"{BASE}/TA-LOT-102")], SELECTORS, policy="delta_lots",
    )
    assert _triples(changed) == _triples(baseline)
    assert changed["sources"][1]["evidence_sha256"] != \
        baseline["sources"][1]["evidence_sha256"]
    assert changed["sources"][1]["html_sha256"] != \
        baseline["sources"][1]["html_sha256"]
    assert changed["fingerprint"] != baseline["fingerprint"]


def test_row_evidence_and_raw_text_preserve_original_cells():
    row = _preview("partial-correction.html", "TA-LOT-102")["rows"][1]
    assert row["amount_raw"] == "40元"
    assert row["raw_text"] == "LOT-A | 安装 | 40元"
    assert row["row_index"] == 2
    assert row["source_notice_id"] == "TA-LOT-102"
    assert len(row["html_sha256"]) == 64
    assert len(row["evidence_sha256"]) == 64


def test_replace_lots_without_verified_correction_keeps_original():
    result = _effective("replace_lots",
                        corrections=(("identity-conflict.html", "TA-LOT-107"),))
    assert result["excluded_sources"][0]["code"] == "LOT_IDENTITY_CONFLICT"
    assert _triples(result) == [
        ("LOT-A", "终端", "100.00"), ("LOT-B", "控制器", "200.00")]
    assert result["withdrawn"] == []


def test_invalid_policy_is_refused():
    import pytest
    from tender_anchor.errors import AppError

    with pytest.raises(AppError) as caught:
        build_effective_lots(_preview("simple.html", "TA-LOT-100"), [],
                             policy="merge_lots")
    assert caught.value.code == "LOT_POLICY_INVALID"


def test_replace_lots_uses_only_listed_correction_rows_across_sources():
    result = resolve_effective_lots(
        _html("simple.html"), f"{BASE}/TA-LOT-100",
        [(_html("replace-correction.html"), f"{BASE}/TA-LOT-103")],
        SELECTORS, policy="replace_lots",
    )
    assert _triples(result) == [
        ("LOT-A", "终端", "120.00"), ("LOT-A", "安装", "45.00")]
    assert [(row["lot_id"], row["item"]) for row in result["withdrawn"]] == [
        ("LOT-B", "控制器")]
    assert result["sources"][0]["notice_id"] == "TA-LOT-100"


def test_correction_used_as_original_is_refused_as_identity_conflict():
    result = resolve_effective_lots(
        _html("partial-correction.html"), f"{BASE}/TA-LOT-102",
        [(_html("replace-correction.html"), f"{BASE}/TA-LOT-103")],
        SELECTORS, policy="delta_lots",
    )
    assert result["excluded_sources"][0]["code"] == "LOT_IDENTITY_CONFLICT"
    assert _triples(result) == [
        ("LOT-A", "终端", "110.00"), ("LOT-A", "安装", "40.00")]


def test_cli_emits_effective_list_and_is_byte_stable(capsys):
    argv = [
        "lot-preview", "--html", str(FIXTURE / "simple.html"),
        "--url", f"{BASE}/TA-LOT-100",
        "--correction", f"{FIXTURE / 'partial-correction.html'}::{BASE}/TA-LOT-102",
        "--policy", "replace_lots",
    ]
    assert cli.main(argv) == 0
    first = capsys.readouterr().out
    assert cli.main(argv) == 0
    second = capsys.readouterr().out
    assert first == second
    payload = json.loads(first)
    assert payload["policy"] == "replace_lots"
    assert [(row["lot_id"], row["item"], row["amount_normalized"])
            for row in payload["rows"]] == [
                ("LOT-A", "终端", "110.00"), ("LOT-A", "安装", "40.00")]
    assert payload["withdrawn"][0]["item"] == "控制器"


def test_cli_requires_path_and_url_for_correction(capsys):
    assert cli.main([
        "lot-preview", "--html", str(FIXTURE / "simple.html"),
        "--url", f"{BASE}/TA-LOT-100", "--correction", "missing-separator",
    ]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "CONFIG_INVALID"
