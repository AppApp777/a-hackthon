"""Structured report generator for agent evaluation traces.

Reads JSON traces from the traces/ directory and produces a comprehensive
10-section analysis report in Markdown, JSON, and/or HTML formats.

Usage:
    python report_generator.py [--output-dir reports/] [--format md|json|html|all]
    python report_generator.py --format html                # HTML only
    python report_generator.py --format html --trace-id outbound_528f852f  # single trace
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

TRACE_DIR = Path(__file__).parent / "traces"
TEMPLATE_DIR = Path(__file__).parent / "templates"
VERSION = "1.0.0"


# ── Loading ──────────────────────────────────────────────────────────────


def load_traces() -> list[dict]:
    """Load all valid evaluation traces from the traces/ directory."""
    traces: list[dict] = []
    if not TRACE_DIR.exists():
        return traces
    for p in sorted(TRACE_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if "id" in data and "score_report" in data:
                traces.append(data)
        except (json.JSONDecodeError, KeyError):
            continue
    return traces


# ── Helpers ──────────────────────────────────────────────────────────────


def _scores(traces: list[dict], key: str = "overall_score") -> list[float]:
    return [t["score_report"][key] for t in traces if t["score_report"].get(key) is not None]


def _model(t: dict) -> str:
    return t.get("run_metadata", {}).get("model_backend", "unknown")


def _difficulty(t: dict) -> str:
    return t.get("scenario", {}).get("difficulty", "unknown")


def _has_harness(t: dict) -> bool:
    return bool((t.get("metadata") or {}).get("harness_summary"))


def _outbound(t: dict) -> dict:
    return (t.get("metadata") or {}).get("outbound_report") or {}


def _diagnosis(t: dict) -> dict:
    return (t.get("metadata") or {}).get("diagnosis") or {}


def _stat_block(values: list[float]) -> dict:
    if not values:
        return {"avg": 0, "min": 0, "max": 0, "median": 0, "count": 0}
    return {
        "avg": round(statistics.mean(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "median": round(statistics.median(values), 3),
        "count": len(values),
    }


# ── Section builders ─────────────────────────────────────────────────────


def _sec1_executive_summary(traces: list[dict]) -> dict:
    scores = _scores(traces)
    models = sorted(set(_model(t) for t in traces))
    domains = sorted(set((t.get("metadata") or {}).get("domain", "unknown") for t in traces))
    return {
        "total_traces": len(traces),
        "models_tested": models,
        "domains": domains,
        "scores": _stat_block(scores),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _sec2_model_comparison(traces: list[dict]) -> list[dict]:
    by_model: dict[str, list[dict]] = defaultdict(list)
    for t in traces:
        by_model[_model(t)].append(t)
    rows = []
    for model, group in sorted(by_model.items()):
        rows.append(
            {
                "model": model,
                "traces": len(group),
                "overall": _stat_block(_scores(group, "overall_score")),
                "hard": _stat_block(_scores(group, "hard_score")),
                "soft": _stat_block(_scores(group, "soft_score")),
            }
        )
    return rows


def _sec3_difficulty_distribution(traces: list[dict]) -> list[dict]:
    by_diff: dict[str, list[dict]] = defaultdict(list)
    for t in traces:
        by_diff[_difficulty(t)].append(t)
    order = {"easy": 0, "medium": 1, "hard": 2, "extreme": 3}
    rows = []
    for diff, group in sorted(by_diff.items(), key=lambda x: order.get(x[0], 99)):
        rows.append(
            {
                "difficulty": diff,
                "count": len(group),
                "scores": _stat_block(_scores(group)),
            }
        )
    return rows


def _sec4_score_distribution(traces: list[dict]) -> list[dict]:
    buckets = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
    scores = _scores(traces)
    result = []
    for lo, hi in buckets:
        label = f"{lo:.1f}-{hi:.1f}" if hi <= 1.0 else f"{lo:.1f}-1.0"
        count = sum(1 for s in scores if lo <= s < hi)
        result.append({"bucket": label, "count": count})
    return result


def _sec5_step_compliance(traces: list[dict]) -> dict:
    compliances = [
        _outbound(t).get("step_compliance_score", 0)
        for t in traces
        if _outbound(t).get("step_compliance_score") is not None
    ]
    skipped_steps: Counter[str] = Counter()
    for t in traces:
        for step in _outbound(t).get("step_compliance", []):
            if step.get("status") in ("skipped", "missed", "not_reached"):
                skipped_steps[step.get("step_id", "?")] += 1
    return {
        "compliance_scores": _stat_block(compliances),
        "top_skipped_steps": skipped_steps.most_common(10),
    }


def _sec6_failure_modes(traces: list[dict]) -> dict:
    mode_counter: Counter[str] = Counter()
    severities: Counter[str] = Counter()
    for t in traces:
        diag = _diagnosis(t)
        for fm in diag.get("failure_modes") or []:
            mode_counter[fm] += 1
        sev = diag.get("severity")
        if sev:
            severities[sev] += 1
    return {
        "top_failure_modes": mode_counter.most_common(10),
        "severity_distribution": dict(severities),
    }


def _sec7_safety(traces: list[dict]) -> dict:
    total_violations = 0
    veto_count = 0
    traces_with_violations = 0
    for t in traces:
        ob = _outbound(t)
        v = ob.get("forbidden_violation_count", 0)
        total_violations += v
        if v > 0:
            traces_with_violations += 1
        hs = (t.get("metadata") or {}).get("harness_summary") or {}
        veto_count += hs.get("blocked_outputs", 0)
    return {
        "total_forbidden_violations": total_violations,
        "traces_with_violations": traces_with_violations,
        "total_veto_blocks": veto_count,
    }


def _sec8_harness_impact(traces: list[dict]) -> dict:
    with_h = [t for t in traces if _has_harness(t)]
    without_h = [t for t in traces if not _has_harness(t)]
    result: dict[str, Any] = {
        "with_harness": {"count": len(with_h), "scores": _stat_block(_scores(with_h))},
        "without_harness": {"count": len(without_h), "scores": _stat_block(_scores(without_h))},
    }
    wa = result["with_harness"]["scores"]["avg"]
    woa = result["without_harness"]["scores"]["avg"]
    result["delta"] = round(wa - woa, 3) if with_h and without_h else None
    return result


def _sec9_per_scenario(traces: list[dict]) -> list[dict]:
    by_scenario: dict[str, list[dict]] = defaultdict(list)
    for t in traces:
        by_scenario[t["scenario"]["name"]].append(t)
    rows = []
    for name, group in sorted(by_scenario.items()):
        scores = _scores(group)
        rows.append(
            {
                "scenario": name,
                "difficulty": group[0]["scenario"].get("difficulty", "?"),
                "runs": len(group),
                "scores": _stat_block(scores),
                "models_run": sorted(set(_model(t) for t in group)),
            }
        )
    return rows


def _sec10_recommendations(report: dict) -> list[str]:
    recs: list[str] = []
    # Low overall average
    avg = report["executive_summary"]["scores"]["avg"]
    if avg < 0.5:
        recs.append(
            f"Overall average score is low ({avg:.2f}). Prioritize fixing top failure modes."
        )
    # Compliance gaps
    comp = report["step_compliance"]["compliance_scores"]["avg"]
    if comp < 0.6:
        recs.append(
            f"Step compliance is weak ({comp:.2f}). Add harness step-injection for commonly skipped steps."
        )
    top_skipped = report["step_compliance"]["top_skipped_steps"]
    if top_skipped:
        names = ", ".join(s[0] for s in top_skipped[:3])
        recs.append(
            f"Most frequently skipped steps: {names}. Consider mandatory tool-call enforcement."
        )
    # Forbidden violations
    if report["safety"]["total_forbidden_violations"] > 0:
        recs.append(
            f"{report['safety']['total_forbidden_violations']} forbidden-behavior violations detected. Review prompt constraints."
        )
    # Harness uplift
    delta = report["harness_impact"]["delta"]
    if delta is not None and delta > 0.05:
        recs.append(
            f"Harness boosts scores by +{delta:.2f}. Consider always-on harness for production."
        )
    elif delta is not None and delta <= 0:
        recs.append(
            "Harness shows no score uplift. Investigate whether interventions are effective."
        )
    # Failure modes
    top_modes = report["failure_modes"]["top_failure_modes"]
    if top_modes:
        recs.append(
            f"Top failure mode: '{top_modes[0][0]}' ({top_modes[0][1]} occurrences). Target root-cause fixes first."
        )
    # Difficulty gaps
    for row in report["difficulty_distribution"]:
        if row["difficulty"] in ("hard", "extreme") and row["scores"]["avg"] < 0.4:
            recs.append(
                f"'{row['difficulty']}' scenarios average {row['scores']['avg']:.2f}. Add scenario-specific coaching prompts."
            )
    if not recs:
        recs.append("No critical issues detected. Continue monitoring with additional trace data.")
    return recs


# ── Report assembly ──────────────────────────────────────────────────────


def build_report(traces: list[dict]) -> dict:
    report: dict[str, Any] = {}
    report["executive_summary"] = _sec1_executive_summary(traces)
    report["model_comparison"] = _sec2_model_comparison(traces)
    report["difficulty_distribution"] = _sec3_difficulty_distribution(traces)
    report["score_distribution"] = _sec4_score_distribution(traces)
    report["step_compliance"] = _sec5_step_compliance(traces)
    report["failure_modes"] = _sec6_failure_modes(traces)
    report["safety"] = _sec7_safety(traces)
    report["harness_impact"] = _sec8_harness_impact(traces)
    report["per_scenario"] = _sec9_per_scenario(traces)
    report["recommendations"] = _sec10_recommendations(report)
    return report


# ── Markdown renderer ────────────────────────────────────────────────────


def render_markdown(report: dict) -> str:
    lines: list[str] = []

    def _add(text: str = "") -> None:
        lines.append(text)

    es = report["executive_summary"]
    _add("# Evaluation Report")
    _add(f"\nGenerated: {es['generated_at']}\n")

    # 1
    _add("## 1. Executive Summary\n")
    _add(f"- **Total traces**: {es['total_traces']}")
    _add(f"- **Models tested**: {', '.join(es['models_tested'])}")
    _add(f"- **Domains**: {', '.join(es['domains'])}")
    s = es["scores"]
    _add(
        f"- **Scores** — avg: {s['avg']:.3f}, min: {s['min']:.3f}, max: {s['max']:.3f}, median: {s['median']:.3f}"
    )

    # 2
    _add("\n## 2. Model Comparison\n")
    _add("| Model | Traces | Overall Avg | Hard Avg | Soft Avg |")
    _add("|-------|--------|-------------|----------|----------|")
    for row in report["model_comparison"]:
        _add(
            f"| {row['model']} | {row['traces']} | {row['overall']['avg']:.3f} | {row['hard']['avg']:.3f} | {row['soft']['avg']:.3f} |"
        )

    # 3
    _add("\n## 3. Difficulty Distribution\n")
    _add("| Difficulty | Count | Avg Score | Min | Max |")
    _add("|-----------|-------|-----------|-----|-----|")
    for row in report["difficulty_distribution"]:
        s = row["scores"]
        _add(
            f"| {row['difficulty']} | {row['count']} | {s['avg']:.3f} | {s['min']:.3f} | {s['max']:.3f} |"
        )

    # 4
    _add("\n## 4. Score Distribution\n")
    _add("| Bucket | Count |")
    _add("|--------|-------|")
    for row in report["score_distribution"]:
        bar = "#" * row["count"]
        _add(f"| {row['bucket']} | {row['count']} {bar} |")

    # 5
    _add("\n## 5. Step Compliance Analysis\n")
    sc = report["step_compliance"]["compliance_scores"]
    _add(f"- **Avg compliance**: {sc['avg']:.3f} (over {sc['count']} traces)")
    top = report["step_compliance"]["top_skipped_steps"]
    if top:
        _add("- **Top skipped steps**:")
        for step_id, count in top:
            _add(f"  - `{step_id}`: {count} times")
    else:
        _add("- No step-level compliance data available.")

    # 6
    _add("\n## 6. Failure Mode Analysis\n")
    fm = report["failure_modes"]
    if fm["top_failure_modes"]:
        _add("| Failure Mode | Occurrences |")
        _add("|-------------|-------------|")
        for mode, count in fm["top_failure_modes"]:
            _add(f"| {mode} | {count} |")
    else:
        _add("No failure modes recorded.")
    if fm["severity_distribution"]:
        _add(
            "\n**Severity distribution**: "
            + ", ".join(f"{k}: {v}" for k, v in sorted(fm["severity_distribution"].items()))
        )

    # 7
    _add("\n## 7. Safety & Forbidden Behavior\n")
    sf = report["safety"]
    _add(f"- **Total forbidden violations**: {sf['total_forbidden_violations']}")
    _add(f"- **Traces with violations**: {sf['traces_with_violations']}")
    _add(f"- **Harness veto blocks**: {sf['total_veto_blocks']}")

    # 8
    _add("\n## 8. Harness Impact\n")
    hi = report["harness_impact"]
    _add(
        f"- **With harness**: {hi['with_harness']['count']} traces, avg {hi['with_harness']['scores']['avg']:.3f}"
    )
    _add(
        f"- **Without harness**: {hi['without_harness']['count']} traces, avg {hi['without_harness']['scores']['avg']:.3f}"
    )
    if hi["delta"] is not None:
        sign = "+" if hi["delta"] >= 0 else ""
        _add(f"- **Delta**: {sign}{hi['delta']:.3f}")
    else:
        _add("- **Delta**: N/A (need both with/without traces)")

    # 9
    _add("\n## 9. Per-Scenario Details\n")
    _add("| Scenario | Difficulty | Runs | Avg | Min | Max | Models |")
    _add("|----------|-----------|------|-----|-----|-----|--------|")
    for row in report["per_scenario"]:
        s = row["scores"]
        models = ", ".join(row["models_run"])
        _add(
            f"| {row['scenario']} | {row['difficulty']} | {row['runs']} | {s['avg']:.3f} | {s['min']:.3f} | {s['max']:.3f} | {models} |"
        )

    # 10
    _add("\n## 10. Recommendations\n")
    for i, rec in enumerate(report["recommendations"], 1):
        _add(f"{i}. {rec}")

    _add("")
    return "\n".join(lines)


# ── HTML renderer (Jinja2, single-trace) ───────────────────────────────


def _prepare_trace_context(trace: dict) -> dict:
    """Extract all template variables from a single trace dict."""
    sr = trace["score_report"]
    meta = trace.get("metadata") or {}
    ob = meta.get("outbound_report") or {}
    diag = meta.get("diagnosis") or {}
    harness = meta.get("harness_summary")
    rubric = ob.get("rubric") or sr.get("rubric") or {}
    run_meta = trace.get("run_metadata") or {}
    cost = run_meta.get("cost_summary") or {}
    domain = meta.get("domain", "restaurant")
    domain_labels = {"outbound_call": "外呼", "restaurant": "订餐"}

    return {
        # Header
        "trace_id": trace["id"],
        "scenario_name": trace["scenario"]["name"],
        "difficulty": trace["scenario"].get("difficulty", ""),
        "domain": domain,
        "domain_label": domain_labels.get(domain, domain),
        "is_outbound": domain == "outbound_call",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "version": VERSION,
        # Scores
        "overall_score": sr.get("overall_score", 0) or 0,
        "hard_score": sr.get("hard_score", 0) or 0,
        "soft_score": sr.get("soft_score"),
        "conversation_length": sr.get("conversation_length", 0),
        # Outbound specifics
        "step_compliance_score": ob.get("step_compliance_score"),
        "branch_accuracy_score": ob.get("branch_accuracy_score"),
        "forbidden_violation_count": ob.get("forbidden_violation_count", 0),
        "opening_correct": ob.get("opening_correct", False),
        "closing_correct": ob.get("closing_correct", False),
        "progress_rate": ob.get("progress_rate"),
        # Rubric
        "rubric_dimensions": rubric.get("dimensions") or [],
        "rubric_grade": rubric.get("grade", ""),
        "rubric_total": rubric.get("rubric_total", 0),
        "rubric_max": rubric.get("rubric_max", 34),
        # Steps
        "step_compliance": ob.get("step_compliance") or [],
        # Violations
        "forbidden_violations": ob.get("forbidden_violations") or [],
        # Diagnosis
        "diagnosis": diag if diag else None,
        # Conversation
        "messages": (trace.get("conversation") or {}).get("messages") or [],
        # Harness
        "harness_summary": harness,
        # Cost
        "cost_summary": cost,
        # Checks
        "checks": sr.get("checks") or [],
        # Failure summary
        "failure_summary": sr.get("failure_summary") or [],
    }


def _get_jinja_env():
    """Create Jinja2 Environment with custom filters for SVG math."""
    from jinja2 import Environment, FileSystemLoader

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=False,  # We handle escaping in template
    )
    # Custom filters for trigonometry (SVG radar chart)
    env.filters["cos"] = lambda angle: math.cos(angle)
    env.filters["sin"] = lambda angle: math.sin(angle)
    return env


def generate_html_report(trace: dict, output_path: Path | None = None) -> str:
    """Render a single trace into a self-contained HTML report.

    Args:
        trace: A single evaluation trace dict.
        output_path: If provided, write the HTML to this file.

    Returns:
        The rendered HTML string.
    """
    env = _get_jinja_env()
    template = env.get_template("eval_report.html")
    ctx = _prepare_trace_context(trace)
    html = template.render(**ctx)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")

    return html


def generate_html_reports(traces: list[dict], out_dir: Path) -> list[Path]:
    """Generate individual HTML reports for each trace.

    Returns list of output file paths.
    """
    env = _get_jinja_env()
    template = env.get_template("eval_report.html")
    paths: list[Path] = []

    for trace in traces:
        ctx = _prepare_trace_context(trace)
        html = template.render(**ctx)
        # Use trace id truncated for filename
        safe_id = trace["id"][:16].replace("-", "")
        fname = f"eval_report_{safe_id}.html"
        fpath = out_dir / fname
        fpath.write_text(html, encoding="utf-8")
        paths.append(fpath)

    return paths


# ── Main ─────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate structured evaluation report")
    parser.add_argument(
        "--output-dir", default="reports/", help="Output directory (default: reports/)"
    )
    parser.add_argument(
        "--format",
        default="all",
        choices=["md", "json", "html", "all"],
        help="Output format (default: all)",
    )
    parser.add_argument(
        "--trace-id",
        default=None,
        help="Generate HTML report for a specific trace ID (prefix match)",
    )
    args = parser.parse_args()

    traces = load_traces()
    if not traces:
        print("No valid traces found in traces/. Run evaluations first.")
        return

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = args.format

    # Single-trace HTML mode
    if args.trace_id:
        tid = args.trace_id
        matched = [t for t in traces if t["id"].startswith(tid) or tid in t["id"]]
        if not matched:
            print(f"No trace matching '{args.trace_id}'. Available IDs:")
            for t in traces[:10]:
                print(f"  {t['id'][:16]}... ({t['scenario']['name']})")
            return
        trace = matched[0]
        safe_id = trace["id"][:16].replace("-", "")
        html_path = out_dir / f"eval_report_{safe_id}.html"
        generate_html_report(trace, html_path)
        size_kb = html_path.stat().st_size / 1024
        print(f"HTML report generated for trace {trace['id'][:12]}...:")
        print(f"  {html_path.resolve()} ({size_kb:.0f} KB)")
        return

    outputs: list[str] = []
    report: dict | None = None

    # Aggregate reports (MD, JSON)
    if fmt in ("md", "all"):
        report = build_report(traces)
        md_path = out_dir / "evaluation_report.md"
        md_path.write_text(render_markdown(report), encoding="utf-8")
        outputs.append(f"  Markdown: {md_path.resolve()}")

    if fmt in ("json", "all"):
        if report is None:
            report = build_report(traces)
        json_path = out_dir / "evaluation_report.json"
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        outputs.append(f"  JSON:     {json_path.resolve()}")

    # HTML reports (per-trace)
    if fmt in ("html", "all"):
        html_paths = generate_html_reports(traces, out_dir)
        if html_paths:
            total_kb = sum(p.stat().st_size for p in html_paths) / 1024
            outputs.append(
                f"  HTML:     {len(html_paths)} reports in {out_dir.resolve()}/ "
                f"(total {total_kb:.0f} KB)"
            )

    print(f"Report generated ({len(traces)} traces):")
    for line in outputs:
        print(line)


if __name__ == "__main__":
    main()
