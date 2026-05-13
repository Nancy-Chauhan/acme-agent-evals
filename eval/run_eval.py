#!/usr/bin/env python3
"""Run the Acme GitHub-agent evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

HARNESS_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(HARNESS_ROOT / ".env")

from eval.arize_logging import create_arize_client, create_dataset, dataset_name
from eval.evaluators import evaluate_task, judge_output_quality
from eval.repo_control import reset_fixture_repo
from eval.runners.base import ModelSpec, RunnerConfig
from eval.runners.pi_runner import run_pi_task
from eval.runners.raw_runner import run_raw_task
from eval.tasks import load_model_matrix, load_tasks, split_tasks

logger = logging.getLogger("acme_agent_evals")


def configure_logging() -> None:
    log_file = HARNESS_ROOT / "eval.log"
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt)
    handler = logging.FileHandler(log_file, mode="a")
    handler.setFormatter(logging.Formatter(fmt))
    logging.getLogger().addHandler(handler)


def local_results_path() -> Path:
    out_dir = HARNESS_ROOT / "outputs" / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return out_dir / f"results-{stamp}-pid{os.getpid()}.jsonl"


def experiment_results_path(experiment_name: str) -> Path:
    out_dir = HARNESS_ROOT / "outputs" / "experiments"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(
        char if char.isalnum() or char in ("-", "_", ".") else "-"
        for char in experiment_name
    )
    return out_dir / f"{safe_name}.csv"


def write_result(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True, default=str) + "\n")


async def execute_task(
    task: dict[str, Any],
    model: ModelSpec,
    repo: str,
    fixture_worktree: Path,
    skill_path: Path,
    thinking: str,
    run_index: int,
    results_path: Path,
    runner: str,
) -> str:
    config = RunnerConfig(
        model=model,
        repo=repo,
        fixture_worktree=fixture_worktree,
        skill_path=skill_path,
        output_dir=HARNESS_ROOT / "outputs" / "raw",
        thinking=thinking,
        run_index=run_index,
        task_id=task["id"],
    )
    if runner == "pi":
        result = await run_pi_task(task["description"], config)
    elif runner == "raw":
        result = await run_raw_task(task["description"], config)
    else:
        raise ValueError(f"Unknown runner: {runner}")
    record = {
        "task_id": task["id"],
        "tier": task["tier"],
        "category": task["category"],
        "expected_output": task.get("expected_output", {}),
        "run_index": run_index,
        **result,
    }
    write_result(results_path, record)
    return json.dumps(result)


def parse_output(output: str | None) -> tuple[str, dict[str, Any]]:
    if output is None:
        return "", {}
    try:
        result = json.loads(output)
        return result.get("output", output), result
    except (json.JSONDecodeError, TypeError):
        return output, {}


def make_evaluators(task_by_desc: dict[str, dict[str, Any]]):
    from arize.experiments import EvaluationResult

    def get_task(dataset_row) -> dict[str, Any]:
        description = dataset_row.get("attributes.input.value", "")
        return task_by_desc.get(description, {})

    def correctness(output: str, dataset_row) -> EvaluationResult:
        if output is None:
            return EvaluationResult(score=0, label="error", explanation="Task produced no output.")
        task = get_task(dataset_row)
        output_text, _ = parse_output(output)
        return evaluate_task(output_text or "", task)

    def output_quality(output: str, dataset_row) -> EvaluationResult:
        task = get_task(dataset_row)
        if task.get("tier") != 4:
            return EvaluationResult(score=1.0, label="n/a", explanation="Not a tier 4 task.")
        if output is None:
            return EvaluationResult(score=0, label="error", explanation="No output.")
        output_text, _ = parse_output(output)
        return judge_output_quality(output_text or "", task)

    def efficiency(output: str, dataset_row) -> EvaluationResult:
        if output is None:
            return EvaluationResult(score=0, label="error", explanation="No output.")
        task = get_task(dataset_row)
        _, result = parse_output(output)
        calls = int(result.get("tool_calls") or 0)
        expected = int(task.get("expected_steps", 5))
        if calls == 0:
            score, label = 0.0, "no_tools"
        elif calls <= expected:
            score, label = 1.0, "efficient"
        elif calls <= expected * 2:
            score = max(0.3, 1.0 - (calls - expected) / max(expected, 1))
            label = "moderate"
        else:
            score, label = 0.2, "excessive"
        return EvaluationResult(score=score, label=label, explanation=f"{calls} tool calls")

    def latency(output: str, dataset_row) -> EvaluationResult:
        if output is None:
            return EvaluationResult(score=0, label="error", explanation="No output.")
        _, result = parse_output(output)
        seconds = float(result.get("latency_seconds") or 0)
        if seconds <= 30:
            score, label = 1.0, "fast"
        elif seconds <= 120:
            score, label = 0.7, "moderate"
        elif seconds <= 300:
            score, label = 0.4, "slow"
        else:
            score, label = 0.1, "very_slow"
        return EvaluationResult(score=score, label=label, explanation=f"{seconds}s")

    def tool_adherence(output: str, dataset_row) -> EvaluationResult:
        if output is None:
            return EvaluationResult(score=0, label="error", explanation="No output.")
        _, result = parse_output(output)
        tool_names = [str(name).lower() for name in result.get("tool_names", [])]
        commands = [str(cmd).lower() for cmd in result.get("tool_commands", [])]
        if not tool_names:
            return EvaluationResult(score=0, label="no_tools", explanation="No tool calls recorded.")

        used_bash = any(name == "bash" or "bash" in name or "shell" in name for name in tool_names)
        used_gh_or_git = any("gh " in cmd or cmd.startswith("gh") or "git " in cmd for cmd in commands)
        unexpected = [
            name
            for name in tool_names
            if not any(allowed in name for allowed in ("bash", "read", "grep", "find", "ls"))
        ]
        score = 0.4
        if used_bash:
            score += 0.3
        if used_gh_or_git:
            score += 0.3
        if unexpected:
            score = min(score, 0.6)
        label = "adherent" if score >= 0.8 else "partial" if score >= 0.4 else "off_path"
        explanation = (
            f"tools={tool_names}; gh_or_git={used_gh_or_git}; unexpected={unexpected}"
        )
        return EvaluationResult(score=score, label=label, explanation=explanation)

    return [correctness, output_quality, efficiency, latency, tool_adherence]


def run_arize_experiment(
    client: Any,
    dataset_id: str,
    tasks: list[dict[str, Any]],
    model: ModelSpec,
    repo: str,
    fixture_worktree: Path,
    skill_path: Path,
    thinking: str,
    run_index: int,
    results_path: Path,
    experiment_prefix: str,
    runner: str,
    dry_run: bool,
) -> None:
    task_by_desc = {task["description"]: task for task in tasks}

    async def task_fn(dataset_row) -> str:
        description = dataset_row.get("attributes.input.value", "")
        task = task_by_desc[description]
        return await execute_task(
            task,
            model,
            repo,
            fixture_worktree,
            skill_path,
            thinking,
            run_index,
            results_path,
            runner,
        )

    experiment_name = f"{experiment_prefix}-{model.family}-{model.version}-{runner}-run{run_index + 1}"
    logger.info("Running experiment %s", experiment_name)
    experiment, experiment_df = client.experiments.run(
        name=experiment_name,
        dataset=dataset_id,
        task=task_fn,
        evaluators=make_evaluators(task_by_desc),
        concurrency=1,
        exit_on_error=False,
        dry_run=dry_run,
        timeout=600,
    )
    experiment_path = experiment_results_path(experiment_name)
    experiment_df.to_csv(experiment_path, index=False)
    logger.info(
        "Experiment complete: %s rows=%s csv=%s experiment_id=%s",
        experiment_name,
        len(experiment_df),
        experiment_path,
        getattr(experiment, "id", ""),
    )


async def run_local_tasks(
    tasks: list[dict[str, Any]],
    models: list[ModelSpec],
    repo: str,
    fixture_worktree: Path,
    skill_path: Path,
    thinking: str,
    runs: int,
    run_offset: int,
    results_path: Path,
    runner: str,
) -> None:
    for model in models:
        for run_index in range(run_offset, run_offset + runs):
            for task in tasks:
                logger.info("Local run: %s %s run %d", model.model, task["id"], run_index + 1)
                await execute_task(
                    task,
                    model,
                    repo,
                    fixture_worktree,
                    skill_path,
                    thinking,
                    run_index,
                    results_path,
                    runner,
                )


def run_read_tasks(
    client: Any,
    tasks: list[dict[str, Any]],
    models: list[ModelSpec],
    repo: str,
    fixture_worktree: Path,
    skill_path: Path,
    thinking: str,
    runs: int,
    run_offset: int,
    results_path: Path,
    runner: str,
    dry_run: bool,
    dataset_id_override: str | None = None,
    experiment_prefix_override: str | None = None,
) -> None:
    if not tasks:
        return
    if client is None:
        asyncio.run(run_local_tasks(tasks, models, repo, fixture_worktree, skill_path, thinking, runs, run_offset, results_path, runner))
        return

    experiment_prefix = (
        experiment_prefix_override
        or ("raw-baseline-read" if runner == "raw" else "pi-harness-read")
    )
    dataset_id = dataset_id_override or create_dataset(client, tasks, dataset_name(experiment_prefix))
    for model in models:
        for run_index in range(run_offset, run_offset + runs):
            run_arize_experiment(
                client,
                dataset_id,
                tasks,
                model,
                repo,
                fixture_worktree,
                skill_path,
                thinking,
                run_index,
                results_path,
                experiment_prefix,
                runner,
                dry_run,
            )


def run_write_tasks(
    client: Any,
    tasks: list[dict[str, Any]],
    models: list[ModelSpec],
    repo: str,
    fixture_worktree: Path,
    skill_path: Path,
    thinking: str,
    runs: int,
    run_offset: int,
    results_path: Path,
    runner: str,
    dry_run: bool,
    allow_writes: bool,
) -> None:
    if not tasks:
        return
    if not allow_writes:
        logger.warning("Skipping %d write tasks. Re-run with --allow-writes to include them.", len(tasks))
        return

    for task in tasks:
        task_list = [task]
        dataset_id = None
        if client is not None:
            experiment_prefix = (
                f"raw-baseline-write-{task['id']}"
                if runner == "raw"
                else f"pi-harness-write-{task['id']}"
            )
            dataset_id = create_dataset(client, task_list, dataset_name(experiment_prefix))

        for model in models:
            for run_index in range(run_offset, run_offset + runs):
                reset_fixture_repo(repo, fixture_worktree, dry_run=dry_run)
                if client is None:
                    asyncio.run(
                        execute_task(
                            task,
                            model,
                            repo,
                            fixture_worktree,
                            skill_path,
                            thinking,
                            run_index,
                            results_path,
                            runner,
                        )
                    )
                    continue
                assert dataset_id is not None
                run_arize_experiment(
                    client,
                    dataset_id,
                    task_list,
                    model,
                    repo,
                    fixture_worktree,
                    skill_path,
                    thinking,
                    run_index,
                    results_path,
                    experiment_prefix,
                    runner,
                    dry_run,
                )


def select_models(args: argparse.Namespace) -> list[ModelSpec]:
    if args.model:
        provider = args.provider or ("openai" if args.model.startswith("gpt-") else "anthropic")
        family = args.family or ("gpt" if provider == "openai" else "sonnet")
        version = args.version or args.model.replace("claude-sonnet-", "").replace("gpt-", "")
        return [ModelSpec(provider=provider, model=args.model, family=family, version=version)]
    matrix_path = Path(args.model_matrix)
    if not matrix_path.is_absolute():
        matrix_path = HARNESS_ROOT / matrix_path
    return load_model_matrix(matrix_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Acme GitHub-agent evaluation")
    parser.add_argument("--repo", default=os.environ.get("EVAL_REPO", ""), help="GitHub repo owner/name")
    parser.add_argument(
        "--fixture-worktree",
        default=os.environ.get("FIXTURE_WORKTREE") or str(HARNESS_ROOT),
        help="Local fixture worktree path",
    )
    parser.add_argument("--model-matrix", default=os.environ.get("MODEL_MATRIX", "configs/model_matrix.json"))
    parser.add_argument("--model", default=None, help="Single model to run")
    parser.add_argument("--provider", default=None, help="Provider for --model")
    parser.add_argument("--family", default=None, help="Family label for --model")
    parser.add_argument("--version", default=None, help="Version label for --model")
    parser.add_argument("--task", default=None, help="Comma-separated task IDs")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3, 4], default=None)
    parser.add_argument("--runs", type=int, default=int(os.environ.get("RUNS", "3")))
    parser.add_argument("--run-offset", type=int, default=0, help="Zero-based run index to start from")
    parser.add_argument("--thinking", default=os.environ.get("PI_THINKING", "medium"))
    parser.add_argument("--runner", choices=["pi", "raw"], default=os.environ.get("RUNNER", "pi"))
    parser.add_argument("--dataset-id", default=os.environ.get("ARIZE_DATASET_ID"), help="Existing Arize dataset id to append experiments to")
    parser.add_argument("--experiment-prefix", default=os.environ.get("EXPERIMENT_PREFIX"), help="Experiment name prefix")
    parser.add_argument("--dry-run", action="store_true", help="Run locally without Arize logging")
    parser.add_argument("--allow-writes", action="store_true", help="Allow write tasks to mutate GitHub")
    return parser.parse_args()


def main() -> None:
    configure_logging()
    args = parse_args()
    if not args.repo:
        raise SystemExit("--repo is required or EVAL_REPO must be set")

    fixture_worktree = Path(args.fixture_worktree).expanduser().resolve()
    skill_path = HARNESS_ROOT / "eval" / "skills" / "gh-cli-lobehub.md"
    models = select_models(args)
    tasks = load_tasks(args.repo, task_filter=args.task, tier_filter=args.tier)
    if not tasks:
        raise SystemExit("No tasks matched the requested filters")

    os.environ["EVAL_REPO"] = args.repo
    results_path = local_results_path()
    read_tasks, write_tasks = split_tasks(tasks)
    logger.info(
        "Config: repo=%s models=%s tasks=%d runs=%d run_offset=%d runner=%s dry_run=%s allow_writes=%s results=%s",
        args.repo,
        [model.model for model in models],
        len(tasks),
        args.runs,
        args.run_offset,
        args.runner,
        args.dry_run,
        args.allow_writes,
        results_path,
    )

    client = None if args.dry_run else create_arize_client()
    run_read_tasks(
        client,
        read_tasks,
        models,
        args.repo,
        fixture_worktree,
        skill_path,
        args.thinking,
        args.runs,
        args.run_offset,
        results_path,
        args.runner,
        args.dry_run,
        args.dataset_id,
        args.experiment_prefix,
    )
    run_write_tasks(
        client,
        write_tasks,
        models,
        args.repo,
        fixture_worktree,
        skill_path,
        args.thinking,
        args.runs,
        args.run_offset,
        results_path,
        args.runner,
        args.dry_run,
        args.allow_writes,
    )
    logger.info("Done. Local results: %s", results_path)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
