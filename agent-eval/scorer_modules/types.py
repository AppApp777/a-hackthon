"""Typed scoring primitives — every objective point must have evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class EvidenceRef:
    """Pointer to a specific verifiable event in the evaluation trace."""

    source: Literal[
        "harness_event",
        "tool_result",
        "db_state",
        "policy_graph",
        "llm_judge",
        "rule_check",
        "transcript",
    ]
    description: str
    turn: int | None = None
    ledger_index: int | None = None
    event_hash: str | None = None


@dataclass(frozen=True)
class ScoreAtom:
    """Atomic scoring unit — the smallest indivisible piece of a score."""

    key: str
    category: Literal[
        "hard_metric",
        "step_compliance",
        "branch_accuracy",
        "temporal_order",
        "path_alignment",
        "soft_quality",
        "safety_veto",
    ]
    max_points: float
    awarded_points: float
    decision: Literal["pass", "fail", "partial", "veto", "not_applicable"]
    reason: str
    evidence: tuple[EvidenceRef, ...] = ()
    failure_mode: str | None = None

    @property
    def score_ratio(self) -> float:
        if self.max_points == 0:
            return 0.0
        return self.awarded_points / self.max_points


@dataclass
class ScoreBreakdown:
    """Aggregated score from multiple ScoreAtoms."""

    atoms: list[ScoreAtom] = field(default_factory=list)

    @property
    def total_awarded(self) -> float:
        return sum(a.awarded_points for a in self.atoms)

    @property
    def total_max(self) -> float:
        return sum(a.max_points for a in self.atoms)

    @property
    def overall_ratio(self) -> float:
        if self.total_max == 0:
            return 0.0
        return self.total_awarded / self.total_max

    def atoms_by_category(self, category: str) -> list[ScoreAtom]:
        return [a for a in self.atoms if a.category == category]

    def failed_atoms(self) -> list[ScoreAtom]:
        return [a for a in self.atoms if a.decision in ("fail", "veto")]

    def has_critical_failure(self) -> bool:
        return any(a.decision == "veto" for a in self.atoms)


def assert_objective_atoms_have_evidence(atoms: list[ScoreAtom]) -> None:
    """Contract: objective scoring atoms must have at least one evidence ref."""
    objective_categories = {
        "hard_metric",
        "step_compliance",
        "branch_accuracy",
        "temporal_order",
        "path_alignment",
    }
    for atom in atoms:
        if atom.category in objective_categories and atom.decision != "not_applicable":
            if not atom.evidence:
                raise ValueError(
                    f"ScoreAtom '{atom.key}' ({atom.category}) has decision='{atom.decision}' "
                    f"but no evidence. Objective atoms must have evidence."
                )


def assert_no_failed_awards_success(atoms: list[ScoreAtom]) -> None:
    """Contract: atoms with fail/veto decision must not award points."""
    for atom in atoms:
        if atom.decision in ("fail", "veto") and atom.awarded_points > 0:
            raise ValueError(
                f"ScoreAtom '{atom.key}' has decision='{atom.decision}' "
                f"but awarded_points={atom.awarded_points}. Failed atoms must not award points."
            )
