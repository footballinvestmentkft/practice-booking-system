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
| `gopro-network-routing-diag` | implemented, **requires manual operator step** | GoPro WiFi + backend cellular coexistence (Block 1). See [Block 1: GoPro WiFi manual join workaround](#block-1-gopro-wifi-manual-join-workaround) below — this scenario pauses mid-run for a human to join the GoPro WiFi AP from iPhone Settings. |

Add a new scenario by writing a `scenario_xxx(ctx) -> ScenarioReport` function
in `mc1_regression/scenarios.py` and registering it in `SCENARIOS`.

## Block 1: GoPro WiFi manual join workaround

**Status (as of commit `c02c04dc`): this is a physical-validation workaround,
not the final product UX.** In-app automatic GoPro WiFi join
(`NEHotspotConfiguration`) requires the
`com.apple.developer.networking.HotspotConfiguration` entitlement, and Apple
does not grant that entitlement to personal/free-tier Apple Developer teams.
Under the current provisioning (`DEVELOPMENT_TEAM = 4D7V9ZWVHY`, personal
team), `SystemWiFiTransport.joinAccessPoint` always throws `.unavailable`,
and the app falls back to `GoProConnectionState.awaitingManualWiFiJoin(ssid:)`
— a human must join the GoPro WiFi network by hand. Closing this gap for
real requires either a paid Apple Developer Program membership or a
different networking strategy (tracked separately, not part of this fix).

### PASS criteria (current, manual-join era)

1. App falls back to `awaitingManualWiFiJoin(ssid:)` (auto-join unavailable,
   handled gracefully — not a crash/hang).
2. Operator manually joins the GoPro WiFi AP via iPhone Settings → Wi-Fi.
3. App, back in foreground, confirms GoPro HTTP reachable.
4. `fetchCameraState` succeeds.
5. Backend reports `device_status=ready` for the GoPro device.
6. `[NET-DIAG]` and `[GOPRO-AUTO]` lines present in the iPhone console
   artifact (corroborating evidence, not PASS/FAIL gates).

### Physical test steps (plain language, for the person running the test)

1. **Turn on the GoPro** and put it into pairing/discoverable mode (the
   Bluetooth icon on its screen should be blinking).
2. **Plug the iPhone into your Mac via USB** (needed for console capture).
3. **On the iPhone, open the LFA app** and make sure you're on the main
   screen (not Session Lab — the script opens that for you).
4. **Start the script** (`./scripts/run_mc1_regression.sh --scenario
   gopro-network-routing-diag ...` — see Run section above for the full
   env vars). Don't touch the phone yet.
5. **Watch the terminal.** When you see these four lines, that's your cue:
   ```
   [net-diag] >>> GoPro Wi-Fi auto-join unavailable under current provisioning
   [net-diag] >>> Manual action required: iPhone Settings -> Wi-Fi -> select GoPro SSID
   [net-diag] >>> Return to LFA app after Wi-Fi connection
   [net-diag] >>> App will verify GoPro HTTP and signal backend ready
   ```
6. **Go to iPhone Settings → Wi-Fi** and tap the GoPro's network name in the
   list (it looks like `GP13XXXXXXXXX`). Enter the GoPro's WiFi password if
   asked (shown on the GoPro's own screen).
7. **Once the iPhone shows it's connected to the GoPro WiFi**, go back to
   the LFA app (press the Home button / swipe up, then tap the app icon
   again — don't force-quit it).
8. **Wait.** You don't need to do anything else — the app detects it's back
   in the foreground and automatically checks the GoPro connection and
   tells the backend it's ready. This can take up to ~3 minutes total from
   when the four lines appeared.
9. **Watch the terminal again.** You'll see either:
   - `[net-diag] === PASS: GoPro WiFi + backend cellular routing CONFIRMED ===`
     → done, test passed.
   - `[net-diag] === FAIL: ...===` → something didn't connect in time; check
     that you joined the right WiFi network and the GoPro stayed powered on.
10. **No need to restart anything** — neither the iPhone nor the GoPro
    need a reboot for this test.

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
