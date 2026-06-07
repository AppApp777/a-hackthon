"""LLM abstraction layer. Supports: claude CLI (default), Anthropic API, OpenAI-compatible API."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

PROVIDER = os.getenv("LLM_PROVIDER", "claude_cli")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", None)
JUDGE_MODEL = os.getenv("JUDGE_MODEL", None)
JUDGE_MODEL_SECONDARY = os.getenv("JUDGE_MODEL_SECONDARY", None)

# Optional usage callback — set by orchestrator to track token costs.
# Signature: callback(model: str, usage: dict, purpose: str) -> None
_usage_callback: Any = None


def _call_claude_cli(
    messages: list[dict],
    system: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    model: str | None = None,
) -> dict:
    """Call Claude via the claude CLI's print mode (-p), passing prompt via stdin."""
    import shutil
    import tempfile

    parts = []
    for msg in messages:
        role = msg["role"]
        content = msg.get("content", "")
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict):
                    texts.append(block.get("text", block.get("content", str(block))))
                else:
                    texts.append(str(block))
            content = "\n".join(texts)
        if role == "user":
            parts.append(content)
        elif role == "assistant":
            parts.append(f"[Assistant]\n{content}")
        elif role == "tool":
            parts.append(f"[Tool Result]\n{content}")

    full_prompt = "\n\n".join(parts)

    try:
        claude_bin = shutil.which("claude")
        if not claude_bin:
            raise FileNotFoundError("claude CLI not found in PATH")

        cmd_args = [claude_bin, "-p", "--output-format", "text"]
        if model:
            cmd_args.extend(["--model", model])

        sys_path = None
        if system:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            ) as sf:
                sf.write(system)
                sys_path = sf.name
            cmd_args.extend(["--system-prompt-file", sys_path])

        # Run from temp dir to avoid CLAUDE.md auto-discovery polluting the agent context
        isolated_cwd = tempfile.gettempdir()

        result = subprocess.run(
            cmd_args,
            input=full_prompt,
            capture_output=True,
            text=True,
            timeout=180,
            encoding="utf-8",
            cwd=isolated_cwd,
        )

        if sys_path:
            try:
                os.unlink(sys_path)
            except OSError:
                pass

        response_text = result.stdout.strip()
        if result.returncode != 0 and not response_text:
            response_text = f"[CLI Error] {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        response_text = "[CLI Error] Claude CLI timed out after 180s"
    except Exception as e:
        response_text = f"[CLI Error] {e}"

    return {
        "content": response_text,
        "tool_calls": [],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


def _call_anthropic(
    messages: list[dict],
    model: str,
    system: str | None = None,
    tools: list[dict] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> dict:
    import anthropic

    ant_kwargs: dict[str, Any] = {}
    base_url = os.getenv("ANTHROPIC_BASE_URL")
    if base_url:
        ant_kwargs["base_url"] = base_url
    auth_token = os.getenv("ANTHROPIC_AUTH_TOKEN")
    if auth_token:
        ant_kwargs["auth_token"] = auth_token
    client = anthropic.Anthropic(**ant_kwargs)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools
    response = client.messages.create(**kwargs)

    result: dict[str, Any] = {"content": "", "tool_calls": []}
    for block in response.content:
        if block.type == "text":
            result["content"] += block.text
        elif block.type == "tool_use":
            result["tool_calls"].append(
                {
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                }
            )
    result["stop_reason"] = response.stop_reason
    result["usage"] = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }
    return result


_MODEL_ENDPOINTS: dict[str, tuple[str, str]] = {
    "gpt-": ("JMRAI_API_KEY", "https://jmrai.net/v1"),
    "gemini-": ("JMRAI_API_KEY", "https://jmrai.net/v1"),
    "glm-5": ("DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    "kimi-k2.6": ("DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    "qwen": ("DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    "DeepSeek-V4": ("AIPING_API_KEY", "https://aiping.cn/api/v1"),
    "deepseek-v": ("BAI_API_KEY", "https://api.b.ai/v1"),
    "glm-": ("BAI_API_KEY", "https://api.b.ai/v1"),
    "kimi-k": ("BAI_API_KEY", "https://api.b.ai/v1"),
    "MiniMax": ("MINIMAX_API_KEY", "https://api.minimaxi.com/v1"),
    "kimi": ("KIMI_API_KEY", "https://api.kimi.com/coding/v1"),
    "deepseek": ("DEEPSEEK_API_KEY", "https://api.deepseek.com"),
    "glm": ("GLM_API_KEY", "https://open.bigmodel.cn/api/paas/v4"),
    "mimo": ("OPENAI_API_KEY", "https://token-plan-cn.xiaomimimo.com/v1"),
    "LongCat": ("LONGCAT_API_KEY", "https://api.longcat.chat/openai"),
}


def _resolve_openai_client(model: str):
    """Pick the right base_url and api_key based on model prefix."""
    from openai import OpenAI

    for prefix, (key_env, base_url) in _MODEL_ENDPOINTS.items():
        if model.startswith(prefix):
            api_key = os.getenv(key_env)
            if api_key:
                kwargs: dict[str, Any] = {"api_key": api_key, "base_url": base_url}
                if prefix == "kimi":
                    kwargs["default_headers"] = {"User-Agent": "claude-code/1.0"}
                return OpenAI(**kwargs)
    return OpenAI(
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )


def _call_mimo_raw(
    messages: list[dict],
    model: str,
    system: str | None = None,
    tools: list[dict] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> dict:
    """Direct HTTP call to mimo API, bypassing OpenAI SDK serialization issues."""
    import httpx

    base_url = "https://token-plan-cn.xiaomimimo.com/v1"
    api_key = os.getenv("OPENAI_API_KEY", "")

    full_messages = []
    if system:
        full_messages.append({"role": "system", "content": system})
    for m in messages:
        full_messages.append({"role": m["role"], "content": m.get("content") or "(无内容)"})

    body: dict[str, Any] = {
        "model": model,
        "messages": full_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        body["tools"] = [{"type": "function", "function": t} for t in tools]
        body["tool_choice"] = "auto"

    resp = httpx.post(
        f"{base_url}/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"mimo API error {resp.status_code}: {resp.text[:300]}")
    data = resp.json()

    msg = data["choices"][0]["message"]
    result: dict[str, Any] = {"content": msg.get("content") or "", "tool_calls": []}
    for tc in msg.get("tool_calls") or []:
        result["tool_calls"].append(
            {
                "id": tc["id"],
                "name": tc["function"]["name"],
                "arguments": json.loads(tc["function"]["arguments"])
                if isinstance(tc["function"]["arguments"], str)
                else tc["function"]["arguments"],
            }
        )
    result["stop_reason"] = data["choices"][0].get("finish_reason")
    usage = data.get("usage", {})
    result["usage"] = {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
    }
    return result


def _call_openai(
    messages: list[dict],
    model: str,
    system: str | None = None,
    tools: list[dict] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> dict:
    if model.startswith("mimo"):
        return _call_mimo_raw(messages, model, system, tools, temperature, max_tokens)
    client = _resolve_openai_client(model)
    full_messages = []
    if system:
        full_messages.append({"role": "system", "content": system})
    for m in messages:
        if m.get("content") or m.get("tool_calls"):
            full_messages.append(m)
        else:
            full_messages.append({**m, "content": "(无内容)"})

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": full_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = [{"type": "function", "function": t} for t in tools]
        kwargs["tool_choice"] = "auto"
    response = client.chat.completions.create(**kwargs)
    msg = response.choices[0].message

    result: dict[str, Any] = {"content": msg.content or "", "tool_calls": []}
    if msg.tool_calls:
        for tc in msg.tool_calls:
            result["tool_calls"].append(
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments),
                }
            )
    result["stop_reason"] = response.choices[0].finish_reason
    result["usage"] = {
        "input_tokens": response.usage.prompt_tokens if response.usage else 0,
        "output_tokens": response.usage.completion_tokens if response.usage else 0,
    }
    return result


def _infer_provider(model: str | None) -> str | None:
    """Infer provider from model name if possible."""
    if not model:
        return None
    if model.startswith("claude-"):
        # Use Anthropic API only if key is available; otherwise CLI with --model
        if os.getenv("ANTHROPIC_API_KEY"):
            return "anthropic"
        return "claude_cli"
    if model.startswith("gpt-") or model.startswith("o1") or model.startswith("o3"):
        return "openai"
    if model.startswith("gemini"):
        return "openai"
    if model.startswith("deepseek"):
        return "openai"  # DeepSeek uses OpenAI-compatible API
    if model.startswith("glm"):
        return "openai"  # GLM uses OpenAI-compatible API
    if model.startswith("MiniMax"):
        return "openai"  # MiniMax uses OpenAI-compatible API
    if model.startswith("kimi"):
        return "openai"  # Kimi uses OpenAI-compatible API
    if model.startswith("qwen"):
        return "openai"  # Qwen/Bailian uses OpenAI-compatible API
    if model.startswith("mimo"):
        return "openai"
    if model.startswith("LongCat"):
        return "openai"
    return None


def chat(
    messages: list[dict],
    model: str | None = None,
    system: str | None = None,
    tools: list[dict] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    provider: str | None = None,
) -> dict:
    """Unified chat interface. Returns {content, tool_calls, stop_reason, usage}."""
    model = model or DEFAULT_MODEL
    # Auto-detect provider from model name if not explicitly set
    p = provider or _infer_provider(model) or PROVIDER

    if p == "claude_cli" and temperature == 0 and os.getenv("ANTHROPIC_API_KEY"):
        p = "anthropic"
    if p == "claude_cli":
        result = _call_claude_cli(messages, system, temperature, max_tokens, model=model)
    elif p == "anthropic":
        m = model or "claude-sonnet-4-6"
        result = _call_anthropic(messages, m, system, tools, temperature, max_tokens)
    else:
        m = model or "gpt-4o"
        result = _call_openai(messages, m, system, tools, temperature, max_tokens)

    if result.get("content"):
        result["content"] = _strip_model_artifacts(result["content"])

    # Fire usage callback if registered (cost tracking)
    if _usage_callback is not None:
        try:
            resolved_model = model or "claude_cli"
            _usage_callback(resolved_model, result.get("usage", {}))
        except Exception:
            pass  # Never let tracking break the call path

    return result


_THINK_RE = re.compile(r"<think>[\s\S]*?</think>\s*", re.IGNORECASE)
_MINIMAX_TOOL_RE = re.compile(r"<minimax:tool_call>[\s\S]*?</minimax:tool_call>\s*", re.IGNORECASE)
_MINIMAX_TOOL_UNCLOSED_RE = re.compile(r"<minimax:tool_call>[\s\S]*$", re.IGNORECASE)


def _strip_model_artifacts(text: str) -> str:
    """Remove model-specific artifacts: think blocks, MiniMax XML tool calls, etc."""
    text = _THINK_RE.sub("", text)
    text = _MINIMAX_TOOL_RE.sub("", text)
    text = _MINIMAX_TOOL_UNCLOSED_RE.sub("", text)
    return text.strip()


def chat_text(
    prompt: str,
    model: str | None = None,
    system: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> str:
    """Simple text-in text-out helper. Defaults to JUDGE_MODEL (used by scorer/diagnosis)."""
    result = chat(
        messages=[{"role": "user", "content": prompt}],
        model=model or JUDGE_MODEL,
        system=system,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return _strip_model_artifacts(result["content"])
