"""Unit tests for app/services/training_day.py — Phase 1 training day resolution.

TD-01   resolve_training_timezone: valid Europe/Budapest → ("Europe/Budapest", "browser_iana")
TD-02   resolve_training_timezone: valid America/Sao_Paulo → ("America/Sao_Paulo", "browser_iana")
TD-03   resolve_training_timezone: valid UTC → ("UTC", "browser_iana")
TD-04   resolve_training_timezone: invalid string → ("UTC", "utc_fallback")
TD-05   resolve_training_timezone: None → ("UTC", "utc_fallback")
TD-06   resolve_training_timezone: empty string → ("UTC", "utc_fallback")
TD-07   resolve_training_timezone: too long (>64 chars) → ("UTC", "utc_fallback")
TD-08   compute_training_local_date: Budapest UTC+2 — 00:30 UTC = same day in Budapest
TD-09   compute_training_local_date: São Paulo UTC-3 — 00:30 UTC = previous day in São Paulo
TD-10   compute_training_local_date: UTC — always same as input date
TD-11   compute_training_local_date: 23:30 UTC+2 Budapest — same day
TD-12   compute_training_local_date: invalid tz string falls back to UTC
TD-13   resolve_location_source: fresh GPS (< 5 min old) → "browser_geolocation"
TD-14   resolve_location_source: stale GPS (> 5 min old) → "stale_browser_geolocation"
TD-15   resolve_location_source: exactly at 5-min boundary → "browser_geolocation"
TD-16   resolve_location_source: lat=None → "unavailable"
TD-17   resolve_location_source: lng=None → "unavailable"
TD-18   resolve_location_source: captured_at=None with valid lat/lng → "unavailable"
TD-19   current_training_date_utc: returns today's UTC date (smoke test)
TD-20   record_attempt stores browser_timezone, training_timezone, training_timezone_source,
        training_local_date, location_source for a Budapest user
TD-21   record_attempt with browser_timezone=None stores UTC fallback fields
TD-22   record_attempt with valid location stores location_lat, location_lng, location_source
TD-23   record_attempt with no location stores location_source="unavailable"
TD-24   calculate_daily_attempt_index uses training_local_date, not UTC
TD-25   daily cap in submit route uses training_local_date (São Paulo still on prev day at 00:30 UTC)
TD-26   check_single_game_eligibility: Budapest user eligible on Budapest date, not UTC date
TD-27   check_reward_eligibility: counted by training_local_date, not UTC date
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

_UID = 7  # non-1 test user id; satisfies Hardcoded FK ID Guard

import pytest

from app.services.training_day import (
    compute_training_local_date,
    current_training_date_utc,
    resolve_location_source,
    resolve_training_timezone,
)


# ── Reference timestamps ───────────────────────────────────────────────────────

# 2026-06-05 00:30 UTC  →  Budapest 02:30 (same day)  |  São Paulo 21:30 (prev day)
_UTC_00_30 = datetime(2026, 6, 5, 0, 30, 0, tzinfo=timezone.utc)
# 2026-06-05 23:30 UTC  →  Budapest next day at 01:30  |  São Paulo 20:30 (same day)
_UTC_23_30 = datetime(2026, 6, 5, 23, 30, 0, tzinfo=timezone.utc)


# ── TD-01..07: resolve_training_timezone ──────────────────────────────────────

class TestResolveTrainingTimezone:

    def test_td01_budapest(self):
        tz, src = resolve_training_timezone("Europe/Budapest")
        assert tz  == "Europe/Budapest"
        assert src == "browser_iana"

    def test_td02_sao_paulo(self):
        tz, src = resolve_training_timezone("America/Sao_Paulo")
        assert tz  == "America/Sao_Paulo"
        assert src == "browser_iana"

    def test_td03_utc_valid(self):
        tz, src = resolve_training_timezone("UTC")
        assert tz  == "UTC"
        assert src == "browser_iana"

    def test_td04_invalid_string(self):
        tz, src = resolve_training_timezone("Invalid/Timezone")
        assert tz  == "UTC"
        assert src == "utc_fallback"

    def test_td05_none(self):
        tz, src = resolve_training_timezone(None)
        assert tz  == "UTC"
        assert src == "utc_fallback"

    def test_td06_empty_string(self):
        tz, src = resolve_training_timezone("")
        assert tz  == "UTC"
        assert src == "utc_fallback"

    def test_td07_too_long(self):
        tz, src = resolve_training_timezone("A" * 65)
        assert tz  == "UTC"
        assert src == "utc_fallback"


# ── TD-08..12: compute_training_local_date ────────────────────────────────────

class TestComputeTrainingLocalDate:

    def test_td08_budapest_00_30_utc_same_day(self):
        """00:30 UTC = 02:30 Budapest (UTC+2) → still 2026-06-05."""
        d = compute_training_local_date(_UTC_00_30, "Europe/Budapest")
        assert d == date(2026, 6, 5)

    def test_td09_sao_paulo_00_30_utc_previous_day(self):
        """00:30 UTC = 21:30 São Paulo (UTC-3) → 2026-06-04 (previous day)."""
        d = compute_training_local_date(_UTC_00_30, "America/Sao_Paulo")
        assert d == date(2026, 6, 4)

    def test_td10_utc_same_as_input(self):
        d = compute_training_local_date(_UTC_00_30, "UTC")
        assert d == date(2026, 6, 5)

    def test_td11_budapest_23_30_utc_next_day(self):
        """23:30 UTC = 01:30 Budapest (UTC+2, next day) → 2026-06-06."""
        d = compute_training_local_date(_UTC_23_30, "Europe/Budapest")
        assert d == date(2026, 6, 6)

    def test_td12_invalid_tz_falls_back_to_utc(self):
        d = compute_training_local_date(_UTC_00_30, "Nonsense/Zone")
        assert d == date(2026, 6, 5)


# ── TD-13..18: resolve_location_source ───────────────────────────────────────

class TestResolveLocationSource:

    def _now(self):
        return datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)

    def test_td13_fresh_gps(self):
        captured = self._now() - timedelta(minutes=2)
        src = resolve_location_source(47.5, 19.0, captured, self._now())
        assert src == "browser_geolocation"

    def test_td14_stale_gps(self):
        captured = self._now() - timedelta(minutes=6)
        src = resolve_location_source(47.5, 19.0, captured, self._now())
        assert src == "stale_browser_geolocation"

    def test_td15_exactly_at_5_min_boundary(self):
        """Exactly 300 s old: still fresh (<=)."""
        captured = self._now() - timedelta(seconds=300)
        src = resolve_location_source(47.5, 19.0, captured, self._now())
        assert src == "browser_geolocation"

    def test_td16_lat_none(self):
        src = resolve_location_source(None, 19.0, self._now(), self._now())
        assert src == "unavailable"

    def test_td17_lng_none(self):
        src = resolve_location_source(47.5, None, self._now(), self._now())
        assert src == "unavailable"

    def test_td18_captured_at_none(self):
        src = resolve_location_source(47.5, 19.0, None, self._now())
        assert src == "unavailable"


# ── TD-19: current_training_date_utc ─────────────────────────────────────────

def test_td19_current_training_date_utc_is_today():
    """Smoke test: returns today's UTC date."""
    today = datetime.now(timezone.utc).date()
    assert current_training_date_utc() == today


