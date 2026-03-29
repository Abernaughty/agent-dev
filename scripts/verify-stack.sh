#!/usr/bin/env bash
# verify-stack.sh — Smoke test for the Dev Suite dashboard + API stack.
#
# Checks that both the FastAPI backend and SvelteKit dashboard are
# running and can talk to each other. Run from the repo root.
#
# Usage:
#   ./scripts/verify-stack.sh
#   ./scripts/verify-stack.sh --api-only    # Skip dashboard check
#   ./scripts/verify-stack.sh --dash-only   # Skip API check

set -euo pipefail

API_URL="${BACKEND_URL:-http://localhost:8000}"
DASH_URL="${DASHBOARD_URL:-http://localhost:5173}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

pass=0
fail=0
skip=0

check() {
    local label="$1" url="$2" expect="${3:-200}"
    printf "  %-40s " "$label"
    status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")
    if [ "$status" = "$expect" ]; then
        printf "${GREEN}OK${NC} (%s)\n" "$status"
        pass=$((pass + 1))
    elif [ "$status" = "000" ]; then
        printf "${RED}UNREACHABLE${NC}\n"
        fail=$((fail + 1))
    else
        printf "${YELLOW}UNEXPECTED${NC} (got %s, expected %s)\n" "$status" "$expect"
        fail=$((fail + 1))
    fi
}

# SSE endpoints stream indefinitely. Standard curl -o /dev/null -w %{http_code}
# appends the status code to body bytes ("200000" not "200").
# Use python urllib which reads headers before body, with a 5s timeout.
check_sse() {
    local label="$1" url="$2"
    printf "  %-40s " "$label"
    local code
    code=$(python3 -c "
import urllib.request, urllib.error
try:
    resp = urllib.request.urlopen('$url', timeout=5)
    print(resp.status)
    resp.close()
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print('000')
" 2>/dev/null || echo "000")
    if [ "$code" = "200" ]; then
        printf "${GREEN}OK${NC} (SSE %s)\n" "$code"
        pass=$((pass + 1))
    elif [ -z "$code" ] || [ "$code" = "000" ]; then
        printf "${RED}UNREACHABLE${NC}\n"
        fail=$((fail + 1))
    else
        printf "${YELLOW}UNEXPECTED${NC} (got %s, expected 200)\n" "$code"
        fail=$((fail + 1))
    fi
}

check_json() {
    local label="$1" url="$2" jq_filter="$3"
    printf "  %-40s " "$label"
    body=$(curl -s --max-time 5 "$url" 2>/dev/null || echo "")
    if [ -z "$body" ]; then
        printf "${RED}UNREACHABLE${NC}\n"
        fail=$((fail + 1))
        return
    fi
    result=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print($jq_filter)" 2>/dev/null || echo "PARSE_ERROR")
    if [ "$result" != "PARSE_ERROR" ] && [ -n "$result" ]; then
        printf "${GREEN}OK${NC} (%s)\n" "$result"
        pass=$((pass + 1))
    else
        printf "${RED}FAILED${NC} (bad response)\n"
        fail=$((fail + 1))
    fi
}

echo ""
echo -e "${CYAN}Dev Suite Stack Verification${NC}"
echo "================================================"

# --- API checks ---
if [ "${1:-}" != "--dash-only" ]; then
    echo ""
    echo -e "${CYAN}FastAPI Backend${NC} ($API_URL)"
    echo "------------------------------------------------"
    check "Health endpoint" "$API_URL/health"
    check_json "Health: uptime" "$API_URL/health" "f\"uptime={d.get('uptime_seconds', '?')}s\""
    check "Agents endpoint" "$API_URL/agents"
    check "Tasks endpoint" "$API_URL/tasks"
    check "Memory endpoint" "$API_URL/memory"
    check "PRs endpoint" "$API_URL/prs"
    check "OpenAPI docs" "$API_URL/docs"
    check_sse "SSE stream (reachable)" "$API_URL/stream"
fi

# --- Dashboard checks ---
if [ "${1:-}" != "--api-only" ]; then
    echo ""
    echo -e "${CYAN}SvelteKit Dashboard${NC} ($DASH_URL)"
    echo "------------------------------------------------"
    check "Dashboard home" "$DASH_URL"
    check "Proxy: /api/agents" "$DASH_URL/api/agents"
    check "Proxy: /api/tasks" "$DASH_URL/api/tasks"
    check "Proxy: /api/memory" "$DASH_URL/api/memory"
    check "Proxy: /api/prs" "$DASH_URL/api/prs"
    # NOTE: Proxy SSE (/api/stream) is NOT checked here. Vite's dev server
    # buffers streaming responses and won't flush headers to CLI tools within
    # a reasonable timeout. SSE proxy is verified by:
    #   1. Direct /stream check above (proves SSE endpoint works)
    #   2. Other /api/* proxy checks (proves proxy layer works)
    #   3. Browser EventSource connection during Stage 1 manual testing
fi

# --- Summary ---
total=$((pass + fail + skip))
echo ""
echo "================================================"
if [ "$fail" -eq 0 ]; then
    echo -e "${GREEN}ALL CHECKS PASSED${NC} ($pass/$total)"
else
    echo -e "${RED}$fail FAILED${NC}, ${GREEN}$pass passed${NC} (of $total)"
fi
echo ""

exit "$fail"
