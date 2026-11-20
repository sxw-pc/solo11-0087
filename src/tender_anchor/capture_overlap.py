"""Two-portal capture overlap evidence and one-sided coverage certificates.

Each publishing contract names its own statistical unit and sampling model.
The ``public_id_records`` contract never merges distinct public IDs, even when
their content hashes match.  The ``operator_canonical_notices`` contract merges
an alias pair only when the local operator is pre-authorised as the alias
authority, the receipt is one-to-one, and both receipt hashes agree.  A shared
body hash alone is never treated as evidence that two IDs are one notice.

Certificates use the exact hypergeometric recapture tail for the declared
independent SRSWOR design; ``m / (n_a + n_b)`` and Chapman estimates are not
used as coverage guarantees.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from .capture_evaluate import evaluate_contract
from .capture_identity import (
    PORTALS,
    PUBLISHING_CONTRACTS,
    normalized_source,
    stale_receipts,
)
from .coverage_certificate import ALPHA, canonical_digest

CONTRACT_IDS = PUBLISHING_CONTRACTS
_HEX = set("0123456789abcdef")


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value.lower()) <= _HEX


def read_capture_panel(path: str | Path) -> dict[str, Any]:
    """Read and structurally validate a synthetic two-portal capture panel."""
    panel = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(panel, dict) or panel.get("version") != 1:
        raise ValueError("capture panel requires version 1")
    if not _nonempty_str(panel.get("cohort_id")):
        raise ValueError("capture panel requires a cohort id")
    captures = panel.get("captures")
    if not isinstance(captures, dict) or set(captures) != set(PORTALS):
        raise ValueError("two named capture streams are required")
    by_portal: dict[str, dict[str, str]] = {}
    for portal in PORTALS:
        rows = captures[portal]
        if not isinstance(rows, list) or not rows:
            raise ValueError("capture rows are required")
        found: dict[str, str] = {}
        for row in rows:
            if (not isinstance(row, dict) or not _nonempty_str(row.get("notice_id"))
                    or not _valid_hash(row.get("source_hash"))
                    or row["notice_id"] in found):
                raise ValueError("capture receipt invalid or repeated")
            if "portal" in row and row["portal"] != portal:
                raise ValueError("capture receipt portal mismatch")
            found[row["notice_id"]] = row["source_hash"].lower()
        by_portal[portal] = found
    contracts = panel.get("contracts")
    if not isinstance(contracts, list) or len(contracts) != len(CONTRACT_IDS):
        raise ValueError("capture contracts are required")
    contract_ids = []
    for contract in contracts:
        if not isinstance(contract, dict) or not _nonempty_str(contract.get("contract_id")):
            raise ValueError("capture contracts are required")
        contract_ids.append(contract["contract_id"])
    if set(contract_ids) != set(CONTRACT_IDS):
        raise ValueError("capture contracts are required")
    threshold = panel.get("coverage_threshold")
    if threshold is not None and (
        isinstance(threshold, bool) or not isinstance(threshold, (int, float))
        or not 0 < threshold < 1
    ):
        raise ValueError("coverage threshold must lie strictly between 0 and 1")
    receipts: set[str] = set()
    pairs: set[tuple[str, str]] = set()
    for alias in panel.get("alias_receipts", []):
        if not isinstance(alias, dict) or not all(
            _nonempty_str(alias.get(key))
            for key in ("receipt_id", "issuer", "portal_a_id", "portal_b_id")
        ):
            raise ValueError("alias receipt invalid")
        pair = (alias["portal_a_id"], alias["portal_b_id"])
        if (alias["receipt_id"] in receipts or pair in pairs
                or pair[0] not in by_portal["portal_a"]
                or pair[1] not in by_portal["portal_b"]
                or by_portal["portal_a"][pair[0]] != by_portal["portal_b"][pair[1]]):
            raise ValueError("alias identity or content mismatch")
        receipts.add(alias["receipt_id"])
        pairs.add(pair)
    return panel


def read_version_bindings(db_path: str | Path) -> dict[str, str]:
    """Read the current ``notice_id -> source_hash`` binding from SQLite."""
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"version binding database not found: {path}")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT notice_id, source_hash FROM notices").fetchall()
    finally:
        connection.close()
    return {str(notice_id): str(source_hash).lower() for notice_id, source_hash in rows}


def _capture_maps(panel: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    return {
        portal: {
            row["notice_id"]: str(row["source_hash"]).lower()
            for row in panel["captures"][portal]
        }
        for portal in PORTALS
    }


def preview_overlap(
    panel: dict[str, Any],
    *,
    version_bindings: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Evaluate both contracts and return a deterministic certificate report."""
    bindings = (
        None
        if version_bindings is None
        else {str(key): str(value).lower() for key, value in version_bindings.items()}
    )
    by_portal = _capture_maps(panel)
    conflicts = sorted(
        notice_id
        for notice_id in by_portal["portal_a"].keys() & by_portal["portal_b"].keys()
        if by_portal["portal_a"][notice_id] != by_portal["portal_b"][notice_id]
    )
    stale = stale_receipts(by_portal, bindings)
    source_digest = canonical_digest(normalized_source(panel, bindings))
    results = [
        evaluate_contract(
            panel, contract, by_portal, conflicts, stale, bindings is not None
        )
        for contract in sorted(panel["contracts"], key=lambda row: str(row["contract_id"]))
    ]
    for result in results:
        result["source_digest"] = source_digest
        result["evidence_digest"] = canonical_digest(
            {key: value for key, value in result.items() if key != "evidence_digest"}
        )
    report = {
        "cohort_id": panel["cohort_id"],
        "panel_version": panel["version"],
        "portal_a_count": len(by_portal["portal_a"]),
        "portal_b_count": len(by_portal["portal_b"]),
        "exact_id_overlap": sum(
            1
            for notice_id in by_portal["portal_a"].keys() & by_portal["portal_b"].keys()
            if by_portal["portal_a"][notice_id] == by_portal["portal_b"][notice_id]
        ),
        "attested_aliases": len(panel.get("alias_receipts", [])),
        "contracts": sorted(result["contract_id"] for result in results),
        "coverage_decision": {
            result["contract_id"]: {
                "decision": result["decision"],
                "threshold": result["threshold"],
                "coverage_lower_bound": result["coverage_lower_bound"],
                "N_upper": result["N_upper"],
                "can_claim_threshold_coverage": result["can_claim_threshold_coverage"],
            }
            for result in results
        },
        "contract_results": results,
        "source_digest": source_digest,
        "version_bindings_digest": (
            None if bindings is None else canonical_digest(dict(sorted(bindings.items())))
        ),
    }
    report["evidence_digest"] = canonical_digest(
        {key: value for key, value in report.items() if key != "evidence_digest"}
    )
    return report


def certificate_is_current(
    certificate: Mapping[str, Any],
    panel: dict[str, Any],
    *,
    version_bindings: Mapping[str, str] | None = None,
) -> bool:
    """Return whether a cached certificate still matches freshly bound receipts."""
    fresh = preview_overlap(panel, version_bindings=version_bindings)
    return (
        certificate.get("source_digest") == fresh["source_digest"]
        and certificate.get("evidence_digest") == fresh["evidence_digest"]
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Evaluate two-portal capture overlap coverage certificates"
    )
    parser.add_argument("--panel", required=True)
    parser.add_argument(
        "--db", help="bind every receipt to the current notice version in this SQLite store"
    )
    args = parser.parse_args()
    bindings = read_version_bindings(args.db) if args.db else None
    print(
        json.dumps(
            preview_overlap(read_capture_panel(args.panel), version_bindings=bindings),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
