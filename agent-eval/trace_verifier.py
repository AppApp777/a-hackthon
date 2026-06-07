"""Trace Verifier: align observed agent behavior against the policy graph.

Core algorithm: weighted edit distance (full DP) between expected step sequence
and observed event stream. Produces a VerificationResult with per-atom verdicts,
illegal transitions, and the alignment matrix for diagnostics.

Contract compliance:
- Only uses EventLedger (immutable, append-only) as source of truth for tool events
- Never trusts agent self-reports
- Each scored atom links to concrete evidence event IDs
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from models import Conversation, EventLedger, Role, ToolEventType
from models_outbound import OutboundScenario
from policy_graph import (
    AtomStatus,
    ConstraintType,
    PolicyGraph,
    Predicate,
    ScoringAtom,
    SemanticUtterancePredicate,
    ToolPredicate,
    UtterancePredicate,
    compile_policy_graph,
)

# ── Oracle Q3: Negation detection + fuzzy matching ──

_NEG_PREFIX = re.compile(
    r"(并?没有|并?没|没有|没能|没法|没办法|"
    r"未能|未|无法|不能|不可以|不可|不会|"
    r"不支持|不受理|不处理|不办理|不帮|不给|不予|"
    r"拒绝|暂时无法|暂时不能|暂不|"
    r"无需|不用|不需要|不是|并非)"
)
_NEG_SUFFIX = re.compile(r"(不了|不成|不成功|失败|没成功)$")
_DOUBLE_NEG = re.compile(r"(不是不|并不是不|并非不|没有说不|不是说不)")
_CONTRAST = re.compile(r"(但是|但|不过|只是|而是|可以|现在|马上|已经|已)")


def _negation_status(text: str, negation_terms: tuple[str, ...] = ()) -> str:
    """Returns 'clear', 'negated', or 'ambiguous'."""
    for term in negation_terms:
        if term in text:
            return "negated"
    if _DOUBLE_NEG.search(text):
        return "ambiguous"
    if _NEG_PREFIX.search(text):
        if _CONTRAST.search(text):
            return "ambiguous"
        return "negated"
    if _NEG_SUFFIX.search(text):
        return "negated"
    return "clear"


def _clean_chars(s: str) -> str:
    return re.sub(r"[^一-鿿㐀-䶿\w]", "", s)


def _char_ngrams(s: str) -> set[str]:
    chars = _clean_chars(s)
    grams: set[str] = set()
    for n in (2, 3):
        for i in range(len(chars) - n + 1):
            grams.add(chars[i : i + n])
    return grams


def _dice(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


def _alias_match_score(alias: str, text: str) -> float:
    """Fuzzy alias matching: exact substring → 1.0, else char n-gram + sliding window."""
    alias_n = alias.lower()
    text_n = text.lower()
    if alias_n in text_n:
        return 1.0
    alias_chars = _clean_chars(alias_n)
    if len(alias_chars) < 3:
        return 0.0
    text_chars = _clean_chars(text_n)
    if not text_chars:
        return 0.0
    target_len = len(alias_chars)
    best = 0.0
    alias_counter = Counter(alias_chars)
    alias_grams = _char_ngrams(alias_n)
    for wlen in range(max(1, target_len - 3), target_len + 4):
        for i in range(len(text_chars) - wlen + 1):
            win = text_chars[i : i + wlen]
            win_counter = Counter(win)
            common = sum((alias_counter & win_counter).values())
            total_a = sum(alias_counter.values())
            total_w = sum(win_counter.values())
            prec = common / max(total_a, 1)
            rec = common / max(total_w, 1)
            char_f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
            gram_dice = _dice(alias_grams, _char_ngrams(win))
            seq_ratio = SequenceMatcher(None, alias_chars, win).ratio()
            score = 0.50 * char_f1 + 0.30 * gram_dice + 0.20 * seq_ratio
            best = max(best, score)
    return best if best >= 0.80 else 0.0


def _match_semantic_predicate(
    pred: SemanticUtterancePredicate,
    events: list,
    harness_source_token: str = "",
) -> tuple[bool, list[str]]:
    """Match SemanticUtterancePredicate with negation awareness + fuzzy matching."""
    for ev in events:
        if ev.kind != "agent_utterance":
            continue
        if ev.source == harness_source_token:
            continue
        text = ev.content
        text_lower = text.lower()

        # Quick reject: negative alias hit
        if any(neg.lower() in text_lower for neg in pred.negative_aliases):
            continue

        # Quick accept: positive alias hit + clear negation
        if any(pos.lower() in text_lower for pos in pred.positive_aliases):
            if _negation_status(text_lower, pred.negation_terms) == "clear":
                return True, [ev.event_id]

        # Concept group matching
        if pred.concept_groups:
            total_weight = sum(cg.weight for cg in pred.concept_groups)
            covered = 0.0
            for cg in pred.concept_groups:
                best = max((_alias_match_score(a, text) for a in cg.aliases), default=0.0)
                if best >= cg.min_alias_score:
                    covered += cg.weight
                elif not cg.required:
                    pass
                else:
                    covered += cg.weight * 0.35
            coverage = covered / total_weight if total_weight > 0 else 0.0
            neg = _negation_status(text_lower, pred.negation_terms)
            if neg == "negated":
                continue
            if coverage >= pred.fast_accept_threshold and neg == "clear":
                return True, [ev.event_id]

        # Fallback: keyword matching with negation check
        if pred.keywords:
            hits = sum(1 for kw in pred.keywords if kw in text_lower)
            if hits >= max(1, len(pred.keywords) * pred.min_match_ratio):
                if _negation_status(text_lower, pred.negation_terms) == "clear":
                    return True, [ev.event_id]

    return False, []


# ── Normalized Event Stream ──


class EventKind:
    AGENT_UTTERANCE = "agent_utterance"
    TOOL_EXECUTED = "tool_executed"
    TOOL_BLOCKED = "tool_blocked"
    TOOL_FAILED = "tool_failed"
    USER_RESPONSE = "user_response"
    SYSTEM_INJECTION = "system_injection"


@dataclass(frozen=True)
class TraceEvent:
    """A single normalized event in the execution trace."""

    seq: int
    kind: str  # EventKind
    turn: int
    content: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_result: Any = None
    tool_error: str | None = None
    source: str = "agent"  # "agent", "harness", "user", "system"
    event_id: str = ""  # for evidence linking


def normalize_trace(
    conversation: Conversation,
    ledger: EventLedger | None = None,
) -> list[TraceEvent]:
    """Convert conversation + ledger into a normalized, ordered event stream.

    Merges conversation messages with ledger tool events into a single timeline.
    System messages are included but tagged for exclusion from scoring.
    """
    events: list[TraceEvent] = []
    seq = 0

    # Extract conversation events
    for msg in conversation.messages:
        if msg.metadata.get("__post_call_verified__"):
            continue

        seq += 1
        if msg.role == Role.AGENT:
            events.append(
                TraceEvent(
                    seq=seq,
                    kind=EventKind.AGENT_UTTERANCE,
                    turn=msg.turn,
                    content=msg.content,
                    source="agent",
                    event_id=f"msg_{msg.turn}_agent",
                )
            )
            # Inline tool calls from conversation (fallback when no ledger)
            if not ledger:
                for tc in msg.tool_calls:
                    seq += 1
                    kind = EventKind.TOOL_EXECUTED if not tc.error else EventKind.TOOL_FAILED
                    events.append(
                        TraceEvent(
                            seq=seq,
                            kind=kind,
                            turn=msg.turn,
                            tool_name=tc.tool_name,
                            tool_args=tc.arguments or {},
                            tool_result=tc.result,
                            tool_error=tc.error,
                            source=tc.source,
                            event_id=f"tc_{tc.id}",
                        )
                    )
        elif msg.role == Role.USER:
            seq += 1
            events.append(
                TraceEvent(
                    seq=seq,
                    kind=EventKind.USER_RESPONSE,
                    turn=msg.turn,
                    content=msg.content,
                    source="user",
                    event_id=f"msg_{msg.turn}_user",
                )
            )
        elif msg.role == Role.SYSTEM:
            seq += 1
            events.append(
                TraceEvent(
                    seq=seq,
                    kind=EventKind.SYSTEM_INJECTION,
                    turn=msg.turn,
                    content=msg.content,
                    source="system",
                    event_id=f"msg_{msg.turn}_system",
                )
            )

    # Merge ledger events (authoritative for tool execution)
    if ledger:
        rolled_back = ledger.rollback_ids
        for le in ledger.events:
            seq += 1
            if le.event_type == ToolEventType.TOOL_EXECUTED:
                if le.tool_call_id and le.tool_call_id in rolled_back:
                    kind = EventKind.TOOL_BLOCKED
                elif le.error:
                    kind = EventKind.TOOL_FAILED
                else:
                    kind = EventKind.TOOL_EXECUTED
            elif (
                le.event_type == ToolEventType.TOOL_BLOCKED
                or le.event_type == ToolEventType.TOOL_FABRICATED
            ):
                kind = EventKind.TOOL_BLOCKED
            else:
                kind = EventKind.TOOL_FAILED

            events.append(
                TraceEvent(
                    seq=seq,
                    kind=kind,
                    turn=le.turn,
                    tool_name=le.tool_name,
                    tool_args=dict(le.arguments) if le.arguments else {},
                    tool_result=le.result,
                    tool_error=le.error,
                    source=le.source,
                    event_id=f"ledger_{le.seq}",
                )
            )

    events.sort(key=lambda e: (e.turn, e.seq))
    return events


# ── Step Observation Extraction ──


@dataclass
class ObservedStep:
    """An observed step execution extracted from the trace."""

    step_id: str
    first_turn: int
    last_turn: int
    evidence_events: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    branch_taken: str | None = None
    confidence: float = 0.0  # 0-1, how confident the match is


def _match_predicate(
    pred: Predicate,
    events: list[TraceEvent],
    scenario_order_id: str = "",
    harness_source_token: str = "",
) -> tuple[bool, list[str]]:
    """Check if a predicate is satisfied by any event. Returns (matched, evidence_ids)."""
    if isinstance(pred, ToolPredicate):
        for ev in events:
            if ev.kind != EventKind.TOOL_EXECUTED:
                continue
            if ev.tool_name != pred.tool_name:
                continue
            if ev.source == harness_source_token:
                continue
            if pred.required_args:
                from scorer_outbound import _ast_match_tool_call

                m = _ast_match_tool_call(
                    pred.tool_name, pred.required_args, ev.tool_name, ev.tool_args
                )
                if not m.matched:
                    continue
            # Entity binding: check order_id
            if scenario_order_id:
                ev_oid = ev.tool_args.get("order_id", "")
                if ev_oid and ev_oid != scenario_order_id:
                    continue
            return True, [ev.event_id]
        return False, []

    elif isinstance(pred, SemanticUtterancePredicate):
        return _match_semantic_predicate(pred, events, harness_source_token)

    elif isinstance(pred, UtterancePredicate):
        for ev in events:
            if ev.kind != EventKind.AGENT_UTTERANCE:
                continue
            text = ev.content.lower()
            hits = sum(1 for kw in pred.keywords if kw in text)
            if hits >= max(1, len(pred.keywords) * pred.min_match_ratio):
                neg = _negation_status(text, negation_terms=pred.negation_terms)
                if neg != "negated":
                    return True, [ev.event_id]
        return False, []

    return False, []


def extract_observed_steps(
    graph: PolicyGraph,
    events: list[TraceEvent],
    scenario_order_id: str = "",
    harness_source_token: str = "",
) -> list[ObservedStep]:
    """Extract which steps were actually executed from the event trace.

    Evidence consumption: tool events can only satisfy ONE step (consumed on first match).
    Utterance events can satisfy multiple steps only if they match different predicates
    (e.g., one step needs "confirm_name" keywords, another needs "query_order" tool).
    This prevents a single generic utterance from inflating step compliance.
    """
    observed: list[ObservedStep] = []
    agent_events = [e for e in events if e.source != "system"]

    # Track consumed evidence: tool events are exclusive, utterance events are per-keyword-set
    consumed_tool_events: set[str] = set()
    consumed_utterance_events: dict[
        str, set[tuple[str, ...]]
    ] = {}  # event_id -> set of keyword tuples used

    for step_id in graph.topological_order():
        node = graph.get_node(step_id)
        if not node:
            continue

        all_evidence: list[str] = []
        all_turns: list[int] = []
        tools_used: list[str] = []
        matched_any = False
        # Coverage gating (adversarial hardening): a tool match is authoritative, but a
        # multi-action CONVERSATIONAL step must evidence ≥50% of its required actions —
        # otherwise one stuffed alias ("抱歉") would mark a 3-action step (apologize +
        # restate_issue + confirm_issue) fully complete. Single-action steps are 1/1.
        tool_matched = False
        conv_total = 0
        conv_matched = 0

        for pred in node.predicates:
            is_tool = isinstance(pred, ToolPredicate)
            if not is_tool:
                conv_total += 1
            matched, evidence_ids = _match_predicate(
                pred, agent_events, scenario_order_id, harness_source_token
            )
            if matched:
                # Enforce evidence uniqueness
                usable_ids = []
                for eid in evidence_ids:
                    ev = next((e for e in events if e.event_id == eid), None)
                    if not ev:
                        continue
                    if ev.kind == EventKind.TOOL_EXECUTED:
                        if eid in consumed_tool_events:
                            continue
                        consumed_tool_events.add(eid)
                        usable_ids.append(eid)
                    elif ev.kind == EventKind.AGENT_UTTERANCE:
                        kw_key = pred.keywords if isinstance(pred, UtterancePredicate) else ()
                        prev_keys = consumed_utterance_events.get(eid, set())
                        if kw_key and kw_key in prev_keys:
                            continue
                        consumed_utterance_events.setdefault(eid, set()).add(kw_key)
                        usable_ids.append(eid)
                    else:
                        usable_ids.append(eid)

                if usable_ids:
                    matched_any = True
                    if is_tool:
                        tool_matched = True
                    else:
                        conv_matched += 1
                    all_evidence.extend(usable_ids)
                    for eid in usable_ids:
                        ev = next((e for e in events if e.event_id == eid), None)
                        if ev:
                            all_turns.append(ev.turn)
                            if ev.tool_name:
                                tools_used.append(ev.tool_name)

        # Apply coverage gate: drop a conversational-only match that covers <50% of the
        # step's required actions (a tool match always keeps the step).
        if matched_any and not tool_matched and conv_total >= 2 and conv_matched * 2 < conv_total:
            matched_any = False

        if not node.predicates:
            for ev in agent_events:
                if ev.kind != EventKind.AGENT_UTTERANCE:
                    continue
                text = ev.content.lower()
                kws = re.split(r"[，。、/+\s]+", node.instruction.lower())
                kws = [k for k in kws if len(k) >= 2]
                if kws:
                    kw_key = tuple(kws)
                    prev_keys = consumed_utterance_events.get(ev.event_id, set())
                    if kw_key in prev_keys:
                        continue
                    hits = sum(1 for k in kws if k in text)
                    if hits >= max(1, len(kws) * 0.3):
                        matched_any = True
                        all_evidence.append(ev.event_id)
                        all_turns.append(ev.turn)
                        consumed_utterance_events.setdefault(ev.event_id, set()).add(kw_key)
                        break

        if matched_any:
            confidence = len(all_evidence) / max(len(node.predicates), 1)
            observed.append(
                ObservedStep(
                    step_id=step_id,
                    first_turn=min(all_turns) if all_turns else 0,
                    last_turn=max(all_turns) if all_turns else 0,
                    evidence_events=all_evidence,
                    tools_used=tools_used,
                    confidence=min(1.0, confidence),
                )
            )

    observed.sort(key=lambda s: s.first_turn)
    return observed


# ── Weighted Edit Distance DP Alignment ──


@dataclass
class AlignmentOp:
    """A single operation in the alignment."""

    op_type: str  # "match", "substitute", "delete_expected", "insert_observed"
    expected_step: str = ""
    observed_step: str = ""
    cost: float = 0.0
    detail: str = ""


# Cost functions
_COST_MATCH = 0.0
_COST_MISSING_REQUIRED = 3.0
_COST_MISSING_OPTIONAL = 0.5
_COST_EXTRA_SAFE = 0.2
_COST_OUT_OF_ORDER = 2.0
_COST_WRONG_BRANCH = 2.5
_COST_CRITICAL_UNSAFE = 10.0


def _deletion_cost(step_id: str, graph: PolicyGraph) -> float:
    """Cost of missing an expected step (deletion from expected sequence)."""
    node = graph.get_node(step_id)
    if not node:
        return _COST_MISSING_REQUIRED
    if node.is_optional:
        return _COST_MISSING_OPTIONAL
    return _COST_MISSING_REQUIRED


def _insertion_cost(obs: ObservedStep, graph: PolicyGraph) -> float:
    """Cost of an extra observed step not in expected sequence."""
    node = graph.get_node(obs.step_id)
    if not node:
        return _COST_EXTRA_SAFE
    return _COST_EXTRA_SAFE


def _substitution_cost(
    expected_id: str,
    obs: ObservedStep,
    graph: PolicyGraph,
    prev_expected: str = "",
    prev_observed: str = "",
) -> float:
    """Cost of substituting expected step with a different observed step.

    Graph-aware: if the observed step is a legal successor of the previous
    observed step in the policy graph, the cost is reduced (legal skip).
    """
    if expected_id == obs.step_id:
        return _COST_MATCH

    # Check if observed step is a legal successor in the graph
    if prev_observed:
        legal_successors = {t for t, _ in graph.successors(prev_observed)}
        if obs.step_id in legal_successors:
            return _COST_OUT_OF_ORDER * 0.5  # legal graph transition, half penalty

    return _COST_OUT_OF_ORDER


def align_sequences(
    expected: list[str],
    observed: list[ObservedStep],
    graph: PolicyGraph,
) -> tuple[float, list[AlignmentOp]]:
    """Graph-constrained weighted edit distance DP alignment.

    Unlike naive sequence edit distance, this considers the PolicyGraph structure:
    - Matches between legal graph successors get reduced substitution cost
    - The alignment respects branch/skip edges, not just sequential order

    Returns (total_cost, alignment_operations).
    """
    n = len(expected)
    m = len(observed)

    # dp[i][j] = min cost to align expected[0:i] with observed[0:j]
    INF = float("inf")
    dp = [[INF] * (m + 1) for _ in range(n + 1)]
    backtrack = [[""] * (m + 1) for _ in range(n + 1)]

    dp[0][0] = 0.0

    # Base: deleting expected steps (missing)
    for i in range(1, n + 1):
        dp[i][0] = dp[i - 1][0] + _deletion_cost(expected[i - 1], graph)
        backtrack[i][0] = "delete"

    # Base: inserting extra observed steps
    for j in range(1, m + 1):
        dp[0][j] = dp[0][j - 1] + _insertion_cost(observed[j - 1], graph)
        backtrack[0][j] = "insert"

    # Fill DP table — graph-constrained: substitution cost depends on predecessor context
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            # Match/Substitute — pass predecessor info for graph-aware cost
            prev_exp = expected[i - 2] if i >= 2 else ""
            prev_obs = observed[j - 2].step_id if j >= 2 else ""
            sub_cost = _substitution_cost(
                expected[i - 1], observed[j - 1], graph, prev_exp, prev_obs
            )
            val_sub = dp[i - 1][j - 1] + sub_cost

            # Delete expected (missing step)
            val_del = dp[i - 1][j] + _deletion_cost(expected[i - 1], graph)

            # Insert observed (extra step)
            val_ins = dp[i][j - 1] + _insertion_cost(observed[j - 1], graph)

            if val_sub <= val_del and val_sub <= val_ins:
                dp[i][j] = val_sub
                backtrack[i][j] = "match" if sub_cost == _COST_MATCH else "substitute"
            elif val_del <= val_ins:
                dp[i][j] = val_del
                backtrack[i][j] = "delete"
            else:
                dp[i][j] = val_ins
                backtrack[i][j] = "insert"

    # Traceback
    ops: list[AlignmentOp] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and backtrack[i][j] in ("match", "substitute"):
            cost = _substitution_cost(expected[i - 1], observed[j - 1], graph)
            op_type = "match" if cost == _COST_MATCH else "substitute"
            detail = ""
            if op_type == "substitute":
                detail = f"期望 {expected[i - 1]}，观测到 {observed[j - 1].step_id}"
            ops.append(
                AlignmentOp(
                    op_type=op_type,
                    expected_step=expected[i - 1],
                    observed_step=observed[j - 1].step_id,
                    cost=cost,
                    detail=detail,
                )
            )
            i -= 1
            j -= 1
        elif i > 0 and (j == 0 or backtrack[i][j] == "delete"):
            cost = _deletion_cost(expected[i - 1], graph)
            ops.append(
                AlignmentOp(
                    op_type="delete_expected",
                    expected_step=expected[i - 1],
                    cost=cost,
                    detail=f"缺失步骤: {expected[i - 1]}",
                )
            )
            i -= 1
        else:
            cost = _insertion_cost(observed[j - 1], graph)
            ops.append(
                AlignmentOp(
                    op_type="insert_observed",
                    observed_step=observed[j - 1].step_id,
                    cost=cost,
                    detail=f"额外步骤: {observed[j - 1].step_id}",
                )
            )
            j -= 1

    ops.reverse()
    return dp[n][m], ops


def align_strict(
    expected: list[str],
    observed: list[ObservedStep],
    graph: PolicyGraph,
) -> tuple[float, list[AlignmentOp]]:
    """Strict matching: observed must exactly match expected in order and content."""
    ops: list[AlignmentOp] = []
    total_cost = 0.0
    max_len = max(len(expected), len(observed))
    for i in range(max_len):
        if i < len(expected) and i < len(observed):
            if expected[i] == observed[i].step_id:
                ops.append(
                    AlignmentOp(
                        op_type="match",
                        expected_step=expected[i],
                        observed_step=observed[i].step_id,
                        cost=0.0,
                    )
                )
            else:
                cost = _COST_OUT_OF_ORDER
                ops.append(
                    AlignmentOp(
                        op_type="substitute",
                        expected_step=expected[i],
                        observed_step=observed[i].step_id,
                        cost=cost,
                        detail=f"期望 {expected[i]}，观测到 {observed[i].step_id}",
                    )
                )
                total_cost += cost
        elif i < len(expected):
            cost = _deletion_cost(expected[i], graph)
            ops.append(
                AlignmentOp(
                    op_type="delete_expected",
                    expected_step=expected[i],
                    cost=cost,
                    detail=f"缺失步骤: {expected[i]}",
                )
            )
            total_cost += cost
        else:
            cost = _insertion_cost(observed[i], graph)
            ops.append(
                AlignmentOp(
                    op_type="insert_observed",
                    observed_step=observed[i].step_id,
                    cost=cost,
                    detail=f"额外步骤: {observed[i].step_id}",
                )
            )
            total_cost += cost
    return total_cost, ops


def align_unordered(
    expected: list[str],
    observed: list[ObservedStep],
    graph: PolicyGraph,
) -> tuple[float, list[AlignmentOp]]:
    """Unordered matching: all expected steps must appear, order doesn't matter."""
    ops: list[AlignmentOp] = []
    total_cost = 0.0
    observed_ids = {o.step_id for o in observed}
    remaining_observed = set(observed_ids)

    for exp_id in expected:
        if exp_id in observed_ids:
            ops.append(
                AlignmentOp(op_type="match", expected_step=exp_id, observed_step=exp_id, cost=0.0)
            )
            remaining_observed.discard(exp_id)
        else:
            node = graph.get_node(exp_id)
            is_optional = node.is_optional if node else False
            cost = _COST_MISSING_OPTIONAL if is_optional else _COST_MISSING_REQUIRED
            ops.append(
                AlignmentOp(
                    op_type="delete_expected",
                    expected_step=exp_id,
                    cost=cost,
                    detail=f"缺失步骤: {exp_id}",
                )
            )
            total_cost += cost

    for obs_id in remaining_observed:
        cost = _COST_EXTRA_SAFE
        ops.append(
            AlignmentOp(
                op_type="insert_observed",
                observed_step=obs_id,
                cost=cost,
                detail=f"额外步骤: {obs_id}",
            )
        )
        total_cost += cost

    return total_cost, ops


