#!/usr/bin/env python3
"""红队测试：验证 step 检测护栏（覆盖率门 + 否定守卫）。stdlib + 确定性。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import json  # noqa: E402

from models import Conversation, Message, Role  # noqa: E402
from models_outbound import OutboundScenario  # noqa: E402
from policy_graph import compile_policy_graph  # noqa: E402
from trace_verifier import verify_trace  # noqa: E402

scen = OutboundScenario(
    **json.loads(
        (ROOT / "scenarios" / "outbound" / "after_sales_complaint.json").read_text("utf-8")
    )
)
graph = compile_policy_graph(scen)


def observed(utterances: list[str]) -> set[str]:
    msgs = [Message(turn=i + 1, role=Role.AGENT, content=u) for i, u in enumerate(utterances)]
    conv = Conversation(scenario_id=scen.id, messages=msgs)
    return set(verify_trace(scen, conv, ledger=None, graph=graph).observed_path)


def check(name: str, utterances: list[str], step: str, expect_observed: bool):
    obs = observed(utterances)
    got = step in obs
    ok = "PASS" if got == expect_observed else "FAIL"
    print(
        f"[{ok}] {name}: 步骤 {step} 期望{'完成' if expect_observed else '未完成'}, 实际{'完成' if got else '未完成'}  观测={sorted(obs)}"
    )


print("acknowledge_issue 需 apologize+restate_issue+confirm_issue（3 动作，覆盖率门要求≥2）")
# 1) 只道歉，不复述不确认 → 1/3 < 50% → 应未完成
check("只说抱歉", ["非常抱歉给您添麻烦了。"], "acknowledge_issue", expect_observed=False)
# 2) 道歉+复述+确认 → 3/3 → 应完成
check(
    "道歉+复述+确认",
    ["非常抱歉，您反映少送了一份菜，是这样吗？"],
    "acknowledge_issue",
    expect_observed=True,
)

print("\noffer_solution 需 offer_refund+offer_redelivery（否定守卫按动作）")
# 3) 拒绝退款但给补送 → refund 被否定, redelivery 命中 → 1/2=50% → 应完成（确实给了方案）
check(
    "无法退款但补送",
    ["暂时无法退款，不过可以给您补送一份。"],
    "offer_solution",
    expect_observed=True,
)
# 4) 两个都拒 → 0/2 → 应未完成
check(
    "既不退也不补",
    ["实在抱歉，这单暂时无法退款，也无法补送。"],
    "offer_solution",
    expect_observed=False,
)
