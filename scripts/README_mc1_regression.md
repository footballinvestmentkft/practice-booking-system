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
| `gopro-preview-poc` | implemented, **POC, requires manual operator step** | GoPro live preview (docs/GOPRO_LIVE_PREVIEW_POC_PLAN.md). Repeats the same manual-WiFi-join flow as `gopro-network-routing-diag`, then triggers `GoProStreamProbe` on-device and pulls `gopro_stream_diag.json` for a layer-by-layer (HTTP/UDP/MPEG-TS/NAL/decode) PASS/FAIL. |
| `gopro-combined-cycle-proof` | implemented, **Block 3, requires manual operator step** | GoPro preview + recording run concurrently (shutter/start + stream/start at the same time), proven via `media/list` diffing (a genuinely new file on the SD card) and `gopro_recording_diag.json`/`gopro_stream_diag.json` — not console logs. Same manual-WiFi-join flow as the other GoPro scenarios. |
| `gopro-camera-state-probe` | implemented, **read-only, requires manual operator step** | Capture Quality block, step 1: captures the GoPro's raw `camera/state` HTTP response (`gopro_camera_state_diag.json`) — does NOT change any camera setting. Exists because `GoProCameraStatus` has decoded `firmware="unknown"` on every physical run so far, meaning its flat-field decode likely never matched HERO13's real response shape; this surfaces the raw JSON so a human can read the actual current preset. |
| `capture-quality-proof` | implemented, **no manual step** | Capture Quality + Metadata block: runs one ordinary `smoke` cycle (iPad + iPhone both recording locally), then pulls `capture_metadata_diag.json` from BOTH devices and validates the explicit 720p/30fps-or-360p/30fps-fallback profile actually took effect (not the old device-default `.high` preset) — resolution, fps, codec, and orientation are all checked per device. |
| `gopro-preview-aspect-probe` | implemented, **requires manual operator step** | **Distinct from `gopro-camera-state-probe`** (which never starts the preview — HTTP read only). This one actually starts the live preview stream, decodes frames, and measures the REAL decoded width/height/aspect ratio (`gopro_preview_aspect_diag.json`) — because the archival recording profile (camera/state settings) and the preview stream's actual geometry are independent and were never measured together before. A human should also visually confirm a live GoPro image on the dashboard during the run; the script only verifies decoded-frame count, not pixel content. |
| `gopro-preset-write-validation` | implemented, **WRITES a camera setting, requires manual operator step** | The first GoPro scenario that actually mutates camera state (`VideoAspectRatio=8:7`, `VideoResolution` 8:7-compatible, `FPS=30`), with **mandatory rollback** on any failure at any step (write/verify/recording-proof/preview-after-write). PASS is STRICT: only a full unbroken chain counts — any rollback-triggered path is a handled FAIL, and rollback itself failing is a CRITICAL FAIL requiring manual camera inspection. Pulls 6 artifacts: `gopro_preset_before_diag.json`, `gopro_preset_write_diag.json`, `gopro_preset_after_diag.json`, `gopro_recording_diag.json`, `gopro_preview_aspect_diag.json`, `gopro_preset_final_diag.json`. |
| `tricamera-capture-skeleton-proof` | implemented, **3-camera proof, requires manual operator step** | End-to-end 3-camera proof: iPhone (instructor + GoPro controller) + iPad (player) + GoPro (auxiliary). Runs one full capture cycle, then: (1) collects `capture_metadata_diag.json` from both iOS devices via `copy_app_container_file` (no console log parsing — contains `fileSizeBytes`, `outputFilePath`, `actualDurationSeconds`, `codec`); (2) runs Vision-based skeleton processing on iPhone's video and collects `skeleton_output.json` from `Documents/` (also via `copy_app_container_file`); (3) fetches GoPro media list as evidence. **PASS is backend-grounded**: only `instructor+player confirmed_start`, `gopro confirmed_start`, `all 3 confirmed_stop`, and `timestamp sync report` gate the result — artifact collection steps are corroborating evidence that do not block PASS. Same GoPro WiFi manual join flow as other GoPro scenarios (operator gate in terminal). |

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
2. Operator manually joins the GoPro WiFi AP via iPhone Settings → Wi-Fi,
   then confirms in the terminal (the script blocks on this — see below).
3. The script resends the `gopro-connect` deep link, which the app reads as
   "manual join confirmed" and verifies GoPro HTTP reachable.
4. `fetchCameraState` succeeds.
5. Backend reports `device_status=ready` for the GoPro device.
6. `[NET-DIAG]` and `[GOPRO-AUTO]` lines present in the iPhone console
   artifact (corroborating evidence, not PASS/FAIL gates).

**Important code-level note:** the app's `waitAndSignalGoProReady` loop only
watches the connection state for 45 seconds after each `gopro-connect` deep
link, and the manual-join confirmation (`confirmManualWiFiJoined`) only
fires when a `gopro-connect` deep link arrives while the app is already in
`awaitingManualWiFiJoin`. Joining WiFi by hand does **not** auto-resume the
flow — that's why the script pauses on an `ENTER` prompt and resends the
deep link itself right after you confirm.

### Physical test steps (plain language, for the person running the test)

1. **Turn on the GoPro** and put it into pairing/discoverable mode (the
   Bluetooth icon on its screen should be blinking).