# ── Temporal Constraint Checking ──


@dataclass
class ConstraintViolation:
    """A violated temporal constraint."""

    constraint_description: str
    source: str
    target: str
    source_turn: int | None = None
    target_turn: int | None = None
    penalty: float = 0.0


def check_temporal_constraints(
    graph: PolicyGraph,
    observed: list[ObservedStep],
    events: list[TraceEvent],
    harness_source_token: str = "",
) -> list[ConstraintViolation]:
    """Check all temporal constraints against observed execution."""
    violations: list[ConstraintViolation] = []
    obs_map = {o.step_id: o for o in observed}

    # Build tool execution order from events
    tool_first_turn: dict[str, int] = {}
    for ev in events:
        if ev.kind == EventKind.TOOL_EXECUTED and ev.source != harness_source_token:
            if ev.tool_name not in tool_first_turn:
                tool_first_turn[ev.tool_name] = ev.turn

    for tc in graph.constraints:
        # Resolve source/target to turns
        src_turn: int | None = None
        tgt_turn: int | None = None

        # Try step_id first, then tool_name
        if tc.source in obs_map:
            src_turn = obs_map[tc.source].first_turn
        elif tc.source in tool_first_turn:
            src_turn = tool_first_turn[tc.source]

        if tc.target in obs_map:
            tgt_turn = obs_map[tc.target].first_turn
        elif tc.target in tool_first_turn:
            tgt_turn = tool_first_turn[tc.target]

        if tc.constraint_type == ConstraintType.BEFORE:
            if src_turn is not None and tgt_turn is not None:
                if src_turn > tgt_turn:
                    violations.append(
                        ConstraintViolation(
                            constraint_description=tc.description,
                            source=tc.source,
                            target=tc.target,
                            source_turn=src_turn,
                            target_turn=tgt_turn,
                            penalty=tc.penalty,
                        )
                    )

        elif tc.constraint_type == ConstraintType.REQUIRES:
            if tgt_turn is not None and src_turn is None:
                violations.append(
                    ConstraintViolation(
                        constraint_description=f"缺失前置: {tc.description}",
                        source=tc.source,
                        target=tc.target,
                        target_turn=tgt_turn,
                        penalty=tc.penalty,
                    )
                )
            elif src_turn is not None and tgt_turn is not None and src_turn > tgt_turn:
                violations.append(
                    ConstraintViolation(
                        constraint_description=tc.description,
                        source=tc.source,
                        target=tc.target,
                        source_turn=src_turn,
                        target_turn=tgt_turn,
                        penalty=tc.penalty,
                    )
                )

    return violations


