"""Generate blind labeling materials for human calibration pilot.

Extracts conversations from selected traces WITHOUT showing system scores.
Produces individual text files + empty CSV for human annotator to fill.
"""

import csv
import json
import os
from pathlib import Path

TRACES_DIR = Path(__file__).parent.parent / "traces"
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "calibration" / "blind_pilot"

SELECTED_TRACES = [
    "fec0f023-d30b-46c0-b9a9-4ab5606cb5bf",
    "96bb49e1-3bae-40ff-bc55-f5fab0b2827c",
    "outbound_528f852f",
    "outbound_c50d015b",
    "outbound_c5a77fde",
    "outbound_b721247d",
    "outbound_22c0a09e",
    "outbound_e6dcbff3",
    "outbound_f1f52154",
    "outbound_89c7257c",
    "outbound_706fbaea",
    "outbound_64322b6d",
    "outbound_5af36c0d",
    "outbound_a6e6d2ee",
    "outbound_130887c4",
    "outbound_5df2d028",
    "0ee5fef1-2a40-4d1d-a95c-612840d5ba5f",
    "outbound_c15731d6",
    "outbound_65365325",
    "outbound_1041b5a5",
    "outbound_349b29da",
    "outbound_85ba029e",
    "outbound_152feba3",
    "outbound_2c0f63fd",
]


def format_conversation_blind(trace: dict, trace_idx: int) -> str:
    """Format a trace for blind human review — NO scores, NO system metadata."""
    lines = []
    lines.append(f"{'=' * 60}")
    lines.append(f"  对话 #{trace_idx:02d}")
    lines.append(f"{'=' * 60}")
    lines.append("")

    scenario_id = trace.get("scenario_id", "unknown")
    scenario_desc = ""
    scenario_data = trace.get("scenario", {})
    if isinstance(scenario_data, dict):
        scenario_desc = scenario_data.get("description", "")
        call_purpose = scenario_data.get("call_purpose", "")
    else:
        scenario_desc = str(scenario_data)[:200]
        call_purpose = ""

    lines.append(f"【场景】{scenario_id}")
    if scenario_desc:
        lines.append(f"【场景描述】{scenario_desc[:300]}")
    if call_purpose:
        lines.append(f"【通话目的】{call_purpose[:200]}")
    lines.append("")
    lines.append("-" * 40)
    lines.append("  对话记录")
    lines.append("-" * 40)
    lines.append("")

    messages = trace.get("conversation", {}).get("messages", [])
    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        turn = msg.get("turn", i + 1)

        if role == "assistant":
            role_label = "🤖 Agent"
        elif role == "user":
            role_label = "👤 用户"
        elif role == "system":
            role_label = "⚙️ 系统"
        else:
            role_label = f"[{role}]"

        lines.append(f"[轮次 {turn}] {role_label}:")
        if content:
            lines.append(f"  {content[:1000]}")

        tool_calls = msg.get("tool_calls", [])
        for tc in tool_calls:
            tool_name = tc.get("tool_name", "unknown_tool")
            args = tc.get("arguments", {})
            result = tc.get("result", "")
            error = tc.get("error", "")
            lines.append(f"  📞 工具调用: {tool_name}")
            if args:
                args_str = ", ".join(f"{k}={v}" for k, v in list(args.items())[:5])
                lines.append(f"     参数: {args_str[:200]}")
            if result:
                lines.append(f"     结果: {str(result)[:300]}")
            if error:
                lines.append(f"     错误: {error[:200]}")
        lines.append("")

    lines.append(f"{'=' * 60}")
    lines.append(f"  对话结束 (共 {len(messages)} 条消息)")
    lines.append(f"{'=' * 60}")
    return "\n".join(lines)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR / "conversations", exist_ok=True)

    system_scores = {}
    all_convos = []

    for idx, trace_id in enumerate(SELECTED_TRACES, 1):
        trace_file = TRACES_DIR / f"{trace_id}.json"
        if not trace_file.exists():
            print(f"WARNING: {trace_file} not found, skipping")
            continue

        with open(trace_file, encoding="utf-8") as f:
            trace = json.load(f)

        score_report = trace.get("score_report", {})
        system_scores[f"{idx:02d}"] = {
            "trace_id": trace_id,
            "scenario_id": trace.get("scenario_id", "unknown"),
            "model": trace.get("run_metadata", {}).get("model_backend", "unknown"),
            "system_score_100": score_report.get("overall_score_100"),
            "system_veto_cap": score_report.get("veto_cap"),
            "system_gate_type": score_report.get("gate_type", "none"),
        }

        blind_text = format_conversation_blind(trace, idx)
        conv_file = OUTPUT_DIR / "conversations" / f"对话_{idx:02d}.txt"
        with open(conv_file, "w", encoding="utf-8") as f:
            f.write(blind_text)
        all_convos.append(blind_text)

    with open(OUTPUT_DIR / "所有对话合集.txt", "w", encoding="utf-8") as f:
        f.write("=" * 60 + "\n")
        f.write("  人工盲标材料 — 请在不看系统分数的情况下评分\n")
        f.write("  共 24 条对话，评判标准见 评判标准卡.md\n")
        f.write("=" * 60 + "\n\n")
        f.write("\n\n".join(all_convos))

    with open(OUTPUT_DIR / "_system_scores_DO_NOT_OPEN.json", "w", encoding="utf-8") as f:
        json.dump(system_scores, f, ensure_ascii=False, indent=2)

    csv_path = OUTPUT_DIR / "人工标注表.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "对话编号",
                "总分(0-100)",
                "总评级(A/B/C/D/F)",
                "是否应触发veto(是/否)",
                "veto原因(如有)",
                "任务完成度(0-2)",
                "工具/DB操作正确性(0-2/NA)",
                "时序正确性(0-2)",
                "约束合规(0-2)",
                "知识准确性(0-2)",
                "安全/隐私(0-2)",
                "软质量(1-5)",
                "关键失败轮次(如有)",
                "备注",
            ]
        )
        for idx in range(1, len(SELECTED_TRACES) + 1):
            writer.writerow([f"{idx:02d}"] + [""] * 13)

    print(f"\n盲标材料已生成到: {OUTPUT_DIR}")
    print("  - conversations/ 目录: 24 个单独对话文件")
    print("  - 所有对话合集.txt: 全部对话合并版")
    print("  - 人工标注表.csv: 空白标注表（你要填的）")
    print("  - _system_scores_DO_NOT_OPEN.json: 系统分数（标注完再打开！）")
    print("\n请先读 评判标准卡，然后逐条评分，填入 CSV。")


if __name__ == "__main__":
    main()
