"""Tests for coverage_guided module — coverage gap analysis and targeted scenario generation."""

import sys
from pathlib import Path

# Ensure agent-eval root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coverage_guided import (
    CoverageGapReport,
    analyze_coverage_gaps,
    format_gap_report,
    generate_targeted_scenarios,
    run_coverage_loop,
)
from models import ToolFault
from models_outbound import (
    Branch,
    CallContext,
    CalleePersona,
    ForbiddenBehavior,
    InstructionStep,
    OutboundScenario,
    OutboundScoreReport,
    PersonaArchetype,
    StepComplianceEntry,
)

# ── Fixtures ──


def _make_scenario(
    num_steps: int = 3,
    with_branches: bool = True,
    with_forbidden: bool = True,
    with_faults: bool = False,
    persona: PersonaArchetype | None = None,
) -> OutboundScenario:
    """Build a minimal but valid OutboundScenario for testing."""
    steps = []
    for i in range(1, num_steps + 1):
        branches = []
        if with_branches and i == 2:
            branches = [
                Branch(condition="用户接受", next_step=f"step_{i + 1}", description="接受方案"),
                Branch(condition="用户拒绝", next_step=f"step_{i + 1}", description="拒绝方案"),
            ]
        steps.append(
            InstructionStep(
                step_id=f"step_{i}",
                order=i,
                instruction=f"测试步骤 {i}: 执行操作 {i}",
                required_actions=[f"action_{i}"],
                branches=branches,
            )
        )

    forbidden = []
    if with_forbidden:
        forbidden = [
            ForbiddenBehavior(
                id="fb_1",
                description="禁止承诺全额退款",
                severity="major",
                detection_keywords=["全额退款", "全部退还"],
            ),
            ForbiddenBehavior(
                id="fb_2",
                description="禁止泄露骑手电话",
                severity="critical",
                detection_keywords=["骑手电话", "配送员手机"],
            ),
        ]

    faults = []
    if with_faults:
        faults = [
            ToolFault(
                tool_name="query_order",
                trigger_turn=2,
                fault_type="timeout",
                description="查询超时",
            ),
        ]

    callee = CalleePersona()
    if persona:
        callee.archetype = persona

    return OutboundScenario(
        id="test_scenario",
        name="测试场景",
        description="用于单元测试的场景",
        call_purpose="测试覆盖率引导",
        call_context=CallContext(
            order_id="MT202605170050",
            customer_name="张先生",
            delivery_address="测试地址",
            rider_name="李师傅",
            merchant_name="测试商家",
        ),
        instruction_steps=steps,
        forbidden_behaviors=forbidden,
        callee_persona=callee,
        tool_faults=faults,
        expected_steps_completed=[s.step_id for s in steps],
    )


def _make_report(
    scenario_id: str = "test_scenario",
    steps_completed: list[str] | None = None,
    branches_taken: dict[str, str] | None = None,
    forbidden_violations: list[dict] | None = None,
) -> OutboundScoreReport:
    """Build a minimal OutboundScoreReport for testing."""
    compliance = []
    for sid in steps_completed or []:
        entry = StepComplianceEntry(
            step_id=sid,
            instruction=f"步骤 {sid}",
            status="completed",
            turn=1,
        )
        if branches_taken and sid in branches_taken:
            entry.branch_taken = branches_taken[sid]
        compliance.append(entry)

    return OutboundScoreReport(
        scenario_id=scenario_id,
        step_compliance=compliance,
        forbidden_violations=forbidden_violations or [],
    )


# ── analyze_coverage_gaps ──


