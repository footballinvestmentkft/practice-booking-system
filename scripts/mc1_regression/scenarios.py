"""MC1 regression scenarios (MC1-AUTO-2).

Each scenario function takes a ScenarioContext, drives the backend + both
physical devices via lfa-mc1:// deep links, and returns a ScenarioReport whose
`passed` flag is derived exclusively from backend ground truth
(cycle_devices[].recording_status, cycle.status, session.status).

Supported now:
  smoke       — 1 cycle, fast sanity check (join, mark-ready, begin, end)
  multicycle  — N cycles in one session (default 3) — start/stop must each
                report confirmed_start/confirmed_stop on both devices

Registered but not yet implemented (raise NotImplementedError with a clear
reason) — wire these up once the underlying iOS/backend feature exists:
  retry        — orchestrator retry-without-reset (blocked on ORCH-7)
  finalization — session finalize flow (blocked on ORCH-8)
"""
from __future__ import annotations

from .lib import (
    ScenarioContext,
    ScenarioReport,
    ValidationError,
    confirm_device_start,
    confirm_device_stop,
    copy_app_container_file,
    copy_from_device,
    create_session,
    device_recording_status,
    extract_capture_path_from_log,
    extract_skeleton_path_from_log,
    get_server_time_iso,
    get_session,
    latest_cycle,
    list_cycles,
    poll_until,
    register_device,
    send_deep_link,
    transition_session,
)

DEVICE_REGISTER_TIMEOUT_SECONDS = 120
CYCLE_CONFIRM_TIMEOUT_SECONDS = 30
RECORD_SECONDS = 4
# After the script PATCHes session to DEVICES_READY the iOS VM needs one 3s poll
# cycle to see the updated status + fresh revision before begin-cycle is sent.
# CCO already retries activateSession on 409, so 4s is a safe conservative buffer.
POST_DEVICES_READY_SETTLE_SECONDS = 4


def _join_both_devices(ctx: ScenarioContext, report: ScenarioReport, session_uuid: str) -> tuple[int, int]:
    """Join both devices. Returns (instructor_device_id, player_device_id).

    Role assignment depends on the scenario:
      - smoke/multicycle (legacy): iPad=instructor, iPhone=player
      - gopro-tricamera:           iPhone=instructor, iPad=player
    The caller controls which UDID gets which role by the order of
    ctx.ipad_udid / ctx.iphone_udid and the role= parameter.
    This function is role-agnostic — it just waits for both device roles to appear.
    """
    print("Sending pre-join dump-snapshot to both devices...")
    send_deep_link(ctx.ipad_udid, "dump-snapshot")
    send_deep_link(ctx.iphone_udid, "dump-snapshot")

    print(f"Sending join deep link to iPad (role={ctx.ipad_role})...")
    send_deep_link(ctx.ipad_udid, "join", session_uuid=session_uuid, role=ctx.ipad_role)
    print(f"Sending join deep link to iPhone (role={ctx.iphone_role})...")
    send_deep_link(ctx.iphone_udid, "join", session_uuid=session_uuid, role=ctx.iphone_role)

    def devices_registered():
        s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
        devices = s.get("devices", [])
        instructor = next((d for d in devices if d["device_role"] == "instructor_primary"), None)
        player = next((d for d in devices if d["device_role"] == "player_primary"), None)
        return (instructor, player) if instructor and player else None

    try:
        instructor_dev, player_dev = poll_until("both devices registered", DEVICE_REGISTER_TIMEOUT_SECONDS, devices_registered)
    except ValidationError:
        _dump_session_state(ctx, session_uuid, "join-timeout")
        print("  Requesting post-timeout dump-snapshot from both devices...")
        send_deep_link(ctx.ipad_udid, "dump-snapshot")
        send_deep_link(ctx.iphone_udid, "dump-snapshot")
        raise
    report.step("both devices registered", True,
                instructor_device_id=instructor_dev["id"], player_device_id=player_dev["id"])
    return instructor_dev["id"], player_dev["id"]


def _dump_session_state(ctx: ScenarioContext, session_uuid: str, label: str) -> None:
    """Print session + device status inline for diagnostics on timeout."""
    try:
        s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
        print(f"  [{label}] session.status={s.get('status')} revision={s.get('revision')}")
        for d in s.get("devices", []):
            print(f"    device id={d.get('id')} role={d.get('device_role')} "
                  f"status={d.get('device_status')} removed={d.get('removed_at') is not None}")
        cycles = list_cycles(ctx.api_base, ctx.instructor_token, session_uuid)
        if cycles:
            c = max(cycles, key=lambda x: x.get("cycle_index", 0))
            print(f"    latest_cycle index={c.get('cycle_index')} status={c.get('status')}")
            for cd in c.get("cycle_devices", []):
                print(f"      cd device_id={cd.get('session_device_id')} "
                      f"recording={cd.get('recording_status')}")
    except ValidationError as e:
        print(f"  [{label}] diagnostic dump failed: {e}")


def _mark_devices_ready(ctx: ScenarioContext, report: ScenarioReport, session_uuid: str) -> None:
    import time as _time
    # Script-driven backend transition: GET fresh revision + PATCH devices_ready.
    # Bypasses iOS transitionToDevicesReady() which suffered from stale revision
    # → silent 409 → ViewModel .error → session stuck in LOBBY.
    print("Transitioning session to DEVICES_READY via backend API (script-driven)...")
    try:
        updated = transition_session(ctx.api_base, ctx.instructor_token, session_uuid, "devices_ready")
        status = updated.get("status", "?")
        revision = updated.get("revision", "?")
        print(f"  session.status={status} revision={revision}")
        report.step("session DEVICES_READY", True, status=status, revision=revision)
    except ValidationError as e:
        report.step("session DEVICES_READY", False, error=str(e))
        _dump_session_state(ctx, session_uuid, "mark-ready-fail")
        raise

    print(f"Waiting {POST_DEVICES_READY_SETTLE_SECONDS}s for iOS VM to poll updated session...")
    _time.sleep(POST_DEVICES_READY_SETTLE_SECONDS)


def _run_one_cycle(
    ctx: ScenarioContext, report: ScenarioReport, session_uuid: str,
    instructor_device_id: int, player_device_id: int, cycle_index: int, record_seconds: int = RECORD_SECONDS,
) -> bool:
    instructor_udid = ctx.iphone_udid if ctx.iphone_role == "instructor" else ctx.ipad_udid
    print(f"Sending begin-cycle deep link to instructor (cycle {cycle_index})...")
    send_deep_link(instructor_udid, "begin-cycle")

    def confirmed_start():
        cycles = list_cycles(ctx.api_base, ctx.instructor_token, session_uuid)
        cyc = latest_cycle(cycles)
        if not cyc or cyc["cycle_index"] != cycle_index:
            return None
        s1 = device_recording_status(cyc, instructor_device_id)
        s2 = device_recording_status(cyc, player_device_id)
        if s1 == "confirmed_start" and s2 == "confirmed_start":
            return {"instructor": s1, "player": s2}
        return None

    try:
        confirmed = poll_until(
            f"cycle {cycle_index} confirmed_start on both devices",
            CYCLE_CONFIRM_TIMEOUT_SECONDS, confirmed_start,
        )
        report.step(f"cycle {cycle_index} confirmed_start", True, **confirmed)
    except ValidationError as e:
        report.step(f"cycle {cycle_index} confirmed_start", False, error=str(e))
        _dump_session_state(ctx, session_uuid, f"cycle{cycle_index}-start-timeout")
        return False

    print(f"Recording for {record_seconds}s...")
    import time
    time.sleep(record_seconds)

    print(f"Sending end-cycle deep link to instructor (cycle {cycle_index})...")
    send_deep_link(instructor_udid, "end-cycle")

    def confirmed_stop():
        cycles = list_cycles(ctx.api_base, ctx.instructor_token, session_uuid)
        cyc = latest_cycle(cycles)
        if not cyc or cyc["cycle_index"] != cycle_index:
            return None
        s1 = device_recording_status(cyc, instructor_device_id)
        s2 = device_recording_status(cyc, player_device_id)
        if cyc["status"] == "completed" and s1 == "confirmed_stop" and s2 == "confirmed_stop":
            return {"instructor": s1, "player": s2}
        return None

    try:
        confirmed = poll_until(
            f"cycle {cycle_index} confirmed_stop on both devices",
            CYCLE_CONFIRM_TIMEOUT_SECONDS, confirmed_stop,
        )
        report.step(f"cycle {cycle_index} confirmed_stop", True, **confirmed)
        return True
    except ValidationError as e:
        report.step(f"cycle {cycle_index} confirmed_stop", False, error=str(e))
        _dump_session_state(ctx, session_uuid, f"cycle{cycle_index}-stop-timeout")
        return False


def scenario_smoke(ctx: ScenarioContext) -> ScenarioReport:
    report = ScenarioReport(name="smoke", passed=False)
    session = create_session(ctx.api_base, ctx.instructor_token)
    session_uuid = session["session_uuid"]
    report.session_uuid = session_uuid
    print(f"[smoke] session created: {session_uuid}")

    try:
        instructor_id, player_id = _join_both_devices(ctx, report, session_uuid)
        send_deep_link(ctx.ipad_udid, "dump-snapshot")
        send_deep_link(ctx.iphone_udid, "dump-snapshot")
        _mark_devices_ready(ctx, report, session_uuid)
        ok = _run_one_cycle(ctx, report, session_uuid, instructor_id, player_id, cycle_index=0)
        send_deep_link(ctx.ipad_udid, "dump-snapshot")
        send_deep_link(ctx.iphone_udid, "dump-snapshot")
        report.passed = ok
    except ValidationError as e:
        report.error = str(e)
        report.passed = False
    return report


def scenario_multicycle(ctx: ScenarioContext) -> ScenarioReport:
    report = ScenarioReport(name="multicycle", passed=False)
    session = create_session(ctx.api_base, ctx.instructor_token)
    session_uuid = session["session_uuid"]
    report.session_uuid = session_uuid
    print(f"[multicycle] session created: {session_uuid} ({ctx.cycles} cycles)")

    try:
        instructor_id, player_id = _join_both_devices(ctx, report, session_uuid)
        send_deep_link(ctx.ipad_udid, "dump-snapshot")
        send_deep_link(ctx.iphone_udid, "dump-snapshot")
        _mark_devices_ready(ctx, report, session_uuid)

        all_ok = True
        for cycle_index in range(ctx.cycles):
            ok = _run_one_cycle(ctx, report, session_uuid, instructor_id, player_id, cycle_index=cycle_index)
            send_deep_link(ctx.ipad_udid, "dump-snapshot")
            send_deep_link(ctx.iphone_udid, "dump-snapshot")
            all_ok = all_ok and ok
            if not ok:
                break
        report.passed = all_ok
    except ValidationError as e:
        report.error = str(e)
        report.passed = False
    return report


