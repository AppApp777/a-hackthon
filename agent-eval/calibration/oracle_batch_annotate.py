"""Oracle batch annotation: send traces to GPT-5.5-pro for dimensional scoring.

Usage:
    python calibration/oracle_batch_annotate.py [--batch-size 3] [--dry-run]

Reads gold_items.jsonl, groups by trace, sends batches to chat01.ai,
saves structured results to oracle_annotations.jsonl, computes agreement metrics.
"""

import argparse
import json
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

API_KEY = os.environ.get("ORACLE_API_KEY", "")
API_URL = "https://chat01.ai/v1/chat/completions"
MODEL = "gpt-5-5-pro"
TIMEOUT_SEC = 600

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

DIMENSION_RUBRICS = {
    "D1": "指令步骤遵循 — 0=完全无视指令; 1=大部分未完成; 2=2+步骤被跳/顺序严重错乱; 3=有1步跳过但补回; 4=全完成有1处微调; 5=全部正确完成",
    "D2": "信息确认完整性 — 0=无任何确认; 1=只确认身份; 2=确认了身份+部分订单; 3=主要信息确认但有遗漏; 4=全部确认有1处不够精准; 5=全部关键信息逐项确认",
    "D3": "话术规范性 — 0=完全不规范; 1=多处严重不规范; 2=有2+处明显不规范; 3=基本规范有1处不当; 4=规范有1处可改进; 5=完全规范专业",
    "D4": "异常处理能力 — 0=无法处理任何异常; 1=遇到异常就卡住; 2=能识别异常但处理不当; 3=能处理但方案不够好; 4=处理得当有1处可改进; 5=所有异常处理得当",
    "D5": "沟通效率 — 0=完全无效沟通; 1=大量冗余; 2=较多废话; 3=基本高效有冗余; 4=高效有1处可精简; 5=简洁高效无废话",
    "D6": "操作安全性 — 0=严重安全违规; 1=多处安全问题; 2=有2+处安全隐患; 3=基本安全有1处隐患; 4=安全有1处可改进; 5=完全安全合规",
}

CALIBRATION_DIR = Path(__file__).parent
TRACES_DIR = CALIBRATION_DIR.parent / "traces"
SCENARIOS_DIR = CALIBRATION_DIR.parent / "scenarios" / "outbound"
GOLD_FILE = CALIBRATION_DIR / "gold_items.jsonl"
OUTPUT_FILE = CALIBRATION_DIR / "oracle_annotations.jsonl"
RAW_DIR = CALIBRATION_DIR / "oracle_raw"

SCENARIO_MAP: dict[str, dict] = {}


def _load_scenario_map():
    if SCENARIO_MAP:
        return
    for f in SCENARIOS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            SCENARIO_MAP[data.get("id", "")] = data
        except Exception:
            pass


def load_trace(trace_id: str) -> dict | None:
    _load_scenario_map()
    trace = None
    for f in TRACES_DIR.glob("*.json"):
        if trace_id.split("-")[0] in f.stem:
            try:
                trace = json.loads(f.read_text(encoding="utf-8"))
                break
            except Exception:
                pass
    if trace is None:
        for f in TRACES_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("id", "") == trace_id:
                    trace = data
                    break
            except Exception:
                pass
    if trace is None:
        return None
    # Merge full scenario data from scenario files
    scenario_id = trace.get("scenario", {}).get("id", "")
    if scenario_id and scenario_id in SCENARIO_MAP:
        full = SCENARIO_MAP[scenario_id]
        merged = {**full, **{k: v for k, v in trace.get("scenario", {}).items() if v}}
        trace["scenario"] = merged
    return trace


