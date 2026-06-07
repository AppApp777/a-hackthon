"""tests/judge_moat - the curated 'moat' suite.

These are SIGNAGE tests: the few that would fail if our central claim were false.
The claim: a scored point must be backed by EXECUTABLE EVIDENCE (a ledger event
and the matching world-state change), not by what the transcript says happened.

They share the exact fixtures and helpers used by `agent-eval/scripts/judge_demo.py`,
so the demo can never silently drift from the tests. No API, no network, no LLM judge.

Run:  PYTHONPATH=agent-eval python -m pytest tests/judge_moat -q
"""

import sys
from pathlib import Path

_AE = Path(__file__).resolve().parents[2] / "agent-eval"
sys.path.insert(0, str(_AE))
sys.path.insert(0, str(_AE / "scripts"))

import judge_demo as jd  # noqa: E402

IDX = jd._scenario_index()


def test_genuine_execution_is_not_falsely_vetoed():
    """A trace whose tools really executed (DB written) must score high and NOT be
    vetoed - proving the veto discriminates rather than punishing everything."""
    rep, led, _conv, _db = jd.score_case(jd._load(jd.DEFAULT_GOOD), IDX)
    assert rep.overall_score_100 >= 80
    assert rep.gate_type == "none"
    assert not led.has_fabricated


def test_self_reported_action_without_ledger_is_capped_to_zero():
    """A real trace where the agent claims a refund it never executed: no ledger
    event, no DB row -> non-compensatory fabrication veto -> 0."""
    rep, led, _conv, _db = jd.score_case(jd._load(jd.DEFAULT_BAD), IDX)
    assert led.has_fabricated
    assert rep.gate_type == "zero"
    assert rep.overall_score_100 == 0


def test_metamorphic_same_dialogue_only_evidence_differs():
    """Hold the visible dialogue byte-identical, remove ONLY the hidden execution
    evidence. The same scorer must collapse the score. Identical words, opposite
    scores, because we score execution - not transcript. This is the moat."""
    good = jd._load(jd.DEFAULT_GOOD)
    rep_g, _lg, conv_g, _dg = jd.score_case(good, IDX)
    rep_m, _lm, conv_m, _dm = jd.score_case(jd.mutate_remove_evidence(good), IDX)
    assert jd.visible_dialogue_sha256(conv_g) == jd.visible_dialogue_sha256(conv_m)
    assert rep_g.overall_score_100 >= 80 and rep_g.gate_type == "none"
    assert rep_m.overall_score_100 == 0 and rep_m.gate_type == "zero"
    assert rep_g.overall_score_100 - rep_m.overall_score_100 >= 60


def test_veto_is_non_compensatory():
    """The mutated trace keeps a high pre-veto (evidence-layer) score because the
    dialogue quality is unchanged - yet the final score is 0. Fluency, politeness,
    and structure cannot offset a fabricated business action."""
    rep_m, _lm, _cm, _dm = jd.score_case(jd.mutate_remove_evidence(jd._load(jd.DEFAULT_GOOD)), IDX)
    assert jd._pre_veto(rep_m) >= 40  # dialogue-quality credit survives
    assert rep_m.overall_score_100 == 0  # but the veto caps the final score
