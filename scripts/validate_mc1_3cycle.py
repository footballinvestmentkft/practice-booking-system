#!/usr/bin/env python3
"""MC1 3-cycle physical validation — drives the backend + iOS devices, asserts on
backend ground truth.

What this does:
  1. Logs in as instructor + player (POST /auth/login).
  2. Creates a session via the backend API (instructor).
  3. Sends `lfa-mc1://automate?action=join...` to both devices (xcrun devicectl)
     so each device's app joins/registers without a manual QR scan.
  4. Polls until both devices have registered.
  5. Sends `mark-ready` to the iPad → session DEVICES_READY.
  6. For 3 cycles: sends `begin-cycle`, polls until both devices report
     confirmed_start, waits RECORD_SECONDS, sends `end-cycle`, polls until both
     devices report confirmed_stop.
  7. Prints a single PASS/FAIL summary.

PASS/FAIL is decided exclusively from backend responses (cycle_devices[].recording_status,
cycle.status). iOS console logs ([CCO]/[PCO]) are collected as corroborating evidence only
— see validate_mc1_3cycle.sh, which captures them in parallel.

This does not touch ORCH-6 cycle logic, GoPro, or upload — it only calls existing
backend/iOS surfaces (MC1-AUTO-1 deep links + existing REST endpoints).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

RECORD_SECONDS = 4
POLL_INTERVAL_SECONDS = 1.5
POLL_TIMEOUT_SECONDS = 30
DEVICE_REGISTER_TIMEOUT_SECONDS = 120
DEVICES_READY_TIMEOUT_SECONDS = 20


@dataclass
class CycleResult:
    cycle_index: int
    started_ok: bool
    stopped_ok: bool
    ipad_recording_status_start: str | None = None
    iphone_recording_status_start: str | None = None
    ipad_recording_status_stop: str | None = None
    iphone_recording_status_stop: str | None = None
    notes: list[str] = field(default_factory=list)


class ValidationError(RuntimeError):
    pass


def http_request(method: str, url: str, token: str | None = None, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise ValidationError(f"HTTP {e.code} {method} {url}: {detail}") from e


def login(api_base: str, email: str, password: str) -> str:
    resp = http_request("POST", f"{api_base}/api/v1/auth/login", body={"email": email, "password": password})
    return resp["access_token"]


def create_session(api_base: str, instructor_token: str) -> dict:
    return http_request(
        "POST", f"{api_base}/api/v1/multicamera/sessions",
        token=instructor_token, body={"max_participants": 2, "max_devices": 4},
    )


def get_session(api_base: str, token: str, session_uuid: str) -> dict:
    return http_request("GET", f"{api_base}/api/v1/multicamera/sessions/{session_uuid}", token=token)


def list_cycles(api_base: str, token: str, session_uuid: str) -> list[dict]:
    return http_request("GET", f"{api_base}/api/v1/multicamera/sessions/{session_uuid}/cycles", token=token)


def send_deep_link(udid: str, action: str, **params: str) -> None:
    query = urlencode({"action": action, **params})
    url = f"lfa-mc1://automate?{query}"
    result = subprocess.run(
        ["xcrun", "devicectl", "device", "send", "url", "--device", udid, url],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ValidationError(f"devicectl send url failed for {udid} ({url}): {result.stderr.strip()}")
    print(f"  -> deep link sent to {udid}: {url}")


def poll_until(description: str, timeout_s: float, fn) -> Any:
    deadline = time.monotonic() + timeout_s
    last_result = None
    while time.monotonic() < deadline:
        last_result = fn()
        if last_result:
            return last_result
        time.sleep(POLL_INTERVAL_SECONDS)
    raise ValidationError(f"Timeout waiting for: {description}")


def device_recording_status(cycle: dict, session_device_id: int) -> str | None:
    for cd in cycle.get("cycle_devices", []):
        if cd.get("session_device_id") == session_device_id:
            return cd.get("recording_status")
    return None


def latest_cycle(cycles: list[dict]) -> dict | None:
    if not cycles:
        return None
    return max(cycles, key=lambda c: c["cycle_index"])


def run(args: argparse.Namespace) -> int:
    print("=== MC1 3-cycle physical validation ===")
    print(f"api_base={args.api_base} ipad_udid={args.ipad_udid} iphone_udid={args.iphone_udid}")

    instructor_token = login(args.api_base, args.instructor_email, args.instructor_password)
    player_token = login(args.api_base, args.player_email, args.player_password)
    print("Logged in as instructor + player.")

    session = create_session(args.api_base, instructor_token)
    session_uuid = session["session_uuid"]
    print(f"Session created: {session_uuid}")

    print("Sending join deep link to iPad (instructor)...")
    send_deep_link(args.ipad_udid, "join", session_uuid=session_uuid, role="instructor")
    print("Sending join deep link to iPhone (player)...")
    send_deep_link(args.iphone_udid, "join", session_uuid=session_uuid, role="player")

    def devices_registered() -> dict | None:
        s = get_session(args.api_base, instructor_token, session_uuid)
        devices = s.get("devices", [])
        ipad = next((d for d in devices if d["device_role"] == "instructor_primary"), None)
        iphone = next((d for d in devices if d["device_role"] == "player_primary"), None)
        return {"session": s, "ipad": ipad, "iphone": iphone} if ipad and iphone else None

    print("Waiting for both devices to auto-register...")
    reg = poll_until("both devices registered", DEVICE_REGISTER_TIMEOUT_SECONDS, devices_registered)
    ipad_device_id = reg["ipad"]["id"]
    iphone_device_id = reg["iphone"]["id"]
    print(f"  ipad session_device_id={ipad_device_id} iphone session_device_id={iphone_device_id}")

    print("Sending mark-ready deep link to iPad...")
    send_deep_link(args.ipad_udid, "mark-ready")

    def devices_ready() -> bool:
        s = get_session(args.api_base, instructor_token, session_uuid)
        return s["status"] in ("devices_ready", "active")

    poll_until("session DEVICES_READY", DEVICES_READY_TIMEOUT_SECONDS, devices_ready)
    print("Session is DEVICES_READY.")

    results: list[CycleResult] = []
    for cycle_num in range(1, 4):
        print(f"\n--- Cycle {cycle_num}/3 ---")
        cr = CycleResult(cycle_index=cycle_num - 1, started_ok=False, stopped_ok=False)

        print("Sending begin-cycle deep link to iPad...")
        send_deep_link(args.ipad_udid, "begin-cycle")

        def confirmed_start() -> dict | None:
            cycles = list_cycles(args.api_base, instructor_token, session_uuid)
            cyc = latest_cycle(cycles)
            if not cyc or cyc["cycle_index"] != cr.cycle_index:
                return None
            ipad_status = device_recording_status(cyc, ipad_device_id)
            iphone_status = device_recording_status(cyc, iphone_device_id)
            if ipad_status == "confirmed_start" and iphone_status == "confirmed_start":
                return {"cycle": cyc, "ipad": ipad_status, "iphone": iphone_status}
            return None

        try:
            confirmed = poll_until(
                f"cycle {cycle_num} confirmed_start on both devices",
                POLL_TIMEOUT_SECONDS, confirmed_start,
            )
            cr.started_ok = True
            cr.ipad_recording_status_start = confirmed["ipad"]
            cr.iphone_recording_status_start = confirmed["iphone"]
            print(f"  confirmed_start OK (ipad={confirmed['ipad']} iphone={confirmed['iphone']})")
        except ValidationError as e:
            cr.notes.append(str(e))
            print(f"  FAIL: {e}")
            results.append(cr)
            continue

        print(f"Recording for {RECORD_SECONDS}s...")
        time.sleep(RECORD_SECONDS)

        print("Sending end-cycle deep link to iPad...")
        send_deep_link(args.ipad_udid, "end-cycle")

        def confirmed_stop() -> dict | None:
            cycles = list_cycles(args.api_base, instructor_token, session_uuid)
            cyc = latest_cycle(cycles)
            if not cyc or cyc["cycle_index"] != cr.cycle_index:
                return None
            ipad_status = device_recording_status(cyc, ipad_device_id)
            iphone_status = device_recording_status(cyc, iphone_device_id)
            if (
                cyc["status"] == "completed"
                and ipad_status == "confirmed_stop"
                and iphone_status == "confirmed_stop"
            ):
                return {"cycle": cyc, "ipad": ipad_status, "iphone": iphone_status}
            return None

        try:
            confirmed = poll_until(
                f"cycle {cycle_num} confirmed_stop on both devices",
                POLL_TIMEOUT_SECONDS, confirmed_stop,
            )
            cr.stopped_ok = True
            cr.ipad_recording_status_stop = confirmed["ipad"]
            cr.iphone_recording_status_stop = confirmed["iphone"]
            print(f"  confirmed_stop OK (ipad={confirmed['ipad']} iphone={confirmed['iphone']})")
        except ValidationError as e:
            cr.notes.append(str(e))
            print(f"  FAIL: {e}")

        results.append(cr)

    print("\n=== PASS/FAIL SUMMARY ===")
    all_pass = True
    for r in results:
        status = "PASS" if (r.started_ok and r.stopped_ok) else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(
            f"Cycle {r.cycle_index}: {status}"
            f" | start(ipad={r.ipad_recording_status_start}, iphone={r.iphone_recording_status_start})"
            f" | stop(ipad={r.ipad_recording_status_stop}, iphone={r.iphone_recording_status_stop})"
        )
        for note in r.notes:
            print(f"    note: {note}")

    print(f"\nSession UUID: {session_uuid}")
    print(f"OVERALL: {'PASS' if all_pass and len(results) == 3 else 'FAIL'}")
    return 0 if all_pass and len(results) == 3 else 1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api-base", required=True, help="e.g. https://...vercel.app (no trailing slash)")
    p.add_argument("--ipad-udid", required=True)
    p.add_argument("--iphone-udid", required=True)
    p.add_argument("--instructor-email", required=True)
    p.add_argument("--instructor-password", required=True)
    p.add_argument("--player-email", required=True)
    p.add_argument("--player-password", required=True)
    return p.parse_args()


if __name__ == "__main__":
    try:
        sys.exit(run(parse_args()))
    except ValidationError as e:
        print(f"\nFATAL: {e}")
        sys.exit(2)
