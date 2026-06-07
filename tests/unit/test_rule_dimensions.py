"""Tests for rule-based dimension routing (_compute_rule_dimensions)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent-eval"))

from models import CheckResult
from scorer_outbound import _compute_rule_dimensions


def _check(check_id, dimension, score, passed=True):
    return CheckResult(
        check_id=check_id,
        check_type="rule",
        dimension=dimension,
        description="",
        passed=passed,
        score=score,
    )


def test_d1_maps_step_compliance():
    rubric = _compute_rule_dimensions(
        [], step_score=0.8, branch_score=None, turn_efficiency=0.7, violations=[]
    )
    d1 = next(d for d in rubric.dimensions if d.dimension_id == "D1")
    assert d1.score == 4  # round(0.8 * 5) = 4


def test_d1_zero_step_score():
    rubric = _compute_rule_dimensions(
        [], step_score=0.0, branch_score=None, turn_efficiency=0.5, violations=[]
    )
    d1 = next(d for d in rubric.dimensions if d.dimension_id == "D1")
    assert d1.score == 0


def test_d4_na_when_cooperative():
    rubric = _compute_rule_dimensions(
        [], step_score=0.9, branch_score=None, turn_efficiency=0.5, violations=[]
    )
    d4 = next(d for d in rubric.dimensions if d.dimension_id == "D4")
    assert d4.undertested is True


def test_d4_scored_when_branch_exists():
    rubric = _compute_rule_dimensions(
        [], step_score=0.5, branch_score=0.8, turn_efficiency=0.5, violations=[]
    )
    d4 = next(d for d in rubric.dimensions if d.dimension_id == "D4")
    assert d4.undertested is False
    assert d4.score >= 0


def test_d6_penalizes_safety_violations():
    viols = [{"behavior_id": "x", "severity": "critical"}]
    rubric_clean = _compute_rule_dimensions(
        [], step_score=0.5, branch_score=None, turn_efficiency=0.5, violations=[]
    )
    rubric_dirty = _compute_rule_dimensions(
        [], step_score=0.5, branch_score=None, turn_efficiency=0.5, violations=viols
    )
    d6_clean = next(d for d in rubric_clean.dimensions if d.dimension_id == "D6")
    d6_dirty = next(d for d in rubric_dirty.dimensions if d.dimension_id == "D6")
    assert d6_dirty.score <= d6_clean.score


def test_all_six_dimensions_present():
    rubric = _compute_rule_dimensions(
        [], step_score=0.5, branch_score=None, turn_efficiency=0.5, violations=[]
    )
    ids = {d.dimension_id for d in rubric.dimensions}
    assert ids == {"D1", "D2", "D3", "D4", "D5", "D6"}


def test_none_turn_efficiency():
    rubric = _compute_rule_dimensions(
        [], step_score=0.5, branch_score=None, turn_efficiency=None, violations=[]
    )
    d5 = next(d for d in rubric.dimensions if d.dimension_id == "D5")
    assert 0 <= d5.score <= 5


def test_identity_check_routes_to_d2():
    checks = [_check("identity_confirmation", "compliance", 1.0)]
    rubric = _compute_rule_dimensions(
        checks, step_score=0.5, branch_score=None, turn_efficiency=0.5, violations=[]
    )
    d2 = next(d for d in rubric.dimensions if d.dimension_id == "D2")
    assert d2.score >= 3


def test_rubric_report_grade():
    rubric = _compute_rule_dimensions(
        [], step_score=1.0, branch_score=None, turn_efficiency=1.0, violations=[]
    )
    assert rubric.grade in ("优秀", "合格", "需改进", "严重不合格")
    assert rubric.rubric_max > 0
