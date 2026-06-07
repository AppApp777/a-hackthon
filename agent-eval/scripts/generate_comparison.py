"""Generate multi-model comparison table from existing trace files."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def load_traces(traces_dir: Path) -> list[dict]:
    traces = []
    for p in sorted(traces_dir.glob("*.json")):
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if "scenario" not in data or "score_report" not in data:
            continue
        traces.append(data)
    return traces


def build_comparison(traces: list[dict]) -> dict:
    model_scenario: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    model_stats: dict[str, list[float]] = defaultdict(list)

    for t in traces:
        rm = t.get("run_metadata", {})
        model = rm.get("model_backend", "unknown")
        has_h = bool((t.get("metadata") or {}).get("harness_summary"))
        label = f"{'H+' if has_h else ''}{model}"

        scenario = t["scenario"].get("name", "?")
        difficulty = t["scenario"].get("difficulty", "?")
        sr = t["score_report"]
        ob = (t.get("metadata") or {}).get("outbound_report", {})

        row = {
            "overall": sr.get("overall_score"),
            "hard": sr.get("hard_score"),
            "soft": sr.get("soft_score"),
            "step": ob.get("step_compliance_score"),
            "branch": ob.get("branch_accuracy_score"),
            "difficulty": difficulty,
            "has_harness": has_h,
        }

        model_scenario[label][scenario].append(row)
        if row["overall"] is not None:
            model_stats[label].append(row["overall"])

    return {
        "model_scenario": {k: dict(v) for k, v in model_scenario.items()},
        "model_stats": {
            k: {
                "count": len(v),
                "avg": sum(v) / len(v) if v else 0,
                "min": min(v) if v else 0,
                "max": max(v) if v else 0,
            }
            for k, v in model_stats.items()
        },
    }


def print_summary(comparison: dict):
    stats = comparison["model_stats"]
    ms = comparison["model_scenario"]

    print("=" * 70)
    print("多模型评测对比报告")
    print("=" * 70)

    print("\n## 模型总览\n")
    print(f"{'模型':<35} {'Traces':>7} {'平均':>7} {'最低':>7} {'最高':>7}")
    print("-" * 70)
    for model in sorted(stats, key=lambda m: -stats[m]["avg"]):
        s = stats[model]
        print(
            f"{model:<35} {s['count']:>7} "
            f"{s['avg'] * 100:>6.1f}% {s['min'] * 100:>6.1f}% {s['max'] * 100:>6.1f}%"
        )

    print("\n## 场景 × 模型 得分矩阵\n")
    models = sorted(stats, key=lambda m: -stats[m]["avg"])
    all_scenarios: set[str] = set()
    for m in ms.values():
        all_scenarios.update(m.keys())

    header = f"{'场景':<25}"
    for m in models:
        short = m[:15]
        header += f" {short:>15}"
    print(header)
    print("-" * len(header))

    for scenario in sorted(all_scenarios):
        row = f"{scenario[:24]:<25}"
        for model in models:
            entries = ms.get(model, {}).get(scenario, [])
            if entries:
                avg = sum(e["overall"] for e in entries if e["overall"] is not None) / max(
                    1, len([e for e in entries if e["overall"] is not None])
                )
                row += f" {avg * 100:>14.0f}%"
            else:
                row += f" {'—':>15}"
        print(row)


def save_json(comparison: dict, output_path: Path):
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(comparison, f, ensure_ascii=False, indent=2, default=str)
    print(f"\nJSON saved to {output_path}")


if __name__ == "__main__":
    traces_dir = Path(__file__).parent.parent / "traces"
    output_path = Path(__file__).parent.parent / "traces" / "_comparison_report.json"

    if not traces_dir.exists():
        print(f"Traces directory not found: {traces_dir}", file=sys.stderr)
        sys.exit(1)

    traces = load_traces(traces_dir)
    print(f"Loaded {len(traces)} traces from {traces_dir}\n")

    comparison = build_comparison(traces)
    print_summary(comparison)
    save_json(comparison, output_path)