class TestAnalyzeCoverageGaps:
    def test_zero_coverage_no_reports(self):
        """No reports at all -> everything is a gap (except tool_fault_coverage
        which defaults to 1.0 when no faults are defined)."""
        scenario = _make_scenario()
        gaps = analyze_coverage_gaps(scenario, [])
        # tool_fault_coverage defaults to 1.0 when no faults, contributing 0.20 weight
        # so overall is 0.2 not 0.0 — but step/branch/constraint are all 0
        assert gaps.current_coverage <= 0.25
        assert len(gaps.gaps) > 0
        # Should have step gaps for all steps
        step_gaps = [g for g in gaps.gaps if g.dimension == "step"]
        assert len(step_gaps) == len(scenario.instruction_steps)

    def test_full_step_coverage(self):
        """All steps completed -> no step gaps."""
        scenario = _make_scenario(num_steps=3, with_branches=False, with_forbidden=False)
        report = _make_report(
            steps_completed=["step_1", "step_2", "step_3"],
        )
        gaps = analyze_coverage_gaps(scenario, [report])
        step_gaps = [g for g in gaps.gaps if g.dimension == "step"]
        assert len(step_gaps) == 0

    def test_partial_step_coverage(self):
        """Only 2 of 3 steps completed -> 1 step gap."""
        scenario = _make_scenario(num_steps=3, with_branches=False, with_forbidden=False)
        report = _make_report(steps_completed=["step_1", "step_2"])
        gaps = analyze_coverage_gaps(scenario, [report])
        step_gaps = [g for g in gaps.gaps if g.dimension == "step"]
        assert len(step_gaps) == 1
        assert step_gaps[0].item_id == "step_3"

    def test_branch_gaps_detected(self):
        """Branches exist but only one taken -> the other is a gap."""
        scenario = _make_scenario(num_steps=3, with_branches=True, with_forbidden=False)
        report = _make_report(
            steps_completed=["step_1", "step_2", "step_3"],
            branches_taken={"step_2": "用户接受"},
        )
        gaps = analyze_coverage_gaps(scenario, [report])
        branch_gaps = [g for g in gaps.gaps if g.dimension == "branch"]
        # At least the "用户拒绝" branch should be a gap
        reject_gaps = [g for g in branch_gaps if "用户拒绝" in g.item_id]
        assert len(reject_gaps) >= 1

    def test_constraint_gaps_detected(self):
        """Forbidden behaviors exist but none triggered -> constraint gaps."""
        scenario = _make_scenario(with_forbidden=True, with_branches=False)
        report = _make_report(steps_completed=["step_1", "step_2", "step_3"])
        gaps = analyze_coverage_gaps(scenario, [report])
        constraint_gaps = [g for g in gaps.gaps if g.dimension == "constraint"]
        assert len(constraint_gaps) == 2  # fb_1 and fb_2

    def test_constraint_partially_triggered(self):
        """One constraint triggered, one not."""
        scenario = _make_scenario(with_forbidden=True, with_branches=False)
        report = _make_report(
            steps_completed=["step_1", "step_2", "step_3"],
            forbidden_violations=[{"behavior_id": "fb_1", "turn": 3, "evidence": "test"}],
        )
        gaps = analyze_coverage_gaps(scenario, [report])
        constraint_gaps = [g for g in gaps.gaps if g.dimension == "constraint"]
        # fb_1 was triggered, fb_2 was not
        ids = {g.item_id for g in constraint_gaps}
        assert "fb_2" in ids
        assert "fb_1" not in ids

    def test_persona_gaps_included(self):
        """Should suggest untested persona archetypes."""
        scenario = _make_scenario(
            persona=PersonaArchetype.COOPERATIVE, with_branches=False, with_forbidden=False
        )
        report = _make_report(steps_completed=["step_1", "step_2", "step_3"])
        gaps = analyze_coverage_gaps(scenario, [report])
        persona_gaps = [g for g in gaps.gaps if g.dimension == "persona"]
        # At least WARY, IMPATIENT, STUBBORN, BOUNDARY should be suggested
        assert len(persona_gaps) >= 4
        gap_personas = {g.item_id for g in persona_gaps}
        assert PersonaArchetype.WARY in gap_personas
        assert PersonaArchetype.IMPATIENT in gap_personas

    def test_tool_fault_gaps(self):
        """Tool faults without reports -> fault gaps."""
        scenario = _make_scenario(with_faults=True, with_branches=False, with_forbidden=False)
        gaps = analyze_coverage_gaps(scenario, [])
        fault_gaps = [g for g in gaps.gaps if g.dimension == "tool_fault"]
        assert len(fault_gaps) == 1
        assert "query_order" in fault_gaps[0].item_id

    def test_gap_count_by_dimension(self):
        """gap_count_by_dimension correctly tallies gaps."""
        scenario = _make_scenario()
        gaps = analyze_coverage_gaps(scenario, [])
        for dim, count in gaps.gap_count_by_dimension.items():
            actual = len([g for g in gaps.gaps if g.dimension == dim])
            assert actual == count

    def test_scenario_id_propagated(self):
        """scenario_id should be set in the gap report."""
        scenario = _make_scenario()
        gaps = analyze_coverage_gaps(scenario, [])
        assert gaps.scenario_id == "test_scenario"


