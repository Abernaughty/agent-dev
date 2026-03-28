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
        ((pass++))
    elif [ "$status" = "000" ]; then
        printf "${RED}UNREACHABLE${NC}\n"
        ((fail++))
    else
        printf "${YELLOW}UNEXPECTED${NC} (got %s, expected %s)\n" "$status" "$expect"
        ((fail++))
    fi
}

check_json() {
    local label="$1" url="$2" jq_filter="$3"
    printf "  %-40s " "$label"
    body=$(curl -s --max-time 5 "$url" 2>/dev/null || echo "")
    if [ -z "$body" ]; then
        printf "${RED}UNREACHABLE${NC}\n"
        ((fail++))
        return
    fi
    result=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print($jq_filter)" 2>/dev/null || echo "PARSE_ERROR")
    if [ "$result" != "PARSE_ERROR" ] && [ -n "$result" ]; then
        printf "${GREEN}OK${NC} (%s)\n" "$result"
        ((pass++))
    else
        printf "${RED}FAILED${NC} (bad response)\n"
        ((fail++))
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
    check "SSE stream (reachable)" "$API_URL/stream"
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
    check "Proxy: /api/stream" "$DASH_URL/api/stream"
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
