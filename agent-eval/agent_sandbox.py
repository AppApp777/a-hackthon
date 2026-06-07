"""Subprocess sandbox for agent isolation — prevents memory introspection attacks."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from models import Conversation, ToolCall


class SandboxedAgent:
    """Wraps an agent to run in a separate subprocess.

    The agent cannot access orchestrator memory, tool_sim, ledger, or harness
    because it runs in a different process. Communication is JSON over pipes.
    """

    def __init__(
        self,
        scenario_json: str,
        tool_defs: list[dict[str, Any]],
        model: str | None = None,
    ):
        self.scenario_json = scenario_json
        self.tool_defs = tool_defs
        self.model = model
        self._proc: subprocess.Popen | None = None
        self._start_worker()

    def _start_worker(self):
        worker_path = str(Path(__file__).parent / "_agent_worker.py")
        self._proc = subprocess.Popen(
            [sys.executable, worker_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        # Send init message
        init_msg = json.dumps(
            {
                "type": "init",
                "scenario": self.scenario_json,
                "tool_defs": self.tool_defs,
                "model": self.model,
            },
            ensure_ascii=False,
        )
        self._send(init_msg)
        resp = self._recv()
        if resp.get("status") != "ready":
            raise RuntimeError(f"Agent worker failed to start: {resp}")

    def _send(self, msg: str):
        self._proc.stdin.write(msg + "\n")
        self._proc.stdin.flush()

    def _recv(self) -> dict:
        line = self._proc.stdout.readline().strip()
        if not line:
            stderr = self._proc.stderr.read()
            raise RuntimeError(f"Agent worker died: {stderr[:500]}")
        return json.loads(line)

    def initiate_call(self) -> tuple[str, list[dict]]:
        self._send(json.dumps({"type": "initiate"}))
        resp = self._recv()
        return resp["text"], resp.get("tool_requests", [])

    def respond(self, conversation: Conversation) -> tuple[str, list[dict]]:
        conv_data = conversation.model_dump(mode="json")
        self._send(
            json.dumps(
                {"type": "respond", "conversation": conv_data}, ensure_ascii=False, default=str
            )
        )
        resp = self._recv()
        return resp["text"], resp.get("tool_requests", [])

    def close(self):
        if self._proc and self._proc.poll() is None:
            self._send(json.dumps({"type": "shutdown"}))
            self._proc.terminate()
            self._proc.wait(timeout=5)


class IsolatedAgentAdapter:
    """Adapter that makes SandboxedAgent look like OutboundBaselineAgent to the orchestrator.

    Tool execution still happens in the orchestrator process — only the agent's
    LLM reasoning runs in the sandbox. Tool requests come back as dicts,
    and the adapter runs them through the real executor.
    """

    def __init__(
        self,
        scenario_json: str,
        tool_executor,
        tool_defs: list[dict[str, Any]],
        model: str | None = None,
    ):
        self._sandbox = SandboxedAgent(scenario_json, tool_defs, model)
        self._tool_executor = tool_executor
        self._tool_defs = tool_defs

    def initiate_call(self) -> tuple[str, list[ToolCall]]:
        text, tool_requests = self._sandbox.initiate_call()
        tool_calls = self._execute_requests(tool_requests)
        return text, tool_calls

    def respond(self, conversation: Conversation) -> tuple[str, list[ToolCall]]:
        text, tool_requests = self._sandbox.respond(conversation)
        tool_calls = self._execute_requests(tool_requests)
        return text, tool_calls

    def _execute_requests(self, requests: list[dict]) -> list[ToolCall]:
        results = []
        for req in requests:
            tc = self._tool_executor(req.get("tool", ""), req.get("args", {}))
            results.append(tc)
        return results

    def close(self):
        self._sandbox.close()
