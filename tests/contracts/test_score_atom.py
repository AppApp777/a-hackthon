"""Contract tests for ScoreAtom evidence pipeline."""

from __future__ import annotations

import pytest
from scorer_modules.types import (
    EvidenceRef,
    ScoreAtom,
    ScoreBreakdown,
    assert_no_failed_awards_success,
    assert_objective_atoms_have_evidence,
)


def _evidence(desc: str = "test") -> tuple[EvidenceRef, ...]:
    return (EvidenceRef(source="rule_check", description=desc),)


class TestScoreAtom:
    def test_pass_atom(self):
        atom = ScoreAtom(
            key="opening",
            category="hard_metric",
            max_points=1.0,
            awarded_points=1.0,
            decision="pass",
            reason="OK",
            evidence=_evidence(),
        )
        assert atom.score_ratio == 1.0

    def test_fail_atom_zero_score(self):
        atom = ScoreAtom(
            key="forbidden",
            category="hard_metric",
            max_points=1.0,
            awarded_points=0.0,
            decision="fail",
            reason="violation",
            evidence=_evidence(),
        )
        assert atom.score_ratio == 0.0

    def test_partial_atom(self):
        atom = ScoreAtom(
            key="tool_usage",
            category="hard_metric",
            max_points=1.0,
            awarded_points=0.75,
            decision="partial",
            reason="3/4 tools",
            evidence=_evidence(),
        )
        assert atom.score_ratio == 0.75

    def test_veto_atom(self):
        atom = ScoreAtom(
            key="safety",
            category="safety_veto",
            max_points=0.0,
            awarded_points=0.0,
            decision="veto",
            reason="critical violation",
            evidence=_evidence(),
        )
        assert atom.decision == "veto"


class TestScoreBreakdown:
    def test_total_awarded(self):
        bd = ScoreBreakdown(
            atoms=[
                ScoreAtom("a", "hard_metric", 1.0, 1.0, "pass", "ok", _evidence()),
                ScoreAtom("b", "hard_metric", 1.0, 0.5, "partial", "half", _evidence()),
            ]
        )
        assert bd.total_awarded == 1.5
        assert bd.total_max == 2.0
        assert bd.overall_ratio == 0.75

    def test_failed_atoms(self):
        bd = ScoreBreakdown(
            atoms=[
                ScoreAtom("a", "hard_metric", 1.0, 1.0, "pass", "ok", _evidence()),
                ScoreAtom("b", "hard_metric", 1.0, 0.0, "fail", "bad", _evidence()),
                ScoreAtom("c", "safety_veto", 0.0, 0.0, "veto", "critical", _evidence()),
            ]
        )
        failed = bd.failed_atoms()
        assert len(failed) == 2
        assert bd.has_critical_failure()

    def test_atoms_by_category(self):
        bd = ScoreBreakdown(
            atoms=[
                ScoreAtom("a", "hard_metric", 1.0, 1.0, "pass", "ok", _evidence()),
                ScoreAtom("b", "step_compliance", 1.0, 0.5, "partial", "ok", _evidence()),
                ScoreAtom("c", "hard_metric", 1.0, 0.0, "fail", "bad", _evidence()),
            ]
        )
        hard = bd.atoms_by_category("hard_metric")
        assert len(hard) == 2


class TestContracts:
    def test_objective_atoms_must_have_evidence(self):
        atoms = [
            ScoreAtom("opening", "hard_metric", 1.0, 1.0, "pass", "ok"),
        ]
        with pytest.raises(ValueError, match="no evidence"):
            assert_objective_atoms_have_evidence(atoms)

    def test_objective_atoms_with_evidence_pass(self):
        atoms = [
            ScoreAtom("opening", "hard_metric", 1.0, 1.0, "pass", "ok", _evidence()),
        ]
        assert_objective_atoms_have_evidence(atoms)

    def test_not_applicable_atoms_skip_evidence_check(self):
        atoms = [
            ScoreAtom("optional_step", "step_compliance", 1.0, 0.0, "not_applicable", "skipped"),
        ]
        assert_objective_atoms_have_evidence(atoms)

    def test_soft_quality_atoms_dont_need_evidence(self):
        atoms = [
            ScoreAtom("D1", "soft_quality", 5.0, 4.0, "pass", "good"),
        ]
        assert_objective_atoms_have_evidence(atoms)

    def test_failed_atom_cannot_award_points(self):
        atoms = [
            ScoreAtom("bad", "hard_metric", 1.0, 0.5, "fail", "bad", _evidence()),
        ]
        with pytest.raises(ValueError, match="must not award"):
            assert_no_failed_awards_success(atoms)

    def test_pass_atom_can_award_points(self):
        atoms = [
            ScoreAtom("good", "hard_metric", 1.0, 1.0, "pass", "ok", _evidence()),
        ]
        assert_no_failed_awards_success(atoms)

    def test_veto_atom_cannot_award_points(self):
        atoms = [
            ScoreAtom("safety", "safety_veto", 1.0, 0.5, "veto", "critical", _evidence()),
        ]
        with pytest.raises(ValueError, match="must not award"):
            assert_no_failed_awards_success(atoms)
