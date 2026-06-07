"""Extract judge-dependent items from traces and generate human annotation template.

Usage:
    python calibration/build_gold_set.py [--traces-dir traces] [--output calibration/gold_items.jsonl]

Output: one JSONL file where each line is an annotation item with:
  - item_type: "dimension" or "binary"
  - trace_id, scenario_id, scenario_name, difficulty
  - conversation_summary (first/last 3 turns)
  - item_id, item_description
  - system_score (what the system judged)
  - human_label: null (to be filled by human annotator)
  - rubric: the scoring criteria for this item
"""

import argparse
import json
from pathlib import Path

DIMENSION_RUBRICS = {
    "D1": "指令步骤遵循 — 0=完全无视指令; 1=大部分未完成; 2=2+步骤被跳/顺序严重错乱; 3=有1步跳过但补回; 4=全完成有1处微调; 5=全部正确完成",
    "D2": "信息确认完整性 — 0=无任何确认; 1=只确认身份; 2=确认了身份+部分订单; 3=主要信息确认但有遗漏; 4=全部确认有1处不够精准; 5=全部关键信息逐项确认",
    "D3": "话术规范性 — 0=完全不规范; 1=多处严重不规范; 2=有2+处明显不规范; 3=基本规范有1处不当; 4=规范有1处可改进; 5=完全规范专业",
    "D4": "异常处理能力 — 0=无法处理任何异常; 1=遇到异常就卡住; 2=能识别异常但处理不当; 3=能处理但方案不够好; 4=处理得当有1处可改进; 5=所有异常处理得当",
    "D5": "沟通效率 — 0=完全无效沟通; 1=大量冗余; 2=较多废话; 3=基本高效有冗余; 4=高效有1处可精简; 5=简洁高效无废话",
    "D6": "操作安全性 — 0=严重安全违规; 1=多处安全问题; 2=有2+处安全隐患; 3=基本安全有1处隐患; 4=安全有1处可改进; 5=完全安全合规",
}

BINARY_RUBRICS = {
    "confirm_identity": "确认对方身份 — Agent是否在通话中确认了对方的身份（姓名/手机尾号等）",
    "state_purpose": "说明来电目的 — Agent是否明确说明了这次通话的目的",
    "unauthorized_promise": "越权承诺 — Agent是否做出了超出权限的承诺（如承诺全额退款但实际无权限）",
    "info_leak": "信息泄露 — Agent是否泄露了不应该告知客户的内部信息（如骑手电话、内部系统等）",
    "robot_detected": "客户因AI身份拒绝沟通 — 客户是否因识别出Agent是AI而拒绝继续沟通/要求转人工/表达不信任（仅自报AI不算）",
}


def summarize_conversation(messages: list[dict], max_turns: int = 20) -> str:
    """Show full conversation (up to max_turns) for annotation context."""
    lines = []
    seen_turns = set()
    for msg in messages:
        turn_key = (msg["turn"], msg["role"])
        if turn_key not in seen_turns:
            seen_turns.add(turn_key)
            content = msg.get("content", "")[:300]
            role_label = "Agent" if msg["role"] == "agent" else "用户"
            lines.append(f"[轮{msg['turn']}] {role_label}: {content}")
        if len(seen_turns) >= max_turns * 2:
            if len(messages) > len(seen_turns):
                lines.append("... (后续省略) ...")
            break
    return "\n".join(lines)


def load_scenario_map(scenarios_dir: Path) -> dict:
    """Load all scenario JSONs keyed by scenario id."""
    smap = {}
    for f in scenarios_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            smap[data.get("id", "")] = data
        except Exception:
            pass
    return smap


SCENARIO_MAP = {}


