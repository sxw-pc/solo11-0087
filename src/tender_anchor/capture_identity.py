"""Identity, alias, and version-binding evidence for two capture streams.

This module converts captured receipts into statistical units under one named
publishing contract.  It never infers identity from a shared content hash
alone: aliases only merge when the contract pre-authorises the issuer and the
local one-to-one receipt is admissible.  Any version conflict, stale binding,
or ambiguous alias is isolated before ``m`` and ``U`` are counted.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .coverage_certificate import ALPHA

PUBLISHING_CONTRACTS = ("operator_canonical_notices", "public_id_records")
EXPECTED_UNITS = {
    "public_id_records": "public_id",
    "operator_canonical_notices": "operator_canonical_notice",
}
PORTALS = ("portal_a", "portal_b")


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def normalized_source(
    panel: Mapping[str, Any], bindings: Mapping[str, str] | None
) -> dict[str, Any]:
    """Build an order-independent snapshot used for replay source digests."""

    def sort_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
        return sorted(
            (
                {
                    "notice_id": str(row["notice_id"]).lower(),
                    "source_hash": str(row["source_hash"]).lower(),
                }
                for row in rows
            ),
            key=lambda row: row["notice_id"],
        )

    aliases = sorted(
        (
            {
                "receipt_id": alias["receipt_id"],
                "issuer": alias["issuer"],
                "portal_a_id": alias["portal_a_id"],
                "portal_b_id": alias["portal_b_id"],
            }
            for alias in panel.get("alias_receipts", [])
        ),
        key=lambda alias: (
            alias["receipt_id"],
            alias["portal_a_id"],
            alias["portal_b_id"],
            alias["issuer"],
        ),
    )
    parsed_contracts = sorted(
        (dict(contract) for contract in panel["contracts"]),
        key=lambda contract: str(contract["contract_id"]),
    )
    return {
        "panel_version": panel["version"],
        "cohort_id": panel["cohort_id"],
        "captures": {portal: sort_rows(panel["captures"][portal]) for portal in PORTALS},
        "alias_receipts": aliases,
        "coverage_threshold": panel.get("coverage_threshold"),
        "contracts": parsed_contracts,
        "version_bindings": (
            None if bindings is None else {key: bindings[key] for key in sorted(bindings)}
        ),
        "certificate_method": "exact_hypergeometric_tail_inversion",
        "alpha": ALPHA.numerator / ALPHA.denominator,
    }


def stale_receipts(
    by_portal: Mapping[str, Mapping[str, str]],
    bindings: Mapping[str, str] | None,
) -> list[dict[str, str]]:
    """Flag receipts whose stored notice version no longer matches the capture."""
    if bindings is None:
        return []
    stale = []
    for portal in PORTALS:
        for notice_id, source_hash in by_portal[portal].items():
            if notice_id in bindings and bindings[notice_id] == source_hash:
                continue
            # A receipt absent from the bound store is reported as missing rather
            # than silently treated as current.
            reason = "not_in_store" if notice_id not in bindings else "version_changed"
            stale.append({"portal": portal, "notice_id": notice_id, "reason": reason})
    return sorted(stale, key=lambda item: (item["portal"], item["notice_id"]))


class _UnionFind:
    def __init__(self, nodes: Iterable[str]) -> None:
        self.parent = {node: node for node in nodes}

    def find(self, node: str) -> str:
        root = node
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[node] != node:
            self.parent[node], node = root, self.parent[node]
        return root

    def union(self, left: str, right: str) -> bool:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return False
        self.parent[right_root] = left_root
        return True


def assess_aliases(
    panel: Mapping[str, Any],
    contract: Mapping[str, Any],
    by_portal: Mapping[str, Mapping[str, str]],
    conflicts: set[str],
    stale: set[tuple[str, str]],
) -> dict[str, Any]:
    """Return admissible alias receipts plus isolated conflicts and defects."""
    receipts = list(panel.get("alias_receipts", []))
    trusted = contract.get("trusted_alias_issuer")
    evidence: dict[str, Any] = {
        "applicable": True,
        "trusted_alias_issuer": trusted if _nonempty_str(trusted) else None,
        "attested_receipts": len(receipts),
        "admissible_receipts": 0,
        "isolated_receipts": 0,
        "admissible_receipt_ids": [],
        "isolated": [],
        "_admissible": [],
        "_defects": [],
    }
    if not receipts:
        return evidence
    count_a: dict[str, int] = {}
    count_b: dict[str, int] = {}
    receipt_ids: set[str] = set()
    repeated_receipts: set[str] = set()
    for alias in receipts:
        count_a[alias["portal_a_id"]] = count_a.get(alias["portal_a_id"], 0) + 1
        count_b[alias["portal_b_id"]] = count_b.get(alias["portal_b_id"], 0) + 1
        receipt_id = str(alias["receipt_id"])
        if receipt_id in receipt_ids:
            repeated_receipts.add(receipt_id)
        receipt_ids.add(receipt_id)
    for alias in sorted(receipts, key=lambda row: str(row["receipt_id"])):
        a_id, b_id = alias["portal_a_id"], alias["portal_b_id"]
        reasons = []
        if not _nonempty_str(trusted):
            reasons.append("alias_authority_missing")
        elif alias["issuer"] != trusted:
            reasons.append("alias_issuer_not_trusted")
        if str(alias["receipt_id"]) in repeated_receipts:
            reasons.append("alias_receipt_repeated")
        if count_a[a_id] > 1:
            reasons.append("alias_portal_a_reused")
        if count_b[b_id] > 1:
            reasons.append("alias_portal_b_reused")
        if a_id not in by_portal["portal_a"] or b_id not in by_portal["portal_b"]:
            reasons.append("alias_endpoint_missing")
        elif a_id in by_portal["portal_b"] or b_id in by_portal["portal_a"]:
            reasons.append("alias_endpoint_ambiguous")
        if a_id in conflicts or b_id in conflicts:
            reasons.append("alias_endpoint_version_conflict")
        if any(
            (portal, notice_id) in stale
            for portal, notice_id in (
                ("portal_a", a_id), ("portal_b", b_id),
                ("portal_b", a_id), ("portal_a", b_id),
            )
        ):
            reasons.append("alias_endpoint_stale")
        if by_portal["portal_a"].get(a_id) != by_portal["portal_b"].get(b_id):
            reasons.append("alias_hash_mismatch")
        if reasons:
            evidence["isolated"].append(
                {"receipt_id": alias["receipt_id"], "reasons": sorted(set(reasons))}
            )
            evidence["_defects"].append(
                {
                    "code": "alias_conflict",
                    "receipt_id": alias["receipt_id"],
                    "reasons": sorted(set(reasons)),
                }
            )
        else:
            evidence["_admissible"].append(alias)
    evidence["admissible_receipts"] = len(evidence["_admissible"])
    evidence["isolated_receipts"] = len(evidence["isolated"])
    evidence["admissible_receipt_ids"] = sorted(
        alias["receipt_id"] for alias in evidence["_admissible"]
    )
    evidence["isolated"].sort(key=lambda item: str(item["receipt_id"]))
    return evidence


def public_units(
    a: Mapping[str, str], b: Mapping[str, str]
) -> tuple[int, int]:
    """Units for the public-ID contract: only same ID and same version hash."""
    overlap = sum(1 for notice_id in a.keys() & b.keys() if a[notice_id] == b[notice_id])
    return overlap, len(a.keys() | b.keys())


def canonical_units(
    a: Mapping[str, str],
    b: Mapping[str, str],
    aliases: list[Mapping[str, str]],
) -> tuple[int, int, list[dict[str, str]]]:
    """Units for the operator contract: merge only admissible alias receipts."""
    union = _UnionFind(set(a) | set(b))
    cycles: list[dict[str, str]] = []
    for alias in sorted(aliases, key=lambda row: str(row["receipt_id"])):
        if not union.union(alias["portal_a_id"], alias["portal_b_id"]):
            cycles.append({"receipt_id": alias["receipt_id"], "reason": "alias_cycle"})
    components: dict[str, dict[str, bool]] = {}
    for notice_id in a:
        components.setdefault(union.find(notice_id), {"a": False, "b": False})["a"] = True
    for notice_id in b:
        components.setdefault(union.find(notice_id), {"a": False, "b": False})["b"] = True
    overlap = sum(1 for flags in components.values() if flags["a"] and flags["b"])
    return overlap, len(components), cycles


