"""Shared runner contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ModelSpec:
    """One model cell in the drift matrix."""

    provider: str
    model: str
    family: str
    version: str

    @property
    def label(self) -> str:
        return f"{self.family}-{self.version}"


@dataclass(frozen=True)
class RunnerConfig:
    """Runtime configuration for one task execution."""

    model: ModelSpec
    repo: str
    fixture_worktree: Path
    skill_path: Path
    output_dir: Path
    thinking: str = "medium"
    timeout_s: int = 600
    run_index: int = 0
    task_id: str = "unknown"


@dataclass
class TaskResult:
    """Stable result shape emitted by every runner."""

    output: str = ""
    tool_calls: int = 0
    tool_names: list[str] = field(default_factory=list)
    tool_commands: list[str] = field(default_factory=list)
    tool_errors: int = 0
    latency_seconds: float = 0.0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_cost_usd: float | None = None
    provider: str = ""
    model: str = ""
    family: str = ""
    version: str = ""
    resolved_model: str | None = None
    runner: str = "pi"
    pi_version: str | None = None
    raw_event_path: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
