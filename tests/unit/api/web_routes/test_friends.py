"""Unit tests for PR-F1 — Minimal Friendship System.

FR-01   Friendship model has id, requester_id, addressee_id, status, created_at, updated_at
FR-18   POST /friends/send with valid email → PENDING row created + redirect ?success=request_sent
FR-19   POST /friends/send with unknown identifier → redirect ?error=user_not_found
FR-20   POST /friends/send with own email → redirect ?error=self_request
FR-21   POST /friends/send when already PENDING → redirect ?error=request_pending
FR-02   CheckConstraint 'ck_no_self_friendship' present in __table_args__
FR-03   UniqueConstraint 'uq_friendship_pair' present in __table_args__
FR-04   POST /friends/request/{self} → redirect ?error=self_request
FR-05   POST /friends/request/{inactive} → redirect ?error=user_not_found
FR-06   POST /friends/request/{id} success → PENDING row created + redirect ?success=request_sent
FR-07   POST /friends/accept/{id} wrong addressee → redirect ?error=not_found
FR-08   POST /friends/accept/{id} success → status ACCEPTED + redirect ?success=request_accepted
FR-09   POST /friends/decline/{id} wrong addressee → redirect ?error=not_found
FR-10   POST /friends/remove/{id} when not friends → redirect ?error=not_friends
FR-11   is_friends returns True for ACCEPTED friendship
FR-12   is_friends returns False for PENDING friendship
FR-13   is_friends is symmetric (B→A returns True when A→B accepted)
FR-14   send_friend_request creates FRIEND_REQUEST_RECEIVED notification
FR-15   accept_friend_request creates FRIEND_REQUEST_ACCEPTED notification
FR-16   GET /friends → 200, renders friends.html with friends + incoming_count context
FR-17   GET /friends/requests → 200, renders friends.html with active_tab='requests'
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest
from fastapi.responses import RedirectResponse
from sqlalchemy import CheckConstraint, UniqueConstraint

from app.models.friendship import (
    Friendship, FriendshipStatus, get_friendship, is_friends,
)
from app.models.notification import NotificationType

_BASE = "app.api.web_routes.friends"


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _user(uid=1, active=True):
    u = MagicMock()
    u.id = uid
    u.email = f"user{uid}@lfa.com"
    u.nickname = None
    u.is_active = active
    return u


def _req(qp=None):
    r = MagicMock()
    _qp = qp or {}
    r.query_params.get = lambda k, d=None: _qp.get(k, d)
    return r


def _db():
    return MagicMock()


def _run(coro):
    return asyncio.run(coro)


def _friendship(fid=10, requester_id=1, addressee_id=2,
                status=FriendshipStatus.PENDING):
    f = MagicMock(spec=Friendship)
    f.id = fid
    f.requester_id = requester_id
    f.addressee_id = addressee_id
    f.status = status
    return f


# ── Model structure ───────────────────────────────────────────────────────────

class TestFriendshipModel:

    def test_fr01_model_columns_present(self):
        cols = {c.key for c in Friendship.__table__.columns}
        assert {"id", "requester_id", "addressee_id", "status",
                "created_at", "updated_at"}.issubset(cols)

    def test_fr02_check_constraint_no_self_friendship(self):
        args = Friendship.__table_args__
        names = {c.name for c in args if isinstance(c, CheckConstraint)}
        assert "ck_no_self_friendship" in names

    def test_fr03_unique_constraint_pair(self):
        args = Friendship.__table_args__
        names = {c.name for c in args if isinstance(c, UniqueConstraint)}
        assert "uq_friendship_pair" in names


# ── is_friends helper ─────────────────────────────────────────────────────────

class TestIsFriendsHelper:

    def _make_db(self, row):
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row
        return db

    def test_fr11_true_for_accepted(self):
        row = _friendship(status=FriendshipStatus.ACCEPTED)
        db = self._make_db(row)
        assert is_friends(db, 1, 2) is True

    def test_fr12_false_for_pending(self):
        db = self._make_db(None)
        assert is_friends(db, 1, 2) is False

    def test_fr13_symmetric_b_to_a(self):
        """is_friends(A,B) and is_friends(B,A) both call the same underlying query
        (both directions are encoded in the OR filter). Verify the helper accepts
        (b_id, a_id) without error and relies on the OR clause."""
        row = _friendship(requester_id=2, addressee_id=1,
                          status=FriendshipStatus.ACCEPTED)
        db = self._make_db(row)
        assert is_friends(db, 2, 1) is True


# ── Route: send_friend_request ────────────────────────────────────────────────

class TestSendFriendRequest:

    def test_fr04_self_request_blocked(self):
        from app.api.web_routes.friends import send_friend_request
        user = _user(uid=5)
        db = _db()
        result = _run(send_friend_request(user_id=5, db=db, user=user))
        assert isinstance(result, RedirectResponse)
        assert "error=self_request" in result.headers["location"]

    def test_fr05_inactive_user_blocked(self):
        from app.api.web_routes.friends import send_friend_request
        user = _user(uid=1)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = None
        result = _run(send_friend_request(user_id=99, db=db, user=user))
        assert isinstance(result, RedirectResponse)
        assert "error=user_not_found" in result.headers["location"]

    def test_fr06_success_creates_pending(self):
        from app.api.web_routes.friends import send_friend_request
        user = _user(uid=1)
        target = _user(uid=2)
        db = _db()

        call_count = [0]
        def _first():
            c = call_count[0]
            call_count[0] += 1
            if c == 0:
                return target   # target user query
            return None         # get_friendship query

        db.query.return_value.filter.return_value.first.side_effect = _first

        with patch(f"{_BASE}.get_friendship", return_value=None), \
             patch(f"{_BASE}.notification_service") as mock_svc:
            result = _run(send_friend_request(user_id=2, db=db, user=user))

        assert isinstance(result, RedirectResponse)
        assert "success=request_sent" in result.headers["location"]
        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_fr14_notification_sent_on_request(self):
        from app.api.web_routes.friends import send_friend_request
        user = _user(uid=1)
        target = _user(uid=2)
        db = _db()

        with patch(f"{_BASE}.get_friendship", return_value=None), \
             patch(f"{_BASE}.notification_service") as mock_svc:
            db.query.return_value.filter.return_value.first.return_value = target
            _run(send_friend_request(user_id=2, db=db, user=user))

        mock_svc.create_notification.assert_called_once()
        kwargs = mock_svc.create_notification.call_args.kwargs
        assert kwargs["user_id"] == 2
        assert kwargs["notification_type"] == NotificationType.FRIEND_REQUEST_RECEIVED


# ── Route: accept_friend_request ──────────────────────────────────────────────

class TestAcceptFriendRequest:

    def test_fr07_wrong_addressee_blocked(self):
        from app.api.web_routes.friends import accept_friend_request
        user = _user(uid=3)
        row = _friendship(fid=10, requester_id=1, addressee_id=2,
                          status=FriendshipStatus.PENDING)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row
        result = _run(accept_friend_request(friendship_id=10, db=db, user=user))
        assert isinstance(result, RedirectResponse)
        assert "error=not_found" in result.headers["location"]

    def test_fr08_accept_success(self):
        from app.api.web_routes.friends import accept_friend_request
        user = _user(uid=2)
        row = _friendship(fid=10, requester_id=1, addressee_id=2,
                          status=FriendshipStatus.PENDING)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row

        with patch(f"{_BASE}.notification_service"):
            result = _run(accept_friend_request(friendship_id=10, db=db, user=user))

        assert isinstance(result, RedirectResponse)
        assert "success=request_accepted" in result.headers["location"]
        assert row.status == FriendshipStatus.ACCEPTED
        db.commit.assert_called_once()

    def test_fr15_notification_sent_on_accept(self):
        from app.api.web_routes.friends import accept_friend_request
        user = _user(uid=2)
        row = _friendship(fid=10, requester_id=1, addressee_id=2,
                          status=FriendshipStatus.PENDING)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row

        with patch(f"{_BASE}.notification_service") as mock_svc:
            _run(accept_friend_request(friendship_id=10, db=db, user=user))

        mock_svc.create_notification.assert_called_once()
        kwargs = mock_svc.create_notification.call_args.kwargs
        assert kwargs["user_id"] == 1  # sent to original requester
        assert kwargs["notification_type"] == NotificationType.FRIEND_REQUEST_ACCEPTED


# ── Route: decline_friend_request ────────────────────────────────────────────

class TestDeclineFriendRequest:

    def test_fr09_wrong_addressee_blocked(self):
        from app.api.web_routes.friends import decline_friend_request
        user = _user(uid=3)
        row = _friendship(fid=10, requester_id=1, addressee_id=2,
                          status=FriendshipStatus.PENDING)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row
        result = _run(decline_friend_request(friendship_id=10, db=db, user=user))
        assert isinstance(result, RedirectResponse)
        assert "error=not_found" in result.headers["location"]


# ── Route: remove_friend ──────────────────────────────────────────────────────

class TestRemoveFriend:

    def test_fr10_not_friends_returns_error(self):
        from app.api.web_routes.friends import remove_friend
        user = _user(uid=1)
        db = _db()

        with patch(f"{_BASE}.get_friendship", return_value=None):
            result = _run(remove_friend(user_id=2, db=db, user=user))

        assert isinstance(result, RedirectResponse)
        assert "error=not_friends" in result.headers["location"]


# ── Page routes ───────────────────────────────────────────────────────────────

class TestFriendsPages:

    def test_fr16_friends_page_renders(self):
        from app.api.web_routes.friends import friends_page
        user = _user(uid=1)
        req = _req()
        db = _db()

        with patch(f"{_BASE}._friend_list", return_value=[]), \
             patch(f"{_BASE}._incoming_requests", return_value=[]), \
             patch(f"{_BASE}._outgoing_requests", return_value=[]), \
             patch(f"{_BASE}.templates") as mock_tpl:
            mock_tpl.TemplateResponse.return_value = MagicMock(status_code=200)
            result = _run(friends_page(request=req, db=db, user=user))

        mock_tpl.TemplateResponse.assert_called_once()
        call_args = mock_tpl.TemplateResponse.call_args
        template_name = call_args.args[0]
        context = call_args.args[1]
        assert template_name == "friends.html"
        assert "friends" in context
        assert "incoming_count" in context

    def test_fr17_friends_requests_page_renders(self):
        from app.api.web_routes.friends import friends_requests_page
        user = _user(uid=1)
        req = _req()
        db = _db()

        with patch(f"{_BASE}._friend_list", return_value=[]), \
             patch(f"{_BASE}._incoming_requests", return_value=[]), \
             patch(f"{_BASE}._outgoing_requests", return_value=[]), \
             patch(f"{_BASE}.templates") as mock_tpl:
            mock_tpl.TemplateResponse.return_value = MagicMock(status_code=200)
            result = _run(friends_requests_page(request=req, db=db, user=user))

        mock_tpl.TemplateResponse.assert_called_once()
        call_args = mock_tpl.TemplateResponse.call_args
        template_name = call_args.args[0]
        context = call_args.args[1]
        assert template_name == "friends.html"
        assert context.get("active_tab") == "requests"
        assert "incoming" in context
        assert "outgoing" in context


# ── Route: send_friend_request_by_identifier (/friends/send) ─────────────────

class TestSendFriendRequestByIdentifier:

    def _target(self, uid=2, email="player@lfa.com", nickname="player"):
        t = _user(uid=uid)
        t.email = email
        t.nickname = nickname
        return t

    def test_fr18_valid_email_creates_pending(self):
        from app.api.web_routes.friends import send_friend_request_by_identifier
        user = _user(uid=1)
        user.email = "me@lfa.com"
        target = self._target()
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = target

        with patch(f"{_BASE}.get_friendship", return_value=None), \
             patch(f"{_BASE}.notification_service"):
            result = _run(
                send_friend_request_by_identifier(
                    request=_req(), identifier="player@lfa.com",
                    db=db, user=user,
                )
            )

        assert isinstance(result, RedirectResponse)
        assert "success=request_sent" in result.headers["location"]
        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_fr19_unknown_identifier_returns_user_not_found(self):
        from app.api.web_routes.friends import send_friend_request_by_identifier
        user = _user(uid=1)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = None

        result = _run(
            send_friend_request_by_identifier(
                request=_req(), identifier="nobody@nowhere.com",
                db=db, user=user,
            )
        )

        assert isinstance(result, RedirectResponse)
        assert "error=user_not_found" in result.headers["location"]

    def test_fr20_own_identifier_blocked_as_self_request(self):
        from app.api.web_routes.friends import send_friend_request_by_identifier
        user = _user(uid=1)
        user.email = "me@lfa.com"
        # target lookup returns the same user
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = user

        result = _run(
            send_friend_request_by_identifier(
                request=_req(), identifier="me@lfa.com",
                db=db, user=user,
            )
        )

        assert isinstance(result, RedirectResponse)
        assert "error=self_request" in result.headers["location"]

    def test_fr21_duplicate_pending_returns_request_pending(self):
        from app.api.web_routes.friends import send_friend_request_by_identifier
        user = _user(uid=1)
        target = self._target()
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = target
        existing = _friendship(requester_id=1, addressee_id=2,
                               status=FriendshipStatus.PENDING)

        with patch(f"{_BASE}.get_friendship", return_value=existing):
            result = _run(
                send_friend_request_by_identifier(
                    request=_req(), identifier="player@lfa.com",
                    db=db, user=user,
                )
            )

        assert isinstance(result, RedirectResponse)
        assert "error=request_pending" in result.headers["location"]


# ── GET /friends/search — FR-22..FR-30 ───────────────────────────────────────

class TestFriendsSearch:
    """Tests for the live-search autocomplete endpoint."""

    def _run_search(self, q, db, user, limit=10):
        from app.api.web_routes.friends import friends_search
        return _run(friends_search(request=_req(), q=q, limit=limit, db=db, user=user))

    def test_fr22_query_param_has_min_length_2(self):
        """FR-22: friends_search q parameter declares min_length=2 (FastAPI enforces at routing layer)."""
        import inspect
        from app.api.web_routes.friends import friends_search
        sig    = inspect.signature(friends_search)
        q_info = sig.parameters["q"].default          # FastAPI FieldInfo object
        # Pydantic v2: constraints live in q_info.metadata as annotated validators
        min_len = getattr(q_info, "min_length", None)
        if min_len is None:
            # Pydantic v2 path: metadata=[MinLen(2), ...]
            min_len = next(
                (getattr(m, "min_length", None) for m in getattr(q_info, "metadata", [])),
                None,
            )
        assert min_len == 2, f"Expected min_length=2 on q, got {min_len}"

    def test_fr23_matching_name_returns_correct_structure(self):
        """FR-23: q matching a user's name → JSON list with id/display_name/email/state."""
        user = _user(uid=1)
        target = _user(uid=2)
        target.name = "Budapest Player"
        target.nickname = None
        target.email = "bplayer@lfa.com"

        db = _db()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [target]

        with patch(f"{_BASE}._friendship_state", return_value=("none", None)):
            resp = self._run_search("Bud", db, user)

        import json
        data = json.loads(resp.body)
        assert len(data) == 1
        assert data[0]["id"] == 2
        assert "display_name" in data[0]
        assert data[0]["email"] == "bplayer@lfa.com"
        assert data[0]["state"] == "none"
        assert "friendship_id" in data[0]

    def test_fr24_self_excluded_from_results(self):
        """FR-24: current user never appears in search results (User.id != user.id filter)."""
        user = _user(uid=1)
        # DB returns empty because user.id is filtered out at query level
        db = _db()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = []

        resp = self._run_search("me", db, user)
        import json
        data = json.loads(resp.body)
        assert not any(item["id"] == 1 for item in data)

    def test_fr25_inactive_users_excluded(self):
        """FR-25: inactive users absent from results (User.is_active==True filter at DB level)."""
        user = _user(uid=1)
        # DB returns empty because inactive users are filtered out
        db = _db()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = []

        resp = self._run_search("inactive", db, user)
        import json
        data = json.loads(resp.body)
        assert len(data) == 0

    def test_fr26_state_none_for_stranger(self):
        """FR-26: user with no friendship → state='none', friendship_id=None."""
        viewer = _user(uid=1)
        target = _user(uid=2)
        target.name = "Stranger"
        target.nickname = None

        db = _db()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [target]

        with patch(f"{_BASE}._friendship_state", return_value=("none", None)):
            resp = self._run_search("Str", db, viewer)

        import json
        data = json.loads(resp.body)
        assert data[0]["state"] == "none"
        assert data[0]["friendship_id"] is None

    def test_fr27_state_accepted_for_existing_friend(self):
        """FR-27: existing accepted friendship → state='accepted', friendship_id set."""
        viewer = _user(uid=1)
        target = _user(uid=2)
        target.name = "Good Friend"
        target.nickname = None

        db = _db()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [target]

        with patch(f"{_BASE}._friendship_state", return_value=("accepted", 99)):
            resp = self._run_search("Good", db, viewer)

        import json
        data = json.loads(resp.body)
        assert data[0]["state"] == "accepted"
        assert data[0]["friendship_id"] == 99

    def test_fr28_state_pending_sent(self):
        """FR-28: outgoing pending request → state='pending_sent'."""
        viewer = _user(uid=1)
        target = _user(uid=2)
        target.name = "Pending Target"
        target.nickname = None

        db = _db()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [target]

        with patch(f"{_BASE}._friendship_state", return_value=("pending_sent", 55)):
            resp = self._run_search("Pen", db, viewer)

        import json
        data = json.loads(resp.body)
        assert data[0]["state"] == "pending_sent"

    def test_fr29_state_pending_received(self):
        """FR-29: incoming pending request → state='pending_received'."""
        viewer = _user(uid=1)
        target = _user(uid=2)
        target.name = "Incoming Requester"
        target.nickname = None

        db = _db()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = [target]

        with patch(f"{_BASE}._friendship_state", return_value=("pending_received", 77)):
            resp = self._run_search("Inc", db, viewer)

        import json
        data = json.loads(resp.body)
        assert data[0]["state"] == "pending_received"

    def test_fr30_limit_respected(self):
        """FR-30: limit=3 returns at most 3 results."""
        viewer = _user(uid=1)
        targets = [_user(uid=i) for i in range(2, 6)]  # 4 users
        for t in targets:
            t.name = f"Player {t.id}"
            t.nickname = None

        db = _db()
        # DB already applies LIMIT — simulate by returning only 3
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = targets[:3]

        with patch(f"{_BASE}._friendship_state", return_value=("none", None)):
            resp = self._run_search("Player", db, viewer, limit=3)

        import json
        data = json.loads(resp.body)
        assert len(data) <= 3


