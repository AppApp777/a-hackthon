"""Harness ablation study runner.

Runs scenarios × models × harness on/off to measure Harness effectiveness.
Produces a matrix showing which models benefit from Harness and by how much.

Usage:
    # Full ablation (needs API keys for each model)
    python ablation_runner.py --models sonnet,haiku,minimax --scenarios delivery_confirm,after_sales

    # Quick mode: rule-only scoring, no LLM judge
    python ablation_runner.py --models sonnet,haiku --no-llm-judge

    # Reuse existing traces to rebuild the matrix without re-running
    python ablation_runner.py --reuse-traces

    # Single model deep dive
    python ablation_runner.py --models haiku --all-scenarios
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from harness import HarnessConfig
from orchestrator_outbound import OutboundOrchestrator, load_outbound_scenario

SCENARIOS_DIR = Path(__file__).parent / "scenarios" / "outbound"
TRACE_DIR = Path(__file__).parent / "traces" / "ablation"
REPORT_PATH = TRACE_DIR / "ablation_report.json"

DEMO_SCENARIOS = [
    "delivery_confirm_basic",
    "after_sales_complaint",
    "rider_feimaotui_notify",
    "stress_test_extreme",
    "rider_holiday_overtime",
    "merchant_violation_warning",
]


def run_one(
    scenario_path: Path,
    model: str,
    use_harness: bool,
    no_llm_judge: bool,
    adaptive: bool = False,
) -> dict:
    scenario = load_outbound_scenario(str(scenario_path))
    label = "adaptive" if (use_harness and adaptive) else ("harness" if use_harness else "bare")

    print(f"  {scenario.name[:28]:<30} {model:<16} {label:<8}", end="", flush=True)

    harness_config = None
    if use_harness:
        harness_config = HarnessConfig(adaptive=adaptive)

    orch = OutboundOrchestrator(
        scenario=scenario,
        use_llm_judge=not no_llm_judge,
        trace_dir=str(TRACE_DIR),
        agent_type="baseline",
        agent_model=model,
        use_harness=use_harness,
        harness_config=harness_config,
    )

    t0 = time.time()
    trace = orch.run(verbose=False)
    elapsed = time.time() - t0

    sr = trace.score_report
    ob = trace.metadata.get("outbound_report", {})
    hs = trace.metadata.get("harness_summary", {})

    result = {
        "scenario_id": scenario.id,
        "scenario_name": scenario.name,
        "scenario_file": scenario_path.name,
        "difficulty": scenario.difficulty,
        "model": model,
        "harness": use_harness,
        "adaptive": adaptive,
        "trace_id": trace.id[:8],
        "overall_score_100": ob.get("overall_score_100"),
        "hard_score": sr.hard_score,
        "soft_score": sr.soft_score,
        "overall_score": sr.overall_score,
        "turns": sr.conversation_length,
        "opening_correct": ob.get("opening_correct"),
        "closing_correct": ob.get("closing_correct"),
        "forbidden_violations": ob.get("forbidden_violation_count", 0),
        "step_compliance": ob.get("step_compliance_score"),
        "call_result_correct": ob.get("call_result_correct"),
        "harness_interventions": hs.get("total_interventions", 0) if hs else 0,
        "harness_blocks": hs.get("blocked_outputs", 0) if hs else 0,
        "adaptive_level": hs.get("adaptive", {}).get("level") if hs else None,
        "failure_count": len(sr.failure_summary),
        "failures": sr.failure_summary,
        "elapsed_sec": round(elapsed, 1),
        "timestamp": datetime.now().isoformat(),
    }

    score = ob.get("overall_score_100")
    score_str = f"{score:.0f}" if score is not None else "N/A"
    interventions = result["harness_interventions"]
    print(f"  → {score_str:>4}分  {interventions}次干预  {elapsed:.1f}s")

    return result


def rebuild_from_traces() -> list[dict]:
    results = []
    for path in sorted(TRACE_DIR.glob("outbound_*.json")):
        with open(path, encoding="utf-8") as f:
            trace = json.load(f)
        sr = trace.get("score_report", {})
        meta = trace.get("metadata", {})
        ob = meta.get("outbound_report", {})
        rm = trace.get("run_metadata", {})
        hs = meta.get("harness_summary")

        results.append(
            {
                "scenario_id": trace.get("scenario", {}).get("id", ""),
                "scenario_name": trace.get("scenario", {}).get("name", ""),
                "model": rm.get("model_backend", ""),
                "harness": bool(hs),
                "overall_score_100": ob.get("overall_score_100"),
                "hard_score": sr.get("hard_score"),
                "soft_score": sr.get("soft_score"),
                "overall_score": sr.get("overall_score"),
                "turns": sr.get("conversation_length"),
                "opening_correct": ob.get("opening_correct"),
                "closing_correct": ob.get("closing_correct"),
                "forbidden_violations": ob.get("forbidden_violation_count", 0),
                "step_compliance": ob.get("step_compliance_score"),
                "harness_interventions": hs.get("total_interventions", 0) if hs else 0,
                "harness_blocks": hs.get("blocked_outputs", 0) if hs else 0,
                "failure_count": len(sr.get("failure_summary", [])),
            }
        )
    return results


def print_ablation_matrix(results: list[dict]):
    """Print the core ablation matrix: model × harness → score."""
    matrix: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    for r in results:
        if "error" in r:
            continue
        model = r["model"]
        if r.get("adaptive"):
            label = "adaptive"
        elif r["harness"]:
            label = "harness"
        else:
            label = "bare"
        score = r.get("overall_score_100")
        if score is not None:
            matrix[model][label].append(score)

    if not matrix:
        print("无有效数据")
        return

    print(f"\n{'═' * 72}")
    print("  Harness 消融实验 — 模型 × Harness 开/关")
    print(f"{'═' * 72}")
    print(f"  {'模型':<20} {'无Harness':>10} {'有Harness':>10} {'Δ':>8} {'救活率':>10}")
    print(f"  {'─' * 62}")

    for model in sorted(matrix.keys()):
        bare_scores = matrix[model].get("bare", [])
        harness_scores = matrix[model].get("harness", [])

        bare_avg = sum(bare_scores) / len(bare_scores) if bare_scores else None
        harness_avg = sum(harness_scores) / len(harness_scores) if harness_scores else None

        bare_str = f"{bare_avg:.1f}" if bare_avg is not None else "—"
        harness_str = f"{harness_avg:.1f}" if harness_avg is not None else "—"

        if bare_avg is not None and harness_avg is not None:
            delta = harness_avg - bare_avg
            delta_str = f"+{delta:.1f}" if delta >= 0 else f"{delta:.1f}"
            if bare_avg > 0:
                rescue_pct = delta / bare_avg * 100
                rescue_str = f"{rescue_pct:+.0f}%"
            else:
                rescue_str = "N/A"
        else:
            delta_str = "—"
            rescue_str = "—"

        print(f"  {model:<20} {bare_str:>10} {harness_str:>10} {delta_str:>8} {rescue_str:>10}")

    print(f"{'═' * 72}")


def print_scenario_matrix(results: list[dict]):
    """Print per-scenario breakdown: scenario × (model, harness) → score."""
    matrix: dict[str, dict[str, str]] = defaultdict(dict)

    for r in results:
        if "error" in r:
            continue
        scenario = r.get("scenario_name", "?")[:25]
        model = r["model"]
        label = "H" if r["harness"] else "B"
        key = f"{model[:8]}_{label}"
        score = r.get("overall_score_100")
        matrix[scenario][key] = f"{score:.0f}" if score is not None else "—"

    if not matrix:
        return

    all_keys = sorted({k for scores in matrix.values() for k in scores})

    print(f"\n{'═' * (30 + 10 * len(all_keys))}")
    print("  场景 × 模型·Harness 得分明细（B=无Harness, H=有Harness）")
    print(f"{'═' * (30 + 10 * len(all_keys))}")

    header = f"  {'场景':<28}" + "".join(f"{k:>10}" for k in all_keys)
    print(header)
    print(f"  {'─' * (26 + 10 * len(all_keys))}")

    for scenario, scores in sorted(matrix.items()):
        row = f"  {scenario:<28}" + "".join(f"{scores.get(k, '—'):>10}" for k in all_keys)
        print(row)

    print(f"{'═' * (30 + 10 * len(all_keys))}")


def print_error_type_analysis(results: list[dict]):
    """Analyze which error types Harness fixes vs can't fix."""
    error_categories = {
        "opening_wrong": {
            "fixable": False,
            "bare": 0,
            "harness": 0,
            "bare_total": 0,
            "harness_total": 0,
        },
        "closing_wrong": {
            "fixable": True,
            "bare": 0,
            "harness": 0,
            "bare_total": 0,
            "harness_total": 0,
        },
        "forbidden_word": {
            "fixable": True,
            "bare": 0,
            "harness": 0,
            "bare_total": 0,
            "harness_total": 0,
        },
        "step_incomplete": {
            "fixable": True,
            "bare": 0,
            "harness": 0,
            "bare_total": 0,
            "harness_total": 0,
        },
    }

    for r in results:
        if "error" in r:
            continue
        label = "harness" if r["harness"] else "bare"
        error_categories["opening_wrong"][label] += 0 if r.get("opening_correct") else 1
        error_categories["opening_wrong"][f"{label}_total"] += 1
        error_categories["closing_wrong"][label] += 0 if r.get("closing_correct") else 1
        error_categories["closing_wrong"][f"{label}_total"] += 1
        error_categories["forbidden_word"][label] += r.get("forbidden_violations", 0)
        error_categories["forbidden_word"][f"{label}_total"] += 1
        error_categories["step_incomplete"][label] += (
            0 if (r.get("step_compliance") or 0) >= 0.8 else 1
        )
        error_categories["step_incomplete"][f"{label}_total"] += 1

    print(f"\n{'═' * 72}")
    print("  错误类型 × Harness 修复能力")
    print(f"{'═' * 72}")
    print(f"  {'错误类型':<20} {'无Harness':>12} {'有Harness':>12} {'能修复?':>10}")
    print(f"  {'─' * 56}")

    for etype, data in error_categories.items():
        bare_rate = data["bare"] / max(data["bare_total"], 1)
        harness_rate = data["harness"] / max(data["harness_total"], 1)
        fixable = "✅ 能" if data["fixable"] else "❌ 不能"
        print(f"  {etype:<20} {bare_rate:>10.0%}   {harness_rate:>10.0%}   {fixable:>10}")

    print(f"{'═' * 72}")


