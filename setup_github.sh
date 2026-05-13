#!/usr/bin/env bash
#
# setup_github.sh — Creates GitHub metadata (labels, milestones, issues, PRs)
# for the Acme Agent Evals fixture repo.
#
# Prerequisites:
#   - gh CLI installed and authenticated
#   - Repository already pushed to GitHub
#
# Usage:
#   ./setup_github.sh <owner/repo>
#
# Example:
#   ./setup_github.sh myorg/acme-agent-evals

set -euo pipefail

REPO="${1:?Usage: ./setup_github.sh <owner/repo>}"

# Delay between GitHub API mutation calls to avoid secondary rate limits
# (~80 mutations/minute allowed; 0.5s gives us headroom)
API_DELAY=0.5

echo "Setting up GitHub metadata for ${REPO}..."
echo ""

# ── Cleanup from previous runs ──────────────────────────────────────────
#
# Uses the GraphQL deleteIssue mutation to truly remove issues and PRs,
# not just close them. This keeps the repo clean for repeated eval runs.

echo "==> Cleaning up previous run state..."

# Helper: delete an issue/PR by its GraphQL node ID
delete_issue_by_node_id() {
  local node_id="$1"
  gh api graphql -f query="mutation { deleteIssue(input: {issueId: \"${node_id}\"}) { clientMutationId } }" 2>/dev/null || true
}

# Close all open PRs and delete their branches
echo "  Closing open PRs..."
gh pr list --repo "$REPO" --state open --json number,headRefName --jq '.[] | "\(.number) \(.headRefName)"' 2>/dev/null | while read -r pr_num branch; do
  gh pr close "$pr_num" --repo "$REPO" --delete-branch 2>/dev/null || true
  echo "    Closed PR #${pr_num} (branch: ${branch})"
  sleep "$API_DELAY"
done
# Note: GitHub doesn't support deleting PRs via API (deleteIssue only works on issues).
# Closed PRs with deleted branches are harmless — they just consume issue numbers.

# Delete all open issues
echo "  Deleting open issues..."
gh issue list --repo "$REPO" --state open --limit 200 --json number,id --jq '.[] | "\(.number) \(.id)"' 2>/dev/null | while read -r issue_num node_id; do
  delete_issue_by_node_id "$node_id"
  echo "    Deleted issue #${issue_num}"
  sleep "$API_DELAY"
done

# Delete all closed issues
echo "  Deleting closed issues..."
gh issue list --repo "$REPO" --state closed --limit 200 --json number,id --jq '.[] | "\(.number) \(.id)"' 2>/dev/null | while read -r issue_num node_id; do
  delete_issue_by_node_id "$node_id"
  echo "    Deleted closed issue #${issue_num}"
  sleep "$API_DELAY"
done

# Delete non-main branches
echo "  Cleaning branches..."
git checkout main 2>/dev/null || true
git fetch origin --prune 2>/dev/null || true
for branch in fix/batch-shutdown ci/add-python-3.12 feat/grpc-transport; do
  git branch -D "$branch" 2>/dev/null || true
  git push origin --delete "$branch" 2>/dev/null || true
done

# Delete all milestones (must include state=all to catch closed ones)
echo "  Deleting milestones..."
for state in open closed; do
  gh api "repos/$REPO/milestones?state=$state&per_page=100" --jq '.[].number' 2>/dev/null | while read -r ms_num; do
    gh api "repos/$REPO/milestones/$ms_num" --method DELETE 2>/dev/null || true
    echo "    Deleted milestone #${ms_num}"
  done
done

echo "  Cleanup done."
echo ""

# ── Labels ───────────────────────────────────────────────────────────────

echo "==> Creating labels..."

# Delete default labels that might conflict
for label in "bug" "enhancement" "documentation" "good first issue" "help wanted" "invalid" "question" "wontfix" "duplicate"; do
  gh label delete "$label" --repo "$REPO" --yes 2>/dev/null || true
  sleep "$API_DELAY"
done

