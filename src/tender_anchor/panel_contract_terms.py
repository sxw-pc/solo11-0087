"""Validation of the two authorised recusal contracts.

A contract is only valid when it names the authority, a time no earlier than
its cited disclosures, an alternate for every conflicted reviewer, and a scope
consistent with its declared kind.  Whole-round contracts must cover every bid
of the round; absolute contracts may only strike the disclosed bid pairs.
"""

from __future__ import annotations

from typing import Any

from .errors import PanelError
from .panel_base import text, parse_time

CONTRACT_KINDS = ("whole_round_recusal", "independent_absolute")


def _scope_pairs(
    raw: dict[str, Any],
    reviewers: dict[str, dict[str, Any]],
    bids: dict[str, dict[str, Any]],
) -> set[tuple[str, str]] | None:
    scope = raw.get("scope")
    if scope is None:
        return None
    if not isinstance(scope, list):
        raise PanelError("PANEL_CONTRACT_UNAUTHORISED", "contract scope is invalid")
    pairs: set[tuple[str, str]] = set()
    for item in scope:
        if isinstance(item, dict):
            reviewer_id = text(item.get("reviewer_id"), "scope reviewer_id")
            bid_id = text(item.get("bid_id"), "scope bid_id")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            reviewer_id = text(item[0], "scope reviewer_id")
            bid_id = text(item[1], "scope bid_id")
        else:
            raise PanelError("PANEL_CONTRACT_UNAUTHORISED", "contract scope is invalid")
        if reviewer_id not in reviewers or bid_id not in bids:
            raise PanelError(
                "PANEL_CONTRACT_UNAUTHORISED", "contract scope identity is invalid"
            )
        pairs.add((reviewer_id, bid_id))
    return pairs


def _check_scope(
    kind: str,
    pairs: set[tuple[str, str]],
    conflicted: set[str],
    disclosed: set[tuple[str, str]],
    bids: dict[str, dict[str, Any]],
) -> None:
    if kind == "whole_round_recusal":
        if {reviewer for reviewer, _ in pairs} != conflicted:
            raise PanelError(
                "PANEL_CONTRACT_UNAUTHORISED",
                "whole-round scope must cover every conflicted reviewer",
            )
        for reviewer_id in conflicted:
            covered = {bid_id for owner, bid_id in pairs if owner == reviewer_id}
            if covered != set(bids):
                raise PanelError(
                    "PANEL_CONTRACT_UNAUTHORISED",
                    "whole-round scope must cover every bid of the round",
                )
    else:
        rogue = pairs - disclosed
        if rogue:
            raise PanelError(
                "PANEL_CONTRACT_UNAUTHORISED",
                "absolute scope may only strike disclosed bid pairs",
            )


def normalize_contract(
    raw: dict[str, Any],
    reviewers: dict[str, dict[str, Any]],
    disclosures: dict[str, dict[str, Any]],
    bids: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    contract_id = text(raw.get("contract_id"), "contract_id")
    kind = text(raw.get("kind"), "contract kind")
    if kind not in CONTRACT_KINDS:
        raise PanelError("PANEL_CONTRACT_UNAUTHORISED", f"unknown contract kind: {kind}")
    text(raw.get("authorized_by"), "contract authorized_by")
    authorized_at = text(raw.get("authorized_at"), "contract authorized_at")
    parse_time(authorized_at, "contract authorized_at")
    basis = raw.get("basis_disclosure_ids")
    if not isinstance(basis, list) or not basis:
        raise PanelError("PANEL_CONTRACT_UNAUTHORISED", "contract requires basis disclosures")
    basis_ids = sorted({text(item, "basis disclosure id") for item in basis})
    for disclosure_id in basis_ids:
        if disclosure_id not in disclosures:
            raise PanelError(
                "PANEL_CONTRACT_UNAUTHORISED", "contract cites unknown disclosure"
            )
    alternate_map = raw.get("alternate_map")
    if not isinstance(alternate_map, dict) or not alternate_map:
        raise PanelError("PANEL_CONTRACT_UNAUTHORISED", "contract requires alternate_map")
    conflicted = {disclosures[item]["reviewer_id"] for item in basis_ids}
    disclosed = {
        (disclosures[item]["reviewer_id"], disclosures[item]["bid_id"])
        for item in basis_ids
    }
    normalized_map: dict[str, str] = {}
    for reviewer_id, alternate_id in sorted(alternate_map.items()):
        if reviewer_id not in conflicted:
            raise PanelError(
                "PANEL_CONTRACT_UNAUTHORISED", "alternate_map has no conflict basis"
            )
        alternate = reviewers.get(str(alternate_id))
        if alternate is None or alternate["role"] != "alternate":
            raise PanelError(
                "PANEL_CONTRACT_UNAUTHORISED", "alternate reviewer is ineligible"
            )
        if alternate_id in conflicted:
            raise PanelError(
                "PANEL_CONTRACT_UNAUTHORISED", "alternate cannot be conflicted"
            )
        normalized_map[reviewer_id] = str(alternate_id)
    for reviewer_id in conflicted:
        if reviewer_id not in normalized_map:
            raise PanelError(
                "PANEL_CONTRACT_UNAUTHORISED", "conflicted reviewer has no alternate"
            )
    pairs = _scope_pairs(raw, reviewers, bids)
    if pairs is not None:
        _check_scope(kind, pairs, conflicted, disclosed, bids)
    return {
        "contract_id": contract_id,
        "kind": kind,
        "authorized_by": str(raw["authorized_by"]),
        "authorized_at": authorized_at,
        "basis_disclosure_ids": basis_ids,
        "alternate_map": normalized_map,
        "scope": sorted(pairs) if pairs is not None else None,
    }


def normalize_contracts(
    case: dict[str, Any],
    reviewers: dict[str, dict[str, Any]],
    disclosures: dict[str, dict[str, Any]],
    bids: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_contracts = case.get("contracts", [])
    if not isinstance(raw_contracts, list) or not all(
        isinstance(item, dict) for item in raw_contracts
    ):
        raise PanelError("PANEL_CASE_INVALID", "contracts must be objects")
    seen: set[str] = set()
    result = []
    for raw in raw_contracts:
        contract = normalize_contract(raw, reviewers, disclosures, bids)
        if contract["contract_id"] in seen:
            raise PanelError(
                "PANEL_CASE_INVALID",
                f"contract identity is invalid: {contract['contract_id']}",
            )
        seen.add(contract["contract_id"])
        result.append(contract)
    return result