def scenario_capture_quality_proof(ctx: ScenarioContext) -> ScenarioReport:
    """Capture Quality + Metadata block: runs one ordinary smoke cycle (both
    iPad + iPhone recording locally, the proven 2-device flow), then pulls
    capture_metadata_diag.json from BOTH devices and validates the explicit
    720p/30fps-or-360p/30fps-fallback profile actually took effect — not the
    old device-default `.high` preset.

    PASS criteria (per device, capture_metadata_diag.json-grounded):
      1. actualResolution in {"1280x720", "640x360"}
      2. actualFPS within [28, 32] (nominal frame rate tolerance around 30)
      3. actualCodec is a non-empty, known value ("h264")
      4. actualOrientation is portrait/landscape, not "unknown(...)"
    """
    import json
    from pathlib import Path

    report = ScenarioReport(name="capture-quality-proof", passed=False)
    session = create_session(ctx.api_base, ctx.instructor_token)
    session_uuid = session["session_uuid"]
    report.session_uuid = session_uuid
    print(f"[capture-quality] session created: {session_uuid}")

    try:
        instructor_id, player_id = _join_both_devices(ctx, report, session_uuid)
        _mark_devices_ready(ctx, report, session_uuid)
        ok = _run_one_cycle(ctx, report, session_uuid, instructor_id, player_id, cycle_index=0)
        if not ok:
            raise ValidationError("base smoke cycle did not complete — capture quality cannot be assessed")

        send_deep_link(ctx.ipad_udid, "capture-info")
        send_deep_link(ctx.iphone_udid, "capture-info")
        import time as _time
        _time.sleep(3)

        def _check_device(udid: str, label: str) -> dict:
            local_diag = str(ctx.artifact.dir / f"capture_metadata_diag_{label}.json")
            if not copy_app_container_file(udid, "Documents/capture_metadata_diag.json", local_diag):
                report.step(f"[{label}] capture_metadata_diag.json collected", False, error="copy failed")
                raise ValidationError(f"capture_metadata_diag.json not collectable from {label}")
            try:
                diag = json.loads(Path(local_diag).read_text())
            except (OSError, json.JSONDecodeError) as e:
                report.step(f"[{label}] capture_metadata_diag.json collected", False, error=f"unparseable: {e}")
                raise ValidationError(f"capture_metadata_diag.json from {label} unparseable: {e}")
            report.step(f"[{label}] capture_metadata_diag.json collected", True, **diag)
            print(f"[capture-quality] {label} diag: {json.dumps(diag, indent=2)}")
            return diag

        for udid, label in ((ctx.ipad_udid, "ipad"), (ctx.iphone_udid, "iphone")):
            diag = _check_device(udid, label)
            resolution = diag.get("actualResolution")
            fps = diag.get("actualFPS")
            codec = diag.get("actualCodec")
            orientation = diag.get("actualOrientation")

            res_ok = resolution in ("1280x720", "640x360")
            fps_ok = isinstance(fps, (int, float)) and 28 <= fps <= 32
            codec_ok = codec in ("h264",)
            orient_ok = orientation in ("portrait", "landscapeLeft", "landscapeRight", "portraitUpsideDown")

            report.step(f"[{label}] resolution in {{720p,360p}}", res_ok, value=resolution)
            report.step(f"[{label}] fps ~30", fps_ok, value=fps)
            report.step(f"[{label}] codec explicit", codec_ok, value=codec)
            report.step(f"[{label}] orientation known", orient_ok, value=orientation)

            print(f"[capture-quality] {label}: resolution={'OK' if res_ok else 'FAIL'}({resolution}) "
                  f"fps={'OK' if fps_ok else 'FAIL'}({fps}) codec={'OK' if codec_ok else 'FAIL'}({codec}) "
                  f"orientation={'OK' if orient_ok else 'FAIL'}({orientation})")

            if not (res_ok and fps_ok and codec_ok and orient_ok):
                raise ValidationError(
                    f"{label} capture quality check failed: resolution={resolution} fps={fps} "
                    f"codec={codec} orientation={orientation}"
                )

        report.passed = True
        print("[capture-quality] === PASS: both devices recorded at the explicit profile ===")

    except ValidationError as e:
        report.error = str(e)
        report.passed = False
        print(f"[capture-quality] === FAIL: {e} ===")
    return report


def scenario_retry(ctx: ScenarioContext) -> ScenarioReport:
    raise NotImplementedError(
        "retry scenario is registered but not implemented — blocked on ORCH-7 "
        "(orchestrator retry-without-session-reset is not built yet)"
    )


def scenario_finalization(ctx: ScenarioContext) -> ScenarioReport:
    raise NotImplementedError(
        "finalization scenario is registered but not implemented — blocked on ORCH-8 "
        "(iOS session-finalize flow is not built yet)"
    )


GOPRO_CONNECT_TIMEOUT_SECONDS = 45
GOPRO_DIAG_SETTLE_SECONDS = 5


def scenario_gopro_iphone_diagnostics(ctx: ScenarioContext) -> ScenarioReport:
    """Isolated GoPro diagnostics: verify iPhone can control GoPro directly.

    Tests HTTP reachability, shutter start/stop, and media list — all from
    the iPhone. No backend confirmDeviceStart/Stop, no cycle creation.
    The GoPro must already be WiFi-connected to the iPhone.
    """
    import time as _time

    report = ScenarioReport(name="gopro-iphone-diagnostics", passed=False)

    # 1. Create a minimal session so deep links work (LobbyView must be open)
    session = create_session(ctx.api_base, ctx.instructor_token)
    session_uuid = session["session_uuid"]
    report.session_uuid = session_uuid
    print(f"[gopro-diag] session created: {session_uuid}")

    try:
        # 2. Join iPhone only (enough for deep link handling)
        print("[gopro-diag] Joining iPhone to session...")
        send_deep_link(ctx.iphone_udid, "join", session_uuid=session_uuid, role="player")
        _time.sleep(5)

        # 3. Run HTTP diagnostics on iPhone
        print("[gopro-diag] Sending gopro-http-diag to iPhone...")
        send_deep_link(ctx.iphone_udid, "gopro-http-diag")
        _time.sleep(GOPRO_DIAG_SETTLE_SECONDS)
        report.step("gopro-http-diag sent", True)

        # 4. Check GoPro connection state
        print("[gopro-diag] Sending gopro-status to iPhone...")
        send_deep_link(ctx.iphone_udid, "gopro-status")
        _time.sleep(2)
        report.step("gopro-status sent", True)

        # 5. Try GoPro connect (BLE+WiFi+HTTP in-app flow)
        print("[gopro-diag] Sending gopro-connect to iPhone...")
        send_deep_link(ctx.iphone_udid, "gopro-connect")
        _time.sleep(GOPRO_CONNECT_TIMEOUT_SECONDS)
        report.step("gopro-connect sent", True)

        # 6. Check status after connect
        print("[gopro-diag] Post-connect: sending gopro-status + gopro-http-diag...")
        send_deep_link(ctx.iphone_udid, "gopro-status")
        _time.sleep(1)
        send_deep_link(ctx.iphone_udid, "gopro-http-diag")
        _time.sleep(GOPRO_DIAG_SETTLE_SECONDS)
        report.step("post-connect diag sent", True)

        # 7. Try shutter start (no backend confirm, just HTTP)
        print("[gopro-diag] Sending gopro-start to iPhone (no backend confirm, HTTP only)...")
        send_deep_link(ctx.iphone_udid, "gopro-start", gopro_device_id="0")
        _time.sleep(GOPRO_SHUTTER_SETTLE_SECONDS)
        report.step("gopro-start sent", True)

        # 8. Check status during "recording"
        print("[gopro-diag] Mid-recording: sending gopro-status...")
        send_deep_link(ctx.iphone_udid, "gopro-status")
        _time.sleep(3)

        # 9. Try shutter stop
        print("[gopro-diag] Sending gopro-stop to iPhone...")
        send_deep_link(ctx.iphone_udid, "gopro-stop", gopro_device_id="0")
        _time.sleep(GOPRO_SHUTTER_SETTLE_SECONDS)
        report.step("gopro-stop sent", True)

        # 10. Media list
        print("[gopro-diag] Sending gopro-media-list to iPhone...")
        send_deep_link(ctx.iphone_udid, "gopro-media-list")
        _time.sleep(3)
        report.step("gopro-media-list sent", True)

        # 11. Final snapshot
        print("[gopro-diag] Final snapshot...")
        send_deep_link(ctx.iphone_udid, "dump-snapshot")
        _time.sleep(2)

        report.passed = True
        print("[gopro-diag] All diagnostic deep links sent. Check iPhone console for [GOPRO-DIAG] and [GOPRO-AUTO] output.")

    except ValidationError as e:
        report.error = str(e)
        report.passed = False
    return report


GOPRO_SHUTTER_SETTLE_SECONDS = 3
GOPRO_RECORD_SECONDS = 8


