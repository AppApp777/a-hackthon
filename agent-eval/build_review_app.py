"""Build the blind review HTML app with auto-assessment data.

Reads trace JSON to extract structured messages, auto-checks with turn associations,
and scenario references. Human reviewer confirms/rejects each inline check.

Usage:
    python build_review_app.py
"""

import json
import re
from pathlib import Path

TRACE_DIR = Path(__file__).parent / "traces" / "meta_eval"
REVIEW_DIR = TRACE_DIR / "blind_review"
SCENARIOS_DIR = Path(__file__).parent / "scenarios" / "outbound"
TEMPLATE = Path(__file__).parent / "blind_review_app.html"
OUTPUT = Path(__file__).parent / "blind_review_app_ready.html"

TOOL_NAME_ZH = {
    "query_order": "查询订单",
    "query_customer": "查询客户",
    "update_delivery_status": "更新配送状态",
    "reschedule_delivery": "改期配送",
    "create_compensation": "创建补偿",
    "check_compensation_eligibility": "检查补偿资格",
    "log_call_result": "记录通话结果",
    "transfer_to_human": "转人工",
    "send_wechat_invite": "发企业微信邀请",
    "check_rider_contract": "查骑手合同",
    "check_platform_config": "查平台配置",
}


def _load_scenario_refs() -> dict[str, dict]:
    refs = {}
    for path in SCENARIOS_DIR.glob("*.json"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        steps = []
        for step in data.get("instruction_steps", []):
            steps.append(
                {
                    "id": step.get("step_id", ""),
                    "order": step.get("order", 0),
                    "instruction": step.get("instruction", ""),
                    "actions": step.get("required_actions", []),
                    "optional": step.get("is_optional", False),
                }
            )
        forbidden = []
        for fb in data.get("forbidden_behaviors", []):
            forbidden.append(
                {
                    "id": fb.get("id", ""),
                    "description": fb.get("description", ""),
                    "severity": fb.get("severity", "major"),
                    "keywords": fb.get("detection_keywords", []),
                }
            )
        refs[data.get("name", "")] = {
            "steps": steps,
            "must_call_tools": data.get("must_call_tools", []),
            "forbidden": forbidden,
            "expected_result": data.get("expected_call_result", ""),
            "expected_steps": data.get("expected_steps_completed", []),
            "opening": data.get("mandatory_opening", ""),
            "closing": data.get("mandatory_closing", ""),
            "optimal_turns": data.get("optimal_turns", 0),
            "budget": data.get("call_context", {}).get("compensation_budget", 0),
        }
    return refs


def _parse_turn_from_detail(detail: str) -> int | None:
    m = re.search(r"第(\d+)轮", detail)
    return int(m.group(1)) if m else None


def _extract_auto_checks(trace: dict, ref: dict | None) -> list[dict]:
    checks = []
    sr = trace.get("score_report", {})
    meta = trace.get("metadata", {})
    ob = meta.get("outbound_report", {})
    conv = trace.get("conversation", {})
    msgs = conv.get("messages", [])
    agent_turns = [m["turn"] for m in msgs if m.get("role") == "agent"]
    first_turn = agent_turns[0] if agent_turns else 1
    last_agent_turn = agent_turns[-1] if agent_turns else 1

    opening_ok = ob.get("opening_correct")
    if opening_ok is not None:
        checks.append(
            {
                "cat": "speech",
                "label": "开场白规范",
                "pass": opening_ok,
                "after_turn": first_turn,
                "detail": f"要求包含: {ref['opening']}" if ref and ref.get("opening") else "",
            }
        )

    closing_ok = ob.get("closing_correct")
    if closing_ok is not None:
        checks.append(
            {
                "cat": "speech",
                "label": "结束语规范",
                "pass": closing_ok,
                "after_turn": last_agent_turn,
                "detail": f"要求包含: {ref['closing']}" if ref and ref.get("closing") else "",
            }
        )

    failures = sr.get("failure_summary", [])

    if ref and ref.get("must_call_tools"):
        tool_turn_map = {}
        for m in msgs:
            for tc in m.get("tool_calls", []):
                name = tc.get("tool_name", "")
                if name not in tool_turn_map:
                    tool_turn_map[name] = m.get("turn", 0)
        for tool_name in ref["must_call_tools"]:
            missed = any(tool_name in f and "未调用" in f for f in failures)
            t = tool_turn_map.get(tool_name, last_agent_turn)
            zh = TOOL_NAME_ZH.get(tool_name, tool_name)
            checks.append(
                {
                    "cat": "tool",
                    "label": f"必须调用「{zh}」",
                    "pass": not missed,
                    "after_turn": t,
                    "detail": "未调用" if missed else f"已在第{t}轮调用",
                }
            )

    step_detail = ob.get("step_compliance_detail", {})
    step_compliance = ob.get("step_compliance_score")
    if ref and ref.get("expected_steps"):
        for si, step_id in enumerate(ref["expected_steps"]):
            step_info = next((s for s in ref.get("steps", []) if s["id"] == step_id), None)
            label = step_info["instruction"][:50] if step_info else step_id
            completed = step_detail.get(step_id, {}).get("completed")
            if completed is None:
                completed = step_compliance is not None and step_compliance > 0.5
            est_turn = min(first_turn + si, last_agent_turn)
            checks.append(
                {
                    "cat": "step",
                    "label": f"步骤「{step_id}」: {label}",
                    "pass": completed if isinstance(completed, bool) else None,
                    "after_turn": est_turn,
                    "detail": "",
                }
            )

    forbidden_failures = [f for f in failures if "forbidden" in f.lower() or "禁止" in f]
    if ref and ref.get("forbidden"):
        for fb in ref["forbidden"]:
            violated = any(
                fb["description"] in f or any(kw in f for kw in fb.get("keywords", []))
                for f in forbidden_failures
            )
            detail_str = ""
            turn = last_agent_turn
            if violated:
                match_f = next(
                    (
                        f
                        for f in forbidden_failures
                        if fb["description"] in f or any(kw in f for kw in fb.get("keywords", []))
                    ),
                    "",
                )
                detail_str = match_f
                pt = _parse_turn_from_detail(match_f)
                if pt:
                    turn = pt
            checks.append(
                {
                    "cat": "forbidden",
                    "label": f"禁止: {fb['description']}",
                    "pass": not violated,
                    "after_turn": turn,
                    "detail": detail_str if violated else "",
                }
            )

    result_ok = ob.get("call_result_correct")
    if result_ok is not None:
        expected = ref.get("expected_result", "") if ref else ""
        checks.append(
            {
                "cat": "outcome",
                "label": f"通话结果正确（期望: {expected}）",
                "pass": result_ok,
                "after_turn": last_agent_turn,
                "detail": "",
            }
        )

    if ref and ref.get("optimal_turns"):
        actual = len(agent_turns)
        optimal = ref["optimal_turns"]
        eff = max(0, 1 - abs(actual - optimal) / max(optimal, 1))
        checks.append(
            {
                "cat": "efficiency",
                "label": f"轮次效率（最优{optimal}轮，实际{actual}轮）",
                "pass": eff >= 0.6,
                "after_turn": last_agent_turn,
                "detail": f"效率 {eff:.0%}",
            }
        )

    return checks


def _extract_messages(trace: dict) -> list[dict]:
    conv = trace.get("conversation", {})
    messages = []
    for msg in conv.get("messages", []):
        m = {
            "turn": msg.get("turn", 0),
            "role": msg.get("role", ""),
            "content": msg.get("content", ""),
            "tool_calls": [],
            "emo": msg.get("metadata", {}).get("emotional_state", ""),
        }
        for tc in msg.get("tool_calls", []):
            name = tc.get("tool_name", "")
            result = tc.get("result")
            result_flat = {}
            if isinstance(result, dict):
                for k, v in result.items():
                    if isinstance(v, (list, dict)):
                        result_flat[k] = json.dumps(v, ensure_ascii=False)[:80]
                    else:
                        result_flat[k] = str(v)
            m["tool_calls"].append(
                {
                    "name": name,
                    "name_zh": TOOL_NAME_ZH.get(name, name),
                    "ok": not tc.get("error"),
                    "error": tc.get("error", ""),
                    "result_flat": result_flat,
                    "args": tc.get("arguments", {}),
                }
            )
        messages.append(m)
    return messages


def main():
    mapping_path = REVIEW_DIR / "_mapping.json"
    mapping = {}
    if mapping_path.exists():
        with open(mapping_path, encoding="utf-8") as f:
            mapping = json.load(f)

    scenario_refs = _load_scenario_refs()

    trace_lookup = {}
    for path in TRACE_DIR.glob("outbound_*.json"):
        with open(path, encoding="utf-8") as f:
            trace = json.load(f)
        trace_lookup[trace.get("id", "")[:8]] = trace

    calls = []
    for md_path in sorted(REVIEW_DIR.glob("CALL-*.md")):
        call_id = md_path.stem
        m = mapping.get(call_id, {})
        scenario_name = m.get("scenario", "")
        trace = trace_lookup.get(m.get("trace_id", ""))
        ref = scenario_refs.get(scenario_name)

        with open(md_path, encoding="utf-8") as f:
            md = f.read()
        desc = ""
        for line in md.split("\n"):
            if line.startswith("**场景描述**"):
                desc = line.replace("**场景描述**: ", "").replace("**场景描述**:", "").strip()
                break

        calls.append(
            {
                "id": call_id,
                "desc": desc,
                "msgs": _extract_messages(trace) if trace else [],
                "checks": _extract_auto_checks(trace, ref) if trace else [],
                "ref": ref,
                "md": md,
            }
        )

    if not calls:
        print("没有找到 CALL-*.md 文件")
        return

    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    html = html.replace("__CALLS_DATA_PLACEHOLDER__", json.dumps(calls, ensure_ascii=False))
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(html)

    checks_count = sum(len(c["checks"]) for c in calls)
    print(f"✓ 已生成: {OUTPUT}")
    print(f"  {len(calls)} 份对话，{checks_count} 条检查项（已关联到对应轮次）")


if __name__ == "__main__":
    main()