for label_args in \
  "bug|d73a4a|Something isn't working" \
  "enhancement|a2eeef|New feature or request" \
  "docs|0075ca|Documentation improvements" \
  "good-first-issue|7057ff|Good for newcomers" \
  "critical|b60205|Critical priority" \
  "exporter|e4e669|Related to exporters" \
  "ci|ededed|CI/CD related" \
  "v2.0|5319e7|Targeted for v2.0 release" \
  "wontfix|ffffff|This will not be worked on"; do
  IFS='|' read -r name color desc <<< "$label_args"
  gh label create "$name" --repo "$REPO" --color "$color" --description "$desc" --force
  sleep "$API_DELAY"
done

echo "  Labels created."

# ── Milestones ───────────────────────────────────────────────────────────
#
# All milestones are created OPEN. We close v1.0.0 and v1.1.0 after all
# issues are assigned (see end of Open Issues section).

echo "==> Creating milestones..."

gh api repos/"$REPO"/milestones --method POST \
  -f title="v1.0.0" -f state="open" \
  -f description="Initial release of the Acme SDK" \
  -f due_on="2024-07-01T00:00:00Z" 2>/dev/null || true
sleep "$API_DELAY"

gh api repos/"$REPO"/milestones --method POST \
  -f title="v1.1.0" -f state="open" \
  -f description="Exporters, batch processing, and OAuth support" \
  -f due_on="2024-09-15T00:00:00Z" 2>/dev/null || true
sleep "$API_DELAY"

gh api repos/"$REPO"/milestones --method POST \
  -f title="v2.0" -f state="open" \
  -f description="Major feature release: gRPC, metrics, custom attributes" \
  -f due_on="2025-03-01T00:00:00Z" 2>/dev/null || true
sleep "$API_DELAY"

# Store milestone numbers for closing later
V1_NUM=$(gh api repos/"$REPO"/milestones --jq '.[] | select(.title=="v1.0.0") | .number' 2>/dev/null || echo "")
V11_NUM=$(gh api repos/"$REPO"/milestones --jq '.[] | select(.title=="v1.1.0") | .number' 2>/dev/null || echo "")

# Milestone titles for gh issue create --milestone
V1_MILESTONE="v1.0.0"
V11_MILESTONE="v1.1.0"
V2_MILESTONE="v2.0"

echo "  Created milestones: ${V1_MILESTONE}, ${V11_MILESTONE}, ${V2_MILESTONE}"

# ── Helper: create issue ────────────────────────────────────────────────

create_issue() {
  local title="$1"
  local body="$2"
  local labels="$3"
  local assignee="${4:-}"
  local milestone="${5:-}"

  local args=(--repo "$REPO" --title "$title" --body "$body" --label "$labels")
  if [[ -n "$milestone" ]]; then
    args+=(--milestone "$milestone")
  fi

  local url=""

  # Try with assignee first, fall back to without if the user doesn't exist
  if [[ -n "$assignee" ]]; then
    url=$(gh issue create "${args[@]}" --assignee "$assignee" 2>/dev/null) || \
    url=$(gh issue create "${args[@]}" 2>/dev/null) || true
  else
    url=$(gh issue create "${args[@]}" 2>/dev/null) || true
  fi

  sleep "$API_DELAY"
  echo "$url"
}

# ── Closed Issues (historical) ──────────────────────────────────────────

echo ""
echo "==> Creating closed issues (historical)..."

# Closed bugs
CLOSED1=$(create_issue \
  "Client throws unhandled exception on 401 response" \
  "When the API returns a 401 Unauthorized response, the client raises a raw httpx exception instead of an AuthenticationError.\n\n## Steps to reproduce\n1. Use an invalid API key\n2. Try to send spans\n3. Get raw httpx.HTTPStatusError instead of AuthenticationError" \
  "bug" "" "$V1_MILESTONE")
echo "  Created: $CLOSED1"

CLOSED2=$(create_issue \
  "Span duration_ms not computed when end_time is set" \
  "Setting end_time on a Span without explicitly setting duration_ms leaves duration_ms as None. The model should auto-compute it." \
  "bug" "" "$V1_MILESTONE")
echo "  Created: $CLOSED2"

CLOSED3=$(create_issue \
  "Retry logic doesn't handle connection refused errors" \
  "When the Acme endpoint is unreachable, the retry logic doesn't catch ConnectionRefusedError, causing immediate failure." \
  "bug" "" "$V1_MILESTONE")
