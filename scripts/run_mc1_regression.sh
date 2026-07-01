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
#   ./scripts/run_mc1_regression.sh [--scenario smoke|multicycle|retry|finalization|gopro-network-routing-diag|all] [--cycles N]
#
# Credentials (email + password) are prompted interactively — never stored in
# env vars, files, or logs. Passwords are read via Python getpass (no echo).
#
# Default --scenario is "all" (currently: smoke, multicycle; retry and
# finalization are registered but report SKIPPED until ORCH-7/ORCH-8 land).
# Default --cycles is 3 (only affects the multicycle scenario).
#
# gopro-network-routing-diag (MC1 Block 1) requires a manual operator step:
# in-app GoPro WiFi auto-join is unavailable under the current personal/free
# Apple Developer team (HotspotConfiguration entitlement not provisionable),
# so the operator must join the GoPro WiFi network by hand from iPhone
# Settings > Wi-Fi when the script prompts for it. This is a
# physical-validation workaround, not the final product UX — see
# README_mc1_regression.md "Block 1: GoPro WiFi manual join workaround".
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
_DEFAULT_API_BASE="https://practice-booking-system-git-deploy-vercel-staging-lfa-ec-test.vercel.app"
API_BASE="${API_BASE:-${_DEFAULT_API_BASE}}"

# ── Static preflight: catch known-regression wiring gaps BEFORE touching any
# device or network (2026-07-01 flow audit). Checks deep-link action parity,
# GoPro preview start ordering, PCO attach role-gating, skeleton feed wiring,
# device routing, artifact collector completeness, log-capture config.
#
# SKIP_STATIC_PREFLIGHT=1 is a documented DEVELOPER EMERGENCY ESCAPE HATCH ONLY.
# It is NOT a normal run mode: both SKIP_STATIC_PREFLIGHT=1 AND a non-empty
# SKIP_STATIC_PREFLIGHT_REASON are required, every skip is appended to
# scripts/mc1_regression_runs/static_preflight_skip_audit.log, and the run is
# passed through to runner.py as MC1_STATIC_PREFLIGHT_SKIPPED=1 — which forces
# overall_pass=False and stamps the report so it can never be read back as a
# valid PASS, no matter what the scenario itself reports.
echo
echo "Running static preflight check (no devices required)..."
if [[ "${SKIP_STATIC_PREFLIGHT:-0}" == "1" ]]; then
  if [[ -z "${SKIP_STATIC_PREFLIGHT_REASON:-}" ]]; then
    echo
    echo "ERROR: SKIP_STATIC_PREFLIGHT=1 requires SKIP_STATIC_PREFLIGHT_REASON=\"<explanation>\"."
    echo "  Skipping the static preflight without a recorded reason is not permitted."
    exit 1
  fi
  echo "WARNING: SKIP_STATIC_PREFLIGHT=1 — skipping static preflight check."
  echo "  Reason: ${SKIP_STATIC_PREFLIGHT_REASON}"
  echo "  This run will be marked overall_pass=false regardless of scenario outcome —"
  echo "  a skipped-preflight run is NEVER a valid PASS."
  _SKIP_AUDIT_LOG="${SCRIPT_DIR}/mc1_regression_runs/static_preflight_skip_audit.log"
  mkdir -p "$(dirname "${_SKIP_AUDIT_LOG}")"
  printf '%s | user=%s | reason=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(whoami)" "${SKIP_STATIC_PREFLIGHT_REASON}" >> "${_SKIP_AUDIT_LOG}"
  export MC1_STATIC_PREFLIGHT_SKIPPED=1
  export MC1_STATIC_PREFLIGHT_SKIP_REASON="${SKIP_STATIC_PREFLIGHT_REASON}"
else
  if ! python3 "${SCRIPT_DIR}/mc1_regression/preflight_static_check.py"; then
    echo
    echo "ERROR: static preflight check FAILED — see failures above."
    echo "  These are known-regression classes that have caused physical-test"
    echo "  failures before; fix them first, or set SKIP_STATIC_PREFLIGHT=1 with"
    echo "  SKIP_STATIC_PREFLIGHT_REASON=\"...\" to proceed anyway (emergency only —"
    echo "  the run will NOT count as a valid PASS)."
    exit 1
  fi
fi

# ── Preflight: verify API_BASE is reachable before prompting credentials ──
echo
echo "Checking API_BASE: ${API_BASE}"
_HEALTH_URL="${API_BASE}/api/v1/system/time"
_HTTP_CODE="$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "${_HEALTH_URL}" 2>/dev/null || true)"
if [[ "${_HTTP_CODE}" != "200" ]]; then
  echo
  echo "ERROR: Backend not reachable (HTTP ${_HTTP_CODE:-000})."
  echo "  URL tried: ${_HEALTH_URL}"
  echo
  echo "  Is the deployment active? Try:"
  echo "  API_BASE=${_DEFAULT_API_BASE} \\"
  echo "  ./scripts/run_mc1_regression.sh --scenario all"
  echo
  exit 1
fi
echo "Backend OK (HTTP ${_HTTP_CODE})."

# ── Credential prompt (before console capture so it appears immediately) ──
echo
echo "MC1 Regression — staging credentials"
echo "────────────────────────────────────"
read -r -p "Instructor email [staging-instructor@lfa-staging.io]: " _INST_EMAIL
_INST_EMAIL="${_INST_EMAIL:-staging-instructor@lfa-staging.io}"
read -r -s -p "Instructor password: " _INST_PASS; echo
echo
read -r -p "Player email    [staging-player1@lfa-staging.io]: " _PL_EMAIL
_PL_EMAIL="${_PL_EMAIL:-staging-player1@lfa-staging.io}"
read -r -s -p "Player password: " _PL_PASS; echo
echo

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${SCRIPT_DIR}/mc1_regression_runs/${TIMESTAMP}_${SCENARIO}"
mkdir -p "${OUT_DIR}/console"

