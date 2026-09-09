#!/usr/bin/env python3
"""MC1 static preflight check — no devices required.

Runs BEFORE any physical regression run (run_mc1_regression.sh). Catches the
classes of wiring bugs that have historically only surfaced physically —
missing deep-link actions, missing GoPro preview start, missing PCO attach,
missing skeleton feed wiring, device-routing mismatches, missing artifact
collectors, and log-capture misconfiguration — by statically inspecting the
Swift + Python source tree.

This does NOT replace unit tests or a physical run. It exists to make sure a
physical run is never spent re-discovering a bug that was already visible in
the source (see tricamera-capture-skeleton-proof FAIL history, commit
dfccd932 root-cause analysis, and the 2026-07-01 flow audit).

Usage:
    python3 scripts/mc1_regression/preflight_static_check.py
Exit code 0 = all checks PASS, 1 = at least one FAIL.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
IOS_MC = REPO_ROOT / "ios" / "LFAEducationCenter" / "MultiCamera"
SCENARIOS_PY = REPO_ROOT / "scripts" / "mc1_regression" / "scenarios.py"
LIB_PY = REPO_ROOT / "scripts" / "mc1_regression" / "lib.py"
RUN_SH = REPO_ROOT / "scripts" / "run_mc1_regression.sh"

FAILURES: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        PASSES.append(f"[PASS] {name}")
    else:
        FAILURES.append(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── CHECK 1: deep-link action parity (bridge enum ↔ handle() ↔ dispatch) ────

def check_deep_link_parity() -> None:
    bridge = read(IOS_MC / "MC1AutomationBridge.swift")
    lobby = read(IOS_MC / "MultiCameraLobbyView.swift")

    enum_cases = set(re.findall(r"case (\w+)\((?:[^)]*)\)\s*$|case (\w+)\s*$",
                                 re.search(r"enum MC1AutomationAction.*?\n}", bridge, re.S).group(0),
                                 re.M))
    enum_cases = {a or b for a, b in enum_cases if (a or b)}

    # Actions are posted via post(.case) since the sequenced-envelope refactor
    # (P0 hardening, 2026-07-04 — replay protection).
    handled_actions = set(re.findall(r'post\(\.(\w+)', bridge))
    dispatch_cases = set(re.findall(r'case \.(\w+)\(', lobby)) | set(re.findall(r'case \.(\w+):', lobby))

    missing_dispatch = handled_actions - dispatch_cases
    check(
        "every MC1AutomationBridge action has a MultiCameraLobbyView dispatch case",
        not missing_dispatch,
        f"missing dispatch for: {sorted(missing_dispatch)}" if missing_dispatch else "",
    )

    missing_enum = enum_cases - handled_actions
    check(
        "every MC1AutomationAction enum case is set by handle(url:)",
        not missing_enum,
        f"declared but never set: {sorted(missing_enum)}" if missing_enum else "",
    )


# ── CHECK 2: GoPro preview must start BEFORE begin-cycle in the proof scenario ──

def check_gopro_preview_before_begin_cycle() -> None:
    src = read(SCENARIOS_PY)
    fn_match = re.search(
        r"def scenario_tricamera_capture_skeleton_proof.*?(?=\ndef scenario_gopro_network_routing_diag)",
        src, re.S,
    )
    if not fn_match:
        check("gopro-stream-start precedes begin-cycle in tricamera-capture-skeleton-proof", False,
              "scenario function not found")
        return
    body = fn_match.group(0)
    stream_start_pos = body.find('"gopro-stream-start"')
    begin_cycle_pos = body.find('"begin-cycle"')
    ok = stream_start_pos != -1 and begin_cycle_pos != -1 and stream_start_pos < begin_cycle_pos
    check(
        "gopro-stream-start precedes begin-cycle in tricamera-capture-skeleton-proof",
        ok,
        f"stream_start_pos={stream_start_pos} begin_cycle_pos={begin_cycle_pos}",
    )

    # FIXED 2026-07-01 (was: KNOWN GAP). The .goProStreamStart case used to discard
    # GoProStreamProbe.run()'s diag dict (`_ = await ...`). It must now capture the
    # result and write it via GoProStreamDiagWriter, matching every other GoPro POC
    # action (goProPreviewPOC, goProCombinedCycleProof, goProPreviewAspectProbe).
    lobby_src = read(IOS_MC / "MultiCameraLobbyView.swift")
    stream_case_pos = lobby_src.find("case .goProStreamStart:")
    stream_case_body = lobby_src[stream_case_pos:stream_case_pos + 1200] if stream_case_pos != -1 else ""
    # Strip `//` line comments before pattern matching — the fix's own explanatory
    # comment quotes the OLD discarded-diag pattern as documentation, which would
    # otherwise false-positive match the "still discarded" check below.
    code_only = "\n".join(
        line for line in stream_case_body.splitlines() if not line.strip().startswith("//")
    )
    diag_written = bool(re.search(
        r"let diag = await GoProStreamProbe\.shared\.run\(.*?\).*?GoProStreamDiagWriter\.write\(diag\)",
        code_only, re.S,
    ))
    diag_discarded = bool(re.search(r"_\s*=\s*await GoProStreamProbe\.shared\.run\(", code_only))
    check(
        "gopro-stream-start writes its GoProStreamProbe diag (not discarded)",
        diag_written and not diag_discarded,
        "MultiCameraLobbyView.swift .goProStreamStart case discards the run() result "
        "instead of writing it via GoProStreamDiagWriter" if not (diag_written and not diag_discarded) else "",
    )


# ── CHECK 3: PlayerCaptureOrchestrator.attach() must be role-gated ──────────
#
# FIXED 2026-07-01 (was: KNOWN BUG). autoRegisterDevice() in
# MultiCameraSessionViewModel.swift used to call orch.attach(...) unconditionally,
# for BOTH instructor and player devices. Since the instructor (iPhone) and
# the player (iPad) share the same backend cycle-status stream, this meant
# the instructor's OWN PlayerCaptureOrchestrator independently reacted to the
# same cycle as the instructor's CycleCaptureOrchestrator, racing it to call
# confirmDeviceStart/confirmDeviceStop for the SAME (instructor) device_id.
# If PCO's call landed first, CCO's own confirm-start got a stale-revision
# 409 — and CCO's error handler treats ANY confirm-start httpError as fatal,
# calling captureController.stopCapture() and tearing down the instructor's
# OWN recording even though the backend already showed confirmed_start=true.
#
# Fix: MultiCameraSessionViewModel.shouldAttachPlayerCaptureOrchestrator(deviceRole:)
# is an explicit POSITIVE allow-list (playerPrimary/playerSecondary only), and
# autoRegisterDevice() must call it to gate orch.attach(). This check verifies
# BOTH that the static gate function exists with the right truth table AND that
# autoRegisterDevice() actually calls it before orch.attach() — so a future
# refactor can't silently drop the gate while leaving the function behind.

def check_pco_attach_role_gated() -> None:
    src = read(IOS_MC / "MultiCameraSessionViewModel.swift")

    # 3a. The gate function itself must exist with the correct positive truth table.
    gate_fn_match = re.search(
        r"static func shouldAttachPlayerCaptureOrchestrator\(deviceRole: MCDeviceRole\) -> Bool \{.*?\n    \}",
        src, re.S,
    )
    gate_fn_ok = bool(gate_fn_match)
    if gate_fn_ok:
        gate_body = gate_fn_match.group(0)
        gate_fn_ok = (
            re.search(r"case \.playerPrimary, \.playerSecondary:\s*\n\s*return true", gate_body) is not None
            and re.search(r"case \.instructorPrimary, \.auxiliaryCamera:\s*\n\s*return false", gate_body) is not None
        )
    check(
        "shouldAttachPlayerCaptureOrchestrator() exists with correct positive truth table",
        gate_fn_ok,
        "expected a static func with playerPrimary/playerSecondary → true, "
        "instructorPrimary/auxiliaryCamera → false" if not gate_fn_ok else "",
    )

    # 3b. autoRegisterDevice() must call the gate function before orch.attach(...).
    fn_match = re.search(r"private func autoRegisterDevice.*?\n    }\n", src, re.S)
    if not fn_match:
        check("orch.attach() is gated by shouldAttachPlayerCaptureOrchestrator()", False,
              "autoRegisterDevice() not found")
        return
    body = fn_match.group(0)
    attach_match = re.search(r"orch\.attach\(", body)
    if not attach_match:
        check("orch.attach() is gated by shouldAttachPlayerCaptureOrchestrator()", False,
              "orch.attach( call not found in autoRegisterDevice()")
        return
    preceding = body[:attach_match.start()]
    gate_call = re.search(r"(if|guard)[^{]*\bshouldAttachPlayerCaptureOrchestrator\(", preceding, re.S)
    ok = False
    if gate_call:
        after_gate = preceding[gate_call.end():]
        opens = after_gate.count("{")
        closes = after_gate.count("}")
        ok = closes <= opens  # brace not yet closed → attach is still inside the gated block
    check(
        "orch.attach() is gated by shouldAttachPlayerCaptureOrchestrator()",
        ok,
        "orch.attach() in autoRegisterDevice() is not guarded by an if/guard calling "
        "shouldAttachPlayerCaptureOrchestrator(deviceRole:) — the instructor's own PCO would "
        "independently race CCO for confirmDeviceStart/Stop on the instructor's own device_id "
        "(2026-07-01 flow audit finding)."
        if not ok else "",
    )


# ── CHECK 4: status dashboard wiring (MC2-PR3 — backend-polled, no frames) ──

def check_skeleton_feed_wiring() -> None:
    # Renamed purpose in MC2-PR3: the skeleton/live-frame feed is gone; what
    # must be wired now is the polled-state path — the dashboard reads the VM's
    # latestCycle (backend evidence), and the VM actually polls it.
    src = read(IOS_MC / "InstructorDashboardView.swift")
    check("dashboard reads vm.latestCycle (backend-polled cycle evidence)",
          "vm.latestCycle" in src)
    vm_src = read(IOS_MC / "MultiCameraSessionViewModel.swift")
    check("view model polls listCycles for the controller (latestCycle feed)",
          bool(re.search(r"if self\.isController \{.*?listCycles", vm_src, re.S)))


# ── CHECK 5: device routing (MC2-PR1 final topology: iPad=non-recording ──────
#    instructor, iPhone=player + GoPro bridge, GoPro managed_by the player)

def check_device_routing() -> None:
    lib_src = read(LIB_PY)
    ctx_match = re.search(r"class ScenarioContext:.*?(?=\n\n@dataclass|\Z)", lib_src, re.S)
    ok_ipad = bool(ctx_match and re.search(r'ipad_role:\s*str\s*=\s*"instructor"', ctx_match.group(0)))
    ok_iphone = bool(ctx_match and re.search(r'iphone_role:\s*str\s*=\s*"player"', ctx_match.group(0)))
    check("ScenarioContext default: iPad role = instructor (non-recording)", ok_ipad)
    check("ScenarioContext default: iPhone role = player", ok_iphone)

    scenarios_src = read(SCENARIOS_PY)
    fn_match = re.search(
        r"def scenario_tricamera_capture_skeleton_proof.*?(?=\ndef scenario_gopro_network_routing_diag)",
        scenarios_src, re.S,
    )
    gopro_ok = bool(fn_match and re.search(
        r'device_role="auxiliary_camera".*?managed_by_device_id=player_id', fn_match.group(0), re.S))
    check("GoPro registered as auxiliary_camera managed_by player_id", gopro_ok)


# ── CHECK 6: artifact collector completeness ────────────────────────────────

def check_artifact_collectors() -> None:
    scenarios_src = read(SCENARIOS_PY)
    fn_match = re.search(
        r"def scenario_tricamera_capture_skeleton_proof.*?(?=\ndef scenario_gopro_network_routing_diag)",
        scenarios_src, re.S,
    )
    body = fn_match.group(0) if fn_match else ""
    checks = {
        "iphone_capture_metadata.json collected": "capture_metadata_diag.json" in body and "iphone_capture_metadata" in body,
        "ipad_capture_metadata.json collected": "ipad_capture_metadata" in body,
        "skeleton_output.json collected": "skeleton_output.json" in body,
        "gopro media evidence checked (console log grep)": "GOPRO-MEDIA-BEGIN" in body,
        # FIXED 2026-07-01 (was: KNOWN GAP). gopro-stream-start used to discard
        # GoProStreamProbe's diag dict (`_ = await GoProStreamProbe.shared.run(...)` in
        # MultiCameraLobbyView.swift's .goProStreamStart case), so no gopro_stream_diag.json
        # was ever written for THIS scenario and there was zero automated evidence that the
        # GoPro preview actually decoded frames. Now the dict is written and collected here,
        # AND gated (see check below) — not just corroborating evidence.
        "gopro_stream_diag.json collected (preview decode evidence)": "gopro_stream_diag" in body,
    }
    for name, ok in checks.items():
        check(name, ok)

    # GoPro preview quality must actually GATE the PASS, not just be collected as
    # corroborating evidence — a scenario that only writes the file but never checks
    # udpPacketsReceived/videoPIDFound/decodeSuccesses would let a silently-dead GoPro
    # preview report PASS.
    #
    # Scoped to the tricamera scenario BODY (2026-07-04 hardening) — the previous
    # whole-file regex matched any `critical_ok = all(` anywhere before the step
    # string anywhere, i.e. it never actually verified THIS scenario's gate.
    critical_block_match = re.search(r"critical_ok = all\(.*?\n        \)", body, re.S)
    critical_block = critical_block_match.group(0) if critical_block_match else ""
    check("tricamera scenario has a critical_ok gate block", bool(critical_block))

    gate_ok = '"gopro preview stream quality"' in body and \
        '"gopro preview stream quality"' in critical_block
    check("gopro preview stream quality gates PASS (critical_ok)", gate_ok)

    # MC2-PR3 (no-MPC): the live-panel pose-overlay layer is REMOVED — writer,
    # deep link and reader together (key-contract rule). The scenario must not
    # reference pose_overlay_diag at all anymore.
    panel_names = ("instructor", "player", "gopro")
    pose_removed = "pose_overlay_diag" not in body
    pose_not_gated = all(f'"{p} panel frame traffic"' not in critical_block for p in panel_names)
    check("pose_overlay_diag fully removed from tricamera scenario (MC2-PR3 no-MPC)", pose_removed)
    check("per-panel frame traffic is NOT in critical_ok (no-MPC decision)", pose_not_gated)


# ── CHECK 8: MPC/live-panel layer stays removed (MC2-PR3 regression guard) ──

def check_mpc_layer_removed() -> None:
    """MC2-PR3 removed the MPC streaming + live pose overlay layer after the
    2026-07-12 dual-player run froze the iPad (main-actor frame publish storm ×
    2 players × 3 on-device pose processors). This guard keeps it removed: no
    hidden fallback, no re-introduced frame-processing path on the dashboard."""
    for gone in ("CameraStreamService.swift", "CameraFramePublisher.swift",
                 "RemoteCameraView.swift", "LivePoseOverlayProcessor.swift"):
        check(f"{gone} stays deleted (no-MPC architecture)", not (IOS_MC / gone).exists())

    bridge_src = read(IOS_MC / "MC1AutomationBridge.swift")
    check("pose-overlay-diag deep link action removed from MC1AutomationBridge",
          "pose-overlay-diag" not in bridge_src and "poseOverlayDiag" not in bridge_src)

    dashboard_src = read(IOS_MC / "InstructorDashboardView.swift")
    for banned in ("AVCaptureSession", "CameraStreamService", "LivePoseOverlayProcessor",
                   "RemoteCameraView", "lastFrame", "UIImage("):
        check(f"InstructorDashboardView has no live-frame path ({banned})",
              banned not in dashboard_src)
    check("InstructorDashboardView panels resolve via DevicePanelStateResolver",
          "DevicePanelStateResolver.resolve" in dashboard_src)


# ── CHECK 9: orientation/aspect wiring + no-distorting-stretch ──────────────

def check_orientation_aspect_wiring() -> None:
    capture_mgr_src = read(IOS_MC / "SessionCaptureManager.swift")
    ground_truth_ok = "orientationAtRecordingStart" in capture_mgr_src and bool(re.search(
        r"orientationAtRecordingStart = OrientationMapper\.currentOrientationLabel", capture_mgr_src))
    check("SessionCaptureManager captures orientationAtRecordingStart as live ground truth", ground_truth_ok)

    consistency_fields = ["deviceOrientationAtRecordingStart", "fileOrientationCoarse",
                          "orientationConsistent", "effectiveAspectRatio"]
    consistency_ok = all(f in capture_mgr_src for f in consistency_fields)
    check("CaptureMetadataDiagWriter computes orientation-consistency + effective aspect fields",
          consistency_ok,
          f"missing: {[f for f in consistency_fields if f not in capture_mgr_src]}" if not consistency_ok else "")

    scenarios_src = read(SCENARIOS_PY)
    fn_match = re.search(
        r"def scenario_tricamera_capture_skeleton_proof.*?(?=\ndef scenario_gopro_network_routing_diag)",
        scenarios_src, re.S,
    )
    body = fn_match.group(0) if fn_match else ""
    critical_block_match = re.search(r"critical_ok = all\(.*?\n        \)", body, re.S)
    critical_block = critical_block_match.group(0) if critical_block_match else ""
    # MC2-PR1 final topology: the iPad is a NON-RECORDING instructor — its old
    # orientation/aspect gates are replaced by the negative-evidence gate
    # ("instructor no capture output"). Only the player iPhone still records
    # on the iOS side, so orientation/aspect stays pinned for it + the GoPro.
    orientation_gate_steps = [
        "iphone orientation consistent", "iphone effective aspect ratio matches orientation",
        "iphone encoded aspect ratio is 16:9",
        "instructor no capture output",
        "gopro preview aspect ratio is 16:9",
    ]
    scenario_asserts_ok = all(f'"{step}"' in body for step in orientation_gate_steps)
    missing_steps = [s for s in orientation_gate_steps if f'"{s}"' not in body]
    check("scenario asserts player orientation/aspect + instructor no-capture (MC2-PR1)",
          scenario_asserts_ok,
          f"missing report.step(...) for: {missing_steps}" if not scenario_asserts_ok else "")
    # Scoped to the tricamera critical_ok block (2026-07-04 hardening) — the
    # previous whole-file regex could match a different scenario's gate.
    gated_ok = all(f'"{step}"' in critical_block for step in orientation_gate_steps)
    check("orientation/aspect assertions gate PASS (critical_ok)", gated_ok)

    # The effective-aspect EXPECTATION must be orientation-aware (2026-07-04
    # physical-run proof): a portrait-mandated run (RC checklist J / #357)
    # records a correct 9:16 effective aspect, so an unconditional 16:9
    # expectation is unsatisfiable there. Pin the mapping itself.
    aware_ok = bool(re.search(r'"portrait":\s*\(9,\s*16\)', scenarios_src)) \
        and bool(re.search(r'"landscape":\s*\(16,\s*9\)', scenarios_src))
    check("effective-aspect expectation is orientation-aware (portrait→9:16, landscape→16:9)", aware_ok)

    # (The former GoPro preview-panel aspect check is gone with the panel itself:
    # MC2-PR3 removed every live preview from the dashboard — status tiles only.)


# ── CHECK 7: dual console log capture is distinctness-guarded ───────────────

def check_log_capture_config() -> None:
    src = read(RUN_SH)
    ok = bool(re.search(r'_IPAD_LEGACY_UDID.*==.*_IPHONE_LEGACY_UDID', src))
    check("run_mc1_regression.sh guards against iPad/iPhone UDID collision (duplicate log)", ok)
    ok_override = "IPHONE_LEGACY_UDID" in src and "IPAD_LEGACY_UDID" in src
    check("run_mc1_regression.sh supports manual UDID override env vars", ok_override)

    # P0 hardening (2026-07-04): legacy UDIDs must come from a real CoreDevice→legacy
    # mapping (devicectl list devices --json-output → hardwareProperties.udid), NOT
    # from `idevice_id -l` enumeration order — order-based assignment silently swapped
    # the iPhone/iPad console logs and misattributed every console-grounded check.
    mapping_ok = "_map_legacy_udid" in src and "hardwareProperties" in src
    check("console log capture maps legacy UDIDs via devicectl identity (not list order)",
          mapping_ok)
    order_heuristic = bool(re.search(r'_LEGACY_UDIDS.*\|\s*head -1', src)) or \
        bool(re.search(r"sed -n '2p'", src))
    check("order-based legacy-UDID guessing (head -1 / sed -n 2p) is gone",
          not order_heuristic)

    # 2026-07-04 RCA: tricamera ran with the iPad console capture silently
    # SKIPPED (WARN only) — the player-side failure left zero console evidence.
    # Both the shell wrapper and the runner must hard-fail tricamera scenarios
    # when either device console capture is unavailable.
    check("run_mc1_regression.sh hard-fails tricamera scenarios without dual console capture",
          "Dual-console hard precondition" in src and "exit 1" in src)
    runner_src = read(REPO_ROOT / "scripts" / "mc1_regression" / "runner.py")
    lib_src = read(REPO_ROOT / "scripts" / "mc1_regression" / "lib.py")
    check("runner.py enforces check_dual_console_precondition before scenarios",
          "check_dual_console_precondition" in runner_src)
    check("lib.DUAL_CONSOLE_REQUIRED_SCENARIOS covers the tricamera proof",
          '"tricamera-capture-skeleton-proof"' in lib_src
          and "DUAL_CONSOLE_REQUIRED_SCENARIOS" in lib_src)


# ── CHECK 11: pose overlay diag writer↔reader key contract ──────────────────
#
# P0 hardening (2026-07-04 review): the tricamera panel gate read a per-panel
# key the Swift writer never emits, so every panel gate was a guaranteed false
# FAIL. This check re-derives both sides of the contract from source; the same
# contract is also pinned at test time by tests/test_diag_contract.py.

def check_pose_diag_key_contract() -> None:
    # MC2-PR3: the pose-overlay writer is gone — the contract is now that NO
    # reader remains either (a stale panel.get() reader would silently read
    # nothing on a physical test day).
    scenarios_src = read(SCENARIOS_PY)
    reader_keys = set(re.findall(r'panel\.get\("(\w+)"', scenarios_src))
    check("no per-panel pose keys read anywhere (writer removed in MC2-PR3)",
          not reader_keys,
          f"scenarios.py still reads {sorted(reader_keys)} but "
          f"PoseOverlayDiagWriter no longer exists" if reader_keys else "")


# ── CHECK 12: stale-artifact invalidation + freshness gating ─────────────────

def check_stale_artifact_protection() -> None:
    lib_src = read(LIB_PY)
    helpers_ok = ("def invalidate_app_container_file(" in lib_src
                  and "def load_fresh_diag(" in lib_src
                  and "STALE_DIAG_SENTINEL_KEY" in lib_src)
    check("lib.py provides invalidate_app_container_file + load_fresh_diag + sentinel key",
          helpers_ok)

    scenarios_src = read(SCENARIOS_PY)
    fn_match = re.search(
        r"def scenario_tricamera_capture_skeleton_proof.*?(?=\ndef scenario_gopro_network_routing_diag)",
        scenarios_src, re.S,
    )
    body = fn_match.group(0) if fn_match else ""

    # Invalidation must run BEFORE the first device interaction (join), and its
    # step must gate critical_ok — a failed invalidation means stale evidence
    # cannot be ruled out.
    inv_pos = body.find("_invalidate_stale_diags(")
    join_pos = body.find("_join_both_devices(")
    check("tricamera invalidates stale diag artifacts before joining devices",
          inv_pos != -1 and join_pos != -1 and inv_pos < join_pos,
          f"inv_pos={inv_pos} join_pos={join_pos}")
    critical_block_match = re.search(r"critical_ok = all\(.*?\n        \)", body, re.S)
    critical_block = critical_block_match.group(0) if critical_block_match else ""
    check("stale-diag invalidation step gates PASS (critical_ok)",
          '"stale diag artifacts invalidated"' in critical_block)

    # Every gating diag read in the tricamera scenario must go through the
    # freshness loader instead of raw json.loads.
    fresh_reads = body.count("load_fresh_diag(")
    # 4 call sites since MC2-PR3 (the pose-overlay read went away with the panel layer).
    check("tricamera gating diag reads use load_fresh_diag (>=4 call sites)",
          fresh_reads >= 4, f"found {fresh_reads} load_fresh_diag call(s)")


# ── CHECK 13: deep-link action replay protection (consume mechanism) ─────────

def check_action_consume_mechanism() -> None:
    bridge_src = read(IOS_MC / "MC1AutomationBridge.swift")
    envelope_ok = ("struct MC1SequencedAction" in bridge_src
                   and re.search(r"let seq: Int", bridge_src) is not None)
    check("MC1AutomationBridge posts sequenced action envelopes", envelope_ok)
    consume_ok = bool(re.search(
        r"func consume\(_ envelope: MC1SequencedAction\) -> Bool", bridge_src))
    check("MC1AutomationBridge.consume() exists (once-only claim per action)", consume_ok)

    # The lobby (sole bridge subscriber since MC2-PR3) must claim via consume()
    # before dispatching — otherwise a @Published replay on view rebuild re-runs
    # the last action (double GoPro shutter / spurious reset-session).
    lobby_src = read(IOS_MC / "MultiCameraLobbyView.swift")
    lobby_recv = re.search(r"onReceive\(MC1AutomationBridge\.shared\.\$lastAction.*?switch",
                           lobby_src, re.S)
    lobby_gated = bool(lobby_recv and "consume(" in lobby_recv.group(0))
    check("MultiCameraLobbyView dispatch is gated by consume()", lobby_gated)

    dash_src = read(IOS_MC / "InstructorDashboardView.swift")
    check("InstructorDashboardView no longer subscribes to the automation bridge",
          "MC1AutomationBridge" not in dash_src)


# ── CHECK 14: interactive scenarios excluded from unattended `all` ───────────

def check_interactive_scenarios_excluded_from_all() -> None:
    scenarios_src = read(SCENARIOS_PY)
    set_match = re.search(r"INTERACTIVE_SCENARIOS = \{(.*?)\}", scenarios_src, re.S)
    declared = set(re.findall(r'"([\w-]+)"', set_match.group(1))) if set_match else set()
    check("INTERACTIVE_SCENARIOS declared in scenarios.py", bool(declared))

    # Every scenario function that calls input() must be in the interactive set.
    # Map input() call positions back to their enclosing scenario name.
    fn_spans: list[tuple[int, str]] = [
        (m.start(), m.group(1)) for m in
        re.finditer(r'    report = ScenarioReport\(name="([\w-]+)"', scenarios_src)
    ]
    undeclared = set()
    for m in re.finditer(r"\binput\(", scenarios_src):
        owner = None
        for start, name in fn_spans:
            if start < m.start():
                owner = name
        if owner and owner not in declared:
            undeclared.add(owner)
    check("every input()-blocking scenario is declared interactive",
          not undeclared,
          f"scenario(s) with input() missing from INTERACTIVE_SCENARIOS: {sorted(undeclared)}"
          if undeclared else "")

    runner_src = read(REPO_ROOT / "scripts" / "mc1_regression" / "runner.py")
    filter_ok = "INTERACTIVE_SCENARIOS" in runner_src and bool(re.search(
        r'k not in INTERACTIVE_SCENARIOS', runner_src))
    check("runner.py excludes INTERACTIVE_SCENARIOS from --scenario all", filter_ok)


# ── CHECK 10: SKIP_STATIC_PREFLIGHT can never produce a valid PASS ─────────

def check_skip_preflight_cannot_pass() -> None:
    run_sh_src = read(RUN_SH)
    reason_required = bool(re.search(
        r'SKIP_STATIC_PREFLIGHT_REASON.*ERROR: SKIP_STATIC_PREFLIGHT=1 requires', run_sh_src, re.S,
    )) or bool(re.search(
        r'-z "\$\{SKIP_STATIC_PREFLIGHT_REASON:-\}"', run_sh_src,
    ))
    check("SKIP_STATIC_PREFLIGHT=1 requires a non-empty SKIP_STATIC_PREFLIGHT_REASON", reason_required)

    audit_logged = "static_preflight_skip_audit.log" in run_sh_src
    check("SKIP_STATIC_PREFLIGHT usage is appended to an audit log", audit_logged)

    env_passthrough = "MC1_STATIC_PREFLIGHT_SKIPPED" in run_sh_src
    check("run_mc1_regression.sh passes MC1_STATIC_PREFLIGHT_SKIPPED to runner.py", env_passthrough)

    runner_src = read(REPO_ROOT / "scripts" / "mc1_regression" / "runner.py")
    forces_fail = bool(re.search(
        r'if static_preflight_skipped:\s*\n\s*overall_pass = False', runner_src,
    ))
    check("runner.py forces overall_pass=False when static preflight was skipped", forces_fail)
    stamped = "static_preflight_skipped" in runner_src and "static_preflight_skip_reason" in runner_src
    check("report.json/report.txt are stamped with static_preflight_skipped + reason", stamped)


def main() -> int:
    check_deep_link_parity()
    check_gopro_preview_before_begin_cycle()
    check_pco_attach_role_gated()
    check_skeleton_feed_wiring()
    check_device_routing()
    check_artifact_collectors()
    check_mpc_layer_removed()
    check_orientation_aspect_wiring()
    check_log_capture_config()
    check_skip_preflight_cannot_pass()
    check_pose_diag_key_contract()
    check_stale_artifact_protection()
    check_action_consume_mechanism()
    check_interactive_scenarios_excluded_from_all()

    print("=== MC1 static preflight check ===\n")
    for line in PASSES:
        print(line)
    for line in FAILURES:
        print(line)
    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
