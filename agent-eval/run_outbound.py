"""CLI entry point for outbound call evaluation.

Usage:
    python run_outbound.py <scenario.json> [--model claude-sonnet-4-6] [--no-llm-judge]
    python run_outbound.py <scenario.json> --compare sonnet,haiku,gpt-4o
"""

import argparse
import sys
from pathlib import Path

from eval_coverage import compute_coverage, format_coverage
from orchestrator_outbound import OutboundOrchestrator, load_outbound_scenario

# Model shorthand → full model ID
MODEL_ALIASES = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "gpt-4o": "gpt-4o",
    "gpt-4o-mini": "gpt-4o-mini",
    "deepseek": "deepseek-chat",
    "deepseek-r1": "deepseek-reasoner",
    "glm-4": "glm-4",
    "glm-4-flash": "glm-4-flash",
    "minimax": "MiniMax-M2.7",
    "minimax-fast": "MiniMax-M2.7-highspeed",
    "minimax-m2.5": "MiniMax-M2.5",
    "kimi": "kimi-for-coding",
    "kimi-k2.6": "kimi-for-coding",
    "qwen-max": "qwen-max",
    "qwen-plus": "qwen-plus",
    "qwen-turbo": "qwen-turbo",
    "qwen-long": "qwen-long",
}


def resolve_model(name: str) -> str:
    return MODEL_ALIASES.get(name.lower(), name)


def run_single(
    scenario_path: str,
    model: str,
    no_llm_judge: bool,
    trace_dir: str,
    use_harness: bool = False,
    verbose: bool = True,
):
    from harness import HarnessConfig

    scenario = load_outbound_scenario(scenario_path)
    if verbose:
        print(f"\n{'#' * 60}")
        print(f"# 模型: {model}")
        print(f"# 场景: {scenario.name}")
        if use_harness:
            print("# Harness: 已启用")
        print(f"{'#' * 60}")

    orchestrator = OutboundOrchestrator(
        scenario=scenario,
        use_llm_judge=not no_llm_judge,
        trace_dir=trace_dir,
        agent_type="baseline",
        agent_model=model,
        use_harness=use_harness,
        harness_config=HarnessConfig() if use_harness else None,
    )

    trace = orchestrator.run(verbose=verbose)
    return trace


def run_compare(scenario_path: str, models: list[str], no_llm_judge: bool, trace_dir: str):
    """Run same scenario with multiple models and print comparison table."""
    from models_outbound import OutboundScoreReport

    scenario = load_outbound_scenario(scenario_path)
    results = []
    outbound_reports: list[OutboundScoreReport] = []

    for model in models:
        full_model = resolve_model(model)
        print(f"\n{'=' * 60}")
        print(f"正在测试模型: {model} ({full_model})")
        print(f"{'=' * 60}")
        try:
            trace = run_single(scenario_path, full_model, no_llm_judge, trace_dir, verbose=True)
            sr = trace.score_report
            ob_report = trace.metadata.get("outbound_report")
            if ob_report:
                outbound_reports.append(OutboundScoreReport(**ob_report))
            sim_q = trace.metadata.get("simulator_quality", {})
            results.append(
                {
                    "model": model,
                    "full_model": full_model,
                    "hard_score": sr.hard_score,
                    "soft_score": sr.soft_score,
                    "overall_score": sr.overall_score,
                    "turns": sr.conversation_length,
                    "failures": len(sr.failure_summary),
                    "trace_id": trace.id[:8],
                    "sim_quality": "✓" if sim_q.get("passed", True) else "⚠",
                }
            )
        except Exception as e:
            print(f"  ✗ 模型 {model} 运行失败: {e}")
            results.append(
                {
                    "model": model,
                    "full_model": full_model,
                    "hard_score": None,
                    "soft_score": None,
                    "overall_score": None,
                    "turns": 0,
                    "failures": -1,
                    "trace_id": "FAILED",
                    "sim_quality": "—",
                }
            )

    # Print comparison table
    print(f"\n\n{'=' * 70}")
    print("模型对比结果")
    print(f"{'=' * 70}")
    header = f"{'模型':<20} {'硬指标':<10} {'软指标':<10} {'综合':<10} {'轮次':<6} {'失败项':<6} {'模拟器':<6}"
    print(header)
    print(f"{'-' * 70}")
    for r in results:
        hard = f"{r['hard_score']:.1%}" if r["hard_score"] is not None else "FAIL"
        soft = f"{r['soft_score']:.1%}" if r["soft_score"] is not None else "N/A"
        overall = f"{r['overall_score']:.1%}" if r["overall_score"] is not None else "N/A"
        print(
            f"{r['model']:<20} {hard:<10} {soft:<10} {overall:<10} {r['turns']:<6} {r['failures']:<6} {r['sim_quality']:<6}"
        )
    print(f"{'=' * 70}")

    # Coverage report across all successful runs
    if outbound_reports:
        cov = compute_coverage(scenario, outbound_reports)
        print(f"\n{format_coverage(cov)}")
    print()


