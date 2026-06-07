"""Export traces for human blind review.

Reads meta_eval trace files and produces:
  1. A randomized, model-blinded transcript set (Markdown per transcript)
  2. A rubric scoring sheet (CSV) for 2 human raters
  3. After human scoring: import CSV and compute ICC / kappa / Spearman

Usage:
    python meta_eval_blind_review.py export           # generate review materials
    python meta_eval_blind_review.py import scores.csv # import human scores
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

TRACE_DIR = Path(__file__).parent / "traces" / "meta_eval"
REVIEW_DIR = Path(__file__).parent / "traces" / "meta_eval" / "blind_review"
HUMAN_SCORES_PATH = TRACE_DIR / "human_scores.json"

RUBRIC_DIMENSIONS = [
    ("overall", "通话整体质量", "1-5"),
    ("instruction_following", "指令遵循", "1-5"),
    ("tool_usage", "工具使用", "1-5"),
    ("context_retention", "上下文保持", "1-5"),
    ("tone", "语气/沟通质量", "1-5"),
    ("efficiency", "轮次效率", "1-5"),
    ("critical_violation", "是否有严重违规", "yes/no"),
    (
        "violation_type",
        "违规类型（可多选）",
        "forbidden/tool_fabrication/privacy/unauthorized/none",
    ),
]


def _load_traces() -> list[dict]:
    traces = []
    for path in sorted(TRACE_DIR.glob("outbound_*.json")):
        with open(path, encoding="utf-8") as f:
            trace = json.load(f)
        traces.append(trace)
    return traces


def _blind_id(trace: dict, idx: int) -> str:
    return f"CALL-{idx:03d}"


def _extract_transcript(trace: dict) -> str:
    conv = trace.get("conversation", {})
    messages = conv.get("messages", [])
    lines = []
    for msg in messages:
        role_map = {"agent": "Agent", "user": "客户", "system": "[系统]"}
        role = role_map.get(msg.get("role", ""), msg.get("role", "?"))
        turn = msg.get("turn", "?")
        text = msg.get("content", "")
        lines.append(f"**[第{turn}轮 {role}]** {text}")

        for tc in msg.get("tool_calls", []):
            tool_name = tc.get("tool_name", "?")
            if tc.get("error"):
                lines.append(f"  → 工具 {tool_name}: ✗ {tc['error']}")
            elif tc.get("result"):
                result_str = json.dumps(tc["result"], ensure_ascii=False)[:150]
                lines.append(f"  → 工具 {tool_name}: ✓ {result_str}")
    return "\n\n".join(lines)


def export_review_materials():
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    traces = _load_traces()
    if not traces:
        print("没有找到 trace 文件。先运行 meta_eval_runner.py")
        return

    indices = list(range(len(traces)))
    random.seed(42)
    random.shuffle(indices)

    mapping = {}
    csv_rows = []

    for new_idx, orig_idx in enumerate(indices):
        trace = traces[orig_idx]
        blind_id = _blind_id(trace, new_idx)
        trace_id = trace.get("id", "?")[:8]
        scenario_name = trace.get("scenario", {}).get("name", "?")
        model = trace.get("run_metadata", {}).get("model_backend", "?")

        mapping[blind_id] = {
            "trace_id": trace_id,
            "scenario": scenario_name,
            "model": model,
            "original_index": orig_idx,
        }

        transcript = _extract_transcript(trace)
        md_content = f"# {blind_id}\n\n**场景描述**: {trace.get('scenario', {}).get('description', '?')}\n\n---\n\n{transcript}\n"

        md_path = REVIEW_DIR / f"{blind_id}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        csv_rows.append(
            {
                "blind_id": blind_id,
                "rater": "",
                **{dim[0]: "" for dim in RUBRIC_DIMENSIONS},
                "notes": "",
            }
        )

    mapping_path = REVIEW_DIR / "_mapping.json"
    with open(mapping_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)

    csv_path = REVIEW_DIR / "scoring_sheet.csv"
    fieldnames = ["blind_id", "rater"] + [d[0] for d in RUBRIC_DIMENSIONS] + ["notes"]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in csv_rows:
            writer.writerow(row)
        for row in csv_rows:
            writer.writerow(row)

    rubric_path = REVIEW_DIR / "RUBRIC.md"
    with open(rubric_path, "w", encoding="utf-8") as f:
        f.write("# 人类盲审评分标准\n\n")
        f.write("> 请独立评分，不要与其他评分人交流。不要查看模型名称。\n\n")
        f.write("## 评分维度\n\n")
        f.write("| 维度 | 说明 | 量表 |\n|---|---|---|\n")
        for dim_id, desc, scale in RUBRIC_DIMENSIONS:
            f.write(f"| {dim_id} | {desc} | {scale} |\n")
        f.write("\n## 分数含义（1-5）\n\n")
        f.write("| 分数 | 含义 |\n|---|---|\n")
        f.write("| 5 | 优秀——完全按指令执行，无失误 |\n")
        f.write("| 4 | 良好——基本完成，有小瑕疵 |\n")
        f.write("| 3 | 一般——完成了核心任务但有明显问题 |\n")
        f.write("| 2 | 较差——多处失误，任务完成度低 |\n")
        f.write("| 1 | 极差——几乎没有完成任务或严重违规 |\n")
        f.write("\n## 严重违规类型\n\n")
        f.write("- **forbidden**: 使用禁止用语\n")
        f.write("- **tool_fabrication**: 声称调用了工具但实际没有\n")
        f.write("- **privacy**: 泄露客户/骑手隐私\n")
        f.write("- **unauthorized**: 越权承诺（超预算补偿等）\n")
        f.write("- **none**: 无严重违规\n")

    print("✓ 导出完成:")
    print(f"  对话文件: {REVIEW_DIR}/CALL-*.md ({len(traces)} 份)")
    print(f"  评分表: {csv_path}")
    print(f"  评分标准: {rubric_path}")
    print(f"  映射文件: {mapping_path} (评分完成后用于解盲)")


def import_scores(csv_path: str):
    """Import human scores from CSV and produce human_scores.json."""
    mapping_path = REVIEW_DIR / "_mapping.json"
    with open(mapping_path, encoding="utf-8") as f:
        mapping = json.load(f)

    scores_by_trace: dict[str, list[dict]] = {}

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            blind_id = row.get("blind_id", "").strip()
            if not blind_id or blind_id not in mapping:
                continue
            rater = row.get("rater", "").strip()
            if not rater:
                continue

            trace_id = mapping[blind_id]["trace_id"]
            score_entry = {
                "rater": rater,
                "blind_id": blind_id,
            }
            for dim_id, _, _ in RUBRIC_DIMENSIONS:
                val = row.get(dim_id, "").strip()
                if val.isdigit():
                    score_entry[dim_id] = int(val)
                else:
                    score_entry[dim_id] = val

            scores_by_trace.setdefault(trace_id, []).append(score_entry)

    aggregated = {}
    for trace_id, entries in scores_by_trace.items():
        numeric_overalls = [
            e["overall"] for e in entries if isinstance(e.get("overall"), (int, float))
        ]
        aggregated[trace_id] = {
            "overall": sum(numeric_overalls) / len(numeric_overalls) if numeric_overalls else None,
            "raters": entries,
        }

    with open(HUMAN_SCORES_PATH, "w", encoding="utf-8") as f:
        json.dump(aggregated, f, ensure_ascii=False, indent=2)
    print(f"✓ 人类评分已导入: {HUMAN_SCORES_PATH} ({len(aggregated)} 通对话)")


def main():
    parser = argparse.ArgumentParser(description="人类盲审材料导出/导入")
    parser.add_argument("action", choices=["export", "import"])
    parser.add_argument("csv_path", nargs="?", help="import 时的 CSV 路径")
    args = parser.parse_args()

    if args.action == "export":
        export_review_materials()
    elif args.action == "import":
        if not args.csv_path:
            print("用法: python meta_eval_blind_review.py import <scores.csv>")
            return
        import_scores(args.csv_path)


if __name__ == "__main__":
    main()
