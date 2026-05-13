"""Evaluators for scoring agent outputs against expected results.

Each evaluator returns an arize.experiments.EvaluationResult.
"""

import json
import logging
import os
import re
import subprocess
import time

from arize.experiments import EvaluationResult

logger = logging.getLogger(__name__)


def _anthropic_messages_create_with_retry(client, **kwargs):
    """Retry transient Anthropic overloads during judge calls."""
    last_exc = None
    for attempt in range(4):
        try:
            return client.messages.create(**kwargs)
        except Exception as exc:  # Anthropic may wrap 529s by SDK version.
            last_exc = exc
            text = str(exc).lower()
            if "529" not in text and "overloaded" not in text:
                raise
            sleep_s = 2 ** attempt
            logger.warning("Judge call overloaded; retrying in %ss", sleep_s)
            time.sleep(sleep_s)
    raise last_exc


def evaluate_task(output: str, task: dict) -> EvaluationResult:
    """Route to the correct evaluator based on task expected_output type."""
    expected = task.get("expected_output", {})
    eval_type = expected.get("type", "")

    if eval_type == "exact":
        return exact_match(output, expected)
    elif eval_type == "structured":
        return structured_match(output, expected)
    elif eval_type == "count_by_label":
        return count_by_label_match(output, expected)
    elif eval_type == "state_check":
        return state_check(output, expected, task)
    elif eval_type == "llm_judge":
        return llm_judge(output, expected)
    elif eval_type == "approximate":
        return approximate_match(output, expected)
    else:
        return EvaluationResult(
            score=0, label="unknown_type",
            explanation=f"Unknown expected_output type: {eval_type}",
        )


def exact_match(output: str, expected: dict) -> EvaluationResult:
    """Compare agent output to an exact expected value."""
    value = expected["value"]
    output_lower = output.lower().strip()

    if isinstance(value, list):
        # Check all items appear in the output
        found = [str(v).lower() in output_lower for v in value]
        score = sum(found) / len(found) if found else 0
        label = "correct" if all(found) else "partial" if any(found) else "incorrect"
        missing = [str(v) for v, f in zip(value, found) if not f]
        explanation = f"Expected {value}. Missing: {missing}" if missing else "All values found."
    elif isinstance(value, (int, float)):
        # Look for the number in the output
        numbers = re.findall(r'\b' + str(value) + r'\b', output)
        score = 1.0 if numbers else 0.0
        label = "correct" if numbers else "incorrect"
        explanation = f"Expected {value}. {'Found' if numbers else 'Not found'} in output."
    else:
        # String comparison
        str_value = str(value).lower()
        score = 1.0 if str_value in output_lower else 0.0
        label = "correct" if score == 1.0 else "incorrect"
        explanation = f"Expected '{value}'. {'Found' if score else 'Not found'} in output."

    return EvaluationResult(score=score, label=label, explanation=explanation)


def approximate_match(output: str, expected: dict) -> EvaluationResult:
    """For approximate-type tasks, do a best-effort keyword check."""
    value = expected.get("value", "")
    explanation = expected.get("explanation", "")

    # Extract key terms from the expected value/explanation
    if isinstance(value, dict):
        key_terms = list(value.values())
    elif isinstance(value, str):
        key_terms = [value]
    else:
        key_terms = [str(value)]

    output_lower = output.lower()
    found = sum(1 for term in key_terms if str(term).lower() in output_lower)
    total = max(len(key_terms), 1)
    score = found / total

    return EvaluationResult(
        score=score,
        label="approximate" if score > 0.5 else "mismatch",
        explanation=f"Matched {found}/{total} key terms. Expected: {value}",
    )


def structured_match(output: str, expected: dict) -> EvaluationResult:
    """Check that expected structured fields appear in the output."""
    value = expected["value"]
    output_lower = output.lower()
    checks_passed = 0
    checks_total = 0
    details = []

    def check_value(key, val):
        nonlocal checks_passed, checks_total
        checks_total += 1
        str_val = str(val).lower()
        if str_val in output_lower:
            checks_passed += 1
            details.append(f"  [pass] {key}: {val}")
        else:
            details.append(f"  [fail] {key}: {val}")

    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, list):
                for item in v:
                    check_value(k, item)
            else:
                check_value(k, v)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                for k, v in item.items():
                    if isinstance(v, list):
                        for sub in v:
                            check_value(k, sub)
                    else:
                        check_value(k, v)
            else:
                check_value("item", item)

    score = checks_passed / checks_total if checks_total > 0 else 0
    label = "correct" if score >= 0.8 else "partial" if score > 0.3 else "incorrect"

    return EvaluationResult(
        score=score, label=label,
        explanation=f"Matched {checks_passed}/{checks_total} fields.\n" + "\n".join(details),
    )


