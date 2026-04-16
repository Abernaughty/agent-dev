#!/usr/bin/env bash
# smoke-test.sh -- E2E smoke test for the Dev Suite stack.
#
# Runs verify-stack.sh as prerequisite, then submits the canonical test
# prompt to the orchestrator and polls until a terminal state is reached.
#
# Usage:
#   ./scripts/smoke-test.sh              # Full smoke test (stages 0-2)
#   ./scripts/smoke-test.sh --dry-run    # Stages 0-1 only (no LLM calls)
#
# Environment:
#   BACKEND_URL    API base (default: http://localhost:8000)
#   API_SECRET     Bearer token (omit for dev mode)
#   SMOKE_TIMEOUT  Max seconds to wait for task (default: 300)
#
# Exit codes:
#   0  All stages passed
#   1  Stage failure (see output)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

API_URL="${BACKEND_URL:-http://localhost:8000}"
TIMEOUT="${SMOKE_TIMEOUT:-300}"
POLL_INTERVAL=2
DRY_RUN=false

if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=true
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
DIM='\033[0;90m'
NC='\033[0m'

# -- Auth header --
AUTH_HEADER=""
if [ -n "${API_SECRET:-}" ]; then
    AUTH_HEADER="Authorization: Bearer $API_SECRET"
fi

curl_get() {
    local url="$1"
    if [ -n "$AUTH_HEADER" ]; then
        curl -s --max-time 10 -H "$AUTH_HEADER" "$url"
    else
        curl -s --max-time 10 "$url"
    fi
}

curl_post() {
    local url="$1" data="$2"
    if [ -n "$AUTH_HEADER" ]; then
        curl -s --max-time 30 -H "$AUTH_HEADER" -H "Content-Type: application/json" -d "$data" "$url"
    else
        curl -s --max-time 30 -H "Content-Type: application/json" -d "$data" "$url"
    fi
}

# ============================================================
# Stage 0: Infrastructure health
# ============================================================
echo ""
echo -e "${CYAN}=== SMOKE TEST ===${NC}"
echo -e "${DIM}$(date '+%Y-%m-%d %H:%M:%S')${NC}"
echo ""
echo -e "${CYAN}Stage 0: Infrastructure Health${NC}"
echo "------------------------------------------------"

if ! bash "$SCRIPT_DIR/verify-stack.sh"; then
    echo ""
    echo -e "${RED}STAGE 0 FAILED${NC} -- verify-stack.sh reported errors"
    echo "Fix connectivity issues before running the smoke test."
    exit 1
fi

echo ""
echo -e "${GREEN}Stage 0 PASSED${NC}"

# ============================================================
# Stage 1: Read-only data flow
# ============================================================
echo ""
echo -e "${CYAN}Stage 1: Read-Only Data Flow${NC}"
echo "------------------------------------------------"

stage1_pass=0
stage1_fail=0