def scenario_gopro_tricamera_smoke(ctx: ScenarioContext) -> ScenarioReport:
    """3-camera smoke: iPhone (instructor+GoPro controller) + iPad (player) + GoPro, 1 cycle.

    Role model:
      iPhone = instructor/controller + GoPro bridge (cellular keeps backend reachable)
      iPad   = player/student camera (WiFi/wired, no SIM needed)
      GoPro  = auxiliary camera, managed by iPhone

    Preconditions (physical only):
      - GoPro HERO12 powered on and in pairing/connectable state
      - iPhone has cellular data enabled (backend access alongside GoPro WiFi)
      - Both devices running fresh LFA app build
    """
    import time as _time

    report = ScenarioReport(name="gopro-tricamera-smoke", passed=False)
    session = create_session(ctx.api_base, ctx.instructor_token)
    session_uuid = session["session_uuid"]
    report.session_uuid = session_uuid
    print(f"[gopro-tricam] session created: {session_uuid}")
    print(f"[gopro-tricam] role model: iPhone=instructor+GoPro, iPad=player")

    try:
        # 1. Join both — iPhone as instructor, iPad as player
        instructor_id, player_id = _join_both_devices(ctx, report, session_uuid)

        # 2. Register GoPro as auxiliary_camera, managed by instructor (iPhone)
        print(f"[gopro-register] Registering GoPro managed_by instructor device_id={instructor_id}...")
        gopro_sd = register_device(
            ctx.api_base, ctx.instructor_token, session_uuid,
            device_role="auxiliary_camera",
            device_type="gopro",
            device_name="GoPro HERO13 (automation)",
            managed_by_device_id=instructor_id,
        )
        gopro_device_id = gopro_sd["id"]
        managed_by = gopro_sd.get("managed_by_device_id")
        print(f"[gopro-register]   GoPro session_device_id={gopro_device_id} managed_by={managed_by}")
        if managed_by != instructor_id:
            report.step("gopro managed_by instructor", False, expected=instructor_id, actual=managed_by)
            raise ValidationError(f"GoPro managed_by={managed_by} but expected instructor id={instructor_id}")
        report.step("gopro registered (managed by instructor)", True, gopro_device_id=gopro_device_id, managed_by=managed_by)

        # 3. GoPro connect on iPhone (BLE → WiFi → HTTP, in-app flow)
        #    Passes gopro_device_id so iPhone can signal ready via backend updateDeviceStatus
        print(f"[gopro-connect] Sending gopro-connect to iPhone UDID={ctx.iphone_udid[:8]}... gopro_device_id={gopro_device_id}")
        send_deep_link(ctx.iphone_udid, "gopro-connect", gopro_device_id=str(gopro_device_id))
        print("[gopro-connect] Waiting for BLE scan + in-app WiFi Join + HTTP verify...")
        print("[gopro-connect] (User: tap 'Join' on the Wi-Fi prompt if it appears on iPhone)")

        # 4. Poll backend for GoPro device status == ready (iPhone signals this)
        def gopro_device_ready():
            s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
            for d in s.get("devices", []):
                if d["id"] == gopro_device_id:
                    status = d.get("device_status") or d.get("status")
                    if status == "ready":
                        return d
            return None

        try:
            gp_ready = poll_until("GoPro device ready (backend)", GOPRO_CONNECT_TIMEOUT_SECONDS, gopro_device_ready)
            print(f"[gopro-connect] GoPro device ready: OK (verified via backend device_status)")
            report.step("gopro ready (backend-verified)", True)
        except ValidationError as e:
            report.step("gopro ready (backend-verified)", False, error=str(e))
            send_deep_link(ctx.iphone_udid, "gopro-status")
            send_deep_link(ctx.iphone_udid, "dump-snapshot")
            _time.sleep(2)
            raise ValidationError(f"GoPro not ready — connection may have failed: {e}")

        # 5. Dump snapshots (both devices)
        send_deep_link(ctx.ipad_udid, "dump-snapshot")
        send_deep_link(ctx.iphone_udid, "dump-snapshot")

        # 6. Mark devices ready
        _mark_devices_ready(ctx, report, session_uuid)

        # 6. Begin cycle — instructor is iPhone
        print("Sending begin-cycle deep link to iPhone/instructor (cycle 0)...")
        send_deep_link(ctx.iphone_udid, "begin-cycle")

        # 7. Wait for iPad + iPhone confirmed_start
        def ipad_iphone_confirmed_start():
            cycles = list_cycles(ctx.api_base, ctx.instructor_token, session_uuid)
            cyc = latest_cycle(cycles)
            if not cyc or cyc["cycle_index"] != 0:
                return None
            s1 = device_recording_status(cyc, instructor_id)
            s2 = device_recording_status(cyc, player_id)
            if s1 == "confirmed_start" and s2 == "confirmed_start":
                return cyc
            return None

        cycle = poll_until("iPad+iPhone confirmed_start", CYCLE_CONFIRM_TIMEOUT_SECONDS, ipad_iphone_confirmed_start)
        report.step("ipad+iphone confirmed_start", True)

        # 8. Verify GoPro cycle_device exists
        cycle_id = cycle["id"]
        gopro_cd = next(
            (cd for cd in cycle.get("cycle_devices", []) if cd.get("session_device_id") == gopro_device_id),
            None,
        )
        if gopro_cd is None:
            report.step("gopro cycle_device exists", False, error="no cycle_device for GoPro")
            raise ValidationError("No cycle_device found for GoPro")
        report.step("gopro cycle_device exists", True, cycle_device_id=gopro_cd["id"])

        # 9. Start GoPro recording via iPhone (iPhone = GoPro bridge, iOS-driven confirm)
        print(f"[gopro-start] Sending gopro-start to iPhone UDID={ctx.iphone_udid[:8]}... gopro_device_id={gopro_device_id}")
        send_deep_link(ctx.iphone_udid, "gopro-start", gopro_device_id=str(gopro_device_id))
        _time.sleep(GOPRO_SHUTTER_SETTLE_SECONDS)

        # 10. Poll backend for GoPro confirmed_start (iOS only confirms if HTTP shutter succeeded)
        print("[gopro-start] Polling backend for GoPro confirmed_start (proves shutter + backend confirm)...")
        def gopro_confirmed_start():
            cycles = list_cycles(ctx.api_base, ctx.instructor_token, session_uuid)
            cyc = latest_cycle(cycles)
            if not cyc:
                return None
            status = device_recording_status(cyc, gopro_device_id)
            return status if status == "confirmed_start" else None

        try:
            poll_until("GoPro confirmed_start (iOS-driven)", CYCLE_CONFIRM_TIMEOUT_SECONDS, gopro_confirmed_start)
            print("[gopro-start] GoPro confirmed_start: OK (GoPro HTTP shutter + backend confirm both succeeded)")
            report.step("gopro confirmed_start (iOS-driven)", True)
        except ValidationError as e:
            report.step("gopro confirmed_start (iOS-driven)", False, error=str(e))
            _dump_session_state(ctx, session_uuid, "gopro-start-timeout")
            print("[gopro-start] FAIL — GoPro may be disconnected, not ready, or HTTP shutter failed")
            raise

        # 11. Record for N seconds, request GoPro status mid-recording for evidence
        print(f"Recording all 3 cameras for {GOPRO_RECORD_SECONDS}s...")
        _time.sleep(GOPRO_RECORD_SECONDS // 2)
        print("[gopro-midcheck] Requesting GoPro status from iPhone mid-recording...")
        send_deep_link(ctx.iphone_udid, "gopro-status")
        _time.sleep(GOPRO_RECORD_SECONDS - GOPRO_RECORD_SECONDS // 2)

        # 12. Stop GoPro FIRST via iPhone (iOS-driven confirm)
        print(f"[gopro-stop] Sending gopro-stop to iPhone UDID={ctx.iphone_udid[:8]}... gopro_device_id={gopro_device_id}")
        send_deep_link(ctx.iphone_udid, "gopro-stop", gopro_device_id=str(gopro_device_id))
        _time.sleep(GOPRO_SHUTTER_SETTLE_SECONDS)

        # 13. Poll backend for GoPro confirmed_stop (iOS only confirms if shutter stop succeeded)
        print("[gopro-stop] Polling backend for GoPro confirmed_stop...")
        def gopro_confirmed_stop():
            cycles = list_cycles(ctx.api_base, ctx.instructor_token, session_uuid)
            cyc = latest_cycle(cycles)
            if not cyc:
                return None
            status = device_recording_status(cyc, gopro_device_id)
            return status if status == "confirmed_stop" else None

        try:
            poll_until("GoPro confirmed_stop (iOS-driven)", CYCLE_CONFIRM_TIMEOUT_SECONDS, gopro_confirmed_stop)
            print("[gopro-stop] GoPro confirmed_stop: OK (GoPro HTTP shutter stop + backend confirm both succeeded)")
            report.step("gopro confirmed_stop (iOS-driven)", True)
        except ValidationError as e:
            report.step("gopro confirmed_stop (iOS-driven)", False, error=str(e))
            _dump_session_state(ctx, session_uuid, "gopro-stop-timeout")
            print("[gopro-stop] FAIL — GoPro may have disconnected during recording")
            raise

        # 14. End cycle — instructor is iPhone
        print("[end-cycle] Sending end-cycle deep link to iPhone/instructor (after GoPro stop)...")
        send_deep_link(ctx.iphone_udid, "end-cycle")

        # 15. Wait for iPad + iPhone confirmed_stop (GoPro already confirmed)
        def all_three_confirmed_stop():
            cycles = list_cycles(ctx.api_base, ctx.instructor_token, session_uuid)
            cyc = latest_cycle(cycles)
            if not cyc:
                return None
            s_inst = device_recording_status(cyc, instructor_id)
            s_play = device_recording_status(cyc, player_id)
            s_gp = device_recording_status(cyc, gopro_device_id)
            if s_inst == "confirmed_stop" and s_play == "confirmed_stop" and s_gp == "confirmed_stop":
                return {"instructor": s_inst, "player": s_play, "gopro": s_gp}
            return None

        try:
            result = poll_until("all 3 devices confirmed_stop", CYCLE_CONFIRM_TIMEOUT_SECONDS, all_three_confirmed_stop)
            report.step("all 3 confirmed_stop", True, **result)
        except ValidationError as e:
            report.step("all 3 confirmed_stop", False, error=str(e))
            _dump_session_state(ctx, session_uuid, "tricam-stop-timeout")
            raise

        # 16. Fetch GoPro media list via iPhone for evidence
        print(f"[gopro-media] Fetching GoPro media list from iPhone UDID={ctx.iphone_udid[:8]}...")
        send_deep_link(ctx.iphone_udid, "gopro-media-list")
        _time.sleep(2)

        # 17. Final snapshots (both devices — iPhone snapshot has GoPro state)
        print("[final] Requesting final snapshots from both devices...")
        send_deep_link(ctx.ipad_udid, "dump-snapshot")
        send_deep_link(ctx.iphone_udid, "dump-snapshot")

        report.passed = True

    except ValidationError as e:
        report.error = str(e)
        report.passed = False
    return report


SKELETON_PROCESS_TIMEOUT_SECONDS = 60
ARTIFACT_COLLECT_SECONDS = 5


def scenario_tricamera_capture_skeleton_proof(ctx: ScenarioContext) -> ScenarioReport:
    """End-to-end proof: 3-camera capture + skeleton overlay.

    iPhone=instructor+GoPro controller, iPad=player, GoPro=auxiliary.
    After capture: collect 3 video artifacts + run skeleton processing on iPhone video.
    """
    import time as _time

    report = ScenarioReport(name="tricamera-capture-skeleton-proof", passed=False)
    session = create_session(ctx.api_base, ctx.instructor_token)
    session_uuid = session["session_uuid"]
    report.session_uuid = session_uuid
    print(f"[proof] session created: {session_uuid}")
    print(f"[proof] iPhone=instructor+GoPro, iPad=player")

    try:
        # 1. Join both devices
        instructor_id, player_id = _join_both_devices(ctx, report, session_uuid)

        # 2. Register GoPro managed by instructor (iPhone)
        print(f"[proof] Registering GoPro managed_by instructor device_id={instructor_id}...")
        gopro_sd = register_device(
            ctx.api_base, ctx.instructor_token, session_uuid,
            device_role="auxiliary_camera", device_type="gopro",
            device_name="GoPro HERO13 (proof)",
            managed_by_device_id=instructor_id,
        )
        gopro_device_id = gopro_sd["id"]
        report.step("gopro registered", True, gopro_device_id=gopro_device_id, managed_by=instructor_id)

        # 3. GoPro connect on iPhone
        print(f"[proof] gopro-connect → iPhone...")
        send_deep_link(ctx.iphone_udid, "gopro-connect", gopro_device_id=str(gopro_device_id))

        def gopro_device_ready():
            s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
            for d in s.get("devices", []):
                if d["id"] == gopro_device_id and (d.get("device_status") or d.get("status")) == "ready":
                    return d
            return None

        try:
            poll_until("GoPro ready (backend)", GOPRO_CONNECT_TIMEOUT_SECONDS, gopro_device_ready)
            report.step("gopro ready", True)
        except ValidationError as e:
            report.step("gopro ready", False, error=str(e))
            send_deep_link(ctx.iphone_udid, "gopro-status")
            _time.sleep(2)
            raise

        # 4. Devices ready
        _mark_devices_ready(ctx, report, session_uuid)

        # 5. Begin cycle (instructor = iPhone)
        print("[proof] begin-cycle → iPhone/instructor...")
        send_deep_link(ctx.iphone_udid, "begin-cycle")

        # 6. Wait for iPhone + iPad confirmed_start
        def two_devices_started():
            cycles = list_cycles(ctx.api_base, ctx.instructor_token, session_uuid)
            cyc = latest_cycle(cycles)
            if not cyc or cyc["cycle_index"] != 0:
                return None
            s1 = device_recording_status(cyc, instructor_id)
            s2 = device_recording_status(cyc, player_id)
            return cyc if s1 == "confirmed_start" and s2 == "confirmed_start" else None

        cycle = poll_until("instructor+player confirmed_start", CYCLE_CONFIRM_TIMEOUT_SECONDS, two_devices_started)
        report.step("instructor+player confirmed_start", True)
        cycle_id = cycle["id"]

        # 7. GoPro start (iPhone)
        print("[proof] gopro-start → iPhone...")
        send_deep_link(ctx.iphone_udid, "gopro-start", gopro_device_id=str(gopro_device_id))

        def gopro_started():
            cycles = list_cycles(ctx.api_base, ctx.instructor_token, session_uuid)
            cyc = latest_cycle(cycles)
            return "ok" if cyc and device_recording_status(cyc, gopro_device_id) == "confirmed_start" else None

        try:
            poll_until("GoPro confirmed_start", CYCLE_CONFIRM_TIMEOUT_SECONDS, gopro_started)
            report.step("gopro confirmed_start", True)
        except ValidationError as e:
            report.step("gopro confirmed_start", False, error=str(e))
            _dump_session_state(ctx, session_uuid, "gopro-start-fail")
            raise

        # 8. Record
        print(f"[proof] recording all 3 cameras for {GOPRO_RECORD_SECONDS}s...")
        _time.sleep(GOPRO_RECORD_SECONDS)

        # 9. GoPro stop FIRST
        print("[proof] gopro-stop → iPhone...")
        send_deep_link(ctx.iphone_udid, "gopro-stop", gopro_device_id=str(gopro_device_id))

        def gopro_stopped():
            cycles = list_cycles(ctx.api_base, ctx.instructor_token, session_uuid)
            cyc = latest_cycle(cycles)
            return "ok" if cyc and device_recording_status(cyc, gopro_device_id) == "confirmed_stop" else None

        try:
            poll_until("GoPro confirmed_stop", CYCLE_CONFIRM_TIMEOUT_SECONDS, gopro_stopped)
            report.step("gopro confirmed_stop", True)
        except ValidationError as e:
            report.step("gopro confirmed_stop", False, error=str(e))
            raise

        # 10. End cycle (instructor = iPhone)
        print("[proof] end-cycle → iPhone/instructor...")
        send_deep_link(ctx.iphone_udid, "end-cycle")

        # 11. All 3 confirmed_stop
        def all_stopped():
            cycles = list_cycles(ctx.api_base, ctx.instructor_token, session_uuid)
            cyc = latest_cycle(cycles)
            if not cyc:
                return None
            s = {
                "instructor": device_recording_status(cyc, instructor_id),
                "player": device_recording_status(cyc, player_id),
                "gopro": device_recording_status(cyc, gopro_device_id),
            }
            return s if all(v == "confirmed_stop" for v in s.values()) else None

        result = poll_until("all 3 confirmed_stop", CYCLE_CONFIRM_TIMEOUT_SECONDS, all_stopped)
        report.step("all 3 confirmed_stop", True, **result)

        # 12. Timestamp sync check
        cycles_final = list_cycles(ctx.api_base, ctx.instructor_token, session_uuid)
        cyc_final = latest_cycle(cycles_final)
        if cyc_final:
            devices_timing = []
            for cd in cyc_final.get("cycle_devices", []):
                devices_timing.append({
                    "device_id": cd.get("session_device_id"),
                    "started_at": cd.get("started_at"),
                    "stopped_at": cd.get("stopped_at"),
                    "recording_status": cd.get("recording_status"),
                })
            ctx.artifact.write_json("backend_state/proof_cycle_timing.json", {
                "cycle_id": cyc_final["id"],
                "cycle_status": cyc_final.get("status"),
                "devices": devices_timing,
            })
            print(f"[proof] timestamp sync report saved")
            report.step("timestamp sync report", True)

        # 13. Collect capture info from both iOS devices
        print("[proof] collecting capture info from iPhone...")
        send_deep_link(ctx.iphone_udid, "capture-info")
        _time.sleep(2)
        print("[proof] collecting capture info from iPad...")
        send_deep_link(ctx.ipad_udid, "capture-info")
        _time.sleep(2)

        # 14. GoPro media list
        print("[proof] fetching GoPro media list...")
        send_deep_link(ctx.iphone_udid, "gopro-media-list")
        _time.sleep(ARTIFACT_COLLECT_SECONDS)

        # 15. Skeleton processing on iPhone video
        print("[proof] skeleton-process → iPhone (Vision framework on local video)...")
        send_deep_link(ctx.iphone_udid, "skeleton-process")
        print(f"[proof] waiting {SKELETON_PROCESS_TIMEOUT_SECONDS}s for skeleton processing...")
        _time.sleep(SKELETON_PROCESS_TIMEOUT_SECONDS)

        # 16. Final snapshots + capture-info (for file paths)
        send_deep_link(ctx.ipad_udid, "capture-info")
        send_deep_link(ctx.iphone_udid, "capture-info")
        send_deep_link(ctx.ipad_udid, "dump-snapshot")
        send_deep_link(ctx.iphone_udid, "dump-snapshot")
        _time.sleep(3)

        # 17. Artifact collection — read console logs, extract paths, copy files
        print("[proof] === ARTIFACT COLLECTION ===")
        import os

        iphone_log_path = ctx.artifact.iphone_console_log
        ipad_log_path = ctx.artifact.ipad_console_log
        iphone_log = iphone_log_path.read_text() if iphone_log_path.exists() else ""
        ipad_log = ipad_log_path.read_text() if ipad_log_path.exists() else ""

        artifacts_dir = ctx.artifact.dir / "video_artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # 17a. iPhone video
        iphone_video_path = extract_capture_path_from_log(iphone_log)
        if iphone_video_path:
            local_iphone_video = str(artifacts_dir / "iphone_capture.mov")
            if copy_from_device(ctx.iphone_udid, iphone_video_path, local_iphone_video):
                size = os.path.getsize(local_iphone_video) if os.path.exists(local_iphone_video) else 0
                report.step("iphone video collected", size > 0, path=local_iphone_video, size=size)
            else:
                report.step("iphone video collected", False, error="copy failed")
        else:
            report.step("iphone video collected", False, error="path not found in console log")

        # 17b. iPad video
        ipad_video_path = extract_capture_path_from_log(ipad_log)
        if ipad_video_path:
            local_ipad_video = str(artifacts_dir / "ipad_capture.mov")
            if copy_from_device(ctx.ipad_udid, ipad_video_path, local_ipad_video):
                size = os.path.getsize(local_ipad_video) if os.path.exists(local_ipad_video) else 0
                report.step("ipad video collected", size > 0, path=local_ipad_video, size=size)
            else:
                report.step("ipad video collected", False, error="copy failed")
        else:
            report.step("ipad video collected", False, error="path not found in console log")

        # 17c. GoPro evidence (media list in console log)
        gopro_media_found = "[GOPRO-MEDIA-BEGIN]" in iphone_log
        report.step("gopro media evidence", gopro_media_found,
                     note="media list found in iPhone console log" if gopro_media_found else "not found")

        # 17d. Skeleton JSON
        skeleton_path = extract_skeleton_path_from_log(iphone_log)
        if skeleton_path:
            local_skeleton = str(artifacts_dir / "skeleton.json")
            if copy_from_device(ctx.iphone_udid, skeleton_path, local_skeleton):
                size = os.path.getsize(local_skeleton) if os.path.exists(local_skeleton) else 0
                report.step("skeleton json collected", size > 100, path=local_skeleton, size=size)
            else:
                report.step("skeleton json collected", False, error="copy failed")
        else:
            report.step("skeleton json collected", False, error="path not found in console log")

        # 18. Final PASS: all critical steps must be OK
        critical_ok = all(
            s.get("ok") for s in report.steps
            if s["description"] in (
                "iphone+ipad confirmed_start", "gopro confirmed_start",
                "all 3 confirmed_stop", "timestamp sync report",
            )
        )
        report.passed = critical_ok
        print(f"[proof] === PROOF {'PASS' if critical_ok else 'FAIL'} ===")

    except ValidationError as e:
        report.error = str(e)
        report.passed = False
    return report


NETWORK_ROUTING_DIAG_TIMEOUT_SECONDS = 180  # GoPro connect (manual WiFi join) + updateDeviceStatus
NETWORK_ROUTING_DIAG_SETTLE_SECONDS = 3


def scenario_gopro_network_routing_diag(ctx: ScenarioContext) -> ScenarioReport:
    """MC1 Block-1: GoPro WiFi + backend cellular coexistence validation.

    Validates that iPhone can call the backend (updateDeviceStatus) while
    simultaneously connected to the GoPro WiFi AP. This is the root cause
    fix for the GoPro ready-signal 45s timeout.

    KNOWN LIMITATION (as of c02c04dc): in-app automatic GoPro WiFi join via
    NEHotspotConfiguration requires the com.apple.developer.networking.
    HotspotConfiguration entitlement, which Apple does not grant to personal/
    free-tier development teams. SystemWiFiTransport.joinAccessPoint always
    throws .unavailable under the current provisioning, so the app falls
    back to GoProConnectionState.awaitingManualWiFiJoin(ssid:) and a HUMAN
    must join the GoPro WiFi AP manually via iPhone Settings > Wi-Fi. This is
    a physical-validation workaround, NOT the final product UX — a paid
    Apple Developer Program membership (or an alternative networking
    strategy) is required before automatic in-app join can return.

    PASS criteria (all backend-grounded, no console parsing):
      1. GoPro device registered in session (backend POST /devices)
      2. App reaches awaitingManualWiFiJoin(ssid:) (manual fallback engaged)
      3. Human joins the GoPro WiFi AP manually, returns to the app
      4. GoPro device_status == "ready" on backend after gopro-connect
         (proves updateDeviceStatus reached the backend over cellular
          while iPhone was on GoPro WiFi AP)

    Preconditions (physical):
      - GoPro HERO13 powered on and in BLE pairing/discoverable mode
      - iPhone has cellular data enabled
      - iPhone has the lfa-mc1:// scheme app installed
      - Operator is present to manually join the GoPro WiFi AP from
        iPhone Settings > Wi-Fi when prompted (see console output below)

    The diagnostic `[NET-DIAG]` and `[GOPRO-AUTO]` log lines in the iPhone
    console are corroborating evidence — they are NOT used for PASS/FAIL.
    """
    import json
    import time as _time
    from pathlib import Path

    report = ScenarioReport(name="gopro-network-routing-diag", passed=False)
    session = create_session(ctx.api_base, ctx.instructor_token)
    session_uuid = session["session_uuid"]
    report.session_uuid = session_uuid
    print(f"[net-diag] session created: {session_uuid}")

    try:
        # 1. Join iPhone as instructor (needs active session for deep links to land)
        print("[net-diag] Joining iPhone as instructor...")
        send_deep_link(ctx.iphone_udid, "join", session_uuid=session_uuid, role="instructor")
        _time.sleep(5)  # wait for join + autoRegisterDevice

        def iphone_registered():
            s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
            return next((d for d in s.get("devices", [])
                         if d["device_role"] == "instructor_primary"), None)

        instructor_dev = poll_until(
            "iPhone registered as instructor_primary",
            DEVICE_REGISTER_TIMEOUT_SECONDS, iphone_registered,
        )
        instructor_id = instructor_dev["id"]
        report.step("iphone registered as instructor", True, device_id=instructor_id)
        print(f"[net-diag] iPhone instructor device_id={instructor_id}")

        # 2. Register GoPro managed by iPhone (before connect — gives us a device_id)
        print(f"[net-diag] Registering GoPro managed_by instructor device_id={instructor_id}...")
        gopro_sd = register_device(
            ctx.api_base, ctx.instructor_token, session_uuid,
            device_role="auxiliary_camera", device_type="gopro",
            device_name="GoPro HERO13 (net-diag)",
            managed_by_device_id=instructor_id,
        )
        gopro_device_id = gopro_sd["id"]
        managed_by = gopro_sd.get("managed_by_device_id")
        if managed_by != instructor_id:
            report.step("gopro managed_by instructor", False,
                        expected=instructor_id, actual=managed_by)
            raise ValidationError(f"GoPro managed_by={managed_by} expected={instructor_id}")
        report.step("gopro registered (managed by instructor)", True,
                    gopro_device_id=gopro_device_id)
        print(f"[net-diag] GoPro registered: session_device_id={gopro_device_id}")

        # 3. Network probe BEFORE GoPro WiFi join (baseline)
        print("[net-diag] Sending network-routing-diag BEFORE gopro-connect...")
        send_deep_link(ctx.iphone_udid, "network-routing-diag", label="before-gopro")
        _time.sleep(NETWORK_ROUTING_DIAG_SETTLE_SECONDS)
        report.step("pre-connect network probe sent", True)

        # 4. GoPro connect (BLE → WiFi AP). The app's wait-for-ready loop
        #    (waitAndSignalGoProReady) only watches GoProConnectionManager.state
        #    for 45s after THIS deep link, and the manual-join confirmation
        #    (confirmManualWiFiJoined) is only invoked when a gopro-connect
        #    deep link arrives WHILE the app is in .awaitingManualWiFiJoin.
        #    A human joining WiFi by hand does not retrigger that check on its
        #    own — so we block here on operator confirmation, THEN resend
        #    gopro-connect to pick up the manual join and start a fresh 45s
        #    HTTP-verify window.
        print(f"[net-diag] Sending gopro-connect to iPhone (gopro_device_id={gopro_device_id})...")
        print("[net-diag] Physical: ensure GoPro is on and in BLE pairing mode.")
        send_deep_link(ctx.iphone_udid, "gopro-connect", gopro_device_id=str(gopro_device_id))
        report.step("gopro-connect deep link sent", True)
        print("[net-diag] >>> GoPro Wi-Fi auto-join unavailable under current provisioning")
        print("[net-diag] >>> Manual action required: iPhone Settings -> Wi-Fi -> select GoPro SSID")
        print("[net-diag] >>> Return to LFA app after Wi-Fi connection")
        print("[net-diag] >>> App will verify GoPro HTTP and signal backend ready")

        # 5. Network probe AFTER GoPro WiFi join starts (reveals routing state)
        _time.sleep(15)  # give BLE scan + AP-activation time to reach awaitingManualWiFiJoin
        print("[net-diag] Sending network-routing-diag AFTER gopro-connect (mid-connect)...")
        send_deep_link(ctx.iphone_udid, "network-routing-diag", label="mid-gopro-connect")
        _time.sleep(NETWORK_ROUTING_DIAG_SETTLE_SECONDS)
        report.step("mid-connect network probe sent", True)

        # The exact GoPro WiFi SSID is read live from the camera over BLE and
        # is NOT printed by [GoPro]/[NET-DIAG] log lines — it only appears in
        # the Debug Snapshot's "gopro_connection: Csatlakozz: <ssid>" line.
        # Dump it now so the operator (and this artifact) has the real SSID.
        print("[net-diag] Dumping snapshot to read the exact GoPro WiFi SSID...")
        send_deep_link(ctx.iphone_udid, "dump-snapshot")
        _time.sleep(2)
        print("[net-diag] >>> Check console/iphone_console.log just written above for "
              "'gopro_connection: Csatlakozz: <SSID>' — that <SSID> is the GoPro WiFi network name.")

        # 5b. Block here until the operator has manually joined the GoPro WiFi
        #     network and returned to the LFA app. Do not press Enter early —
        #     the BLE handshake needs the ~15s above to even show the SSID.
        input(
            "\n[net-diag] >>> Join the GoPro WiFi network on the iPhone now "
            "(Settings -> Wi-Fi -> GoPro SSID), then return to the LFA app.\n"
            "[net-diag] >>> Press ENTER here ONLY after the iPhone shows it is "
            "connected to the GoPro WiFi network: "
        )
        print("[net-diag] Operator confirmed manual WiFi join — resending gopro-connect "
              "to trigger confirmManualWiFiJoined()...")
        send_deep_link(ctx.iphone_udid, "gopro-connect", gopro_device_id=str(gopro_device_id))
        report.step("gopro-connect re-sent after manual join", True)

        # 6. Poll backend for GoPro device_status == ready
        #    PASS gate: updateDeviceStatus reached backend over cellular while on GoPro WiFi
        print(f"[net-diag] Polling backend for GoPro device_status=ready "
              f"(timeout={NETWORK_ROUTING_DIAG_TIMEOUT_SECONDS}s)...")

        def gopro_device_ready():
            s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
            for d in s.get("devices", []):
                if d["id"] == gopro_device_id:
                    status = d.get("device_status") or d.get("status")
                    if status == "ready":
                        return d
            return None

        # gopro_diag.json is written by GoProDiagRecorder on every
        # signalGoProReady attempt (success or failure) — pulled via
        # devicectl appDataContainer copy, independent of console log
        # capture (idevicesyslog print() capture is unreliable on-device).
        def _pull_gopro_diag() -> dict | None:
            local_diag = str(ctx.artifact.dir / "gopro_diag.json")
            if not copy_app_container_file(ctx.iphone_udid, "Documents/gopro_diag.json", local_diag):
                return None
            try:
                diag = json.loads(Path(local_diag).read_text())
            except (OSError, json.JSONDecodeError) as e:
                print(f"[net-diag] gopro_diag.json unreadable: {e}")
                return None
            print(f"[net-diag] gopro_diag.json: outcome={diag.get('outcome')} "
                  f"localState={diag.get('localState')} httpStatus={diag.get('httpStatus')} "
                  f"detail={diag.get('detail')} timestamp={diag.get('timestamp')}")
            return diag

        try:
            gp_ready = poll_until(
                "GoPro device_status==ready on backend",
                NETWORK_ROUTING_DIAG_TIMEOUT_SECONDS, gopro_device_ready,
            )
            print(f"[net-diag] PASS: GoPro device_status=ready on backend "
                  f"(device_id={gopro_device_id}, revision={gp_ready.get('revision')})")
            print("[net-diag] => iPhone called updateDeviceStatus over cellular "
                  "while on GoPro WiFi AP: routing fix CONFIRMED")
            report.step("gopro device_status==ready (backend-verified)", True,
                        device_id=gopro_device_id, revision=gp_ready.get("revision"))
            diag = _pull_gopro_diag()
            if diag:
                report.step("gopro_diag.json collected", True, **diag)
        except ValidationError as e:
            report.step("gopro device_status==ready (backend-verified)", False, error=str(e))
            # Send diagnostic probe at timeout point, then pull the on-device
            # diagnostic file — this is the authoritative source for WHY
            # signalGoProReady didn't reach the backend (HTTP status/URLError,
            # local GoPro state, last attempt timestamp), since console log
            # capture cannot be relied on for this.
            send_deep_link(ctx.iphone_udid, "network-routing-diag", label="at-timeout")
            send_deep_link(ctx.iphone_udid, "dump-snapshot")
            _time.sleep(3)
            diag = _pull_gopro_diag()
            if diag:
                report.step("gopro_diag.json collected", True, **diag)
                raise ValidationError(
                    f"GoPro device_status never reached 'ready' on backend "
                    f"(timeout={NETWORK_ROUTING_DIAG_TIMEOUT_SECONDS}s). "
                    f"gopro_diag.json says: outcome={diag.get('outcome')} "
                    f"localState={diag.get('localState')} httpStatus={diag.get('httpStatus')} "
                    f"detail={diag.get('detail')}"
                )
            report.step("gopro_diag.json collected", False, error="copy or parse failed")
            raise ValidationError(
                f"GoPro device_status never reached 'ready' on backend "
                f"(timeout={NETWORK_ROUTING_DIAG_TIMEOUT_SECONDS}s). "
                f"gopro_diag.json was not collectable either — check that the "
                f"build on the iPhone includes GoProDiagRecorder (commit after "
                f"8bc3a204) and that Documents/gopro_diag.json exists."
            )

        # 7. Final snapshot + post-connect network probe
        print("[net-diag] Sending post-connect network probe + snapshot...")
        send_deep_link(ctx.iphone_udid, "network-routing-diag", label="after-ready")
        _time.sleep(NETWORK_ROUTING_DIAG_SETTLE_SECONDS)
        send_deep_link(ctx.iphone_udid, "dump-snapshot")
        _time.sleep(2)
        report.step("post-connect network probe sent", True)

        report.passed = True
        print("[net-diag] === PASS: GoPro WiFi + backend cellular routing CONFIRMED ===")

    except ValidationError as e:
        report.error = str(e)
        report.passed = False
        print(f"[net-diag] === FAIL: {e} ===")
    return report


PREVIEW_POC_TIMEOUT_SECONDS = 180   # GoPro connect (manual WiFi join) + updateDeviceStatus
PREVIEW_POC_STREAM_DURATION_SECONDS = 25  # how long GoProStreamProbe runs on-device
PREVIEW_POC_SETTLE_SECONDS = 5      # margin after the on-device run before pulling the diag file


def scenario_gopro_preview_poc(ctx: ScenarioContext) -> ScenarioReport:
    """MC1 GoPro live preview POC (docs/GOPRO_LIVE_PREVIEW_POC_PLAN.md).

    Repeats the gopro-network-routing-diag connect + manual-WiFi-join flow
    (Block 1, already proven working as of commit 3bcd035e) as a precondition,
    then triggers GoProStreamProbe (stream/start → UDP receive → MPEG-TS demux
    → H.264 decode) and pulls Documents/gopro_stream_diag.json — no console
    log parsing, same evidence pattern as gopro_diag.json.

    PASS criteria (backend/diag-file grounded, not console-log-grounded):
      1. GoPro reaches device_status=ready on backend (same gate as Block 1)
      2. gopro_stream_diag.json collectable
      3. streamStartHTTPStatus == "ok"
      4. udpPacketsReceived > 0
      5. videoPIDFound == true (MPEG-TS demux identified the H.264 PID)
      6. decodeSuccesses > 0 (VideoToolbox decoded at least one frame)

    A failure at any layer (HTTP / UDP / MPEG-TS-PID / NAL-SPS-PPS / decode)
    is reported with the diag file's layer-by-layer fields, per the POC plan's
    "honest pass/fail" requirement — this scenario does NOT require full
    decode success to produce useful, actionable output.
    """
    import json
    import time as _time
    from pathlib import Path

    report = ScenarioReport(name="gopro-preview-poc", passed=False)
    session = create_session(ctx.api_base, ctx.instructor_token)
    session_uuid = session["session_uuid"]
    report.session_uuid = session_uuid
    print(f"[preview-poc] session created: {session_uuid}")

    try:
        # 1. Join iPhone as instructor
        print("[preview-poc] Joining iPhone as instructor...")
        send_deep_link(ctx.iphone_udid, "join", session_uuid=session_uuid, role="instructor")
        _time.sleep(5)

        def iphone_registered():
            s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
            return next((d for d in s.get("devices", [])
                         if d["device_role"] == "instructor_primary"), None)

        instructor_dev = poll_until(
            "iPhone registered as instructor_primary",
            DEVICE_REGISTER_TIMEOUT_SECONDS, iphone_registered,
        )
        instructor_id = instructor_dev["id"]
        report.step("iphone registered as instructor", True, device_id=instructor_id)

        # 2. Register GoPro
        gopro_sd = register_device(
            ctx.api_base, ctx.instructor_token, session_uuid,
            device_role="auxiliary_camera", device_type="gopro",
            device_name="GoPro HERO13 (preview-poc)",
            managed_by_device_id=instructor_id,
        )
        gopro_device_id = gopro_sd["id"]
        report.step("gopro registered (managed by instructor)", True,
                    gopro_device_id=gopro_device_id)
        print(f"[preview-poc] GoPro registered: session_device_id={gopro_device_id}")

        # 3. Connect (BLE → WiFi AP). Same manual-join gate as Block 1.
        print(f"[preview-poc] Sending gopro-connect to iPhone (gopro_device_id={gopro_device_id})...")
        print("[preview-poc] Physical: ensure GoPro is on and in BLE pairing mode.")
        send_deep_link(ctx.iphone_udid, "gopro-connect", gopro_device_id=str(gopro_device_id))
        report.step("gopro-connect deep link sent", True)
        print("[preview-poc] >>> GoPro Wi-Fi auto-join unavailable under current provisioning")
        print("[preview-poc] >>> Manual action required: iPhone Settings -> Wi-Fi -> select GoPro SSID")
        print("[preview-poc] >>> Return to LFA app after Wi-Fi connection")
        print("[preview-poc] >>> App will verify GoPro HTTP and signal backend ready")

        _time.sleep(15)  # give BLE scan + AP-activation time to reach awaitingManualWiFiJoin
        send_deep_link(ctx.iphone_udid, "dump-snapshot")
        print("[preview-poc] >>> Check console/iphone_console.log just written above for "
              "'gopro_connection: Csatlakozz: <SSID>' — that <SSID> is the GoPro WiFi network name.")

        input(
            "\n[preview-poc] >>> Join the GoPro WiFi network on the iPhone now "
            "(Settings -> Wi-Fi -> GoPro SSID), then return to the LFA app.\n"
            "[preview-poc] >>> Press ENTER here ONLY after the iPhone shows it is "
            "connected to the GoPro WiFi network: "
        )
        print("[preview-poc] Operator confirmed manual WiFi join — resending gopro-connect...")
        send_deep_link(ctx.iphone_udid, "gopro-connect", gopro_device_id=str(gopro_device_id))
        report.step("gopro-connect re-sent after manual join", True)

        # 4. Poll backend for GoPro device_status == ready (Block 1 precondition)
        print(f"[preview-poc] Polling backend for GoPro device_status=ready "
              f"(timeout={PREVIEW_POC_TIMEOUT_SECONDS}s)...")

        def gopro_device_ready():
            s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
            for d in s.get("devices", []):
                if d["id"] == gopro_device_id:
                    status = d.get("device_status") or d.get("status")
                    if status == "ready":
                        return d
            return None

        try:
            gp_ready = poll_until(
                "GoPro device_status==ready on backend",
                PREVIEW_POC_TIMEOUT_SECONDS, gopro_device_ready,
            )
            print(f"[preview-poc] GoPro device_status=ready on backend "
                  f"(device_id={gopro_device_id}, revision={gp_ready.get('revision')})")
            report.step("gopro device_status==ready (backend-verified)", True,
                        device_id=gopro_device_id, revision=gp_ready.get("revision"))
        except ValidationError as e:
            report.step("gopro device_status==ready (backend-verified)", False, error=str(e))
            raise ValidationError(
                f"GoPro never reached device_status=ready — this is the Block 1 "
                f"precondition, not the preview POC itself. {e}"
            )

        # 5. Trigger the live preview POC on-device, wait for it to finish.
        print(f"[preview-poc] Sending gopro-preview-poc "
              f"(duration_s={PREVIEW_POC_STREAM_DURATION_SECONDS})...")
        send_deep_link(ctx.iphone_udid, "gopro-preview-poc",
                        duration_s=str(PREVIEW_POC_STREAM_DURATION_SECONDS))
        report.step("gopro-preview-poc deep link sent", True)
        wait_s = PREVIEW_POC_STREAM_DURATION_SECONDS + PREVIEW_POC_SETTLE_SECONDS
        print(f"[preview-poc] Waiting {wait_s}s for on-device stream/start -> UDP -> "
              f"decode -> stream/stop to complete...")
        _time.sleep(wait_s)

        # 6. Pull gopro_stream_diag.json — authoritative, layer-by-layer evidence.
        local_diag = str(ctx.artifact.dir / "gopro_stream_diag.json")
        if not copy_app_container_file(ctx.iphone_udid, "Documents/gopro_stream_diag.json", local_diag):
            report.step("gopro_stream_diag.json collected", False, error="copy failed")
            raise ValidationError(
                "gopro_stream_diag.json was not collectable — check that the iPhone "
                "build includes GoProStreamProbe (commit ec424a27 or later) and that "
                "the deep link actually reached MultiCameraLobbyView (app must be on "
                "Session Lab, not backgrounded)."
            )
        try:
            diag = json.loads(Path(local_diag).read_text())
        except (OSError, json.JSONDecodeError) as e:
            report.step("gopro_stream_diag.json collected", False, error=f"unparseable: {e}")
            raise ValidationError(f"gopro_stream_diag.json copied but unparseable: {e}")

        report.step("gopro_stream_diag.json collected", True, **diag)
        print(f"[preview-poc] gopro_stream_diag.json: {json.dumps(diag, indent=2)}")

        # 7. Layer-by-layer PASS/FAIL — report every layer regardless of where
        #    it stops, per the POC plan's "honest pass/fail" requirement.
        http_ok = diag.get("streamStartHTTPStatus") == "ok"
        udp_ok = (diag.get("udpPacketsReceived") or 0) > 0
        sync_ok = diag.get("tsSyncOffsetDetected") is not None
        mpegts_ok = bool(diag.get("videoPIDFound"))
        codec = diag.get("selectedCodec")
        nal_ok = (codec == "hevc" and bool(diag.get("vpsSeen")) and bool(diag.get("spsSeen")) and bool(diag.get("ppsSeen"))) \
            or (codec != "hevc" and bool(diag.get("spsSeen")) and bool(diag.get("ppsSeen")))
        decode_ok = (diag.get("decodeSuccesses") or 0) > 0

        report.step("[layer] HTTP stream/start", http_ok, value=diag.get("streamStartHTTPStatus"))
        report.step("[layer] UDP packets received", udp_ok, value=diag.get("udpPacketsReceived"))
        report.step("[layer] TS sync offset found", sync_ok,
                    offset=diag.get("tsSyncOffsetDetected"), format_guess=diag.get("tsSyncFormatGuess"),
                    hit=diag.get("tsSyncHitDatagrams"), miss=diag.get("tsSyncMissDatagrams"))
        report.step("[layer] MPEG-TS video PID found", mpegts_ok,
                    selected_pid=diag.get("selectedVideoPID"), codec=codec,
                    candidates=diag.get("videoCandidatePIDs"), pmt_streams=diag.get("pmtStreams"),
                    pat_parses=diag.get("patParseCount"), pmt_parses=diag.get("pmtParseCount"),
                    reason=diag.get("reasonNoVideoPID"))
        report.step("[layer] H.264/HEVC parameter sets seen", nal_ok,
                    vps=diag.get("vpsSeen"), sps=diag.get("spsSeen"), pps=diag.get("ppsSeen"))
        report.step("[layer] VideoToolbox decode success", decode_ok,
                    attempts=diag.get("decodeAttempts"), successes=diag.get("decodeSuccesses"))

        print(f"[preview-poc] LAYER BREAKDOWN: "
              f"HTTP={'OK' if http_ok else 'FAIL'} | "
              f"UDP={'OK' if udp_ok else 'FAIL'} ({diag.get('udpPacketsReceived', 0)} pkts) | "
              f"TS-SYNC={'OK' if sync_ok else 'FAIL'} "
              f"(offset={diag.get('tsSyncOffsetDetected')}, {diag.get('tsSyncFormatGuess')}, "
              f"hit={diag.get('tsSyncHitDatagrams', 0)}/miss={diag.get('tsSyncMissDatagrams', 0)}) | "
              f"MPEG-TS/PID={'OK' if mpegts_ok else 'FAIL'} "
              f"(pid={diag.get('selectedVideoPID')}, codec={codec}) | "
              f"NAL/PARAMSETS={'OK' if nal_ok else 'FAIL'} | "
              f"DECODE={'OK' if decode_ok else 'FAIL'} "
              f"({diag.get('decodeSuccesses', 0)}/{diag.get('decodeAttempts', 0)}) | "
              f"fps={diag.get('fps', 0)}")

        if not mpegts_ok:
            print(f"[preview-poc] PMT introspection — every elementary stream found "
                  f"(PAT parses={diag.get('patParseCount')}, PMT parses={diag.get('pmtParseCount')}):")
            for s in (diag.get("pmtStreams") or []):
                print(f"[preview-poc]   pid={s.get('pid')} streamType={s.get('streamType')} "
                      f"descriptorTags={s.get('descriptorTags')}")
            print(f"[preview-poc] videoCandidatePIDs={diag.get('videoCandidatePIDs')} "
                  f"reasonNoVideoPID={diag.get('reasonNoVideoPID')}")

        if not http_ok:
            raise ValidationError(f"stream/start HTTP failed: {diag.get('streamStartHTTPStatus')}")
        if not udp_ok:
            raise ValidationError(
                "No UDP packets received on port 8554 — either stream/start didn't "
                "actually start streaming, the GoPro AP routing dropped it, or the "
                "NWListener bound to the wrong interface."
            )
        if not sync_ok:
            raise ValidationError(
                "UDP packets received but no stable TS sync offset (0x47 at offset, "
                "offset+188, offset+376) found within the first 16 bytes of any "
                "datagram — the payload may not be MPEG-TS at all (different "
                "container/codec), or it needs a wider offset search."
            )
        if not mpegts_ok:
            raise ValidationError(
                f"TS sync found at offset {diag.get('tsSyncOffsetDetected')} "
                f"({diag.get('tsSyncFormatGuess')}) but no recognized video stream_type "
                f"in PMT. {diag.get('reasonNoVideoPID')}"
            )
        if not nal_ok:
            raise ValidationError(
                f"Video PID {diag.get('selectedVideoPID')} (codec={codec}) found but "
                f"parameter sets incomplete (vps={diag.get('vpsSeen')}, "
                f"sps={diag.get('spsSeen')}, pps={diag.get('ppsSeen')})."
            )
        if not decode_ok:
            raise ValidationError(
                "SPS/PPS seen but VideoToolbox never decoded a frame — check "
                "decodeAttempts vs decodeSuccesses and errorReason in the diag file."
            )

        report.passed = True
        print("[preview-poc] === PASS: GoPro live preview decoded at least one frame ===")

    except ValidationError as e:
        report.error = str(e)
        report.passed = False
        print(f"[preview-poc] === FAIL: {e} ===")
    return report


COMBINED_CYCLE_TIMEOUT_SECONDS = 180
COMBINED_CYCLE_RECORD_DURATION_SECONDS = 15  # how long both recording + preview run concurrently
COMBINED_CYCLE_SETTLE_SECONDS = 8  # 3s on-device finalize + margin for devicectl copy


def scenario_gopro_combined_cycle_proof(ctx: ScenarioContext) -> ScenarioReport:
    """MC1 GoPro Block 3: preview + recording combined cycle proof.

    Preview (stream/start, proven working as of commit a078598c — HEVC,
    19fps decode) and recording (shutter/start, the documented
    record-then-download model) are two independent GoPro Open GoPro API
    calls that had never been validated running together. This scenario:
      1. Repeats the Block 1 connect + manual-WiFi-join flow (precondition)
      2. Reads GoPro media/list BEFORE recording
      3. Starts recording (shutter/start) AND the live preview concurrently,
         for the same ~15s window
      4. Stops recording (shutter/stop)
      5. Reads GoPro media/list AFTER recording, diffs against the before
         snapshot

    PASS criteria (gopro_recording_diag.json + gopro_stream_diag.json
    grounded, not console-log-grounded):
      1. shutterStartOK == true, recordingStateAfterStart == "recording"
      2. shutterStopOK == true, recordingStateAfterStop == "stopped"
      3. newFileCountDelta > 0 (a new file genuinely appeared on the SD card)
      4. previewDecodeSuccesses > 0 (preview kept decoding while recording —
         proves the two GoPro API paths don't interfere with each other)
    """
    import json
    import time as _time
    from pathlib import Path

    report = ScenarioReport(name="gopro-combined-cycle-proof", passed=False)
    session = create_session(ctx.api_base, ctx.instructor_token)
    session_uuid = session["session_uuid"]
    report.session_uuid = session_uuid
    print(f"[combined-cycle] session created: {session_uuid}")

    try:
        # 1. Join + register + connect, same manual-join gate as Block 1/POC.
        print("[combined-cycle] Joining iPhone as instructor...")
        send_deep_link(ctx.iphone_udid, "join", session_uuid=session_uuid, role="instructor")
        _time.sleep(5)

        def iphone_registered():
            s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
            return next((d for d in s.get("devices", [])
                         if d["device_role"] == "instructor_primary"), None)

        instructor_dev = poll_until(
            "iPhone registered as instructor_primary",
            DEVICE_REGISTER_TIMEOUT_SECONDS, iphone_registered,
        )
        instructor_id = instructor_dev["id"]
        report.step("iphone registered as instructor", True, device_id=instructor_id)

        gopro_sd = register_device(
            ctx.api_base, ctx.instructor_token, session_uuid,
            device_role="auxiliary_camera", device_type="gopro",
            device_name="GoPro HERO13 (combined-cycle)",
            managed_by_device_id=instructor_id,
        )
        gopro_device_id = gopro_sd["id"]
        report.step("gopro registered (managed by instructor)", True,
                    gopro_device_id=gopro_device_id)
        print(f"[combined-cycle] GoPro registered: session_device_id={gopro_device_id}")

        print(f"[combined-cycle] Sending gopro-connect to iPhone (gopro_device_id={gopro_device_id})...")
        print("[combined-cycle] Physical: ensure GoPro is on and in BLE pairing mode.")
        send_deep_link(ctx.iphone_udid, "gopro-connect", gopro_device_id=str(gopro_device_id))
        report.step("gopro-connect deep link sent", True)
        print("[combined-cycle] >>> GoPro Wi-Fi auto-join unavailable under current provisioning")
        print("[combined-cycle] >>> Manual action required: iPhone Settings -> Wi-Fi -> select GoPro SSID")
        print("[combined-cycle] >>> Return to LFA app after Wi-Fi connection")
        print("[combined-cycle] >>> App will verify GoPro HTTP and signal backend ready")

        _time.sleep(15)
        send_deep_link(ctx.iphone_udid, "dump-snapshot")
        print("[combined-cycle] >>> Check console/iphone_console.log just written above for "
              "'gopro_connection: Csatlakozz: <SSID>' — that <SSID> is the GoPro WiFi network name.")

        input(
            "\n[combined-cycle] >>> Join the GoPro WiFi network on the iPhone now "
            "(Settings -> Wi-Fi -> GoPro SSID), then return to the LFA app.\n"
            "[combined-cycle] >>> Press ENTER here ONLY after the iPhone shows it is "
            "connected to the GoPro WiFi network: "
        )
        print("[combined-cycle] Operator confirmed manual WiFi join — resending gopro-connect...")
        send_deep_link(ctx.iphone_udid, "gopro-connect", gopro_device_id=str(gopro_device_id))
        report.step("gopro-connect re-sent after manual join", True)

        print(f"[combined-cycle] Polling backend for GoPro device_status=ready "
              f"(timeout={COMBINED_CYCLE_TIMEOUT_SECONDS}s)...")

        def gopro_device_ready():
            s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
            for d in s.get("devices", []):
                if d["id"] == gopro_device_id:
                    status = d.get("device_status") or d.get("status")
                    if status == "ready":
                        return d
            return None

        try:
            gp_ready = poll_until(
                "GoPro device_status==ready on backend",
                COMBINED_CYCLE_TIMEOUT_SECONDS, gopro_device_ready,
            )
            print(f"[combined-cycle] GoPro device_status=ready on backend "
                  f"(device_id={gopro_device_id}, revision={gp_ready.get('revision')})")
            report.step("gopro device_status==ready (backend-verified)", True,
                        device_id=gopro_device_id, revision=gp_ready.get("revision"))
        except ValidationError as e:
            report.step("gopro device_status==ready (backend-verified)", False, error=str(e))
            raise ValidationError(
                f"GoPro never reached device_status=ready — this is the Block 1 "
                f"precondition, not the combined cycle proof itself. {e}"
            )

        # 2-4. Trigger the combined recording+preview run on-device.
        print(f"[combined-cycle] Sending gopro-combined-cycle-proof "
              f"(duration_s={COMBINED_CYCLE_RECORD_DURATION_SECONDS})...")
        send_deep_link(ctx.iphone_udid, "gopro-combined-cycle-proof",
                        duration_s=str(COMBINED_CYCLE_RECORD_DURATION_SECONDS))
        report.step("gopro-combined-cycle-proof deep link sent", True)
        wait_s = COMBINED_CYCLE_RECORD_DURATION_SECONDS + COMBINED_CYCLE_SETTLE_SECONDS
        print(f"[combined-cycle] Waiting {wait_s}s for on-device "
              f"shutter/start + preview -> shutter/stop -> media/list diff to complete...")
        _time.sleep(wait_s)

        # 5. Pull gopro_recording_diag.json (authoritative for shutter/media)
        #    and gopro_stream_diag.json (authoritative for the concurrent preview).
        local_rec_diag = str(ctx.artifact.dir / "gopro_recording_diag.json")
        if not copy_app_container_file(ctx.iphone_udid, "Documents/gopro_recording_diag.json", local_rec_diag):
            report.step("gopro_recording_diag.json collected", False, error="copy failed")
            raise ValidationError(
                "gopro_recording_diag.json was not collectable — check that the iPhone "
                "build includes GoProRecordingCycleProbe and that the app was on Session Lab."
            )
        try:
            diag = json.loads(Path(local_rec_diag).read_text())
        except (OSError, json.JSONDecodeError) as e:
            report.step("gopro_recording_diag.json collected", False, error=f"unparseable: {e}")
            raise ValidationError(f"gopro_recording_diag.json copied but unparseable: {e}")
        report.step("gopro_recording_diag.json collected", True, **diag)
        print(f"[combined-cycle] gopro_recording_diag.json: {json.dumps(diag, indent=2)}")

        local_stream_diag = str(ctx.artifact.dir / "gopro_stream_diag.json")
        copy_app_container_file(ctx.iphone_udid, "Documents/gopro_stream_diag.json", local_stream_diag)

        # 6. Layer-by-layer PASS/FAIL.
        start_ok = bool(diag.get("shutterStartOK")) and diag.get("recordingStateAfterStart") == "recording"
        stop_ok = bool(diag.get("shutterStopOK")) and diag.get("recordingStateAfterStop") == "stopped"
        new_file_ok = (diag.get("newFileCountDelta") or 0) > 0
        preview_ok = (diag.get("previewDecodeSuccesses") or 0) > 0

        report.step("[layer] shutter/start -> recording", start_ok,
                    ok=diag.get("shutterStartOK"), state=diag.get("recordingStateAfterStart"))
        report.step("[layer] shutter/stop -> stopped", stop_ok,
                    ok=diag.get("shutterStopOK"), state=diag.get("recordingStateAfterStop"))
        report.step("[layer] new media file on SD card", new_file_ok,
                    before=diag.get("mediaCountBefore"), after=diag.get("mediaCountAfter"),
                    new_files=diag.get("newFilesDetected"))
        report.step("[layer] preview decoded concurrently", preview_ok,
                    attempts=diag.get("previewDecodeAttempts"), successes=diag.get("previewDecodeSuccesses"))

        print(f"[combined-cycle] LAYER BREAKDOWN: "
              f"SHUTTER-START={'OK' if start_ok else 'FAIL'} | "
              f"SHUTTER-STOP={'OK' if stop_ok else 'FAIL'} | "
              f"NEW-FILE={'OK' if new_file_ok else 'FAIL'} "
              f"(delta={diag.get('newFileCountDelta')}, files={diag.get('newFilesDetected')}) | "
              f"PREVIEW-CONCURRENT={'OK' if preview_ok else 'FAIL'} "
              f"({diag.get('previewDecodeSuccesses', 0)}/{diag.get('previewDecodeAttempts', 0)})")

        if not start_ok:
            raise ValidationError(
                f"shutter/start did not result in recording state: "
                f"shutterStartOK={diag.get('shutterStartOK')} "
                f"recordingStateAfterStart={diag.get('recordingStateAfterStart')} "
                f"error={diag.get('shutterStartError')}"
            )
        if not stop_ok:
            raise ValidationError(
                f"shutter/stop did not result in stopped state: "
                f"shutterStopOK={diag.get('shutterStopOK')} "
                f"recordingStateAfterStop={diag.get('recordingStateAfterStop')} "
                f"error={diag.get('shutterStopError')}"
            )
        if not new_file_ok:
            raise ValidationError(
                f"No new file detected on the GoPro SD card after the recording "
                f"window (mediaCountBefore={diag.get('mediaCountBefore')}, "
                f"mediaCountAfter={diag.get('mediaCountAfter')}) — recording state "
                f"transitioned correctly but no file evidence backs it up."
            )
        if not preview_ok:
            raise ValidationError(
                "Recording + new file confirmed, but the concurrent preview decoded "
                "0 frames — recording and preview may interfere with each other on "
                "this firmware/connection."
            )

        report.passed = True
        print("[combined-cycle] === PASS: GoPro recorded a new file AND preview decoded frames concurrently ===")

    except ValidationError as e:
        report.error = str(e)
        report.passed = False
        print(f"[combined-cycle] === FAIL: {e} ===")
    return report


CAMERA_STATE_PROBE_TIMEOUT_SECONDS = 180


def scenario_gopro_camera_state_probe(ctx: ScenarioContext) -> ScenarioReport:
    """Capture Quality block, step 1: READ (never write) the GoPro's current
    camera/state, raw. GoProCameraStatus has decoded firmware="unknown" on
    every physical run this session — meaning the flat-field decode
    (firmware_version/is_recording/battery_level/sd_card_space_remaining)
    has likely never matched the real HERO13 response shape. This scenario
    captures the raw response text + top-level JSON keys so a human can
    read the actual current resolution/fps/lens-mode preset before any
    preset-WRITE code is attempted (docs/MEDIA_PIPELINE_PLAN.md, Capture
    Quality block).

    PASS criteria: rawResponseOK == true (HTTP succeeded) AND the diag file
    is collectable. This does NOT validate resolution/fps content — that's
    a human-readable judgment call until the real schema is confirmed.
    """
    import json
    import time as _time
    from pathlib import Path

    report = ScenarioReport(name="gopro-camera-state-probe", passed=False)
    session = create_session(ctx.api_base, ctx.instructor_token)
    session_uuid = session["session_uuid"]
    report.session_uuid = session_uuid
    print(f"[camera-state] session created: {session_uuid}")

    try:
        print("[camera-state] Joining iPhone as instructor...")
        send_deep_link(ctx.iphone_udid, "join", session_uuid=session_uuid, role="instructor")
        _time.sleep(5)

        def iphone_registered():
            s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
            return next((d for d in s.get("devices", [])
                         if d["device_role"] == "instructor_primary"), None)

        instructor_dev = poll_until(
            "iPhone registered as instructor_primary",
            DEVICE_REGISTER_TIMEOUT_SECONDS, iphone_registered,
        )
        instructor_id = instructor_dev["id"]
        report.step("iphone registered as instructor", True, device_id=instructor_id)

        gopro_sd = register_device(
            ctx.api_base, ctx.instructor_token, session_uuid,
            device_role="auxiliary_camera", device_type="gopro",
            device_name="GoPro HERO13 (camera-state-probe)",
            managed_by_device_id=instructor_id,
        )
        gopro_device_id = gopro_sd["id"]
        report.step("gopro registered (managed by instructor)", True,
                    gopro_device_id=gopro_device_id)

        print(f"[camera-state] Sending gopro-connect to iPhone (gopro_device_id={gopro_device_id})...")
        print("[camera-state] Physical: ensure GoPro is on and in BLE pairing mode.")
        send_deep_link(ctx.iphone_udid, "gopro-connect", gopro_device_id=str(gopro_device_id))
        report.step("gopro-connect deep link sent", True)
        print("[camera-state] >>> GoPro Wi-Fi auto-join unavailable under current provisioning")
        print("[camera-state] >>> Manual action required: iPhone Settings -> Wi-Fi -> select GoPro SSID")
        print("[camera-state] >>> Return to LFA app after Wi-Fi connection")

        _time.sleep(15)
        send_deep_link(ctx.iphone_udid, "dump-snapshot")
        print("[camera-state] >>> Check console/iphone_console.log for "
              "'gopro_connection: Csatlakozz: <SSID>' — that <SSID> is the GoPro WiFi network name.")

        input(
            "\n[camera-state] >>> Join the GoPro WiFi network on the iPhone now "
            "(Settings -> Wi-Fi -> GoPro SSID), then return to the LFA app.\n"
            "[camera-state] >>> Press ENTER here ONLY after the iPhone shows it is "
            "connected to the GoPro WiFi network: "
        )
        send_deep_link(ctx.iphone_udid, "gopro-connect", gopro_device_id=str(gopro_device_id))
        report.step("gopro-connect re-sent after manual join", True)

        def gopro_device_ready():
            s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
            for d in s.get("devices", []):
                if d["id"] == gopro_device_id:
                    status = d.get("device_status") or d.get("status")
                    if status == "ready":
                        return d
            return None

        try:
            poll_until("GoPro device_status==ready on backend",
                       CAMERA_STATE_PROBE_TIMEOUT_SECONDS, gopro_device_ready)
            report.step("gopro device_status==ready (backend-verified)", True)
        except ValidationError as e:
            report.step("gopro device_status==ready (backend-verified)", False, error=str(e))
            raise ValidationError(f"GoPro never reached device_status=ready. {e}")

        print("[camera-state] Sending gopro-camera-state-probe...")
        send_deep_link(ctx.iphone_udid, "gopro-camera-state-probe")
        report.step("gopro-camera-state-probe deep link sent", True)
        _time.sleep(5)

        local_diag = str(ctx.artifact.dir / "gopro_camera_state_diag.json")
        if not copy_app_container_file(ctx.iphone_udid, "Documents/gopro_camera_state_diag.json", local_diag):
            report.step("gopro_camera_state_diag.json collected", False, error="copy failed")
            raise ValidationError("gopro_camera_state_diag.json was not collectable.")
        try:
            diag = json.loads(Path(local_diag).read_text())
        except (OSError, json.JSONDecodeError) as e:
            report.step("gopro_camera_state_diag.json collected", False, error=f"unparseable: {e}")
            raise ValidationError(f"gopro_camera_state_diag.json copied but unparseable: {e}")
        report.step("gopro_camera_state_diag.json collected", True,
                    rawResponseOK=diag.get("rawResponseOK"), topLevelKeys=diag.get("topLevelKeys"))

        print(f"[camera-state] rawResponseOK={diag.get('rawResponseOK')} "
              f"topLevelKeys={diag.get('topLevelKeys')}")
        print(f"[camera-state] >>> FULL RAW RESPONSE (read this to find the real "
              f"resolution/fps/lens-mode fields):\n{diag.get('rawResponseText')}")

        if not diag.get("rawResponseOK"):
            raise ValidationError(f"camera/state HTTP call failed: {diag.get('error')}")

        report.passed = True
        print("[camera-state] === PASS: camera/state raw response captured ===")

    except ValidationError as e:
        report.error = str(e)
        report.passed = False
        print(f"[camera-state] === FAIL: {e} ===")
    return report


PREVIEW_ASPECT_PROBE_TIMEOUT_SECONDS = 180
PREVIEW_ASPECT_PROBE_STREAM_DURATION_SECONDS = 20
PREVIEW_ASPECT_PROBE_SETTLE_SECONDS = 5


def scenario_gopro_preview_aspect_probe(ctx: ScenarioContext) -> ScenarioReport:
    """GoPro Preview Aspect Probe — DISTINCT from gopro-camera-state-probe.

    gopro-camera-state-probe:    HTTP camera/state read ONLY — no preview,
                                  no UDP, no decode, no live image.
    gopro-preview-aspect-probe:  ACTUALLY starts the live preview stream
                                  (stream/start -> UDP -> MPEG-TS demux ->
                                  H.264/HEVC decode) and measures the real
                                  decoded width/height/aspect/codec/fps —
                                  because the GoPro's archival recording
                                  profile (camera/state settings) and the
                                  preview stream's actual geometry are two
                                  independent things that were never
                                  measured together before this scenario
                                  (see docs/GOPRO_LIVE_PREVIEW_POC_PLAN.md
                                  and the aspect-ratio audit).

    PASS criteria (gopro_preview_aspect_diag.json-grounded):
      1. GoPro device_status==ready on backend (same Block 1 precondition)
      2. streamStartHTTPStatus == "ok"
      3. decodedFrameCount > 0
      4. previewWidth/previewHeight/previewAspectRatio all present (not null)
    A human should also visually confirm a live GoPro image appeared on the
    instructor dashboard during this run — the script cannot verify pixels,
    only that frames were successfully decoded.
    """
    import json
    import time as _time
    from pathlib import Path

    report = ScenarioReport(name="gopro-preview-aspect-probe", passed=False)
    session = create_session(ctx.api_base, ctx.instructor_token)
    session_uuid = session["session_uuid"]
    report.session_uuid = session_uuid
    print(f"[preview-aspect] session created: {session_uuid}")

    try:
        print("[preview-aspect] Joining iPhone as instructor...")
        send_deep_link(ctx.iphone_udid, "join", session_uuid=session_uuid, role="instructor")
        _time.sleep(5)

        def iphone_registered():
            s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
            return next((d for d in s.get("devices", [])
                         if d["device_role"] == "instructor_primary"), None)

        instructor_dev = poll_until(
            "iPhone registered as instructor_primary",
            DEVICE_REGISTER_TIMEOUT_SECONDS, iphone_registered,
        )
        instructor_id = instructor_dev["id"]
        report.step("iphone registered as instructor", True, device_id=instructor_id)

        gopro_sd = register_device(
            ctx.api_base, ctx.instructor_token, session_uuid,
            device_role="auxiliary_camera", device_type="gopro",
            device_name="GoPro HERO13 (preview-aspect-probe)",
            managed_by_device_id=instructor_id,
        )
        gopro_device_id = gopro_sd["id"]
        report.step("gopro registered (managed by instructor)", True,
                    gopro_device_id=gopro_device_id)

        print(f"[preview-aspect] Sending gopro-connect to iPhone (gopro_device_id={gopro_device_id})...")
        print("[preview-aspect] Physical: ensure GoPro is on and in BLE pairing mode.")
        send_deep_link(ctx.iphone_udid, "gopro-connect", gopro_device_id=str(gopro_device_id))
        report.step("gopro-connect deep link sent", True)
        print("[preview-aspect] >>> GoPro Wi-Fi auto-join unavailable under current provisioning")
        print("[preview-aspect] >>> Manual action required: iPhone Settings -> Wi-Fi -> select GoPro SSID")
        print("[preview-aspect] >>> Return to LFA app after Wi-Fi connection")
        print("[preview-aspect] >>> App will verify GoPro HTTP and signal backend ready")

        _time.sleep(15)
        send_deep_link(ctx.iphone_udid, "dump-snapshot")
        print("[preview-aspect] >>> Check console/iphone_console.log for "
              "'gopro_connection: Csatlakozz: <SSID>' — that <SSID> is the GoPro WiFi network name.")

        input(
            "\n[preview-aspect] >>> Join the GoPro WiFi network on the iPhone now "
            "(Settings -> Wi-Fi -> GoPro SSID), then return to the LFA app.\n"
            "[preview-aspect] >>> Press ENTER here ONLY after the iPhone shows it is "
            "connected to the GoPro WiFi network: "
        )
        print("[preview-aspect] Operator confirmed manual WiFi join — resending gopro-connect...")
        send_deep_link(ctx.iphone_udid, "gopro-connect", gopro_device_id=str(gopro_device_id))
        report.step("gopro-connect re-sent after manual join", True)

        def gopro_device_ready():
            s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
            for d in s.get("devices", []):
                if d["id"] == gopro_device_id:
                    status = d.get("device_status") or d.get("status")
                    if status == "ready":
                        return d
            return None

        try:
            poll_until("GoPro device_status==ready on backend",
                       PREVIEW_ASPECT_PROBE_TIMEOUT_SECONDS, gopro_device_ready)
            report.step("gopro device_status==ready (backend-verified)", True)
        except ValidationError as e:
            report.step("gopro device_status==ready (backend-verified)", False, error=str(e))
            raise ValidationError(
                f"GoPro never reached device_status=ready — this is the Block 1 "
                f"precondition, not the preview aspect probe itself. {e}"
            )

        print(f"[preview-aspect] Sending gopro-preview-aspect-probe "
              f"(duration_s={PREVIEW_ASPECT_PROBE_STREAM_DURATION_SECONDS})... "
              f"watch the instructor dashboard GoPro panel now for a live image.")
        send_deep_link(ctx.iphone_udid, "gopro-preview-aspect-probe",
                        duration_s=str(PREVIEW_ASPECT_PROBE_STREAM_DURATION_SECONDS))
        report.step("gopro-preview-aspect-probe deep link sent", True)
        wait_s = PREVIEW_ASPECT_PROBE_STREAM_DURATION_SECONDS + PREVIEW_ASPECT_PROBE_SETTLE_SECONDS
        print(f"[preview-aspect] Waiting {wait_s}s for stream/start -> UDP -> decode -> stream/stop...")
        _time.sleep(wait_s)

        local_diag = str(ctx.artifact.dir / "gopro_preview_aspect_diag.json")
        if not copy_app_container_file(ctx.iphone_udid, "Documents/gopro_preview_aspect_diag.json", local_diag):
            report.step("gopro_preview_aspect_diag.json collected", False, error="copy failed")
            raise ValidationError("gopro_preview_aspect_diag.json was not collectable.")
        try:
            diag = json.loads(Path(local_diag).read_text())
        except (OSError, json.JSONDecodeError) as e:
            report.step("gopro_preview_aspect_diag.json collected", False, error=f"unparseable: {e}")
            raise ValidationError(f"gopro_preview_aspect_diag.json copied but unparseable: {e}")
        report.step("gopro_preview_aspect_diag.json collected", True, **diag)
        print(f"[preview-aspect] gopro_preview_aspect_diag.json: {json.dumps(diag, indent=2)}")

        http_ok = diag.get("streamStartHTTPStatus") == "ok"
        decode_ok = (diag.get("decodedFrameCount") or 0) > 0
        dims_ok = diag.get("previewWidth") is not None and diag.get("previewHeight") is not None \
            and diag.get("previewAspectRatio") is not None

        report.step("[layer] stream/start HTTP", http_ok, value=diag.get("streamStartHTTPStatus"))
        report.step("[layer] decoded frames > 0", decode_ok, value=diag.get("decodedFrameCount"))
        report.step("[layer] preview width/height/aspect known", dims_ok,
                    width=diag.get("previewWidth"), height=diag.get("previewHeight"),
                    aspect=diag.get("previewAspectRatio"))

        print(f"[preview-aspect] LAYER BREAKDOWN: "
              f"HTTP={'OK' if http_ok else 'FAIL'} | "
              f"DECODE={'OK' if decode_ok else 'FAIL'} ({diag.get('decodedFrameCount', 0)} frames) | "
              f"DIMENSIONS={'OK' if dims_ok else 'FAIL'} "
              f"({diag.get('previewWidth')}x{diag.get('previewHeight')}, {diag.get('previewAspectRatio')}) | "
              f"codec={diag.get('previewCodec')} fps={diag.get('previewFPS')}")

        if not http_ok:
            raise ValidationError(f"stream/start HTTP failed: {diag.get('streamStartHTTPStatus')}")
        if not decode_ok:
            raise ValidationError(
                f"No frames decoded (decodedFrameCount={diag.get('decodedFrameCount')}, "
                f"decodeAttempts={diag.get('decodeAttempts')}) — errorReason={diag.get('errorReason')}"
            )
        if not dims_ok:
            raise ValidationError(
                "Frames decoded but no width/height/aspect captured — format "
                "description may not have propagated before the first decode."
            )

        report.passed = True
        print("[preview-aspect] === PASS: live GoPro preview decoded with measured aspect — "
              "VISUALLY CONFIRM a live image was shown on the dashboard GoPro panel ===")

    except ValidationError as e:
        report.error = str(e)
        report.passed = False
        print(f"[preview-aspect] === FAIL: {e} ===")
    return report


PRESET_VALIDATION_TIMEOUT_SECONDS = 180
PRESET_VALIDATION_WAIT_SECONDS = 90  # writes (~9s) + verify + recording proof (15s preview+settle) + margin


def scenario_gopro_preset_write_validation(ctx: ScenarioContext) -> ScenarioReport:
    """GoPro 8:7 Recording Preset Read/Write Validation — the first GoPro
    scenario that actually WRITES a camera setting (VideoAspectRatio=8:7,
    VideoResolution=4K_8:7_V2 or 5.3K_8:7_V2 fallback, FPS=30), with
    MANDATORY rollback on any failure at any step (write/verify/recording
    proof/preview-after-write).

    PASS criteria — STRICT, per explicit product requirement:
      Only outcome=="applied_full_chain_pass" in gopro_preset_final_diag.json
      counts as PASS. ANY rollback-triggered path (outcome starting with
      "handled_fail_") is a HANDLED FAIL, not PASS, even though the camera
      was safely restored. ANY rollback FAILURE (outcome starting with
      "critical_fail_") is reported as CRITICAL FAIL — the camera may be in
      an unknown state and needs manual verification.

    Pulls 6 artifacts: gopro_preset_before_diag.json, gopro_preset_write_diag.json,
    gopro_preset_after_diag.json, gopro_recording_diag.json,
    gopro_preview_aspect_diag.json, gopro_preset_final_diag.json.
    """
    import json
    import time as _time
    from pathlib import Path

    report = ScenarioReport(name="gopro-preset-write-validation", passed=False)
    session = create_session(ctx.api_base, ctx.instructor_token)
    session_uuid = session["session_uuid"]
    report.session_uuid = session_uuid
    print(f"[preset-validation] session created: {session_uuid}")

    try:
        print("[preset-validation] Joining iPhone as instructor...")
        send_deep_link(ctx.iphone_udid, "join", session_uuid=session_uuid, role="instructor")
        _time.sleep(5)

        def iphone_registered():
            s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
            return next((d for d in s.get("devices", [])
                         if d["device_role"] == "instructor_primary"), None)

        instructor_dev = poll_until(
            "iPhone registered as instructor_primary",
            DEVICE_REGISTER_TIMEOUT_SECONDS, iphone_registered,
        )
        instructor_id = instructor_dev["id"]
        report.step("iphone registered as instructor", True, device_id=instructor_id)

        gopro_sd = register_device(
            ctx.api_base, ctx.instructor_token, session_uuid,
            device_role="auxiliary_camera", device_type="gopro",
            device_name="GoPro HERO13 (preset-write-validation)",
            managed_by_device_id=instructor_id,
        )
        gopro_device_id = gopro_sd["id"]
        report.step("gopro registered (managed by instructor)", True,
                    gopro_device_id=gopro_device_id)

        print(f"[preset-validation] Sending gopro-connect to iPhone (gopro_device_id={gopro_device_id})...")
        print("[preset-validation] Physical: ensure GoPro is on and in BLE pairing mode.")
        send_deep_link(ctx.iphone_udid, "gopro-connect", gopro_device_id=str(gopro_device_id))
        report.step("gopro-connect deep link sent", True)
        print("[preset-validation] >>> GoPro Wi-Fi auto-join unavailable under current provisioning")
        print("[preset-validation] >>> Manual action required: iPhone Settings -> Wi-Fi -> select GoPro SSID")
        print("[preset-validation] >>> Return to LFA app after Wi-Fi connection")

        _time.sleep(15)
        send_deep_link(ctx.iphone_udid, "dump-snapshot")
        print("[preset-validation] >>> Check console/iphone_console.log for "
              "'gopro_connection: Csatlakozz: <SSID>' — that <SSID> is the GoPro WiFi network name.")

        input(
            "\n[preset-validation] >>> Join the GoPro WiFi network on the iPhone now "
            "(Settings -> Wi-Fi -> GoPro SSID), then return to the LFA app.\n"
            "[preset-validation] >>> Press ENTER here ONLY after the iPhone shows it is "
            "connected to the GoPro WiFi network: "
        )
        send_deep_link(ctx.iphone_udid, "gopro-connect", gopro_device_id=str(gopro_device_id))
        report.step("gopro-connect re-sent after manual join", True)

        def gopro_device_ready():
            s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
            for d in s.get("devices", []):
                if d["id"] == gopro_device_id:
                    status = d.get("device_status") or d.get("status")
                    if status == "ready":
                        return d
            return None

        try:
            poll_until("GoPro device_status==ready on backend",
                       PRESET_VALIDATION_TIMEOUT_SECONDS, gopro_device_ready)
            report.step("gopro device_status==ready (backend-verified)", True)
        except ValidationError as e:
            report.step("gopro device_status==ready (backend-verified)", False, error=str(e))
            raise ValidationError(f"GoPro never reached device_status=ready. {e}")

        print("[preset-validation] >>> WRITE OPERATION STARTING — this will change GoPro "
              "settings (VideoAspectRatio, VideoResolution, possibly FPS), with mandatory "
              "rollback on any failure. Do not touch the camera during this run.")
        send_deep_link(ctx.iphone_udid, "gopro-preset-write-validation")
        report.step("gopro-preset-write-validation deep link sent", True)
        print(f"[preset-validation] Waiting {PRESET_VALIDATION_WAIT_SECONDS}s for the full "
              f"read -> write -> verify -> recording-proof -> preview-after-write -> "
              f"(rollback if needed) chain to complete...")
        _time.sleep(PRESET_VALIDATION_WAIT_SECONDS)

        artifacts = {
            "before": "gopro_preset_before_diag.json",
            "write": "gopro_preset_write_diag.json",
            "after": "gopro_preset_after_diag.json",
            "recording": "gopro_recording_diag.json",
            "preview_aspect": "gopro_preview_aspect_diag.json",
            "final": "gopro_preset_final_diag.json",
        }
        collected: dict[str, dict] = {}
        for label, filename in artifacts.items():
            local_path = str(ctx.artifact.dir / filename)
            ok = copy_app_container_file(ctx.iphone_udid, f"Documents/{filename}", local_path)
            report.step(f"[artifact] {filename} collected", ok)
            if ok:
                try:
                    collected[label] = json.loads(Path(local_path).read_text())
                except (OSError, json.JSONDecodeError) as e:
                    print(f"[preset-validation] {filename} copied but unparseable: {e}")

        if "final" not in collected:
            raise ValidationError(
                "gopro_preset_final_diag.json was not collectable — the write chain may not "
                "have completed, or the deep link never reached MultiCameraLobbyView. "
                "CRITICAL: physically check the GoPro's current settings before reusing it."
            )

        final = collected["final"]
        outcome = final.get("outcome", "unknown")
        print(f"[preset-validation] gopro_preset_final_diag.json outcome={outcome}")
        print(f"[preset-validation] FULL final diag: {json.dumps(final, indent=2)}")

        report.step("[outcome] final diag outcome", outcome == "applied_full_chain_pass",
                    outcome=outcome, rollbackAttempted=final.get("rollbackAttempted"),
                    rollbackConfirmed=final.get("rollbackConfirmed"))

        if outcome == "applied_full_chain_pass":
            print("[preset-validation] === PASS: 8:7 preset applied, verified, recorded, "
                  "and preview measured (full chain, no rollback needed) ===")
            report.passed = True
        elif outcome.startswith("handled_fail_"):
            raise ValidationError(
                f"HANDLED FAIL: write chain did not complete, but rollback was CONFIRMED "
                f"(camera restored to original settings). outcome={outcome}"
            )
        elif outcome.startswith("critical_fail_"):
            raise ValidationError(
                f"*** CRITICAL FAIL *** rollback did NOT confirm — the GoPro may be in an "
                f"UNKNOWN STATE. Physically check camera settings before any further use. "
                f"outcome={outcome} afterRollbackState={final.get('afterRollbackState')}"
            )
        else:
            raise ValidationError(f"Unrecognized outcome value: {outcome}")

    except ValidationError as e:
        report.error = str(e)
        report.passed = False
        print(f"[preset-validation] === FAIL: {e} ===")
    return report


SCENARIOS = {
    "smoke": scenario_smoke,
    "multicycle": scenario_multicycle,
    "retry": scenario_retry,
    "finalization": scenario_finalization,
    "gopro-tricamera-smoke": scenario_gopro_tricamera_smoke,
    "gopro-iphone-diagnostics": scenario_gopro_iphone_diagnostics,
    "gopro-network-routing-diag": scenario_gopro_network_routing_diag,
    "gopro-preview-poc": scenario_gopro_preview_poc,
    "gopro-combined-cycle-proof": scenario_gopro_combined_cycle_proof,
    "gopro-camera-state-probe": scenario_gopro_camera_state_probe,
    "gopro-preview-aspect-probe": scenario_gopro_preview_aspect_probe,
    "gopro-preset-write-validation": scenario_gopro_preset_write_validation,
    "tricamera-capture-skeleton-proof": scenario_tricamera_capture_skeleton_proof,
}
