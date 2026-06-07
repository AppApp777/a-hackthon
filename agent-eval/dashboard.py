"""Web dashboard for viewing evaluation traces."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Agent 对话评测系统")

TRACE_DIR = Path("traces")
STATIC_DIR = Path("static")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/traces")
async def list_traces():
    traces = []
    if TRACE_DIR.exists():
        for p in sorted(TRACE_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            meta = data.get("metadata", {})
            domain = meta.get("domain", "restaurant")
            outbound = meta.get("outbound_report", {})
            entry = {
                "id": data["id"],
                "scenario_name": data["scenario"]["name"],
                "difficulty": data["scenario"]["difficulty"],
                "overall_score": data["score_report"]["overall_score"],
                "hard_score": data["score_report"]["hard_score"],
                "soft_score": data["score_report"]["soft_score"],
                "turns": data["score_report"]["conversation_length"],
                "failures": len(data["score_report"]["failure_summary"]),
                "file": p.name,
                "domain": domain,
                "model": data.get("run_metadata", {}).get("model_backend", ""),
                "agent_type": data.get("run_metadata", {}).get("agent_type", ""),
            }
            # Cost summary from run_metadata
            cost_summary = data.get("run_metadata", {}).get("cost_summary", {})
            if cost_summary:
                entry["cost_summary"] = cost_summary

            if domain == "outbound_call":
                entry.update(
                    {
                        "step_compliance_score": outbound.get("step_compliance_score", 0),
                        "branch_accuracy_score": outbound.get("branch_accuracy_score", 0),
                        "forbidden_violations": outbound.get("forbidden_violation_count", 0),
                        "opening_correct": outbound.get("opening_correct", False),
                        "closing_correct": outbound.get("closing_correct", False),
                        "has_harness": meta.get("harness_summary") is not None,
                        "has_diagnosis": meta.get("diagnosis") is not None,
                        "harness_interventions": (meta.get("harness_summary") or {}).get(
                            "total_interventions", 0
                        ),
                        "progress_rate": outbound.get("progress_rate"),
                    }
                )
            traces.append(entry)
    return traces


@app.get("/api/overview")
async def overview():
    """Aggregate: model × scenario score matrix + failure mode stats."""
    traces = []
    if TRACE_DIR.exists():
        for p in TRACE_DIR.glob("*.json"):
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            traces.append(data)

    if not traces:
        return {"matrix": [], "models": [], "scenarios": [], "stats": {}}

    models = sorted(set(t.get("run_metadata", {}).get("model_backend", "unknown") for t in traces))
    scenarios = sorted(set(t["scenario"]["name"] for t in traces))

    matrix = []
    for t in traces:
        model = t.get("run_metadata", {}).get("model_backend", "unknown")
        has_harness = bool((t.get("metadata") or {}).get("harness_summary"))
        label = f"{'H+' if has_harness else ''}{model}"
        matrix.append(
            {
                "model": model,
                "label": label,
                "scenario": t["scenario"]["name"],
                "difficulty": t["scenario"]["difficulty"],
                "overall_score": t["score_report"]["overall_score"],
                "hard_score": t["score_report"]["hard_score"],
                "soft_score": t["score_report"]["soft_score"],
                "has_harness": has_harness,
                "domain": (t.get("metadata") or {}).get("domain", "restaurant"),
                "progress_rate": ((t.get("metadata") or {}).get("outbound_report") or {}).get(
                    "progress_rate"
                ),
            }
        )

    scores = [
        t["score_report"]["overall_score"]
        for t in traces
        if t["score_report"]["overall_score"] is not None
    ]
    failure_modes = {}
    for t in traces:
        diag = (t.get("metadata") or {}).get("diagnosis") or {}
        for fm in diag.get("failure_modes") or []:
            failure_modes[fm] = failure_modes.get(fm, 0) + 1
    top_failures = sorted(failure_modes.items(), key=lambda x: -x[1])[:5]

    # Local rule ratio: percentage of checks that are deterministic (rule-based)
    total_rule = 0
    total_llm = 0
    for t in traces:
        for c in t["score_report"].get("checks", []):
            if c.get("check_type") == "rule":
                total_rule += 1
            elif c.get("check_type") == "llm":
                total_llm += 1
    total_checks = total_rule + total_llm
    rule_ratio = total_rule / total_checks if total_checks else 0

    # pass^k: group by (scenario, model), compute success rate
    from collections import defaultdict

    scenario_model_scores: dict[str, list[float]] = defaultdict(list)
    for t in traces:
        sc = t["scenario"]["name"]
        model = t.get("run_metadata", {}).get("model_backend", "unknown")
        score = t["score_report"]["overall_score"]
        if score is not None:
            scenario_model_scores[f"{sc}|{model}"].append(score)

    pass_k_data = []
    for key, s_list in scenario_model_scores.items():
        if len(s_list) < 2:
            continue
        sc, model = key.split("|", 1)
        c = sum(1 for s in s_list if s >= 0.6)
        p = c / len(s_list)
        pass_k_data.append(
            {
                "scenario": sc,
                "model": model,
                "runs": len(s_list),
                "pass_rate": round(p, 3),
                "pass_k1": round(p, 3),
                "pass_k2": round(p**2, 3),
                "pass_k3": round(p**3, 3),
            }
        )
    pass_k_data.sort(key=lambda x: -x["runs"])

    # Cost estimate: use real cost_summary if available, fall back to heuristic
    total_estimated_cost = 0.0
    total_tracked_tokens = 0
    total_tracked_calls = 0
    for t in traces:
        cost_summary = t.get("run_metadata", {}).get("cost_summary", {})
        if cost_summary and cost_summary.get("total_calls", 0) > 0:
            total_estimated_cost += cost_summary.get("estimated_cost_usd", 0)
            total_tracked_tokens += cost_summary.get("total_tokens", 0)
            total_tracked_calls += cost_summary.get("total_calls", 0)
        else:
            # Fallback heuristic for traces without cost tracking
            model_cost_per_1k = {
                "claude-sonnet-4-6": 0.015,
                "claude-haiku-4-5-20251001": 0.001,
                "claude-opus-4-6": 0.075,
                "mimo-v2.5-pro": 0.002,
                "MiniMax-M2.7": 0.003,
                "LongCat-2.0-Preview": 0.002,
            }
            model = t.get("run_metadata", {}).get("model_backend", "unknown")
            turns = t["score_report"].get("conversation_length", 0)
            avg_tokens_per_turn = 800
            est_tokens = turns * avg_tokens_per_turn
            cost_rate = model_cost_per_1k.get(model, 0.01)
            total_estimated_cost += est_tokens / 1000 * cost_rate

    stats = {
        "total_traces": len(traces),
        "avg_score": sum(scores) / len(scores) if scores else 0,
        "max_score": max(scores) if scores else 0,
        "min_score": min(scores) if scores else 0,
        "top_failure_modes": [{"mode": m, "count": c} for m, c in top_failures],
        "rule_ratio": round(rule_ratio, 3),
        "rule_checks": total_rule,
        "llm_checks": total_llm,
        "pass_k": pass_k_data[:10],
        "estimated_cost_usd": round(total_estimated_cost, 4),
        "total_tracked_tokens": total_tracked_tokens,
        "total_tracked_calls": total_tracked_calls,
    }
    return {"matrix": matrix, "models": models, "scenarios": scenarios, "stats": stats}


@app.get("/api/model-comparison")
async def model_comparison():
    """Aggregate model × scenario comparison with per-dimension scores."""
    all_data: list[dict] = []
    if TRACE_DIR.exists():
        for p in TRACE_DIR.glob("*.json"):
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            all_data.append(data)

    if not all_data:
        return {"models": [], "scenarios": [], "data": [], "summary": {}}

    models_set: set[str] = set()
    scenarios_set: set[str] = set()
    rows: list[dict] = []

    for t in all_data:
        model = t.get("run_metadata", {}).get("model_backend", "unknown")
        scenario = t["scenario"]["name"]
        difficulty = t["scenario"]["difficulty"]
        sr = t["score_report"]
        meta = t.get("metadata") or {}
        ob = meta.get("outbound_report") or {}
        has_harness = bool(meta.get("harness_summary"))

        models_set.add(model)
        scenarios_set.add(scenario)

        rows.append(
            {
                "model": model,
                "scenario": scenario,
                "difficulty": difficulty,
                "overall": sr["overall_score"],
                "hard": sr["hard_score"],
                "soft": sr["soft_score"],
                "step_compliance": ob.get("step_compliance_score"),
                "branch_accuracy": ob.get("branch_accuracy_score"),
                "temporal_order": ob.get("temporal_order_score"),
                "alignment": ob.get("alignment_score"),
                "has_harness": has_harness,
                "domain": meta.get("domain", "restaurant"),
                "veto_cap": sr.get("veto_cap") or ob.get("veto_cap") or "none",
                "progress_rate": ob.get("progress_rate"),
            }
        )

    # Build per-model summary
    summary: dict[str, dict] = {}
    for model in sorted(models_set):
        model_rows = [r for r in rows if r["model"] == model]
        overall_scores = [r["overall"] for r in model_rows if r["overall"] is not None]
        hard_scores = [r["hard"] for r in model_rows if r["hard"] is not None]
        soft_scores = [r["soft"] for r in model_rows if r["soft"] is not None]
        summary[model] = {
            "avg_overall": sum(overall_scores) / len(overall_scores) if overall_scores else 0,
            "avg_hard": sum(hard_scores) / len(hard_scores) if hard_scores else 0,
            "avg_soft": sum(soft_scores) / len(soft_scores) if soft_scores else 0,
            "count": len(model_rows),
            "with_harness": sum(1 for r in model_rows if r["has_harness"]),
        }

    return {
        "models": sorted(models_set),
        "scenarios": sorted(scenarios_set),
        "data": rows,
        "summary": summary,
    }


@app.get("/api/chart-data")
async def chart_data():
    """Heatmap data: model × scenario score matrix for visualization."""
    all_data: list[dict] = []
    if TRACE_DIR.exists():
        for p in TRACE_DIR.glob("*.json"):
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            all_data.append(data)

    if not all_data:
        return {"models": [], "scenarios": [], "scores": {}}

    models_set: set[str] = set()
    scenarios_set: set[str] = set()
    # Accumulate scores per model|scenario for averaging when there are multiple runs
    from collections import defaultdict

    score_accum: dict[str, list[float]] = defaultdict(list)

    for t in all_data:
        model = t.get("run_metadata", {}).get("model_backend", "unknown")
        scenario = t["scenario"]["name"]
        score = t["score_report"]["overall_score"]
        models_set.add(model)
        scenarios_set.add(scenario)
        if score is not None:
            score_accum[f"{model}|{scenario}"].append(score)

    scores: dict[str, float] = {}
    for key, vals in score_accum.items():
        scores[key] = round(sum(vals) / len(vals) * 100, 1)

    return {
        "models": sorted(models_set),
        "scenarios": sorted(scenarios_set),
        "scores": scores,
    }


@app.get("/api/coverage/{scenario_id}")
async def coverage_detail(scenario_id: str):
    """Return coverage stats + gap list for a specific scenario."""
    from coverage_guided import analyze_coverage_gaps
    from models_outbound import OutboundScenario, OutboundScoreReport

    # Collect traces for this scenario
    reports: list[OutboundScoreReport] = []
    scenario_data = None
    if TRACE_DIR.exists():
        for p in TRACE_DIR.glob("*.json"):
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
                sc = data.get("scenario", {})
                if sc.get("id") == scenario_id or sc.get("name") == scenario_id:
                    if scenario_data is None:
                        scenario_data = sc
                    ob = (data.get("metadata") or {}).get("outbound_report")
                    if ob:
                        reports.append(OutboundScoreReport(**ob))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    if scenario_data is None:
        # Try loading from scenario files
        scenario_dir = Path("scenarios/outbound")
        if scenario_dir.exists():
            for p in scenario_dir.glob("*.json"):
                try:
                    with open(p, encoding="utf-8") as f:
                        sc_data = json.load(f)
                    if sc_data.get("id") == scenario_id or sc_data.get("name") == scenario_id:
                        scenario_data = sc_data
                        break
                except (json.JSONDecodeError, KeyError):
                    continue

    if scenario_data is None:
        return {"error": f"scenario {scenario_id} not found"}

    try:
        scenario = OutboundScenario(**scenario_data)
    except Exception as e:
        return {"error": f"failed to parse scenario: {e!s}"}

    gap_report = analyze_coverage_gaps(scenario, reports)
    return {
        "scenario_id": gap_report.scenario_id,
        "scenario_name": scenario.name,
        "current_coverage": gap_report.current_coverage,
        "gap_count": len(gap_report.gaps),
        "gap_count_by_dimension": gap_report.gap_count_by_dimension,
        "suggested_scenario_count": gap_report.suggested_scenario_count,
        "gaps": [
            {
                "dimension": g.dimension,
                "item_id": g.item_id,
                "description": g.description,
                "suggested_persona": g.suggested_persona,
                "suggested_mutation": g.suggested_mutation,
            }
            for g in gap_report.gaps
        ],
        "report_count": len(reports),
    }


@app.get("/api/traces/{trace_id}")
async def get_trace(trace_id: str):
    for p in TRACE_DIR.glob("*.json"):
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if data["id"] == trace_id:
            return data
    return {"error": "not found"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8765)