# ── generate_targeted_scenarios ──


class TestGenerateTargetedScenarios:
    def test_generates_up_to_max(self):
        """Should not exceed max_scenarios."""
        scenario = _make_scenario()
        gaps = analyze_coverage_gaps(scenario, [])
        generated = generate_targeted_scenarios(scenario, gaps, max_scenarios=2)
        assert len(generated) <= 2

    def test_branch_gap_gets_matching_persona(self):
        """Branch gap with '用户拒绝' should produce a WARY or STUBBORN persona."""
        scenario = _make_scenario(with_branches=True, with_forbidden=False)
        report = _make_report(
            steps_completed=["step_1", "step_2", "step_3"],
            branches_taken={"step_2": "用户接受"},
        )
        gaps = analyze_coverage_gaps(scenario, [report])

        # Filter to only branch gaps for targeted generation
        branch_only = CoverageGapReport(
            scenario_id=gaps.scenario_id,
            current_coverage=gaps.current_coverage,
            gaps=[g for g in gaps.gaps if g.dimension == "branch"],
            gap_count_by_dimension={
                "branch": len([g for g in gaps.gaps if g.dimension == "branch"])
            },
            suggested_scenario_count=1,
        )
        generated = generate_targeted_scenarios(scenario, branch_only, max_scenarios=3)
        assert len(generated) >= 1

        # The generated scenario should have a persona that triggers "用户拒绝"
        for sc in generated:
            if sc.callee_persona.archetype:
                assert sc.callee_persona.archetype in (
                    PersonaArchetype.WARY,
                    PersonaArchetype.STUBBORN,
                )

    def test_constraint_gap_produces_boundary_persona(self):
        """Constraint about '全额退款' should suggest BOUNDARY or STUBBORN."""
        scenario = _make_scenario(with_forbidden=True, with_branches=False)
        report = _make_report(steps_completed=["step_1", "step_2", "step_3"])
        gaps = analyze_coverage_gaps(scenario, [report])

        constraint_only = CoverageGapReport(
            scenario_id=gaps.scenario_id,
            current_coverage=gaps.current_coverage,
            gaps=[g for g in gaps.gaps if g.dimension == "constraint"],
            gap_count_by_dimension={
                "constraint": len([g for g in gaps.gaps if g.dimension == "constraint"])
            },
            suggested_scenario_count=2,
        )
        generated = generate_targeted_scenarios(scenario, constraint_only, max_scenarios=3)
        assert len(generated) >= 1

        for sc in generated:
            if sc.callee_persona.archetype:
                assert sc.callee_persona.archetype in (
                    PersonaArchetype.BOUNDARY,
                    PersonaArchetype.STUBBORN,
                    PersonaArchetype.IMPATIENT,
                    PersonaArchetype.WARY,
                )

    def test_generated_scenario_is_valid(self):
        """Generated scenarios must pass OutboundScenario.validate()."""
        scenario = _make_scenario()
        gaps = analyze_coverage_gaps(scenario, [])
        generated = generate_targeted_scenarios(scenario, gaps, max_scenarios=3)
        for sc in generated:
            errors = sc.validate()
            assert errors == [], f"Generated scenario {sc.id} has validation errors: {errors}"

    def test_generated_scenario_has_unique_id(self):
        """Each generated scenario should have a unique ID."""
        scenario = _make_scenario()
        gaps = analyze_coverage_gaps(scenario, [])
        generated = generate_targeted_scenarios(scenario, gaps, max_scenarios=5)
        ids = [sc.id for sc in generated]
        assert len(ids) == len(set(ids)), f"Duplicate IDs found: {ids}"

    def test_base_scenario_not_modified(self):
        """Generating scenarios should not modify the base scenario (deepcopy)."""
        scenario = _make_scenario(persona=PersonaArchetype.COOPERATIVE)
        original_persona = scenario.callee_persona.archetype
        gaps = analyze_coverage_gaps(scenario, [])
        generate_targeted_scenarios(scenario, gaps, max_scenarios=3)
        # Base scenario's persona should be unchanged
        assert scenario.callee_persona.archetype == original_persona

    def test_empty_gaps_no_generation(self):
        """No gaps -> no scenarios generated."""
        scenario = _make_scenario(num_steps=1, with_branches=False, with_forbidden=False)
        empty_gaps = CoverageGapReport(
            scenario_id=scenario.id,
            current_coverage=1.0,
            gaps=[],
            gap_count_by_dimension={},
            suggested_scenario_count=0,
        )
        generated = generate_targeted_scenarios(scenario, empty_gaps, max_scenarios=5)
        assert len(generated) == 0

    def test_max_scenarios_zero(self):
        """max_scenarios=0 -> no scenarios generated."""
        scenario = _make_scenario()
        gaps = analyze_coverage_gaps(scenario, [])
        generated = generate_targeted_scenarios(scenario, gaps, max_scenarios=0)
        assert len(generated) == 0