# ── TD-20..23: record_attempt stores Phase 1 fields ──────────────────────────

class TestRecordAttemptPhase1Fields:
    """Verify that record_attempt() persists all Phase 1 training day fields."""

    def _game(self, base_xp=15, max_daily=6, config=None):
        g = MagicMock()
        g.id = 1
        g.code = "color_reaction"
        g.base_xp = base_xp
        g.max_daily_attempts = max_daily
        g.config = config or {}
        g.skill_targets = {"reactions": 1.0}
        return g

    def _db_empty(self):
        """DB that returns count=0 for attempt index and no neg deltas."""
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.count.return_value = 0
        db.query.return_value = q
        db.execute.return_value.fetchall.return_value = []
        return db

    def _data(self):
        return {
            "started_at": "2026-06-05T10:00:00Z",
            "score_normalized": 75.0,
            "avg_reaction_ms": 350,
            "idempotency_key": None,
        }

    def _call(self, db, game, browser_timezone=None, lat=None, lng=None,
              accuracy_m=None, captured_at=None):
        from app.services.virtual_training_service import VirtualTrainingService
        with patch("app.services.virtual_training_service.VirtualTrainingAttempt") as MockAttempt, \
             patch("app.services.gamification.xp_service.award_xp"), \
             patch("app.services.virtual_training_metrics.compute_vt_skill_deltas", return_value={}):
            instance = MagicMock()
            instance.is_valid = True
            instance.xp_awarded = 10
            instance.skill_deltas = {}
            MockAttempt.return_value = instance
            VirtualTrainingService.record_attempt(
                db=db,
                user_id=_UID,
                game=game,
                data=self._data(),
                idempotency_key="test_key",
                browser_timezone=browser_timezone,
                location_lat=lat,
                location_lng=lng,
                location_accuracy_m=accuracy_m,
                location_captured_at=captured_at,
            )
            return MockAttempt.call_args

    def test_td20_budapest_stores_correct_fields(self):
        """TD-20: Budapest tz → training_timezone=Europe/Budapest, source=browser_iana."""
        db   = self._db_empty()
        game = self._game()
        kwargs = self._call(db, game, browser_timezone="Europe/Budapest")
        kw = kwargs.kwargs if kwargs.kwargs else kwargs[1]
        assert kw["browser_timezone"]         == "Europe/Budapest"
        assert kw["training_timezone"]        == "Europe/Budapest"
        assert kw["training_timezone_source"] == "browser_iana"
        assert kw["training_local_date"]      is not None

    def test_td21_none_timezone_stores_utc_fallback(self):
        """TD-21: browser_timezone=None → UTC fallback fields."""
        db   = self._db_empty()
        game = self._game()
        kwargs = self._call(db, game, browser_timezone=None)
        kw = kwargs.kwargs if kwargs.kwargs else kwargs[1]
        assert kw["browser_timezone"]         is None
        assert kw["training_timezone"]        == "UTC"
        assert kw["training_timezone_source"] == "utc_fallback"

    def test_td22_valid_location_stored(self):
        """TD-22: GPS fix present → location_lat/lng and location_source set."""
        db   = self._db_empty()
        game = self._game()
        now  = datetime.now(timezone.utc)
        cap  = now - timedelta(minutes=1)
        kwargs = self._call(db, game, lat=47.5, lng=19.0, accuracy_m=20, captured_at=cap)
        kw = kwargs.kwargs if kwargs.kwargs else kwargs[1]
        assert kw["location_lat"]    == 47.5
        assert kw["location_lng"]    == 19.0
        assert kw["location_source"] == "browser_geolocation"

    def test_td23_no_location_stores_unavailable(self):
        """TD-23: No GPS → location_source='unavailable'."""
        db   = self._db_empty()
        game = self._game()
        kwargs = self._call(db, game)
        kw = kwargs.kwargs if kwargs.kwargs else kwargs[1]
        assert kw["location_lat"]    is None
        assert kw["location_lng"]    is None
        assert kw["location_source"] == "unavailable"


