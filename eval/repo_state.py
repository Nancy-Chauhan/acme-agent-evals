"""Snapshot and reconcile GitHub repo state for fast eval resets.

Usage:
  python repo_state.py snapshot <owner/repo>      # capture current state
  python repo_state.py reconcile <owner/repo>     # reconcile to snapshot
  python repo_state.py diff <owner/repo>          # show what would change

The reconciler handles cheap mutations (state, labels, assignees, milestone,
comments). For structural drift it can't fix (deleted issues, deleted/merged
PR branches), it exits non-zero so the caller can fall back to setup_github.sh.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

STATE_FILE = Path(__file__).parent / "repo_state.json"
API_DELAY = 0.3


def _gh(args: list[str], check: bool = True) -> str:
    r = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=60)
    if check and r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {r.stderr}")
    return r.stdout


def _gh_json(args: list[str]) -> Any:
    out = _gh(args).strip()
    return json.loads(out) if out else None


def _gh_api(path: str, method: str = "GET", fields: dict | None = None) -> Any:
    args = ["api", path, "--method", method]
    if fields:
        for k, v in fields.items():
            args += ["-f", f"{k}={v}"]
    out = _gh(args, check=False).strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out


def fetch_state(repo: str) -> dict:
    """Fetch current GitHub state for the repo."""
    logger.info("Fetching state for %s...", repo)

    labels = _gh_json([
        "label", "list", "--repo", repo, "--limit", "200",
        "--json", "name,color,description",
    ]) or []

    milestones = _gh_api(f"repos/{repo}/milestones?state=all&per_page=100") or []
    milestones = [
        {"number": m["number"], "title": m["title"], "state": m["state"],
         "description": m.get("description") or "", "due_on": m.get("due_on")}
        for m in milestones
    ]

    issues_raw = _gh_json([
        "issue", "list", "--repo", repo, "--state", "all", "--limit", "200",
        "--json", "number,title,state,labels,assignees,milestone,comments",
    ]) or []
    issues = []
    for i in issues_raw:
        issues.append({
            "number": i["number"],
            "title": i["title"],
            "state": i["state"].lower(),
            "labels": sorted([l["name"] for l in i.get("labels") or []]),
            "assignees": sorted([a["login"] for a in i.get("assignees") or []]),
            "milestone": (i.get("milestone") or {}).get("title"),
            "comment_count": len(i.get("comments") or []),
        })

    prs_raw = _gh_json([
        "pr", "list", "--repo", repo, "--state", "all", "--limit", "200",
        "--json", "number,title,state,isDraft,labels,assignees,milestone,headRefName,baseRefName",
    ]) or []
    prs = []
    for p in prs_raw:
        prs.append({
            "number": p["number"],
            "title": p["title"],
            "state": p["state"].lower(),
            "draft": p.get("isDraft", False),
            "labels": sorted([l["name"] for l in p.get("labels") or []]),
            "assignees": sorted([a["login"] for a in p.get("assignees") or []]),
            "milestone": (p.get("milestone") or {}).get("title"),
            "head": p["headRefName"],
            "base": p["baseRefName"],
        })

    return {"labels": labels, "milestones": milestones, "issues": issues, "prs": prs}


def snapshot(repo: str) -> None:
    state = fetch_state(repo)
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))
    logger.info("Snapshot written: %d labels, %d milestones, %d issues, %d PRs → %s",
                len(state["labels"]), len(state["milestones"]),
                len(state["issues"]), len(state["prs"]), STATE_FILE)


def _index_by(items: list[dict], key: str) -> dict:
    return {it[key]: it for it in items}


def compute_actions(expected: dict, actual: dict) -> tuple[list[dict], list[str]]:
    """Compute reconcile actions. Returns (actions, fallback_reasons)."""
    actions: list[dict] = []
    fallback: list[str] = []

    # ── Labels ──
    exp_labels = _index_by(expected["labels"], "name")
    act_labels = _index_by(actual["labels"], "name")
    for name, el in exp_labels.items():
        al = act_labels.get(name)
        if al is None:
            actions.append({"op": "label_create", "label": el})
        elif al.get("color") != el.get("color") or (al.get("description") or "") != (el.get("description") or ""):
            actions.append({"op": "label_update", "label": el})
    for name in act_labels:
        if name not in exp_labels:
            actions.append({"op": "label_delete", "name": name})

    # ── Milestones ──
    exp_ms = _index_by(expected["milestones"], "title")
    act_ms = _index_by(actual["milestones"], "title")
    for title, em in exp_ms.items():
        am = act_ms.get(title)
        if am is None:
            actions.append({"op": "milestone_create", "milestone": em})
        elif am["state"] != em["state"] or (am.get("description") or "") != (em.get("description") or ""):
            actions.append({"op": "milestone_update", "number": am["number"], "milestone": em})
    for title, am in act_ms.items():
        if title not in exp_ms:
            actions.append({"op": "milestone_delete", "number": am["number"]})

    # ── Issues ── (match by number — assumes snapshot has same number space)
    exp_issues = _index_by(expected["issues"], "number")
    act_issues = _index_by(actual["issues"], "number")
    for num, ei in exp_issues.items():
        ai = act_issues.get(num)
        if ai is None:
            fallback.append(f"issue #{num} ({ei['title']!r}) is missing — needs full setup")
            continue
        if ai["title"] != ei["title"]:
            fallback.append(f"issue #{num} title changed — needs full setup")
            continue
        if ai["state"] != ei["state"]:
            actions.append({"op": "issue_state", "number": num, "state": ei["state"]})
        if ai["labels"] != ei["labels"]:
            actions.append({"op": "issue_labels", "number": num,
                            "add": list(set(ei["labels"]) - set(ai["labels"])),
                            "remove": list(set(ai["labels"]) - set(ei["labels"]))})
        if ai["assignees"] != ei["assignees"]:
            actions.append({"op": "issue_assignees", "number": num,
                            "add": list(set(ei["assignees"]) - set(ai["assignees"])),
                            "remove": list(set(ai["assignees"]) - set(ei["assignees"]))})
        if ai["milestone"] != ei["milestone"]:
            actions.append({"op": "issue_milestone", "number": num, "milestone": ei["milestone"]})
        if ai["comment_count"] != ei["comment_count"]:
            # Comment drift is common (tasks add comments). We can't easily delete arbitrary
            # comments by ID without fetching them. Flag for fallback if extras exist.
            if ai["comment_count"] > ei["comment_count"]:
                fallback.append(f"issue #{num} has {ai['comment_count']} comments, expected {ei['comment_count']} — needs full setup")
            elif ai["comment_count"] < ei["comment_count"]:
                fallback.append(f"issue #{num} missing comments — needs full setup")

    # Extra issues (created by tasks)
    for num, ai in act_issues.items():
        if num not in exp_issues:
            actions.append({"op": "issue_delete", "number": num, "node_id_lookup": True})

    # ── PRs ──
    exp_prs = _index_by(expected["prs"], "number")
    act_prs = _index_by(actual["prs"], "number")
    for num, ep in exp_prs.items():
        ap = act_prs.get(num)
        if ap is None:
            fallback.append(f"PR #{num} missing — needs full setup")
            continue
        if ap["state"] == "merged" and ep["state"] != "merged":
            fallback.append(f"PR #{num} was merged — can't unmerge, needs full setup")
            continue
        if ap["state"] != ep["state"]:
            actions.append({"op": "pr_state", "number": num, "state": ep["state"]})
        if ap["draft"] != ep["draft"]:
            actions.append({"op": "pr_draft", "number": num, "draft": ep["draft"]})
        if ap["labels"] != ep["labels"]:
            actions.append({"op": "pr_labels", "number": num,
                            "add": list(set(ep["labels"]) - set(ap["labels"])),
                            "remove": list(set(ap["labels"]) - set(ep["labels"]))})
        if ap["assignees"] != ep["assignees"]:
            actions.append({"op": "pr_assignees", "number": num,
                            "add": list(set(ep["assignees"]) - set(ap["assignees"])),
                            "remove": list(set(ap["assignees"]) - set(ep["assignees"]))})
        if ap["milestone"] != ep["milestone"]:
            actions.append({"op": "pr_milestone", "number": num, "milestone": ep["milestone"]})

    for num, ap in act_prs.items():
        if num not in exp_prs:
            actions.append({"op": "pr_close", "number": num})

    return actions, fallback


def apply_actions(repo: str, actions: list[dict]) -> None:
    """Execute reconcile actions against the repo."""
    for a in actions:
        op = a["op"]
        try:
            if op == "label_create":
                l = a["label"]
                _gh(["label", "create", l["name"], "--repo", repo,
                     "--color", l["color"], "--description", l.get("description") or ""])
            elif op == "label_update":
                l = a["label"]
                _gh(["label", "edit", l["name"], "--repo", repo,
                     "--color", l["color"], "--description", l.get("description") or ""])
            elif op == "label_delete":
                _gh(["label", "delete", a["name"], "--repo", repo, "--yes"])
            elif op == "milestone_create":
                m = a["milestone"]
                fields = {"title": m["title"], "state": m["state"], "description": m.get("description") or ""}
                if m.get("due_on"):
                    fields["due_on"] = m["due_on"]
                _gh_api(f"repos/{repo}/milestones", method="POST", fields=fields)
            elif op == "milestone_update":
                m = a["milestone"]
                _gh_api(f"repos/{repo}/milestones/{a['number']}", method="PATCH",
                        fields={"state": m["state"], "description": m.get("description") or ""})
            elif op == "milestone_delete":
                _gh_api(f"repos/{repo}/milestones/{a['number']}", method="DELETE")
            elif op == "issue_state":
                cmd = "reopen" if a["state"] == "open" else "close"
                _gh(["issue", cmd, str(a["number"]), "--repo", repo])
            elif op == "issue_labels":
                if a["add"]:
                    _gh(["issue", "edit", str(a["number"]), "--repo", repo,
                         "--add-label", ",".join(a["add"])])
                if a["remove"]:
                    # check=False: label may already be gone (e.g., deleted via label_delete)
                    _gh(["issue", "edit", str(a["number"]), "--repo", repo,
                         "--remove-label", ",".join(a["remove"])], check=False)
            elif op == "issue_assignees":
                if a["add"]:
                    _gh(["issue", "edit", str(a["number"]), "--repo", repo,
                         "--add-assignee", ",".join(a["add"])], check=False)
                if a["remove"]:
                    _gh(["issue", "edit", str(a["number"]), "--repo", repo,
                         "--remove-assignee", ",".join(a["remove"])], check=False)
            elif op == "issue_milestone":
                if a["milestone"]:
                    _gh(["issue", "edit", str(a["number"]), "--repo", repo,
                         "--milestone", a["milestone"]])
                else:
                    _gh(["issue", "edit", str(a["number"]), "--repo", repo,
                         "--remove-milestone"])
            elif op == "issue_delete":
                node = _gh_json(["issue", "view", str(a["number"]), "--repo", repo, "--json", "id"])
                if node and node.get("id"):
                    _gh(["api", "graphql", "-f",
                         f'query=mutation {{ deleteIssue(input: {{issueId: "{node["id"]}"}}) {{ clientMutationId }} }}'])
            elif op == "pr_state":
                if a["state"] == "open":
                    _gh(["pr", "reopen", str(a["number"]), "--repo", repo])
                else:
                    _gh(["pr", "close", str(a["number"]), "--repo", repo])
            elif op == "pr_draft":
                if a["draft"]:
                    _gh(["pr", "ready", str(a["number"]), "--repo", repo, "--undo"])
                else:
                    _gh(["pr", "ready", str(a["number"]), "--repo", repo])
            elif op == "pr_labels":
                if a["add"]:
                    _gh(["pr", "edit", str(a["number"]), "--repo", repo,
                         "--add-label", ",".join(a["add"])])
                if a["remove"]:
                    _gh(["pr", "edit", str(a["number"]), "--repo", repo,
                         "--remove-label", ",".join(a["remove"])], check=False)
            elif op == "pr_assignees":
                if a["add"]:
                    _gh(["pr", "edit", str(a["number"]), "--repo", repo,
                         "--add-assignee", ",".join(a["add"])], check=False)
                if a["remove"]:
                    _gh(["pr", "edit", str(a["number"]), "--repo", repo,
                         "--remove-assignee", ",".join(a["remove"])], check=False)
            elif op == "pr_milestone":
                if a["milestone"]:
                    _gh(["pr", "edit", str(a["number"]), "--repo", repo,
                         "--milestone", a["milestone"]])
                else:
                    _gh(["pr", "edit", str(a["number"]), "--repo", repo,
                         "--remove-milestone"])
            elif op == "pr_close":
                _gh(["pr", "close", str(a["number"]), "--repo", repo], check=False)
            else:
                logger.warning("Unknown op: %s", op)
            time.sleep(API_DELAY)
        except Exception as e:
            logger.error("Action failed (%s): %s", op, e)
            raise


def reconcile(repo: str) -> int:
    if not STATE_FILE.exists():
        logger.error("No snapshot at %s — run `snapshot` first", STATE_FILE)
        return 2
    expected = json.loads(STATE_FILE.read_text())
    actual = fetch_state(repo)
    actions, fallback = compute_actions(expected, actual)

    if fallback:
        logger.warning("Reconcile not possible — %d structural diffs:", len(fallback))
        for f in fallback:
            logger.warning("  %s", f)
        return 2

    if not actions:
        logger.info("Repo state matches snapshot — no actions needed.")
        return 0

    logger.info("Applying %d reconcile actions...", len(actions))
    for a in actions:
        logger.info("  %s", {k: v for k, v in a.items() if k != "label" and k != "milestone"})
    apply_actions(repo, actions)
    logger.info("Reconcile complete.")
    return 0


def diff(repo: str) -> int:
    if not STATE_FILE.exists():
        logger.error("No snapshot")
        return 2
    expected = json.loads(STATE_FILE.read_text())
    actual = fetch_state(repo)
    actions, fallback = compute_actions(expected, actual)
    print(json.dumps({"actions": actions, "fallback": fallback}, indent=2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cmd, repo = sys.argv[1], sys.argv[2]
    if cmd == "snapshot":
        snapshot(repo)
    elif cmd == "reconcile":
        sys.exit(reconcile(repo))
    elif cmd == "diff":
        sys.exit(diff(repo))
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
