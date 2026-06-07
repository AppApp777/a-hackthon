"""Generate a self-contained HTML report from evaluation traces.

Usage:
    python generate_report.py [--output report.html] [--top N]
    python generate_report.py --ids outbound_528f852f,outbound_0abb815d
    python generate_report.py --best 5   # auto-pick diverse representative traces

The output HTML file can be opened directly in any browser — no Python or server needed.
Evaluators can double-click the file to see the full dashboard with all trace data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

TRACE_DIR = Path(__file__).parent / "traces"
STATIC_DIR = Path(__file__).parent / "static"


def _load_all_traces() -> list[dict]:
    """Load all valid traces from the traces directory (non-recursive)."""
    traces = []
    if not TRACE_DIR.exists():
        return traces
    for p in sorted(TRACE_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.name.startswith("_"):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if "id" not in data or "score_report" not in data:
                continue
            data["_source_file"] = p.stem  # for --ids matching by filename
            traces.append(data)
        except (json.JSONDecodeError, KeyError):
            continue
    return traces


def load_traces(
    top: int | None = None, ids: list[str] | None = None, best: int | None = None
) -> list[dict]:
    all_traces = _load_all_traces()

    if ids:
        # Match by id or filename prefix
        id_set = set(ids)
        matched = []
        for t in all_traces:
            tid = t["id"]
            fname = t.get("_source_file", "")
            for wanted in id_set:
                if (
                    tid.startswith(wanted)
                    or wanted in tid
                    or fname.startswith(wanted)
                    or wanted in fname
                ):
                    matched.append(t)
                    break
        return matched

    if best:
        # Smart selection: pick diverse traces across score ranges, scenarios, models
        return _pick_best(all_traces, best)

    if top:
        all_traces = all_traces[:top]
    return all_traces


def _pick_best(traces: list[dict], n: int) -> list[dict]:
    """Pick n diverse representative traces covering different score bands,
    scenarios, and models. Prefer non-zero scores."""
    # Filter out 0-score traces (usually broken/meta-eval artifacts)
    valid = [t for t in traces if t["score_report"]["overall_score"] > 0]
    if not valid:
        valid = traces

    # Sort by score descending
    valid.sort(key=lambda t: t["score_report"]["overall_score"], reverse=True)

    # Bucket into score bands: high(>=0.7), mid(0.4-0.7), low(<0.4)
    high = [t for t in valid if t["score_report"]["overall_score"] >= 0.7]
    mid = [t for t in valid if 0.4 <= t["score_report"]["overall_score"] < 0.7]
    low = [t for t in valid if t["score_report"]["overall_score"] < 0.4]

    selected: list[dict] = []
    seen_scenarios: set[str] = set()
    seen_models: set[str] = set()

    def pick_from(bucket: list[dict]) -> dict | None:
        # Prefer unique scenario+model combos
        for t in bucket:
            sc = t["scenario"]["name"]
            mdl = t.get("run_metadata", {}).get("model_backend", "")
            if sc not in seen_scenarios or mdl not in seen_models:
                seen_scenarios.add(sc)
                seen_models.add(mdl)
                bucket.remove(t)
                return t
        # Fallback: just take the first
        return bucket.pop(0) if bucket else None

    # Distribute picks across bands
    for bucket in [high, mid, low, high, mid, low, high, mid, low]:
        if len(selected) >= n:
            break
        t = pick_from(bucket)
        if t:
            selected.append(t)

    # If still short, fill from any remaining
    remaining = [t for t in valid if t not in selected]
    while len(selected) < n and remaining:
        selected.append(remaining.pop(0))

    return selected


def build_trace_list(traces: list[dict]) -> list[dict]:
    result = []
    for data in traces:
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
            "file": "",
            "domain": domain,
            "model": data.get("run_metadata", {}).get("model_backend", ""),
            "agent_type": data.get("run_metadata", {}).get("agent_type", ""),
        }
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
        result.append(entry)
    return result


def build_overview(traces: list[dict]) -> dict:
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
    failure_modes: dict[str, int] = {}
    for t in traces:
        diag = (t.get("metadata") or {}).get("diagnosis") or {}
        for fm in diag.get("failure_modes") or []:
            failure_modes[fm] = failure_modes.get(fm, 0) + 1
    top_failures = sorted(failure_modes.items(), key=lambda x: -x[1])[:5]

    stats = {
        "total_traces": len(traces),
        "avg_score": sum(scores) / len(scores) if scores else 0,
        "max_score": max(scores) if scores else 0,
        "min_score": min(scores) if scores else 0,
        "top_failure_modes": [{"mode": m, "count": c} for m, c in top_failures],
    }
    return {"matrix": matrix, "models": models, "scenarios": scenarios, "stats": stats}


def build_full_traces_map(traces: list[dict]) -> dict[str, dict]:
    return {t["id"]: t for t in traces}


def generate_html(traces: list[dict]) -> str:
    html_template = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    trace_list_data = build_trace_list(traces)
    overview_data = build_overview(traces)
    full_traces = build_full_traces_map(traces)

    embedded_data = f"""
