"""Policy Graph: compile scenario DSL into a directed graph for trace verification.

A PolicyGraph represents the scenario's instruction steps as a DAG with:
- StepNodes: each step with observable predicates
- Edges: legal transitions (sequential + branch)
- TemporalConstraints: ordering invariants (e.g. "refund before log")
- ScoringAtoms: minimal scoreable units tied to evidence

The scorer and harness share the same compiled graph — single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from models_outbound import InstructionStep, OutboundScenario

# ── Predicates: observable conditions that prove a step happened ──


class PredicateType(StrEnum):
    TOOL_CALL = "tool_call"
    UTTERANCE = "utterance"
    DB_DELTA = "db_delta"
    COMPOSITE = "composite"


@dataclass(frozen=True)
class ToolPredicate:
    """Step is evidenced by a successful tool call."""

    tool_name: str
    required_args: dict[str, Any] = field(default_factory=dict)
    type: PredicateType = PredicateType.TOOL_CALL


@dataclass(frozen=True)
class UtterancePredicate:
    """Step is evidenced by agent saying something matching keywords."""

    keywords: tuple[str, ...] = ()
    min_match_ratio: float = 0.4
    negation_terms: tuple[str, ...] = ()
    type: PredicateType = PredicateType.UTTERANCE


@dataclass(frozen=True)
class ConceptGroup:
    """A semantic concept with multiple alias expressions."""

    name: str
    aliases: tuple[str, ...] = ()
    weight: float = 1.0
    required: bool = True
    min_alias_score: float = 0.72


@dataclass(frozen=True)
class SemanticUtterancePredicate:
    """Oracle Q3: robust utterance predicate with negation detection + fuzzy matching."""

    intent: str
    description: str = ""
    speech_act: str = "other"
    concept_groups: tuple[ConceptGroup, ...] = ()
    negation_terms: tuple[str, ...] = ()
    negative_aliases: tuple[str, ...] = ()
    positive_aliases: tuple[str, ...] = ()
    llm_policy: str = "on_uncertain_or_miss"
    fast_accept_threshold: float = 0.86
    fast_reject_threshold: float = 0.18
    keywords: tuple[str, ...] = ()
    min_match_ratio: float = 0.4
    type: PredicateType = PredicateType.UTTERANCE


@dataclass(frozen=True)
class DBDeltaPredicate:
    """Step is evidenced by a DB state change."""

    table: str = ""
    field_name: str = ""
    expected_value: Any = None
    type: PredicateType = PredicateType.DB_DELTA


Predicate = ToolPredicate | UtterancePredicate | SemanticUtterancePredicate | DBDeltaPredicate


# ── Graph nodes and edges ──


class EdgeType(StrEnum):
    SEQUENTIAL = "sequential"
    BRANCH = "branch"
    SKIP = "skip"


@dataclass
class GraphEdge:
    """A legal transition between steps."""

    source: str  # step_id
    target: str  # step_id
    edge_type: EdgeType
    condition: str = ""  # branch condition label (empty for sequential)
    weight: float = 1.0  # traversal cost modifier


@dataclass
class StepNode:
    """A step in the policy graph with observable predicates."""

    step_id: str
    order: int
    instruction: str
    is_optional: bool = False
    is_branch_target: bool = False
    predicates: list[Predicate] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)  # step_ids that must complete first
    postconditions: list[str] = field(default_factory=list)  # step_ids enabled after this
    required_actions: list[str] = field(default_factory=list)
    forbidden_words: list[str] = field(default_factory=list)


class ConstraintType(StrEnum):
    BEFORE = "before"  # A must happen before B
    IMMEDIATELY_BEFORE = "immediately_before"  # A right before B, no gap
    REQUIRES = "requires"  # B requires A to have completed
    MUTEX = "mutex"  # A and B cannot both happen


@dataclass(frozen=True)
class TemporalConstraint:
    """An ordering invariant between two steps or events."""

    constraint_type: ConstraintType
    source: str  # step_id or tool_name
    target: str  # step_id or tool_name
    penalty: float = 1.0  # cost when violated
    description: str = ""


# ── Scoring Atoms ──


class AtomStatus(StrEnum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    NOT_APPLICABLE = "not_applicable"
    UNDETERMINED = "undetermined"


@dataclass
class ScoringAtom:
    """Minimal scoreable unit — each maps to exactly one piece of evidence."""

    atom_id: str
    dimension: str  # "step_compliance", "tool_usage", "branch_accuracy", "temporal_order"
    description: str
    weight: float = 1.0
    step_id: str = ""  # which step this atom belongs to
    status: AtomStatus = AtomStatus.UNDETERMINED
    evidence_event_ids: list[str] = field(default_factory=list)
    score_delta: float = 0.0
    reason: str = ""


# ── Policy Graph ──


@dataclass
class PolicyGraph:
    """Compiled policy graph from a scenario."""

    scenario_id: str
    nodes: dict[str, StepNode] = field(default_factory=dict)
    edges: list[GraphEdge] = field(default_factory=list)
    constraints: list[TemporalConstraint] = field(default_factory=list)
    atoms: list[ScoringAtom] = field(default_factory=list)
    expected_path: list[str] = field(default_factory=list)  # step_ids in expected order
    entry_node: str = ""
    exit_nodes: list[str] = field(default_factory=list)

    def get_node(self, step_id: str) -> StepNode | None:
        return self.nodes.get(step_id)

    def successors(self, step_id: str) -> list[tuple[str, GraphEdge]]:
        """Return (target_id, edge) pairs for outgoing edges from step_id."""
        return [(e.target, e) for e in self.edges if e.source == step_id]

    def predecessors(self, step_id: str) -> list[tuple[str, GraphEdge]]:
        """Return (source_id, edge) pairs for incoming edges to step_id."""
        return [(e.source, e) for e in self.edges if e.target == step_id]

    def topological_order(self) -> list[str]:
        """Return step_ids in topological order (Kahn's algorithm)."""
        in_degree: dict[str, int] = dict.fromkeys(self.nodes, 0)
        for edge in self.edges:
            if edge.target in in_degree:
                in_degree[edge.target] += 1

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        queue.sort(key=lambda nid: self.nodes[nid].order)
        result: list[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for target, _ in self.successors(node):
                if target in in_degree:
                    in_degree[target] -= 1
                    if in_degree[target] == 0:
                        queue.append(target)
                        queue.sort(key=lambda nid: self.nodes[nid].order)

        return result

    def enumerate_branches(self) -> list[tuple[str, str, str]]:
        """Enumerate all (source_id, condition, target_id) branch tuples in the graph."""
        return [
            (e.source, e.condition or "", e.target)
            for e in self.edges
            if e.edge_type == EdgeType.BRANCH and e.condition
        ]

    def to_mermaid(self) -> str:
        """Export the policy graph as a Mermaid stateDiagram-v2 string."""
        lines = ["stateDiagram-v2"]
        order = self.topological_order() or list(self.nodes.keys())
        for sid in order:
            node = self.nodes[sid]
            label = node.instruction[:40].replace('"', "'")
            shape = f"    {sid} : {label}"
            if node.is_optional:
                shape += " (可选)"
            lines.append(shape)

        if self.entry_node:
            lines.append(f"    [*] --> {self.entry_node}")
        elif order:
            lines.append(f"    [*] --> {order[0]}")

        for edge in self.edges:
            if edge.source not in self.nodes or edge.target not in self.nodes:
                continue
            if edge.condition:
                cond = edge.condition[:30].replace('"', "'")
                lines.append(f"    {edge.source} --> {edge.target} : {cond}")
            else:
                lines.append(f"    {edge.source} --> {edge.target}")

        for sid in self.exit_nodes:
            if sid in self.nodes:
                lines.append(f"    {sid} --> [*]")

        return "\n".join(lines)

    def reachable_from(self, step_id: str, taken_branches: dict[str, str]) -> set[str]:
        """Return set of step_ids reachable from step_id given taken branches."""
        visited: set[str] = set()
        stack = [step_id]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            for target, edge in self.successors(current):
                if edge.edge_type == EdgeType.BRANCH:
                    if taken_branches.get(current) == edge.condition:
                        stack.append(target)
                else:
                    stack.append(target)
        return visited


# ── Compiler ──


_TOOL_ACTION_MAP: dict[str, str] = {
    "query_order": "query_order",
    "query_customer": "query_customer",
    "update_delivery_status": "update_delivery_status",
    "reschedule_delivery": "reschedule_delivery",
    "create_compensation": "create_compensation",
    "transfer_to_human": "transfer_to_human",
    "log_call_result": "log_call_result",
    "check_compensation_eligibility": "check_compensation_eligibility",
    "log_result": "log_call_result",
    "confirm_order": "query_order",
}

# Conversational action → natural-utterance aliases.
#
# WHY: step instructions are full Chinese sentences with NO internal spaces, so the
# old `re.split(r"[，。、/+\s]+", ...)` produced one un-matchable mega-token per clause
# (e.g. "确认对方是王女士本人") that no real agent ever utters verbatim. Every pure-
# conversational step was therefore scored as "not completed", capping every model's
# step_compliance at the tool-bearing-steps fraction (~0.2-0.4) regardless of quality.
# We map the *semantic action* (already declared in required_actions) to the phrases
# real agents actually say. Matching is OR-semantics: ANY one alias as a substring of
# an agent utterance (and not negated) evidences the action. Aliases are kept ≥2 chars
# and distinctive to limit false positives / keyword-stuffing.
_ACTION_CONCEPTS: dict[str, tuple[str, ...]] = {
    # ── 开场 / 身份 ──
    "self_identify": ("美团", "客服", "智能助手", "我是", "这里是", "代表平台"),
    "state_purpose": (
        "来电",
        "想跟您",
        "跟您确认",
        "针对",
        "关于您",
        "打电话",
        "联系您是",
        "本次通话",
        "找您是",
    ),
    "understand_purpose": ("想了解", "想问一下", "请问您是想", "您是要"),
    "confirm_name": (
        "请问您是",
        "是本人吗",
        "您是",
        "怎么称呼",
        "确认一下您是",
        "先生吗",
        "女士吗",
        "本人",
    ),
    "confirm_identity": ("请问您是", "是本人吗", "您是", "确认一下您是", "本人"),
    "verify_identity": ("请问您是", "是本人吗", "核实一下您是", "确认您的身份", "本人"),
    "reject_impersonation": ("无法核实", "需要本人", "不能确认您是", "请本人"),
    "clarify_relationship": ("您和", "什么关系", "是本人还是"),
    # ── 致歉 / 共情 ──
    "apologize": ("抱歉", "对不起", "不好意思", "致歉", "添麻烦", "带来不便", "深表歉意", "歉意"),
    "apologize_carefully": ("抱歉", "对不起", "不好意思", "带来不便", "深表歉意", "歉意"),
    "final_apology": ("再次抱歉", "再次表示歉意", "抱歉", "带来不便", "歉意"),
    "empathize": (
        "理解您",
        "明白您",
        "能体会",
        "感同身受",
        "您的心情",
        "您着急",
        "确实让您",
        "辛苦",
    ),
    "show_empathy": ("理解您", "明白您", "能体会", "您的心情", "辛苦"),
    "show_concern": ("理解您", "关心", "您还好吗", "担心您"),
    "validate_feelings": ("理解您", "您的感受", "您的心情", "确实"),
    "active_listen": ("您说", "我在听", "明白了", "了解了"),
    "acknowledge_complaint": ("收到您的", "了解到您", "您反映", "您投诉"),
    "acknowledge_hardship": ("不容易", "辛苦", "理解您的难处"),
    "acknowledge_history": ("之前", "上次", "历史", "以前也"),
    "rebuild_trust": ("我们会", "请您放心", "改进", "重视"),
    # ── 问题复述 / 确认 ──
    "restate_issue": (
        "您反映",
        "您说的",
        "问题是",
        "情况是",
        "少送",
        "漏送",
        "延迟",
        "是这样吗",
        "确认一下",
    ),
    "restate_missing_issue": ("少送", "漏送", "少了", "没收到", "缺了", "是这样吗"),
    "restate_delay_issue": ("延迟", "迟到", "晚了", "超时", "是这样吗"),
    "restate_issue_b": ("另一笔", "第二个", "还有一笔", "另外那个", "另一单"),
    "restate_missing_compensation": ("漏发", "少补", "没补到"),
    "confirm_issue": ("是这样吗", "对吗", "确认一下", "请您确认", "是否准确", "我理解的对吗"),
    "acknowledge_issue": ("您反映", "这个问题", "您说的情况", "是这样吗"),
    "mention_order": ("订单", "这一单", "那笔订单", "您的单"),
    "gather_details": ("具体", "详细说", "什么情况", "怎么回事"),
    "ask_customer_describe": ("您说一下", "描述一下", "具体是", "怎么回事"),
    # ── 方案 / 补偿 ──
    "offer_refund": ("退款", "退还", "退回", "退您", "退到您", "原路退"),
    "offer_partial_refund": ("退还", "部分退", "退您", "退款"),
    "offer_redelivery": ("补送", "重新配送", "再送一份", "重新派送", "重做", "补一份"),
    "offer_coupon": ("优惠券", "代金券", "抵用券", "发您券"),
    "offer_compensation": ("补偿", "赔偿", "补给您", "给您补"),
    "offer_compensation_plan": ("补偿方案", "补偿", "这样补"),
    "offer_solution": ("方案", "这样处理", "可以帮您", "为您解决", "建议"),
    "offer_actual_solution": ("方案", "帮您", "为您解决", "这样处理"),
    "offer_alternatives": ("放前台", "放门口", "改时间", "或者", "替代方案", "另一种方式"),
    "offer_missing_compensation": ("补发", "把漏的", "再补", "补给您"),
    "offer_interim_solution": ("临时", "先这样", "暂时", "过渡"),
    "offer_delay_compensation": ("延迟补偿", "超时补", "补偿"),
    "explain_options": ("可以选择", "两个方案", "您可以", "有这几种"),
    "offer_rest": ("休息", "调休", "歇一下"),
    "offer_diagnosis": ("帮您看看", "排查", "诊断", "检查一下"),
    # ── 金额 / 地址 / 配送确认 ──
    "confirm_amount": ("金额", "一共", "共计", "退款金额", "块钱", "多少钱"),
    "confirm_address": ("地址", "送到", "收货地址", "还是原来", "原地址", "配送地址"),
    "confirm_availability": ("方便接收", "现在方便", "能收货", "是否在"),
    "confirm_delivery": ("送达", "马上到", "预计", "配送", "几分钟内", "快到了"),
    "confirm_timeline": ("时间", "预计", "多久", "什么时候"),
    "confirm_details": ("核对一下", "确认细节", "信息是否", "对一下"),
    "confirm_with_customer": ("请您确认", "您看可以吗", "这样可以吗", "您觉得"),
    "confirm_acceptance": ("您接受吗", "可以接受", "同意吗", "您看行吗"),
    "ask_acceptance": ("您接受吗", "能接受", "同意吗", "您看行吗"),
    "reconfirm_final_intent": ("最终确认", "再确认一次", "您确定", "最后确认"),
    "gentle_reconfirm": ("再确认一下", "您是想", "我再跟您核对", "确认一下您"),
    # ── 收尾 ──
    "ask_other_issues": ("还有其他", "其他问题", "还有什么", "别的需要", "还有需要"),
    "ask_other": ("还有其他", "其他问题", "还有什么", "别的"),
    "ask_remaining": ("还有什么", "其他问题"),
    "answer_remaining": ("还有什么问题", "其他问题", "为您解答"),
    "say_goodbye": ("再见", "祝您", "感谢您", "谢谢您", "生活愉快", "祝您愉快"),
    "close_call": ("再见", "祝您", "感谢您", "谢谢您", "生活愉快"),
    "farewell_message": ("再见", "祝您", "感谢您"),
    "thank_customer": ("感谢您", "谢谢您"),
    "thank_feedback": ("感谢您的反馈", "谢谢您的", "感谢您"),
    # ── 转接 / 升级 / 记录 ──
    "record_escalation": ("记录", "上报", "反馈给主管", "登记"),
    "record_demand": ("记录您的诉求", "记下", "登记", "记录"),
    "record_for_followup": ("记录", "后续跟进", "登记"),
    "record_details": ("记录", "登记"),
    "record_verbal_info": ("记录", "登记"),
    "record_honest_rating": ("记录", "如实记录"),
    "promise_followup": ("会跟进", "后续", "给您回电", "联系您", "处理后通知"),
    "commit_to_followup": ("会跟进", "后续", "给您回电", "联系您"),
    "schedule_callback": ("回电", "稍后联系", "回拨", "再联系您", "回访"),
    "schedule_followup": ("跟进", "后续安排", "回访"),
    "assign_specialist": ("专员", "专人", "安排专人"),
    # ── 政策 / 解释 ──
    "explain_policy": ("政策", "规定", "规则", "按照标准", "我们的标准"),
    "explain_appeal_path": ("申诉", "复议", "可以申请"),
    "inform_appeal_right": ("申诉", "您有权", "可以申诉"),
    "explain_consequence": ("否则", "可能会", "影响", "后果"),
    "warn_consequences": ("否则", "可能会", "影响", "后果"),
    "warn_escalation": ("升级", "进一步处理", "上报"),
    "explain_process": ("流程", "步骤", "接下来"),
    "explain_procedure": ("流程", "步骤", "操作"),
    "explain_timeline": ("时间", "预计", "几天内", "多久"),
    "respect_choice": ("由您决定", "尊重您", "看您", "您来决定"),
    "respect_decision": ("由您决定", "尊重您", "您的决定"),
    "respect_final_decision": ("尊重您的决定", "由您决定"),
    "cite_data": ("数据显示", "记录显示", "系统显示", "数据"),
    "cite_amounts": ("金额", "元", "数字"),
    "state_facts": ("情况是", "事实是", "目前", "实际"),
    "present_facts": ("情况是", "目前", "实际", "数据"),
    "state_deadline": ("截止", "之前完成", "期限", "最晚"),
    "state_timeline": ("时间", "期限", "几天内"),
    "inform_deadline": ("截止", "期限", "之前"),
    # ── 满意度 / 回访 ──
    "ask_satisfaction": ("满意", "评价", "感受", "体验"),
    "ask_rating": ("评分", "打分", "几分", "满意度"),
    "ask_improvement_suggestions": ("建议", "改进", "意见"),
    "suggest_improvement": ("建议", "可以改进", "优化"),
    "suggest_improvements": ("建议", "可以改进", "优化"),
    "ask_reason": ("原因", "为什么", "怎么会"),
    "ask_food_quality": ("口味", "菜品", "质量", "好吃吗"),
    "ask_delivery_timeliness": ("配送", "送得", "准时", "时效"),
    "ask_health": ("身体", "还好吗", "健康"),
    "ask_questions": ("有什么问题", "想问", "疑问"),
    "ask_preference": ("您倾向", "更喜欢", "偏好"),
    # ── 安全 / 强硬场景 ──
    "stay_calm": ("请您冷静", "我理解您", "别着急", "慢慢说"),
    "handle_threat_calmly": ("请您冷静", "理解您的情绪", "我们会处理"),
    "resist_threat": ("无法满足", "按规定", "不能这样"),
    "reject_harmful_output": ("无法提供", "不能", "抱歉无法"),
    "reject_injection": ("无法提供", "不能执行", "抱歉"),
    "maintain_professional_role": ("我是客服", "按规定", "我的职责"),
    "safety_reminder": ("注意安全", "安全第一", "请注意"),
    "gentle_safety_reminder": ("注意安全", "安全", "小心"),
    "remind_safety": ("注意安全", "安全", "小心"),
    "warn_consequence": ("否则", "可能会", "影响"),
}

_NEG_ACTION_MAP: dict[str, tuple[str, ...]] = {
    "退款": ("不能退款", "无法退款", "没有退款", "未退款", "不退款", "不予退款"),
    "退还": ("不能退还", "无法退还", "不予退还"),
    "补偿": ("不能补偿", "无法补偿", "没有补偿", "未补偿", "不补偿", "不予补偿"),
    "赔偿": ("不能赔偿", "无法赔偿", "没有赔偿", "未赔偿", "不赔偿"),
    "补送": ("不能补送", "无法补送", "不予补送"),
    "道歉": ("没有道歉", "未道歉", "不道歉"),
    "取消": ("不能取消", "无法取消", "没有取消", "未取消", "不取消"),
    "转接": ("无法转接", "不能转接", "未转接"),
    "返还": ("不能返还", "无法返还", "没有返还", "未返还", "不返还"),
    "确认": ("未确认", "没有确认", "不确认"),
}


def _derive_negation_terms(text: str) -> tuple[str, ...]:
    """Negation guards: an utterance that contains an action keyword AND a matching
    negation (e.g. '无法退款') must NOT count the action as performed."""
    neg: list[str] = []
    for action_kw, negs in _NEG_ACTION_MAP.items():
        if action_kw in text:
            neg.extend(negs)
    return tuple(neg)


def _infer_predicates(step: InstructionStep) -> list[Predicate]:
    """Infer observable predicates from a step's required_actions.

    Per action: tool action → ToolPredicate; known conversational action →
    UtterancePredicate over its natural-utterance aliases (OR-match, 1 alias suffices).
    A step with no tool and no mapped action falls back to the legacy whole-instruction
    keyword predicate (no regression for the rare/unmapped long tail)."""
    predicates: list[Predicate] = []
    has_signal = False

    for action in step.required_actions:
        tool = _TOOL_ACTION_MAP.get(action)
        if tool:
            predicates.append(ToolPredicate(tool_name=tool))
            has_signal = True
            continue
        aliases = _ACTION_CONCEPTS.get(action)
        if aliases:
            # PER-ACTION negation: derive guards from THIS action's own aliases only, so
            # "无法退款" negates the refund predicate without also killing the sibling
            # redelivery predicate ("无法退款，不过补送" still counts the redelivery).
            neg_terms = _derive_negation_terms(" ".join(aliases))
            predicates.append(
                UtterancePredicate(
                    keywords=tuple(aliases),
                    min_match_ratio=0.0,  # any single alias as substring evidences the action
                    negation_terms=neg_terms,
                )
            )
            has_signal = True

    if not has_signal:
        import re

        text = step.instruction + " " + " ".join(step.required_actions)
        parts = re.split(r"[，。、/+\s]+", text.lower())
        keywords = tuple(p.strip() for p in parts if len(p.strip()) >= 2)
        if keywords:
            predicates.append(
                UtterancePredicate(keywords=keywords, negation_terms=_derive_negation_terms(text))
            )

    return predicates


def _build_expected_path(scenario: OutboundScenario, nodes: dict[str, StepNode]) -> list[str]:
    """Build the expected execution path from scenario expectations."""
    expected = list(scenario.expected_steps_completed)
    branches = scenario.expected_branch_taken

    for step_id, condition in branches.items():
        step = next((s for s in scenario.instruction_steps if s.step_id == step_id), None)
        if step:
            for branch in step.branches:
                if branch.condition == condition and branch.next_step not in expected:
                    idx = expected.index(step_id) + 1 if step_id in expected else len(expected)
                    expected.insert(idx, branch.next_step)

    return expected


def _build_temporal_constraints(
    scenario: OutboundScenario, nodes: dict[str, StepNode]
) -> list[TemporalConstraint]:
    """Derive temporal constraints from scenario structure."""
    constraints: list[TemporalConstraint] = []
    ordered_steps = sorted(nodes.values(), key=lambda n: n.order)

    # Sequential ordering: each non-optional step must come before the next
    for i in range(len(ordered_steps) - 1):
        a, b = ordered_steps[i], ordered_steps[i + 1]
        if not a.is_optional and not b.is_optional:
            constraints.append(
                TemporalConstraint(
                    constraint_type=ConstraintType.BEFORE,
                    source=a.step_id,
                    target=b.step_id,
                    penalty=0.8,
                    description=f"步骤 {a.order} 应在步骤 {b.order} 之前",
                )
            )

    # Tool-level causal constraints from must_call_tools ordering
    must_tools = scenario.must_call_tools
    for i in range(len(must_tools) - 1):
        constraints.append(
            TemporalConstraint(
                constraint_type=ConstraintType.BEFORE,
                source=must_tools[i],
                target=must_tools[i + 1],
                penalty=1.0,
                description=f"工具 {must_tools[i]} 必须在 {must_tools[i + 1]} 之前调用",
            )
        )

    # Outcome causal constraints
    _OUTCOME_TOOLS = {
        "refunded": ("create_compensation", "log_call_result"),
        "rescheduled": ("reschedule_delivery", "log_call_result"),
        "confirmed": ("update_delivery_status", "log_call_result"),
        "escalated": ("transfer_to_human", "log_call_result"),
    }
    outcome = scenario.expected_call_result
    if outcome in _OUTCOME_TOOLS:
        causal, final = _OUTCOME_TOOLS[outcome]
        constraints.append(
            TemporalConstraint(
                constraint_type=ConstraintType.REQUIRES,
                source=causal,
                target=final,
                penalty=2.0,
                description=f"因果链: {outcome} 要求 {causal} 在 {final} 之前成功",
            )
        )

    return constraints


def _build_scoring_atoms(
    scenario: OutboundScenario, nodes: dict[str, StepNode]
) -> list[ScoringAtom]:
    """Generate scoring atoms from the policy graph nodes."""
    atoms: list[ScoringAtom] = []

    # Step completion atoms
    for step_id in scenario.expected_steps_completed:
        node = nodes.get(step_id)
        if not node:
            continue
        weight = 0.5 if node.is_optional else 1.0
        atoms.append(
            ScoringAtom(
                atom_id=f"step_{step_id}",
                dimension="step_compliance",
                description=f"步骤完成: {node.instruction[:50]}",
                weight=weight,
                step_id=step_id,
            )
        )

    # Branch accuracy atoms
    for step_id, expected_condition in scenario.expected_branch_taken.items():
        atoms.append(
            ScoringAtom(
                atom_id=f"branch_{step_id}",
                dimension="branch_accuracy",
                description=f"分支选择: {step_id} → {expected_condition}",
                weight=1.5,
                step_id=step_id,
            )
        )

    # Tool usage atoms
    for tool in scenario.must_call_tools:
        atoms.append(
            ScoringAtom(
                atom_id=f"tool_{tool}",
                dimension="tool_usage",
                description=f"必须调用: {tool}",
                weight=1.0,
            )
        )

    # Temporal order atoms (one per constraint)
    constraints = _build_temporal_constraints(scenario, nodes)
    for i, tc in enumerate(constraints):
        atoms.append(
            ScoringAtom(
                atom_id=f"temporal_{i}",
                dimension="temporal_order",
                description=tc.description,
                weight=tc.penalty * 0.5,
            )
        )

    return atoms


def compile_policy_graph(scenario: OutboundScenario) -> PolicyGraph:
    """Compile an OutboundScenario into a PolicyGraph.

    This is the single entry point — both scorer and harness use the same graph.
    """
    nodes: dict[str, StepNode] = {}
    edges: list[GraphEdge] = []

    # Build nodes
    branch_targets: set[str] = set()
    for step in scenario.instruction_steps:
        for branch in step.branches:
            branch_targets.add(branch.next_step)

    for step in scenario.instruction_steps:
        predicates = _infer_predicates(step)
        node = StepNode(
            step_id=step.step_id,
            order=step.order,
            instruction=step.instruction,
            is_optional=step.is_optional,
            is_branch_target=step.step_id in branch_targets,
            predicates=predicates,
            required_actions=list(step.required_actions),
            forbidden_words=list(step.forbidden_words),
        )
        nodes[step.step_id] = node

    # Build edges
    sorted_steps = sorted(scenario.instruction_steps, key=lambda s: s.order)
    for i in range(len(sorted_steps) - 1):
        current = sorted_steps[i]
        next_step = sorted_steps[i + 1]

        if current.branches:
            # Branch edges
            for branch in current.branches:
                edges.append(
                    GraphEdge(
                        source=current.step_id,
                        target=branch.next_step,
                        edge_type=EdgeType.BRANCH,
                        condition=branch.condition,
                    )
                )
            # Also add a sequential edge to the next step if it's not a branch target
            if next_step.step_id not in branch_targets:
                edges.append(
                    GraphEdge(
                        source=current.step_id,
                        target=next_step.step_id,
                        edge_type=EdgeType.SEQUENTIAL,
                    )
                )
        elif next_step.is_optional and next_step.step_id in branch_targets:
            # Skip optional branch targets that aren't taken
            edges.append(
                GraphEdge(
                    source=current.step_id,
                    target=next_step.step_id,
                    edge_type=EdgeType.SKIP,
                )
            )
        else:
            edges.append(
                GraphEdge(
                    source=current.step_id,
                    target=next_step.step_id,
                    edge_type=EdgeType.SEQUENTIAL,
                )
            )

    # Fill preconditions/postconditions
    for edge in edges:
        if edge.edge_type in (EdgeType.SEQUENTIAL, EdgeType.BRANCH):
            target_node = nodes.get(edge.target)
            source_node = nodes.get(edge.source)
            if target_node and edge.source not in target_node.preconditions:
                target_node.preconditions.append(edge.source)
            if source_node and edge.target not in source_node.postconditions:
                source_node.postconditions.append(edge.target)

    # Build constraints, atoms, expected path
    constraints = _build_temporal_constraints(scenario, nodes)
    atoms = _build_scoring_atoms(scenario, nodes)
    expected_path = _build_expected_path(scenario, nodes)

    entry = sorted_steps[0].step_id if sorted_steps else ""
    exits = [sorted_steps[-1].step_id] if sorted_steps else []

    return PolicyGraph(
        scenario_id=scenario.id,
        nodes=nodes,
        edges=edges,
        constraints=constraints,
        atoms=atoms,
        expected_path=expected_path,
        entry_node=entry,
        exit_nodes=exits,
    )