def count_by_label_match(output: str, expected: dict) -> EvaluationResult:
    """Check labeled counts, allowing either 'open: 4' or '4 open' wording."""
    value = expected["value"]
    output_lower = output.lower()
    checks_passed = 0
    details = []

    for label, count in value.items():
        label_text = re.escape(str(label).lower())
        count_text = re.escape(str(count).lower())
        patterns = [
            rf"\b{label_text}\b[^\d,;\n]{{0,30}}\b{count_text}\b",
            rf"\b{count_text}\b[^a-z0-9,;\n]{{0,30}}\b{label_text}\b",
        ]
        matched = any(re.search(pattern, output_lower) for pattern in patterns)
        if matched:
            checks_passed += 1
            details.append(f"  [pass] {label}: {count}")
        else:
            details.append(f"  [fail] {label}: {count}")

    checks_total = len(value)
    score = checks_passed / checks_total if checks_total else 0
    label = "correct" if score == 1.0 else "partial" if score > 0 else "incorrect"
    return EvaluationResult(
        score=score,
        label=label,
        explanation=f"Matched {checks_passed}/{checks_total} labeled counts.\n" + "\n".join(details),
    )


def state_check(output: str, expected: dict, task: dict) -> EvaluationResult:
    """Verify repo state after a write operation using gh CLI."""
    checks = expected.get("checks", [])
    repo = os.environ.get("EVAL_REPO", "")
    passed = 0
    details = []
    # Track issue number resolved from title so subsequent checks can use it
    resolved_issue_num = None

    task_desc = task.get("description", "")

    for check_desc in checks:
        result, resolved_num = _verify_state_check(check_desc, repo, resolved_issue_num, task_desc)
        if resolved_num:
            resolved_issue_num = resolved_num
        if result:
            passed += 1
            details.append(f"  [pass] {check_desc}")
        else:
            details.append(f"  [fail] {check_desc}")

    total = max(len(checks), 1)
    score = passed / total
    label = "correct" if score == 1.0 else "partial" if score > 0 else "incorrect"

    return EvaluationResult(
        score=score, label=label,
        explanation=f"State checks: {passed}/{total} passed.\n" + "\n".join(details),
    )


