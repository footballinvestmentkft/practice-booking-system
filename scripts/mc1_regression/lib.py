"""Shared helpers for the MC1 physical regression suite (MC1-AUTO-2).

Backend API calls, lfa-mc1:// deep links, polling, and artifact-directory
management used by every scenario in scenarios.py. PASS/FAIL is always decided
from backend responses here — nothing in this module trusts on-device UI or
console output for a verdict; console/snapshot capture is corroborating
evidence written alongside the report.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

POLL_INTERVAL_SECONDS = 1.5
SNAPSHOT_RE = re.compile(r"\[MC1-SNAPSHOT-BEGIN\](.*?)\[MC1-SNAPSHOT-END\]", re.DOTALL)


class ValidationError(RuntimeError):
    pass


# ── Backend API ────────────────────────────────────────────────────────────

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


def transition_session(api_base: str, token: str, session_uuid: str, target: str) -> dict:
    """GET fresh revision then PATCH session status. Retries once on 409 revision conflict."""
    session = get_session(api_base, token, session_uuid)
    revision = session.get("revision", 0)
    try:
        return http_request(
            "PATCH", f"{api_base}/api/v1/multicamera/sessions/{session_uuid}/status",
            token=token, body={"target_status": target, "revision": revision},
        )
    except ValidationError as e:
        if "409" not in str(e):
            raise
        # Revision bumped between GET and PATCH — fetch fresh and retry once.
        print(f"  [transition_session] 409 on first attempt, retrying with fresh revision...")
        session = get_session(api_base, token, session_uuid)
        return http_request(
            "PATCH", f"{api_base}/api/v1/multicamera/sessions/{session_uuid}/status",
            token=token, body={"target_status": target, "revision": session.get("revision", 0)},
        )


def register_device(
    api_base: str, token: str, session_uuid: str,
    device_role: str, device_type: str, device_name: str,
    managed_by_device_id: int | None = None,
) -> dict:
    body: dict = {
        "device_role": device_role,
        "device_type": device_type,
        "device_name": device_name,
    }
    if managed_by_device_id is not None:
        body["managed_by_device_id"] = managed_by_device_id
    return http_request(
        "POST", f"{api_base}/api/v1/multicamera/sessions/{session_uuid}/devices",
        token=token, body=body,
    )


def confirm_device_start(
    api_base: str, token: str, session_uuid: str,
    cycle_id: int, session_device_id: int,
    started_at: str, cycle_device_revision: int,
) -> dict:
    return http_request(
        "POST",
        f"{api_base}/api/v1/multicamera/sessions/{session_uuid}/cycles/{cycle_id}/devices/{session_device_id}/confirm-start",
        token=token,
        body={
            "started_at": started_at,
            "cycle_device_revision": cycle_device_revision,
        },
    )


def confirm_device_stop(
    api_base: str, token: str, session_uuid: str,
    cycle_id: int, session_device_id: int,
    stopped_at: str, cycle_device_revision: int,
) -> dict:
    return http_request(
        "POST",
        f"{api_base}/api/v1/multicamera/sessions/{session_uuid}/cycles/{cycle_id}/devices/{session_device_id}/confirm-stop",
        token=token,
        body={
            "stopped_at": stopped_at,
            "cycle_device_revision": cycle_device_revision,
        },
    )


def get_server_time_iso(api_base: str) -> str:
    resp = http_request("GET", f"{api_base}/api/v1/system/time")
    return resp.get("server_time_utc", "")


def device_recording_status(cycle: dict, session_device_id: int) -> str | None:
    for cd in cycle.get("cycle_devices", []):
        if cd.get("session_device_id") == session_device_id:
            return cd.get("recording_status")
    return None


def latest_cycle(cycles: list[dict]) -> dict | None:
    if not cycles:
        return None
    return max(cycles, key=lambda c: c["cycle_index"])


def poll_until(description: str, timeout_s: float, fn: Callable[[], Any]) -> Any:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(POLL_INTERVAL_SECONDS)
    raise ValidationError(f"Timeout waiting for: {description}")


# ── Device file copy ──────────────────────────────────────────────────────────

def copy_from_device(udid: str, device_path: str, local_path: str) -> bool:
    """Copy a file from an iOS device to the local filesystem via devicectl."""
    result = subprocess.run(
        ["xcrun", "devicectl", "device", "copy", "from", "--device", udid,
         "--source", device_path, "--destination", local_path],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode == 0:
        print(f"  -> copied from device: {device_path} → {local_path}")
        return True
    print(f"  -> copy failed: {result.stderr.strip()[:200]}")
    return False


LFA_BUNDLE_ID = "com.lovas-zoltan.lfa-education-center"


def copy_app_container_file(udid: str, relative_path: str, local_path: str,
                             bundle_id: str = LFA_BUNDLE_ID) -> bool:
    """Copy a file from the app's Documents directory via devicectl's
    appDataContainer domain. Unlike copy_from_device, this needs no absolute
    on-device path discovered from a console log line — only the bundle ID
    and a path relative to the app's data container, which is fixed and
    known in advance. Use for diagnostic files the app writes proactively
    (e.g. gopro_diag.json) where console log capture cannot be relied on.
    """
    result = subprocess.run(
        ["xcrun", "devicectl", "device", "copy", "from", "--device", udid,
         "--domain-type", "appDataContainer", "--domain-identifier", bundle_id,
         "--source", relative_path, "--destination", local_path],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode == 0:
        print(f"  -> copied app container file: {relative_path} → {local_path}")
        return True
    print(f"  -> app container copy failed: {result.stderr.strip()[:300]}")
    return False


def extract_capture_path_from_log(console_log: str, tag: str = "[CAPTURE-INFO]") -> str | None:
    """Parse outputFile= from console log CAPTURE-INFO line."""
    for line in console_log.splitlines():
        if tag in line and "outputFile=" in line:
            # [CAPTURE-INFO] state=... outputFile=/path/to/file.mov size=12345
            parts = line.split("outputFile=")
            if len(parts) > 1:
                path = parts[1].split(" ")[0].strip()
                if path and path != "nil":
                    return path
    return None


def extract_skeleton_path_from_log(console_log: str) -> str | None:
    """Parse skeleton output file path from SKELETON-RESULT log line."""
    for line in console_log.splitlines():
        if "[SKELETON-RESULT]" in line and "file=" in line:
            parts = line.split("file=")
            if len(parts) > 1:
                return parts[1].split(" ")[0].strip()
    return None


# ── Deep links ───────────────────────────────────────────────────────────────

def _open_url_cmd(udid: str, url: str) -> list[str]:
    # Correct syntax for devicectl ≥ 629 (Xcode 15+):
    #   xcrun devicectl device process openURL <url> --device <udid>
    # NOT: device send url --device <udid> <url>  (old/nonexistent form)
    return ["xcrun", "devicectl", "device", "process", "openURL", url, "--device", udid]


_URL_SCHEME_NOT_FOUND_CODE = "10007"
_BUNDLE_ID = "com.lovas-zoltan.lfa-education-center"


def preflight_url_scheme(udid: str, label: str) -> None:
    """Verify that the lfa-mc1:// URL scheme is registered on the device.

    Fails fast with a clear install instruction if the installed build predates
    the scheme registration (PR #353 / main HEAD 4c8d3632).
    """
    print(f"[preflight] Checking lfa-mc1:// scheme on {label} ({udid[:8]}...)...")
    result = subprocess.run(
        _open_url_cmd(udid, "lfa-mc1://automate?action=noop"),
        capture_output=True, text=True,
    )
    if result.returncode != 0 and _URL_SCHEME_NOT_FOUND_CODE in result.stderr:
        raise ValidationError(
            f"\n"
            f"  lfa-mc1:// scheme NOT registered on {label} ({udid}).\n"
            f"  The installed build predates PR #353 (main 4c8d3632) which added\n"
            f"  the URL scheme to Info.plist.\n"
            f"\n"
            f"  ACTION REQUIRED — install a fresh DEBUG build:\n"
            f"    1. In Xcode: scheme 'LFAEducationCenter' → target '{label}' → ⌘R\n"
            f"    2. Wait for install + launch on device\n"
            f"    3. Repeat for the other device\n"
            f"    4. Re-run: ./scripts/run_mc1_regression.sh --scenario all\n"
        )
    # Any other non-zero return (e.g. app not running yet) is acceptable here —
    # what matters is the scheme IS registered (error is not 10007).
    print(f"[preflight]   {label}: lfa-mc1:// scheme OK")


def send_deep_link(udid: str, action: str, **params: str) -> None:
    query = urlencode({"action": action, **params})
    url = f"lfa-mc1://automate?{query}"
    result = subprocess.run(
        _open_url_cmd(udid, url),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ValidationError(f"devicectl process openURL failed for {udid} ({url}): {result.stderr.strip()}")
    print(f"  -> deep link sent to {udid}: {url}")


# ── Artifact directory ───────────────────────────────────────────────────────

class ArtifactRun:
    """One timestamped directory holding everything a regression run produces.

    `run_dir` is the fully-resolved final directory (the caller — normally
    run_mc1_regression.sh — picks the timestamp/name and creates it before
    starting console capture, so console logs already exist by the time this
    class touches them).
    """

    def __init__(self, run_dir: Path):
        self.dir = run_dir
        self.console_dir = self.dir / "console"
        self.backend_state_dir = self.dir / "backend_state"
        self.snapshots_dir = self.dir / "debug_snapshots"
        for d in (self.dir, self.console_dir, self.backend_state_dir, self.snapshots_dir):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def ipad_console_log(self) -> Path:
        return self.console_dir / "ipad_console.log"

    @property
    def iphone_console_log(self) -> Path:
        return self.console_dir / "iphone_console.log"

    def write_json(self, relative_path: str, data: Any) -> None:
        path = self.dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, default=str))

    def write_text(self, relative_path: str, text: str) -> None:
        path = self.dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def dump_backend_state(self, scenario_name: str, api_base: str, token: str, session_uuid: str) -> None:
        session = get_session(api_base, token, session_uuid)
        cycles = list_cycles(api_base, token, session_uuid)
        self.write_json(f"backend_state/{scenario_name}_session.json", session)
        self.write_json(f"backend_state/{scenario_name}_cycles.json", cycles)


class ConsoleOffsetTracker:
    """Tracks per-scenario byte offsets into the shared console log files so
    snapshot extraction can be scoped to a single scenario's run window even
    though both device console captures run for the whole regression session."""

    def __init__(self, artifact: ArtifactRun):
        self.artifact = artifact
        self._ipad_offset = self._size(artifact.ipad_console_log)
        self._iphone_offset = self._size(artifact.iphone_console_log)

    @staticmethod
    def _size(path: Path) -> int:
        return path.stat().st_size if path.exists() else 0

    def mark_scenario_start(self) -> None:
        self._ipad_offset = self._size(self.artifact.ipad_console_log)
        self._iphone_offset = self._size(self.artifact.iphone_console_log)

    def extract_snapshots(self, scenario_name: str) -> None:
        for label, path, offset in (
            ("ipad", self.artifact.ipad_console_log, self._ipad_offset),
            ("iphone", self.artifact.iphone_console_log, self._iphone_offset),
        ):
            if not path.exists():
                continue
            data = path.read_bytes()[offset:]
            text = data.decode("utf-8", errors="replace")
            blocks = [m.strip() for m in SNAPSHOT_RE.findall(text)]
            for i, block in enumerate(blocks):
                self.artifact.write_text(
                    f"debug_snapshots/{scenario_name}_{label}_{i:02d}.txt", block
                )

    def extract_tagged_lines(self, scenario_name: str, tag: str) -> list[str]:
        """Pulls e.g. [CCO]/[PCO] lines seen since mark_scenario_start(), per device."""
        lines: list[str] = []
        for path, offset in (
            (self.artifact.ipad_console_log, self._ipad_offset),
            (self.artifact.iphone_console_log, self._iphone_offset),
        ):
            if not path.exists():
                continue
            data = path.read_bytes()[offset:]
            text = data.decode("utf-8", errors="replace")
            lines.extend(line.strip() for line in text.splitlines() if tag in line)
        return lines


@dataclass
class ScenarioContext:
    api_base: str
    ipad_udid: str
    iphone_udid: str
    instructor_token: str
    player_token: str
    artifact: ArtifactRun
    offsets: ConsoleOffsetTracker
    cycles: int = 3
    ipad_role: str = "player"
    iphone_role: str = "instructor"


@dataclass
class ScenarioReport:
    name: str
    passed: bool
    session_uuid: str | None = None
    steps: list[dict] = field(default_factory=list)
    error: str | None = None

    def step(self, description: str, ok: bool, **details: Any) -> None:
        self.steps.append({"description": description, "ok": ok, **details})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