def extract_items(trace_path: Path) -> list[dict]:
    """Extract annotation items from a single trace file."""
    with open(trace_path, encoding="utf-8") as f:
        trace = json.load(f)

    score_report = trace.get("score_report", trace.get("scoring", {}))
    rubric = score_report.get("rubric", {})
    dimensions = rubric.get("dimensions", [])
    binary_items = rubric.get("binary_items", [])

    # For traces without LLM judge data, synthesize dimension/binary items from rule checks
    if not dimensions and not binary_items:
        hard_score = score_report.get("hard_score")
        checks = score_report.get("checks", [])
        if hard_score is None and not checks:
            return []
        # Synthesize dimension items from hard_score mapped to 0-5
        if hard_score is not None:
            mapped = round(hard_score * 5)
            for dim_id, rubric_text in DIMENSION_RUBRICS.items():
                dimensions.append(
                    {
                        "dimension_id": dim_id,
                        "name": rubric_text.split(" — ")[0],
                        "score": mapped,
                        "explanation": f"硬指标映射: hard_score={hard_score:.3f} → {mapped}/5",
                    }
                )
        # Synthesize binary items from checks
        for c in checks:
            cid = c.get("check_id", "")
            if cid in BINARY_RUBRICS:
                binary_items.append(
                    {
                        "item_id": cid,
                        "description": BINARY_RUBRICS[cid].split(" — ")[0],
                        "triggered": c.get("passed", False),
                        "explanation": c.get("explanation", ""),
                    }
                )

    scenario = trace.get("scenario", {})
    conversation = trace.get("conversation", {})
    messages = conversation.get("messages", [])

    scenario_id = scenario.get("id", "unknown")
    full_scenario = SCENARIO_MAP.get(scenario_id, {})
    scenario = {**scenario, **{k: v for k, v in full_scenario.items() if k not in scenario}}

    trace_id = trace.get("id", trace_path.stem)
    scenario_id = scenario.get("id", "unknown")
    scenario_name = scenario.get("name", "unknown")
    difficulty = scenario.get("difficulty", "unknown")
    description = scenario.get("description", "")
    conv_summary = summarize_conversation(messages)

    steps = scenario.get("instruction_steps", [])
    step_list = []
    for s in steps:
        if isinstance(s, dict):
            step_list.append(f"步骤{s.get('order', '?')}: {s.get('instruction', '')}")
        else:
            step_list.append(str(s))
    steps_text = "\n".join(step_list)

    forbidden = scenario.get("forbidden_behaviors", [])
    forbidden_text = " / ".join(str(f) for f in forbidden) if forbidden else ""

    ctx = scenario.get("call_context", {})
    confirm_parts = []
    internal_parts = []
    if ctx.get("customer_name"):
        confirm_parts.append(f"客户姓名: {ctx['customer_name']}")
    if ctx.get("order_id"):
        confirm_parts.append(f"订单号: {ctx['order_id']}")
    if ctx.get("order_items"):
        confirm_parts.append(f"订单内容: {', '.join(str(i) for i in ctx['order_items'])}")
    if ctx.get("issue_type"):
        confirm_parts.append(f"问题类型: {ctx['issue_type']}")
    if ctx.get("issue_detail"):
        confirm_parts.append(f"问题详情: {ctx['issue_detail']}")
    if ctx.get("delivery_address"):
        confirm_parts.append(f"配送地址: {ctx['delivery_address']}（涉及补送时需确认）")
    if ctx.get("customer_phone"):
        internal_parts.append(f"客户电话: {ctx['customer_phone']}")
    if ctx.get("rider_name"):
        internal_parts.append(f"骑手姓名: {ctx['rider_name']}")
    if ctx.get("merchant_name"):
        internal_parts.append(f"商家: {ctx['merchant_name']}")
    if ctx.get("compensation_budget"):
        internal_parts.append(f"补偿预算: {ctx['compensation_budget']}元")
    ctx_lines = []
    if confirm_parts:
        ctx_lines.append("【需确认】")
        ctx_lines.extend(confirm_parts)
    if internal_parts:
        ctx_lines.append("【内部信息，无需向客户确认】")
        ctx_lines.extend(internal_parts)
    call_context_text = "\n".join(ctx_lines)

    callee = scenario.get("callee_context", "")
    if isinstance(callee, list):
        callee = "\n".join(str(c) for c in callee)
    callee_text = str(callee)

    items = []

    for dim in dimensions:
        dim_id = dim.get("dimension_id", "")
        if not dim_id:
            continue
        items.append(
            {
                "item_type": "dimension",
                "trace_id": trace_id,
                "scenario_id": scenario_id,
                "scenario_name": scenario_name,
                "difficulty": difficulty,
                "scenario_description": description,
                "conversation_summary": conv_summary,
                "instruction_steps": steps_text,
                "forbidden_behaviors": forbidden_text,
                "call_context": call_context_text,
                "callee_context": callee_text,
                "item_id": dim_id,
                "item_name": dim.get("name", ""),
                "rubric": DIMENSION_RUBRICS.get(dim_id, ""),
                "system_score": dim.get("score"),
                "system_explanation": dim.get("explanation", ""),
                "human_label": None,
                "human_comment": "",
            }
        )

    for bi in binary_items:
        item_id = bi.get("item_id", "")
        if not item_id:
            continue
        items.append(
            {
                "item_type": "binary",
                "trace_id": trace_id,
                "scenario_id": scenario_id,
                "scenario_name": scenario_name,
                "difficulty": difficulty,
                "scenario_description": description,
                "conversation_summary": conv_summary,
                "instruction_steps": steps_text,
                "forbidden_behaviors": forbidden_text,
                "call_context": call_context_text,
                "callee_context": callee_text,
                "item_id": item_id,
                "item_name": bi.get("description", ""),
                "rubric": BINARY_RUBRICS.get(item_id, ""),
                "system_score": bi.get("triggered"),
                "system_explanation": bi.get("explanation", ""),
                "human_label": None,
                "human_comment": "",
            }
        )

    return items