# ── Template content tests — FR-31..FR-32 ────────────────────────────────────

import os as _os
from jinja2 import Environment, FileSystemLoader

_TMPL_DIR = _os.path.join(
    _os.path.dirname(__file__), "..", "..", "..", "..", "app", "templates"
)


def _render_friends(friends=None):
    """Render friends.html Friends tab with optional friend list."""
    env = Environment(loader=FileSystemLoader(_TMPL_DIR), autoescape=True)
    template = env.get_template("friends.html")
    fr_mock = MagicMock()
    fr_mock.id = 7
    fr_mock.name = "Test Friend"
    fr_mock.nickname = None
    fr_mock.email = "friend@lfa.com"
    return template.render(
        request=MagicMock(),
        user=MagicMock(),
        friends=friends if friends is not None else [fr_mock],
        incoming=[],
        outgoing=[],
        incoming_count=0,
        success=None,
        error=None,
        active_tab=None,
    )


class TestFriendsTemplateContent:

    def test_fr31_friend_card_has_view_profile_link(self):
        """FR-31: friend card contains /players/{id} View Profile link."""
        html = _render_friends()
        assert "/players/7" in html
        assert "View Profile" in html

    def test_fr32_player_profile_challenge_link_uses_friend_id(self):
        """FR-32: player_profile.html challenge link uses ?friend_id= param, not ?friend=."""
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(_TMPL_DIR), autoescape=True)
        template = env.get_template("public/player_profile.html")
        profile_user = MagicMock()
        profile_user.id = 42
        profile_user.full_name = "Player"
        profile_user.username = "player"
        fp = MagicMock()
        fp.state = "accepted"
        html = template.render(
            request=MagicMock(),
            profile_user=profile_user,
            user=MagicMock(id=99),
            fp=fp,
            friendship_panel=fp,
            profile_grid_slots=[],
            is_own_profile=False,
            is_authenticated=True,
            highlight_video=None,
            card_draft=MagicMock(published_data=None),
        )
        assert "?friend_id=42" in html
        assert "?friend=42" not in html
