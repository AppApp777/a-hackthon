#!/usr/bin/env python3
"""Post-hoc veto simulation: test new internal-info-leak patterns against 22 traces."""

import json
import re
from pathlib import Path

# Tightened patterns — mirror scorer_outbound.py _INTERNAL_INFO_PATTERNS after review.
PATTERNS = [
    ("step_checklist", re.compile(r"[✅☑✓][^\n]{0,30}[✅☑✓]", re.I)),
    (
        "workflow_status",
        re.compile(
            r"(本次通话|本次回访|通话已).{0,10}(圆满|顺利完成|已记录|结果已|处理情况|处理如下|执行完毕)",
            re.I,
        ),
    ),
    ("script_execution", re.compile(r"(按[^。\n]{0,10}脚本|指令脚本).{0,6}(执行|完成)", re.I)),
    ("log_id_leak", re.compile(r"(\blog_\w*|记录编号)\s*[:：]\s*\w+", re.I)),
    ("numbered_step_output", re.compile(r"步骤\s*\d+\s*[:：]", re.I)),
    ("result_summary", re.compile(r"(通话结果汇总|通话记录摘要|通话小结|通话摘要)", re.I)),
    ("system_name", re.compile(r"(CRM|OA|ERP|工单系统|调度系统|内部系统|后台系统|管理平台)", re.I)),
    (
        "internal_process",
        re.compile(r"(内部流程|审批链|工单流转|系统架构|内部规定|操作手册)", re.I),
    ),
]

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "calibration" / "blind_pilot"

mapping = json.loads((DATA_DIR / "_system_scores_DO_NOT_OPEN.json").read_text(encoding="utf-8"))
human_veto_set = {"02", "04", "07", "09", "12", "14", "16", "17", "19", "20", "21", "22"}

results = []
for idx in sorted(mapping.keys()):
    entry = mapping[idx]
    trace = json.loads(
        (DATA_DIR / "traces_v2" / f"{entry['trace_id']}.json").read_text(encoding="utf-8")
    )
    msgs = trace.get("conversation", {}).get("messages", [])
    agent_text = " ".join(m.get("content", "") for m in msgs if m.get("role") == "agent")

    hits = []
    for pid, pat in PATTERNS:
        if pat.search(agent_text):
            hits.append(pid)

    sys_veto = len(hits) > 0
    h_veto = idx in human_veto_set
    match = (
        "TP"
        if sys_veto and h_veto
        else "FP"
        if sys_veto and not h_veto
        else "FN"
        if not sys_veto and h_veto
        else "TN"
    )
    results.append((idx, sys_veto, h_veto, match, hits))
    print(f"{idx}: sys_veto={str(sys_veto):5} human_veto={str(h_veto):5} {match:2} hits={hits}")

tp = sum(1 for r in results if r[3] == "TP")
fp = sum(1 for r in results if r[3] == "FP")
fn = sum(1 for r in results if r[3] == "FN")
tn = sum(1 for r in results if r[3] == "TN")
p = tp / (tp + fp) if tp + fp else 0
r_ = tp / (tp + fn) if tp + fn else 0
f1 = 2 * p * r_ / (p + r_) if p + r_ else 0
print(f"\nTP={tp} FP={fp} FN={fn} TN={tn}")
print(f"Precision={p:.3f} Recall={r_:.3f} F1={f1:.3f}")