# ── Branch Verification ──


def verify_branches(
    graph: PolicyGraph,
    observed: list[ObservedStep],
    scenario: OutboundScenario,
    events: list[TraceEvent] | None = None,
) -> dict[str, tuple[bool, str]]:
    """Verify branch decisions against expected branches.

    Three-layer verification:
    1. Target step observed (necessary but not sufficient)
    2. Branch condition evidence found in user responses near the branch point
    3. Alternative branch targets NOT observed (no ambiguity)

    Returns dict: step_id -> (correct, actual_branch_or_reason).
    """
    results: dict[str, tuple[bool, str]] = {}
    obs_set = {o.step_id for o in observed}
    obs_map = {o.step_id: o for o in observed}
    expected_branches = scenario.expected_branch_taken

    for step_id, expected_condition in expected_branches.items():
        step = next((s for s in scenario.instruction_steps if s.step_id == step_id), None)
        if not step or not step.branches:
            results[step_id] = (False, "步骤无分支定义")
            continue

        # Layer 1: which target steps were observed?
        taken_targets: list[tuple[str, str]] = []  # (condition, next_step)
        for branch in step.branches:
            if branch.next_step in obs_set:
                taken_targets.append((branch.condition, branch.next_step))

        # Layer 2: check for condition evidence in user responses
        branch_step_obs = obs_map.get(step_id)
        condition_evidence_found = False
        if events and branch_step_obs:
            branch_turn = branch_step_obs.last_turn
            user_msgs_near_branch = [
                e
                for e in events
                if e.kind == EventKind.USER_RESPONSE
                and branch_turn - 1 <= e.turn <= branch_turn + 2
            ]
            condition_keywords = re.split(r"[，。、/+\s]+", expected_condition.lower())
            condition_keywords = [k for k in condition_keywords if len(k) >= 1]
            # Also generate sub-phrases for compound conditions like "客户在家"
            if len(condition_keywords) == 1 and len(condition_keywords[0]) > 2:
                full = condition_keywords[0]
                subs = [full[i : i + 2] for i in range(len(full) - 1)]
                condition_keywords = list(set(condition_keywords + subs))
            for user_ev in user_msgs_near_branch:
                text = user_ev.content.lower()
                if condition_keywords:
                    hits = sum(1 for k in condition_keywords if k in text)
                    if hits >= 1:
                        condition_evidence_found = True
                        break

        # Layer 3: check for ambiguity (multiple branch targets observed)
        if len(taken_targets) > 1:
            target_names = [t[0] for t in taken_targets]
            results[step_id] = (False, f"分支歧义: 多个目标被观测 {target_names}")
            continue

        if len(taken_targets) == 1:
            taken_condition = taken_targets[0][0]
            if taken_condition == expected_condition:
                if events and not condition_evidence_found:
                    results[step_id] = (True, f"{taken_condition} [目标步骤匹配但条件证据弱]")
                else:
                    results[step_id] = (True, taken_condition)
            else:
                results[step_id] = (False, f"期望 '{expected_condition}'，实际 '{taken_condition}'")
        elif len(taken_targets) == 0:
            results[step_id] = (False, "未观测到分支执行")

    return results


