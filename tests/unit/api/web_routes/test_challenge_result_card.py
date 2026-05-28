"""Challenge Result Card route tests — RC-01..RC-10.

RC-01  Challenger participant → 200
RC-02  Challenged participant → 200
RC-03  Non-participant → 403
RC-04  Non-existent challenge → 403
RC-05  owned_format_ids drives format_rows.owned=True
RC-06  has_any_owned=True when at least one format owned
RC-07  has_any_owned=False when no formats owned
RC-08  unlocked_phases correctly passed to context for COMPLETED challenge
RC-09  locked_phases correctly passed to context for COMPLETED challenge
RC-10  format_rows contain required keys: design_id, label, dims, credit_cost, owned
"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

_BASE = "app.api.web_routes.vt_challenges"


def _run(coro):
    return asyncio.run(coro)


def _user(uid=1):
    from app.models.user import UserRole
    u = MagicMock()
    u.id   = uid
    u.role = UserRole.STUDENT
    return u


def _challenge(challenger_id=1, challenged_id=2, status_val="completed",
               challenger_attempt_id=None, challenged_attempt_id=None):
    from app.models.vt_challenge import ChallengeStatus
    ch = MagicMock()
    ch.id                    = 99
    ch.challenger_id         = challenger_id
    ch.challenged_id         = challenged_id
    ch.status                = ChallengeStatus(status_val)
    ch.completed_at          = None
    ch.challenger_attempt_id = challenger_attempt_id
    ch.challenged_attempt_id = challenged_attempt_id
    ch.challenger            = MagicMock(nickname="Alice", email="alice@test.com")
    ch.challenged            = MagicMock(nickname="Bob",   email="bob@test.com")
    ch.game                  = MagicMock(name="Color Reaction")
    ch.winner_id             = None
    ch.is_draw               = False
    ch.forfeit_user_id       = None
    ch.forfeit_reason        = None
    return ch


def _db_with_challenge(ch):
    db = MagicMock()
    q  = MagicMock()
    q.filter.return_value  = q
    q.first.return_value   = ch
    db.query.return_value  = q
    return db


def _db_no_challenge():
    db = MagicMock()
    q  = MagicMock()
    q.filter.return_value  = q
    q.first.return_value   = None
    db.query.return_value  = q
    return db


def _call(challenge_id=99, user=None, db=None,
          unlocked=None, locked=None, owned_ids=None):
    from app.api.web_routes.vt_challenges import challenge_result_card

    user    = user or _user(uid=1)
    request = MagicMock()
    request.query_params.get.return_value = None

    if unlocked is None:
        unlocked = ["completed_score_win"]
    if locked is None:
        locked = []
    if owned_ids is None:
        owned_ids = ["challenge_post_16_9", "challenge_story_9_16"]

    captured = {}

    def _fake_tmpl(tmpl, ctx, **kw):
        captured["template"] = tmpl
        captured["context"]  = ctx
        return MagicMock(status_code=200)

    with patch(f"{_BASE}.templates") as mock_tmpl, \
         patch(f"{_BASE}.require_student_onboarding", return_value=None), \
         patch(f"{_BASE}._spec_ctx", return_value={}), \
         patch(f"{_BASE}.get_unlocked_challenge_card_phases", return_value=unlocked), \
         patch(f"{_BASE}.get_locked_challenge_card_phases",   return_value=locked), \
         patch("app.services.card_design_service.get_owned_design_ids",
               return_value=list(owned_ids)):
        mock_tmpl.TemplateResponse.side_effect = _fake_tmpl
        _run(challenge_result_card(
            challenge_id=challenge_id,
            request=request,
            db=db or _db_with_challenge(_challenge()),
            user=user,
        ))

    return captured


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestChallengeResultCard:

    def test_rc01_challenger_gets_200(self):
        """RC-01: challenger participant → 200 (template rendered)."""
        user = _user(uid=1)
        ch   = _challenge(challenger_id=1, challenged_id=2)
        db   = _db_with_challenge(ch)
        cap  = _call(user=user, db=db)
        assert cap["template"] == "vt_challenge_result_card.html"

    def test_rc02_challenged_gets_200(self):
        """RC-02: challenged participant → 200 (template rendered)."""
        user = _user(uid=2)
        ch   = _challenge(challenger_id=1, challenged_id=2)
        db   = _db_with_challenge(ch)
        cap  = _call(user=user, db=db)
        assert cap["template"] == "vt_challenge_result_card.html"

    def test_rc03_non_participant_403(self):
        """RC-03: non-participant user → 403."""
        user = _user(uid=99)
        ch   = _challenge(challenger_id=1, challenged_id=2)
        db   = _db_with_challenge(ch)
        with pytest.raises(HTTPException) as exc_info:
            _call(user=user, db=db)
        assert exc_info.value.status_code == 403

    def test_rc04_missing_challenge_403(self):
        """RC-04: challenge not found → 403."""
        user = _user(uid=1)
        db   = _db_no_challenge()
        with pytest.raises(HTTPException) as exc_info:
            _call(user=user, db=db)
        assert exc_info.value.status_code == 403

    def test_rc05_owned_format_flagged_correctly(self):
        """RC-05: format_rows.owned=True only for formats in owned_ids."""
        user = _user(uid=1)
        ch   = _challenge(challenger_id=1, challenged_id=2)
        db   = _db_with_challenge(ch)
        # Own only Post 16:9, not Story 9:16
        ctx = _call(user=user, db=db,
                    owned_ids=["challenge_post_16_9"])["context"]
        rows = {r["design_id"]: r for r in ctx["format_rows"]}
        assert rows["challenge_post_16_9"]["owned"]  is True
        assert rows["challenge_story_9_16"]["owned"] is False

    def test_rc06_has_any_owned_true(self):
        """RC-06: has_any_owned=True when at least one format is owned."""
        user = _user(uid=1)
        ch   = _challenge(challenger_id=1, challenged_id=2)
        db   = _db_with_challenge(ch)
        ctx = _call(user=user, db=db,
                    owned_ids=["challenge_post_16_9"])["context"]
        assert ctx["has_any_owned"] is True

    def test_rc07_has_any_owned_false(self):
        """RC-07: has_any_owned=False when no formats are owned."""
        user = _user(uid=1)
        ch   = _challenge(challenger_id=1, challenged_id=2)
        db   = _db_with_challenge(ch)
        ctx = _call(user=user, db=db, owned_ids=[])["context"]
        assert ctx["has_any_owned"] is False

    def test_rc08_unlocked_phases_in_context(self):
        """RC-08: unlocked_phases passed to context correctly."""
        user = _user(uid=1)
        ch   = _challenge(challenger_id=1, challenged_id=2)
        db   = _db_with_challenge(ch)
        ctx  = _call(user=user, db=db,
                     unlocked=["completed_score_win", "skill_delta_result"])["context"]
        assert "completed_score_win"  in ctx["unlocked_phases"]
        assert "skill_delta_result"   in ctx["unlocked_phases"]

    def test_rc09_locked_phases_in_context(self):
        """RC-09: locked_phases passed to context correctly."""
        user = _user(uid=1)
        ch   = _challenge(challenger_id=1, challenged_id=2)
        db   = _db_with_challenge(ch)
        ctx  = _call(user=user, db=db,
                     locked=["challenge_sent"])["context"]
        assert "challenge_sent" in ctx["locked_phases"]

    def test_rc10_format_rows_have_required_keys(self):
        """RC-10: every format_row contains design_id, label, dims, credit_cost, owned."""
        user = _user(uid=1)
        ch   = _challenge(challenger_id=1, challenged_id=2)
        db   = _db_with_challenge(ch)
        ctx  = _call(user=user, db=db)["context"]
        for row in ctx["format_rows"]:
            for key in ("design_id", "label", "dims", "credit_cost", "owned"):
                assert key in row, f"format_row missing key {key!r}"
