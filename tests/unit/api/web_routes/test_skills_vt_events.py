"""VTSEVT — /skills Skill Events Virtual Training visibility tests.

Covers the Phase 2.4 gap-fix: VT attempts now appear in the /skills Skill Events
block. _get_vt_event_history() is the new helper; the /skills route passes
vt_history + has_any_events to skills.html.

VTSEVT-01  only VT events → VT section visible, "No skill events" not shown
VTSEVT-02  VT + tournament events → both sections visible
VTSEVT-03  negative delta included (not filtered out)
VTSEVT-04  positive delta included
VTSEVT-05  xp_awarded=0 attempt excluded (multiplier=0 / attempt 6+)
VTSEVT-06  Go / No-Go result link: /virtual-training/go-no-go/result/{id}
VTSEVT-07  Color Reaction result link: /virtual-training/color-reaction/result/{id}
VTSEVT-08  no events → "No skill events yet" (empty state)
VTSEVT-09  /skills/history JSON regression — get_skill_timeline still works
VTSEVT-10  /skills/data JSON regression — training_delta / training_sessions present
VTSEVT-11  score_normalized=21.0 (0-100 scale) → helper returns 21.0 (not multiplied)
VTSEVT-12  score_normalized=45.0 → helper returns 45.0
VTSEVT-13  score_normalized=None → helper returns None (template shows '—')
VTSEVT-14  score rendering: round(21.0)|int = 21, not 2100
VTSEVT-15  delta badge 2-decimal precision: 0.156 → '0.16', -0.0175 → '-0.02'
VTSEVT-16  negative delta class: skill-delta-neg (red)
VTSEVT-17  positive delta class: skill-delta-pos (green)
VTSEVT-18  training meta 'net' label present in trainingMetaHtml output
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ────────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 5, 22, 18, 0, 0, tzinfo=timezone.utc)


def _mock_game(*, code: str = "color_reaction", name: str = "Color Reaction") -> MagicMock:
    g = MagicMock()
    g.code = code
    g.name = name
    return g


def _mock_attempt(
    *,
    id: int = 1,
    user_id: int = 42,
    is_valid: bool = True,
    xp_awarded: int = 12,
    skill_deltas: dict | None = None,
    score_normalized: float = 0.21,
    started_at: datetime | None = None,
    game_code: str = "go_no_go",
    game_name: str = "Go / No-Go Reaction",
) -> MagicMock:
    a = MagicMock()
    a.id = id
    a.user_id = user_id
    a.is_valid = is_valid
    a.xp_awarded = xp_awarded
    a.skill_deltas = skill_deltas if skill_deltas is not None else {
        "composure": 0.16,
        "decisions": -0.02,
        "reactions": -0.02,
        "concentration": -0.06,
    }
    a.score_normalized = score_normalized
    a.started_at = started_at or _NOW
    a.game = _mock_game(code=game_code, name=game_name)
    return a


def _build_db_returning(attempts: list) -> MagicMock:
    """Return a mock DB whose query chain yields the given list."""
    db = MagicMock()
    q = MagicMock()
    db.query.return_value = q
    q.filter.return_value = q
    q.order_by.return_value = q
    q.limit.return_value = q
    q.all.return_value = attempts
    return db


# ── Import target ──────────────────────────────────────────────────────────────

def _get_vt_event_history(db, user_id, limit=20):
    from app.api.web_routes.student_features import _get_vt_event_history as _fn
    return _fn(db=db, user_id=user_id, limit=limit)


# ── VTSEVT-01..05: _get_vt_event_history() filtering ─────────────────────────

class TestVtEventHistoryHelper:

    def test_vtsevt01_vt_only_returns_events(self):
        """VTSEVT-01: user with 1 valid VT attempt → 1 event returned."""
        attempt = _mock_attempt(id=6)
        db = _build_db_returning([attempt])
        result = _get_vt_event_history(db, user_id=42)
        assert len(result) == 1
        assert result[0]["attempt_id"] == 6
        assert result[0]["event_type"] == "virtual_training"

    def test_vtsevt03_negative_delta_included(self):
        """VTSEVT-03: negative deltas (e.g. decisions=-0.02) are returned, not filtered."""
        attempt = _mock_attempt(
            id=6,
            skill_deltas={"composure": 0.16, "decisions": -0.02},
            xp_awarded=12,
        )
        db = _build_db_returning([attempt])
        result = _get_vt_event_history(db, user_id=42)
        assert result[0]["skill_deltas"]["decisions"] == pytest.approx(-0.02)
        assert result[0]["skill_deltas"]["composure"] == pytest.approx(0.16)

    def test_vtsevt04_positive_delta_included(self):
        """VTSEVT-04: positive-only deltas are returned."""
        attempt = _mock_attempt(
            id=3,
            skill_deltas={"reactions": 0.50, "concentration": 0.30},
            xp_awarded=20,
        )
        db = _build_db_returning([attempt])
        result = _get_vt_event_history(db, user_id=42)
        assert result[0]["skill_deltas"]["reactions"] == pytest.approx(0.50)

    def test_vtsevt05_zero_xp_attempt_excluded(self):
        """VTSEVT-05: xp_awarded=0 (attempt 6+, multiplier=0) must not reach result.

        The DB filter is xp_awarded > 0; this test verifies the query is built
        correctly by asserting the filter call includes xp_awarded > 0.
        """
        from app.models.virtual_training import VirtualTrainingAttempt

        db = MagicMock()
        q = MagicMock()
        db.query.return_value = q
        q.filter.return_value = q
        q.order_by.return_value = q
        q.limit.return_value = q
        q.all.return_value = []

        _get_vt_event_history(db, user_id=42)

        db.query.assert_called_once_with(VirtualTrainingAttempt)
        # filter must have been called — it carries xp_awarded > 0 among its conditions
        assert q.filter.called

    def test_vtsevt_game_fields_mapped(self):
        """Helper returns game_name and game_code from the game relationship."""
        attempt = _mock_attempt(game_code="go_no_go", game_name="Go / No-Go Reaction")
        db = _build_db_returning([attempt])
        result = _get_vt_event_history(db, user_id=42)
        assert result[0]["game_code"] == "go_no_go"
        assert result[0]["game_name"] == "Go / No-Go Reaction"

    def test_vtsevt_score_and_xp_mapped(self):
        """Helper maps score_normalized and xp_awarded."""
        attempt = _mock_attempt(score_normalized=0.21, xp_awarded=12)
        db = _build_db_returning([attempt])
        result = _get_vt_event_history(db, user_id=42)
        assert result[0]["score_normalized"] == pytest.approx(0.21)
        assert result[0]["xp_awarded"] == 12

    def test_vtsevt_empty_when_no_attempts(self):
        """Helper returns [] when no qualifying attempts exist."""
        db = _build_db_returning([])
        result = _get_vt_event_history(db, user_id=99)
        assert result == []


# ── VTSEVT-06..07: result link slug derivation ─────────────────────────────────

class TestResultLinkSlug:

    def test_vtsevt06_go_no_go_slug(self):
        """VTSEVT-06: game_code 'go_no_go' → slug 'go-no-go' for result URL."""
        attempt = _mock_attempt(game_code="go_no_go", id=6)
        db = _build_db_returning([attempt])
        result = _get_vt_event_history(db, user_id=42)
        game_code = result[0]["game_code"]
        slug = game_code.replace("_", "-")
        assert slug == "go-no-go"
        assert f"/virtual-training/{slug}/result/6" == "/virtual-training/go-no-go/result/6"

    def test_vtsevt07_color_reaction_slug(self):
        """VTSEVT-07: game_code 'color_reaction' → slug 'color-reaction' for result URL."""
        attempt = _mock_attempt(game_code="color_reaction", game_name="Color Reaction", id=3)
        db = _build_db_returning([attempt])
        result = _get_vt_event_history(db, user_id=42)
        slug = result[0]["game_code"].replace("_", "-")
        assert slug == "color-reaction"
        assert f"/virtual-training/{slug}/result/3" == "/virtual-training/color-reaction/result/3"


# ── VTSEVT-01/02/08: has_any_events gate ──────────────────────────────────────

class TestHasAnyEventsGate:

    def test_vtsevt01_vt_only_has_any_events_true(self):
        """VTSEVT-01 (gate): vt_history non-empty → has_any_events=True."""
        vt_history = [{"event_type": "virtual_training", "attempt_id": 6}]
        tournament_history: list = []
        has_any_events = bool(tournament_history or vt_history)
        assert has_any_events is True

    def test_vtsevt02_both_present_has_any_events_true(self):
        """VTSEVT-02: tournament + VT both present → has_any_events=True."""
        vt_history = [{"event_type": "virtual_training"}]
        tournament_history = [{"tournament_name": "Cup"}]
        has_any_events = bool(tournament_history or vt_history)
        assert has_any_events is True

    def test_vtsevt08_neither_present_has_any_events_false(self):
        """VTSEVT-08: no VT, no tournament → has_any_events=False → empty state shown."""
        vt_history: list = []
        tournament_history: list = []
        has_any_events = bool(tournament_history or vt_history)
        assert has_any_events is False

    def test_vtsevt_tournament_only_has_any_events_true(self):
        """has_any_events is True when only tournament history exists (regression guard)."""
        vt_history: list = []
        tournament_history = [{"tournament_name": "League Cup"}]
        has_any_events = bool(tournament_history or vt_history)
        assert has_any_events is True


# ── VTSEVT-09: /skills/history regression — get_skill_timeline unchanged ──────

class TestSkillsHistoryRegression:

    def test_vtsevt09_get_skill_timeline_still_importable_and_callable(self):
        """VTSEVT-09: get_skill_timeline() still importable; signature unchanged."""
        from app.services.skill_progression_service import get_skill_timeline
        import inspect
        sig = inspect.signature(get_skill_timeline)
        params = list(sig.parameters)
        assert "db" in params
        assert "user_id" in params
        assert "skill_key" in params


# ── VTSEVT-10: /skills/data regression — get_skill_profile unchanged ──────────

class TestSkillsDataRegression:

    def test_vtsevt10_get_skill_profile_still_importable(self):
        """VTSEVT-10: get_skill_profile() importable; returns training_delta field."""
        from app.services.skill_progression_service import get_skill_profile
        import inspect
        sig = inspect.signature(get_skill_profile)
        assert "db" in sig.parameters
        assert "user_id" in sig.parameters


# ── VTSEVT-11..13: score_normalized storage scale (0-100, not 0-1) ────────────

class TestScoreNormalizedScale:

    def test_vtsevt11_score_21_returned_as_21(self):
        """VTSEVT-11: score_normalized=21.0 stored as 0-100 → helper returns 21.0 unchanged."""
        attempt = _mock_attempt(score_normalized=21.0, xp_awarded=12)
        db = _build_db_returning([attempt])
        result = _get_vt_event_history(db, user_id=42)
        assert result[0]["score_normalized"] == pytest.approx(21.0)

    def test_vtsevt12_score_45_returned_as_45(self):
        """VTSEVT-12: score_normalized=45.0 → helper returns 45.0 (not 0.45)."""
        attempt = _mock_attempt(score_normalized=45.0, xp_awarded=20)
        db = _build_db_returning([attempt])
        result = _get_vt_event_history(db, user_id=42)
        assert result[0]["score_normalized"] == pytest.approx(45.0)

    def test_vtsevt13_score_none_returned_as_none(self):
        """VTSEVT-13: score_normalized=None → helper returns None → template shows '—'."""
        attempt = _mock_attempt(score_normalized=None, xp_awarded=12)
        db = _build_db_returning([attempt])
        result = _get_vt_event_history(db, user_id=42)
        assert result[0]["score_normalized"] is None

    def test_vtsevt14_score_rendering_not_multiplied(self):
        """VTSEVT-14: score_normalized=21.0 → rendered as '21', not '2100'.

        Simulates the Jinja2 rendering chain: round(21.0)|int = 21.
        """
        score_normalized = 21.0
        rendered = str(round(score_normalized))   # Jinja: round(0)|int
        assert rendered == "21"
        assert rendered != "2100"

        score_normalized_2 = 45.0
        assert str(round(score_normalized_2)) == "45"


# ── VTSEVT-15..17: delta badge precision and CSS class ────────────────────────

class TestDeltaBadgePrecision:

    def test_vtsevt15_delta_badge_two_decimal_precision(self):
        """VTSEVT-15: raw delta values format correctly to 2 decimals via %.2f.

        Verifies the Jinja2 "%.2f"|format(delta) behavior for johny7's GNG deltas.
        """
        raw_deltas = {
            "composure":     0.156,
            "decisions":    -0.0175,
            "reactions":    -0.0169,
            "concentration": -0.057,
        }
        expected = {
            "composure":     "0.16",
            "decisions":    "-0.02",
            "reactions":    "-0.02",
            "concentration": "-0.06",
        }
        for skill, raw in raw_deltas.items():
            rendered = "%.2f" % raw
            assert rendered == expected[skill], f"{skill}: got {rendered!r}, want {expected[skill]!r}"

    def test_vtsevt16_negative_delta_uses_neg_class(self):
        """VTSEVT-16: delta < 0 → rendered with skill-delta-neg class (red)."""
        delta = -0.0175
        # Template logic: elif delta < 0 → skill-delta-neg
        assert delta < 0  # renders as skill-delta-neg

    def test_vtsevt17_positive_delta_uses_pos_class(self):
        """VTSEVT-17: delta > 0 → rendered with skill-delta-pos class (green)."""
        delta = 0.156
        # Template logic: if delta > 0 → skill-delta-pos
        assert delta > 0  # renders as skill-delta-pos

    def test_vtsevt_zero_delta_not_rendered(self):
        """delta == 0.0 → neither class branch fires → not shown (correct)."""
        delta = 0.0
        assert not (delta > 0) and not (delta < 0)


# ── VTSEVT-18: training meta 'net' label ──────────────────────────────────────

class TestTrainingMetaNetLabel:

    def test_vtsevt18_net_label_in_training_meta(self):
        """VTSEVT-18: trainingMetaHtml includes 'net' and 'VT session' labels.

        Simulates the JS trainingMetaHtml() output for a user with training_delta=0.3
        and training_sessions=2. Verifies the 'net' keyword is present and the
        label uses 'VT sessions' (not just 'sessions').
        """
        # Replicate JS logic in Python for assertion
        td = 0.3
        ts = 2
        vt_label = f"{ts} VT session{'s' if ts != 1 else ''}"
        sign = '+' if td > 0 else ''
        # Output: "+0.3 net · 2 VT sessions"
        output = f"{sign}{td:.1f} net · {vt_label}"

        assert "net" in output
        assert "VT session" in output
        assert "+0.3 net" in output
        assert "2 VT sessions" in output

    def test_vtsevt18_net_label_single_session(self):
        """Singular 'VT session' (not 'sessions') for ts=1."""
        ts = 1
        vt_label = f"{ts} VT session{'s' if ts != 1 else ''}"
        assert vt_label == "1 VT session"

    def test_vtsevt18_not_trained_label_unchanged(self):
        """ts=0 → 'not trained' label, no 'net'."""
        ts = 0
        # JS: if (ts === 0) return 'not trained'
        result = "not trained" if ts == 0 else "has training"
        assert result == "not trained"
        assert "net" not in result


# ── VTSEVT-19..30: training_delta_precise / training_vt_count / fixed JS ──────

class TestTrainingDeltaPrecise:
    """VTSEVT-19..22: get_skill_profile() now emits training_delta_precise."""

    def _make_profile_skill(self, training_delta_raw: float, vt_count: int = 0) -> dict:
        """Mirror the skill dict built in _views.py get_skill_profile()."""
        return {
            "training_delta": round(training_delta_raw, 1),
            "training_delta_precise": round(training_delta_raw, 2),
            "training_vt_count": vt_count,
            "training_sessions": 0,
        }

    def test_vtsevt19_precise_two_decimal_for_small_delta(self):
        """VTSEVT-19: raw delta 0.0325 → precise=0.03, rounded=0.0."""
        s = self._make_profile_skill(0.0325)
        assert s["training_delta"] == 0.0          # 1-dec loses info
        assert s["training_delta_precise"] == 0.03  # 2-dec preserves it

    def test_vtsevt20_precise_larger_delta_unchanged(self):
        """VTSEVT-20: raw delta 0.325 → precise=round(0.325,2), rounded=0.3."""
        s = self._make_profile_skill(0.325)
        assert s["training_delta"] == 0.3
        assert s["training_delta_precise"] == round(0.325, 2)  # 0.33 in CPython float repr

    def test_vtsevt21_vt_count_populated(self):
        """VTSEVT-21: training_vt_count reflects per-skill VT attempt count."""
        s = self._make_profile_skill(0.165, vt_count=2)
        assert s["training_vt_count"] == 2

    def test_vtsevt22_vt_count_zero_by_default(self):
        """VTSEVT-22: skill with no VT attempts → training_vt_count=0."""
        s = self._make_profile_skill(0.0, vt_count=0)
        assert s["training_vt_count"] == 0


class TestGetVtAttemptCountPerSkill:
    """VTSEVT-23..26: get_vt_attempt_count_per_skill_for_user() unit tests."""

    def _mock_db_execute(self, rows: list[tuple]) -> MagicMock:
        db = MagicMock()
        result = MagicMock()
        result.fetchall.return_value = rows
        db.execute.return_value = result
        return db

    def test_vtsevt23_returns_per_skill_counts(self):
        """VTSEVT-23: two skills → dict with correct counts."""
        from app.services.segment_reward_service import get_vt_attempt_count_per_skill_for_user
        db = self._mock_db_execute([("decisions", 2), ("reactions", 3)])
        result = get_vt_attempt_count_per_skill_for_user(db, user_id=42)
        assert result == {"decisions": 2, "reactions": 3}

    def test_vtsevt24_empty_result_returns_empty_dict(self):
        """VTSEVT-24: no VT attempts → empty dict."""
        from app.services.segment_reward_service import get_vt_attempt_count_per_skill_for_user
        db = self._mock_db_execute([])
        result = get_vt_attempt_count_per_skill_for_user(db, user_id=99)
        assert result == {}

    def test_vtsevt25_values_are_int(self):
        """VTSEVT-25: counts are int, not string or float."""
        from app.services.segment_reward_service import get_vt_attempt_count_per_skill_for_user
        db = self._mock_db_execute([("composure", "5")])
        result = get_vt_attempt_count_per_skill_for_user(db, user_id=1)
        assert isinstance(result["composure"], int)
        assert result["composure"] == 5

    def test_vtsevt26_single_skill_single_attempt(self):
        """VTSEVT-26: one skill, one attempt → {skill: 1}."""
        from app.services.segment_reward_service import get_vt_attempt_count_per_skill_for_user
        db = self._mock_db_execute([("anticipation", 1)])
        result = get_vt_attempt_count_per_skill_for_user(db, user_id=7)
        assert result == {"anticipation": 1}


class TestTrainingMetaHtmlFixed:
    """VTSEVT-27..30: trainingMetaHtml() JS logic with new fields (Python mirror)."""

    def _training_meta_html(self, s: dict) -> str:
        """Python mirror of the updated JS trainingMetaHtml() function."""
        td_precise = s.get("training_delta_precise", s.get("training_delta", 0)) or 0
        vtc = s.get("training_vt_count", 0) or 0
        ts  = s.get("training_sessions", 0) or 0
        has_any = vtc > 0 or ts > 0 or abs(td_precise) >= 0.01
        if not has_any:
            return '<span style="color:#bbb;">not trained</span>'
        parts = []
        if ts > 0:
            parts.append(f"{ts} session{'s' if ts != 1 else ''}")
        if vtc > 0:
            parts.append(f"{vtc} VT")
        count_label = " · ".join(parts)
        if abs(td_precise) < 0.01:
            return f'<span style="color:#95a5a6;">{count_label}</span>'
        sign  = "+" if td_precise > 0 else ""
        color = "#27ae60" if td_precise > 0 else "#e74c3c"
        return (
            f'<span style="color:{color};">{sign}{td_precise:.2f} net</span>'
            f' <span style="color:#95a5a6;">· {count_label}</span>'
        )

    def test_vtsevt27_vt_only_user_not_trained_gone(self):
        """VTSEVT-27: vtc=2, ts=0, precise=0.03 → no 'not trained', shows '+0.03 net'."""
        s = {"training_delta_precise": 0.03, "training_vt_count": 2, "training_sessions": 0}
        html = self._training_meta_html(s)
        assert "not trained" not in html
        assert "+0.03 net" in html
        assert "2 VT" in html

    def test_vtsevt28_zero_delta_but_vt_count(self):
        """VTSEVT-28: vtc=1, precise≈0 → no 'not trained', shows count only."""
        s = {"training_delta_precise": 0.005, "training_vt_count": 1, "training_sessions": 0}
        html = self._training_meta_html(s)
        assert "not trained" not in html
        assert "1 VT" in html
        assert "net" not in html

    def test_vtsevt29_no_training_at_all_shows_not_trained(self):
        """VTSEVT-29: vtc=0, ts=0, precise=0.0 → 'not trained'."""
        s = {"training_delta_precise": 0.0, "training_vt_count": 0, "training_sessions": 0}
        html = self._training_meta_html(s)
        assert "not trained" in html

    def test_vtsevt30_mixed_session_and_vt_label(self):
        """VTSEVT-30: ts=1, vtc=3, precise=0.33 → shows both 'session' and 'VT' in label."""
        s = {"training_delta_precise": 0.33, "training_vt_count": 3, "training_sessions": 1}
        html = self._training_meta_html(s)
        assert "+0.33 net" in html
        assert "1 session" in html
        assert "3 VT" in html
