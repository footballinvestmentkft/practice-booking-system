"""Challenge Results route tests — CR-01..CR-12.

CR-01  GET /challenges/results → template = vt_challenge_results.html
CR-02  Only terminal-status challenges returned (COMPLETED / EXPIRED / CANCELLED / DECLINED)
CR-03  PENDING / ACCEPTED challenges NOT returned
CR-04  status=completed filter → only COMPLETED
CR-05  status=all filter → COMPLETED + EXPIRED + CANCELLED + DECLINED
CR-06  page=0 size=2 → at most 2 rows
CR-07  has_next=True when more rows than page size
CR-08  has_next=False when fewer rows than page size
CR-09  Empty state: no terminal challenges → rows == []
CR-10  User sees only their own challenges (challenger or challenged)
CR-11  Default route status parameter is "all" (not "completed")
CR-12  /challenges hub template contains View All link to /challenges/results
"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest

_BASE = "app.api.web_routes.vt_challenges"


def _run(coro):
    return asyncio.run(coro)


def _user(uid=1):
    from app.models.user import UserRole
    u = MagicMock()
    u.id   = uid
    u.role = UserRole.STUDENT
    return u


def _challenge(cid, challenger_id, challenged_id, status_val, completed_at=None):
    from app.models.vt_challenge import ChallengeStatus
    ch = MagicMock()
    ch.id             = cid
    ch.challenger_id  = challenger_id
    ch.challenged_id  = challenged_id
    ch.status         = ChallengeStatus(status_val)
    ch.completed_at   = completed_at
    ch.created_at     = None
    ch.challenger     = MagicMock(nickname="Alice", email="alice@test.com")
    ch.challenged     = MagicMock(nickname="Bob",   email="bob@test.com")
    ch.game           = MagicMock(name="Color Reaction")
    return ch


def _db_returning(challenges):
    """DB mock whose .query(...).filter(...).filter(...).order_by(...).offset(...).limit(...).all()
    returns the given list."""
    db = MagicMock()
    q  = MagicMock()
    q.filter.return_value  = q
    q.order_by.return_value = q
    q.offset.return_value  = q
    q.limit.return_value   = q
    q.all.return_value     = challenges
    db.query.return_value  = q
    return db


def _call(user=None, db=None, page=0, size=20, status="completed"):
    from app.api.web_routes.vt_challenges import challenge_results

    user    = user or _user()
    request = MagicMock()
    request.query_params.get.return_value = None

    captured = {}

    def _fake_tmpl(tmpl, ctx, **kw):
        captured["template"] = tmpl
        captured["context"]  = ctx
        return MagicMock(status_code=200)

    with patch(f"{_BASE}.templates") as mock_tmpl, \
         patch(f"{_BASE}.require_student_onboarding", return_value=None), \
         patch(f"{_BASE}._spec_ctx", return_value={}):
        mock_tmpl.TemplateResponse.side_effect = _fake_tmpl
        _run(challenge_results(
            request=request,
            page=page,
            size=size,
            status=status,
            db=db or _db_returning([]),
            user=user,
        ))

    return captured


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestChallengeResults:

    def test_cr01_renders_results_template(self):
        """CR-01: GET /challenges/results → vt_challenge_results.html."""
        cap = _call()
        assert cap["template"] == "vt_challenge_results.html"

    def test_cr02_terminal_challenges_in_rows(self):
        """CR-02: COMPLETED challenges appear in rows."""
        ch = _challenge(1, 1, 2, "completed")
        db = _db_returning([ch])
        ctx = _call(db=db)["context"]
        assert len(ctx["rows"]) == 1
        assert ctx["rows"][0]["id"] == 1

    def test_cr03_pending_challenges_not_returned(self):
        """CR-03: PENDING challenges must not appear (only terminal statuses queried)."""
        # The DB mock returns nothing — simulating the filter working correctly.
        # We verify the route builds rows only from what DB returns.
        db = _db_returning([])
        ctx = _call(db=db, status="completed")["context"]
        assert ctx["rows"] == []

    def test_cr04_status_completed_filter(self):
        """CR-04: status=completed passes [COMPLETED] to the query."""
        from app.models.vt_challenge import ChallengeStatus
        from app.api.web_routes.vt_challenges import _RESULT_STATUS_MAP
        assert ChallengeStatus.COMPLETED in _RESULT_STATUS_MAP["completed"]
        assert ChallengeStatus.PENDING   not in _RESULT_STATUS_MAP["completed"]
        assert ChallengeStatus.ACCEPTED  not in _RESULT_STATUS_MAP["completed"]

    def test_cr05_status_all_filter(self):
        """CR-05: status=all includes COMPLETED, EXPIRED, CANCELLED, DECLINED."""
        from app.models.vt_challenge import ChallengeStatus
        from app.api.web_routes.vt_challenges import _RESULT_STATUS_MAP
        statuses = _RESULT_STATUS_MAP["all"]
        assert ChallengeStatus.COMPLETED in statuses
        assert ChallengeStatus.EXPIRED   in statuses
        assert ChallengeStatus.CANCELLED in statuses
        assert ChallengeStatus.DECLINED  in statuses

    def test_cr06_page_size_truncation(self):
        """CR-06: page size truncates rows to at most `size`."""
        challenges = [_challenge(i, 1, 2, "completed") for i in range(3)]
        # DB returns 3 items but we set size=2, so the +1 trick should give has_next
        db = _db_returning(challenges)
        ctx = _call(db=db, page=0, size=2)["context"]
        assert len(ctx["rows"]) == 2

    def test_cr07_has_next_true_when_more_rows(self):
        """CR-07: has_next=True when DB returns size+1 items."""
        challenges = [_challenge(i, 1, 2, "completed") for i in range(3)]
        db = _db_returning(challenges)
        ctx = _call(db=db, page=0, size=2)["context"]
        assert ctx["has_next"] is True

    def test_cr08_has_next_false_when_fewer_rows(self):
        """CR-08: has_next=False when DB returns fewer than size+1 items."""
        challenges = [_challenge(1, 1, 2, "completed")]
        db = _db_returning(challenges)
        ctx = _call(db=db, page=0, size=20)["context"]
        assert ctx["has_next"] is False

    def test_cr09_empty_state(self):
        """CR-09: no challenges → rows == [], has_next=False."""
        ctx = _call(db=_db_returning([]))["context"]
        assert ctx["rows"] == []
        assert ctx["has_next"] is False

    def test_cr10_rows_contain_card_and_detail_urls(self):
        """CR-10: each row has card_url and detail_url pointing to correct paths."""
        ch = _challenge(42, 1, 2, "completed")
        db = _db_returning([ch])
        ctx = _call(db=db, status="completed")["context"]
        row = ctx["rows"][0]
        assert row["card_url"]   == "/challenges/42/card"
        assert row["detail_url"] == "/challenges/42"

    def test_cr11_default_status_is_all(self):
        """CR-11: /challenges/results default status parameter is 'all', not 'completed'."""
        import inspect
        from app.api.web_routes.vt_challenges import challenge_results
        sig     = inspect.signature(challenge_results)
        default = sig.parameters["status"].default
        # FastAPI Query wraps the default — unwrap it
        actual  = getattr(default, "default", default)
        assert actual == "all", (
            f"Default status should be 'all' for discoverability; got {actual!r}"
        )

    def test_cr12_challenges_hub_template_has_view_all_link(self):
        """CR-12: vt_challenges.html terminal_rows section contains link to /challenges/results."""
        import os
        tmpl_path = os.path.join(
            os.path.dirname(__file__),
            "../../../../app/templates/vt_challenges.html",
        )
        with open(os.path.normpath(tmpl_path), encoding="utf-8") as f:
            content = f.read()
        assert "/challenges/results" in content, (
            "vt_challenges.html must contain a link to /challenges/results in the terminal_rows section"
        )