2. **Plug the iPhone into your Mac via USB** (needed for console capture).
3. **On the iPhone, open the LFA app** and make sure you're on the main
   screen (not Session Lab — the script opens that for you).
4. **Start the script** (`./scripts/run_mc1_regression.sh --scenario
   gopro-network-routing-diag ...` — see Run section above for the full
   env vars). Don't touch the phone yet — the script joins the session and
   opens Session Lab on the iPhone for you.
5. **Watch the terminal.** When you see these four lines, that's your cue
   that the app has started trying to connect to the GoPro:
   ```
   [net-diag] >>> GoPro Wi-Fi auto-join unavailable under current provisioning
   [net-diag] >>> Manual action required: iPhone Settings -> Wi-Fi -> select GoPro SSID
   [net-diag] >>> Return to LFA app after Wi-Fi connection
   [net-diag] >>> App will verify GoPro HTTP and signal backend ready
   ```
6. **A few seconds later** the terminal prints a line telling you to check
   the console log for the exact GoPro WiFi network name:
   ```
   [net-diag] >>> Check console/iphone_console.log just written above for
   'gopro_connection: Csatlakozz: <SSID>' — that <SSID> is the GoPro WiFi network name.
   ```
   Open `scripts/mc1_regression_runs/<latest-timestamp>_gopro-network-routing-diag/console/iphone_console.log`
   in another window (or just scroll the terminal if it's printed there too)
   and find that line — it tells you exactly which Wi-Fi network to join.
7. **Go to iPhone Settings → Wi-Fi** and tap that exact network name in the
   list (GoPro SSIDs look like `GP13XXXXXXXXX`). Enter the GoPro's WiFi
   password if asked (shown on the GoPro's own screen).
8. **Once the iPhone shows it's connected to the GoPro WiFi**, go back to
   the LFA app (press the Home button / swipe up, then tap the app icon
   again — don't force-quit it). You should still be on the Session Lab
   screen.
9. **Now, and only now, go back to the terminal** — it is waiting at a
   prompt:
   ```
   [net-diag] >>> Press ENTER here ONLY after the iPhone shows it is
   connected to the GoPro WiFi network:
   ```
   Press **Enter**. This tells the script to resend the connect signal to
   the app, which is what actually makes it check the GoPro and tell the
   backend it's ready.
10. **Wait up to ~3 minutes.** You don't need to touch the phone again —
    watch the terminal.
11. **Watch the terminal.** You'll see either:
    - `[net-diag] === PASS: GoPro WiFi + backend cellular routing CONFIRMED ===`
      → done, test passed.
    - `[net-diag] === FAIL: ...===` → something didn't connect in time; check
      that you joined the right WiFi network (step 6's exact SSID) and that
      the GoPro stayed powered on and didn't go to sleep.
12. **No need to restart anything** — neither the iPhone nor the GoPro
    need a reboot for this test.

### If it FAILs — what to capture before retrying

idevicesyslog does not reliably capture Swift `print()` output on a physical
device (privacy redaction depends on attach timing / lock state — in one run
the entire `[NET-DIAG]`/`[GOPRO-AUTO]`/`[GoPro]` line set was silently
missing from `console/iphone_console.log` despite the app working as
expected). Because of that, **do not rely on the console log as your primary
evidence.** Instead:

- **Check `gopro_diag.json` in the run's artifact directory first.** The app
  writes this file directly (via `GoProDiagRecorder`, see
  `MultiCameraLobbyView.swift`) every time it attempts to tell the backend
  the GoPro is ready, success or failure. The script pulls it automatically
  on both PASS and FAIL via `devicectl device copy from --domain-type
  appDataContainer` — no console log parsing involved. It contains:
  `timestamp`, `goProDeviceId`, `localState` (the app's GoPro connection
  state at attempt time), `outcome` (`signalReady_ok` /
  `signalReady_failed` / `connect_failed` / `wait_timeout_45s` /
  `skipped_no_context`), `httpStatus` (if the backend responded with an
  HTTP error), and `detail` (URLError code or backend response body).
  The script also prints this inline:
  `[net-diag] gopro_diag.json: outcome=... localState=... httpStatus=... detail=... timestamp=...`
  — and the FAIL message itself now includes these fields directly.
- If `gopro_diag.json` could not be collected (printed as `copy or parse
  failed`), the iPhone's installed build predates commit `8bc3a204`'s
  follow-up `GoProDiagRecorder` fix — reinstall a current build first.
- **Screenshot the iPhone's Settings → Wi-Fi screen** showing which network
  it's actually connected to (confirms whether it really joined the GoPro
  AP or fell back to your normal WiFi/cellular).
- **Copy the full terminal output** from the `[net-diag] === FAIL` line
  upward to the most recent `[net-diag] >>>` block.
- **Copy** `scripts/mc1_regression_runs/<latest-timestamp>_gopro-network-routing-diag/`
  in full (both `gopro_diag.json` and `console/iphone_console.log`, even if
  the latter turns out empty of useful lines).
- **Note whether the GoPro's screen was still on** when the test failed —
  GoPro HERO13 can auto-sleep its WiFi AP after a few minutes of no client
  activity, which looks identical to a routing failure from the app's side.

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
