"""Coverage-Guided Scenario Generation — closes the coverage loop.

Flow: compute_coverage() -> analyze_coverage_gaps() -> generate_targeted_scenarios()
     -> (user runs evaluation) -> repeat until target coverage reached.

Inspired by CGADS: coverage stats -> find uncovered regions -> generate targeted
scenarios -> re-evaluate -> loop.
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from eval_coverage import compute_coverage, cross_reference_branch_coverage
from models_outbound import (
    OutboundScenario,
    OutboundScoreReport,
    PersonaArchetype,
)

# ── Data Classes ──


@dataclass
class CoverageGap:
    """A single uncovered item in the evaluation surface."""

    dimension: str  # "step", "branch", "constraint", "persona", "tool_fault"
    item_id: str  # specific uncovered item ID
    description: str  # human-readable description
    suggested_persona: str | None = None  # recommended PersonaArchetype
    suggested_mutation: str | None = None  # recommended MutationType


@dataclass
class CoverageGapReport:
    """Structured report of all coverage gaps for a scenario."""

    scenario_id: str
    current_coverage: float
    gaps: list[CoverageGap] = field(default_factory=list)
    gap_count_by_dimension: dict[str, int] = field(default_factory=dict)
    suggested_scenario_count: int = 0


@dataclass
class CoverageLoopResult:
    """Result of running the coverage-guided generation loop."""

    iterations: int
    initial_coverage: float
    final_coverage: float  # estimated (no actual evaluation run)
    generated_scenarios: list[OutboundScenario] = field(default_factory=list)
    remaining_gaps: list[CoverageGap] = field(default_factory=list)


# ── Branch -> Persona Mapping ──
# Core intelligence: given an uncovered branch condition, which persona would
# naturally trigger it?

BRANCH_PERSONA_MAP: dict[str, list[PersonaArchetype]] = {
    "用户拒绝": [PersonaArchetype.WARY, PersonaArchetype.STUBBORN],
    "用户接受": [PersonaArchetype.COOPERATIVE],
    "用户犹豫": [PersonaArchetype.HESITANT],
    "用户忙碌": [PersonaArchetype.BUSY, PersonaArchetype.RUSHED],
    "用户质疑": [PersonaArchetype.WARY, PersonaArchetype.CONTRADICTORY],
    "用户发火": [PersonaArchetype.IMPATIENT, PersonaArchetype.BOUNDARY],
    "信号差": [PersonaArchetype.RUSHED],
    "不在家": [PersonaArchetype.BUSY, PersonaArchetype.RUSHED],
    "拒绝": [PersonaArchetype.WARY, PersonaArchetype.STUBBORN],
    "同意": [PersonaArchetype.COOPERATIVE],
    "犹豫": [PersonaArchetype.HESITANT],
    "选退款": [PersonaArchetype.COOPERATIVE, PersonaArchetype.IMPATIENT],
    "选补送": [PersonaArchetype.COOPERATIVE, PersonaArchetype.HESITANT],
    "都不满意": [PersonaArchetype.STUBBORN, PersonaArchetype.BOUNDARY],
    "挂断": [PersonaArchetype.BUSY, PersonaArchetype.IMPATIENT],
    "要求转人工": [PersonaArchetype.STUBBORN, PersonaArchetype.BOUNDARY],
    "情绪激动": [PersonaArchetype.IMPATIENT, PersonaArchetype.BOUNDARY],
    "不配合": [PersonaArchetype.STUBBORN, PersonaArchetype.WARY],
    "提供信息": [PersonaArchetype.COOPERATIVE],
    "拒绝提供": [PersonaArchetype.WARY, PersonaArchetype.STUBBORN],
}

# Constraint -> Persona: which persona naturally triggers a forbidden behavior check
CONSTRAINT_PERSONA_MAP: dict[str, list[PersonaArchetype]] = {
    "全额退款": [PersonaArchetype.BOUNDARY, PersonaArchetype.STUBBORN],
    "骑手": [PersonaArchetype.IMPATIENT, PersonaArchetype.WARY],
    "承诺": [PersonaArchetype.BOUNDARY],
    "补偿": [PersonaArchetype.BOUNDARY, PersonaArchetype.STUBBORN],
    "赔偿": [PersonaArchetype.BOUNDARY, PersonaArchetype.STUBBORN],
    "投诉": [PersonaArchetype.IMPATIENT],
    "隐私": [PersonaArchetype.WARY, PersonaArchetype.RED_TEAM],
    "电话": [PersonaArchetype.WARY],
}

# All available personas for rotation
ALL_PERSONAS = list(PersonaArchetype)


def _infer_persona_for_branch(condition: str) -> PersonaArchetype | None:
    """Infer the best persona archetype to trigger a branch condition."""
    condition_lower = condition.lower()
    for keyword, personas in BRANCH_PERSONA_MAP.items():
        if keyword in condition_lower:
            return random.choice(personas)
    return None


def _infer_persona_for_constraint(description: str) -> PersonaArchetype | None:
    """Infer a persona that would pressure the agent into violating a constraint."""
    desc_lower = description.lower()
    for keyword, personas in CONSTRAINT_PERSONA_MAP.items():
        if keyword in desc_lower:
            return random.choice(personas)
    return None


# ── Core Functions ──


def analyze_coverage_gaps(
    scenario: OutboundScenario,
    reports: list[OutboundScoreReport],
) -> CoverageGapReport:
    """Analyze coverage gaps: find uncovered steps, branches, constraints, personas, faults."""
    cov = compute_coverage(scenario, reports)
    gaps: list[CoverageGap] = []

    # 1. Uncovered steps
    tested_steps = set(cov.details.get("steps_tested", []))
    for step in scenario.instruction_steps:
        if step.step_id not in tested_steps:
            gaps.append(
                CoverageGap(
                    dimension="step",
                    item_id=step.step_id,
                    description=f"步骤未覆盖: {step.instruction[:60]}",
                    suggested_persona=PersonaArchetype.COOPERATIVE,
                    suggested_mutation=None,
                )
            )

    # 2. Uncovered branches (use cross_reference for accuracy)
    branch_result = cross_reference_branch_coverage(scenario, reports)
    for gap_branch in branch_result.get("gaps", []):
        condition = gap_branch.get("condition", "")
        source_id = gap_branch.get("source_id", "")
        target_id = gap_branch.get("target_id", "")
        persona = _infer_persona_for_branch(condition)
        gaps.append(
            CoverageGap(
                dimension="branch",
                item_id=f"{source_id}:{condition}",
                description=f"分支未覆盖: {source_id}({condition}) -> {target_id}",
                suggested_persona=persona.value if persona else None,
                suggested_mutation="flip_branch",
            )
        )

    # 3. Uncovered constraints (forbidden behaviors)
    triggered_constraints = set(cov.details.get("constraints_triggered", []))
    for fb in scenario.forbidden_behaviors:
        if fb.id not in triggered_constraints:
            persona = _infer_persona_for_constraint(fb.description)
            gaps.append(
                CoverageGap(
                    dimension="constraint",
                    item_id=fb.id,
                    description=f"约束未触发: {fb.description[:60]}",
                    suggested_persona=persona.value if persona else PersonaArchetype.BOUNDARY,
                    suggested_mutation="inject_forbidden",
                )
            )

    # 4. Uncovered personas
    tested_personas: set[str] = set()
    for _r in reports:
        # Try to infer from the report metadata — persona is set per-scenario
        # For now, check if the scenario had a persona archetype set
        pass
    # Since we track by scenario's callee_persona.archetype, check if reports
    # covered different archetypes. In practice, each report corresponds to one
    # persona. We collect the tested persona from the scenario itself when there
    # are reports.
    if scenario.callee_persona.archetype and reports:
        tested_personas.add(scenario.callee_persona.archetype)

    # Suggest at least a few diverse personas if coverage is low
    priority_personas = [
        PersonaArchetype.COOPERATIVE,
        PersonaArchetype.WARY,
        PersonaArchetype.IMPATIENT,
        PersonaArchetype.STUBBORN,
        PersonaArchetype.BOUNDARY,
    ]
    for pa in priority_personas:
        if pa not in tested_personas:
            gaps.append(
                CoverageGap(
                    dimension="persona",
                    item_id=pa,
                    description=f"画像未覆盖: {pa}",
                    suggested_persona=pa,
                    suggested_mutation="entity_swap",
                )
            )

    # 5. Uncovered tool faults
    if scenario.tool_faults and not reports:
        for fault in scenario.tool_faults:
            gaps.append(
                CoverageGap(
                    dimension="tool_fault",
                    item_id=f"{fault.tool_name}:{fault.fault_type}",
                    description=f"故障未测试: {fault.tool_name} ({fault.fault_type})",
                    suggested_persona=None,
                    suggested_mutation=None,
                )
            )

    # Build dimension counts
    dim_counts: dict[str, int] = {}
    for g in gaps:
        dim_counts[g.dimension] = dim_counts.get(g.dimension, 0) + 1

    # Suggest how many scenarios to generate: 1 per branch gap + 1 per uncovered persona,
    # capped at max_scenarios in the caller
    suggested = dim_counts.get("branch", 0) + dim_counts.get("persona", 0)
    suggested = max(1, min(suggested, 10))

    return CoverageGapReport(
        scenario_id=scenario.id,
        current_coverage=cov.overall_coverage,
        gaps=gaps,
        gap_count_by_dimension=dim_counts,
        suggested_scenario_count=suggested,
    )


def generate_targeted_scenarios(
    base_scenario: OutboundScenario,
    gaps: CoverageGapReport,
    max_scenarios: int = 5,
) -> list[OutboundScenario]:
    """Generate scenarios that target specific coverage gaps.

    Strategy priority:
    1. Branch gaps -> persona that triggers the branch condition
    2. Constraint gaps -> persona that pressures the agent into the constraint area
    3. Step gaps -> cooperative persona that reaches the step
    4. Persona gaps -> swap to the untested persona
    5. Tool fault gaps -> inject the missing fault
    """
    generated: list[OutboundScenario] = []
    used_personas: set[str] = set()

    # Sort gaps by priority: branch > constraint > step > persona > tool_fault
    priority_order = {"branch": 0, "constraint": 1, "step": 2, "persona": 3, "tool_fault": 4}
    sorted_gaps = sorted(gaps.gaps, key=lambda g: priority_order.get(g.dimension, 99))

    for gap in sorted_gaps:
        if len(generated) >= max_scenarios:
            break

        # Skip if we already have a scenario with this persona (avoid duplicates)
        target_persona = gap.suggested_persona
        if target_persona and target_persona in used_personas and gap.dimension == "persona":
            continue

        scenario = _create_scenario_for_gap(base_scenario, gap, len(generated) + 1)
        if scenario:
            generated.append(scenario)
            if target_persona:
                used_personas.add(target_persona)

    return generated


def _create_scenario_for_gap(
    base: OutboundScenario,
    gap: CoverageGap,
    index: int,
) -> OutboundScenario | None:
    """Create a new scenario variant targeting a specific coverage gap."""
    scenario = base.model_copy(deep=True)
    scenario.id = f"{base.id}_cg{index}"
    suffix = f" (覆盖率引导变体 #{index})"
    scenario.name = base.name + suffix

    if gap.dimension == "branch":
        # Set persona to trigger the branch condition
        persona_name = gap.suggested_persona
        if persona_name:
            try:
                archetype = PersonaArchetype(persona_name)
            except ValueError:
                archetype = PersonaArchetype.COOPERATIVE
            _apply_persona(scenario, archetype)
        scenario.description = f"{base.description} | 目标: 覆盖分支 {gap.item_id}"

    elif gap.dimension == "constraint":
        # Set persona that would pressure the agent toward the forbidden behavior
        persona_name = gap.suggested_persona
        if persona_name:
            try:
                archetype = PersonaArchetype(persona_name)
            except ValueError:
                archetype = PersonaArchetype.BOUNDARY
            _apply_persona(scenario, archetype)
        scenario.description = f"{base.description} | 目标: 触发约束 {gap.item_id}"

    elif gap.dimension == "step":
        # Use cooperative persona to maximize step coverage
        _apply_persona(scenario, PersonaArchetype.COOPERATIVE)
        scenario.description = f"{base.description} | 目标: 覆盖步骤 {gap.item_id}"

    elif gap.dimension == "persona":
        persona_name = gap.suggested_persona
        if persona_name:
            try:
                archetype = PersonaArchetype(persona_name)
            except ValueError:
                archetype = PersonaArchetype.COOPERATIVE
            _apply_persona(scenario, archetype)
        scenario.description = f"{base.description} | 目标: 测试画像 {gap.item_id}"

    elif gap.dimension == "tool_fault":
        # Ensure the scenario has the missing fault
        scenario.description = f"{base.description} | 目标: 测试故障 {gap.item_id}"
        # The fault should already be in base, but if not, nothing to add here
        # Just ensure persona is cooperative enough to reach the tool call
        _apply_persona(scenario, PersonaArchetype.COOPERATIVE)

    else:
        return None

    return scenario


def _apply_persona(scenario: OutboundScenario, archetype: PersonaArchetype) -> None:
    """Apply a persona archetype to a scenario's callee_persona.

    Maps archetype to numeric persona parameters for realistic behavior.
    """
    persona = scenario.callee_persona
    persona.archetype = archetype

    # Map archetype to numeric parameters
    _PERSONA_PARAMS: dict[PersonaArchetype, dict[str, int]] = {
        PersonaArchetype.COOPERATIVE: {
            "patience": 8,
            "cooperativeness": 9,
            "comprehension": 8,
            "emotional": 2,
            "busy_level": 2,
            "trust_level": 8,
        },
        PersonaArchetype.BUSY: {
            "patience": 3,
            "cooperativeness": 5,
            "comprehension": 7,
            "emotional": 4,
            "busy_level": 9,
            "trust_level": 5,
        },
        PersonaArchetype.WARY: {
            "patience": 5,
            "cooperativeness": 3,
            "comprehension": 8,
            "emotional": 4,
            "busy_level": 3,
            "trust_level": 2,
        },
        PersonaArchetype.IMPATIENT: {
            "patience": 2,
            "cooperativeness": 4,
            "comprehension": 7,
            "emotional": 7,
            "busy_level": 6,
            "trust_level": 4,
        },
        PersonaArchetype.HESITANT: {
            "patience": 6,
            "cooperativeness": 5,
            "comprehension": 5,
            "emotional": 5,
            "busy_level": 3,
            "trust_level": 5,
        },
        PersonaArchetype.CONFUSED: {
            "patience": 6,
            "cooperativeness": 6,
            "comprehension": 2,
            "emotional": 4,
            "busy_level": 2,
            "trust_level": 5,
        },
        PersonaArchetype.CONTRADICTORY: {
            "patience": 5,
            "cooperativeness": 4,
            "comprehension": 6,
            "emotional": 5,
            "busy_level": 3,
            "trust_level": 4,
        },
        PersonaArchetype.DIGRESSIVE: {
            "patience": 7,
            "cooperativeness": 5,
            "comprehension": 5,
            "emotional": 3,
            "busy_level": 2,
            "trust_level": 6,
        },
        PersonaArchetype.BOUNDARY: {
            "patience": 4,
            "cooperativeness": 3,
            "comprehension": 8,
            "emotional": 6,
            "busy_level": 3,
            "trust_level": 3,
        },
        PersonaArchetype.RUSHED: {
            "patience": 2,
            "cooperativeness": 6,
            "comprehension": 7,
            "emotional": 3,
            "busy_level": 9,
            "trust_level": 5,
        },
        PersonaArchetype.STUBBORN: {
            "patience": 6,
            "cooperativeness": 2,
            "comprehension": 7,
            "emotional": 5,
            "busy_level": 3,
            "trust_level": 3,
        },
        PersonaArchetype.RED_TEAM: {
            "patience": 7,
            "cooperativeness": 3,
            "comprehension": 9,
            "emotional": 2,
            "busy_level": 2,
            "trust_level": 2,
        },
    }

    params = _PERSONA_PARAMS.get(archetype, {})
    for attr, val in params.items():
        setattr(persona, attr, val)


def run_coverage_loop(
    scenario: OutboundScenario,
    existing_reports: list[OutboundScoreReport],
    max_iterations: int = 3,
    target_coverage: float = 0.8,
) -> CoverageLoopResult:
    """Coverage-guided generation loop (does NOT run actual evaluation).

    Each iteration:
    1. Analyze coverage gaps
    2. If coverage >= target, stop
    3. Generate targeted scenarios
    4. Estimate coverage improvement
    5. Repeat

    Returns the generated scenarios for the user to evaluate.
    """
    all_generated: list[OutboundScenario] = []
    current_reports = list(existing_reports)
    initial_cov = compute_coverage(scenario, current_reports).overall_coverage
    current_cov = initial_cov
    remaining_gaps: list[CoverageGap] = []

    for iteration in range(max_iterations):
        if current_cov >= target_coverage:
            break

        gap_report = analyze_coverage_gaps(scenario, current_reports)
        remaining_gaps = gap_report.gaps

        if not gap_report.gaps:
            break

        # Generate scenarios targeting the gaps
        max_per_iter = max(1, 5 - iteration)  # fewer per iteration as coverage grows
        new_scenarios = generate_targeted_scenarios(
            scenario, gap_report, max_scenarios=max_per_iter
        )

        if not new_scenarios:
            break

        # Re-index generated scenario IDs to be globally unique across iterations
        for sc in new_scenarios:
            global_idx = len(all_generated) + 1
            sc.id = f"{scenario.id}_cg{global_idx}"
            all_generated.append(sc)

        # Estimate coverage improvement: assume each targeted scenario covers
        # its gap dimension. This is optimistic but gives a useful upper bound.
        covered_dims = set()
        for s in new_scenarios:
            for gap in gap_report.gaps:
                if gap.suggested_persona and s.callee_persona.archetype:
                    if gap.suggested_persona == s.callee_persona.archetype:
                        covered_dims.add(gap.dimension)

        # Estimate: each covered dimension contributes proportionally
        total_gaps = len(gap_report.gaps)
        newly_covered = len(covered_dims)
        if total_gaps > 0:
            improvement = (1.0 - current_cov) * (newly_covered / total_gaps) * 0.5
            current_cov = min(1.0, current_cov + improvement)

    return CoverageLoopResult(
        iterations=min(max_iterations, len(all_generated)),
        initial_coverage=initial_cov,
        final_coverage=current_cov,
        generated_scenarios=all_generated,
        remaining_gaps=remaining_gaps,
    )


def format_gap_report(report: CoverageGapReport) -> str:
    """Format a CoverageGapReport as human-readable text."""
    lines = [
        f"覆盖率缺口分析 — {report.scenario_id}",
        f"  当前覆盖率: {report.current_coverage:.0%}",
        f"  缺口总数: {len(report.gaps)}",
        f"  建议生成场景数: {report.suggested_scenario_count}",
    ]
    if report.gap_count_by_dimension:
        lines.append("  按维度分布:")
        dim_labels = {
            "step": "步骤",
            "branch": "分支",
            "constraint": "约束",
            "persona": "画像",
            "tool_fault": "故障",
        }
        for dim, count in sorted(report.gap_count_by_dimension.items()):
            label = dim_labels.get(dim, dim)
            lines.append(f"    {label}: {count}")

    if report.gaps:
        lines.append("  具体缺口:")
        for g in report.gaps[:20]:  # Cap display
            persona_hint = f" [建议画像: {g.suggested_persona}]" if g.suggested_persona else ""
            lines.append(f"    - [{g.dimension}] {g.description}{persona_hint}")

    return "\n".join(lines)


# ── CLI ──


def _load_scenario(path: str) -> OutboundScenario:
    """Load an OutboundScenario from a JSON file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return OutboundScenario(**data)


