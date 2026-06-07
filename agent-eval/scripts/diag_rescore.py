#!/usr/bin/env python3
"""临时诊断：用当前代码重新验证一条 trace 的步骤完成情况。
证明 step_compliance 对话步骤是否被系统性漏判（中文未分词导致关键词谓词失效）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from models import Conversation  # noqa: E402
from models_outbound import OutboundScenario  # noqa: E402
from policy_graph import ToolPredicate, UtterancePredicate, compile_policy_graph  # noqa: E402
from trace_verifier import verify_trace  # noqa: E402

trace_path = ROOT / "traces" / sys.argv[1]
scenario_path = ROOT / "scenarios" / "outbound" / sys.argv[2]

trace = json.loads(trace_path.read_text(encoding="utf-8"))
scen_data = json.loads(scenario_path.read_text(encoding="utf-8"))

scenario = OutboundScenario(**scen_data)
conv = Conversation(**trace["conversation"])

graph = compile_policy_graph(scenario)

print("=" * 70)
print(f"模型: {trace.get('run_metadata', {}).get('model_backend')}")
print(f"场景: {scenario.id}  存档分: {trace.get('score_report', {}).get('overall_score')}")
print("=" * 70)

print("\n--- 每步生成的匹配谓词（看对话步骤的关键词是不是整句没分词）---")
for sid in graph.topological_order():
    node = graph.get_node(sid)
    preds = []
    for p in node.predicates:
        if isinstance(p, ToolPredicate):
            preds.append(f"TOOL:{p.tool_name}")
        elif isinstance(p, UtterancePredicate):
            preds.append(f"KW:{list(p.keywords)}")
        else:
            preds.append(type(p).__name__)
    print(f"  [{node.order}] {sid:18s} {' | '.join(preds) if preds else '(无谓词)'}")

v = verify_trace(scenario, conv, ledger=None, graph=graph)

print("\n--- 验证结果 ---")
print(f"expected_path: {v.expected_path}")
print(f"observed_path: {v.observed_path}")
print(f"step_compliance_score : {v.step_compliance_score}")
print(f"branch_accuracy_score : {v.branch_accuracy_score}")
print(f"temporal_order_score  : {v.temporal_order_score}")
print(f"alignment_score       : {v.alignment_score}")

print("\n--- step_compliance 原子逐项 ---")
for a in v.satisfied_atoms + v.unsatisfied_atoms + v.not_applicable_atoms:
    if a.dimension == "step_compliance":
        print(f"  {a.status.value:12s} {a.step_id:18s} {a.reason}")
