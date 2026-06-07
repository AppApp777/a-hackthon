"""Coverage report — shows what percentage of the evaluation surface has been tested.

Converts synthetic data from "made-up" into "systematic testing."
"""

from __future__ import annotations

from dataclasses import dataclass, field

from models_outbound import OutboundScenario, OutboundScoreReport


@dataclass
class CoverageReport:
    instruction_id: str = ""
    step_coverage: float = 0.0
    branch_coverage: float = 0.0
    constraint_coverage: float = 0.0
    persona_coverage: float = 0.0
    tool_fault_coverage: float = 0.0
    overall_coverage: float = 0.0
    details: dict = field(default_factory=dict)


def compute_coverage(
    scenario: OutboundScenario,
    reports: list[OutboundScoreReport],
) -> CoverageReport:
    """Compute coverage across multiple evaluation runs of the same scenario."""
    cov = CoverageReport(instruction_id=scenario.id)

    total_steps = len(scenario.instruction_steps)
    if total_steps > 0 and reports:
        tested_steps: set[str] = set()
        for r in reports:
            for sc in r.step_compliance:
                if sc.status in ("completed", "failed", "skipped"):
                    tested_steps.add(sc.step_id)
        cov.step_coverage = len(tested_steps) / total_steps
        cov.details["steps_tested"] = sorted(tested_steps)
        cov.details["steps_total"] = [s.step_id for s in scenario.instruction_steps]

    total_branches = sum(len(s.branches) for s in scenario.instruction_steps)
    if total_branches > 0 and reports:
        taken_branches: set[str] = set()
        for r in reports:
            for sc in r.step_compliance:
                if sc.branch_taken:
                    taken_branches.add(f"{sc.step_id}:{sc.branch_taken}")
        cov.branch_coverage = len(taken_branches) / total_branches
        cov.details["branches_tested"] = sorted(taken_branches)
        cov.details["branches_total"] = total_branches

    total_constraints = len(scenario.forbidden_behaviors)
    if total_constraints > 0 and reports:
        tested_constraints: set[str] = set()
        for r in reports:
            for v in r.forbidden_violations:
                tested_constraints.add(v.get("behavior_id", ""))
            for c in r.checks:
                if c.dimension == "forbidden_behavior" and not c.passed:
                    tested_constraints.add(c.check_id)
        cov.constraint_coverage = min(1.0, len(tested_constraints) / total_constraints)
        cov.details["constraints_triggered"] = sorted(tested_constraints)

    total_faults = len(scenario.tool_faults)
    if total_faults > 0:
        cov.tool_fault_coverage = 1.0 if reports else 0.0
    else:
        cov.tool_fault_coverage = 1.0

    weights = [0.35, 0.25, 0.20, 0.20]
    scores = [
        cov.step_coverage,
        cov.branch_coverage,
        cov.constraint_coverage,
        cov.tool_fault_coverage,
    ]
    cov.overall_coverage = sum(w * s for w, s in zip(weights, scores, strict=False))

    return cov


def enumerate_graph_branches(scenario: OutboundScenario) -> list[dict]:
    """Enumerate all branches from the compiled PolicyGraph for forced coverage testing."""
    from policy_graph import compile_policy_graph

    graph = compile_policy_graph(scenario)
    branches = graph.enumerate_branches()

    result = []
    for source, condition, target in branches:
        src_node = graph.get_node(source)
        tgt_node = graph.get_node(target)
        result.append(
            {
                "source_id": source,
                "source_name": src_node.instruction if src_node else source,
                "condition": condition,
                "target_id": target,
                "target_name": tgt_node.instruction if tgt_node else target,
            }
        )
    return result


def cross_reference_branch_coverage(
    scenario: OutboundScenario,
    reports: list[OutboundScoreReport],
) -> dict:
    """Cross-reference enumerated branches with actual trace data to find coverage gaps."""
    all_branches = enumerate_graph_branches(scenario)
    if not all_branches:
        return {"total": 0, "covered": 0, "coverage": 1.0, "gaps": []}

    branch_keys = {f"{b['source_id']}:{b['condition']}" for b in all_branches}
    covered_keys: set[str] = set()

    for r in reports:
        for sc in r.step_compliance:
            if sc.branch_taken:
                covered_keys.add(f"{sc.step_id}:{sc.branch_taken}")

    gaps = []
    for b in all_branches:
        key = f"{b['source_id']}:{b['condition']}"
        if key not in covered_keys:
            gaps.append(b)

    total = len(branch_keys)
    covered = len(branch_keys & covered_keys)
    return {
        "total": total,
        "covered": covered,
        "coverage": covered / total if total > 0 else 1.0,
        "gaps": gaps,
    }


def format_branch_coverage_report(
    scenario: OutboundScenario, reports: list[OutboundScoreReport]
) -> str:
    """Format cross-referenced branch coverage with gap analysis."""
    result = cross_reference_branch_coverage(scenario, reports)
    lines = [
        f"分支覆盖分析 — {scenario.id}",
        f"  策略图分支总数: {result['total']}",
        f"  已覆盖: {result['covered']}",
        f"  覆盖率: {result['coverage']:.0%}",
    ]
    if result["gaps"]:
        lines.append("  未覆盖分支:")
        for g in result["gaps"]:
            lines.append(f"    ✗ {g['source_id']}({g['condition']}) → {g['target_id']}")
    else:
        lines.append("  ✓ 所有分支已覆盖")
    return "\n".join(lines)


def format_branch_enumeration(scenario: OutboundScenario) -> str:
    """Format branch enumeration for display."""
    branches = enumerate_graph_branches(scenario)
    if not branches:
        return f"场景 {scenario.id}: 无条件分支"

    lines = [f"场景 {scenario.id} — 分支枚举 ({len(branches)} 条分支)"]
    for i, b in enumerate(branches, 1):
        lines.append(
            f"  [{i}] {b['source_id']}({b['condition']}) → {b['target_id']}"
            f"  | {b['source_name']} → {b['target_name']}"
        )
    return "\n".join(lines)


def format_coverage(cov: CoverageReport) -> str:
    lines = [
        f"覆盖率报告 — {cov.instruction_id}",
        f"  步骤覆盖: {cov.step_coverage:.0%}",
        f"  分支覆盖: {cov.branch_coverage:.0%}",
        f"  约束覆盖: {cov.constraint_coverage:.0%}",
        f"  故障覆盖: {cov.tool_fault_coverage:.0%}",
        f"  综合覆盖: {cov.overall_coverage:.0%}",
    ]
    return "\n".join(lines)