def _load_reports(scenario_id: str, trace_dir: str = "traces") -> list[OutboundScoreReport]:
    """Load OutboundScoreReport objects from trace files belonging to a scenario."""
    reports: list[OutboundScoreReport] = []
    trace_path = Path(trace_dir)
    if not trace_path.exists():
        return reports

    for p in trace_path.glob("*.json"):
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            # Check if this trace belongs to the scenario
            if data.get("scenario", {}).get("id") == scenario_id:
                ob = (data.get("metadata") or {}).get("outbound_report")
                if ob:
                    reports.append(OutboundScoreReport(**ob))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    return reports


def main():
    parser = argparse.ArgumentParser(description="覆盖率引导场景生成")
    parser.add_argument("scenario", help="场景 JSON 文件路径")
    parser.add_argument("--max-scenarios", type=int, default=5, help="最大生成场景数")
    parser.add_argument("--target", type=float, default=0.8, help="目标覆盖率 (0-1)")
    parser.add_argument("--traces", default="traces", help="traces 目录路径")
    parser.add_argument(
        "--output", default=None, help="输出目录 (默认: scenarios/outbound/generated/)"
    )
    args = parser.parse_args()

    scenario = _load_scenario(args.scenario)
    reports = _load_reports(scenario.id, args.traces)

    print(f"场景: {scenario.name} ({scenario.id})")
    print(f"已有评测报告: {len(reports)} 份")
    print()

    # Analyze gaps
    gap_report = analyze_coverage_gaps(scenario, reports)
    print(format_gap_report(gap_report))
    print()

    if gap_report.current_coverage >= args.target:
        print(
            f"覆盖率已达标 ({gap_report.current_coverage:.0%} >= {args.target:.0%}), 无需生成新场景"
        )
        return

    # Run coverage loop
    result = run_coverage_loop(
        scenario,
        reports,
        max_iterations=3,
        target_coverage=args.target,
    )

    print(f"生成了 {len(result.generated_scenarios)} 个针对性场景")
    print(f"预估覆盖率: {result.initial_coverage:.0%} -> {result.final_coverage:.0%}")

    # Save generated scenarios
    output_dir = Path(args.output) if args.output else Path(args.scenario).parent / "generated"
    output_dir.mkdir(parents=True, exist_ok=True)

    for sc in result.generated_scenarios:
        filename = f"{sc.id}.json"
        out_path = output_dir / filename
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(sc.model_dump(), f, ensure_ascii=False, indent=2)
        print(f"  -> {out_path}")

    if result.remaining_gaps:
        print(f"\n剩余缺口 ({len(result.remaining_gaps)}):")
        for g in result.remaining_gaps[:10]:
            print(f"  - [{g.dimension}] {g.description}")


if __name__ == "__main__":
    main()
