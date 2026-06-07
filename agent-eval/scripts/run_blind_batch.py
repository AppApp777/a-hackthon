"""Batch-run 24 traces for blind labeling pilot with fixed simulator — PARALLEL."""

import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

AGENT_EVAL = Path(__file__).parent.parent
TRACE_DIR = AGENT_EVAL / "data" / "calibration" / "blind_pilot" / "traces_v2"

SCENARIOS = [
    "scenarios/outbound/after_sales_complaint.json",
    "scenarios/outbound/after_sales_complaint.json",
    "scenarios/outbound/multi_issue_combo.json",
    "scenarios/outbound/multi_issue_combo.json",
    "scenarios/outbound/rider_feimaotui_notify.json",
    "scenarios/outbound/rider_feimaotui_notify.json",
    "scenarios/outbound/rider_contract_warning.json",
    "scenarios/outbound/rider_safety_incident.json",
    "scenarios/outbound/delivery_confirm_basic.json",
    "scenarios/outbound/delivery_confirm_basic.json",
    "scenarios/outbound/refund_over_budget.json",
    "scenarios/outbound/refund_over_budget.json",
    "scenarios/outbound/merchant_dropout_retention.json",
    "scenarios/outbound/merchant_feature_promotion.json",
    "scenarios/outbound/course_livestream_upgrade.json",
    "scenarios/outbound/merchant_violation_warning.json",
    "scenarios/outbound/stress_test_extreme.json",
    "scenarios/outbound/compliance_conflict.json",
    "scenarios/outbound/user_flip_flop.json",
    "scenarios/outbound/delay_notify_difficult.json",
    "scenarios/outbound/adversarial_prompt_injection.json",
    "scenarios/outbound/adversarial_social_engineering.json",
    "scenarios/outbound/simple_satisfaction_survey.json",
    "scenarios/outbound/system_error_fallback.json",
]

MODEL = "sonnet"
WORKERS = 6
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
            if result.returncode == 0:
                print(
                    f"  [{done_count:2d}/24] OK  {scenario_name:40s} ({elapsed:.0f}s)", flush=True
                )
                return (idx, scenario_name, True, "")
            else:
                err = result.stderr[-200:] if result.stderr else "no stderr"
                print(
                    f"  [{done_count:2d}/24] FAIL {scenario_name:40s} ({elapsed:.0f}s)", flush=True
                )
                return (idx, scenario_name, False, err)
    except subprocess.TimeoutExpired:
        with lock:
            done_count += 1
            print(f"  [{done_count:2d}/24] TIMEOUT {scenario_name}", flush=True)
        return (idx, scenario_name, False, "timeout")
    except Exception as e:
        with lock:
            done_count += 1
            print(f"  [{done_count:2d}/24] ERROR {scenario_name}: {e}", flush=True)
        return (idx, scenario_name, False, str(e))


def main():
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"=== Parallel batch: 24 traces, {WORKERS} workers, model={MODEL} ===")
    print(f"Output: {TRACE_DIR}\n")
    t_start = time.time()

    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(run_one, i, s): i for i, s in enumerate(SCENARIOS, 1)}
        for f in as_completed(futures):
            results.append(f.result())

    elapsed_total = time.time() - t_start
    success = sum(1 for r in results if r[2])
    failed = [(r[0], r[1], r[3]) for r in results if not r[2]]

    print(f"\n=== Done: {success}/24 success, {len(failed)} failed, total {elapsed_total:.0f}s ===")
    if failed:
        print("\nFailed:")
        for idx, name, err in sorted(failed):
            print(f"  [{idx}] {name}: {err[:120]}")


if __name__ == "__main__":
    main()
