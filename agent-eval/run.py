"""CLI entry point for running evaluations."""

from __future__ import annotations

import argparse
import glob
import sys

from dotenv import load_dotenv

load_dotenv()

from orchestrator import Orchestrator, load_scenario


def main():
    parser = argparse.ArgumentParser(description="Agent 对话评测系统")
    parser.add_argument(
        "scenario",
        nargs="?",
        default=None,
        help="场景JSON文件路径。不指定则运行 scenarios/samples/ 下所有场景",
    )
    parser.add_argument(
        "--agent",
        default="baseline",
        choices=["baseline", "oracle", "careless"],
        help="Agent 类型（默认: baseline）",
    )
    parser.add_argument(
        "--no-llm-judge",
        action="store_true",
        help="跳过LLM评委（只跑规则检查，更快更便宜）",
    )
    parser.add_argument(
        "--trace-dir",
        default="traces",
        help="评估轨迹保存目录（默认: traces/）",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="安静模式，不打印对话过程",
    )
    args = parser.parse_args()

    if args.scenario:
        scenarios = [args.scenario]
    else:
        scenarios = sorted(glob.glob("scenarios/samples/*.json"))
        if not scenarios:
            print("错误: 没有找到场景文件。")
            sys.exit(1)

    print(f"共 {len(scenarios)} 个场景待评测 | Agent: {args.agent}\n")

    results = []
    for i, path in enumerate(scenarios, 1):
        print(f"[{i}/{len(scenarios)}] 加载场景: {path}")
        scenario = load_scenario(path)
        orch = Orchestrator(
            scenario=scenario,
            use_llm_judge=not args.no_llm_judge,
            trace_dir=args.trace_dir,
            agent_type=args.agent,
        )
        trace = orch.run(verbose=not args.quiet)
        results.append(trace)

    if len(results) > 1:
        print(f"\n{'=' * 60}")
        print("汇总结果")
        print(f"{'=' * 60}")
        for trace in results:
            r = trace.score_report
            validity = f" [{r.run_validity.status}]" if r.run_validity.status != "valid" else ""
            overall = f"{r.overall_score:.1%}" if r.overall_score is not None else "withheld"
            soft = f"{r.soft_score:.1%}" if r.soft_score is not None else "N/A"
            print(
                f"  {trace.scenario.name}{validity}: 综合 {overall} (硬 {r.hard_score:.1%} / 软 {soft}) — {r.task_outcome.status} — {len(r.failure_summary)} 个问题"
            )
        scored = [t for t in results if t.score_report.overall_score is not None]
        if scored:
            avg = sum(t.score_report.overall_score for t in scored) / len(scored)
            print(f"\n  平均综合得分: {avg:.1%} ({len(scored)}/{len(results)} 有效场景)")


if __name__ == "__main__":
    main()