echo "  Created: $CLOSED3"

CLOSED4=$(create_issue \
  "Config parser ignores ACME_TIMEOUT env var" \
  "AcmeConfig.from_env() doesn't parse ACME_TIMEOUT as a float, causing TypeError." \
  "bug" "" "$V11_MILESTONE")
echo "  Created: $CLOSED4"

CLOSED5=$(create_issue \
  "OTLP exporter silently drops spans over batch limit" \
  "When sending more than 1000 spans in a single export() call, spans beyond 1000 are silently dropped." \
  "bug,exporter" "" "$V11_MILESTONE")
echo "  Created: $CLOSED5"

# Closed enhancements
CLOSED6=$(create_issue \
  "Add JSON file exporter" \
  "We need a file-based exporter for local development and debugging that writes spans to JSON files." \
  "enhancement,exporter" "" "$V11_MILESTONE")
echo "  Created: $CLOSED6"

CLOSED7=$(create_issue \
  "Add console/stdout exporter for debugging" \
  "Developers need a way to see spans printed to the terminal during development." \
  "enhancement,exporter" "" "$V11_MILESTONE")
echo "  Created: $CLOSED7"

CLOSED8=$(create_issue \
  "Support batch processing for high-throughput workloads" \
  "For high-throughput applications, we need a batch processor that accumulates spans and flushes them periodically." \
  "enhancement" "" "$V11_MILESTONE")
echo "  Created: $CLOSED8"

CLOSED9=$(create_issue \
  "Add OAuth 2.0 authentication support" \
  "Enterprise customers need OAuth 2.0 client credentials auth in addition to API keys.\n\n## Requirements\n- Client credentials grant\n- Automatic token refresh\n- Token caching" \
  "enhancement" "" "$V11_MILESTONE")
echo "  Created: $CLOSED9"

# Close all the closed issues
echo "  Closing historical issues..."
for url in "$CLOSED1" "$CLOSED2" "$CLOSED3" "$CLOSED4" "$CLOSED5" "$CLOSED6" "$CLOSED7" "$CLOSED8" "$CLOSED9"; do
  issue_num=$(echo "$url" | grep -oE '[0-9]+$' || true)
  if [[ -n "$issue_num" ]]; then
    gh issue close "$issue_num" --repo "$REPO" --reason completed 2>/dev/null || true
    sleep "$API_DELAY"
  fi
done

# ── Open Issues ──────────────────────────────────────────────────────────

echo ""
echo "==> Creating open issues..."

# Helper to extract issue number from URL
extract_num() { echo "$1" | grep -oE '[0-9]+$' || true; }

# Issue 1 — OTLP exporter timeout
OTLP_URL=$(create_issue \
  "OTLP exporter fails silently on connection timeout" \
  "## Description\n\nWhen the Acme endpoint times out during an OTLP export, the exporter catches the exception and returns a success result with 0 exported spans. This is misleading because the caller thinks export succeeded.\n\n## Expected behavior\n\nThe ExportResult should indicate failure with an appropriate error message.\n\n## Steps to reproduce\n\n1. Configure client with a very short timeout (e.g., 0.001s)\n2. Export spans\n3. Observe that result.success is True but exported_count is 0\n\n## Environment\n- Python 3.11\n- acme-sdk 1.1.0\n- macOS 14.0" \
  "bug,exporter" "alice")
OTLP_ISSUE_NUM=$(extract_num "$OTLP_URL")
echo "  #${OTLP_ISSUE_NUM:-?}: OTLP exporter fails silently on connection timeout"

# Add comments to issue about OTLP timeout
if [[ -n "$OTLP_ISSUE_NUM" ]]; then
  gh issue comment "$OTLP_ISSUE_NUM" --repo "$REPO" --body "I can reproduce this. The issue is in the \`_send\` method — when \`httpx.TimeoutException\` is raised, it gets caught by the retry logic but after retries are exhausted, the exception isn't propagated to the ExportResult.\n\nLooking at \`otlp.py\` line 68-75, the try/except only catches \`HTTPStatusError\`, not \`TimeoutException\`." 2>/dev/null || true
  sleep "$API_DELAY"
  gh issue comment "$OTLP_ISSUE_NUM" --repo "$REPO" --body "Good catch. I think we need to handle \`httpx.TimeoutException\` separately in the OTLP exporter's export method, not just rely on the retry layer. The exporter should always surface transport errors in the ExportResult.\n\nI'll pick this up after the batch shutdown fix lands." 2>/dev/null || true
