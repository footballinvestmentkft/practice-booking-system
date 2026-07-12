"""Dual-console hard precondition tests (2026-07-04 tricamera RCA).

The first tricamera physical run failed on the PLAYER (iPad) side while the
shell wrapper had silently SKIPPED the iPad console capture with a warning —
the exact evidence the failure needed was never collected. These tests pin
the runner-side enforcement: tricamera scenarios must refuse to run unless
BOTH device console log files exist (created by run_mc1_regression.sh before
the runner starts).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from mc1_regression.lib import (  # noqa: E402
    DUAL_CONSOLE_REQUIRED_SCENARIOS,
    check_dual_console_precondition,
)

TRICAMERA = "tricamera-capture-skeleton-proof"


def _console_dir(tmp_path: Path, *files: str) -> Path:
    d = tmp_path / "console"
    d.mkdir()
    for f in files:
        (d / f).write_text("")
    return d


def test_tricamera_proof_is_dual_console_required():
    assert TRICAMERA in DUAL_CONSOLE_REQUIRED_SCENARIOS
    assert "gopro-tricamera-smoke" in DUAL_CONSOLE_REQUIRED_SCENARIOS


def test_both_consoles_present_passes(tmp_path):
    d = _console_dir(tmp_path, "iphone_console.log", "ipad_console.log")
    assert check_dual_console_precondition([TRICAMERA], d) == []


def test_missing_ipad_console_fails(tmp_path):
    d = _console_dir(tmp_path, "iphone_console.log")
    errors = check_dual_console_precondition([TRICAMERA], d)
    assert len(errors) == 1
    assert "iPad" in errors[0]
    assert "ipad_console.log" in errors[0]
    assert TRICAMERA in errors[0]


def test_missing_both_consoles_reports_both(tmp_path):
    d = _console_dir(tmp_path)
    errors = check_dual_console_precondition([TRICAMERA], d)
    assert len(errors) == 2
    assert any("iPhone" in e for e in errors)
    assert any("iPad" in e for e in errors)


def test_non_tricamera_scenario_is_exempt(tmp_path):
    d = _console_dir(tmp_path)  # no console files at all
    assert check_dual_console_precondition(["smoke"], d) == []
    assert check_dual_console_precondition(["all"], d) == []


def test_scenario_list_with_mixed_names_still_enforces(tmp_path):
    d = _console_dir(tmp_path, "iphone_console.log")
    errors = check_dual_console_precondition(["smoke", TRICAMERA], d)
    assert len(errors) == 1 and "iPad" in errors[0]