# ── run_coverage_loop ──


class TestRunCoverageLoop:
    def test_already_at_target_no_generation(self):
        """If coverage >= target, should return immediately without generating."""
        scenario = _make_scenario(num_steps=2, with_branches=False, with_forbidden=False)
        report = _make_report(steps_completed=["step_1", "step_2"])
        result = run_coverage_loop(scenario, [report], max_iterations=3, target_coverage=0.1)
        # Coverage should be >= 0.1 with all steps covered
        assert len(result.generated_scenarios) == 0
        assert result.initial_coverage >= 0.1

    def test_generates_scenarios_when_below_target(self):
        """If coverage < target, should generate scenarios."""
        scenario = _make_scenario(num_steps=3)
        result = run_coverage_loop(scenario, [], max_iterations=3, target_coverage=0.8)
        assert len(result.generated_scenarios) > 0
        assert result.initial_coverage < result.final_coverage or result.initial_coverage == 0.0

    def test_respects_max_iterations(self):
        """Should not exceed max_iterations."""
        scenario = _make_scenario(num_steps=5)
        result = run_coverage_loop(scenario, [], max_iterations=1, target_coverage=1.0)
        assert result.iterations <= 1

    def test_returns_remaining_gaps(self):
        """remaining_gaps should list gaps that weren't covered."""
        scenario = _make_scenario(num_steps=5)
        result = run_coverage_loop(scenario, [], max_iterations=1, target_coverage=1.0)
        # With zero reports, there should be many remaining gaps
        assert len(result.remaining_gaps) > 0

    def test_loop_result_structure(self):
        """CoverageLoopResult should have all expected fields."""
        scenario = _make_scenario()
        result = run_coverage_loop(scenario, [], max_iterations=2, target_coverage=0.9)
        assert isinstance(result.iterations, int)
        assert isinstance(result.initial_coverage, float)
        assert isinstance(result.final_coverage, float)
        assert isinstance(result.generated_scenarios, list)
        assert isinstance(result.remaining_gaps, list)


# ── format_gap_report ──


class TestFormatGapReport:
    def test_format_output_readable(self):
        """Should produce a readable multi-line string."""
        scenario = _make_scenario()
        gaps = analyze_coverage_gaps(scenario, [])
        text = format_gap_report(gaps)
        assert "覆盖率缺口分析" in text
        assert "当前覆盖率" in text
        assert "缺口总数" in text

    def test_format_includes_dimensions(self):
        """Should include dimension breakdown."""
        scenario = _make_scenario()
        gaps = analyze_coverage_gaps(scenario, [])
        text = format_gap_report(gaps)
        assert "按维度分布" in text

    def test_format_empty_report(self):
        """Should handle an empty report gracefully."""
        report = CoverageGapReport(
            scenario_id="empty",
            current_coverage=1.0,
            gaps=[],
            gap_count_by_dimension={},
            suggested_scenario_count=0,
        )
        text = format_gap_report(report)
        assert "empty" in text
        assert "缺口总数: 0" in text