def _verify_state_check(check_desc: str, repo: str, prev_issue_num: int | None = None, task_desc: str = "") -> tuple[bool, int | None]:
    """Run a gh CLI command to verify a single state check.

    Returns (passed, resolved_issue_number) — the resolved number is passed
    to subsequent checks so they can reference the same issue.
    """
    desc_lower = check_desc.lower()

    def _resolve_issue() -> int | None:
        """Find the issue number from the check, title search, or previous context."""
        return (_extract_issue_number(check_desc)
                or _find_issue_by_title(check_desc, repo)
                or prev_issue_num)

    try:
        # --- Issue state: "Issue #N is closed" / "Issue is open" ---
        if "is closed" in desc_lower:
            num = _resolve_issue()
            if num:
                return _gh_issue_field(num, repo, "state") == "CLOSED", num

        if "is open" in desc_lower and "issue" in desc_lower:
            num = _resolve_issue()
            if num:
                return _gh_issue_field(num, repo, "state") == "OPEN", num

        # --- Issue has label: "Issue #N has the 'foo' label" ---
        if "has the" in desc_lower and "label" in desc_lower:
            num = _resolve_issue()
            label_match = re.search(r"'([^']+)'\s*label", check_desc)
            if num and label_match:
                label = label_match.group(1)
                result = _run_gh(
                    ["gh", "issue", "view", str(num), "--repo", repo,
                     "--json", "labels", "--jq", "[.labels[].name] | join(\",\")"])
                return label in result, num

        # --- Issue is in milestone: "Issue is in the v2.0 milestone" ---
        if "milestone" in desc_lower and "in the" in desc_lower:
            num = _resolve_issue()
            ms_match = re.search(r"in the (\S+) milestone", check_desc, re.IGNORECASE)
            if num and ms_match:
                ms_name = ms_match.group(1)
                result = _run_gh(
                    ["gh", "issue", "view", str(num), "--repo", repo,
                     "--json", "milestone", "--jq", ".milestone.title"])
                return result.strip() == ms_name, num

        # --- Label exists (with optional color/description) ---
        if "label" in desc_lower and "exists" in desc_lower:
            label_match = re.search(r"'([^']+)'", check_desc)
            if label_match:
                label = label_match.group(1)
                result = _run_gh(
                    ["gh", "label", "list", "--repo", repo,
                     "--json", "name,color,description"])
                labels = json.loads(result) if result.strip() else []
                found = next((l for l in labels if l["name"] == label), None)
                if not found:
                    return False, None
                # If check mentions a color, verify it
                color_match = re.search(r"color\s+#?([0-9a-fA-F]{6})", check_desc, re.IGNORECASE)
                if color_match:
                    return found.get("color", "").lower() == color_match.group(1).lower(), None
                return True, None

        # --- Label has description ---
        if "label has description" in desc_lower or ("description" in desc_lower and "label" in desc_lower):
            desc_match = re.search(r"description '([^']+)'", check_desc)
            if desc_match:
                expected_desc = desc_match.group(1)
                result = _run_gh(
                    ["gh", "label", "list", "--repo", repo,
                     "--json", "name,description"])
                labels = json.loads(result) if result.strip() else []
                return any(l.get("description") == expected_desc for l in labels), None

        # --- PR exists from branch ---
        if desc_lower.startswith("a pr exists") or desc_lower.startswith("pr exists"):
            branch_match = re.search(r"from (\S+)", check_desc)
            if branch_match:
                branch = branch_match.group(1)
                result = _run_gh(
                    ["gh", "pr", "list", "--repo", repo, "--head", branch,
                     "--state", "open", "--json", "number", "--jq", "length"])
                return int(result.strip() or "0") > 0, None
            num = _extract_pr_number(check_desc)
            if num:
                return _gh_pr_field(num, repo, "state") == "OPEN", None

        # --- PR has review/comment containing text ---
        if desc_lower.startswith("pr") and ("has a review" in desc_lower or "has a comment" in desc_lower or "comment containing" in desc_lower):
            num = _extract_pr_number(check_desc)
            if num:
                # Check PR reviews
                reviews = _run_gh(
                    ["gh", "api", f"repos/{repo}/pulls/{num}/reviews",
                     "--jq", ".[].body"])
                # Check PR comments
                comments = _run_gh(
                    ["gh", "api", f"repos/{repo}/issues/{num}/comments",
                     "--jq", ".[].body"])
                all_text = (reviews + "\n" + comments).lower()
                # Check for specified text content
                text_match = re.search(r"containing[^']*'([^']+)'", check_desc)
                if text_match:
                    return text_match.group(1).lower() in all_text, None
                # Or just check for "the specified text" — look for key phrase
                text_match = re.search(r"saying '([^']+)'", check_desc)
                if text_match:
                    return text_match.group(1).lower() in all_text, None
                # "the specified text" — pull from task description
                if "the specified text" in desc_lower and task_desc:
                    td_match = re.search(r"saying '([^']+)'", task_desc)
                    if td_match:
                        return td_match.group(1).lower() in all_text, None
                # Fallback: just check that reviews/comments exist
                return bool(reviews.strip()) or bool(comments.strip()), None

        # --- PR body/title references issue ---
        if desc_lower.startswith("pr") and "references" in desc_lower:
            num_match = re.search(r"issue #(\d+)", check_desc, re.IGNORECASE)
            branch_match = re.search(r"from (\S+)", check_desc)
            if num_match:
                issue_num = num_match.group(1)
                # Find the PR - try by branch first, then by number
                pr_json = ""
                if branch_match:
                    pr_json = _run_gh(
                        ["gh", "pr", "list", "--repo", repo,
                         "--head", branch_match.group(1), "--state", "open",
                         "--json", "title,body", "--jq", ".[0] | .title + \" \" + .body"])
                if not pr_json.strip():
                    pr_num = _extract_pr_number(check_desc)
                    if pr_num:
                        pr_json = _run_gh(
                            ["gh", "pr", "view", str(pr_num), "--repo", repo,
                             "--json", "title,body", "--jq", '.title + " " + .body'])
                return f"#{issue_num}" in pr_json, None

        # --- Issue exists with title ---
        if "issue exists" in desc_lower:
            title_match = re.search(r"'([^']+)'", check_desc)
            if title_match:
                title = title_match.group(1)
                # Also resolve the issue number for subsequent checks
                num = _find_issue_by_title(check_desc, repo)
                result = _run_gh(
                    ["gh", "issue", "list", "--repo", repo, "--state", "all",
                     "--search", title, "--json", "title", "--jq", ".[].title"])
                return title in result, num

        # --- Issue has a comment (with optional content check) ---
        if "has a comment" in desc_lower:
            num = _extract_issue_number(check_desc)
            if num:
                comments = _run_gh(
                    ["gh", "issue", "view", str(num), "--repo", repo,
                     "--json", "comments", "--jq", "[.comments[].body] | join(\"\\n\")"])
                if not comments.strip():
                    return False, None
                # If the check specifies content to look for
                if "referencing" in desc_lower and "pr" in desc_lower:
                    return bool(re.search(r"PR\s*#?\d+|#\d+", comments)), None
                return True, None

        # --- Branch exists on remote ---
        if "branch" in desc_lower and "exists" in desc_lower:
            branch_match = re.search(r"(?:branch\s+)?(\S+)\s+exists", check_desc, re.IGNORECASE)
            if branch_match:
                branch = branch_match.group(1)
                result = _run_gh(
                    ["git", "ls-remote", "--heads", "origin", branch])
                return branch in result, None

        # --- File content on a branch ---
        if "on that branch has" in desc_lower or ("branch" in desc_lower and "has '" in desc_lower):
            # e.g., "README.md on that branch has 'receive' (not 'recieve')"
            file_match = re.search(r"(\S+\.(?:md|py|yml|json|txt))\s+on that branch has\s+'([^']+)'", check_desc, re.IGNORECASE)
            if file_match:
                filename = file_match.group(1)
                expected_text = file_match.group(2)
                # Check the fix/readme-typo branch specifically (from T13 context)
                for b in ["fix/readme-typo"]:
                    content = _run_gh(
                        ["gh", "api", f"repos/{repo}/contents/{filename}?ref={b}",
                         "--jq", ".content"])
                    if content.strip():
                        import base64
                        try:
                            decoded = base64.b64decode(content.strip()).decode()
                            if expected_text in decoded:
                                return True, None
                        except Exception:
                            pass
                return False, None

        # --- Commit message check ---
        if "commit message" in desc_lower:
            # e.g., "Commit message is descriptive"
            # Check fix/readme-typo branch for its latest commit message
            for b in ["origin/fix/readme-typo"]:
                msg = _run_gh(
                    ["git", "log", b, "-1", "--format=%s"])
                if msg.strip() and len(msg.strip()) > 10:
                    return True, None
            return False, None

    except (subprocess.TimeoutExpired, ValueError, json.JSONDecodeError) as e:
        logger.warning("State check error for '%s': %s", check_desc, e)

    logger.warning("Could not verify state check: %s", check_desc)
    return False, None


