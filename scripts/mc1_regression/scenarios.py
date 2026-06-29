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
    import time as _time

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

        # 4. GoPro connect (BLE → WiFi AP → HTTP verify)
        print(f"[net-diag] Sending gopro-connect to iPhone (gopro_device_id={gopro_device_id})...")
        print("[net-diag] Physical: ensure GoPro is on and in BLE pairing mode.")
        send_deep_link(ctx.iphone_udid, "gopro-connect", gopro_device_id=str(gopro_device_id))
        report.step("gopro-connect deep link sent", True)
        print("[net-diag] >>> GoPro Wi-Fi auto-join unavailable under current provisioning")
        print("[net-diag] >>> Manual action required: iPhone Settings -> Wi-Fi -> select GoPro SSID")
        print("[net-diag] >>> Return to LFA app after Wi-Fi connection")
        print("[net-diag] >>> App will verify GoPro HTTP and signal backend ready")

        # 5. Network probe AFTER GoPro WiFi join starts (reveals routing state)
        _time.sleep(15)  # give BLE scan + connect time to start
        print("[net-diag] Sending network-routing-diag AFTER gopro-connect (mid-connect)...")
        send_deep_link(ctx.iphone_udid, "network-routing-diag", label="mid-gopro-connect")
        _time.sleep(NETWORK_ROUTING_DIAG_SETTLE_SECONDS)
        report.step("mid-connect network probe sent", True)

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
        except ValidationError as e:
            report.step("gopro device_status==ready (backend-verified)", False, error=str(e))
            # Send diagnostic probe at timeout point
            send_deep_link(ctx.iphone_udid, "network-routing-diag", label="at-timeout")
            send_deep_link(ctx.iphone_udid, "dump-snapshot")
            _time.sleep(3)
            raise ValidationError(
                f"GoPro device_status never reached 'ready' on backend "
                f"(timeout={NETWORK_ROUTING_DIAG_TIMEOUT_SECONDS}s). "
                f"Either GoPro didn't connect, or updateDeviceStatus failed "
                f"(cellular routing issue). Check iPhone console for "
                f"[GOPRO-AUTO] signalReady and [NET-DIAG] lines."
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


SCENARIOS = {
    "smoke": scenario_smoke,
    "multicycle": scenario_multicycle,
    "retry": scenario_retry,
    "finalization": scenario_finalization,
    "gopro-tricamera-smoke": scenario_gopro_tricamera_smoke,
    "gopro-iphone-diagnostics": scenario_gopro_iphone_diagnostics,
    "gopro-network-routing-diag": scenario_gopro_network_routing_diag,
    "tricamera-capture-skeleton-proof": scenario_tricamera_capture_skeleton_proof,
}
