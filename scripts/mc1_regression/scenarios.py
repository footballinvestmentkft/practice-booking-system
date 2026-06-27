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
    create_session,
    device_recording_status,
    get_session,
    latest_cycle,
    list_cycles,
    poll_until,
    send_deep_link,
)

DEVICE_REGISTER_TIMEOUT_SECONDS = 120
DEVICES_READY_TIMEOUT_SECONDS = 20
CYCLE_CONFIRM_TIMEOUT_SECONDS = 30
RECORD_SECONDS = 4


def _join_both_devices(ctx: ScenarioContext, report: ScenarioReport, session_uuid: str) -> tuple[int, int]:
    print("Sending join deep link to iPad (instructor)...")
    send_deep_link(ctx.ipad_udid, "join", session_uuid=session_uuid, role="instructor")
    print("Sending join deep link to iPhone (player)...")
    send_deep_link(ctx.iphone_udid, "join", session_uuid=session_uuid, role="player")

    def devices_registered():
        s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
        devices = s.get("devices", [])
        ipad = next((d for d in devices if d["device_role"] == "instructor_primary"), None)
        iphone = next((d for d in devices if d["device_role"] == "player_primary"), None)
        return (ipad, iphone) if ipad and iphone else None

    ipad, iphone = poll_until("both devices registered", DEVICE_REGISTER_TIMEOUT_SECONDS, devices_registered)
    report.step("both devices registered", True, ipad_device_id=ipad["id"], iphone_device_id=iphone["id"])
    return ipad["id"], iphone["id"]


def _mark_devices_ready(ctx: ScenarioContext, report: ScenarioReport, session_uuid: str) -> None:
    print("Sending mark-ready deep link to iPad...")
    send_deep_link(ctx.ipad_udid, "mark-ready")

    def devices_ready():
        s = get_session(ctx.api_base, ctx.instructor_token, session_uuid)
        return s["status"] in ("devices_ready", "active")

    poll_until("session DEVICES_READY", DEVICES_READY_TIMEOUT_SECONDS, devices_ready)
    report.step("session DEVICES_READY", True)


def _run_one_cycle(
    ctx: ScenarioContext, report: ScenarioReport, session_uuid: str,
    ipad_device_id: int, iphone_device_id: int, cycle_index: int, record_seconds: int = RECORD_SECONDS,
) -> bool:
    print(f"Sending begin-cycle deep link to iPad (cycle {cycle_index})...")
    send_deep_link(ctx.ipad_udid, "begin-cycle")

    def confirmed_start():
        cycles = list_cycles(ctx.api_base, ctx.instructor_token, session_uuid)
        cyc = latest_cycle(cycles)
        if not cyc or cyc["cycle_index"] != cycle_index:
            return None
        ipad_status = device_recording_status(cyc, ipad_device_id)
        iphone_status = device_recording_status(cyc, iphone_device_id)
        if ipad_status == "confirmed_start" and iphone_status == "confirmed_start":
            return {"ipad": ipad_status, "iphone": iphone_status}
        return None

    try:
        confirmed = poll_until(
            f"cycle {cycle_index} confirmed_start on both devices",
            CYCLE_CONFIRM_TIMEOUT_SECONDS, confirmed_start,
        )
        report.step(f"cycle {cycle_index} confirmed_start", True, **confirmed)
    except ValidationError as e:
        report.step(f"cycle {cycle_index} confirmed_start", False, error=str(e))
        return False

    print(f"Recording for {record_seconds}s...")
    import time
    time.sleep(record_seconds)

    print(f"Sending end-cycle deep link to iPad (cycle {cycle_index})...")
    send_deep_link(ctx.ipad_udid, "end-cycle")

    def confirmed_stop():
        cycles = list_cycles(ctx.api_base, ctx.instructor_token, session_uuid)
        cyc = latest_cycle(cycles)
        if not cyc or cyc["cycle_index"] != cycle_index:
            return None
        ipad_status = device_recording_status(cyc, ipad_device_id)
        iphone_status = device_recording_status(cyc, iphone_device_id)
        if cyc["status"] == "completed" and ipad_status == "confirmed_stop" and iphone_status == "confirmed_stop":
            return {"ipad": ipad_status, "iphone": iphone_status}
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
        return False


def scenario_smoke(ctx: ScenarioContext) -> ScenarioReport:
    report = ScenarioReport(name="smoke", passed=False)
    session = create_session(ctx.api_base, ctx.instructor_token)
    session_uuid = session["session_uuid"]
    report.session_uuid = session_uuid
    print(f"[smoke] session created: {session_uuid}")

    try:
        ipad_id, iphone_id = _join_both_devices(ctx, report, session_uuid)
        send_deep_link(ctx.ipad_udid, "dump-snapshot")
        send_deep_link(ctx.iphone_udid, "dump-snapshot")
        _mark_devices_ready(ctx, report, session_uuid)
        ok = _run_one_cycle(ctx, report, session_uuid, ipad_id, iphone_id, cycle_index=0)
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
        ipad_id, iphone_id = _join_both_devices(ctx, report, session_uuid)
        send_deep_link(ctx.ipad_udid, "dump-snapshot")
        send_deep_link(ctx.iphone_udid, "dump-snapshot")
        _mark_devices_ready(ctx, report, session_uuid)

        all_ok = True
        for cycle_index in range(ctx.cycles):
            ok = _run_one_cycle(ctx, report, session_uuid, ipad_id, iphone_id, cycle_index=cycle_index)
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


SCENARIOS = {
    "smoke": scenario_smoke,
    "multicycle": scenario_multicycle,
    "retry": scenario_retry,
    "finalization": scenario_finalization,
}
