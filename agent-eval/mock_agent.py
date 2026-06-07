"""Mock agent that replays responses from a frozen trace — no API key required.

Used by ``--demo`` mode to demonstrate the full evaluation pipeline
(scoring, diagnosis, evidence verification) without calling any LLM API.
"""

from __future__ import annotations

import json
from pathlib import Path

from models import Conversation, ToolCall


class MockAgentOutbound:
    """Replays agent turns from a pre-recorded trace file."""

    def __init__(self, trace_path: str | Path):
        with open(trace_path, encoding="utf-8") as f:
            trace = json.load(f)

        self._turns: list[dict] = []
        for msg in trace.get("conversation", {}).get("messages", []):
            if msg.get("role") == "agent":
                self._turns.append(msg)

        self._cursor = 0
        self.model = f"mock-replay({Path(trace_path).stem})"

    def _next_turn(self) -> tuple[str, list[ToolCall]]:
        if self._cursor >= len(self._turns):
            return "(通话结束)", []

        turn = self._turns[self._cursor]
        self._cursor += 1

        text = turn.get("content", "")
        tool_calls = []
        for tc in turn.get("tool_calls", []):
            tool_calls.append(
                ToolCall(
                    tool_name=tc.get("tool_name", ""),
                    arguments=tc.get("arguments", {}),
                    result=tc.get("result"),
                    error=tc.get("error"),
                    latency_ms=tc.get("latency_ms", 0),
                    fault_injected=tc.get("fault_injected", False),
                    source=tc.get("source", "agent"),
                )
            )
        return text, tool_calls

    def initiate_call(self) -> tuple[str, list[ToolCall]]:
        return self._next_turn()

    def respond(self, conversation: Conversation) -> tuple[str, list[ToolCall]]:
        return self._next_turn()
