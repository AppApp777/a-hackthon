"""Tests for the deterministic replay API."""

from __future__ import annotations

import json

from replay import ReplayConfig, ReplayReport, replay_and_score, replay_batch


def _make_minimal_trace(
    overall_score: float = 0.318,
    hard_score: float = 0.475,
    step_score: float = 0.333,
    branch_score: float = 0.0,
    temporal_score: float = 0.875,
    path_score: float = 0.378,
) -> dict:
    """Create a minimal but valid trace for replay testing."""
    return {
        "id": "test-trace-001",
        "scenario": {
            "id": "outbound_aftersales_01",
            "name": "test scenario",
            "instruction_steps": [{"step_id": "open", "order": 1}],
            "forbidden_behaviors": [],
            "must_call_tools": ["query_order"],
            "expected_call_result": "refunded",
        },
        "conversation": {"messages": []},
        "score_report": {
            "overall_score": overall_score,
            "hard_score": hard_score,
            "step_compliance_score": step_score,
            "branch_accuracy_score": branch_score,
            "task_outcome": {"status": "failed"},
            "checks": [
                {
                    "check_id": "opening",
                    "check_type": "rule",
                    "dimension": "speech_protocol",
                    "description": "test",
                    "passed": True,
                    "score": 1.0,
                    "explanation": "",
                },
                {
                    "check_id": "forbidden",
                    "check_type": "rule",
                    "dimension": "forbidden_behavior",
                    "description": "test",
                    "passed": False,
                    "score": 0.0,
                    "explanation": "",
                },
                {
                    "check_id": "outcome",
                    "check_type": "rule",
                    "dimension": "outcome",
                    "description": "test",
                    "passed": False,
                    "score": 0.0,
                    "explanation": "",
                },
                {
                    "check_id": "tool",
                    "check_type": "rule",
                    "dimension": "tool_usage",
                    "description": "test",
                    "passed": True,
                    "score": 0.75,
                    "explanation": "",
                },
                {
                    "check_id": "efficiency",
                    "check_type": "rule",
                    "dimension": "efficiency",
                    "description": "test",
                    "passed": True,
                    "score": 1.0,
                    "explanation": "",
                },
            ],
            "constraint_ledger": [],
            "rubric": {"binary_items": []},
            "failure_summary": [],
        },
        "metadata": {
            "outbound_report": {
                "step_compliance_score": step_score,
                "branch_accuracy_score": branch_score,
                "temporal_order_score": temporal_score,
                "alignment_score": path_score,
            },
        },
    }


class TestReplayAndScore:
    def test_basic_replay(self, tmp_path):
        trace = _make_minimal_trace()
        path = tmp_path / "outbound_test.json"
        path.write_text(json.dumps(trace), encoding="utf-8")

        report = replay_and_score(path)
        assert isinstance(report, ReplayReport)
        assert report.trace_id == "test-trace-001"
        assert report.scenario_id == "outbound_aftersales_01"
        assert report.replayed_score > 0

    def test_score_components_extracted(self, tmp_path):
        trace = _make_minimal_trace()
        path = tmp_path / "outbound_test.json"
        path.write_text(json.dumps(trace), encoding="utf-8")

        report = replay_and_score(path)
        assert "hard_score" in report.components
        assert "step_score" in report.components

    def test_scenario_hash_computed(self, tmp_path):
        trace = _make_minimal_trace()
        path = tmp_path / "outbound_test.json"
        path.write_text(json.dumps(trace), encoding="utf-8")

        report = replay_and_score(path)
        assert len(report.scenario_hash) == 16

    def test_same_trace_same_hash(self, tmp_path):
        trace = _make_minimal_trace()
        p1 = tmp_path / "outbound_a.json"
        p2 = tmp_path / "outbound_b.json"
        p1.write_text(json.dumps(trace), encoding="utf-8")
        p2.write_text(json.dumps(trace), encoding="utf-8")

        r1 = replay_and_score(p1)
        r2 = replay_and_score(p2)
        assert r1.scenario_hash == r2.scenario_hash
        assert r1.replayed_score == r2.replayed_score

    def test_different_scenario_different_hash(self, tmp_path):
        t1 = _make_minimal_trace()
        t2 = _make_minimal_trace()
        t2["scenario"]["id"] = "different_scenario"

        p1 = tmp_path / "outbound_a.json"
        p2 = tmp_path / "outbound_b.json"
        p1.write_text(json.dumps(t1), encoding="utf-8")
        p2.write_text(json.dumps(t2), encoding="utf-8")

        r1 = replay_and_score(p1)
        r2 = replay_and_score(p2)
        assert r1.scenario_hash != r2.scenario_hash

    def test_ablation_override(self, tmp_path):
        trace = _make_minimal_trace()
        path = tmp_path / "outbound_test.json"
        path.write_text(json.dumps(trace), encoding="utf-8")

        normal = replay_and_score(path)
        ablated = replay_and_score(path, ReplayConfig(ablation_overrides={"step_score": 1.0}))
        assert ablated.replayed_score >= normal.replayed_score

    def test_strict_mode_catches_mismatch(self, tmp_path):
        trace = _make_minimal_trace(overall_score=0.999)
        path = tmp_path / "outbound_test.json"
        path.write_text(json.dumps(trace), encoding="utf-8")

        report = replay_and_score(path, ReplayConfig(strict=True))
        assert not report.score_match
        assert any("mismatch" in e.lower() for e in report.errors)

    def test_missing_checks_returns_error(self, tmp_path):
        trace = {"id": "bad", "scenario": {"id": "x"}, "score_report": {}, "metadata": {}}
        path = tmp_path / "outbound_bad.json"
        path.write_text(json.dumps(trace), encoding="utf-8")

        report = replay_and_score(path)
        assert report.replayed_score == 0.0
        assert len(report.errors) > 0


class TestReplayBatch:
    def test_batch_replay(self, tmp_path):
        for i in range(3):
            trace = _make_minimal_trace()
            trace["id"] = f"trace-{i}"
            path = tmp_path / f"outbound_{i:04d}.json"
            path.write_text(json.dumps(trace), encoding="utf-8")

        reports = replay_batch(tmp_path)
        assert len(reports) == 3
        assert all(isinstance(r, ReplayReport) for r in reports)

    def test_batch_ignores_non_trace_files(self, tmp_path):
        trace = _make_minimal_trace()
        (tmp_path / "outbound_001.json").write_text(json.dumps(trace), encoding="utf-8")
        (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
        (tmp_path / "readme.md").write_text("test", encoding="utf-8")

        reports = replay_batch(tmp_path)
        assert len(reports) == 1
