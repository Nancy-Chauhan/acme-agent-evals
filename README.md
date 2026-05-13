# Acme Agent Evals

Acme Agent Evals is a self-contained benchmark for evaluating GitHub-operating
agents. It includes a fictional Python SDK project, a fixture setup script that
creates realistic GitHub issues and pull requests, and an evaluation harness for
running the same tasks across multiple models and runner styles.

The benchmark is designed around a simple question:

> When the task, target repository, tool boundary, and evaluator stay fixed, how
> much does downstream agent behavior change as the model changes?

## Acknowledgements

This project was inspired by and adapted from the
[Acme SDK Python](https://github.com/seldo/acme-sdk-python) evaluation used in
Laurie Voss's
[MCP vs CLI skills work](https://arize.com/blog/mcp-vs-cli-skills-for-agents-what-our-eval-found-and-which-you-should-use/).
This repo extends that benchmark shape with a vendor-neutral pi.dev harness,
raw model baseline, and model-drift analysis workflow.

## What Is In This Repo

- `src/acme_sdk/` — fictional Python SDK used as the target project.
- `tests/`, `docs/`, `examples/` — realistic project surface for GitHub tasks.
- `setup_github.sh` — creates labels, milestones, issues, comments, branches,
  and pull requests in a GitHub repo.
- `eval/tasks.json` — GitHub-agent task suite with expected outputs.
- `eval/run_eval.py` — main experiment runner.
- `eval/runners/pi_runner.py` — pi.dev-based multi-provider agent runner.
- `eval/runners/raw_runner.py` — direct-model baseline runner with a minimal
  read-only `gh` command loop.
- `eval/evaluators.py` — correctness, quality, efficiency, latency, and tool
  adherence evaluators.
- `analysis/` — summary and chart scripts.
- `configs/model_matrix.json` — default Anthropic/OpenAI model sweep.

Generated experiment outputs, raw traces, local logs, secrets, and blog drafts
are ignored by git.

## Requirements

- Python 3.11+ for the eval harness.
- `gh` CLI authenticated to the GitHub account that owns the benchmark repo.
- `pi` CLI installed and authenticated/configured for the model providers you
  want to run.
- Arize AX credentials if you want hosted datasets, experiments, and evaluator
  traces.
- Anthropic/OpenAI credentials for the models in `configs/model_matrix.json`.

## Local Setup

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,eval]"
cp .env.example .env
```

Fill in `.env`:

```bash
EVAL_REPO=OWNER/acme-agent-evals
ARIZE_API_KEY=...
ARIZE_SPACE_ID=...
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
GITHUB_PERSONAL_ACCESS_TOKEN=...
```

If `gh auth status` already works with the right account, the GitHub token is
still useful because the runners pass it through as `GH_TOKEN`.

## Create The GitHub Fixture

The fixture setup mutates GitHub state. Run it only after the repo has been
created and pushed to GitHub.

```bash
./setup_github.sh OWNER/acme-agent-evals
.venv/bin/python -m eval.repo_state snapshot OWNER/acme-agent-evals
```

The setup script creates:

- 9 labels
- 3 milestones
- historical closed issues
- 12 open issues
- comments on selected issues
- 3 branches
- 3 open pull requests

Issue and PR numbers are resolved dynamically at runtime by
`eval/resolve_numbers.py`, so task definitions do not need hardcoded numbers.

## Run A Dry Check

Dry runs execute locally without creating Arize experiments.

```bash
.venv/bin/python -m eval.run_eval \
  --repo OWNER/acme-agent-evals \
  --task T01 \
  --runs 0 \
  --dry-run
```

## Run The pi.dev Harness

```bash
.venv/bin/python -m eval.run_eval \
  --repo OWNER/acme-agent-evals \
  --runner pi \
  --runs 3
```

To run one model:

```bash
.venv/bin/python -m eval.run_eval \
  --repo OWNER/acme-agent-evals \
  --runner pi \
  --model claude-sonnet-4-6 \
  --provider anthropic \
  --family sonnet \
  --version 4.6 \
  --runs 3
```

## Run The Raw Baseline

The raw runner does not use pi.dev or the GitHub CLI skill document. It gives the
model a small read-only `gh` command protocol and feeds command output back to
the model until it returns an answer.

```bash
.venv/bin/python -m eval.run_eval \
  --repo OWNER/acme-agent-evals \
  --runner raw \
  --runs 3
```

## Write Tasks

Write tasks mutate the GitHub fixture. They are skipped unless explicitly
enabled.

```bash
.venv/bin/python -m eval.run_eval \
  --repo OWNER/acme-agent-evals \
  --runner pi \
  --runs 3 \
  --allow-writes
```

Before each write-task run, the harness attempts to reconcile the repo to the
snapshot in `eval/repo_state.json`. If the fast reconcile cannot repair the
state, it falls back to `setup_github.sh` and refreshes the snapshot.

## Summaries

```bash
.venv/bin/python analysis/evaluator_summary.py
.venv/bin/python analysis/summarize.py
.venv/bin/python analysis/compare_runners.py
.venv/bin/python analysis/charts.py
```

Summary CSVs and charts are written under `outputs/`.

## Safety Notes

- Do not commit `.env`, raw traces, or local logs.
- Do not run `setup_github.sh` against a real production repo.
- Do not run write tasks without understanding that they create and modify
  GitHub issues/PRs.
- The benchmark target is intentionally fictional.
