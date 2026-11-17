"""Read overlap evidence from two synthetic local notice capture streams."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_capture_panel(path: str | Path) -> dict[str, Any]:
    panel = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(panel, dict) or panel.get("version") != 1:
        raise ValueError("capture panel requires version 1")
    captures = panel.get("captures")
    if not isinstance(captures, dict) or set(captures) != {"portal_a", "portal_b"}:
        raise ValueError("two named capture streams are required")
    contracts = panel.get("contracts")
    if (not isinstance(contracts, list) or
            {row.get("contract_id") for row in contracts if isinstance(row, dict)}
            != {"public_id_records", "operator_canonical_notices"}):
        raise ValueError("capture contracts are required")
    by_portal: dict[str, dict[str, str]] = {}
    for portal, rows in captures.items():
        if not isinstance(rows, list) or not rows:
            raise ValueError("capture rows are required")
        found: dict[str, str] = {}
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("notice_id"), str)
                    or not row["notice_id"] or not isinstance(row.get("source_hash"), str)
                    or len(row["source_hash"]) != 64 or row["notice_id"] in found):
                raise ValueError("capture receipt invalid or repeated")
            found[row["notice_id"]] = row["source_hash"]
        by_portal[portal] = found
    seen_aliases: set[tuple[str, str]] = set()
    for alias in panel.get("alias_receipts", []):
        if (not isinstance(alias, dict) or alias.get("issuer") != "local-mock-operator"
                or not alias.get("receipt_id") or not alias.get("portal_a_id")
                or not alias.get("portal_b_id")):
            raise ValueError("alias receipt invalid")
        pair = (alias["portal_a_id"], alias["portal_b_id"])
        if (pair in seen_aliases or pair[0] not in by_portal["portal_a"]
                or pair[1] not in by_portal["portal_b"]
                or by_portal["portal_a"][pair[0]] != by_portal["portal_b"][pair[1]]):
            raise ValueError("alias identity or content mismatch")
        seen_aliases.add(pair)
    return panel


def preview_overlap(panel: dict[str, Any]) -> dict[str, Any]:
    a = {row["notice_id"] for row in panel["captures"]["portal_a"]}
    b = {row["notice_id"] for row in panel["captures"]["portal_b"]}
    return {
        "cohort_id": panel["cohort_id"],
        "portal_a_count": len(a),
        "portal_b_count": len(b),
        "exact_id_overlap": len(a & b),
        "attested_aliases": len(panel.get("alias_receipts", [])),
        "contracts": sorted(row["contract_id"] for row in panel["contracts"]),
        "coverage_decision": None,
    }


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Inspect two local notice capture streams")
    parser.add_argument("--panel", required=True)
    args = parser.parse_args()
    print(json.dumps(preview_overlap(read_capture_panel(args.panel)), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
