"""GitHub API rate limit checking and throttling."""

import json
import subprocess
import sys
import time
import logging

logger = logging.getLogger(__name__)


def check_rate_limit() -> dict:
    """Check GitHub API rate limit via `gh api rate_limit`.

    Returns dict with 'remaining', 'limit', and 'reset' (unix timestamp).
    """
    try:
        result = subprocess.run(
            ["gh", "api", "rate_limit", "--jq", ".rate"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return {"remaining": 5000, "limit": 5000, "reset": 0}


def wait_if_needed(min_remaining: int = 500) -> None:
    """Sleep until rate limit resets if remaining < min_remaining."""
    info = check_rate_limit()
    remaining = info.get("remaining", 5000)
    reset_at = info.get("reset", 0)

    if remaining < min_remaining:
        wait_seconds = max(0, reset_at - time.time()) + 5
        logger.warning(
            "Rate limit low (%d remaining). Sleeping %.0fs until reset.",
            remaining, wait_seconds,
        )
        time.sleep(wait_seconds)


def throttled_reset(repo: str, script_dir: str) -> None:
    """Reset repo to snapshot state. Fast path: reconcile.py (~5-30s).
    Fallback: setup_github.sh + re-snapshot (~3 min)."""
    wait_if_needed(min_remaining=500)

    # Ensure we're on main — a previous failed run may have left a feature branch.
    subprocess.run(
        ["git", "checkout", "main"],
        capture_output=True, text=True, timeout=10, cwd=script_dir,
    )

    # Fast path: incremental reconcile against snapshot.
    logger.info("Attempting fast reconcile for %s...", repo)
    r = subprocess.run(
        [sys.executable, f"{script_dir}/eval/repo_state.py", "reconcile", repo],
        capture_output=True, text=True, timeout=300, cwd=script_dir,
    )
    if r.returncode == 0:
        logger.info("Reconcile complete. Brief 5s cooldown.")
        time.sleep(5)
        return

    logger.warning("Reconcile failed (exit %d), falling back to setup_github.sh:\n%s",
                   r.returncode, r.stderr or r.stdout)

    # Fallback: full setup
    result = subprocess.run(
        ["bash", f"{script_dir}/setup_github.sh", repo],
        capture_output=True, text=True, timeout=600, cwd=script_dir,
    )
    if result.returncode != 0:
        logger.error("setup_github.sh failed:\n%s", result.stderr)
        raise RuntimeError(f"setup_github.sh failed with exit code {result.returncode}")

    # Re-snapshot so future reconciles match the fresh state.
    snap = subprocess.run(
        [sys.executable, f"{script_dir}/eval/repo_state.py", "snapshot", repo],
        capture_output=True, text=True, timeout=60, cwd=script_dir,
    )
    if snap.returncode != 0:
        logger.warning("Re-snapshot after fallback failed: %s", snap.stderr)

    logger.info("Reset complete. Waiting 60s for rate limit cooldown...")
    time.sleep(60)