fi

# Issue 2 — Custom span attributes
ATTRS_URL=$(create_issue \
  "Add support for custom span attributes" \
  "## Feature Request\n\nUsers should be able to define custom span attributes with type validation and schema enforcement.\n\n## Motivation\n\nEnterprise customers need to ensure span attributes conform to their internal schemas. Currently attributes are untyped \`dict[str, Any]\`.\n\n## Proposed Solution\n\n1. Add an \`AttributeSchema\` class that defines allowed keys and types\n2. Add an optional \`schema\` parameter to \`Span.__init__\`\n3. Validate attributes on assignment when schema is set\n\n## Alternatives\n\n- Could use Pydantic validators but that's less flexible\n- Could do validation at export time instead of assignment time" \
  "enhancement" "" "$V2_MILESTONE")
ATTRS_ISSUE_NUM=$(extract_num "$ATTRS_URL")
echo "  #${ATTRS_ISSUE_NUM:-?}: Add support for custom span attributes"

# Issue 3 — OAuth docs incomplete
OAUTH_URL=$(create_issue \
  "Documentation for OAuth authentication is incomplete" \
  "The OAuth section in the docs only shows basic usage. Missing:\n\n- How to handle token refresh failures\n- Recommended scopes for different use cases\n- How to use with corporate identity providers (Okta, Azure AD)\n- Troubleshooting common OAuth errors\n\nThe getting-started.md and configuration.md pages need OAuth sections too." \
  "docs,good-first-issue" "bob")
OAUTH_ISSUE_NUM=$(extract_num "$OAUTH_URL")
echo "  #${OAUTH_ISSUE_NUM:-?}: Documentation for OAuth authentication is incomplete"

# Issue 4 — Batch exporter shutdown bug
BATCH_URL=$(create_issue \
  "Batch exporter drops last batch on shutdown" \
  "## Bug Report\n\nWhen the Python interpreter shuts down, the batch processor's atexit handler races with the daemon flush thread. The last batch of spans in the buffer is sometimes dropped because the thread is killed before it can flush.\n\n## Steps to reproduce\n\n\`\`\`python\nprocessor = BatchProcessor(export_fn=send, batch_size=100)\nfor span in generate_spans(150):  # 100 flush, 50 remain\n    processor.add(span)\n# Exit without explicit shutdown — last 50 spans lost\n\`\`\`\n\n## Expected\nAll 150 spans exported.\n\n## Actual\nOnly 100 spans exported. The remaining 50 are lost.\n\n## Root cause\nThe daemon thread is killed before the atexit handler runs." \
  "bug,critical" "alice")
BATCH_ISSUE_NUM=$(extract_num "$BATCH_URL")
echo "  #${BATCH_ISSUE_NUM:-?}: Batch exporter drops last batch on shutdown"

# Issue 5 — Retry config in constructor
RETRY_URL=$(create_issue \
  "Add retry configuration to client constructor" \
  "Currently retry behavior is configured with just \`max_retries\` on the client. Users should be able to pass a full \`RetryConfig\` to control:\n\n- Base delay\n- Max delay\n- Backoff multiplier\n- Jitter factor\n- Retryable status codes\n\n## API proposal\n\n\`\`\`python\nfrom acme_sdk.utils.retry import RetryConfig\n\nclient = AcmeClient(\n    api_key=\"...\",\n    retry_config=RetryConfig(\n        max_retries=5,\n        base_delay=1.0,\n        max_delay=60.0,\n    ),\n)\n\`\`\`" \
  "enhancement" "" "$V2_MILESTONE")
RETRY_ISSUE_NUM=$(extract_num "$RETRY_URL")
echo "  #${RETRY_ISSUE_NUM:-?}: Add retry configuration to client constructor"