<script>
// ── Embedded data (self-contained report, no server needed) ──
const EMBEDDED_MODE = true;
const EMBEDDED_TRACE_LIST = {json.dumps(trace_list_data, ensure_ascii=False)};
const EMBEDDED_OVERVIEW = {json.dumps(overview_data, ensure_ascii=False)};
const EMBEDDED_TRACES = {json.dumps(full_traces, ensure_ascii=False)};
</script>
"""

    override_script = """
<script>
// ── Override fetch-based functions for embedded mode ──
loadTraces = async function() {
  allTraces = EMBEDDED_TRACE_LIST;
  renderTraceList();
  if (!currentTrace && !compareMode) showOverview();
};

showOverview = async function() {
  document.getElementById('traceTitle').textContent = '总览';
  document.getElementById('tabBar').innerHTML = '<div class="tab active">全局概览</div>';
  document.getElementById('mainContent').innerHTML = renderOverview(EMBEDDED_OVERVIEW);
};

loadTrace = async function(id) {
  document.querySelectorAll('.trace-item').forEach(el => el.classList.remove('active'));
  const item = document.getElementById('item-' + id);
  if (item) item.classList.add('active');
  currentTrace = EMBEDDED_TRACES[id];
  document.getElementById('traceTitle').textContent = currentTrace.scenario.name;
  buildTabs();
  currentTab = 'conversation';
  renderTab();
};

launchCompare = async function() {
  if (compareSelected.length !== 2) return;
  const [idA, idB] = compareSelected;
  compareTraces = [EMBEDDED_TRACES[idA], EMBEDDED_TRACES[idB]];
  document.getElementById('traceTitle').textContent = '模型对比';
  document.getElementById('tabBar').innerHTML = [
    {id:'compare_scores', label:'分数对比'},
    {id:'compare_checks', label:'检查项对比'},
    {id:'compare_conv', label:'对话对比'},
  ].map(t => `<div class="tab${t.id==='compare_scores'?' active':''}" data-tab="${t.id}" onclick="switchCompareTab('${t.id}')">${t.label}</div>`).join('');
  renderCompareScores();
};

// Re-trigger initial load with embedded data
loadTraces();
</script>
"""

    result = html_template

    # Remove the original loadTraces() call at the end of the script
    result = result.replace("loadTraces();\n</script>", "</script>")

    # Insert embedded data + override script before </body>
    result = result.replace("</body>", embedded_data + override_script + "\n</body>")

    # Update the title
    result = result.replace(
        "<title>Agent 对话评测系统</title>",
        "<title>Agent 对话评测系统 — 离线报告</title>",
    )

    # Add a generation timestamp badge
    from datetime import datetime

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    badge = f'<div style="position:fixed;bottom:8px;right:12px;font-size:10px;color:#555;z-index:9999;">离线报告 · {ts} · {len(traces)} 条 trace</div>'
    result = result.replace("</body>", badge + "\n</body>")

    return result


def main():
    parser = argparse.ArgumentParser(description="生成自包含 HTML 评测报告")
    parser.add_argument("--output", "-o", default="report.html", help="输出文件路径")
    parser.add_argument("--top", "-n", type=int, default=None, help="只包含最近 N 条 trace")
    parser.add_argument(
        "--ids",
        type=str,
        default=None,
        help="逗号分隔的 trace ID（前缀匹配），如 outbound_528f852f,96bb49e1",
    )
    parser.add_argument(
        "--best",
        type=int,
        default=None,
        help="智能选取 N 条代表性 trace（自动覆盖不同分数段/场景/模型）",
    )
    args = parser.parse_args()

    id_list = args.ids.split(",") if args.ids else None
    traces = load_traces(top=args.top, ids=id_list, best=args.best)
    if not traces:
        print("未找到 trace 文件，请先运行评测。")
        return

    html = generate_html(traces)
    out_path = Path(args.output)
    out_path.write_text(html, encoding="utf-8")
    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"✓ 已生成离线报告: {out_path.resolve()}")
    print(f"  包含 {len(traces)} 条 trace，文件大小 {size_mb:.1f} MB")
    print("  直接双击即可在浏览器中查看，无需启动服务器。")


if __name__ == "__main__":
    main()
