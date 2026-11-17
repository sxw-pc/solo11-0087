"""Attribute result notices to specific tender rounds with evidence levels.

Two independent business authorizations read the same stored evidence:

- 严格台账 (strict ledger): only the local explicit attribution list or the
  result notice's own declared reference may close a tender round.
- 辅助核验 (auxiliary verification): additionally accepts an inferred
  attribution when exactly one candidate round matches on project code,
  purchaser, publish order, and correction relations.

Inference is never written as a source-portal declaration; every conclusion
carries its evidence level and, when unresolved, a stable reason code.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from .models import (
    AttributionEntry,
    AttributionRecord,
    CorrectionRecord,
    NoticeRecord,
    RoundCandidateRecord,
)

EVIDENCE_DECLARED = "declared"
EVIDENCE_EXPLICIT_LIST = "explicit_list"
EVIDENCE_INFERRED = "inferred"
EVIDENCE_NONE = "none"

STATUS_CONFIRMED = "confirmed"
STATUS_CONFLICT = "conflict"
STATUS_PENDING = "pending"

ROUND_CLOSED = "closed"
ROUND_PENDING = "pending"

EVIDENCE_LABELS = {
    EVIDENCE_DECLARED: "公告自身引用",
    EVIDENCE_EXPLICIT_LIST: "本地显式清单",
    EVIDENCE_INFERRED: "推断归属",
    EVIDENCE_NONE: "无",
}
STATUS_LABELS = {
    STATUS_CONFIRMED: "已确认",
    STATUS_CONFLICT: "冲突",
    STATUS_PENDING: "待核",
}
ROUND_LABELS = {ROUND_CLOSED: "已关闭", ROUND_PENDING: "待核"}

STRICT_EVIDENCE = {EVIDENCE_DECLARED, EVIDENCE_EXPLICIT_LIST}
AUXILIARY_EVIDENCE = STRICT_EVIDENCE | {EVIDENCE_INFERRED}
_EVIDENCE_RANK = {EVIDENCE_DECLARED: 3, EVIDENCE_EXPLICIT_LIST: 2, EVIDENCE_INFERRED: 1}


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _time_leq(earlier: str | None, later: str | None) -> bool:
    left, right = _parse(earlier), _parse(later)
    return left is not None and right is not None and left <= right


def _record(
    result_id: str,
    declared: str,
    listed: str,
    inferred: str,
    chosen: str,
    evidence: str,
    status: str,
    reason: str,
    detail: str,
) -> AttributionRecord:
    closed = status == STATUS_CONFIRMED
    strict = ROUND_CLOSED if closed and evidence in STRICT_EVIDENCE else ROUND_PENDING
    auxiliary = ROUND_CLOSED if closed and evidence in AUXILIARY_EVIDENCE else ROUND_PENDING
    return AttributionRecord(
        result_notice_id=result_id,
        declared_notice_id=declared,
        listed_notice_id=listed,
        inferred_notice_id=inferred,
        chosen_notice_id=chosen,
        evidence_level=evidence,
        status=status,
        reason_code=reason,
        strict_status=strict,
        auxiliary_status=auxiliary,
        detail=detail,
    )


def _decide(
    result: NoticeRecord,
    declared: str,
    listed: str,
    eligible: list[str],
    candidates: list[RoundCandidateRecord],
    by_id: dict[str, NoticeRecord],
) -> AttributionRecord:
    rid = result.notice_id
    if declared and listed and declared != listed:
        return _record(
            rid, declared, listed, "", "", EVIDENCE_NONE, STATUS_CONFLICT,
            "ATTRIBUTION_SOURCE_CONFLICT",
            f"公告自身引用 {declared} 与本地清单 {listed} 不一致，保持冲突，不任选其一",
        )
    explicit = declared or listed
    if explicit:
        evidence = EVIDENCE_DECLARED if declared else EVIDENCE_EXPLICIT_LIST
        origin = "公告自身引用" if declared else "本地显式清单"
        target = by_id.get(explicit)
        if target is None:
            return _record(
                rid, declared, listed, "", "", EVIDENCE_NONE, STATUS_PENDING,
                "RESULT_TENDER_MISSING", f"{origin}指向的原招标 {explicit} 不在公告集中",
            )
        if target.kind != "tender":
            return _record(
                rid, declared, listed, "", "", EVIDENCE_NONE, STATUS_CONFLICT,
                "ATTRIBUTION_TARGET_MISMATCH",
                f"{origin}指向的 {explicit} 不是招标公告（{target.kind_label}）",
            )
        if target.project_code != result.project_code or target.purchaser != result.purchaser:
            return _record(
                rid, declared, listed, "", "", EVIDENCE_NONE, STATUS_CONFLICT,
                "ATTRIBUTION_TARGET_MISMATCH",
                f"{origin}指向的轮次 {explicit} 与结果的项目编号或采购单位不一致",
            )
        if not _time_leq(target.publish_at, result.publish_at):
            return _record(
                rid, declared, listed, "", "", EVIDENCE_NONE, STATUS_CONFLICT,
                "RESULT_BEFORE_TENDER",
                f"结果发布时间早于{origin}指向的招标轮次 {explicit}",
            )
        note = f"{origin}确认归属招标轮次 {explicit}"
        if declared and listed:
            note += "（本地清单与公告引用一致）"
        return _record(
            rid, declared, listed, "", explicit, evidence, STATUS_CONFIRMED, "", note,
        )
    if len(eligible) == 1:
        return _record(
            rid, "", "", eligible[0], eligible[0], EVIDENCE_INFERRED, STATUS_CONFIRMED,
            "",
            f"同项目编号、同采购单位、发布顺序与更正关系一致的唯一候选 {eligible[0]}；"
            "推断归属，并非源公告声明",
        )
    if len(eligible) > 1:
        return _record(
            rid, "", "", "", "", EVIDENCE_NONE, STATUS_PENDING,
            "RESULT_CANDIDATES_AMBIGUOUS",
            "存在多个一致候选轮次（" + "、".join(eligible) + "），仅凭项目编号不能裁定",
        )
    same_project = [c for c in candidates if c.same_project_code and c.same_purchaser]
    if same_project and all(not c.publish_order_ok for c in same_project):
        return _record(
            rid, "", "", "", "", EVIDENCE_NONE, STATUS_PENDING,
            "RESULT_BEFORE_TENDER", "结果发布时间早于全部同项目招标轮次",
        )
    if same_project:
        return _record(
            rid, "", "", "", "", EVIDENCE_NONE, STATUS_PENDING,
            "RESULT_TENDER_MISSING",
            "同项目轮次的更正发布时间晚于结果，更正关系不一致，无可用归属轮次",
        )
    return _record(
        rid, "", "", "", "", EVIDENCE_NONE, STATUS_PENDING,
        "RESULT_TENDER_MISSING", "公告集中没有同项目编号、同采购单位的招标轮次",
    )


def attribute_results(
    notices: Sequence[NoticeRecord],
    corrections: Sequence[CorrectionRecord],
    entries: Sequence[AttributionEntry],
) -> tuple[list[RoundCandidateRecord], list[AttributionRecord]]:
    """Compute round candidates and one attribution conclusion per result."""
    by_id = {notice.notice_id: notice for notice in notices}
    tenders = sorted(
        (notice for notice in notices if notice.kind == "tender"),
        key=lambda notice: notice.notice_id,
    )
    listed = {entry.result_notice_id: entry.tender_notice_id for entry in entries}
    corrections_by_original: dict[str, list[CorrectionRecord]] = {}
    for correction in corrections:
        corrections_by_original.setdefault(correction.original_notice_id, []).append(
            correction
        )
    candidates_out: list[RoundCandidateRecord] = []
    attributions: list[AttributionRecord] = []
    results = sorted(
        (notice for notice in notices if notice.kind == "result"),
        key=lambda notice: notice.notice_id,
    )
    for result in results:
        candidates: list[RoundCandidateRecord] = []
        for tender in tenders:
            same_code = bool(result.project_code) and (
                tender.project_code == result.project_code
            )
            same_purchaser = bool(result.purchaser) and (
                tender.purchaser == result.purchaser
            )
            order_ok = _time_leq(tender.publish_at, result.publish_at)
            corrections_ok = all(
                _time_leq(item.published_at, result.publish_at)
                for item in corrections_by_original.get(tender.notice_id, ())
            )
            candidates.append(
                RoundCandidateRecord(
                    result_notice_id=result.notice_id,
                    tender_notice_id=tender.notice_id,
                    same_project_code=same_code,
                    same_purchaser=same_purchaser,
                    publish_order_ok=order_ok,
                    corrections_ok=corrections_ok,
                    eligible=same_code and same_purchaser and order_ok and corrections_ok,
                )
            )
        candidates_out.extend(candidates)
        declared = result.related_notice_id.strip()
        eligible = [item.tender_notice_id for item in candidates if item.eligible]
        attributions.append(
            _decide(
                result, declared, listed.get(result.notice_id, ""), eligible,
                candidates, by_id,
            )
        )
    return candidates_out, attributions


def _mode_conclusion(
    tender_id: str,
    attributions: Iterable[Mapping[str, Any]],
    accepted: set[str],
) -> dict[str, Any]:
    closing = [
        item
        for item in attributions
        if item["chosen_notice_id"] == tender_id
        and item["status"] == STATUS_CONFIRMED
        and item["evidence_level"] in accepted
    ]
    closing.sort(key=lambda item: item["result_notice_id"])
    if not closing:
        return {"status": ROUND_PENDING, "result_notice_ids": [], "evidence_level": None}
    strongest = max(closing, key=lambda item: _EVIDENCE_RANK[item["evidence_level"]])
    return {
        "status": ROUND_CLOSED,
        "result_notice_ids": [item["result_notice_id"] for item in closing],
        "evidence_level": strongest["evidence_level"],
    }


def conclude_rounds(
    tenders: Sequence[Mapping[str, Any]],
    corrections: Sequence[Mapping[str, Any]],
    attributions: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Per tender round, the strict-ledger and auxiliary conclusions."""
    rounds: list[dict[str, Any]] = []
    for tender in sorted(tenders, key=lambda item: item["notice_id"]):
        correction_ids = sorted(
            item["correction_notice_id"]
            for item in corrections
            if item["original_notice_id"] == tender["notice_id"]
        )
        rounds.append(
            {
                "tender_notice_id": tender["notice_id"],
                "project_code": tender["project_code"],
                "purchaser": tender["purchaser"],
                "publish_at": tender["publish_at"],
                "corrections": correction_ids,
                "strict": _mode_conclusion(tender["notice_id"], attributions, STRICT_EVIDENCE),
                "auxiliary": _mode_conclusion(
                    tender["notice_id"], attributions, AUXILIARY_EVIDENCE
                ),
            }
        )
    return rounds