def _run_gh(cmd: list[str], timeout: int = 15) -> str:
    """Run a gh/git command and return stdout."""
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.stdout.strip()


def _gh_issue_field(num: int, repo: str, field: str) -> str:
    """Get a single field from an issue."""
    return _run_gh(
        ["gh", "issue", "view", str(num), "--repo", repo,
         "--json", field, "--jq", f".{field}"]).upper()


def _gh_pr_field(num: int, repo: str, field: str) -> str:
    """Get a single field from a PR."""
    return _run_gh(
        ["gh", "pr", "view", str(num), "--repo", repo,
         "--json", field, "--jq", f".{field}"]).upper()


def _find_issue_by_title(check_desc: str, repo: str) -> int | None:
    """Find an issue number by searching for a title in the task context.

    Used when the check description doesn't contain an issue number.
    Searches for the most recently created open issue matching the task's title.
    """
    title_match = re.search(r"'([^']+)'", check_desc)
    if not title_match:
        return None
    title = title_match.group(1)
    result = _run_gh(
        ["gh", "issue", "list", "--repo", repo, "--state", "all",
         "--search", title, "--json", "number,title", "--limit", "5"])
    try:
        issues = json.loads(result) if result.strip() else []
        for issue in issues:
            if issue.get("title") == title:
                return issue["number"]
    except json.JSONDecodeError:
        pass
    return None


