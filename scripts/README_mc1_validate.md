# MC1 3-cycle physical validation (MC1-AUTO-1)

Automates the 3-cycle MC1 physical test (instructor iPad + player iPhone)
without manual button taps. PASS/FAIL is decided from backend ground truth
(`cycle_devices[].recording_status`), not from on-device UI.

## Prerequisites (manual, one-time)

1. Both devices unlocked, USB-paired, app installed from a build on the
   target branch/commit.
2. App already logged in on both devices (instructor on iPad, player on
   iPhone) and sitting on MainHubView — **do not** open Session Lab manually,
   the script's deep links do that.
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
./scripts/validate_mc1_3cycle.sh
```

## What it does

1. Logs in as instructor + player via `POST /auth/login`.
2. Creates a session via the backend API.
3. Sends `lfa-mc1://automate?action=join...` to both devices (`xcrun devicectl
   device send url`) — each device's app joins/registers exactly as if QR-scanned.
4. Polls until both devices are registered, then sends `mark-ready` to the iPad.
5. For 3 cycles: sends `begin-cycle` → polls until both devices report
   `confirmed_start` → waits 4s → sends `end-cycle` → polls until both report
   `confirmed_stop`.
6. Prints a PASS/FAIL line per cycle and an overall summary.

`validate_mc1_3cycle.sh` additionally captures `[CCO]`/`[PCO]` console logs from
both devices in parallel (`xcrun devicectl device console`) and prints them at
the end as corroborating evidence — they do not affect the PASS/FAIL verdict.

## Deep links (MC1-AUTO-1, DEBUG builds only)

| Action | Effect | Device |
|---|---|---|
| `lfa-mc1://automate?action=join&session_uuid=X&role=instructor\|player` | Opens Session Lab, joins/resumes session X | Both |
| `lfa-mc1://automate?action=mark-ready` | `vm.transitionToDevicesReady()` | iPad only |
| `lfa-mc1://automate?action=begin-cycle` | `vm.beginCycle()` | iPad only |
| `lfa-mc1://automate?action=end-cycle` | `vm.endCycle()` | iPad only |

The player device only ever needs `join` — auto-prepare/auto-start/auto-stop
(ORCH-3/ORCH-4F) handle the rest with no further automation hooks.

## Direct Python invocation (no console capture)

```bash
python3 scripts/validate_mc1_3cycle.py \
  --api-base https://... \
  --ipad-udid <udid> --iphone-udid <udid> \
  --instructor-email ... --instructor-password ... \
  --player-email ... --player-password ...
```

Exit code `0` = PASS (3/3 cycles, both devices confirmed_start + confirmed_stop
on each), `1` = FAIL, `2` = fatal error before cycles started.