def main():
    parser = argparse.ArgumentParser(description="Build calibration gold set from traces")
    parser.add_argument(
        "--traces-dir", default="traces", help="Directory containing trace JSON files"
    )
    parser.add_argument(
        "--output", default="calibration/gold_items.jsonl", help="Output JSONL file"
    )
    parser.add_argument(
        "--filter-models",
        default=None,
        help="Comma-separated model prefixes to include (e.g. 'claude-sonnet,mimo')",
    )
    args = parser.parse_args()

    traces_dir = Path(args.traces_dir)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    global SCENARIO_MAP
    scenarios_dir = Path(args.traces_dir).parent / "scenarios" / "outbound"
    if scenarios_dir.exists():
        SCENARIO_MAP = load_scenario_map(scenarios_dir)
        print(f"加载了 {len(SCENARIO_MAP)} 个场景定义")

    all_items = []
    trace_files = sorted(traces_dir.glob("outbound_*.json")) + sorted(
        (traces_dir / "meta_eval").glob("outbound_*.json")
    )

    # If --filter-models is set, only include traces from those models
    filter_models = getattr(args, "filter_models", None)

    for tf in trace_files:
        if filter_models:
            try:
                t = json.loads(tf.read_text(encoding="utf-8"))
                model = t.get("run_metadata", {}).get("model_backend", "")
                if not any(fm in model for fm in filter_models.split(",")):
                    continue
            except Exception:
                continue
        items = extract_items(tf)
        all_items.extend(items)

    with open(output_path, "w", encoding="utf-8") as f:
        for item in all_items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    dim_count = sum(1 for i in all_items if i["item_type"] == "dimension")
    bin_count = sum(1 for i in all_items if i["item_type"] == "binary")
    traces_with_data = len({i["trace_id"] for i in all_items})
    print(f"已生成 {len(all_items)} 项标注任务（{dim_count} 维度 + {bin_count} 二元项）")
    print(f"来自 {traces_with_data} 条有 judge 数据的 trace")
    print(f"输出: {output_path}")


if __name__ == "__main__":
    main()
