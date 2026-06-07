"""Meta-evaluation batch runner.

Runs 7 scenarios × 3 agent conditions = 21 conversations.
Saves all traces + a summary JSON for downstream metric calculation.

Agent conditions:
  A. good    — MiniMax + harness
  B. medium  — MiniMax bare (no harness)
  C. flawed  — scripted agent, all 5 flaw types, no LLM

Usage:
    python meta_eval_runner.py [--no-llm-judge] [--model minimax]
    python meta_eval_runner.py --flawed-only       # only run flawed agent (fast, no API)
    python meta_eval_runner.py --reuse-traces       # skip runs, rebuild summary from traces/meta_eval/
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from harness import HarnessConfig
from orchestrator_outbound import OutboundOrchestrator, load_outbound_scenario

SCENARIOS_DIR = Path(__file__).parent / "scenarios" / "outbound"
TRACE_DIR = Path(__file__).parent / "traces" / "meta_eval"
SUMMARY_PATH = TRACE_DIR / "meta_eval_summary.json"

DEFAULT_MODEL = "MiniMax-M2.7"

ALL_SCENARIOS = sorted(SCENARIOS_DIR.glob("*.json"))

CONDITIONS = {
    "good": {"harness": True, "agent_type": "baseline"},
    "medium": {"harness": False, "agent_type": "baseline"},
    "flawed": {"harness": False, "agent_type": "flawed"},
}


def run_one(
    scenario_path: Path,
    condition: str,
    model: str,
    no_llm_judge: bool,
) -> dict:
    scenario = load_outbound_scenario(str(scenario_path))
    cfg = CONDITIONS[condition]

    agent_model = None if condition == "flawed" else model
    agent_type = cfg["agent_type"]
    use_harness = cfg["harness"]

    print(f"\n{'─' * 50}")
    print(f"  场景: {scenario.name}  |  条件: {condition}  |  模型: {agent_model or 'scripted'}")
    print(f"{'─' * 50}")

    orch = OutboundOrchestrator(
        scenario=scenario,
        use_llm_judge=not no_llm_judge,
        trace_dir=str(TRACE_DIR),
        agent_type=agent_type,
        agent_model=agent_model,
        use_harness=use_harness,
        harness_config=HarnessConfig() if use_harness else None,
    )

    t0 = time.time()
    trace = orch.run(verbose=False)
    elapsed = time.time() - t0

    sr = trace.score_report
    ob = trace.metadata.get("outbound_report", {})

    result = {
        "scenario_id": scenario.id,
        "scenario_name": scenario.name,
        "scenario_file": scenario_path.name,
        "condition": condition,
        "model": agent_model or "flawed-scripted-v1",
        "trace_id": trace.id[:8],
        "hard_score": sr.hard_score,
        "soft_score": sr.soft_score,
        "overall_score": sr.overall_score,
        "overall_score_100": ob.get("overall_score_100"),
        "turns": sr.conversation_length,
        "failures": sr.failure_summary,
        "failure_count": len(sr.failure_summary),
        "opening_correct": ob.get("opening_correct"),
        "closing_correct": ob.get("closing_correct"),
        "forbidden_violations": ob.get("forbidden_violation_count", 0),
        "step_compliance": ob.get("step_compliance_score"),
        "call_result_correct": ob.get("call_result_correct"),
        "elapsed_sec": round(elapsed, 1),
        "timestamp": datetime.now().isoformat(),
    }

    score_str = f"{sr.overall_score:.1%}" if sr.overall_score is not None else "N/A"
    print(
        f"  → 综合: {score_str}  硬: {sr.hard_score:.1%}  轮次: {sr.conversation_length}  "
        f"失败项: {len(sr.failure_summary)}  耗时: {elapsed:.1f}s"
    )

    return result


def rebuild_summary_from_traces() -> list[dict]:
    """Rebuild summary from existing trace files."""
    results = []
    for path in sorted(TRACE_DIR.glob("outbound_*.json")):
        with open(path, encoding="utf-8") as f:
            trace = json.load(f)
        sr = trace.get("score_report", {})
        meta = trace.get("metadata", {})
        ob = meta.get("outbound_report", {})
        rm = trace.get("run_metadata", {})

        model = rm.get("model_backend", "")
        agent_type = rm.get("agent_type", "")
        has_harness = bool(meta.get("harness_summary"))

        if agent_type == "flawed" or "flawed" in model:
            condition = "flawed"
        elif has_harness:
            condition = "good"
        else:
            condition = "medium"

        results.append(
            {
                "scenario_id": trace.get("scenario", {}).get("id", ""),
                "scenario_name": trace.get("scenario", {}).get("name", ""),
                "condition": condition,
                "model": model,
                "trace_id": trace.get("id", "")[:8],
                "hard_score": sr.get("hard_score"),
                "soft_score": sr.get("soft_score"),
                "overall_score": sr.get("overall_score"),
                "overall_score_100": ob.get("overall_score_100"),
                "turns": sr.get("conversation_length"),
                "failures": sr.get("failure_summary", []),
                "failure_count": len(sr.get("failure_summary", [])),
                "opening_correct": ob.get("opening_correct"),
                "closing_correct": ob.get("closing_correct"),
                "forbidden_violations": ob.get("forbidden_violation_count", 0),
                "step_compliance": ob.get("step_compliance_score"),
                "call_result_correct": ob.get("call_result_correct"),
            }
        )
    return results


def main():
    parser = argparse.ArgumentParser(description="元评测批量运行器")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="被测模型（good/medium 条件）")
    parser.add_argument("--no-llm-judge", action="store_true", help="跳过 LLM 评委")
    parser.add_argument(
        "--flawed-only", action="store_true", help="只跑 flawed 条件（快速，不需要 API）"
    )
    parser.add_argument(
        "--reuse-traces", action="store_true", help="不跑新的，从已有 trace 重建 summary"
    )
    parser.add_argument("--conditions", default=None, help="逗号分隔的条件 (good,medium,flawed)")
    parser.add_argument(
        "--scenarios",
        default=None,
        help="逗号分隔的场景文件名前缀 (delivery_confirm,course_livestream)",
    )
    args = parser.parse_args()

    TRACE_DIR.mkdir(parents=True, exist_ok=True)

    if args.reuse_traces:
        results = rebuild_summary_from_traces()
        print(f"从 {len(results)} 个 trace 文件重建 summary")
    else:
        conditions = (
            [c.strip() for c in args.conditions.split(",")]
            if args.conditions
            else (["flawed"] if args.flawed_only else list(CONDITIONS.keys()))
        )

        scenarios = ALL_SCENARIOS
        if args.scenarios:
            prefixes = [p.strip() for p in args.scenarios.split(",")]
            scenarios = [s for s in ALL_SCENARIOS if any(s.stem.startswith(p) for p in prefixes)]

        results = []
        total = len(scenarios) * len(conditions)
        done = 0

        print(f"\n{'=' * 60}")
        print(
            f"元评测批量运行: {len(ALL_SCENARIOS)} 场景 × {len(conditions)} 条件 = {total} 通对话"
        )
        print(f"模型: {args.model}  |  LLM 评委: {'关' if args.no_llm_judge else '开'}")
        print(f"{'=' * 60}")

        for scenario_path in scenarios:
            for cond in conditions:
                done += 1
                print(f"\n[{done}/{total}]", end="")
                try:
                    result = run_one(scenario_path, cond, args.model, args.no_llm_judge)
                    results.append(result)
                except Exception as e:
                    print(f"  ✗ 失败: {e}")
                    results.append(
                        {
                            "scenario_file": scenario_path.name,
                            "condition": cond,
                            "error": str(e),
                        }
                    )

    summary = {
        "meta_eval_version": "1.0",
        "timestamp": datetime.now().isoformat(),
        "total_runs": len(results),
        "results": results,
    }

    with open(SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n✓ Summary 已保存: {SUMMARY_PATH}")

    _print_matrix(results)


def _print_matrix(results: list[dict]):
    """Print scenario × condition score matrix."""
    from collections import defaultdict

    matrix = defaultdict(dict)
    for r in results:
        if "error" in r:
            continue
        key = r.get("scenario_name") or r.get("scenario_file", "?")
        cond = r["condition"]
        score = r.get("overall_score_100")
        if score is not None:
            matrix[key][cond] = f"{score:.0f}"
        elif r.get("overall_score") is not None:
            matrix[key][cond] = f"{r['overall_score']:.0%}"
        else:
            matrix[key][cond] = "N/A"

    if not matrix:
        return

    print(f"\n{'=' * 70}")
    print("元评测得分矩阵（overall_score_100）")
    print(f"{'=' * 70}")
    header = f"{'场景':<30} {'good':>8} {'medium':>8} {'flawed':>8}"
    print(header)
    print(f"{'─' * 70}")
    for scenario_name, scores in sorted(matrix.items()):
        g = scores.get("good", "—")
        m = scores.get("medium", "—")
        f = scores.get("flawed", "—")
        print(f"{scenario_name:<30} {g:>8} {m:>8} {f:>8}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
