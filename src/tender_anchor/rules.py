"""Quality rules applied after normalization and persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Sequence

from .config import RuleSettings
from .models import (
    AttachmentRecord,
    AttributionRecord,
    CorrectionRecord,
    NoticeRecord,
)

FIELD_LABELS = {
    "notice_id": "公告编号",
    "title": "标题",
    "project_code": "项目编号",
    "region": "地区",
    "purchaser": "采购单位",
    "agency": "代理机构",
    "publish_at": "发布时间",
    "deadline_at": "截止时间",
    "budget_amount": "预算金额",
    "award_amount": "成交金额",
    "winner": "成交供应商",
    "related_notice_id": "关联原公告",
}
RESULT_OPTIONAL = {"deadline_at"}
ATTRIBUTION_SEVERITY = {"conflict": "error", "pending": "warning"}


@dataclass(frozen=True)
class RuleFinding:
    notice_id: str
    rule_code: str
    severity: str
    message: str
    field_name: str = ""

    def to_store_dict(self) -> dict[str, str]:
        return {
            "notice_id": self.notice_id,
            "rule_code": self.rule_code,
            "severity": self.severity,
            "message": self.message,
            "field_name": self.field_name,
        }

    def to_dict(self) -> dict[str, str]:
        return self.to_store_dict()


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _parsed_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _dedupe(findings: Iterable[RuleFinding]) -> list[RuleFinding]:
    chosen: dict[tuple[str, str], RuleFinding] = {}
    for finding in findings:
        chosen.setdefault((finding.notice_id, finding.rule_code), finding)
    return sorted(chosen.values(), key=lambda item: (item.notice_id, item.rule_code))


def apply_rules(
    notices: Sequence[NoticeRecord],
    attachments: Sequence[AttachmentRecord],
    corrections: Sequence[CorrectionRecord],
    settings: RuleSettings,
    attributions: Sequence[AttributionRecord] = (),
) -> list[RuleFinding]:
    findings: list[RuleFinding] = []
    known_ids = {notice.notice_id for notice in notices}
    failed_by_notice: dict[str, list[str]] = {}
    for attachment in attachments:
        if attachment.status != "stored":
            failed_by_notice.setdefault(attachment.notice_id, []).append(
                f"{attachment.filename}({attachment.error_code or 'UNKNOWN'})"
            )
    for notice in notices:
        optional = RESULT_OPTIONAL if notice.kind == "result" else set()
        for field_name in settings.required_fields:
            if field_name in optional:
                continue
            if _is_blank(getattr(notice, field_name, None)):
                label = FIELD_LABELS.get(field_name, field_name)
                findings.append(
                    RuleFinding(
                        notice_id=notice.notice_id,
                        rule_code=f"MISSING_{field_name.upper()}",
                        severity="warning",
                        message=f"缺少必要字段：{label}",
                        field_name=field_name,
                    )
                )
        if notice.budget_amount is not None:
            try:
                budget = Decimal(notice.budget_amount)
            except (InvalidOperation, TypeError):
                budget = None
            if budget is not None and not settings.min_budget <= budget <= settings.max_budget:
                findings.append(
                    RuleFinding(
                        notice_id=notice.notice_id,
                        rule_code="BUDGET_OUT_OF_RANGE",
                        severity="warning",
                        message=(
                            f"预算金额 {notice.budget_amount} 超出规则区间 "
                            f"{settings.min_budget}..{settings.max_budget}"
                        ),
                        field_name="budget_amount",
                    )
                )
        publish = _parsed_time(notice.publish_at)
        deadline = _parsed_time(notice.deadline_at)
        if publish and deadline and deadline < publish:
            findings.append(
                RuleFinding(
                    notice_id=notice.notice_id,
                    rule_code="DEADLINE_BEFORE_PUBLISH",
                    severity="error",
                    message="截止时间早于发布时间",
                    field_name="deadline_at",
                )
            )
        if notice.notice_id in failed_by_notice:
            findings.append(
                RuleFinding(
                    notice_id=notice.notice_id,
                    rule_code="ATTACHMENT_FETCH_FAILED",
                    severity="error",
                    message="附件获取失败：" + "，".join(sorted(failed_by_notice[notice.notice_id])),
                    field_name="attachments",
                )
            )
        if notice.kind == "result" and (_is_blank(notice.award_amount) or _is_blank(notice.winner)):
            findings.append(
                RuleFinding(
                    notice_id=notice.notice_id,
                    rule_code="RESULT_AWARD_MISSING",
                    severity="warning",
                    message="结果公告缺少成交金额或成交供应商",
                    field_name="award_amount",
                )
            )
    for correction in corrections:
        if correction.original_notice_id not in known_ids:
            findings.append(
                RuleFinding(
                    notice_id=correction.correction_notice_id,
                    rule_code="CORRECTION_ORIGINAL_MISSING",
                    severity="error",
                    message=f"更正公告关联的原公告不存在：{correction.original_notice_id}",
                    field_name="related_notice_id",
                )
            )
        if not correction.changes:
            findings.append(
                RuleFinding(
                    notice_id=correction.correction_notice_id,
                    rule_code="CORRECTION_CHANGES_EMPTY",
                    severity="warning",
                    message="更正公告未提取到任何更正事项",
                    field_name="correction_changes",
                )
            )
    findings.extend(_attribution_findings(notices, corrections, attributions))
    return _dedupe(findings)


def _attribution_findings(
    notices: Sequence[NoticeRecord],
    corrections: Sequence[CorrectionRecord],
    attributions: Sequence[AttributionRecord],
) -> list[RuleFinding]:
    """缺结果/歧义/归属冲突复核项，由归属证据驱动。"""
    findings: list[RuleFinding] = []
    confirmed_by_tender: dict[str, list[AttributionRecord]] = {}
    for item in attributions:
        if item.status == "confirmed" and item.chosen_notice_id:
            confirmed_by_tender.setdefault(item.chosen_notice_id, []).append(item)
        else:
            severity = ATTRIBUTION_SEVERITY.get(item.status) or "warning"
            findings.append(
                RuleFinding(
                    notice_id=item.result_notice_id,
                    rule_code=item.reason_code or "ATTRIBUTION_PENDING",
                    severity=severity,
                    message=item.detail or "结果公告归属待核",
                    field_name="related_notice_id",
                )
            )
    for notice in notices:
        if notice.kind != "tender":
            continue
        closing = confirmed_by_tender.get(notice.notice_id, [])
        if any(item.evidence_level in {"declared", "explicit_list"} for item in closing):
            continue
        inferred = sorted(
            item.result_notice_id for item in closing if item.evidence_level == "inferred"
        )
        if inferred:
            findings.append(
                RuleFinding(
                    notice_id=notice.notice_id,
                    rule_code="TENDER_RESULT_INFERRED",
                    severity="warning",
                    message=(
                        "结果归属为推断证据，严格台账待人工确认："
                        + "、".join(inferred)
                    ),
                    field_name="related_notice_id",
                )
            )
        else:
            findings.append(
                RuleFinding(
                    notice_id=notice.notice_id,
                    rule_code="TENDER_RESULT_MISSING",
                    severity="warning",
                    message="招标轮次缺少可信结果公告（严格台账与辅助核验均未关闭）",
                    field_name="related_notice_id",
                )
            )
    result_ids = {notice.notice_id for notice in notices if notice.kind == "result"}
    for correction in corrections:
        if correction.original_notice_id in result_ids:
            findings.append(
                RuleFinding(
                    notice_id=correction.original_notice_id,
                    rule_code="RESULT_SELF_REVISED",
                    severity="warning",
                    message=(
                        f"结果公告自身被更正公告 {correction.correction_notice_id} 修订，"
                        "归属与成交信息需复核"
                    ),
                    field_name="related_notice_id",
                )
            )
    return findings
