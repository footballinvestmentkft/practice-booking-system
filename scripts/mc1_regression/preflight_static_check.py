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

    handled_actions = set(re.findall(r'lastAction = \.(\w+)', bridge))
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


# ── CHECK 4: skeleton overlay feed wiring (all 3 panels) ────────────────────

def check_skeleton_feed_wiring() -> None:
    src = read(IOS_MC / "InstructorDashboardView.swift")
    checks = {
        "localPoseOverlay.attach(to:) wired on .onAppear": bool(
            re.search(r"\.onAppear\s*\{[^}]*localPoseOverlay\.attach\(to:", src, re.S)),
        "remotePoseOverlay fed from streamService.objectWillChange": bool(
            re.search(r"onReceive\(streamService\.objectWillChange\).*?remotePoseOverlay\.feed\(", src, re.S)),
        "goProPoseOverlay fed from goProStreamProbe.objectWillChange": bool(
            re.search(r"onReceive\(goProStreamProbe\.objectWillChange\).*?goProPoseOverlay\.feed\(", src, re.S)),
    }
    for name, ok in checks.items():
        check(name, ok)


# ── CHECK 5: device routing (iPhone=instructor, iPad=player, GoPro managed_by) ──

def check_device_routing() -> None:
    lib_src = read(LIB_PY)
    ctx_match = re.search(r"class ScenarioContext:.*?(?=\n\n@dataclass|\Z)", lib_src, re.S)
    ok_ipad = bool(ctx_match and re.search(r'ipad_role:\s*str\s*=\s*"player"', ctx_match.group(0)))
    ok_iphone = bool(ctx_match and re.search(r'iphone_role:\s*str\s*=\s*"instructor"', ctx_match.group(0)))
    check("ScenarioContext default: iPad role = player", ok_ipad)
    check("ScenarioContext default: iPhone role = instructor", ok_iphone)

    scenarios_src = read(SCENARIOS_PY)
    fn_match = re.search(
        r"def scenario_tricamera_capture_skeleton_proof.*?(?=\ndef scenario_gopro_network_routing_diag)",
        scenarios_src, re.S,
    )
    gopro_ok = bool(fn_match and re.search(
        r'device_role="auxiliary_camera".*?managed_by_device_id=instructor_id', fn_match.group(0), re.S))
    check("GoPro registered as auxiliary_camera managed_by instructor_id", gopro_ok)


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
    gate_ok = '"gopro preview stream quality"' in body and bool(re.search(
        r'critical_ok = all\(.*?"gopro preview stream quality".*?\)', scenarios_src, re.S,
    ))
    check("gopro preview stream quality gates PASS (critical_ok)", gate_ok)

    # Per-panel (instructor/player/gopro) pose overlay frame-traffic collection must also
    # be present AND gate critical_ok — same reasoning as the GoPro preview quality check.
    panel_names = ("instructor", "player", "gopro")
    pose_collected = "pose_overlay_diag" in body
    pose_gated = all(
        bool(re.search(rf'critical_ok = all\(.*?"{p} panel frame traffic".*?\)', scenarios_src, re.S))
        for p in panel_names
    )
    check("pose_overlay_diag.json collected (per-panel frame traffic)", pose_collected)
    check("per-panel frame traffic gates PASS (critical_ok) for instructor+player+gopro", pose_gated)


# ── CHECK 8: per-panel pose overlay diagnostics wiring (counters + export + deep link) ──