# ── Verification Result ──


@dataclass
class VerificationResult:
    """Complete verification output from trace analysis."""

    # Atom-level verdicts
    satisfied_atoms: list[ScoringAtom] = field(default_factory=list)
    unsatisfied_atoms: list[ScoringAtom] = field(default_factory=list)
    not_applicable_atoms: list[ScoringAtom] = field(default_factory=list)

    # Path analysis
    expected_path: list[str] = field(default_factory=list)
    observed_path: list[str] = field(default_factory=list)
    alignment_ops: list[AlignmentOp] = field(default_factory=list)
    alignment_cost: float = 0.0

    # Constraint violations
    temporal_violations: list[ConstraintViolation] = field(default_factory=list)
    illegal_transitions: list[str] = field(default_factory=list)

    # Branch results
    branch_results: dict[str, tuple[bool, str]] = field(default_factory=dict)

    # Aggregate scores (0-1)
    step_compliance_score: float = 0.0
    branch_accuracy_score: float | None = None
    temporal_order_score: float = 1.0
    alignment_score: float = 0.0  # 1 - normalized_cost

    @property
    def overall_verification_score(self) -> float:
        """Weighted combination of all verification dimensions."""
        scores = [
            (self.step_compliance_score, 0.40),
            (self.alignment_score, 0.25),
            (self.temporal_order_score, 0.15),
        ]
        if self.branch_accuracy_score is not None:
            scores.append((self.branch_accuracy_score, 0.20))
            # Redistribute 0.20 from others
            total_w = sum(w for _, w in scores)
            scores = [(s, w / total_w) for s, w in scores]
        else:
            total_w = sum(w for _, w in scores)
            scores = [(s, w / total_w) for s, w in scores]

        return sum(s * w for s, w in scores)


