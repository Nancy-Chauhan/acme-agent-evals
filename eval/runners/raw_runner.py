"""Direct model API runner for raw-vs-harness comparisons.

This runner intentionally skips Pi and the gh skill document. It gives the
model a tiny JSON protocol for requesting safe read-only gh commands, then
feeds command output back until the model returns a final answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from eval.runners.base import RunnerConfig, TaskResult

logger = logging.getLogger(__name__)
HARNESS_ROOT = Path(__file__).resolve().parents[2]

MAX_TOOL_ROUNDS = 8
COMMAND_TIMEOUT_S = 45
MAX_COMMAND_OUTPUT_CHARS = 12_000

RAW_SYSTEM = """You are answering a GitHub operations benchmark task.

You do not have a prebuilt agent harness or skill. If you need current GitHub
state, request exactly one read-only gh CLI command using this JSON shape:

{"tool":"bash","command":"gh issue list --repo OWNER/REPO --json number,title"}

Rules:
- Use the target repo explicitly with --repo on every gh command.
- Use read-only gh commands only. Do not create, edit, close, merge, review, or comment.
- Do not use shell pipes, redirects, command chaining, or command substitution.
- When you have enough information, answer the user directly and concisely.
- If no tool is needed, answer directly.
"""


async def run_raw_task(prompt: str, config: RunnerConfig) -> dict[str, Any]:
    """Run one task through the provider SDK without Pi."""
    start = time.time()
    result = TaskResult(
        provider=config.model.provider,
        model=config.model.model,
        family=config.model.family,
        version=config.model.version,
        runner="raw",
    )
    config.output_dir.mkdir(parents=True, exist_ok=True)
    raw_event_path = _raw_event_path(config)
    result.raw_event_path = str(raw_event_path)

    messages: list[dict[str, str]] = [
        {
            "role": "user",
            "content": (
                f"Target GitHub repo: {config.repo}\n\n"
                f"Task:\n{prompt}\n\n"
                "Return only the requested answer."
            ),
        }
    ]
    env = _build_env(config.repo)
    scratch_parent = HARNESS_ROOT / "outputs" / "raw-cwd"
    scratch_parent.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(
            prefix=f"raw-{_slug(config.model.label)}-{config.task_id}-run{config.run_index + 1}-",
            dir=scratch_parent,
        ) as run_cwd:
            for turn in range(MAX_TOOL_ROUNDS + 1):
                text, usage, resolved_model = await asyncio.to_thread(
                    _call_model,
                    config,
                    messages,
                )
                if resolved_model:
                    result.resolved_model = resolved_model
                _merge_usage(result, usage)
                _append_jsonl(raw_event_path, {"type": "model", "turn": turn, "text": text})

                request = _parse_tool_request(text)
                if not request:
                    result.output = _strip_final_marker(text).strip()
                    break

                command = request["command"]
                result.tool_calls += 1
                result.tool_names.append("bash")
                result.tool_commands.append(command)
                tool_output, is_error = _run_command(command, run_cwd, env)
                if is_error:
                    result.tool_errors += 1
                _append_jsonl(
                    raw_event_path,
                    {
                        "type": "tool",
                        "turn": turn,
                        "command": command,
                        "is_error": is_error,
                        "output": tool_output,
                    },
                )
                messages.append({"role": "assistant", "content": text})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Tool result:\n"
                            f"{tool_output}\n\n"
                            "Continue. If you need another read-only gh command, return the JSON tool request. "
                            "Otherwise answer the original task directly."
                        ),
                    }
                )
            else:
                result.error = f"raw runner exceeded {MAX_TOOL_ROUNDS} tool rounds"
                result.output = f"ERROR: {result.error}"
    except Exception as exc:
        logger.exception("Raw task failed")
        result.error = str(exc)
        result.output = f"ERROR: {exc}"

    result.latency_seconds = round(time.time() - start, 2)
    return result.to_dict()


def _call_model(config: RunnerConfig, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any], str | None]:
    if config.model.provider == "anthropic":
        return _call_anthropic(config, messages)
    if config.model.provider == "openai":
        return _call_openai(config, messages)
    raise ValueError(f"Unsupported raw provider: {config.model.provider}")


def _call_anthropic(config: RunnerConfig, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any], str | None]:
    from anthropic import Anthropic

    client = Anthropic()
    response = client.messages.create(
        model=config.model.model,
        max_tokens=2048,
        system=f"{RAW_SYSTEM}\nTarget GitHub repo: {config.repo}",
        messages=messages,
    )
    text_parts = [
        block.text
        for block in response.content
        if getattr(block, "type", "") == "text" and getattr(block, "text", None)
    ]
    usage = {}
    if getattr(response, "usage", None):
        usage = {
            "input_tokens": getattr(response.usage, "input_tokens", None),
            "output_tokens": getattr(response.usage, "output_tokens", None),
        }
    return "\n".join(text_parts), usage, getattr(response, "model", None)


def _call_openai(config: RunnerConfig, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any], str | None]:
    from openai import OpenAI

    client = OpenAI()
    kwargs: dict[str, Any] = {
        "model": config.model.model,
        "instructions": f"{RAW_SYSTEM}\nTarget GitHub repo: {config.repo}",
        "input": messages,
        "max_output_tokens": 4096,
    }
    if config.model.model == "gpt-5":
        # GPT-5 can spend most of a small output budget on reasoning tokens.
        kwargs["reasoning"] = {"effort": "minimal"}
    response = client.responses.create(**kwargs)
    text = getattr(response, "output_text", "") or _extract_openai_output_text(response)
    usage = {}
    if getattr(response, "usage", None):
        usage = {
            "input_tokens": getattr(response.usage, "input_tokens", None),
            "output_tokens": getattr(response.usage, "output_tokens", None),
        }
    return text, usage, getattr(response, "model", None)


def _extract_openai_output_text(response: Any) -> str:
    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                chunks.append(text)
    return "\n".join(chunks)


def _parse_tool_request(text: str) -> dict[str, str] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    candidates = [cleaned]
    match = re.search(r"\{[\s\S]*?\}", cleaned)
    if match:
        candidates.append(match.group(0))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(data, dict)
            and str(data.get("tool", "")).lower() == "bash"
            and isinstance(data.get("command"), str)
        ):
            return {"command": data["command"].strip()}
    return None


def _run_command(command: str, cwd: str, env: dict[str, str]) -> tuple[str, bool]:
    safe, reason = _is_safe_readonly_gh(command)
    if not safe:
        return f"ERROR: Command rejected by raw baseline safety policy: {reason}", True

    try:
        proc = subprocess.run(
            shlex.split(command),
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {COMMAND_TIMEOUT_S}s", True

    output = (proc.stdout or "").strip()
    if proc.stderr:
        output = f"{output}\nSTDERR:\n{proc.stderr.strip()}".strip()
    if len(output) > MAX_COMMAND_OUTPUT_CHARS:
        output = output[:MAX_COMMAND_OUTPUT_CHARS] + "\n...[truncated]"
    return output or f"(exit {proc.returncode}, no output)", proc.returncode != 0


def _is_safe_readonly_gh(command: str) -> tuple[bool, str]:
    lowered = command.strip().lower()
    if not lowered.startswith("gh "):
        return False, "only gh commands are allowed"
    blocked_shell = (";", "&&", "||", "|", ">", "<", "`", "$(")
    if any(token in command for token in blocked_shell):
        return False, "shell chaining, pipes, redirects, and substitutions are disabled"
    blocked_words = (
        " create",
        " edit",
        " close",
        " reopen",
        " delete",
        " merge",
        " review",
        " comment",
        " run rerun",
        " workflow run",
    )
    if any(word in lowered for word in blocked_words):
        return False, "write-like gh subcommands are disabled"
    if re.search(r"\b(post|patch|put|delete)\b", lowered) and " gh api " in f" {lowered} ":
        return False, "mutating gh api methods are disabled"
    return True, ""


def _build_env(repo: str) -> dict[str, str]:
    env = os.environ.copy()
    token = env.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if token:
        env.setdefault("GH_TOKEN", token)
        env.setdefault("GITHUB_TOKEN", token)
    env["GH_REPO"] = repo
    env["EVAL_REPO"] = repo
    return env


def _merge_usage(result: TaskResult, usage: dict[str, Any]) -> None:
    input_tokens = _safe_int(usage.get("input_tokens"))
    output_tokens = _safe_int(usage.get("output_tokens"))
    if input_tokens is not None:
        result.input_tokens = (result.input_tokens or 0) + input_tokens
    if output_tokens is not None:
        result.output_tokens = (result.output_tokens or 0) + output_tokens


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def _raw_event_path(config: RunnerConfig) -> Path:
    import time as time_module

    stamp = time_module.strftime("%Y%m%d-%H%M%S")
    safe_model = _slug(config.model.model)
    safe_task = _slug(config.task_id)
    return config.output_dir / f"raw-{safe_model}-{safe_task}-run{config.run_index + 1}-{stamp}.jsonl"


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_", ".") else "-" for char in value)


def _strip_final_marker(text: str) -> str:
    return re.sub(r"^\s*(final answer|answer)\s*:\s*", "", text, flags=re.IGNORECASE)
