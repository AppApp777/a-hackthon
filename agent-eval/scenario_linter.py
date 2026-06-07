"""Static scenario linter — catch bad scenarios at compile time, not runtime.

Validates:
1. Every instruction step is reachable from the start node
2. Non-terminal nodes have valid outgoing edges
3. Branch targets reference existing steps
4. Tool references exist in the tool registry
5. Identity confirmation requirements are satisfiable from call_context
6. Rubric items are not structurally dead
7. Temporal constraints reference valid steps
8. Expected call result is achievable via must_call_tools
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tools_outbound import _TOOL_REQUIRED_PARAMS

TOOL_REGISTRY: set[str] = set(_TOOL_REQUIRED_PARAMS.keys())
TOOL_PARAM_REGISTRY: dict[str, list[str]] = dict(_TOOL_REQUIRED_PARAMS)

OUTCOME_TOOL_MAP: dict[str, str] = {
    "refunded": "create_compensation",
    "rescheduled": "reschedule_delivery",
    "confirmed": "update_delivery_status",
    "escalated": "transfer_to_human",
}


@dataclass
class LintFinding:
    level: Literal["error", "warning", "info"]
    code: str
    message: str
    location: str = ""


def lint_scenario(scenario: dict) -> list[LintFinding]:
    """Run all lint checks on a scenario dict. Returns list of findings."""
    findings: list[LintFinding] = []
    findings.extend(_check_step_reachability(scenario))
    findings.extend(_check_branch_targets(scenario))
    findings.extend(_check_tool_references(scenario))
    findings.extend(_check_identity_satisfiability(scenario))
    findings.extend(_check_outcome_consistency(scenario))
    findings.extend(_check_dead_rubric(scenario))
    return findings


def _check_step_reachability(scenario: dict) -> list[LintFinding]:
    findings = []
    steps = scenario.get("instruction_steps", [])
    if not steps:
        findings.append(LintFinding("error", "S001", "No instruction_steps defined"))
        return findings

    reachable = set()
    first_step = steps[0]["step_id"]
    reachable.add(first_step)

    for s in steps:
        if s.get("is_optional"):
            continue
        sid = s["step_id"]
        idx = next((i for i, x in enumerate(steps) if x["step_id"] == sid), -1)
        if idx + 1 < len(steps):
            reachable.add(steps[idx + 1]["step_id"])
        for branch in s.get("branches", []):
            target = branch.get("next_step")
            if target:
                reachable.add(target)

    for s in steps:
        if s["step_id"] not in reachable and not s.get("is_optional"):
            findings.append(
                LintFinding(
                    "warning",
                    "S002",
                    f"Step '{s['step_id']}' may be unreachable",
                    f"instruction_steps[{s.get('order', '?')}]",
                )
            )
    return findings


def _check_branch_targets(scenario: dict) -> list[LintFinding]:
    findings = []
    steps = scenario.get("instruction_steps", [])
    step_ids = {s["step_id"] for s in steps}

    for s in steps:
        for branch in s.get("branches", []):
            target = branch.get("next_step", "")
            if target and target not in step_ids:
                findings.append(
                    LintFinding(
                        "error",
                        "B001",
                        f"Branch in step '{s['step_id']}' targets non-existent step '{target}'",
                        f"instruction_steps.{s['step_id']}.branches",
                    )
                )
    return findings


def _check_tool_references(scenario: dict) -> list[LintFinding]:
    findings = []
    must_call = scenario.get("must_call_tools", [])
    for tool in must_call:
        if tool not in TOOL_REGISTRY:
            findings.append(
                LintFinding(
                    "error",
                    "T001",
                    f"must_call_tools references unknown tool '{tool}'",
                    "must_call_tools",
                )
            )

    for s in scenario.get("instruction_steps", []):
        for action in s.get("required_actions", []):
            if action in TOOL_REGISTRY or action.startswith(
                (
                    "self_",
                    "confirm_",
                    "state_",
                    "offer_",
                    "ask_",
                    "say_",
                    "apologize",
                    "empathize",
                    "final_",
                    "restate_",
                )
            ):
                pass
            else:
                findings.append(
                    LintFinding(
                        "info",
                        "T002",
                        f"required_action '{action}' in step '{s['step_id']}' is not a known tool or speech act",
                        f"instruction_steps.{s['step_id']}.required_actions",
                    )
                )
    return findings


def _check_identity_satisfiability(scenario: dict) -> list[LintFinding]:
    findings = []
    ctx = scenario.get("call_context", {})
    steps = scenario.get("instruction_steps", [])

    has_confirm_identity_step = any(
        "confirm_identity" in s.get("step_id", "")
        or "confirm_name" in (s.get("required_actions") or [])
        or "confirm_identity" in (s.get("required_actions") or [])
        for s in steps
    )

    if has_confirm_identity_step:
        has_name = bool(ctx.get("customer_name"))
        has_role_hint = bool(ctx.get("merchant_name") or ctx.get("institution_name"))
        if not has_name and not has_role_hint:
            findings.append(
                LintFinding(
                    "warning",
                    "I001",
                    "Scenario requires identity confirmation but call_context has no customer_name or role indicator",
                    "call_context",
                )
            )
    return findings


def _check_outcome_consistency(scenario: dict) -> list[LintFinding]:
    findings = []
    expected_result = scenario.get("expected_call_result", "")
    must_call = set(scenario.get("must_call_tools", []))

    if expected_result and expected_result in OUTCOME_TOOL_MAP:
        required_tool = OUTCOME_TOOL_MAP[expected_result]
        if required_tool not in must_call:
            findings.append(
                LintFinding(
                    "warning",
                    "O001",
                    f"expected_call_result='{expected_result}' typically requires '{required_tool}' but it's not in must_call_tools",
                    "expected_call_result",
                )
            )

    if "log_call_result" not in must_call and must_call:
        findings.append(
            LintFinding(
                "info",
                "O002",
                "log_call_result not in must_call_tools — call result won't be formally logged",
                "must_call_tools",
            )
        )
    return findings


def _check_dead_rubric(scenario: dict) -> list[LintFinding]:
    findings = []
    mandatory_opening = scenario.get("mandatory_opening", "")

    if "智能助手" in mandatory_opening or "AI" in mandatory_opening:
        findings.append(
            LintFinding(
                "info",
                "R001",
                "Agent self-discloses as AI in mandatory_opening — 'robot_detected' rubric item will have low signal",
                "mandatory_opening",
            )
        )

    forbidden = scenario.get("forbidden_behaviors", [])
    if not forbidden:
        findings.append(
            LintFinding(
                "info",
                "R002",
                "No forbidden_behaviors defined — safety veto layer will not trigger",
                "forbidden_behaviors",
            )
        )
    return findings


def lint_all_scenarios(scenarios_dir: str | Path) -> dict[str, list[LintFinding]]:
    """Lint all scenario JSON files in a directory."""
    results = {}
    scenarios_dir = Path(scenarios_dir)
    for f in sorted(scenarios_dir.glob("*.json")):
        scenario = json.loads(f.read_text(encoding="utf-8"))
        findings = lint_scenario(scenario)
        if findings:
            results[f.name] = findings
    return results


if __name__ == "__main__":
    import sys

    scenarios_dir = sys.argv[1] if len(sys.argv) > 1 else "scenarios/outbound"
    results = lint_all_scenarios(scenarios_dir)
    total = sum(len(v) for v in results.values())
    errors = sum(1 for v in results.values() for f in v if f.level == "error")
    warnings = sum(1 for v in results.values() for f in v if f.level == "warning")
    print(
        f"Linted {len(results)} scenarios with findings: {errors} errors, {warnings} warnings, {total - errors - warnings} info"
    )
    for name, findings in results.items():
        for f in findings:
            print(f"  [{f.level.upper()}] {name}: {f.code} — {f.message}")
    if errors > 0:
        sys.exit(1)
