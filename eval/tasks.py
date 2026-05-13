"""Task loading and model-matrix utilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eval.resolve_numbers import apply_placeholders, resolve_numbers
from eval.runners.base import ModelSpec

HARNESS_ROOT = Path(__file__).resolve().parents[1]
TASKS_FILE = HARNESS_ROOT / "eval" / "tasks.json"


def load_tasks(
    repo: str,
    task_filter: str | None = None,
    tier_filter: int | None = None,
    tasks_file: Path = TASKS_FILE,
) -> list[dict[str, Any]]:
    """Load tasks, resolve live GitHub placeholders, and apply optional filters."""
    raw_json = tasks_file.read_text(encoding="utf-8")
    mapping = resolve_numbers(repo)
    resolved_json = apply_placeholders(raw_json, mapping)
    data = json.loads(resolved_json)
    tasks = data["tasks"]

    if task_filter:
        task_ids = {task_id.strip().upper() for task_id in task_filter.split(",") if task_id.strip()}
        tasks = [task for task in tasks if task["id"] in task_ids]

    if tier_filter is not None:
        tasks = [task for task in tasks if task["tier"] == tier_filter]

    return tasks


def split_tasks(tasks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split tasks into read/analysis tasks and write tasks."""
    read_tasks = [task for task in tasks if task["category"] in ("read", "analysis")]
    write_tasks = [task for task in tasks if task["category"] == "write"]
    return read_tasks, write_tasks


def load_model_matrix(path: Path) -> list[ModelSpec]:
    """Load the configured model sweep."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [ModelSpec(**item) for item in data]