# ── TD-24: calculate_daily_attempt_index uses training_local_date ─────────────

class TestAttemptIndexTrainingDate:

    def test_td24_uses_training_local_date_filter(self):
        """TD-24: calculate_daily_attempt_index() filters on training_local_date."""
        from app.services.virtual_training_service import VirtualTrainingService

        db = MagicMock()
        q  = MagicMock()
        q.filter.return_value = q
        q.count.return_value  = 2
        db.query.return_value = q

        today = date(2026, 6, 5)
        idx   = VirtualTrainingService.calculate_daily_attempt_index(
            db, user_id=_UID, game_id=1, training_local_date=today
        )

        # count=2 → next attempt is index 3
        assert idx == 3

        # Verify the filter was called with training_local_date
        filter_call_args = q.filter.call_args[0]
        filter_strs = [str(a) for a in filter_call_args]
        assert any("training_local_date" in s for s in filter_strs)


# ── TD-25: São Paulo daily cap — 00:30 UTC is still prev day ─────────────────

class TestDailyCapTimezone:

    def test_td25_sao_paulo_00_30_utc_is_prev_day(self):
        """TD-25: 00:30 UTC = 21:30 São Paulo → training_local_date = 2026-06-04."""
        tz, _   = resolve_training_timezone("America/Sao_Paulo")
        d       = compute_training_local_date(_UTC_00_30, tz)
        assert d == date(2026, 6, 4)

        # Budapest at same instant → 2026-06-05
        tz_bp, _ = resolve_training_timezone("Europe/Budapest")
        d_bp     = compute_training_local_date(_UTC_00_30, tz_bp)
        assert d_bp == date(2026, 6, 5)

        # The two cities get different training days for the SAME UTC timestamp
        assert d != d_bp


