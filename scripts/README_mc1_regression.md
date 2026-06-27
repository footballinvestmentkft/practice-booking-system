# MC1 physical regression suite (MC1-AUTO-2)

Single entry point for MC1 physical validation against real iPad + iPhone
hardware. Every run is automatically archived: backend state dumps, device
console logs, extracted Debug Snapshots, and a PASS/FAIL report — all under
one timestamped directory. PASS/FAIL is decided exclusively from backend
ground truth (`cycle_devices[].recording_status`, `cycle.status`,
`session.status`); console/snapshot capture is corroborating evidence only.

## Prerequisites (manual, one-time per run)

1. Both devices unlocked, USB-paired, app installed from a build on the
   target branch/commit.
2. App already logged in on both devices (instructor on iPad, player on
   iPhone) and sitting on MainHubView — **do not** open Session Lab manually,
   the deep links do that.
3. Grant camera/microphone permission dialogs if iOS prompts (first run only).
4. Find device UDIDs:
   ```
   xcrun devicectl list devices
   ```

## Run

```bash
IPAD_UDID=<ipad-udid> \
IPHONE_UDID=<iphone-udid> \
API_BASE=https://practice-booking-system-git-deploy-vercel-staging-lfa-ec-test.vercel.app \
INSTRUCTOR_EMAIL=<instructor-email> \
INSTRUCTOR_PASSWORD=<instructor-password> \
PLAYER_EMAIL=<player-email> \
PLAYER_PASSWORD=<player-password> \
./scripts/run_mc1_regression.sh --scenario all
```

`--scenario` accepts `smoke`, `multicycle`, `retry`, `finalization`, or `all`
(default `all`). `--cycles N` (default 3) only affects `multicycle`.

## Supported scenarios

| Scenario | Status | What it checks |
|---|---|---|
| `smoke` | implemented | 1 cycle: join → mark-ready → begin-cycle → confirmed_start (both devices) → end-cycle → confirmed_stop (both devices). Fast sanity check. |
| `multicycle` | implemented | N cycles (default 3) in one session — same assertions as `smoke`, repeated, catching cross-cycle regressions (e.g. cycleIndex tracking). |
| `retry` | **registered, not implemented** | Orchestrator retry without full session reset — blocked on ORCH-7, which doesn't exist yet. Running it reports SKIPPED. |
| `finalization` | **registered, not implemented** | Session finalize flow — blocked on ORCH-8 (iOS finalize call not built). Running it reports SKIPPED. |

Add a new scenario by writing a `scenario_xxx(ctx) -> ScenarioReport` function
in `mc1_regression/scenarios.py` and registering it in `SCENARIOS`.

## What gets saved (per run)

```
scripts/mc1_regression_runs/<timestamp>_<scenario>/
  report.txt                          — human-readable PASS/FAIL summary
  report.json                         — same, structured
  backend_state/
    <scenario>_session.json           — GET session at scenario end
    <scenario>_cycles.json            — GET cycles at scenario end
    <scenario>_report.json            — per-scenario structured report
  console/
    ipad_console.log                  — full raw console capture (whole run)
    iphone_console.log                — full raw console capture (whole run)
    <scenario>_cco_lines.txt          — [CCO] lines scoped to this scenario
    <scenario>_pco_lines.txt          — [PCO] lines scoped to this scenario
  debug_snapshots/
    <scenario>_ipad_00.txt            — extracted Debug Snapshot text
    <scenario>_iphone_00.txt
    ...
```

This directory is gitignored (`scripts/mc1_regression_runs/`) — it is
per-run output, not source.

## Deep links (MC1-AUTO-1/2, DEBUG builds only)

| Action | Effect | Device |
|---|---|---|
| `lfa-mc1://automate?action=join&session_uuid=X&role=instructor\|player` | Opens Session Lab, joins/resumes session X | Both |
| `lfa-mc1://automate?action=mark-ready` | `vm.transitionToDevicesReady()` | iPad only |
| `lfa-mc1://automate?action=begin-cycle` | `vm.beginCycle()` | iPad only |
| `lfa-mc1://automate?action=end-cycle` | `vm.endCycle()` | iPad only |
| `lfa-mc1://automate?action=dump-snapshot` | Prints the Debug Snapshot text to console, wrapped in `[MC1-SNAPSHOT-BEGIN]`/`[MC1-SNAPSHOT-END]` | Either |

The player device only ever needs `join` — auto-prepare/auto-start/auto-stop
(ORCH-3/ORCH-4F) handle the rest with no further automation hooks.

## Direct Python invocation (no console capture)

```bash
python3 scripts/mc1_regression/runner.py \
  --scenario multicycle --cycles 5 \
  --out-dir /tmp/mc1-run \
  --api-base https://... \
  --ipad-udid <udid> --iphone-udid <udid> \
  --instructor-email ... --instructor-password ... \
  --player-email ... --player-password ...
```

Exit code `0` = all run scenarios PASS, `1` = at least one FAIL, `2` = fatal
error before any scenario completed.
