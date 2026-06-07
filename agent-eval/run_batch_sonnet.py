"""Batch run scenarios with Claude Sonnet using OAuth token from Claude Code.

Usage (关闭 Claude Code 后在命令行跑，避免限频):
    cd agent-eval
    python run_batch_sonnet.py

会自动读取 ~/.claude/.credentials.json 的 OAuth token。
"""

import json
import subprocess
import sys
import time
from pathlib import Path

CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
SCENARIOS_DIR = Path("scenarios/outbound")
MODEL = "claude-sonnet-4-20250514"


def get_auth_token() -> str:
    creds = json.loads(CREDENTIALS_PATH.read_text())
    return creds["claudeAiOauth"]["accessToken"]


def run_scenario(scenario_path: str, model: str, auth_token: str) -> bool:
    import os

    env = dict(os.environ)
    env["ANTHROPIC_AUTH_TOKEN"] = auth_token
    env["PYTHONPATH"] = "."
    cmd = [
        sys.executable,
        "run_outbound.py",
        scenario_path,
        "--model",
        model,
        "--no-llm-judge",
    ]
    try:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180)
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "轨迹ID" in line or "综合得分" in line:
                    print(f"  {line.strip()}")
            return True
        else:
            last_lines = result.stderr.strip().split("\n")[-3:]
            print(f"  FAIL: {' | '.join(last_lines)}")
            return False
    except subprocess.TimeoutExpired:
        print("  TIMEOUT")
        return False


def main():
    token = get_auth_token()
    print(f"Auth token: {token[:12]}...")

    scenarios = sorted(SCENARIOS_DIR.glob("*.json"))
    easy_medium = []
    for s in scenarios:
        data = json.loads(s.read_text(encoding="utf-8"))
        if data.get("difficulty") in ("easy", "medium"):
            easy_medium.append(s)

    print(f"\nFound {len(easy_medium)} easy/medium scenarios to run with {MODEL}\n")

    success = 0
    for i, s in enumerate(easy_medium, 1):
        name = s.stem
        print(f"[{i}/{len(easy_medium)}] {name}...")
        if run_scenario(str(s), MODEL, token):
            success += 1
        time.sleep(2)

    print(f"\nDone: {success}/{len(easy_medium)} succeeded")


if __name__ == "__main__":
    main()
