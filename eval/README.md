# Eval Harness

This directory contains the GitHub-agent evaluation harness.

## Main Files

- `run_eval.py` runs tasks through either the pi.dev harness or the raw baseline.
- `tasks.json` defines the benchmark tasks and expected outputs.
- `resolve_numbers.py` maps task placeholders to current GitHub issue/PR numbers.
- `repo_state.py` snapshots and reconciles the GitHub fixture state.
- `runners/pi_runner.py` invokes pi.dev in JSON mode.
- `runners/raw_runner.py` invokes model APIs directly with a minimal read-only
  `gh` command loop.
- `evaluators.py` scores correctness, output quality, efficiency, latency, and
  tool adherence.

## Common Commands

```bash
python -m eval.run_eval --repo OWNER/REPO --task T01 --runs 0 --dry-run
python -m eval.run_eval --repo OWNER/REPO --runner pi --runs 3
python -m eval.run_eval --repo OWNER/REPO --runner raw --runs 3
python -m eval.repo_state snapshot OWNER/REPO
python -m eval.repo_state reconcile OWNER/REPO
```

Write tasks are skipped unless `--allow-writes` is passed.
