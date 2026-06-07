"""Subprocess worker for sandboxed agent — runs in isolation from orchestrator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def main():
    from baseline_agent_outbound import OutboundBaselineAgent
    from models import Conversation, ToolCall
    from models_outbound import OutboundScenario

    agent = None
    tool_call_buffer = []

    def dummy_executor(tool_name: str, args: dict) -> ToolCall:
        """Dummy executor — records requests, actual execution happens in orchestrator."""
        tc = ToolCall(tool_name=tool_name, arguments=args, error="[SANDBOX_PENDING]")
        tool_call_buffer.append({"tool": tool_name, "args": args})
        return tc

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _respond({"error": "invalid JSON"})
            continue

        msg_type = msg.get("type")

        if msg_type == "init":
            scenario = OutboundScenario(**json.loads(msg["scenario"]))
            tool_defs = msg["tool_defs"]
            model = msg.get("model")
            agent = OutboundBaselineAgent(
                scenario=scenario,
                tool_executor=dummy_executor,
                tool_defs=tool_defs,
                model=model,
            )
            _respond({"status": "ready"})

        elif msg_type == "initiate":
            tool_call_buffer.clear()
            text, _ = agent.initiate_call()
            _respond({"text": text, "tool_requests": tool_call_buffer[:]})

        elif msg_type == "respond":
            tool_call_buffer.clear()
            conv = Conversation(**msg["conversation"])
            text, _ = agent.respond(conv)
            _respond({"text": text, "tool_requests": tool_call_buffer[:]})

        elif msg_type == "shutdown":
            break


def _respond(data: dict):
    sys.stdout.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