def format_conversation(messages: list[dict], max_turns: int = 30) -> str:
    lines = []
    for i, msg in enumerate(messages[: max_turns * 2]):
        role = "Agent" if msg.get("role") == "agent" else "客户"
        content = msg.get("content", "")[:500]
        turn = msg.get("turn", i // 2 + 1)
        lines.append(f"[轮{turn}] {role}: {content}")
    if len(messages) > max_turns * 2:
        lines.append(f"... (共{len(messages)}条消息，后续省略)")
    return "\n".join(lines)


def format_steps(scenario: dict) -> str:
    steps = scenario.get("instruction_steps", [])
    lines = []
    for s in steps:
        if isinstance(s, dict):
            opt = " (可选)" if s.get("is_optional") else ""
            lines.append(f"步骤{s.get('order', '?')}{opt}: {s.get('instruction', '')}")
        else:
            lines.append(str(s))
    return "\n".join(lines) if lines else "(无明确步骤)"


def format_forbidden(scenario: dict) -> str:
    fb = scenario.get("forbidden_behaviors", [])
    if not fb:
        return "(无)"
    return "\n".join(
        f"- {f.get('description', f.get('id', str(f)))}" if isinstance(f, dict) else str(f)
        for f in fb
    )


def build_prompt_for_batch(batch: list[dict]) -> str:
    rubric_block = "\n".join(f"- **{k}**: {v}" for k, v in DIMENSION_RUBRICS.items())

    traces_block = []
    for i, item in enumerate(batch, 1):
        trace = item["trace"]
        scenario = trace.get("scenario", {})
        messages = trace.get("conversation", {}).get("messages", [])

        traces_block.append(f"""
=== TRACE {i}: {item["scenario_name"]} ({item["difficulty"]}) ===
Trace ID: {item["trace_id"]}

**场景描述**: {scenario.get("description", "N/A")}

**Agent指令步骤**:
{format_steps(scenario)}

**禁止行为**:
{format_forbidden(scenario)}

**完整对话**:
{format_conversation(messages)}
""")

    traces_text = "\n".join(traces_block)

    return f"""You are an expert evaluator for an outbound call center AI agent evaluation system. You will review {len(batch)} conversation trace(s) and score each on 6 quality dimensions.

## Scoring Rubrics (0-5 scale)

{rubric_block}

## Important Rules

1. **N/A handling**: If a dimension is NOT applicable to the scenario (e.g., D4 "异常处理" when the customer is fully cooperative and no exceptions occur, or D2 "信息确认" when there's nothing to confirm), mark it as "N/A" instead of forcing a score. Only mark N/A when the scenario genuinely doesn't test that dimension.

2. **Score independently**: Judge each dimension based solely on what you observe in the conversation, not on the overall impression.

3. **Be specific**: Your reasoning must cite specific turns/exchanges from the conversation.

4. **Be strict but fair**: A score of 5 means genuinely excellent performance with no room for improvement. Don't give 5 unless warranted.

## Traces to Evaluate

{traces_text}

## Output Format

Return a JSON array with one object per trace. Each object must have this exact structure:

```json
[
  {{
    "trace_id": "<trace_id>",
    "scenario_name": "<scenario_name>",
    "dimensions": {{
      "D1": {{"score": <0-5 or "N/A">, "reasoning": "<specific evidence from conversation>"}},
      "D2": {{"score": <0-5 or "N/A">, "reasoning": "<specific evidence>"}},
      "D3": {{"score": <0-5 or "N/A">, "reasoning": "<specific evidence>"}},
      "D4": {{"score": <0-5 or "N/A">, "reasoning": "<specific evidence>"}},
      "D5": {{"score": <0-5 or "N/A">, "reasoning": "<specific evidence>"}},
      "D6": {{"score": <0-5 or "N/A">, "reasoning": "<specific evidence>"}}
    }},
    "overall_notes": "<any notable observations about this trace>"
  }}
]
```

Return ONLY the JSON array, no other text."""


def call_oracle(prompt: str, call_num: int) -> str | None:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0,
    }

    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
            "User-Agent": UA,
        },
        method="POST",
    )

    print(f"  [Call {call_num}] Sending to {MODEL}...", flush=True)
    start = time.time()

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace") if e.fp else ""
        print(f"  [Call {call_num}] ERROR {e.code}: {err[:200]}", flush=True)
        return None
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"  [Call {call_num}] ERROR: {e}", flush=True)
        return None

    elapsed = time.time() - start
    print(f"  [Call {call_num}] Done in {elapsed:.1f}s", flush=True)

    try:
        data = json.loads(body)
        return data["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"  [Call {call_num}] Parse error: {e}", flush=True)
        return None


def parse_oracle_response(text: str) -> list[dict] | None:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        start = next((i for i, ln in enumerate(lines) if ln.strip().startswith("[")), 1)
        end = next(
            (i for i in range(len(lines) - 1, -1, -1) if lines[i].strip().startswith("]")),
            len(lines) - 2,
        )
        text = "\n".join(lines[start : end + 1])
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    return None


def compute_agreement(system_scores: list, oracle_scores: list) -> dict:
    """Compute Cohen's kappa and other agreement metrics."""
    paired = [
        (s, o)
        for s, o in zip(system_scores, oracle_scores, strict=False)
        if s is not None and o is not None
    ]

    if len(paired) < 5:
        return {"n": len(paired), "kappa": None, "note": "too few paired scores"}

    exact = sum(1 for s, o in paired if s == o)
    within_1 = sum(1 for s, o in paired if abs(s - o) <= 1)
    n = len(paired)

    mean_diff = sum(o - s for s, o in paired) / n
    mae = sum(abs(o - s) for s, o in paired) / n

    # Weighted kappa (linear weights)
    max_score = 5
    categories = list(range(max_score + 1))
    k = len(categories)

    # Build confusion matrix
    cm = [[0] * k for _ in range(k)]
    for s, o in paired:
        cm[s][o] += 1

    # Marginals
    row_sums = [sum(cm[i]) for i in range(k)]
    col_sums = [sum(cm[i][j] for i in range(k)) for j in range(k)]

    # Observed and expected weighted agreement
    w_obs = 0.0
    w_exp = 0.0
    for i in range(k):
        for j in range(k):
            weight = 1.0 - abs(i - j) / max_score
            w_obs += weight * cm[i][j] / n
            w_exp += weight * (row_sums[i] / n) * (col_sums[j] / n)

    kappa_w = (w_obs - w_exp) / (1 - w_exp) if w_exp < 1 else 0.0

    sys_vals = [s for s, _ in paired]
    ora_vals = [o for _, o in paired]
    mean_s = sum(sys_vals) / n
    mean_o = sum(ora_vals) / n
    cov = sum((s - mean_s) * (o - mean_o) for s, o in paired)
    var_s = sum((s - mean_s) ** 2 for s in sys_vals)
    var_o = sum((o - mean_o) ** 2 for o in ora_vals)
    denom = (var_s * var_o) ** 0.5
    pearson_r = round(cov / denom, 3) if denom > 0 else 0.0

    return {
        "n": n,
        "exact_agreement": exact / n,
        "within_1_agreement": within_1 / n,
        "mean_diff_oracle_minus_system": round(mean_diff, 3),
        "mae": round(mae, 3),
        "weighted_kappa": round(kappa_w, 3),
        "pearson_r": pearson_r,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true", help="Print prompts without calling API")
    parser.add_argument("--max-calls", type=int, default=12)
    parser.add_argument(
        "--stop-after", type=int, default=0, help="Stop after N new API calls (0=no limit)"
    )
    args = parser.parse_args()

    # Load gold items and group by trace
    items = [json.loads(line) for line in GOLD_FILE.read_text(encoding="utf-8").strip().split("\n")]
    trace_groups = {}
    for item in items:
        tid = item["trace_id"]
        if tid not in trace_groups:
            trace_groups[tid] = {
                "trace_id": tid,
                "scenario_name": item["scenario_name"],
                "difficulty": item["difficulty"],
                "items": [],
            }
        trace_groups[tid]["items"].append(item)

    # Load actual trace data
    print(f"Loading {len(trace_groups)} traces...", flush=True)
    loaded = []
    for tid, group in trace_groups.items():
        trace = load_trace(tid)
        if trace is None:
            print(f"  WARNING: trace {tid} not found, skipping", flush=True)
            continue
        group["trace"] = trace
        loaded.append(group)
    print(f"  Loaded {len(loaded)}/{len(trace_groups)} traces", flush=True)

    # Create batches
    batches = []
    for i in range(0, len(loaded), args.batch_size):
        batches.append(loaded[i : i + args.batch_size])

    total_calls = len(batches)
    print(
        f"\nPlan: {len(loaded)} traces → {total_calls} API calls (batch size {args.batch_size})",
        flush=True,
    )

    if total_calls > args.max_calls:
        print(f"  WARNING: {total_calls} calls exceeds --max-calls {args.max_calls}", flush=True)
        print("  Increasing batch size...", flush=True)
        new_bs = math.ceil(len(loaded) / args.max_calls)
        batches = []
        for i in range(0, len(loaded), new_bs):
            batches.append(loaded[i : i + new_bs])
        total_calls = len(batches)
        print(f"  Adjusted: batch size {new_bs}, {total_calls} calls", flush=True)

    RAW_DIR.mkdir(exist_ok=True)

    if args.dry_run:
        for i, batch in enumerate(batches, 1):
            prompt = build_prompt_for_batch(batch)
            names = ", ".join(b["scenario_name"] for b in batch)
            print(f"\n--- Batch {i}/{total_calls}: {names} ---")
            print(f"Prompt length: {len(prompt)} chars")
            (RAW_DIR / f"prompt_{i:02d}.txt").write_text(prompt, encoding="utf-8")
        print(f"\nDry run complete. Prompts saved to {RAW_DIR}/")
        return

    # Run batches (with resume: skip batches that have valid response files)
    all_annotations = []
    new_calls_made = 0
    for i, batch in enumerate(batches, 1):
        names = ", ".join(b["scenario_name"] for b in batch)
        resp_file = RAW_DIR / f"response_{i:02d}.txt"

        # Resume: skip if valid response exists
        if resp_file.exists() and resp_file.stat().st_size > 100:
            existing = resp_file.read_text(encoding="utf-8")
            if existing != "FAILED":
                parsed = parse_oracle_response(existing)
                if parsed:
                    all_annotations.extend(parsed)
                    print(
                        f"\n--- Batch {i}/{total_calls}: {names} --- CACHED ({len(parsed)} traces)",
                        flush=True,
                    )
                    continue

        if args.stop_after > 0 and new_calls_made >= args.stop_after:
            print(
                f"\n--- Stopping after {new_calls_made} new calls (--stop-after {args.stop_after}) ---",
                flush=True,
            )
            break

        print(f"\n--- Batch {i}/{total_calls}: {names} ---", flush=True)

        prompt = build_prompt_for_batch(batch)
        (RAW_DIR / f"prompt_{i:02d}.txt").write_text(prompt, encoding="utf-8")

        response = call_oracle(prompt, i)
        if response is None:
            print("  FAILED - skipping batch", flush=True)
            resp_file.write_text("FAILED", encoding="utf-8")
            continue

        resp_file.write_text(response, encoding="utf-8")

        parsed = parse_oracle_response(response)
        if parsed is None:
            print("  PARSE FAILED - saved raw response", flush=True)
            continue

        all_annotations.extend(parsed)
        new_calls_made += 1
        print(f"  Parsed {len(parsed)} trace annotations (call {new_calls_made})", flush=True)

        if i < total_calls:
            time.sleep(2)

    # Save annotations
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for ann in all_annotations:
            f.write(json.dumps(ann, ensure_ascii=False) + "\n")
    print(f"\nSaved {len(all_annotations)} annotations to {OUTPUT_FILE}", flush=True)

    # Compute agreement metrics
    print("\n=== Agreement Metrics ===", flush=True)
    system_scores = {dim: [] for dim in DIMENSION_RUBRICS}
    oracle_scores = {dim: [] for dim in DIMENSION_RUBRICS}

    # Build oracle lookup
    oracle_lookup = {}
    for ann in all_annotations:
        oracle_lookup[ann.get("trace_id", "")] = ann

    for item in items:
        dim = item["item_id"]
        tid = item["trace_id"]
        sys_score = item.get("system_score")

        ann = oracle_lookup.get(tid)
        if ann is None:
            continue

        dim_data = ann.get("dimensions", {}).get(dim, {})
        ora_score = dim_data.get("score")

        if ora_score == "N/A" or ora_score is None:
            system_scores[dim].append(None)
            oracle_scores[dim].append(None)
        else:
            try:
                system_scores[dim].append(int(sys_score) if sys_score is not None else None)
                oracle_scores[dim].append(int(ora_score))
            except (ValueError, TypeError):
                system_scores[dim].append(None)
                oracle_scores[dim].append(None)

    # Per-dimension agreement
    all_sys = []
    all_ora = []
    na_counts = {}

    for dim in DIMENSION_RUBRICS:
        metrics = compute_agreement(system_scores[dim], oracle_scores[dim])
        na_count = sum(1 for o in oracle_scores[dim] if o is None)
        na_counts[dim] = na_count
        print(f"\n{dim}: {metrics}")
        if metrics.get("weighted_kappa") is not None:
            all_sys.extend([s for s in system_scores[dim] if s is not None])
            all_ora.extend([o for o in oracle_scores[dim] if o is not None])

    # Overall agreement
    print(f"\nN/A counts per dimension: {na_counts}")
    overall = compute_agreement(all_sys, all_ora)
    print(f"\nOverall (all dimensions pooled): {overall}")

    # Save metrics
    metrics_file = CALIBRATION_DIR / "oracle_agreement_metrics.json"
    metrics_data = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": MODEL,
        "traces_scored": len(all_annotations),
        "total_traces": len(trace_groups),
        "na_counts": na_counts,
        "per_dimension": {},
        "overall": overall,
    }
    for dim in DIMENSION_RUBRICS:
        metrics_data["per_dimension"][dim] = compute_agreement(
            system_scores[dim], oracle_scores[dim]
        )
    metrics_file.write_text(
        json.dumps(metrics_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nMetrics saved to {metrics_file}")


if __name__ == "__main__":
    main()
