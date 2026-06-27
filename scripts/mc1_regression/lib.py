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


# ── Deep links ───────────────────────────────────────────────────────────────

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
