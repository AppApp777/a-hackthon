"""Tests for expanded persona archetypes (5→12) and new CLI features."""

from models_outbound import CalleePersona, PersonaArchetype
from user_sim_outbound import infer_archetype


class TestExpandedPersonas:
    def test_all_12_archetypes_exist(self):
        assert len(PersonaArchetype) == 12

    def test_confused_archetype_inferred(self):
        persona = CalleePersona(comprehension=2, trust_level=6)
        assert infer_archetype(persona) == PersonaArchetype.CONFUSED

    def test_red_team_from_adversarial_mode(self):
        persona = CalleePersona(adversarial_mode=True)
        assert infer_archetype(persona) == PersonaArchetype.RED_TEAM

    def test_all_archetypes_have_pressure_floor(self):
        from user_sim_outbound import _PRESSURE_FLOOR

        for archetype in PersonaArchetype:
            assert archetype in _PRESSURE_FLOOR, f"Missing pressure floor for {archetype}"


class TestMockAgent:
    def test_mock_agent_import(self):
        from mock_agent import MockAgentOutbound

        assert MockAgentOutbound is not None


class TestBranchEnumeration:
    def test_enumerate_branches_import(self):
        from eval_coverage import enumerate_graph_branches

        assert callable(enumerate_graph_branches)

    def test_policy_graph_enumerate(self):
        from policy_graph import EdgeType, GraphEdge, PolicyGraph, StepNode

        graph = PolicyGraph(scenario_id="test")
        graph.nodes = {
            "A": StepNode(step_id="A", order=0, instruction="step A"),
            "B": StepNode(step_id="B", order=1, instruction="step B"),
            "C": StepNode(step_id="C", order=2, instruction="step C"),
        }
        graph.edges = [
            GraphEdge(source="A", target="B", edge_type=EdgeType.BRANCH, condition="yes"),
            GraphEdge(source="A", target="C", edge_type=EdgeType.BRANCH, condition="no"),
            GraphEdge(source="B", target="C", edge_type=EdgeType.SEQUENTIAL),
        ]
        branches = graph.enumerate_branches()
        assert len(branches) == 2
        assert ("A", "yes", "B") in branches
        assert ("A", "no", "C") in branches


class TestPearsonR:
    def test_pearson_r_in_oracle_metrics(self):
        import json

        with open("agent-eval/calibration/oracle_agreement_metrics.json") as f:
            metrics = json.load(f)
        assert "pearson_r" in metrics["overall"]
        assert "pearson_r" in metrics["per_dimension"]["D1"]
