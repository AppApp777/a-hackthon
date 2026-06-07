#!/usr/bin/env python3
"""
可复现证据包 — 一键验证 README 中所有声明的数字。

用法:
    cd A-hackthon
    python reproduce_claims.py

不需要 API key，不需要联网。所有计算基于冻结的 trace 和报告文件。
"""

import json
import subprocess
import sys
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).parent
AGENT_EVAL = ROOT / "agent-eval"
CALIBRATION = AGENT_EVAL / "calibration"

PASS = "✅ PASS"
FAIL = "❌ FAIL"
results = []


def check(name: str, condition: bool, detail: str):
    status = PASS if condition else FAIL
    results.append((name, status, detail))
    print(f"  {status}  {name}: {detail}")


def verify_ablation():
    """验证消融实验声明。"""
    print("\n═══ 消融实验（111 条 trace）═══")
    report_path = CALIBRATION / "ablation_report.json"
    if not report_path.exists():
        check("消融报告存在", False, f"{report_path} 不存在")
        return

    report = json.loads(report_path.read_text(encoding="utf-8"))

    check("trace 数量 = 111", report["trace_count"] == 111, f"实际 {report['trace_count']}")

    summary = {s["config"]: s for s in report["summary"]}

    full_mean = summary["full_system"]["mean"]
    check("完整系统均分 ≈ 37.2%", abs(full_mean - 37.2) < 0.1, f"实际 {full_mean}%")

    llm_mean = summary["soft_judge_only"]["mean"]
    check("仅 LLM 评委均分 ≈ 88.8%", abs(llm_mean - 88.8) < 0.1, f"实际 {llm_mean}%")

    gap = llm_mean - full_mean
    check("消融差距 ≈ 51.6pp", abs(gap - 51.6) < 0.2, f"实际 {gap:.1f}pp")

    no_step = summary["no_step_compliance"]["delta_vs_full"]
    check("去掉步骤合规 delta ≈ +8.1pp", abs(no_step - 8.1) < 0.1, f"实际 +{no_step}pp")

    no_branch = summary["no_branch_accuracy"]["delta_vs_full"]
    check("去掉分支准确 delta ≈ +1.6pp", abs(no_branch - 1.6) < 0.1, f"实际 +{no_branch}pp")

    no_veto = summary["no_safety_veto"]["delta_vs_full"]
    check("去掉安全否决 delta ≈ +1.0pp", abs(no_veto - 1.0) < 0.1, f"实际 +{no_veto}pp")


def verify_paired():
    """验证配对实验声明。"""
    print("\n═══ 配对实验（10 场景 × 3 次）═══")
    report_path = CALIBRATION / "paired_experiment_report.json"
    if not report_path.exists():
        check("配对报告存在", False, f"{report_path} 不存在")
        return

    report = json.loads(report_path.read_text(encoding="utf-8"))

    check("场景数 = 10", report["scenario_count"] == 10, f"实际 {report['scenario_count']}")
    check("重复次数 = 3", report["repeats"] == 3, f"实际 {report['repeats']}")
    check("总跑数 = 30", report["total_runs"] == 30, f"实际 {report['total_runs']}")

    stdevs = sorted([s["stdev"] for s in report["summary"]])
    median_sd = stdevs[len(stdevs) // 2]
    within_12 = sum(1 for sd in stdevs if sd < 12.0)
    check(
        "中位数标准差 < 10%",
        median_sd < 10.0,
        f"中位数 {median_sd:.1f}%，{within_12}/10 个场景 < 12%",
    )

    easy_scenarios = ["delivery_confirm_basic", "simple_satisfaction_survey"]
    medium_scenarios = ["rider_feimaotui_notify"]
    hard_scenarios = [
        "course_livestream_upgrade",
        "after_sales_complaint",
        "refund_over_budget",
        "compliance_conflict",
        "multi_issue_combo",
    ]

    scenario_map = {s["scenario"]: s["mean"] for s in report["summary"]}

    easy_avg = mean([scenario_map[s] for s in easy_scenarios if s in scenario_map])
    medium_avg = mean([scenario_map[s] for s in medium_scenarios if s in scenario_map])
    hard_avg = mean([scenario_map[s] for s in hard_scenarios if s in scenario_map])

    check(
        "难度梯度: easy > medium > hard",
        easy_avg > medium_avg > hard_avg,
        f"easy={easy_avg:.1f}% > medium={medium_avg:.1f}% > hard={hard_avg:.1f}%",
    )


def verify_tests():
    """验证测试套件声明。"""
    print("\n═══ 测试套件 ═══")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "agent-eval/tests/", "--co"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env={**__import__("os").environ, "PYTHONPATH": str(AGENT_EVAL)},
    )
    output = result.stdout + result.stderr
    import re

    match = re.search(r"(\d+) tests? collected", output)
    if match:
        count = int(match.group(1))
        check("测试数量 ≥ 1100", count >= 1100, f"实际 {count}")
    else:
        check("测试收集成功", False, "无法解析 pytest 输出")


def verify_scenarios():
    """验证场景数量声明。"""
    print("\n═══ 场景覆盖 ═══")
    scenario_dir = AGENT_EVAL / "scenarios" / "outbound"
    scenarios = list(scenario_dir.glob("*.json"))
    check("场景数 ≥ 34", len(scenarios) >= 34, f"实际 {len(scenarios)}")

    difficulties = {"easy": 0, "medium": 0, "hard": 0, "extreme": 0}
    for s in scenarios:
        try:
            data = json.loads(s.read_text(encoding="utf-8"))
            diff = data.get("difficulty", "unknown")
            if diff in difficulties:
                difficulties[diff] += 1
        except Exception:
            pass

    check(
        "四个难度等级都有场景",
        all(v > 0 for v in difficulties.values()),
        f"easy={difficulties['easy']} medium={difficulties['medium']} "
        f"hard={difficulties['hard']} extreme={difficulties['extreme']}",
    )


def verify_traces():
    """验证 trace 数量。"""
    print("\n═══ Trace 覆盖 ═══")
    traces_dir = AGENT_EVAL / "traces"
    if traces_dir.exists():
        trace_files = list(traces_dir.glob("*.json"))
        check("trace 数量 ≥ 100", len(trace_files) >= 100, f"实际 {len(trace_files)}")
    else:
        check("traces 目录存在", False, "traces/ 不存在")


def verify_scorer_size():
    """验证 scorer 拆分后的行数。"""
    print("\n═══ 代码规模 ═══")
    scorer = AGENT_EVAL / "scorer_outbound.py"
    if scorer.exists():
        lines = len(scorer.read_text(encoding="utf-8").splitlines())
        check("scorer_outbound.py ≤ 1800 行", lines <= 1800, f"实际 {lines} 行")
    else:
        check("scorer_outbound.py 存在", False, "文件不存在")


def main():
    print("=" * 60)
    print("  可复现证据包 — 验证 README 中所有声明")
    print("=" * 60)

    verify_ablation()
    verify_paired()
    verify_tests()
    verify_scenarios()
    verify_traces()
    verify_scorer_size()

    print("\n" + "=" * 60)
    total = len(results)
    passed = sum(1 for _, s, _ in results if s == PASS)
    failed = total - passed
    print(f"  总计: {total} 项声明 | {passed} 通过 | {failed} 失败")
    print("=" * 60)

    if failed > 0:
        print("\n失败项:")
        for name, status, detail in results:
            if status == FAIL:
                print(f"  {name}: {detail}")
        sys.exit(1)
    else:
        print("\n所有声明均可复现。")


if __name__ == "__main__":
    main()
