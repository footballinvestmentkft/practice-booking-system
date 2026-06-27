#!/usr/bin/env bash
# MC1 physical regression suite — single entry point (MC1-AUTO-2).
#
# Creates a timestamped artifact directory, starts console capture on both
# devices, then runs one or more scenarios that drive the backend + devices
# via lfa-mc1:// deep links. PASS/FAIL always comes from backend ground truth
# (mc1_regression/runner.py); console logs and debug snapshots are saved as
# corroborating evidence only.
#
# Usage:
#   IPAD_UDID=... IPHONE_UDID=... API_BASE=... \
#   INSTRUCTOR_EMAIL=... INSTRUCTOR_PASSWORD=... \
#   PLAYER_EMAIL=... PLAYER_PASSWORD=... \
#   ./scripts/run_mc1_regression.sh [--scenario smoke|multicycle|retry|finalization|all] [--cycles N]
#
# Default --scenario is "all" (currently: smoke, multicycle; retry and
# finalization are registered but report SKIPPED until ORCH-7/ORCH-8 land).
# Default --cycles is 3 (only affects the multicycle scenario).
#
# Find device UDIDs with: xcrun devicectl list devices
# Output: scripts/mc1_regression_runs/<timestamp>_<scenario>/
#   report.txt, report.json             — final PASS/FAIL + per-step detail
#   backend_state/<scenario>_*.json     — GET session + GET cycles dumps
#   console/<device>_console.log        — full raw device console capture
#   console/<scenario>_{cco,pco}_lines.txt — filtered [CCO]/[PCO] lines
#   debug_snapshots/<scenario>_<device>_NN.txt — extracted Debug Snapshot text

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIO="all"
CYCLES=3

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenario) SCENARIO="$2"; shift 2 ;;
    --cycles) CYCLES="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

: "${IPAD_UDID:?Set IPAD_UDID (see: xcrun devicectl list devices)}"
: "${IPHONE_UDID:?Set IPHONE_UDID (see: xcrun devicectl list devices)}"
: "${API_BASE:?Set API_BASE, e.g. https://...vercel.app}"
: "${INSTRUCTOR_EMAIL:?Set INSTRUCTOR_EMAIL}"
: "${INSTRUCTOR_PASSWORD:?Set INSTRUCTOR_PASSWORD}"
: "${PLAYER_EMAIL:?Set PLAYER_EMAIL}"
: "${PLAYER_PASSWORD:?Set PLAYER_PASSWORD}"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${SCRIPT_DIR}/mc1_regression_runs/${TIMESTAMP}_${SCENARIO}"
mkdir -p "${OUT_DIR}/console"

echo "Artifacts: ${OUT_DIR}"

xcrun devicectl device console --device "${IPAD_UDID}" > "${OUT_DIR}/console/ipad_console.log" 2>&1 &
IPAD_CONSOLE_PID=$!
xcrun devicectl device console --device "${IPHONE_UDID}" > "${OUT_DIR}/console/iphone_console.log" 2>&1 &
IPHONE_CONSOLE_PID=$!

cleanup() {
  kill "${IPAD_CONSOLE_PID}" "${IPHONE_CONSOLE_PID}" 2>/dev/null || true
}
trap cleanup EXIT

# Let console capture attach before the run starts.
sleep 2

set +e
python3 "${SCRIPT_DIR}/mc1_regression/runner.py" \
  --scenario "${SCENARIO}" \
  --cycles "${CYCLES}" \
  --out-dir "${OUT_DIR}" \
  --api-base "${API_BASE}" \
  --ipad-udid "${IPAD_UDID}" \
  --iphone-udid "${IPHONE_UDID}" \
  --instructor-email "${INSTRUCTOR_EMAIL}" \
  --instructor-password "${INSTRUCTOR_PASSWORD}" \
  --player-email "${PLAYER_EMAIL}" \
  --player-password "${PLAYER_PASSWORD}"
PY_EXIT=$?
set -e

echo
echo "Full artifacts kept at: ${OUT_DIR}"

exit "${PY_EXIT}"