def check_pose_overlay_diagnostics_wiring() -> None:
    processor_src = read(IOS_MC / "LivePoseOverlayProcessor.swift")
    required_counters = [
        "framesReceived", "framesProcessed", "visionDetectionSuccesses",
        "framesWithSkeletonPoints", "lastFrameReceivedAt",
    ]
    counters_ok = all(c in processor_src for c in required_counters)
    check("LivePoseOverlayProcessor exposes all 5 required diagnostic counters", counters_ok,
          f"missing: {[c for c in required_counters if c not in processor_src]}" if not counters_ok else "")

    writer_ok = "enum PoseOverlayDiagWriter" in processor_src and "pose_overlay_diag.json" in processor_src
    check("PoseOverlayDiagWriter exists and targets pose_overlay_diag.json", writer_ok)

    bridge_src = read(IOS_MC / "MC1AutomationBridge.swift")
    action_ok = "poseOverlayDiag" in bridge_src and '"pose-overlay-diag"' in bridge_src
    check("pose-overlay-diag deep link action registered in MC1AutomationBridge", action_ok)

    dashboard_src = read(IOS_MC / "InstructorDashboardView.swift")
    export_ok = bool(re.search(
        r"case \.poseOverlayDiag = action.*?PoseOverlayDiagWriter\.write\(", dashboard_src, re.S,
    ))
    check("InstructorDashboardView exports pose overlay diag on pose-overlay-diag action", export_ok)

    stream_service_src = read(IOS_MC / "CameraStreamService.swift")
    source_counter_ok = "totalFramesReceived" in stream_service_src
    check("CameraStreamService exposes totalFramesReceived (player panel source-frame count)", source_counter_ok)

    gopro_probe_src = read(IOS_MC / "GoProStreamProbe.swift")
    gopro_source_ok = bool(re.search(r"@Published private\(set\) var decodeSuccesses", gopro_probe_src))
    check("GoProStreamProbe.decodeSuccesses is @Published (gopro panel source-frame count)", gopro_source_ok)


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
    orientation_gate_steps = [
        "iphone orientation consistent", "iphone effective aspect ratio is 16:9",
        "ipad orientation consistent", "ipad effective aspect ratio is 16:9",
        "gopro preview aspect ratio is 16:9",
    ]
    scenario_asserts_ok = all(f'"{step}"' in scenarios_src for step in orientation_gate_steps)
    missing_steps = [s for s in orientation_gate_steps if f'"{s}"' not in scenarios_src]
    check("scenario asserts orientation-consistency + 16:9 aspect for iPhone/iPad/GoPro",
          scenario_asserts_ok,
          f"missing report.step(...) for: {missing_steps}" if not scenario_asserts_ok else "")
    gated_ok = all(
        bool(re.search(rf'critical_ok = all\(.*?"{re.escape(step)}".*?\)', scenarios_src, re.S))
        for step in orientation_gate_steps
    )
    check("orientation/aspect assertions gate PASS (critical_ok)", gated_ok)

    # "No distorting stretch" is a SwiftUI layout property, not runtime data — verify the
    # GoPro preview panel uses aspectRatio(contentMode: .fit), which by definition letterboxes
    # instead of stretching, and does NOT use .fill or a fixed non-aspect frame() override.
    dashboard_src = read(IOS_MC / "InstructorDashboardView.swift")
    panel_pos = dashboard_src.find("private var goProPreviewPanel")
    panel_body = dashboard_src[panel_pos:panel_pos + 800] if panel_pos != -1 else ""
    uses_fit = bool(re.search(r"\.aspectRatio\(contentMode:\s*\.fit\)", panel_body))
    uses_fill = bool(re.search(r"\.aspectRatio\(contentMode:\s*\.fill\)", panel_body))
    no_stretch_ok = panel_pos != -1 and uses_fit and not uses_fill
    check("GoPro preview panel uses aspectRatio(.fit) — no distorting stretch", no_stretch_ok,
          "goProPreviewPanel not found or does not use .fit (or also uses .fill)" if not no_stretch_ok else "")


# ── CHECK 7: dual console log capture is distinctness-guarded ───────────────

def check_log_capture_config() -> None:
    src = read(RUN_SH)
    ok = bool(re.search(r'_IPAD_LEGACY_UDID.*==.*_IPHONE_LEGACY_UDID', src))
    check("run_mc1_regression.sh guards against iPad/iPhone UDID collision (duplicate log)", ok)
    ok_override = "IPHONE_LEGACY_UDID" in src and "IPAD_LEGACY_UDID" in src
    check("run_mc1_regression.sh supports manual UDID override env vars", ok_override)


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
    check_pose_overlay_diagnostics_wiring()
    check_orientation_aspect_wiring()
    check_log_capture_config()
    check_skip_preflight_cannot_pass()

    print("=== MC1 static preflight check ===\n")
    for line in PASSES:
        print(line)
    for line in FAILURES:
        print(line)
    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