# Issue 6 — Console exporter not configurable
CONSOLE_URL=$(create_issue \
  "Console exporter output is not configurable" \
  "The console exporter always prints the same format. Users should be able to:\n\n- Choose which fields to display\n- Set a custom format string\n- Filter spans by status (e.g., only show errors)\n- Set minimum duration threshold\n\nThis would make it more useful for debugging specific issues." \
  "enhancement,exporter")
CONSOLE_ISSUE_NUM=$(extract_num "$CONSOLE_URL")
echo "  #${CONSOLE_ISSUE_NUM:-?}: Console exporter output is not configurable"

# Issue 7 — README typo
TYPO_URL=$(create_issue \
  "Typo in README.md" \
  "In the Support section at the bottom of README.md:\n\n> When you recieve an error, please check the troubleshooting guide...\n\nShould be \"receive\"." \
  "docs,good-first-issue")
TYPO_ISSUE_NUM=$(extract_num "$TYPO_URL")
echo "  #${TYPO_ISSUE_NUM:-?}: Typo in README.md"

# Issue 8 — gRPC transport
GRPC_ISSUE_URL=$(create_issue \
  "Add gRPC transport option for OTLP exporter" \
  "## Feature Request\n\nThe OTLP exporter currently only supports HTTP transport. For latency-sensitive workloads, gRPC would be significantly more efficient.\n\n## Requirements\n\n- Support OTLP/gRPC as an alternative to OTLP/HTTP\n- Reuse the same exporter interface (\`export()\` method)\n- Optional dependency (grpcio) — don't require it for HTTP-only users\n- Support for gRPC metadata (auth headers)\n- TLS configuration\n\n## Technical notes\n\n- The \`opentelemetry-exporter-otlp-proto-grpc\` package provides the proto definitions\n- We already have the \`[grpc]\` extra in pyproject.toml\n- Should share the retry logic from \`utils/retry.py\`" \
  "enhancement,exporter" "dana" "$V2_MILESTONE")
GRPC_ISSUE_NUM=$(extract_num "$GRPC_ISSUE_URL")
echo "  #${GRPC_ISSUE_NUM:-?}: Add gRPC transport option for OTLP exporter"

# Issue 9 — Config parser crash
CONFIG_URL=$(create_issue \
  "Config file parser crashes on empty values" \
  "## Bug Report\n\nWhen a TOML config file has empty string values, the config parser crashes with a TypeError.\n\n\`\`\`toml\n[acme]\napi_key = \"\"\nendpoint = \"https://ingest.acme-sdk.dev\"\n\`\`\`\n\n\`\`\`\nTypeError: cannot parse '' as float\n\`\`\`\n\nThe issue is in \`_interpolate_env_vars\` — it tries to type-cast empty strings." \
  "bug")
CONFIG_ISSUE_NUM=$(extract_num "$CONFIG_URL")
echo "  #${CONFIG_ISSUE_NUM:-?}: Config file parser crashes on empty values"

# Issue 10 — CI doesn't run Python 3.12
CI_312_URL=$(create_issue \
  "CI workflow doesn't run on Python 3.12" \
  "The CI matrix only includes Python 3.9, 3.10, and 3.11. Python 3.12 was released in October 2023 and we should be testing against it.\n\nThe pyproject.toml already lists 3.12 in classifiers but CI doesn't test it." \
  "bug,ci" "charlie")
CI_312_ISSUE_NUM=$(extract_num "$CI_312_URL")
echo "  #${CI_312_ISSUE_NUM:-?}: CI workflow doesn't run on Python 3.12"