def main():
    parser = argparse.ArgumentParser(description="外呼场景评测 — 支持多模型对比")
    parser.add_argument("scenario", nargs="?", default=None, help="场景JSON文件路径")
    parser.add_argument(
        "--model",
        default=None,
        help="被测模型 (如 sonnet, haiku, gpt-4o, deepseek, 或完整model ID)",
    )
    parser.add_argument(
        "--compare", default=None, help="对比多个模型，逗号分隔 (如 sonnet,haiku,gpt-4o)"
    )
    parser.add_argument(
        "--harness", action="store_true", help="启用 Harness 干预层（修复模型弱点）"
    )
    parser.add_argument("--no-llm-judge", action="store_true", help="跳过LLM评委（仅规则打分）")
    parser.add_argument(
        "--fast-mode", action="store_true", help="快速评分模式（单次批量LLM调用，≤10秒）"
    )
    parser.add_argument("--trace-dir", default="traces", help="输出轨迹目录 (默认: traces)")
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="重复运行次数，计算 pass^k 可复现性指标 (τ-bench 方法)",
    )
    parser.add_argument(
        "--demo",
        nargs="?",
        const="auto",
        default=None,
        help="Demo 模式（不需要 API key）：无参数自动选最佳案例，或指定 trace 文件路径",
    )
    parser.add_argument(
        "--branch-test",
        action="store_true",
        help="枚举策略图所有分支，展示强制分支覆盖目标",
    )
    args = parser.parse_args()

    if args.demo is not None:
        import json

        demo_arg = args.demo

        if demo_arg == "auto":
            with open("calibration/ablation_report.json", encoding="utf-8") as _af:
                ablation = json.load(_af)
            best = max(
                ablation["per_trace"],
                key=lambda t: t["soft_judge_only"] - t["full_system"],
            )
            best_tid = best["trace_id"]
            candidates = list(Path("traces").rglob(f"*{best_tid[:8]}*.json"))
            if not candidates:
                print(f"错误: 找不到 trace {best_tid[:8]}")
                sys.exit(1)
            demo_trace = candidates[0]
            llm_only_score = best["soft_judge_only"]
            full_system_score = best["full_system"]
        else:
            demo_trace = Path(demo_arg)
            llm_only_score = None
            full_system_score = None

        if not demo_trace.exists():
            print(f"错误: trace 文件不存在: {demo_trace}")
            sys.exit(1)

        with open(demo_trace, encoding="utf-8") as f:
            trace_data = json.load(f)
        scenario_id = trace_data.get("scenario", {}).get("id", "")
        scenario_file = None
        for p in Path("scenarios/outbound").glob("*.json"):
            with open(p, encoding="utf-8") as f:
                sc = json.load(f)
            if sc.get("id") == scenario_id:
                scenario_file = p
                break

        if scenario_file is None:
            print(f"错误: 找不到场景 {scenario_id} 的定义文件")
            sys.exit(1)

        print(f"{'#' * 60}")
        print("# Demo 模式 — 从冻结 trace 回放（不需要 API key）")
        print(f"# Trace: {demo_trace.name}")
        print(f"# 场景: {trace_data.get('scenario', {}).get('name', '?')}")
        if llm_only_score is not None:
            print(f"# 对比: LLM 评委 {llm_only_score}% vs 完整系统 {full_system_score}%")
        print(f"{'#' * 60}")

        scenario = load_outbound_scenario(str(scenario_file))
        orchestrator = OutboundOrchestrator(
            scenario=scenario,
            use_llm_judge=False,
            trace_dir=args.trace_dir,
            agent_type="mock",
            agent_model=str(demo_trace),
            fast_mode=True,
        )
        trace = orchestrator.run(verbose=True)

        print(f"\n{'=' * 60}")
        print("Demo 结果")
        print(f"{'=' * 60}")
        print(f"  完整系统评分:  {trace.score_report.overall_score:.1%}")
        if llm_only_score is not None:
            print(f"  LLM 评委评分:  {llm_only_score}%  ← 仅 LLM 打分（虚高）")
            gap = llm_only_score - trace.score_report.overall_score * 100
            print(f"  差距:          {gap:.1f}pp ← 规则验证层拉低的幅度")
        print(f"{'=' * 60}")
        sys.exit(0)

    if args.branch_test and args.scenario:
        import json as _json

        from eval_coverage import format_branch_enumeration
        from models_outbound import OutboundScoreReport

        scenario = load_outbound_scenario(args.scenario)
        print(format_branch_enumeration(scenario))

        trace_dir = Path(args.trace_dir)
        if trace_dir.exists():
            reports = []
            for tp in trace_dir.rglob("*.json"):
                try:
                    td = _json.loads(tp.read_text(encoding="utf-8"))
                    if td.get("scenario", {}).get("id") == scenario.id:
                        ob = td.get("metadata", {}).get("outbound_report")
                        if ob:
                            reports.append(OutboundScoreReport(**ob))
                except Exception:
                    pass
            if reports:
                from eval_coverage import format_branch_coverage_report

                print()
                print(format_branch_coverage_report(scenario, reports))
            else:
                print(f"\n  (traces/ 中未找到场景 {scenario.id} 的历史 trace)")

        sys.exit(0)

    if args.scenario is None:
        print("错误: 需要指定场景文件（或使用 --demo 模式）")
        parser.print_help()
        sys.exit(1)

    scenario_path = Path(args.scenario)
    if not scenario_path.exists():
        print(f"错误: 场景文件不存在: {scenario_path}")
        sys.exit(1)

    if args.compare:
        models = [m.strip() for m in args.compare.split(",")]
        run_compare(str(scenario_path), models, args.no_llm_judge, args.trace_dir)
    else:
        model = resolve_model(args.model) if args.model else None
        scenario = load_outbound_scenario(str(scenario_path))
        print(f"已加载场景: {scenario.name} ({scenario.call_type}, {scenario.difficulty.value})")
        if model:
            print(f"被测模型: {model}")
        if args.harness:
            print("Harness: 已启用")

        from harness import HarnessConfig

        orchestrator = OutboundOrchestrator(
            scenario=scenario,
            use_llm_judge=not args.no_llm_judge,
            trace_dir=args.trace_dir,
            agent_type="baseline",
            agent_model=model,
            use_harness=args.harness,
            harness_config=HarnessConfig() if args.harness else None,
            fast_mode=args.fast_mode,
        )
        if args.repeat > 1:
            from reliability import compute_pass_k, format_pass_k

            scores = []
            for i in range(args.repeat):
                print(f"\n--- 第 {i + 1}/{args.repeat} 次运行 ---")
                orch = OutboundOrchestrator(
                    scenario=scenario,
                    use_llm_judge=not args.no_llm_judge,
                    trace_dir=args.trace_dir,
                    agent_type="baseline",
                    agent_model=model,
                    use_harness=args.harness,
                    harness_config=HarnessConfig() if args.harness else None,
                    fast_mode=args.fast_mode,
                )
                t = orch.run(verbose=False)
                s = t.score_report.overall_score or 0
                scores.append(s)
                print(f"  得分: {s:.1%}  轨迹: {t.id[:8]}")
            pass_k = compute_pass_k(scores)
            print(f"\n{format_pass_k(pass_k, scores)}")
        else:
            trace = orchestrator.run(verbose=True)
            print(f"\n完成。轨迹ID: {trace.id[:8]}")


if __name__ == "__main__":
    main()
