"""Fixture repo reset and rate-limit orchestration."""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

from eval.rate_limit import wait_if_needed

logger = logging.getLogger(__name__)

HARNESS_ROOT = Path(__file__).resolve().parents[1]
REPO_STATE_SCRIPT = HARNESS_ROOT / "eval" / "repo_state.py"


def reset_fixture_repo(repo: str, fixture_worktree: Path, dry_run: bool = False) -> None:
    """Reconcile the GitHub fixture state before a write-task run.

    Fast path uses the copied snapshot in this harness repo. Fallback delegates
    to the fixture repo's setup_github.sh and then refreshes the harness snapshot.
    """
    if dry_run:
        logger.info("Dry run: skipping fixture reset")
        return

    wait_if_needed(min_remaining=500)
    _checkout_main(fixture_worktree)

    logger.info("Attempting fast reconcile for %s", repo)
    reconcile = subprocess.run(
        [sys.executable, str(REPO_STATE_SCRIPT), "reconcile", repo],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(fixture_worktree),
    )
    if reconcile.returncode == 0:
        logger.info("Fixture reconcile complete. Cooling down 5s.")
        time.sleep(5)
        return

    logger.warning(
        "Fast reconcile failed with exit %s. Falling back to setup_github.sh.\n%s",
        reconcile.returncode,
        reconcile.stderr or reconcile.stdout,
    )
    setup_script = fixture_worktree / "setup_github.sh"
    if not setup_script.exists():
        raise FileNotFoundError(f"Missing fixture setup script: {setup_script}")

    setup = subprocess.run(
        ["bash", str(setup_script), repo],
        capture_output=True,
        text=True,
        timeout=600,
        cwd=str(fixture_worktree),
    )
    if setup.returncode != 0:
        raise RuntimeError(f"setup_github.sh failed: {setup.stderr or setup.stdout}")

    snapshot = subprocess.run(
        [sys.executable, str(REPO_STATE_SCRIPT), "snapshot", repo],
        capture_output=True,
        text=True,
        timeout=90,
        cwd=str(fixture_worktree),
    )
    if snapshot.returncode != 0:
        logger.warning("Snapshot refresh failed after fallback: %s", snapshot.stderr)

    logger.info("Fixture reset complete. Cooling down 60s.")
    time.sleep(60)


def _checkout_main(fixture_worktree: Path) -> None:
    subprocess.run(
        ["git", "checkout", "main"],
        capture_output=True,
        text=True,
        timeout=15,
        cwd=str(fixture_worktree),
    )
