"""Run remaining 11 traces for blind labeling pilot."""

import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

AGENT_EVAL = Path(__file__).parent.parent
TRACE_DIR = AGENT_EVAL / "data" / "calibration" / "blind_pilot" / "traces_v2"

SCENARIOS = [
    # merchant (4) — all new
    "scenarios/outbound/merchant_dropout_retention.json",
    "scenarios/outbound/merchant_feature_promotion.json",
    "scenarios/outbound/course_livestream_upgrade.json",
    "scenarios/outbound/merchant_violation_warning.json",
    # edge cases (4) — all new
    "scenarios/outbound/stress_test_extreme.json",
    "scenarios/outbound/compliance_conflict.json",
    "scenarios/outbound/user_flip_flop.json",
    "scenarios/outbound/delay_notify_difficult.json",
    # adversarial (2) — all new
    "scenarios/outbound/adversarial_prompt_injection.json",
    # simple (1) — new
    "scenarios/outbound/simple_satisfaction_survey.json",
]

MODEL = "sonnet"
WORKERS = 4
lock = threading.Lock()
done_count = 0


def run_one(idx, scenario):
    global done_count
    scenario_name = Path(scenario).stem
    t0 = time.time()
    cmd = [
        sys.executable,
        "run_outbound.py",
        scenario,
        "--model",
        MODEL,
        "--trace-dir",
        str(TRACE_DIR),
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(AGENT_EVAL),
            capture_output=True,
            text=True,
            timeout=900,
        )
        elapsed = time.time() - t0
        with lock:
            done_count += 1
            status = "OK" if result.returncode == 0 else "FAIL"
            print(
                f"  [{done_count:2d}/11] {status:4s} {scenario_name:40s} ({elapsed:.0f}s)",
                flush=True,
            )
            if result.returncode != 0 and result.stderr:
                print(f"         {result.stderr[-150:]}", flush=True)
        return (idx, scenario_name, result.returncode == 0)
    except subprocess.TimeoutExpired:
        with lock:
            done_count += 1
            print(f"  [{done_count:2d}/11] TIMEOUT {scenario_name}", flush=True)
        return (idx, scenario_name, False)
    except Exception as e:
        with lock:
            done_count += 1
            print(f"  [{done_count:2d}/11] ERROR {scenario_name}: {e}", flush=True)
        return (idx, scenario_name, False)


def main():
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"=== Remaining batch: 11 traces, {WORKERS} workers, model={MODEL} ===\n")
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(run_one, i, s): i for i, s in enumerate(SCENARIOS, 1)}
        for f in as_completed(futures):
            results.append(f.result())
    ok = sum(1 for r in results if r[2])
    print(f"\n=== Done: {ok}/11 success, total {time.time() - t0:.0f}s ===")


if __name__ == "__main__":
    main()
