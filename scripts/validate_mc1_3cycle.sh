#!/usr/bin/env bash
# MC1 3-cycle physical validation wrapper.
#
# Captures [CCO]/[PCO] console logs from both devices in parallel (corroborating
# evidence only) while validate_mc1_3cycle.py drives the backend + deep links and
# decides PASS/FAIL from backend ground truth.
#
# Usage:
#   IPAD_UDID=... IPHONE_UDID=... API_BASE=... \
#   INSTRUCTOR_EMAIL=... INSTRUCTOR_PASSWORD=... \
#   PLAYER_EMAIL=... PLAYER_PASSWORD=... \
#   ./scripts/validate_mc1_3cycle.sh
#
# Required env vars: IPAD_UDID, IPHONE_UDID, API_BASE, INSTRUCTOR_EMAIL,
# INSTRUCTOR_PASSWORD, PLAYER_EMAIL, PLAYER_PASSWORD.
# Find device UDIDs with: xcrun devicectl list devices

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$(mktemp -d -t mc1-validate)"
IPAD_LOG="${LOG_DIR}/ipad_console.log"
IPHONE_LOG="${LOG_DIR}/iphone_console.log"

: "${IPAD_UDID:?Set IPAD_UDID (see: xcrun devicectl list devices)}"
: "${IPHONE_UDID:?Set IPHONE_UDID (see: xcrun devicectl list devices)}"
: "${API_BASE:?Set API_BASE, e.g. https://...vercel.app}"
: "${INSTRUCTOR_EMAIL:?Set INSTRUCTOR_EMAIL}"
: "${INSTRUCTOR_PASSWORD:?Set INSTRUCTOR_PASSWORD}"
: "${PLAYER_EMAIL:?Set PLAYER_EMAIL}"
: "${PLAYER_PASSWORD:?Set PLAYER_PASSWORD}"

echo "Console logs: ${LOG_DIR}"

xcrun devicectl device console --device "${IPAD_UDID}" > "${IPAD_LOG}" 2>&1 &
IPAD_CONSOLE_PID=$!
xcrun devicectl device console --device "${IPHONE_UDID}" > "${IPHONE_LOG}" 2>&1 &
IPHONE_CONSOLE_PID=$!

cleanup() {
  kill "${IPAD_CONSOLE_PID}" "${IPHONE_CONSOLE_PID}" 2>/dev/null || true
}
trap cleanup EXIT

# Let console capture attach before the run starts.
sleep 2

set +e
python3 "${SCRIPT_DIR}/validate_mc1_3cycle.py" \
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
echo "=== Corroborating console evidence ==="
echo "--- iPad [CCO] lines ---"
grep -h '\[CCO\]' "${IPAD_LOG}" || echo "(none captured)"
echo "--- iPhone [PCO] lines ---"
grep -h '\[PCO\]' "${IPHONE_LOG}" || echo "(none captured)"
echo
echo "Full console logs kept at: ${LOG_DIR}"

exit "${PY_EXIT}"