# Issue 11 — Metrics collection support
METRICS_URL=$(create_issue \
  "Add metrics collection support" \
  "## Feature Request\n\nWhile the SDK has a \`Metric\` model and can serialize/send metrics, there's no high-level API for collecting metrics from an application.\n\n## Proposed API\n\n\`\`\`python\nfrom acme_sdk.metrics import MetricsCollector, Counter, Histogram\n\ncollector = MetricsCollector(client=client)\n\n# Define metrics\nrequest_count = collector.counter(\n    name=\"http.request.count\",\n    description=\"Total HTTP requests\",\n    tags={\"service\": \"api\"},\n)\n\nrequest_duration = collector.histogram(\n    name=\"http.request.duration\",\n    description=\"Request duration in ms\",\n    buckets=[10, 50, 100, 250, 500, 1000],\n)\n\n# Record values\nrequest_count.increment()\nrequest_duration.record(42.5)\n\n# Auto-flush every 60 seconds\ncollector.start(flush_interval=60)\n\`\`\`\n\n## Requirements\n\n- Counter, Gauge, Histogram, Summary metric types\n- Thread-safe recording\n- Periodic background flush\n- Tags/labels support\n- Integration with existing exporters\n\nThis is a large feature that should be scoped carefully for v2.0." \
  "enhancement,v2.0" "" "$V2_MILESTONE")
METRICS_ISSUE_NUM=$(extract_num "$METRICS_URL")
echo "  #${METRICS_ISSUE_NUM:-?}: Add metrics collection support"

# Issue 12 — JSON unicode bug
UNICODE_URL=$(create_issue \
  "JSON file exporter doesn't handle unicode properly" \
  "## Bug Report\n\nWhen span attributes contain unicode characters (e.g., CJK characters, emoji), the JSON file exporter writes them as escaped sequences instead of actual unicode.\n\n\`\`\`python\nspan = Span(\n    name=\"process_request\",\n    service_name=\"api\",\n    attributes={\"user.name\": \"田中太郎\", \"status\": \"完了\"},\n)\nexporter.export([span])\n# File contains: {\"user.name\": \"\\u7530\\u4e2d\\u592a\\u90ce\"}\n# Should contain: {\"user.name\": \"田中太郎\"}\n\`\`\`\n\nThe \`json.dump\` call needs \`ensure_ascii=False\`." \
  "bug,exporter")
UNICODE_ISSUE_NUM=$(extract_num "$UNICODE_URL")
echo "  #${UNICODE_ISSUE_NUM:-?}: JSON file exporter doesn't handle unicode properly"

# ── Close historical milestones ──────────────────────────────────────────
# Now that all issues are created with milestone assignments, close v1.0.0 and v1.1.0.

echo ""
echo "==> Closing completed milestones..."
if [[ -n "${V1_NUM:-}" ]]; then
  gh api repos/"$REPO"/milestones/"$V1_NUM" --method PATCH -f state="closed" 2>/dev/null || true
  echo "  Closed milestone v1.0.0"
fi
if [[ -n "${V11_NUM:-}" ]]; then
  gh api repos/"$REPO"/milestones/"$V11_NUM" --method PATCH -f state="closed" 2>/dev/null || true
  echo "  Closed milestone v1.1.0"
fi

# ── Pull Requests ────────────────────────────────────────────────────────

echo ""
echo "==> Creating branches and pull requests..."

# Store the main branch SHA for restoration
MAIN_SHA=$(git rev-parse HEAD)

# Look up issue numbers before creating branches
BATCH_ISSUE=$(gh issue list --repo "$REPO" --search "Batch exporter drops" --json number --jq '.[0].number' 2>/dev/null || echo "")
CI_ISSUE=$(gh issue list --repo "$REPO" --search "CI workflow doesn't run on Python 3.12" --json number --jq '.[0].number' 2>/dev/null || echo "")
GRPC_ISSUE=$(gh issue list --repo "$REPO" --search "gRPC transport" --json number --jq '.[0].number' 2>/dev/null || echo "")

echo "  Issue refs: batch=#${BATCH_ISSUE:-?}, ci=#${CI_ISSUE:-?}, grpc=#${GRPC_ISSUE:-?}"

# PR #1: Fix batch exporter shutdown (Alice)
echo "  Creating fix/batch-shutdown branch..."
git checkout main 2>/dev/null || true
git branch -D fix/batch-shutdown 2>/dev/null || true
git checkout -b fix/batch-shutdown main
# Make a small change to batching.py to simulate the fix
cat >> src/acme_sdk/utils/batching.py << 'PYEOF'


