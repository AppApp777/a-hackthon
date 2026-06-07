"""Causal Diagnosis: find the minimal unsatisfied core that explains agent failure.

Based on the PolicyGraph VerificationResult, traces back from unsatisfied atoms
to find the root cause — the earliest missing step/tool that caused a cascade
of downstream failures.

Also provides counterfactual repair estimation: "if step X had been completed,
the score would recover by +N points."
"""

from __future__ import annotations

from dataclasses import dataclass, field

from models_outbound import OutboundScenario
from policy_graph import PolicyGraph
from trace_verifier import VerificationResult


@dataclass
class CausalChainLink:
    """A single link in the causal failure chain."""

    atom_id: str
    dimension: str
    description: str
    is_root: bool = False  # True if this is the earliest failure
    caused_by: list[str] = field(default_factory=list)  # atom_ids that caused this
    blocks: list[str] = field(default_factory=list)  # atom_ids this failure blocks


@dataclass
class CounterfactualRepair:
    """Simulated minimal fix and its expected impact."""

    repair_description: str
    atoms_recovered: list[str]  # atom_ids that would be satisfied
    estimated_score_delta: float = 0.0
    repair_turn: int | None = None  # turn where the fix should be inserted


@dataclass
class CausalDiagnosisResult:
    """Complete causal diagnosis output."""

    root_causes: list[CausalChainLink] = field(default_factory=list)
    causal_chain: list[CausalChainLink] = field(default_factory=list)
    counterfactual_repairs: list[CounterfactualRepair] = field(default_factory=list)
    failure_mode: str = ""  # "step_skip", "tool_failure", "branch_error", "ordering_violation"
    deviation_point: str = ""  # step_id where agent first deviated
    deviation_turn: int | None = None


def diagnose(
    verification: VerificationResult,
    graph: PolicyGraph,
    scenario: OutboundScenario,
) -> CausalDiagnosisResult:
    """Analyze verification result to find root causes of failure.

    Algorithm:
    1. Collect unsatisfied atoms
    2. For each, trace back through the policy graph to find dependencies
    3. The earliest unsatisfied dependency is the root cause
    4. Estimate counterfactual repair impact
    """
    result = CausalDiagnosisResult()

    if not verification.unsatisfied_atoms:
        result.failure_mode = "none"
        return result

    # Build atom lookup
    all_atoms = {
        a.atom_id: a
        for a in (
            verification.satisfied_atoms
            + verification.unsatisfied_atoms
            + verification.not_applicable_atoms
        )
    }
    unsatisfied_ids = {a.atom_id for a in verification.unsatisfied_atoms}
    {a.atom_id for a in verification.satisfied_atoms}

    # Build dependency map: step atoms depend on their predecessors
    step_deps: dict[str, list[str]] = {}
    for step_id, node in graph.nodes.items():
        atom_id = f"step_{step_id}"
        deps = []
        for pre in node.preconditions:
            pre_atom = f"step_{pre}"
            if pre_atom in all_atoms:
                deps.append(pre_atom)
        step_deps[atom_id] = deps

    # Tool atoms depend on the step that requires them
    tool_to_step: dict[str, str] = {}
    for step in scenario.instruction_steps:
        for action in step.required_actions:
            tool_atom = f"tool_{action}"
            if tool_atom in all_atoms:
                tool_to_step[tool_atom] = f"step_{step.step_id}"

    # Find root causes: unsatisfied atoms whose dependencies are all satisfied (or empty)
    chain: list[CausalChainLink] = []
    roots: list[CausalChainLink] = []

    for atom in verification.unsatisfied_atoms:
        deps = step_deps.get(atom.atom_id, [])
        caused_by = [d for d in deps if d in unsatisfied_ids]
        blocks = [
            other_id
            for other_id, other_deps in step_deps.items()
            if atom.atom_id in other_deps and other_id in unsatisfied_ids
        ]

        link = CausalChainLink(
            atom_id=atom.atom_id,
            dimension=atom.dimension,
            description=atom.reason or atom.description,
            is_root=len(caused_by) == 0,
            caused_by=caused_by,
            blocks=blocks,
        )
        chain.append(link)
        if link.is_root:
            roots.append(link)

    result.causal_chain = chain
    result.root_causes = roots

    # Determine failure mode
    if roots:
        root_dims = {r.dimension for r in roots}
        if "tool_usage" in root_dims:
            result.failure_mode = "tool_failure"
        elif "branch_accuracy" in root_dims:
            result.failure_mode = "branch_error"
        elif "temporal_order" in root_dims:
            result.failure_mode = "ordering_violation"
        elif "step_compliance" in root_dims:
            result.failure_mode = "step_skip"
        else:
            result.failure_mode = "mixed"

    # Find deviation point: first expected step that wasn't observed
    for step_id in verification.expected_path:
        if step_id not in set(verification.observed_path):
            result.deviation_point = step_id
            node = graph.get_node(step_id)
            if node:
                result.deviation_turn = node.order
            break

    # Counterfactual repairs: simulate fixing each root cause
    for root in roots:
        # Estimate how many downstream atoms would recover
        recoverable = {root.atom_id}
        queue = list(root.blocks)
        while queue:
            blocked = queue.pop(0)
            if blocked in recoverable:
                continue
            link = next((c for c in chain if c.atom_id == blocked), None)
            if link:
                other_causes = [c for c in link.caused_by if c not in recoverable]
                if not other_causes:
                    recoverable.add(blocked)
                    queue.extend(link.blocks)

        # Estimate score recovery
        total_weight = sum(a.weight for a in verification.unsatisfied_atoms)
        recovered_weight = sum(all_atoms[aid].weight for aid in recoverable if aid in all_atoms)
        delta = recovered_weight / max(total_weight, 1) * 0.25  # rough estimate

        root_atom = all_atoms.get(root.atom_id)
        repair_desc = f"修复 {root.atom_id}: {root.description}"
        if root_atom and root_atom.step_id:
            node = graph.get_node(root_atom.step_id)
            if node:
                repair_desc = f"在第{node.order}步插入: {node.instruction[:50]}"

        result.counterfactual_repairs.append(
            CounterfactualRepair(
                repair_description=repair_desc,
                atoms_recovered=list(recoverable),
                estimated_score_delta=round(delta, 3),
            )
        )

    return result
