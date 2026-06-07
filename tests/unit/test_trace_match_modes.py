"""Unit tests for trace matching modes (Phase 3.3 — Strands Evals borrowing).

Three modes: strict (exact order+content), ordered (DP edit distance), unordered (set match).
"""

from policy_graph import PolicyGraph, StepNode
from trace_verifier import (
    ObservedStep,
    align_sequences,
    align_strict,
    align_unordered,
)


def _make_graph(step_ids: list[str]) -> PolicyGraph:
    """Minimal graph with the given step IDs as expected path."""
    nodes = {
        sid: StepNode(step_id=sid, order=i, instruction=f"step {sid}")
        for i, sid in enumerate(step_ids)
    }
    g = PolicyGraph.__new__(PolicyGraph)
    g.scenario_id = "test"
    g.nodes = nodes
    g.edges = {}
    g.constraints = []
    g.atoms = []
    g.expected_path = step_ids
    g.exit_nodes = [step_ids[-1]] if step_ids else []
    return g


def _make_observed(step_ids: list[str]) -> list[ObservedStep]:
    return [
        ObservedStep(step_id=sid, first_turn=i + 1, last_turn=i + 1, confidence=1.0)
        for i, sid in enumerate(step_ids)
    ]


class TestStrictMode:
    def test_exact_match(self):
        expected = ["open", "confirm", "close"]
        observed = _make_observed(["open", "confirm", "close"])
        graph = _make_graph(expected)
        cost, ops = align_strict(expected, observed, graph)
        assert cost == 0.0
        assert all(op.op_type == "match" for op in ops)

    def test_wrong_order_fails(self):
        expected = ["open", "confirm", "close"]
        observed = _make_observed(["confirm", "open", "close"])
        graph = _make_graph(expected)
        cost, ops = align_strict(expected, observed, graph)
        assert cost > 0.0
        subs = [op for op in ops if op.op_type == "substitute"]
        assert len(subs) >= 1

    def test_missing_step(self):
        expected = ["open", "confirm", "close"]
        observed = _make_observed(["open", "close"])
        graph = _make_graph(expected)
        cost, ops = align_strict(expected, observed, graph)
        assert cost > 0.0

    def test_extra_step(self):
        expected = ["open", "close"]
        observed = _make_observed(["open", "extra", "close"])
        graph = _make_graph(expected)
        cost, ops = align_strict(expected, observed, graph)
        assert cost > 0.0


class TestOrderedMode:
    def test_exact_match(self):
        expected = ["open", "confirm", "close"]
        observed = _make_observed(["open", "confirm", "close"])
        graph = _make_graph(expected)
        cost, ops = align_sequences(expected, observed, graph)
        assert cost == 0.0

    def test_reordered_partial_cost(self):
        """DP alignment should find best alignment even with reordering."""
        expected = ["open", "confirm", "close"]
        observed = _make_observed(["open", "close", "confirm"])
        graph = _make_graph(expected)
        cost, _ = align_sequences(expected, observed, graph)
        assert cost > 0.0


class TestUnorderedMode:
    def test_all_present_any_order(self):
        expected = ["open", "confirm", "close"]
        observed = _make_observed(["close", "open", "confirm"])
        graph = _make_graph(expected)
        cost, ops = align_unordered(expected, observed, graph)
        assert cost == 0.0
        matches = [op for op in ops if op.op_type == "match"]
        assert len(matches) == 3

    def test_missing_step(self):
        expected = ["open", "confirm", "close"]
        observed = _make_observed(["open", "close"])
        graph = _make_graph(expected)
        cost, ops = align_unordered(expected, observed, graph)
        assert cost > 0.0
        deletes = [op for op in ops if op.op_type == "delete_expected"]
        assert any("confirm" in (op.expected_step or "") for op in deletes)

    def test_extra_step_penalized(self):
        expected = ["open", "close"]
        observed = _make_observed(["open", "extra", "close"])
        graph = _make_graph(expected)
        cost, ops = align_unordered(expected, observed, graph)
        assert cost > 0.0
        inserts = [op for op in ops if op.op_type == "insert_observed"]
        assert len(inserts) == 1

    def test_empty_expected(self):
        expected = []
        observed = _make_observed(["open", "close"])
        graph = _make_graph(expected)
        cost, ops = align_unordered(expected, observed, graph)
        assert cost > 0.0

    def test_empty_both(self):
        expected = []
        observed = _make_observed([])
        graph = _make_graph(expected)
        cost, ops = align_unordered(expected, observed, graph)
        assert cost == 0.0
