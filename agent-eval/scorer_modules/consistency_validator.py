"""Post-judge consistency validator — catches self-contradictions and invalid outputs.

Borrowed from Project W's idea: detect cases where numeric scores and textual
evidence disagree. Fail closed on invalid outputs instead of silent fallback.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from models import RubricBinaryItem, RubricDimensionScore, RubricReport

logger = logging.getLogger(__name__)


@dataclass
class ConsistencyIssue:
    issue_type: str  # "contradiction" | "missing_evidence" | "range_violation" | "empty_output"
    dimension_id: str
    description: str
    severity: str  # "critical" | "warning"
    original_score: float | None = None
    adjusted_score: float | None = None


@dataclass
class ConsistencyReport:
    issues: list[ConsistencyIssue] = field(default_factory=list)
    total_dimensions_checked: int = 0
    contradictions_found: int = 0
    adjustments_made: int = 0
    valid: bool = True

    @property
    def needs_rejudge(self) -> bool:
        return any(i.severity == "critical" for i in self.issues)

    def as_dict(self) -> dict:
        return {
            "valid": self.valid,
            "total_checked": self.total_dimensions_checked,
            "contradictions": self.contradictions_found,
            "adjustments": self.adjustments_made,
            "needs_rejudge": self.needs_rejudge,
            "issues": [
                {
                    "type": i.issue_type,
                    "dimension": i.dimension_id,
                    "description": i.description,
                    "severity": i.severity,
                }
                for i in self.issues
            ],
        }


_POSITIVE_INDICATORS = ["完成", "满足", "正确", "合规", "恰当", "清晰", "得体", "简洁"]
_NEGATIVE_INDICATORS = [
    "未",
    "没有",
    "缺失",
    "遗漏",
    "违反",
    "错误",
    "不当",
    "泄露",
    "跳过",
    "失败",
    "偏离",
]


def validate_rubric_consistency(rubric: RubricReport) -> ConsistencyReport:
    """Validate internal consistency of LLM judge rubric output."""
    report = ConsistencyReport()

    for dim in rubric.dimensions:
        report.total_dimensions_checked += 1
        _check_dimension(dim, report)

    for binary in rubric.binary_items:
        report.total_dimensions_checked += 1
        _check_binary(binary, report)

    if report.issues:
        report.valid = False
        report.contradictions_found = sum(
            1 for i in report.issues if i.issue_type == "contradiction"
        )

    return report


def _check_dimension(dim: RubricDimensionScore, report: ConsistencyReport) -> None:
    explanation = (dim.explanation or "").strip()

    if dim.score < 0 or dim.score > 5:
        report.issues.append(
            ConsistencyIssue(
                issue_type="range_violation",
                dimension_id=dim.dimension_id,
                description=f"Score {dim.score} outside valid range [0, 5]",
                severity="critical",
                original_score=dim.score,
            )
        )
        return

    if dim.score >= 4 and not dim.undertested:
        neg_count = sum(1 for ind in _NEGATIVE_INDICATORS if ind in explanation)
        if neg_count >= 2:
            report.issues.append(
                ConsistencyIssue(
                    issue_type="contradiction",
                    dimension_id=dim.dimension_id,
                    description=f"High score ({dim.score}/5) but explanation contains {neg_count} negative indicators",
                    severity="warning",
                    original_score=dim.score,
                )
            )
            report.adjustments_made += 1

    if dim.score <= 1 and not dim.undertested:
        pos_count = sum(1 for ind in _POSITIVE_INDICATORS if ind in explanation)
        neg_count = sum(1 for ind in _NEGATIVE_INDICATORS if ind in explanation)
        if pos_count >= 2 and neg_count == 0:
            report.issues.append(
                ConsistencyIssue(
                    issue_type="contradiction",
                    dimension_id=dim.dimension_id,
                    description=f"Low score ({dim.score}/5) but explanation is entirely positive ({pos_count} positive indicators, 0 negative)",
                    severity="warning",
                    original_score=dim.score,
                )
            )
            report.adjustments_made += 1

    if not explanation or explanation.startswith("["):
        if dim.score > 0:
            report.issues.append(
                ConsistencyIssue(
                    issue_type="missing_evidence",
                    dimension_id=dim.dimension_id,
                    description=f"Score {dim.score}/5 but no valid explanation provided",
                    severity="warning",
                    original_score=dim.score,
                )
            )


def _check_binary(binary: RubricBinaryItem, report: ConsistencyReport) -> None:
    explanation = (binary.explanation or "").strip()

    if binary.triggered and binary.value < 0:
        if not explanation or explanation.startswith("["):
            report.issues.append(
                ConsistencyIssue(
                    issue_type="missing_evidence",
                    dimension_id=binary.item_id,
                    description=f"Safety penalty triggered ({binary.value}) but no explanation provided",
                    severity="critical",
                )
            )

    if binary.triggered and binary.value < 0:
        pos_phrases = ["未", "没有", "不存在", "无"]
        denial_count = sum(1 for p in pos_phrases if p in explanation[:20])
        if denial_count >= 1 and "但" not in explanation[:30]:
            report.issues.append(
                ConsistencyIssue(
                    issue_type="contradiction",
                    dimension_id=binary.item_id,
                    description=f"Safety item triggered but explanation starts with denial ({explanation[:30]}...)",
                    severity="warning",
                )
            )