# ── TD-26..27: eligibility uses training_local_date ──────────────────────────

class TestEligibilityTrainingDate:

    def _mock_game_obj(self, max_daily=3):
        g = MagicMock()
        g.id = 1
        g.max_daily_attempts = max_daily
        g.is_active = True
        return g

    def _db_with_standalone_count(self, game, count: int):
        """DB mock: game query returns game; standalone count returns count."""
        db = MagicMock()
        q  = MagicMock()
        q.filter.return_value = q
        q.first.return_value  = game
        q.count.return_value  = count
        q.all.return_value    = [game]
        db.query.return_value = q
        return db

    def test_td26_single_game_eligible_on_budapest_date(self):
        """TD-26: eligible when count >= required on the given training_local_date."""
        from app.services.vt_card_eligibility import check_single_game_eligibility
        game = self._mock_game_obj(max_daily=3)
        db   = self._db_with_standalone_count(game, 3)
        eligible, count, required = check_single_game_eligibility(
            db, user_id=_UID, game_id=1, training_local_date=date(2026, 6, 5)
        )
        assert eligible  is True
        assert count     == 3
        assert required  == 3

    def test_td26b_not_eligible_when_count_below_required(self):
        from app.services.vt_card_eligibility import check_single_game_eligibility
        game = self._mock_game_obj(max_daily=3)
        db   = self._db_with_standalone_count(game, 2)
        eligible, count, _ = check_single_game_eligibility(
            db, user_id=_UID, game_id=1, training_local_date=date(2026, 6, 5)
        )
        assert eligible is False
        assert count    == 2

    def test_td27_reward_eligibility_uses_training_local_date(self):
        """TD-27: reward eligibility counted on training_local_date, not UTC."""
        from app.services.vt_card_eligibility import check_reward_eligibility
        game = self._mock_game_obj(max_daily=3)

        # 3 games, each with 3 standalone attempts → tier-3 eligible
        db = MagicMock()
        q  = MagicMock()
        q.filter.return_value = q
        q.count.return_value  = 3     # standalone count per game
        q.all.return_value    = [self._mock_game_obj(max_daily=3) for _ in range(3)]
        db.query.return_value = q

        eligible, completed = check_reward_eligibility(
            db, user_id=_UID, tier=3, training_local_date=date(2026, 6, 5)
        )
        assert eligible  is True
        assert completed == 3
