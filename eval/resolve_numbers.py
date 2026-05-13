"""Resolve issue/PR numbers dynamically from GitHub.

Queries the repo to find current issue and PR numbers by title,
then replaces {{PLACEHOLDER}} tokens in task JSON.
"""

import logging
import re
import subprocess

logger = logging.getLogger(__name__)

# Placeholder key → issue title
ISSUE_KEYS = {
    "ISSUE_OTLP_TIMEOUT": "OTLP exporter fails silently on connection timeout",
    "ISSUE_CUSTOM_ATTRS": "Add support for custom span attributes",
    "ISSUE_OAUTH_DOCS": "Documentation for OAuth authentication is incomplete",
    "ISSUE_BATCH_SHUTDOWN": "Batch exporter drops last batch on shutdown",
    "ISSUE_RETRY_CONFIG": "Add retry configuration to client constructor",
    "ISSUE_CONSOLE_CONFIG": "Console exporter output is not configurable",
    "ISSUE_README_TYPO": "Typo in README.md",
    "ISSUE_GRPC_TRANSPORT": "Add gRPC transport option for OTLP exporter",
    "ISSUE_CONFIG_CRASH": "Config file parser crashes on empty values",
    "ISSUE_CI_PYTHON312": "CI workflow doesn't run on Python 3.12",
    "ISSUE_METRICS": "Add metrics collection support",
    "ISSUE_JSON_UNICODE": "JSON file exporter doesn't handle unicode properly",
    # Closed issues referenced in criteria
    "ISSUE_OTLP_BATCH_DROP": "OTLP exporter silently drops spans over batch limit",
    "ISSUE_JSON_FILE_EXPORTER": "Add JSON file exporter",
    "ISSUE_CONSOLE_EXPORTER_DEBUG": "Add console/stdout exporter for debugging",
}

# Placeholder key → PR title
PR_KEYS = {
    "PR_FIX_BATCH": "Fix batch exporter shutdown race condition",
    "PR_CI_MATRIX": "Add Python 3.12 to CI matrix",
    "PR_GRPC_DRAFT": "Draft: gRPC transport for OTLP",
}


def resolve_numbers(repo: str) -> dict[str, int]:
    """Query GitHub for current issue/PR numbers, return placeholder→number mapping."""
    mapping = {}

    # Get all issues (open + closed)
    result = subprocess.run(
        ["gh", "issue", "list", "--repo", repo, "--state", "all", "--limit", "200",
         "--json", "number,title", "--jq", '.[] | "\\(.number)\\t\\(.title)"'],
        capture_output=True, text=True, timeout=30,
    )
    title_to_num: dict[str, int] = {}
    for line in result.stdout.strip().split("\n"):
        if "\t" in line:
            num_str, title = line.split("\t", 1)
            num = int(num_str)
            # Take the highest number (most recent) for duplicate titles
            title_to_num[title] = max(num, title_to_num.get(title, 0))

    for key, title in ISSUE_KEYS.items():
        if title in title_to_num:
            mapping[key] = title_to_num[title]
        else:
            logger.warning("Issue not found for placeholder %s: '%s'", key, title)

    # Get all PRs (open + closed)
    result = subprocess.run(
        ["gh", "pr", "list", "--repo", repo, "--state", "all", "--limit", "200",
         "--json", "number,title", "--jq", '.[] | "\\(.number)\\t\\(.title)"'],
        capture_output=True, text=True, timeout=30,
    )
    title_to_num = {}
    for line in result.stdout.strip().split("\n"):
        if "\t" in line:
            num_str, title = line.split("\t", 1)
            num = int(num_str)
            title_to_num[title] = max(num, title_to_num.get(title, 0))

    for key, title in PR_KEYS.items():
        if title in title_to_num:
            mapping[key] = title_to_num[title]
        else:
            logger.warning("PR not found for placeholder %s: '%s'", key, title)

    logger.info("Resolved %d/%d placeholders from repo %s",
                len(mapping), len(ISSUE_KEYS) + len(PR_KEYS), repo)
    return mapping


def apply_placeholders(tasks_json: str, mapping: dict[str, int]) -> str:
    """Replace {{KEY}} placeholders in raw JSON string with resolved numbers."""
    result = tasks_json
    for key, num in mapping.items():
        result = result.replace("{{" + key + "}}", str(num))

    unresolved = re.findall(r"\{\{(\w+)\}\}", result)
    if unresolved:
        logger.warning("Unresolved placeholders: %s", unresolved)

    return result
