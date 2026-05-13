"""Pi JSON-mode runner for vendor-agnostic agent tasks."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from eval.runners.base import RunnerConfig, TaskResult

logger = logging.getLogger(__name__)
HARNESS_ROOT = Path(__file__).resolve().parents[2]

JSON_STREAM_LIMIT = 10 * 1024 * 1024
LOCAL_TOOLS = "bash,read,grep,find,ls"

BASE_PROMPT = (
    "You are performing a GitHub operations task for a controlled model-drift "
    "evaluation. Answer concisely with the specific information requested. "
    "Use the gh CLI through the bash tool for GitHub operations. Do not explain "
    "your process unless asked."
)


async def run_pi_task(prompt: str, config: RunnerConfig) -> dict[str, Any]:
    """Run one task with Pi and return the stable result dict."""
    start = time.time()
    result = TaskResult(
        provider=config.model.provider,
        model=config.model.model,
        family=config.model.family,
        version=config.model.version,
        pi_version=_pi_version(),
    )

    pi_path = shutil.which("pi")
    if not pi_path:
        result.error = "pi executable not found"
        result.output = "ERROR: pi executable not found"
        result.latency_seconds = round(time.time() - start, 2)
        return result.to_dict()

    config.output_dir.mkdir(parents=True, exist_ok=True)
    raw_event_path = _raw_event_path(config)
    result.raw_event_path = str(raw_event_path)

    task_prompt = _build_task_prompt(prompt, config.repo)
    command = _build_command(pi_path, config, task_prompt)
    env = _build_env(config.repo)
    scratch_parent = HARNESS_ROOT / "outputs" / "pi-cwd"
    scratch_parent.mkdir(parents=True, exist_ok=True)

    state: dict[str, Any] = {
        "text_chunks": [],
        "final_text": "",
        "tool_names": [],
        "tool_commands": [],
        "tool_errors": 0,
        "usage": {},
        "cost": None,
        "resolved_model": None,
    }

    try:
        with tempfile.TemporaryDirectory(
            prefix=f"{_slug(config.model.label)}-{config.task_id}-run{config.run_index + 1}-",
            dir=scratch_parent,
        ) as run_cwd:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=run_cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=JSON_STREAM_LIMIT,
            )

            async with asyncio.timeout(config.timeout_s):
                await asyncio.gather(
                    _read_stdout(proc, raw_event_path, state),
                    _read_stderr(proc, raw_event_path),
                )
                return_code = await proc.wait()

        if return_code != 0 and not state["final_text"] and not state["text_chunks"]:
            result.error = f"pi exited with status {return_code}"
            result.output = f"ERROR: pi exited with status {return_code}"
        elif return_code != 0:
            result.error = f"pi exited with status {return_code}"

    except TimeoutError:
        result.error = f"pi task timed out after {config.timeout_s}s"
        result.output = f"ERROR: {result.error}"
        try:
            proc.kill()  # type: ignore[possibly-undefined]
        except Exception:
            pass
    except Exception as exc:
        logger.exception("Pi task failed")
        result.error = str(exc)
        result.output = f"ERROR: {exc}"

    text = state["final_text"] or "".join(state["text_chunks"])
    if text and not result.output.startswith("ERROR:"):
        result.output = text.strip()

    result.tool_names = state["tool_names"]
    result.tool_commands = state["tool_commands"]
    result.tool_calls = len(state["tool_names"])
    result.tool_errors = int(state["tool_errors"])
    result.resolved_model = state["resolved_model"]
    usage = state["usage"]
    result.input_tokens = _first_int(usage, ("input_tokens", "prompt_tokens", "inputTokens"))
    result.output_tokens = _first_int(
        usage,
        ("output_tokens", "completion_tokens", "outputTokens"),
    )
    result.total_cost_usd = _first_float(
        usage,
        ("total_cost_usd", "totalCostUsd", "cost_usd", "costUsd"),
    )
    if result.total_cost_usd is None:
        result.total_cost_usd = state["cost"]
    result.latency_seconds = round(time.time() - start, 2)
    return result.to_dict()


def _build_command(pi_path: str, config: RunnerConfig, prompt: str) -> list[str]:
    append_prompt = f"{BASE_PROMPT}\nTarget GitHub repo: {config.repo}"
    return [
        pi_path,
        "--mode",
        "json",
        "--provider",
        config.model.provider,
        "--model",
        config.model.model,
        "--thinking",
        config.thinking,
        "--no-session",
        "--no-context-files",
        "--no-skills",
        "--no-extensions",
        "--no-prompt-templates",
        "--tools",
        LOCAL_TOOLS,
        "--skill",
        str(config.skill_path),
        "--append-system-prompt",
        append_prompt,
        prompt,
    ]


def _build_task_prompt(prompt: str, repo: str) -> str:
    return (
        f"Target GitHub repo: {repo}\n\n"
        f"Task:\n{prompt}\n\n"
        "Return the requested answer only. Use gh CLI commands when current GitHub "
        "state is needed."
    )


def _build_env(repo: str) -> dict[str, str]:
    env = os.environ.copy()
    token = env.get("GITHUB_PERSONAL_ACCESS_TOKEN")
    if token:
        env.setdefault("GH_TOKEN", token)
        env.setdefault("GITHUB_TOKEN", token)
    agent_dir = env.get("PI_CODING_AGENT_DIR") or str(HARNESS_ROOT / "outputs" / "pi-agent")
    session_dir = env.get("PI_CODING_AGENT_SESSION_DIR") or str(
        HARNESS_ROOT / "outputs" / "pi-sessions"
    )
    Path(agent_dir).mkdir(parents=True, exist_ok=True)
    Path(session_dir).mkdir(parents=True, exist_ok=True)
    env["PI_CODING_AGENT_DIR"] = agent_dir
    env["PI_CODING_AGENT_SESSION_DIR"] = session_dir
    env["GH_REPO"] = repo
    env["EVAL_REPO"] = repo
    env.setdefault("PI_SKIP_VERSION_CHECK", "1")
    return env


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() or char in ("-", "_", ".") else "-" for char in value)


async def _read_stdout(proc: asyncio.subprocess.Process, raw_path: Path, state: dict[str, Any]) -> None:
    assert proc.stdout is not None
    with raw_path.open("a", encoding="utf-8") as raw:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            raw.write(text)
            raw.flush()
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                logger.debug("Ignoring non-JSON pi stdout line: %s", text[:200])
                continue
            _parse_event(event, state)


async def _read_stderr(proc: asyncio.subprocess.Process, raw_path: Path) -> None:
    assert proc.stderr is not None
    stderr_path = raw_path.with_suffix(".stderr.log")
    with stderr_path.open("a", encoding="utf-8") as raw:
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            raw.write(line.decode("utf-8", errors="replace"))
            raw.flush()


def _parse_event(event: dict[str, Any], state: dict[str, Any]) -> None:
    event_type = event.get("type")

    if event_type == "session":
        model = event.get("model") or event.get("resolvedModel")
        if isinstance(model, str):
            state["resolved_model"] = model
        return

    if event_type == "message_update":
        delta = _extract_delta(event.get("assistantMessageEvent"))
        if delta:
            state["text_chunks"].append(delta)
        _merge_usage(event, state)
        return

    if event_type == "message_end":
        text = _extract_message_text(event.get("message"))
        if text:
            state["final_text"] = text
        _merge_usage(event, state)
        return

    if event_type == "turn_end":
        text = _extract_message_text(event.get("message"))
        if text:
            state["final_text"] = text
        _merge_usage(event, state)
        return

    if event_type == "tool_execution_start":
        tool_name = str(event.get("toolName") or event.get("tool_name") or "unknown")
        state["tool_names"].append(tool_name)
        command = _extract_tool_command(event.get("args"))
        if command:
            state["tool_commands"].append(command)
        return

    if event_type == "tool_execution_end":
        if event.get("isError"):
            state["tool_errors"] += 1
        _merge_usage(event, state)
        return

    if event_type == "agent_end":
        _merge_usage(event, state)


def _extract_delta(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    if isinstance(value.get("delta"), str):
        return value["delta"]
    if isinstance(value.get("text"), str):
        return value["text"]
    return _extract_message_text(value)


def _extract_message_text(value: Any) -> str:
    chunks: list[str] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, str):
            chunks.append(obj)
            return
        if isinstance(obj, dict):
            text = obj.get("text")
            if isinstance(text, str):
                chunks.append(text)
            content = obj.get("content")
            if content is not None:
                walk(content)
            return
        if isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(value)
    return "".join(chunks)


def _extract_tool_command(args: Any) -> str:
    if isinstance(args, dict):
        for key in ("command", "cmd", "script", "input"):
            value = args.get(key)
            if isinstance(value, str):
                return value
        try:
            return json.dumps(args, sort_keys=True)
        except TypeError:
            return str(args)
    if isinstance(args, str):
        return args
    return ""


def _merge_usage(event: dict[str, Any], state: dict[str, Any]) -> None:
    usage = _find_dict_with_any_key(
        event,
        {
            "input_tokens",
            "output_tokens",
            "prompt_tokens",
            "completion_tokens",
            "inputTokens",
            "outputTokens",
        },
    )
    if usage:
        state["usage"].update(usage)

    cost = _find_first_number(event, ("total_cost_usd", "totalCostUsd", "cost_usd", "costUsd"))
    if cost is not None:
        state["cost"] = cost

    model = _find_first_string(event, ("resolvedModel", "resolved_model", "modelId", "model"))
    if model and not state.get("resolved_model"):
        state["resolved_model"] = model


def _find_dict_with_any_key(obj: Any, keys: set[str]) -> dict[str, Any] | None:
    if isinstance(obj, dict):
        if keys.intersection(obj.keys()):
            return obj
        for value in obj.values():
            found = _find_dict_with_any_key(value, keys)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_dict_with_any_key(item, keys)
            if found:
                return found
    return None


def _find_first_number(obj: Any, keys: Iterable[str]) -> float | None:
    key_set = set(keys)
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in key_set and isinstance(value, (int, float)):
                return float(value)
        for value in obj.values():
            found = _find_first_number(value, key_set)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_first_number(item, key_set)
            if found is not None:
                return found
    return None


def _find_first_string(obj: Any, keys: Iterable[str]) -> str | None:
    key_set = set(keys)
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in key_set and isinstance(value, str):
                return value
        for value in obj.values():
            found = _find_first_string(value, key_set)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_first_string(item, key_set)
            if found:
                return found
    return None


def _first_int(data: dict[str, Any], keys: Iterable[str]) -> int | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
    return None


def _first_float(data: dict[str, Any], keys: Iterable[str]) -> float | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _raw_event_path(config: RunnerConfig) -> Path:
    safe_model = _safe_name(config.model.model)
    safe_task = _safe_name(config.task_id)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return config.output_dir / f"pi-{safe_model}-{safe_task}-run{config.run_index + 1}-{stamp}.jsonl"


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in value)


def _pi_version() -> str | None:
    pi_path = shutil.which("pi")
    if not pi_path:
        return None
    try:
        proc = shutil.which("pi")
        if not proc:
            return None
        result = subprocess.run([proc, "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip() or result.stderr.strip() or None
    except Exception:
        return None
    return None
