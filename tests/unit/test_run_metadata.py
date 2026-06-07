"""Tests for reproducibility metadata (RunMetadata enrichment).

Competitor-parity: records 谁打的分 (judge model), 谁扮演客户 (simulator model),
batch id, evaluator version, and timing — see docs/competitor_gap.md A4/B5/C7.

Truthfulness contracts:
- judge_model_id is None when the LLM judge did not run (use_llm_judge=False).
- seed is None until the LLM layer actually transports a sampling seed.
"""

from pathlib import Path

import pytest
from models import EVALUATOR_VERSION, RunMetadata
from models_outbound import OutboundScenario
from orchestrator_outbound import OutboundOrchestrator

SCN_PATH = (
    Path(__file__).parent.parent.parent
    / "agent-eval"
    / "scenarios"
    / "outbound"
    / "after_sales_complaint.json"
)


@pytest.fixture
def scenario():
    return OutboundScenario.model_validate_json(SCN_PATH.read_text(encoding="utf-8"))


class TestRunMetadataSchema:
    def test_new_fields_exist_with_defaults(self):
        m = RunMetadata()
        # Reproducibility fields present
        for field in (
            "run_id",
            "evaluator_version",
            "judge_model_id",
            "judge_model_secondary_id",
            "simulator_model_id",
            "seed",
            "self_consistency_n",
            "use_llm_judge",
            "started_at",
            "finished_at",
            "duration_seconds",
        ):
            assert field in m.model_dump(), f"missing field {field}"

    def test_run_id_is_unique(self):
        assert RunMetadata().run_id != RunMetadata().run_id

    def test_evaluator_version_recorded(self):
        assert RunMetadata().evaluator_version == EVALUATOR_VERSION

    def test_seed_defaults_none_honest(self):
        # We do not yet transport an LLM seed — must stay None, not a fake value.
        assert RunMetadata().seed is None

    def test_self_consistency_defaults_one(self):
        assert RunMetadata().self_consistency_n == 1


class TestOrchestratorBuildsMetadata:
    def _build(self, scenario, *, use_llm_judge):
        orch = OutboundOrchestrator(
            scenario,
            use_llm_judge=use_llm_judge,
            trace_dir=str(Path(__file__).parent.parent.parent / "agent-eval" / "traces" / "_test"),
            agent_type="baseline",
            agent_model="claude-sonnet-4-6",
        )
        return orch._build_run_metadata()

    def test_target_model_recorded(self, scenario):
        rm = self._build(scenario, use_llm_judge=True)
        assert rm.model_backend == "claude-sonnet-4-6"

    def test_judge_recorded_when_enabled(self, scenario):
        rm = self._build(scenario, use_llm_judge=True)
        # judge + simulator resolved from llm config; must be non-empty when judge runs
        assert rm.judge_model_id
        assert rm.simulator_model_id
        # PoLL secondary always runs (judges.py:354-357) — must be recorded, never None when judge on
        assert rm.judge_model_secondary_id

    def test_judge_none_when_disabled(self, scenario):
        # Truthful: no LLM judge ran → no judge model recorded.
        rm = self._build(scenario, use_llm_judge=False)
        assert rm.judge_model_id is None
        assert rm.judge_model_secondary_id is None
        assert rm.use_llm_judge is False

    def test_duration_computed_from_start(self, scenario):
        from datetime import datetime, timedelta

        orch = OutboundOrchestrator(
            scenario,
            use_llm_judge=False,
            trace_dir=str(Path(__file__).parent.parent.parent / "agent-eval" / "traces" / "_test"),
            agent_type="baseline",
        )
        orch._run_started_at = datetime.now() - timedelta(seconds=5)
        rm = orch._build_run_metadata()
        assert rm.duration_seconds is not None
        assert rm.duration_seconds >= 5.0
        assert rm.started_at is not None
        assert rm.finished_at is not None