# ── Main Verification Entry Point ──


def verify_trace(
    scenario: OutboundScenario,
    conversation: Conversation,
    ledger: EventLedger | None = None,
    graph: PolicyGraph | None = None,
) -> VerificationResult:
    """Full trace verification pipeline.

    1. Compile policy graph (or use provided)
    2. Normalize trace events
    3. Extract observed steps
    4. Run DP alignment
    5. Check temporal constraints
    6. Verify branches
    7. Score all atoms
    8. Return comprehensive result
    """
    if graph is None:
        graph = compile_policy_graph(scenario)

    harness_token = ledger.source_token if ledger else ""
    scenario_oid = scenario.call_context.order_id

    # 1. Normalize events
    events = normalize_trace(conversation, ledger)

    # 2. Extract observed steps
    observed = extract_observed_steps(graph, events, scenario_oid, harness_token)
    observed_path = [o.step_id for o in observed]

    # 3. Alignment — dispatch by trace_match_mode (Strands Evals borrowing)
    expected_path = graph.expected_path
    mode = getattr(scenario, "trace_match_mode", "ordered")
    if mode == "strict":
        alignment_cost, alignment_ops = align_strict(expected_path, observed, graph)
    elif mode == "unordered":
        alignment_cost, alignment_ops = align_unordered(expected_path, observed, graph)
    else:
        alignment_cost, alignment_ops = align_sequences(expected_path, observed, graph)

    # Normalize cost to 0-1 score
    max_possible_cost = sum(_deletion_cost(sid, graph) for sid in expected_path)
    if max_possible_cost > 0:
        alignment_score = max(0.0, 1.0 - alignment_cost / max_possible_cost)
    else:
        alignment_score = 1.0

    # 4. Temporal constraints
    temporal_violations = check_temporal_constraints(graph, observed, events, harness_token)
    total_constraints = len(graph.constraints)
    violated_count = len(temporal_violations)
    if total_constraints > 0:
        temporal_score = max(0.0, 1.0 - violated_count / total_constraints)
    else:
        temporal_score = 1.0

    # 5. Branch verification
    branch_results = verify_branches(graph, observed, scenario, events=events)
    if branch_results:
        correct_branches = sum(1 for ok, _ in branch_results.values() if ok)
        branch_score: float | None = correct_branches / len(branch_results)
    elif any(s.branches for s in scenario.instruction_steps):
        branch_score = None  # has branches but no expectations
    else:
        branch_score = 1.0

    # 6. Score all atoms
    obs_set = {o.step_id for o in observed}
    obs_map = {o.step_id: o for o in observed}

    # Build successful tool set from events
    successful_tools: set[str] = set()
    for ev in events:
        if ev.kind == EventKind.TOOL_EXECUTED and ev.source != harness_token:
            successful_tools.add(ev.tool_name)

    satisfied: list[ScoringAtom] = []
    unsatisfied: list[ScoringAtom] = []
    not_applicable: list[ScoringAtom] = []

    for atom in graph.atoms:
        atom_copy = ScoringAtom(
            atom_id=atom.atom_id,
            dimension=atom.dimension,
            description=atom.description,
            weight=atom.weight,
            step_id=atom.step_id,
        )

        if atom.dimension == "step_compliance":
            if atom.step_id in obs_set:
                atom_copy.status = AtomStatus.SATISFIED
                atom_copy.score_delta = atom.weight
                obs = obs_map.get(atom.step_id)
                if obs:
                    atom_copy.evidence_event_ids = obs.evidence_events
                    atom_copy.reason = f"步骤在第{obs.first_turn}轮完成"
                satisfied.append(atom_copy)
            else:
                node = graph.get_node(atom.step_id)
                if node and node.is_optional and node.is_branch_target:
                    # Check if this branch was supposed to be taken
                    if atom.step_id not in expected_path:
                        atom_copy.status = AtomStatus.NOT_APPLICABLE
                        atom_copy.reason = "分支未触发，不适用"
                        not_applicable.append(atom_copy)
                        continue
                atom_copy.status = AtomStatus.UNSATISFIED
                atom_copy.score_delta = -atom.weight
                atom_copy.reason = "步骤未完成"
                unsatisfied.append(atom_copy)

        elif atom.dimension == "branch_accuracy":
            br = branch_results.get(atom.step_id)
            if br is None:
                atom_copy.status = AtomStatus.NOT_APPLICABLE
                not_applicable.append(atom_copy)
            elif br[0]:
                atom_copy.status = AtomStatus.SATISFIED
                atom_copy.score_delta = atom.weight
                atom_copy.reason = f"分支正确: {br[1]}"
                satisfied.append(atom_copy)
            else:
                atom_copy.status = AtomStatus.UNSATISFIED
                atom_copy.score_delta = -atom.weight
                atom_copy.reason = f"分支错误: {br[1]}"
                unsatisfied.append(atom_copy)

        elif atom.dimension == "tool_usage":
            tool_name = atom.atom_id.replace("tool_", "")
            if tool_name in successful_tools:
                atom_copy.status = AtomStatus.SATISFIED
                atom_copy.score_delta = atom.weight
                atom_copy.reason = "工具成功调用"
                # Find evidence
                for ev in events:
                    if ev.kind == EventKind.TOOL_EXECUTED and ev.tool_name == tool_name:
                        atom_copy.evidence_event_ids.append(ev.event_id)
                        break
                satisfied.append(atom_copy)
            else:
                atom_copy.status = AtomStatus.UNSATISFIED
                atom_copy.score_delta = -atom.weight
                atom_copy.reason = "工具未成功调用"
                unsatisfied.append(atom_copy)

        elif atom.dimension == "temporal_order":
            # Check if corresponding constraint was violated
            idx_str = atom.atom_id.replace("temporal_", "")
            try:
                idx = int(idx_str)
            except ValueError:
                not_applicable.append(atom_copy)
                continue
            if idx < len(graph.constraints):
                constraint = graph.constraints[idx]
                violated = any(
                    v.source == constraint.source and v.target == constraint.target
                    for v in temporal_violations
                )
                if violated:
                    atom_copy.status = AtomStatus.UNSATISFIED
                    atom_copy.score_delta = -atom.weight
                    atom_copy.reason = f"时序违反: {constraint.description}"
                    unsatisfied.append(atom_copy)
                else:
                    atom_copy.status = AtomStatus.SATISFIED
                    atom_copy.score_delta = atom.weight
                    atom_copy.reason = "时序正确"
                    satisfied.append(atom_copy)
            else:
                not_applicable.append(atom_copy)

    # Step compliance score
    step_atoms = [a for a in graph.atoms if a.dimension == "step_compliance"]
    applicable_step_atoms = [
        a for a in step_atoms if not any(na.atom_id == a.atom_id for na in not_applicable)
    ]
    if applicable_step_atoms:
        completed_count = sum(
            1 for a in applicable_step_atoms if any(s.atom_id == a.atom_id for s in satisfied)
        )
        step_compliance_score = completed_count / len(applicable_step_atoms)
    else:
        step_compliance_score = 1.0

    # Detect illegal transitions
    illegal: list[str] = []
    for i in range(len(observed) - 1):
        src = observed[i].step_id
        tgt = observed[i + 1].step_id
        legal_targets = {t for t, _ in graph.successors(src)}
        # Also allow skipping to any later step (not strictly illegal, just suboptimal)
        src_node = graph.get_node(src)
        tgt_node = graph.get_node(tgt)
        if tgt not in legal_targets and src_node and tgt_node:
            if tgt_node.order < src_node.order:
                illegal.append(
                    f"逆序跳转: {src}(第{src_node.order}步) → {tgt}(第{tgt_node.order}步)"
                )

    return VerificationResult(
        satisfied_atoms=satisfied,
        unsatisfied_atoms=unsatisfied,
        not_applicable_atoms=not_applicable,
        expected_path=expected_path,
        observed_path=observed_path,
        alignment_ops=alignment_ops,
        alignment_cost=alignment_cost,
        temporal_violations=temporal_violations,
        illegal_transitions=illegal,
        branch_results=branch_results,
        step_compliance_score=round(step_compliance_score, 3),
        branch_accuracy_score=round(branch_score, 3) if branch_score is not None else None,
        temporal_order_score=round(temporal_score, 3),
        alignment_score=round(alignment_score, 3),
    )