def main():
    parser = argparse.ArgumentParser(description="Harness 消融实验")
    parser.add_argument(
        "--models",
        default="sonnet,haiku",
        help="逗号分隔的模型列表（如 sonnet,haiku,minimax,gpt-4o,deepseek）",
    )
    parser.add_argument("--no-llm-judge", action="store_true", help="跳过 LLM 评委（快速模式）")
    parser.add_argument("--reuse-traces", action="store_true", help="从已有 trace 重建报告")
    parser.add_argument(
        "--all-scenarios", action="store_true", help="跑全部 24 个场景（默认只跑 6 个 demo 场景）"
    )
    parser.add_argument("--scenarios", default=None, help="逗号分隔的场景文件名前缀")
    parser.add_argument(
        "--adaptive",
        action="store_true",
        help="额外跑一组 adaptive harness（bare / harness / adaptive 三组对比）",
    )
    args = parser.parse_args()

    TRACE_DIR.mkdir(parents=True, exist_ok=True)

    if args.reuse_traces:
        results = rebuild_from_traces()
        print(f"从 {len(results)} 个 trace 文件重建报告")
    else:
        models = [m.strip() for m in args.models.split(",")]

        if args.scenarios:
            prefixes = [p.strip() for p in args.scenarios.split(",")]
            scenarios = [
                s
                for s in sorted(SCENARIOS_DIR.glob("*.json"))
                if any(s.stem.startswith(p) for p in prefixes)
            ]
        elif args.all_scenarios:
            scenarios = sorted(SCENARIOS_DIR.glob("*.json"))
        else:
            scenarios = [
                SCENARIOS_DIR / f"{name}.json"
                for name in DEMO_SCENARIOS
                if (SCENARIOS_DIR / f"{name}.json").exists()
            ]

        conditions = [(False, False), (True, False)]
        if args.adaptive:
            conditions.append((True, True))
        cond_labels = "bare/harness/adaptive" if args.adaptive else "bare/harness"
        total = len(scenarios) * len(models) * len(conditions)
        print(f"\n{'═' * 72}")
        print("  Harness 消融实验")
        print(
            f"  {len(scenarios)} 场景 × {len(models)} 模型 × {len(conditions)} ({cond_labels}) = {total} 通对话"
        )
        print(f"  模型: {', '.join(models)}")
        print(f"  LLM 评委: {'关' if args.no_llm_judge else '开'}")
        print(f"{'═' * 72}")

        results = []
        done = 0

        for scenario_path in scenarios:
            for model in models:
                for use_harness, adaptive in conditions:
                    done += 1
                    print(f"\n[{done}/{total}]", end="")
                    try:
                        result = run_one(
                            scenario_path,
                            model,
                            use_harness,
                            args.no_llm_judge,
                            adaptive=adaptive,
                        )
                        results.append(result)
                    except Exception as e:
                        print(f"  ✗ 失败: {e}")
                        results.append(
                            {
                                "scenario_file": scenario_path.name,
                                "model": model,
                                "harness": use_harness,
                                "error": str(e),
                            }
                        )

    report = {
        "ablation_version": "1.0",
        "timestamp": datetime.now().isoformat(),
        "total_runs": len(results),
        "results": results,
    }

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n✓ 报告已保存: {REPORT_PATH}")

    print_ablation_matrix(results)
    print_scenario_matrix(results)
    print_error_type_analysis(results)


if __name__ == "__main__":
    main()