check_json_field() {
    local label="$1" url="$2" field="$3"
    printf "  %-40s " "$label"
    body=$(curl_get "$url" 2>/dev/null || echo "")
    if [ -z "$body" ]; then
        printf "${RED}UNREACHABLE${NC}\n"
        stage1_fail=$((stage1_fail + 1))
        return
    fi
    result=$(echo "$body" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    val = d
    for k in '$field'.split('.'):
        if isinstance(val, list):
            val = val[int(k)] if k.isdigit() else val
        else:
            val = val[k]
    print('OK')
except Exception as e:
    print(f'FAIL: {e}')
" 2>/dev/null || echo "PARSE_ERROR")
    if [[ "$result" == "OK" ]]; then
        printf "${GREEN}OK${NC}\n"
        stage1_pass=$((stage1_pass + 1))
    else
        printf "${RED}FAILED${NC} (%s)\n" "$result"
        stage1_fail=$((stage1_fail + 1))
    fi
}

# Verify API endpoints return expected data
check_json_field "GET /agents -> data array" "$API_URL/agents" "data"
check_json_field "GET /tasks -> data array" "$API_URL/tasks" "data"
check_json_field "GET /memory -> data array" "$API_URL/memory" "data"
check_json_field "GET /health -> uptime_seconds" "$API_URL/health" "uptime_seconds"

# SSE already verified by Stage 0 (verify-stack.sh), no need to recheck

echo ""
if [ "$stage1_fail" -gt 0 ]; then
    echo -e "${RED}Stage 1 FAILED${NC} ($stage1_fail failures)"
    exit 1
fi
echo -e "${GREEN}Stage 1 PASSED${NC} ($stage1_pass checks)"

# ============================================================
# Dry-run exit
# ============================================================
if [ "$DRY_RUN" = true ]; then
    echo ""
    echo -e "${YELLOW}--dry-run: Skipping Stage 2 (no LLM calls)${NC}"
    echo ""
    echo -e "${GREEN}SMOKE TEST PASSED${NC} (stages 0-1, dry-run mode)"
    exit 0
fi

# ============================================================
# Stage 2: Task submission (real LLM calls)
# ============================================================
echo ""
echo -e "${CYAN}Stage 2: Task Submission${NC}"
echo "------------------------------------------------"
echo -e "${YELLOW}NOTE: This stage makes real LLM API calls (~\$0.05-0.50)${NC}"
echo ""

CANONICAL_PROMPT='Create a Python function called greet in a new file greet.py that takes a name parameter and returns a greeting string'

# Issue #105: POST /tasks requires `workspace` when workspace_type is 'local'.
# Fetch the default workspace from GET /workspaces rather than assuming.
DEFAULT_WS=$(curl_get "$API_URL/workspaces" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    for w in d.get('data', []):
        if w.get('is_default'):
            print(w['path'])
            break
except Exception:
    pass
" 2>/dev/null || echo "")

if [ -z "$DEFAULT_WS" ]; then
    echo -e "  ${RED}FAILED${NC} -- GET /workspaces returned no default workspace"
    exit 1
fi

printf "  ${DIM}Workspace: %s${NC}\n" "$DEFAULT_WS"

# Build the request body via python3 so Windows paths (backslashes) are
# escaped correctly as JSON -- inline shell interpolation mangles them.
TASK_BODY=$(python3 -c "
import json, sys
print(json.dumps({
    'description': sys.argv[1],
    'create_pr': False,
    'workspace': sys.argv[2],
}))
" "$CANONICAL_PROMPT" "$DEFAULT_WS")

echo -e "  Submitting canonical test prompt..."
TASK_RESPONSE=$(curl_post "$API_URL/tasks" "$TASK_BODY" 2>/dev/null || echo "")

if [ -z "$TASK_RESPONSE" ]; then
    echo -e "  ${RED}FAILED${NC} -- POST /tasks returned empty response"
    exit 1
fi

TASK_ID=$(echo "$TASK_RESPONSE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d['data']['task_id'])
except Exception:
    print('')
" 2>/dev/null || echo "")

if [ -z "$TASK_ID" ]; then
    echo -e "  ${RED}FAILED${NC} -- Could not extract task_id from response"
    echo -e "  ${DIM}Response: $TASK_RESPONSE${NC}"
    exit 1
fi

echo -e "  ${GREEN}Task created${NC}: $TASK_ID"
echo ""

# -- Poll until terminal state --
START_TIME=$(date +%s)
ELAPSED=0
LAST_STATUS=""

echo -e "  Polling task status (timeout: ${TIMEOUT}s)..."
echo ""

while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
    TASK_BODY=$(curl_get "$API_URL/tasks/$TASK_ID" 2>/dev/null || echo "")
    
    if [ -z "$TASK_BODY" ]; then
        echo -e "  ${YELLOW}WARN${NC} -- GET /tasks/$TASK_ID returned empty, retrying..."
        sleep "$POLL_INTERVAL"
        ELAPSED=$(( $(date +%s) - START_TIME ))
        continue
    fi

    STATUS=$(echo "$TASK_BODY" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    t = d.get('data', d)
    print(t.get('status', 'unknown'))
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")

    if [ "$STATUS" != "$LAST_STATUS" ]; then
        ELAPSED=$(( $(date +%s) - START_TIME ))
        echo -e "  ${DIM}[${ELAPSED}s]${NC} Status: ${CYAN}$STATUS${NC}"
        LAST_STATUS="$STATUS"
    fi

    # Terminal states
    case "$STATUS" in
        passed|completed|done)
            ELAPSED=$(( $(date +%s) - START_TIME ))
            echo ""
            echo -e "  ${GREEN}Task PASSED${NC} in ${ELAPSED}s"
            
            # Extract summary info
            echo "$TASK_BODY" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    t = d.get('data', d)
    tokens = t.get('tokens_used', '?')
    cost = t.get('cost', '?')
    retries = t.get('retries', '?')
    print(f'  Tokens: {tokens} | Cost: \${cost} | Retries: {retries}')
except Exception:
    pass
" 2>/dev/null || true
            echo ""
            echo -e "${GREEN}SMOKE TEST PASSED${NC} (all stages)"
            exit 0
            ;;
        failed|error)
            ELAPSED=$(( $(date +%s) - START_TIME ))
            echo ""
            echo -e "  ${RED}Task FAILED${NC} after ${ELAPSED}s"
            
            # Extract error info
            echo "$TASK_BODY" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    t = d.get('data', d)
    err = t.get('error', t.get('failure_reason', 'No error details'))
    print(f'  Error: {err}')
except Exception:
    pass
" 2>/dev/null || true
            echo ""
            echo -e "${RED}SMOKE TEST FAILED${NC} (task did not pass)"
            exit 1
            ;;
        cancelled)
            echo ""
            echo -e "  ${YELLOW}Task CANCELLED${NC}"
            echo -e "${RED}SMOKE TEST FAILED${NC} (task was cancelled)"
            exit 1
            ;;
    esac

    sleep "$POLL_INTERVAL"
    ELAPSED=$(( $(date +%s) - START_TIME ))
done

# Timeout
echo ""
echo -e "  ${RED}TIMEOUT${NC} -- Task did not reach terminal state in ${TIMEOUT}s"
echo -e "  Last status: $LAST_STATUS"
echo ""
echo -e "${RED}SMOKE TEST FAILED${NC} (timeout)"
exit 1