def _extract_issue_number(text: str) -> int | None:
    match = re.search(r'#(\d+)', text)
    return int(match.group(1)) if match else None


def _extract_pr_number(text: str) -> int | None:
    match = re.search(r'PR\s*#(\d+)', text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def judge_output_quality(output: str, task: dict) -> EvaluationResult:
    """LLM-as-judge scoring completeness, accuracy, and organization for tier 4 tasks.

    Returns a normalized 0-1 score based on three dimensions:
    - Completeness: Did it include all expected items?
    - Accuracy: Are the facts correct, no hallucinations?
    - Organization: Is the output well-structured and usable?
    """
    from anthropic import Anthropic

    description = task.get("description", "")
    criteria = task.get("expected_output", {}).get("criteria", [])
    criteria_text = "\n".join(f"- {c}" for c in criteria)

    judge_model = os.environ.get("JUDGE_MODEL", "claude-opus-4-7")
    client = Anthropic()
    response = _anthropic_messages_create_with_retry(
        client,
        model=judge_model,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": f"""You are evaluating the quality of an AI agent's analysis output.

## Task
{description}

## Agent Output
{output}

## Reference Criteria
{criteria_text}

## Instructions
Score the output on three dimensions (each 1-5):

1. **Completeness**: Does the output cover all the items and aspects mentioned in the criteria? Are there gaps?
2. **Accuracy**: Are the stated facts correct? Is there any hallucinated or fabricated content?
3. **Organization**: Is the output well-structured, clear, and directly usable? Or is it rambling/disorganized?

Respond in JSON:
{{"completeness": <1-5>, "accuracy": <1-5>, "organization": <1-5>, "explanation": "<brief summary>"}}"""
        }],
    )

    try:
        response_text = response.content[0].text
        json_match = re.search(r'\{[^{}]*"completeness"[^{}]*\}', response_text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            comp = result.get("completeness", 0)
            acc = result.get("accuracy", 0)
            org = result.get("organization", 0)
            avg = (comp + acc + org) / 3.0
            normalized = avg / 5.0
            label = "good" if avg >= 4 else "fair" if avg >= 2.5 else "poor"
            return EvaluationResult(
                score=normalized, label=label,
                explanation=f"completeness={comp}/5, accuracy={acc}/5, organization={org}/5. {result.get('explanation', '')}",
            )
    except (json.JSONDecodeError, IndexError, KeyError):
        pass

    return EvaluationResult(
        score=0, label="judge_error",
        explanation=f"Failed to parse quality judge response: {response.content[0].text[:200]}",
    )


def llm_judge(output: str, expected: dict) -> EvaluationResult:
    """Use a fixed judge model to score output against criteria."""
    from anthropic import Anthropic

    criteria = expected.get("criteria", [])
    criteria_text = "\n".join(f"- {c}" for c in criteria)

    judge_model = os.environ.get("JUDGE_MODEL", "claude-opus-4-7")
    client = Anthropic()
    response = _anthropic_messages_create_with_retry(
        client,
        model=judge_model,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": f"""You are an evaluation judge. Score the following agent output against the criteria below.

## Agent Output
{output}

## Criteria
{criteria_text}

## Instructions
For each criterion, determine if the output meets it (yes/no).
Then provide an overall score from 0 to 5:
- 5: All criteria fully met
- 4: Most criteria met, minor gaps
- 3: About half the criteria met
- 2: Some criteria met but significant gaps
- 1: Very few criteria met
- 0: No criteria met or output is irrelevant

Respond in JSON format:
{{"score": <0-5>, "met": [<criteria that were met>], "missed": [<criteria that were missed>], "explanation": "<brief explanation>"}}"""
        }],
    )

    try:
        response_text = response.content[0].text
        # Extract JSON from response (may be wrapped in markdown code blocks)
        json_match = re.search(r'\{[^{}]*"score"[^{}]*\}', response_text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            raw_score = result.get("score", 0)
            normalized = raw_score / 5.0  # Normalize to 0-1
            label = "good" if raw_score >= 4 else "fair" if raw_score >= 2 else "poor"
            return EvaluationResult(
                score=normalized, label=label,
                explanation=result.get("explanation", response_text),
            )
    except (json.JSONDecodeError, IndexError, KeyError):
        pass

    return EvaluationResult(
        score=0, label="judge_error",
        explanation=f"Failed to parse LLM judge response: {response_text[:200]}",
    )
