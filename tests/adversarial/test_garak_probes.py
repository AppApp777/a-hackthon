"""Adversarial tests for Phase 4.2: Garak-inspired probe scenarios.

Validates that all 5 new adversarial scenarios (06-10) load correctly
and have proper structure for the evaluation harness.
"""

import json
from pathlib import Path

import pytest
from models_outbound import OutboundScenario

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "agent-eval" / "scenarios" / "outbound"

NEW_ADVERSARIAL_SCENARIOS = [
    "adversarial_prompt_injection.json",
    "adversarial_role_play.json",
    "adversarial_encoding_bypass.json",
    "adversarial_context_overflow.json",
    "adversarial_output_hijack.json",
]


class TestScenarioLoading:
    @pytest.mark.parametrize("filename", NEW_ADVERSARIAL_SCENARIOS)
    def test_scenario_file_exists(self, filename):
        path = SCENARIOS_DIR / filename
        assert path.exists(), f"{filename} not found in {SCENARIOS_DIR}"

    @pytest.mark.parametrize("filename", NEW_ADVERSARIAL_SCENARIOS)
    def test_scenario_valid_json(self, filename):
        path = SCENARIOS_DIR / filename
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    @pytest.mark.parametrize("filename", NEW_ADVERSARIAL_SCENARIOS)
    def test_scenario_parses_as_model(self, filename):
        path = SCENARIOS_DIR / filename
        data = json.loads(path.read_text(encoding="utf-8"))
        scenario = OutboundScenario(**data)
        assert scenario.id.startswith("outbound_adversarial_")


class TestScenarioStructure:
    @pytest.mark.parametrize("filename", NEW_ADVERSARIAL_SCENARIOS)
    def test_has_forbidden_behaviors(self, filename):
        path = SCENARIOS_DIR / filename
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "forbidden_behaviors" in data
        assert len(data["forbidden_behaviors"]) >= 3

    @pytest.mark.parametrize("filename", NEW_ADVERSARIAL_SCENARIOS)
    def test_has_callee_persona_with_hidden_agenda(self, filename):
        path = SCENARIOS_DIR / filename
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "callee_persona" in data
        assert "hidden_agenda" in data["callee_persona"]
        assert len(data["callee_persona"]["hidden_agenda"]) > 10

    @pytest.mark.parametrize("filename", NEW_ADVERSARIAL_SCENARIOS)
    def test_difficulty_is_hard_or_extreme(self, filename):
        path = SCENARIOS_DIR / filename
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data.get("difficulty") in ("hard", "extreme")

    @pytest.mark.parametrize("filename", NEW_ADVERSARIAL_SCENARIOS)
    def test_has_adversarial_step(self, filename):
        path = SCENARIOS_DIR / filename
        data = json.loads(path.read_text(encoding="utf-8"))
        steps = data.get("instruction_steps", [])
        adversarial_steps = [
            s
            for s in steps
            if any(
                kw in s.get("step_id", "")
                for kw in [
                    "injection",
                    "role_play",
                    "encoding",
                    "context_flood",
                    "output_hijack",
                    "handle_",
                ]
            )
        ]
        assert len(adversarial_steps) >= 1, f"No adversarial step found in {filename}"


class TestScenarioIds:
    def test_all_adversarial_ids_unique(self):
        ids = []
        for f in SCENARIOS_DIR.glob("adversarial_*.json"):
            data = json.loads(f.read_text(encoding="utf-8"))
            ids.append(data["id"])
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {ids}"

    def test_total_adversarial_count_at_least_8(self):
        count = len(list(SCENARIOS_DIR.glob("adversarial_*.json")))
        assert count >= 8, f"Only {count} adversarial scenarios, need at least 8"

    def test_new_ids_sequential(self):
        expected_ids = [f"outbound_adversarial_{i:02d}" for i in range(6, 11)]
        for eid, filename in zip(expected_ids, NEW_ADVERSARIAL_SCENARIOS, strict=False):
            path = SCENARIOS_DIR / filename
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["id"] == eid


class TestGarakProbeMapping:
    """Each new scenario maps to a specific Garak probe type."""

    PROBE_MAP = {
        "adversarial_prompt_injection.json": "promptinject",
        "adversarial_role_play.json": "dan",
        "adversarial_encoding_bypass.json": "encoding",
        "adversarial_context_overflow.json": "goodside",
        "adversarial_output_hijack.json": "sysprompt_extraction",
    }

    @pytest.mark.parametrize("filename", NEW_ADVERSARIAL_SCENARIOS)
    def test_scenario_has_callee_goal(self, filename):
        path = SCENARIOS_DIR / filename
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "callee_goal" in data
        assert len(data["callee_goal"]) > 20

    @pytest.mark.parametrize("filename", NEW_ADVERSARIAL_SCENARIOS)
    def test_scenario_has_must_not_do(self, filename):
        path = SCENARIOS_DIR / filename
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "must_not_do" in data
        assert len(data["must_not_do"]) >= 3
