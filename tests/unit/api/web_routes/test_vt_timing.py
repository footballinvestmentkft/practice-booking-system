"""Timing refinement + late-click bugfix tests — Phase 2.3b.

TIM-01   GNG timeout handler sets clickHandled=true BEFORE windowExpiredAt
TIM-02   GNG LATE_WINDOW_MS is 350 (was 600)
TIM-03   CR  LATE_WINDOW_MS is 350 (was 600)
TIM-04   CR  Jinja fallback for miss_penalty_ms is 500 (was 300)
TIM-05   CR  Jinja fallback for wrong_penalty_ms is 300 (was 200)
TIM-06   GNG late-click guard: lateBy > LATE_WINDOW_MS blocks capture
TIM-07   CR  late-click guard: lateBy > LATE_WINDOW_MS blocks capture
TIM-08   GNG lateGoCount increments on expired GO stimulus
TIM-09   GNG lateNoGoCount increments on expired NO-GO stimulus
TIM-10   GNG clickHandled guard in late listener: !clickHandled → return
TIM-11   GNG timeout handler: windowExpiredAt assigned after clickHandled=true
TIM-12   CR  miss ISI buffer: miss_penalty_ms (500) > LATE_WINDOW_MS (350) → 150ms buffer
TIM-13   CR  wrong ISI buffer: wrong_penalty_ms (300) > LATE_WINDOW_MS (350)? (wrong has no late window)
TIM-14   Late-click raw_metrics v2 extraction: late_go_count via VTSignalExtractor
TIM-15   Late-click raw_metrics v2 extraction: late_no_go_count via VTSignalExtractor
TIM-16   v1 payload → late rates still 0.0 (backward compat unchanged)
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.virtual_training_metrics import VTSignalExtractor

_TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"
_GNG_TEMPLATE  = _TEMPLATES_DIR / "virtual_training_go_no_go.html"
_CR_TEMPLATE   = _TEMPLATES_DIR / "virtual_training_color_reaction.html"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _gng_src() -> str:
    return _GNG_TEMPLATE.read_text()


def _cr_src() -> str:
    return _CR_TEMPLATE.read_text()


# ── TIM-01..11: Template structural / JS correctness ──────────────────────────

class TestGNGTimeoutHandler:
    """TIM-01, TIM-10, TIM-11: GNG stimTimeout callback correctness."""

    def test_tim01_clickhandled_set_before_window_expired(self):
        """TIM-01: GNG timeout handler sets clickHandled=true BEFORE windowExpiredAt."""
        src = _gng_src()
        # Locate the stimTimeout block
        block_start = src.find("stimTimeout = setTimeout(function ()")
        assert block_start != -1, "stimTimeout block not found in GNG template"
        block = src[block_start:]
        # Both assignments must be present
        ch_pos = block.find("clickHandled    = true")
        we_pos = block.find("windowExpiredAt = performance.now()")
        assert ch_pos != -1, (
            "clickHandled = true missing from GNG stimTimeout — BUG-1 fix not applied"
        )
        assert we_pos != -1, "windowExpiredAt = performance.now() missing from GNG stimTimeout"
        assert ch_pos < we_pos, (
            "clickHandled=true must precede windowExpiredAt assignment in stimTimeout"
        )

    def test_tim10_late_listener_guard_requires_clickhandled(self):
        """TIM-10: GNG late-click listener returns early when !clickHandled."""
        src = _gng_src()
        # Guard line must be present exactly as implemented
        assert "if (!clickHandled || lateHandled" in src, (
            "GNG late-click guard '!clickHandled || lateHandled' not found"
        )

    def test_tim11_window_expired_follows_clickhandled_in_timeout(self):
        """TIM-11: In GNG stimTimeout, clickHandled=true immediately precedes windowExpiredAt."""
        src = _gng_src()
        block_start = src.find("stimTimeout = setTimeout(function ()")
        block = src[block_start:]
        # Verify adjacency: no other assignment between the two lines
        ch_idx = block.find("clickHandled    = true")
        we_idx = block.find("windowExpiredAt = performance.now()")
        between = block[ch_idx:we_idx]
        # Only whitespace/newline between them (no intervening assignment)
        lines_between = [
            ln.strip() for ln in between.splitlines()
            if ln.strip() and ln.strip() != "clickHandled    = true;"
        ]
        assert lines_between == [], (
            f"Unexpected code between clickHandled=true and windowExpiredAt: {lines_between}"
        )


class TestLateWindowValues:
    """TIM-02, TIM-03: LATE_WINDOW_MS reduced to 350 in both templates."""

    def test_tim02_gng_late_window_is_350(self):
        """TIM-02: GNG LATE_WINDOW_MS = 350 (reduced from 600)."""
        src = _gng_src()
        assert "LATE_WINDOW_MS  = 350" in src, "GNG LATE_WINDOW_MS not set to 350"
        assert "LATE_WINDOW_MS  = 600" not in src, "GNG still has old LATE_WINDOW_MS = 600"

    def test_tim03_cr_late_window_is_350(self):
        """TIM-03: CR LATE_WINDOW_MS = 350 (reduced from 600)."""
        src = _cr_src()
        assert "LATE_WINDOW_MS  = 350" in src, "CR LATE_WINDOW_MS not set to 350"
        assert "LATE_WINDOW_MS  = 600" not in src, "CR still has old LATE_WINDOW_MS = 600"


class TestCRTimingFallbacks:
    """TIM-04, TIM-05, TIM-12: CR Jinja fallbacks and buffer invariant."""

    def test_tim04_miss_penalty_fallback_is_500(self):
        """TIM-04: CR Jinja fallback for miss_penalty_ms is 500 (was 300)."""
        src = _cr_src()
        match = re.search(r"game\.config\.miss_penalty_ms\s+or\s+(\d+)", src)
        assert match is not None, "Jinja miss_penalty_ms fallback not found in CR template"
        assert int(match.group(1)) == 500, (
            f"miss_penalty_ms fallback should be 500, got {match.group(1)}"
        )

    def test_tim05_wrong_penalty_fallback_is_300(self):
        """TIM-05: CR Jinja fallback for wrong_penalty_ms is 300 (was 200)."""
        src = _cr_src()
        match = re.search(r"game\.config\.wrong_penalty_ms\s+or\s+(\d+)", src)
        assert match is not None, "Jinja wrong_penalty_ms fallback not found in CR template"
        assert int(match.group(1)) == 300, (
            f"wrong_penalty_ms fallback should be 300, got {match.group(1)}"
        )

    def test_tim12_cr_miss_buffer_invariant(self):
        """TIM-12: miss_penalty_ms fallback (500) > LATE_WINDOW_MS (350) → 150ms buffer."""
        src = _cr_src()
        lw_match = re.search(r"LATE_WINDOW_MS\s+=\s+(\d+)", src)
        mp_match = re.search(r"game\.config\.miss_penalty_ms\s+or\s+(\d+)", src)
        assert lw_match and mp_match
        late_window   = int(lw_match.group(1))
        miss_penalty  = int(mp_match.group(1))
        buffer = miss_penalty - late_window
        assert buffer >= 100, (
            f"CR miss buffer too small: miss_penalty({miss_penalty}) - "
            f"LATE_WINDOW({late_window}) = {buffer}ms (need ≥100ms)"
        )


class TestLateClickGuards:
    """TIM-06, TIM-07, TIM-08, TIM-09: guard expressions in both templates."""

    def test_tim06_gng_guard_blocks_after_late_window(self):
        """TIM-06: GNG late-click listener rejects lateBy > LATE_WINDOW_MS."""
        src = _gng_src()
        assert "lateBy > LATE_WINDOW_MS" in src, (
            "GNG missing 'lateBy > LATE_WINDOW_MS' guard"
        )

    def test_tim07_cr_guard_blocks_after_late_window(self):
        """TIM-07: CR late-click listener rejects lateBy > LATE_WINDOW_MS."""
        src = _cr_src()
        assert "lateBy > LATE_WINDOW_MS" in src, (
            "CR missing 'lateBy > LATE_WINDOW_MS' guard"
        )

    def test_tim08_gng_late_go_count_increments_on_go_expired(self):
        """TIM-08: GNG late-click listener increments lateGoCount for expired GO."""
        src = _gng_src()
        assert "lateGoCount++" in src, "lateGoCount++ not found in GNG late-click listener"
        assert 'expiredStimType === "go"' in src, (
            'expiredStimType === "go" check missing — late GO branch not guarded'
        )

    def test_tim09_gng_late_nogo_count_increments_on_nogo_expired(self):
        """TIM-09: GNG late-click listener increments lateNoGoCount for expired NO-GO."""
        src = _gng_src()
        assert "lateNoGoCount++" in src, "lateNoGoCount++ not found in GNG late-click listener"


# ── TIM-14..16: Backend VTSignalExtractor processes late counts correctly ──────

_GNG_PHASE_CONFIG = [
    {"go": 10, "no_go": 5, "isi_ms": 900, "window_ms": 1000, "stimulus_ms": 800},
    {"go": 11, "no_go": 4, "isi_ms": 650, "window_ms": 1000, "stimulus_ms": 800},
]


class TestLateClickExtraction:
    """TIM-14..16: VTSignalExtractor late_go_rate / late_nogo_rate correctness."""

    def _payload(
        self,
        stimuli: int = 30,
        correct: int = 25,
        wrong: int = 2,
        errors: int = 3,
        late_go: int = 0,
        late_nogo: int = 0,
        v: int = 2,
    ) -> dict:
        """Build a complete submit payload with raw_metrics at the requested version."""
        raw: dict = {
            "v": v,
            "per_stimulus": [],
            "per_phase": [],
        }
        if v >= 2:
            raw["late_summary"] = {
                "late_click_count": late_go + late_nogo,
                "late_click_avg_ms": 120,
                "late_click_max_ms": 200,
                "late_go_count":    late_go,
                "late_no_go_count": late_nogo,
            }
        return {
            "stimuli_count":     stimuli,
            "correct_count":     correct,
            "wrong_click_count": wrong,
            "error_count":       errors,
            "avg_reaction_ms":   320,
            "raw_metrics":       raw,
        }

    def test_tim14_late_go_count_extracted_to_rate(self):
        """TIM-14: late_go_count=3 out of 30 stimuli → late_go_rate=0.1."""
        data = self._payload(stimuli=30, late_go=3, late_nogo=0)
        signals = VTSignalExtractor.extract(data, _GNG_PHASE_CONFIG)
        assert abs(signals.late_go_rate - 3 / 30) < 1e-9, (
            f"late_go_rate expected {3/30:.4f}, got {signals.late_go_rate:.4f}"
        )

    def test_tim15_late_nogo_count_extracted_to_rate(self):
        """TIM-15: late_no_go_count=2 out of 30 stimuli → late_nogo_rate ≈ 0.0667."""
        data = self._payload(stimuli=30, late_go=0, late_nogo=2)
        signals = VTSignalExtractor.extract(data, _GNG_PHASE_CONFIG)
        assert abs(signals.late_nogo_rate - 2 / 30) < 1e-9, (
            f"late_nogo_rate expected {2/30:.4f}, got {signals.late_nogo_rate:.4f}"
        )

    def test_tim16_v1_payload_late_rates_zero(self):
        """TIM-16: v1 raw_metrics → late_go_rate and late_nogo_rate default to 0.0."""
        data = self._payload(stimuli=30, v=1)
        signals = VTSignalExtractor.extract(data, _GNG_PHASE_CONFIG)
        assert signals.late_go_rate    == 0.0, "v1 payload must not set late_go_rate"
        assert signals.late_nogo_rate  == 0.0, "v1 payload must not set late_nogo_rate"
        assert signals.late_click_rate == 0.0, "v1 payload must not set late_click_rate"
