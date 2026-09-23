#!/usr/bin/env bash
# Waypoint crash-resume demo driver
# Requires: bash >= 4, curl, uv
#
# Usage:  bash scripts/record_demo.sh
#
# What it does:
#   1. Starts the server with --kill-after $KILL_AFTER (self-destructs mid-run)
#   2. POSTs a research_team run and streams events until the crash
#   3. Restarts the server; streams events until RUN_FINISHED
#   4. Keeps the server alive so you can take a screenshot in the browser
#
# NOTE: docs/screenshot.png is a MANUAL step.
#   After Phase 5 finishes streaming, open http://localhost:8000 in a browser,
#   wait for the timeline to show the "resumed here" marker, and save a
#   screenshot to docs/screenshot.png.
#
# To regenerate docs/demo.gif, install vhs (https://github.com/charmbracelet/vhs)
# then run: vhs scripts/demo.tape  (create demo.tape separately — see comment below)
# Or use any screen recorder; save output to docs/demo.gif.
#
# demo.tape sketch (vhs):
#   Output docs/demo.gif
#   Set FontSize 14
#   Set Width 1200
#   Set Height 700
#   Type "bash scripts/record_demo.sh"
#   Enter
#   Sleep 60s

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────────
KILL_AFTER=20          # seconds before the first server self-destructs
DB_PATH=waypoint_demo.db
PORT=8000
BASE_URL="http://localhost:${PORT}"
SERVER_PID=""

# ── Helpers ──────────────────────────────────────────────────────────────────
cleanup() {
    if [[ -n "$SERVER_PID" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        SERVER_PID=""
    fi
    rm -f "$DB_PATH"
}
trap cleanup EXIT

wait_for_server() {
    echo "Waiting for server on ${BASE_URL} …"
    local i=0
    until curl -sf "${BASE_URL}/workflows" >/dev/null 2>&1; do
        i=$((i + 1))
        if [[ $i -ge 30 ]]; then
            echo "ERROR: server did not start within 30 s" >&2
            exit 1
        fi
        sleep 1
    done
    echo "Server is up."
}

# json_field <field> — reads JSON from stdin, prints the named field value
json_field() {
    python3 -c "import sys,json; print(json.load(sys.stdin)['$1'])"
}

# ── Phase 1: clean state + start server (will self-destruct) ─────────────────
echo ""
echo "=== Phase 1: starting server (will crash in ${KILL_AFTER}s) ==="
rm -f "$DB_PATH"

uv run waypoint serve \
    --dev \
    --kill-after "$KILL_AFTER" \
    --db-path "$DB_PATH" \
    --port "$PORT" \
    >/tmp/waypoint_phase1.log 2>&1 &
SERVER_PID=$!
echo "Server PID: ${SERVER_PID}"

wait_for_server

# ── Phase 2: POST a run ───────────────────────────────────────────────────────
echo ""
echo "=== Phase 2: starting research_team run ==="
RESPONSE=$(curl -sf -X POST "${BASE_URL}/runs" \
    -H "Content-Type: application/json" \
    -d '{"workflow":"research_team","prompt":"State of MCP servers in 2026"}')
echo "Response: ${RESPONSE}"
RUN_ID=$(echo "$RESPONSE" | json_field "run_id")
echo "Run ID: ${RUN_ID}"

# ── Phase 3: stream events until server self-destructs ───────────────────────
echo ""
echo "=== Phase 3: streaming events (server will die ~${KILL_AFTER}s) ==="
curl --no-buffer \
    --max-time $((KILL_AFTER + 5)) \
    "${BASE_URL}/runs/${RUN_ID}/events" \
    2>/dev/null || true

echo ""
echo "=== Connection dropped (server crashed as expected) ==="
SERVER_PID=""   # process is already gone — don't attempt to kill it in cleanup

# ── Phase 4: restart server ───────────────────────────────────────────────────
sleep 2

echo ""
echo "=== Phase 4: restarting server ==="
uv run waypoint serve \
    --dev \
    --db-path "$DB_PATH" \
    --port "$PORT" \
    >/tmp/waypoint_phase2.log 2>&1 &
SERVER_PID=$!
echo "Server PID: ${SERVER_PID}"

wait_for_server

# ── Phase 5: stream events until RUN_FINISHED ────────────────────────────────
echo ""
echo "=== Phase 5: streaming resumed run (max 180s) ==="
curl --no-buffer \
    --max-time 180 \
    "${BASE_URL}/runs/${RUN_ID}/events" \
    2>/dev/null || true

echo ""
echo "=== Run complete ==="

# ── Phase 6: keep server alive for screenshot ────────────────────────────────
echo ""
echo "========================================================"
echo " Open http://localhost:${PORT} in your browser."
echo " The timeline should show the 'resumed here' marker."
echo " Take a screenshot and save it to docs/screenshot.png"
echo " Press Ctrl-C to stop the server and clean up."
echo "========================================================"
echo ""

# Wait so the user can take the screenshot; cleanup trap fires on Ctrl-C / exit
wait "$SERVER_PID" || true
SERVER_PID=""