def _ensure_flush_on_shutdown(processor: BatchProcessor) -> None:
    """Ensure final flush happens before interpreter exit.

    This is registered as an atexit handler and ensures that the
    background flush thread completes before the interpreter exits.
    """
    if not processor._shutdown:
        processor.shutdown(timeout=processor._flush_interval * 3)
PYEOF
git add src/acme_sdk/utils/batching.py

BATCH_REF=""
if [[ -n "$BATCH_ISSUE" ]]; then
  BATCH_REF="Closes #${BATCH_ISSUE}"
fi

GIT_AUTHOR_NAME="Alice Chen" GIT_AUTHOR_EMAIL="alice@acme-sdk.dev" \
GIT_COMMITTER_NAME="Alice Chen" GIT_COMMITTER_EMAIL="alice@acme-sdk.dev" \
git commit -m "fix: ensure batch processor flushes on interpreter shutdown

Adds a more robust atexit handler that waits for the flush thread
to complete. This fixes the race condition where the daemon thread
is killed before the final flush.

${BATCH_REF}"

git push origin fix/batch-shutdown

PR1_URL=$(gh pr create --repo "$REPO" \
  --base main --head fix/batch-shutdown \
  --title "Fix batch exporter shutdown race condition" \
  --body "## Summary
- Adds a more robust atexit handler for BatchProcessor
- Ensures the flush thread completes before interpreter exit
- Fixes the race condition where the last batch is dropped

${BATCH_REF}

## Testing
- [x] Added regression test for shutdown flush
- [x] All existing tests pass
- [x] Manual testing with atexit scenario")

echo "  Created PR: $PR1_URL"

# Add a review comment to PR #1
# NOTE: Cannot create an APPROVE review on your own PR. Using a COMMENT review instead.
# In a real multi-user setup, this would be an approval from Bob.
PR1_NUM=$(echo "$PR1_URL" | grep -oE '[0-9]+$' || true)
if [[ -n "$PR1_NUM" ]]; then
  gh api repos/"$REPO"/pulls/"$PR1_NUM"/reviews --method POST \
    -f body="LGTM! The atexit handler approach is solid. Verified that the flush completes before exit. Approved by Bob." \
    -f event="COMMENT" 2>/dev/null || true
  echo "  Added review comment to PR #${PR1_NUM}"
fi

# PR #2: Add Python 3.12 to CI (Charlie)
echo "  Creating ci/add-python-3.12 branch..."
git checkout main 2>/dev/null || true
git branch -D ci/add-python-3.12 2>/dev/null || true
git checkout -b ci/add-python-3.12

# Add Python 3.12 to the supported versions in pyproject.toml
sed -i.bak 's/"Programming Language :: Python :: 3.11"/"Programming Language :: Python :: 3.11",\n    "Programming Language :: Python :: 3.12"/' pyproject.toml && rm -f pyproject.toml.bak
git add pyproject.toml

CI_REF=""
if [[ -n "$CI_ISSUE" ]]; then
  CI_REF="Closes #${CI_ISSUE}"
fi

GIT_AUTHOR_NAME="Charlie Kim" GIT_AUTHOR_EMAIL="charlie@acme-sdk.dev" \
GIT_COMMITTER_NAME="Charlie Kim" GIT_COMMITTER_EMAIL="charlie@acme-sdk.dev" \
git commit -m "ci: add Python 3.12 to CI test matrix

Adds Python 3.12 to the GitHub Actions test matrix.
${CI_REF}"

git push origin ci/add-python-3.12

PR2_URL=$(gh pr create --repo "$REPO" \
  --base main --head ci/add-python-3.12 \
  --title "Add Python 3.12 to CI matrix" \
  --body "## Summary
- Adds Python 3.12 to the CI test matrix

${CI_REF}

## Testing
- [x] CI should pass on all Python versions")

PR2_NUM=$(extract_num "$PR2_URL")
echo "  Created PR #${PR2_NUM:-?}: $PR2_URL"

# PR #3: Draft gRPC transport (Dana)
echo "  Creating feat/grpc-transport branch..."
git checkout main 2>/dev/null || true
git branch -D feat/grpc-transport 2>/dev/null || true
git checkout -b feat/grpc-transport

# Create a skeleton gRPC exporter
mkdir -p src/acme_sdk/exporters
cat > src/acme_sdk/exporters/grpc.py << 'PYEOF'
"""gRPC transport for the OTLP exporter (WIP)."""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from acme_sdk.models import Span

logger = logging.getLogger(__name__)


class GRPCExporter:
    """Export telemetry data using OTLP over gRPC.

    This exporter provides lower-latency export compared to HTTP,
    suitable for high-throughput production workloads.

    NOTE: This is a work in progress. The gRPC transport is not yet
    fully implemented.

    Args:
        endpoint: gRPC endpoint address (host:port).
        credentials: Optional gRPC channel credentials.
        compression: Whether to use gzip compression.
        timeout: RPC deadline in seconds.
    """

    def __init__(
        self,
        endpoint: str = "localhost:4317",
        credentials: Optional[Any] = None,
        compression: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self._endpoint = endpoint
        self._compression = compression
        self._timeout = timeout

        # TODO: Initialize gRPC channel
        # try:
        #     import grpc
        #     self._channel = grpc.insecure_channel(endpoint)
        # except ImportError:
        #     raise ImportError("grpcio is required for gRPC transport")

        logger.warning("GRPCExporter is not yet fully implemented")

    def export(self, spans: Sequence[Span]) -> None:
        """Export spans via gRPC (not yet implemented)."""
        raise NotImplementedError("gRPC export is not yet implemented")
PYEOF
git add src/acme_sdk/exporters/grpc.py

GRPC_REF=""
if [[ -n "$GRPC_ISSUE" ]]; then
  GRPC_REF="Refs #${GRPC_ISSUE}"
fi

GIT_AUTHOR_NAME="Dana Okafor" GIT_AUTHOR_EMAIL="dana@acme-sdk.dev" \
GIT_COMMITTER_NAME="Dana Okafor" GIT_COMMITTER_EMAIL="dana@acme-sdk.dev" \
git commit -m "feat(wip): scaffold gRPC transport for OTLP exporter

Initial skeleton for the gRPC exporter. Not yet functional —
needs grpcio integration and proto compilation.

${GRPC_REF}"

git push origin feat/grpc-transport

PR3_URL=$(gh pr create --repo "$REPO" \
  --base main --head feat/grpc-transport \
  --title "Draft: gRPC transport for OTLP" \
  --body "## Summary
Work in progress: Adding gRPC transport option for the OTLP exporter.

${GRPC_REF}

## Status
- [x] Skeleton exporter class
- [ ] gRPC channel initialization
- [ ] Proto compilation
- [ ] Span serialization to proto
- [ ] Retry/backoff for gRPC
- [ ] Tests
- [ ] Documentation" \
  --draft)

PR3_NUM=$(extract_num "$PR3_URL")
echo "  Created PR #${PR3_NUM:-?} (draft): $PR3_URL"

# ── Return to main ──────────────────────────────────────────────────────

git checkout main

# ── Summary ──────────────────────────────────────────────────────────────

echo ""
echo "==> Done! Repository metadata setup complete."
echo ""

# Count what we created
OPEN_ISSUES=$(gh issue list --repo "$REPO" --state open --json number --jq 'length' 2>/dev/null || echo "?")
CLOSED_ISSUES=$(gh issue list --repo "$REPO" --state closed --json number --jq 'length' 2>/dev/null || echo "?")
OPEN_PRS=$(gh pr list --repo "$REPO" --state open --json number --jq 'length' 2>/dev/null || echo "?")

echo "Summary:"
echo "  Labels: 9"
echo "  Milestones: 3 (v1.0.0 closed, v1.1.0 closed, v2.0 open)"
echo "  Open issues: ${OPEN_ISSUES}"
echo "  Closed issues: ${CLOSED_ISSUES}"
echo "  Open PRs: ${OPEN_PRS}"
echo ""

# List open issues for easy reference
echo "Open issues:"
gh issue list --repo "$REPO" --state open --json number,title --jq '.[] | "  #\(.number) \(.title)"' 2>/dev/null || true
echo ""
echo "Open PRs:"
gh pr list --repo "$REPO" --state open --json number,title --jq '.[] | "  #\(.number) \(.title)"' 2>/dev/null || true