echo "Artifacts: ${OUT_DIR}"

# Capture iOS device logs via idevicesyslog (libimobiledevice).
# 'log stream --device' is not supported on macOS 26 Tahoe.
# libimobiledevice uses legacy UDID format (hex, no dashes), while xcrun devicectl
# uses CoreDevice UUID format. We auto-detect legacy UDIDs via idevice_id -l.
_IDEVICESYSLOG="$(command -v idevicesyslog 2>/dev/null || true)"
_IDEVICEID="$(command -v idevice_id 2>/dev/null || true)"
IPAD_CONSOLE_PID=""
IPHONE_CONSOLE_PID=""
if [[ -z "${_IDEVICESYSLOG}" ]]; then
  echo "WARNING: idevicesyslog not found. Console capture disabled."
  echo "  Install: brew install libimobiledevice"
elif [[ -z "${_IDEVICEID}" ]]; then
  echo "WARNING: idevice_id not found. Console capture disabled."
else
  # Get all legacy UDIDs visible to libimobiledevice (USB-connected only).
  # Optional override: set IPHONE_LEGACY_UDID / IPAD_LEGACY_UDID env vars to
  # skip auto-detection (get them via: idevice_id -l).
  _LEGACY_UDIDS="$("${_IDEVICEID}" -l 2>/dev/null || true)"
  if [[ -z "${IPHONE_LEGACY_UDID:-}" ]]; then
    _IPHONE_LEGACY_UDID="$(echo "${_LEGACY_UDIDS}" | head -1)"
  else
    _IPHONE_LEGACY_UDID="${IPHONE_LEGACY_UDID}"
  fi
  if [[ -z "${IPAD_LEGACY_UDID:-}" ]]; then
    _IPAD_LEGACY_UDID="$(echo "${_LEGACY_UDIDS}" | sed -n '2p')"
  else
    _IPAD_LEGACY_UDID="${IPAD_LEGACY_UDID}"
  fi

  if [[ -z "${_IPHONE_LEGACY_UDID}" ]]; then
    echo "WARNING: iPhone not visible to idevicesyslog. Connect via USB and trust this Mac."
    echo "  Console capture disabled. Set IPHONE_LEGACY_UDID to override auto-detection."
  else
    echo "Starting console capture (idevicesyslog → LFAEducationCenter)..."
    echo "  iPhone legacy UDID: ${_IPHONE_LEGACY_UDID}"
    "${_IDEVICESYSLOG}" --udid "${_IPHONE_LEGACY_UDID}" --process LFAEducationCenter \
      > "${OUT_DIR}/console/iphone_console.log" 2>&1 &
    IPHONE_CONSOLE_PID=$!
  fi

  if [[ -z "${_IPAD_LEGACY_UDID}" ]]; then
    echo "WARNING: iPad not visible to idevicesyslog (must be USB-connected, not WiFi-only)."
    echo "  iPad console capture SKIPPED. Set IPAD_LEGACY_UDID env var to enable."
    echo "  Get UDID: idevice_id -l  (while iPad is USB-connected)"
  elif [[ "${_IPAD_LEGACY_UDID}" == "${_IPHONE_LEGACY_UDID}" ]]; then
    echo "WARNING: Only one device visible to idevicesyslog — iPad and iPhone appear identical."
    echo "  iPad console capture SKIPPED to avoid duplicate logs."
    echo "  Connect iPad via USB and trust this Mac, then set IPAD_LEGACY_UDID."
  else
    echo "  iPad  legacy UDID: ${_IPAD_LEGACY_UDID}"
    "${_IDEVICESYSLOG}" --udid "${_IPAD_LEGACY_UDID}" --process LFAEducationCenter \
      > "${OUT_DIR}/console/ipad_console.log" 2>&1 &
    IPAD_CONSOLE_PID=$!
  fi
fi

cleanup() {
  [[ -n "${IPAD_CONSOLE_PID:-}" ]]   && kill "${IPAD_CONSOLE_PID}"   2>/dev/null || true
  [[ -n "${IPHONE_CONSOLE_PID:-}" ]] && kill "${IPHONE_CONSOLE_PID}" 2>/dev/null || true
  unset _INST_PASS _PL_PASS _INST_EMAIL _PL_EMAIL
}
trap cleanup EXIT

echo "Waiting for log stream to attach (3s)..."
sleep 3
echo "Running regression scenarios..."
echo

set +e
_INST_EMAIL="${_INST_EMAIL}" _INST_PASS="${_INST_PASS}" \
_PL_EMAIL="${_PL_EMAIL}" _PL_PASS="${_PL_PASS}" \
python3 "${SCRIPT_DIR}/mc1_regression/runner.py" \
  --scenario "${SCENARIO}" \
  --cycles "${CYCLES}" \
  --out-dir "${OUT_DIR}" \
  --api-base "${API_BASE}" \
  --ipad-udid "${IPAD_UDID}" \
  --iphone-udid "${IPHONE_UDID}"
PY_EXIT=$?
set -e

echo
echo "Full artifacts kept at: ${OUT_DIR}"

exit "${PY_EXIT}"
