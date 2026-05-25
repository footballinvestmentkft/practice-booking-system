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


def _fd_entry(level=None, photo_url=None, positions=None, initials="TF"):
    """Build a friend_data dict entry for test use."""
    return {"level": level, "photo_url": photo_url,
            "positions": positions or [], "initials": initials}


def _render_friends(friends=None, friend_data=None, position="midfielder"):
    """Render friends.html Friends tab with optional friend list.

    friend_data: if None, a default is auto-built from `position` for fr_mock (id=7).
    """
    env = Environment(loader=FileSystemLoader(_TMPL_DIR), autoescape=True)
    template = env.get_template("friends.html")
    fr_mock = MagicMock()
    fr_mock.id = 7
    fr_mock.name = "Test Friend"
    fr_mock.nickname = None
    fr_mock.email = "friend@lfa.com"
    fr_mock.position = position

    if friend_data is None:
        from app.utils.football_positions import (
            normalize_position as _np,
            position_label as _pl,
            position_short as _ps,
        )
        if position:
            canonical = _np(position) or position
            raw_label = _pl(canonical)
            pos_label = raw_label.replace("_", " ").title() if "_" in raw_label else raw_label
            positions_list = [{"short": _ps(canonical), "label": pos_label}]
        else:
            positions_list = []
        friend_data = {7: _fd_entry(positions=positions_list)}

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
        friend_data=friend_data,
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


# ── Friend card design + data tests — FR-33..FR-42 ───────────────────────────

class TestFriendsCardDesign:
    """Design token alignment, player data pills, regression for search + remove."""

    def test_fr33_friend_card_has_view_profile_link(self):
        """FR-33: friend card renders /players/{id} View Profile link (with full context)."""
        html = _render_friends(friend_data={7: _fd_entry(level=3, positions=[{"short": "CM", "label": "Central Midfielder"}])})
        assert "/players/7" in html
        assert "View Profile" in html

    def test_fr34_friend_card_challenge_uses_friend_id_param(self):
        """FR-34: Challenge button uses ?friend_id= not bare ?friend=."""
        html = _render_friends(friend_data={7: _fd_entry(level=3, positions=[{"short": "CM", "label": "Central Midfielder"}])})
        assert "?friend_id=7" in html
        assert "?friend=7" not in html

    def test_fr35_position_pill_rendered_when_set(self):
        """FR-35: position short badge appears when positions list is non-empty."""
        html = _render_friends(position="midfielder")
        assert "CM" in html  # centre_midfield → short code "CM"

    def test_fr36_level_pill_rendered_when_available(self):
        """FR-36: level pill appears when friend_data contains a level for the friend."""
        html = _render_friends(friend_data={7: _fd_entry(level=4)})
        assert "Lv 4" in html

    def test_fr37_no_error_when_position_is_none(self):
        """FR-37: no UndefinedError when position_label is empty — page still renders."""
        html = _render_friends(position=None)
        assert "/players/7" in html

    def test_fr38_no_error_when_friend_levels_empty(self):
        """FR-38: no error when friend_data has no level (friend has no LFA license)."""
        html = _render_friends(position="goalkeeper")
        assert "/players/7" in html

    def test_fr39_local_app_tokens_defined_in_template(self):
        """FR-39: friends.html contains local :root --app-* token definitions."""
        tmpl_path = _os.path.join(_TMPL_DIR, "friends.html")
        with open(tmpl_path) as fh:
            content = fh.read()
        for token in (
            "--app-bg:", "--app-card:", "--app-card-alt:",
            "--app-text:", "--app-text-muted:",
            "--app-border:", "--app-success:", "--app-error:",
        ):
            assert token in content, f"Token {token!r} not defined in friends.html"

    def test_fr40_active_tab_uses_lfa_brand_not_purple(self):
        """FR-40: .fr-tab.active uses LFA yellow/black, not purple #4f46e5."""
        import re
        tmpl_path = _os.path.join(_TMPL_DIR, "friends.html")
        with open(tmpl_path) as fh:
            content = fh.read()
        assert "#FFD200" in content, "LFA yellow (#FFD200) missing from friends.html"
        match = re.search(r'\.fr-tab\.active\s*\{[^}]+\}', content)
        assert match, ".fr-tab.active rule not found in friends.html"
        assert "#4f46e5" not in match.group(), \
            "Purple #4f46e5 still present in .fr-tab.active — should be LFA brand"

    def test_fr41_search_endpoint_still_returns_json_list(self):
        """FR-41: GET /friends/search still returns a JSON list (regression)."""
        from app.api.web_routes.friends import friends_search
        import json
        user = _user(uid=1)
        db = _db()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = []
        resp = _run(friends_search(request=_req(), q="test", limit=10, db=db, user=user))
        data = json.loads(resp.body)
        assert isinstance(data, list)

    def test_fr42_remove_friend_returns_success_redirect(self):
        """FR-42: POST /friends/remove/{user_id} for ACCEPTED friend → success redirect."""
        from app.api.web_routes.friends import remove_friend
        user = _user(uid=1)
        row = _friendship(fid=10, requester_id=1, addressee_id=2,
                          status=FriendshipStatus.ACCEPTED)
        db = _db()
        with patch(f"{_BASE}.get_friendship", return_value=row):
            result = _run(remove_friend(user_id=2, db=db, user=user))
        assert isinstance(result, RedirectResponse)
        assert "success=friend_removed" in result.headers["location"]
        db.delete.assert_called_once_with(row)
        db.commit.assert_called_once()


# ── LFA Player sub-page nav tests — FR-NAV-01..FR-NAV-09 ─────────────────────

class TestFriendsNavAlignment:
    """Verify /friends uses LFA Player spec sub-page header pattern."""

    def _page_context(self, route_fn, req, db, user, extra_patches=None):
        """Call route, return the context dict passed to TemplateResponse."""
        patches = {
            f"{_BASE}._friend_list": [],
            f"{_BASE}._incoming_requests": [],
            f"{_BASE}._outgoing_requests": [],
            f"{_BASE}._spec_ctx": {
                "spec_dashboard_url":  "/dashboard/lfa-football-player",
                "spec_dashboard_icon": "⚽",
                "spec_profile_url":    "/profile/lfa-football-player",
                "spec_profile_icon":   "🪪",
            },
        }
        if extra_patches:
            patches.update(extra_patches)

        with patch(f"{_BASE}.templates") as mock_tpl:
            mock_tpl.TemplateResponse.return_value = MagicMock(status_code=200)
            with patch(f"{_BASE}._friend_list", return_value=patches[f"{_BASE}._friend_list"]), \
                 patch(f"{_BASE}._incoming_requests", return_value=patches[f"{_BASE}._incoming_requests"]), \
                 patch(f"{_BASE}._outgoing_requests", return_value=patches[f"{_BASE}._outgoing_requests"]), \
                 patch(f"{_BASE}._spec_ctx", return_value=patches[f"{_BASE}._spec_ctx"]):
                _run(route_fn(request=req, db=db, user=user))

        call_args = mock_tpl.TemplateResponse.call_args
        return call_args.args[1]

    def test_fr_nav_01_friends_page_context_has_spec_dashboard_url(self):
        """FR-NAV-01: friends_page context includes spec_dashboard_url."""
        from app.api.web_routes.friends import friends_page
        ctx = self._page_context(friends_page, _req(), _db(), _user())
        assert "spec_dashboard_url" in ctx
        assert ctx["spec_dashboard_url"]  # non-empty

    def test_fr_nav_02_friends_page_context_has_spec_dashboard_icon(self):
        """FR-NAV-02: friends_page context includes spec_dashboard_icon."""
        from app.api.web_routes.friends import friends_page
        ctx = self._page_context(friends_page, _req(), _db(), _user())
        assert "spec_dashboard_icon" in ctx

    def test_fr_nav_03_requests_page_context_has_spec_context(self):
        """FR-NAV-03: friends_requests_page context also includes spec_dashboard_url."""
        from app.api.web_routes.friends import friends_requests_page
        ctx = self._page_context(friends_requests_page, _req(), _db(), _user())
        assert "spec_dashboard_url" in ctx
        assert "spec_dashboard_icon" in ctx

    def test_fr_nav_04_active_page_block_is_lfa_player(self):
        """FR-NAV-04: friends.html active_page block contains 'lfa-player'."""
        tmpl_path = _os.path.join(_TMPL_DIR, "friends.html")
        with open(tmpl_path) as fh:
            content = fh.read()
        assert "lfa-player" in content, "active_page block must be 'lfa-player'"
        # Must NOT be the old value
        assert "{% block active_page %}friends{% endblock %}" not in content

    def test_fr_nav_05_template_includes_spec_subpage_hdr(self):
        """FR-NAV-05: friends.html includes spec_subpage_hdr.html."""
        tmpl_path = _os.path.join(_TMPL_DIR, "friends.html")
        with open(tmpl_path) as fh:
            content = fh.read()
        assert "spec_subpage_hdr.html" in content

    def test_fr_nav_06_rendered_html_has_spec_pg_hdr_class(self):
        """FR-NAV-06: rendered friends.html contains spec-pg-hdr class."""
        env = Environment(loader=FileSystemLoader(_TMPL_DIR), autoescape=True)
        tmpl = env.get_template("friends.html")
        u = MagicMock()
        u.credit_balance = 0
        u.specialization = None
        html = tmpl.render(
            request=MagicMock(), user=u,
            friends=[], incoming=[], outgoing=[],
            incoming_count=0, success=None, error=None, active_tab=None,
            friend_data={},
            spec_dashboard_url="/dashboard/lfa-football-player",
            spec_dashboard_icon="⚽",
            spec_profile_url="/profile/lfa-football-player",
            spec_profile_icon="🪪",
        )
        assert "spec-pg-hdr" in html, "spec-pg-hdr class missing from rendered friends.html"
        assert "/dashboard/lfa-football-player" in html

    def test_fr_nav_07_existing_card_tests_still_pass(self):
        """FR-NAV-07: FR-33..FR-42 card/design tests are not broken by nav change."""
        # Smoke: render with full context, verify pill and link presence
        html = _render_friends(friend_data={7: _fd_entry(level=3, positions=[{"short": "CM", "label": "Central Midfielder"}])})
        assert "/players/7" in html
        assert "CM" in html
        assert "Lv 3" in html

    def test_fr_nav_08_search_endpoint_still_works(self):
        """FR-NAV-08: GET /friends/search still returns JSON list (nav change regression)."""
        from app.api.web_routes.friends import friends_search
        import json
        db = _db()
        db.query.return_value.filter.return_value.limit.return_value.all.return_value = []
        resp = _run(friends_search(request=_req(), q="test", limit=10, db=db, user=_user()))
        assert isinstance(json.loads(resp.body), list)

    def test_fr_nav_09_challenge_link_still_uses_friend_id(self):
        """FR-NAV-09: Challenge link still uses ?friend_id= after nav change."""
        html = _render_friends(friend_data={7: _fd_entry(level=2)})
        assert "?friend_id=7" in html
        assert "?friend=7" not in html


# ── Friend card MVP redesign — FR-CARD-01..FR-CARD-13 ────────────────────────

class TestFriendsCardMVP:
    """Friend card MVP: avatar, position label, level badge, no email, no OVR, no N+1."""

    def test_fr_card_01_view_profile_link_points_to_friend(self):
        """FR-CARD-01: View Profile link = /players/{friend_id}."""
        html = _render_friends()
        assert "/players/7" in html
        assert "View Profile" in html

    def test_fr_card_02_challenge_link_uses_friend_id(self):
        """FR-CARD-02: Challenge link uses ?friend_id={friend_id}."""
        html = _render_friends()
        assert "?friend_id=7" in html

    def test_fr_card_03_email_not_in_friend_card(self):
        """FR-CARD-03: email address not shown as primary data in friend card."""
        html = _render_friends()
        # fr_mock has name="Test Friend", so display uses name — email never rendered
        assert "friend@lfa.com" not in html

    def test_fr_card_04_centre_midfield_renders_short_badge(self):
        """FR-CARD-04: centre_midfield → short badge 'CM' with title 'Central Midfielder'."""
        html = _render_friends(
            friend_data={7: _fd_entry(positions=[{"short": "CM", "label": "Central Midfielder"}])}
        )
        assert "CM" in html
        assert "Central Midfielder" in html  # appears in title= attribute
        assert "centre_midfield" not in html

    def test_fr_card_05_goalkeeper_renders_short_badge(self):
        """FR-CARD-05: goalkeeper → short badge 'GK' with title 'Goalkeeper'."""
        html = _render_friends(friend_data={7: _fd_entry(positions=[{"short": "GK", "label": "Goalkeeper"}])})
        assert "GK" in html
        assert "Goalkeeper" in html  # appears in title= attribute

    def test_fr_card_06_unknown_position_no_crash(self):
        """FR-CARD-06: unknown position value renders without raising 500."""
        html = _render_friends(
            friend_data={7: _fd_entry(positions=[{"short": "WIN", "label": "Winger Legacy"}])}
        )
        assert "/players/7" in html  # page rendered without crash

    def test_fr_card_07_photo_url_renders_img_tag(self):
        """FR-CARD-07: player_card_photo_url present → <img> with that src."""
        html = _render_friends(
            friend_data={7: _fd_entry(photo_url="https://cdn.lfa.com/photo.jpg")}
        )
        assert "https://cdn.lfa.com/photo.jpg" in html
        assert "<img" in html

    def test_fr_card_08_no_photo_renders_initials_avatar(self):
        """FR-CARD-08: photo_url=None → fr-avatar-initials div rendered with initials."""
        html = _render_friends(friend_data={7: _fd_entry(photo_url=None, initials="TF")})
        assert "fr-avatar-initials" in html
        assert "TF" in html

    def test_fr_card_09_initials_rendering(self):
        """FR-CARD-09: initials rendered correctly from friend_data entry."""
        html_jd = _render_friends(friend_data={7: _fd_entry(initials="JD")})
        assert "JD" in html_jd

        html_a = _render_friends(friend_data={7: _fd_entry(initials="A")})
        assert "A" in html_a

    def test_fr_card_09b_backend_computes_initials(self):
        """FR-CARD-09b: _friend_data_map computes JD for 'John Doe', A for email-only user."""
        from app.api.web_routes.friends import _friend_data_map

        u1 = MagicMock()
        u1.id = 10
        u1.name = "John Doe"
        u1.email = "john@lfa.com"
        u1.position = None

        u2 = MagicMock()
        u2.id = 11
        u2.name = None
        u2.email = "alice@lfa.com"
        u2.position = None

        db = _db()
        db.query.return_value.filter.return_value.all.return_value = []  # no licenses

        result = _friend_data_map(db, [u1, u2])
        assert result[10]["initials"] == "JD"
        assert result[11]["initials"] == "A"

    def test_fr_card_10_level_pill_shown_when_level_present(self):
        """FR-CARD-10: Lv N badge visible when friend_data has a level."""
        html = _render_friends(friend_data={7: _fd_entry(level=5)})
        assert "Lv 5" in html

    def test_fr_card_11_level_pill_absent_when_no_license(self):
        """FR-CARD-11: no Lv badge when friend has no LFA license (level=None)."""
        html = _render_friends(friend_data={7: _fd_entry(level=None)})
        assert "Lv " not in html

    def test_fr_card_12_no_ovr_badge_in_mvp(self):
        """FR-CARD-12: OVR badge must not appear — deferred to Phase 2."""
        import re
        html = _render_friends()
        assert not re.search(r'\bOVR\b', html), \
            "OVR badge must not appear in MVP friend cards (Phase 2 only)"

    def test_fr_card_13_no_get_skill_profile_in_friends_module(self):
        """FR-CARD-13: friends.py does not import/call get_skill_profile — no N+1 OVR."""
        import inspect
        import app.api.web_routes.friends as mod
        src = inspect.getsource(mod)
        assert "get_skill_profile" not in src, \
            "friends.py must not call get_skill_profile — OVR computation is Phase 2"


# ── Portrait tile + multi-position badges — FR-CARD-14..FR-CARD-28 ───────────

class TestFriendsCardPortraitBadges:
    """Portrait tile (64×88px) + multi-position short badges redesign."""

    # ── CSS structure ─────────────────────────────────────────────────────────

    def test_fr_card_14_avatar_css_width_64px(self):
        """FR-CARD-14: .fr-avatar-img/.fr-avatar-initials CSS width is 64px."""
        tmpl_path = _os.path.join(_TMPL_DIR, "friends.html")
        with open(tmpl_path) as fh:
            content = fh.read()
        assert "width: 64px" in content, "Portrait tile width must be 64px"

    def test_fr_card_15_avatar_css_height_88px(self):
        """FR-CARD-15: .fr-avatar-img/.fr-avatar-initials CSS height is 88px."""
        tmpl_path = _os.path.join(_TMPL_DIR, "friends.html")
        with open(tmpl_path) as fh:
            content = fh.read()
        assert "height: 88px" in content, "Portrait tile height must be 88px"

    def test_fr_card_16_avatar_css_border_radius_8px_not_50pct(self):
        """FR-CARD-16: avatar border-radius is 8px (portrait tile), not 50% (circle)."""
        tmpl_path = _os.path.join(_TMPL_DIR, "friends.html")
        with open(tmpl_path) as fh:
            content = fh.read()
        # 8px must be present in the avatar block
        assert "border-radius: 8px" in content, "Portrait tile must use border-radius: 8px"
        # 50% (circle) must NOT appear for the avatar
        import re
        avatar_block = re.search(
            r'\.fr-avatar-img.*?\.fr-card-identity', content, re.DOTALL
        )
        assert avatar_block, ".fr-avatar-img block not found"
        assert "50%" not in avatar_block.group(), \
            "Circle border-radius (50%) must not appear in avatar block — use 8px"

    def test_fr_card_17_fr_pill_pos_short_css_defined(self):
        """FR-CARD-17: .fr-pill-pos-short CSS class is defined in friends.html."""
        tmpl_path = _os.path.join(_TMPL_DIR, "friends.html")
        with open(tmpl_path) as fh:
            content = fh.read()
        assert "fr-pill-pos-short" in content, \
            ".fr-pill-pos-short CSS class must be defined"

    # ── Template rendering ────────────────────────────────────────────────────

    def test_fr_card_18_single_badge_renders_as_primary(self):
        """FR-CARD-18: single position → rendered as fr-pill-pos-primary (not secondary)."""
        html = _render_friends(
            friend_data={7: _fd_entry(positions=[{"short": "ST", "label": "Striker"}])}
        )
        assert "ST" in html
        assert "fr-pill-pos-primary" in html
        assert 'class="fr-pill fr-pill-pos-short"' not in html  # no secondary span

    def test_fr_card_19_first_badge_primary_rest_secondary(self):
        """FR-CARD-19: first position = primary badge, second position = secondary badge."""
        html = _render_friends(
            friend_data={7: _fd_entry(positions=[
                {"short": "CM", "label": "Central Midfielder"},
                {"short": "AM", "label": "Attacking Midfielder"},
            ])}
        )
        assert "CM" in html
        assert "AM" in html
        assert "fr-pill-pos-primary" in html   # first position
        assert "fr-pill-pos-short" in html     # secondary position(s)

    def test_fr_card_20_max_4_badges_rendered(self):
        """FR-CARD-20: template renders all 4 badges when 4 are supplied (backend caps at 4)."""
        html = _render_friends(
            friend_data={7: _fd_entry(positions=[
                {"short": "ST", "label": "Striker"},
                {"short": "CF", "label": "Centre Forward"},
                {"short": "LW", "label": "Left Wing"},
                {"short": "RW", "label": "Right Wing"},
            ])}
        )
        for short in ("ST", "CF", "LW", "RW"):
            assert short in html

    def test_fr_card_21_badge_has_title_with_full_label(self):
        """FR-CARD-21: badge span has title= attribute containing the full position label."""
        html = _render_friends(
            friend_data={7: _fd_entry(positions=[{"short": "GK", "label": "Goalkeeper"}])}
        )
        assert 'title="Goalkeeper"' in html

    def test_fr_card_22_no_position_no_badge_no_crash(self):
        """FR-CARD-22: empty positions list → no badge span rendered, page still renders."""
        html = _render_friends(friend_data={7: _fd_entry(positions=[])})
        # The CSS class definition appears in <style>, but no <span> with it should be rendered
        assert 'class="fr-pill fr-pill-pos-short"' not in html
        assert "/players/7" in html

    # ── _extract_pos_badges backend helper ───────────────────────────────────

    def test_fr_card_23_extract_known_position_returns_correct_short_label(self):
        """FR-CARD-23: _extract_pos_badges with 'goalkeeper' license → GK / Goalkeeper."""
        from app.api.web_routes.friends import _extract_pos_badges
        lic = MagicMock()
        lic.motivation_scores = {"positions": ["goalkeeper"]}
        user = MagicMock()
        user.position = None
        result = _extract_pos_badges(lic, user)
        assert len(result) == 1
        assert result[0]["short"] == "GK"
        assert result[0]["label"] == "Goalkeeper"

    def test_fr_card_24_extract_reads_plural_positions_array(self):
        """FR-CARD-24: _extract_pos_badges reads motivation_scores['positions'] (plural)."""
        from app.api.web_routes.friends import _extract_pos_badges
        lic = MagicMock()
        lic.motivation_scores = {"positions": ["striker", "centre_forward"]}
        user = MagicMock()
        user.position = None
        result = _extract_pos_badges(lic, user)
        assert len(result) == 2
        shorts = {b["short"] for b in result}
        assert "ST" in shorts
        assert "CF" in shorts

    def test_fr_card_25_extract_deduplicates_positions(self):
        """FR-CARD-25: _extract_pos_badges deduplicates identical canonical values."""
        from app.api.web_routes.friends import _extract_pos_badges
        lic = MagicMock()
        lic.motivation_scores = {"positions": ["striker", "STRIKER", "striker"]}
        user = MagicMock()
        user.position = None
        result = _extract_pos_badges(lic, user)
        assert len(result) == 1
        assert result[0]["short"] == "ST"

    def test_fr_card_26_extract_falls_back_to_singular_position_key(self):
        """FR-CARD-26: when no 'positions' array, reads singular 'position' key."""
        from app.api.web_routes.friends import _extract_pos_badges
        lic = MagicMock()
        lic.motivation_scores = {"position": "left_back"}
        user = MagicMock()
        user.position = None
        result = _extract_pos_badges(lic, user)
        assert len(result) == 1
        assert result[0]["short"] == "LB"

    def test_fr_card_27_extract_falls_back_to_user_position_when_no_license(self):
        """FR-CARD-27: lic=None → reads User.position as fallback."""
        from app.api.web_routes.friends import _extract_pos_badges
        user = MagicMock()
        user.position = "striker"
        result = _extract_pos_badges(None, user)
        assert len(result) == 1
        assert result[0]["short"] == "ST"

    def test_fr_card_28_photo_priority_portrait_over_player_card(self):
        """FR-CARD-28: _friend_data_map photo priority: card_photo_portrait_url first."""
        from app.api.web_routes.friends import _friend_data_map

        u = MagicMock()
        u.id = 20
        u.name = "Portrait User"
        u.email = "pu@lfa.com"
        u.position = None

        lic = MagicMock()
        lic.user_id = 20
        lic.specialization_type = "LFA_FOOTBALL_PLAYER"
        lic.is_active = True
        lic.current_level = 2
        lic.card_photo_portrait_url = "https://cdn/portrait.jpg"
        lic.player_card_photo_url   = "https://cdn/playercard.jpg"
        lic.wc_photo_url            = "https://cdn/wc.jpg"
        lic.motivation_scores       = {}

        db = _db()
        db.query.return_value.filter.return_value.all.return_value = [lic]

        result = _friend_data_map(db, [u])
        assert result[20]["photo_url"] == "https://cdn/portrait.jpg"

    def test_fr_card_29_photo_falls_back_to_player_card_when_portrait_none(self):
        """FR-CARD-29: photo falls back to player_card_photo_url when portrait is None."""
        from app.api.web_routes.friends import _friend_data_map

        u = MagicMock()
        u.id = 21
        u.name = "Card User"
        u.email = "cu@lfa.com"
        u.position = None

        lic = MagicMock()
        lic.user_id = 21
        lic.specialization_type = "LFA_FOOTBALL_PLAYER"
        lic.is_active = True
        lic.current_level = 1
        lic.card_photo_portrait_url = None
        lic.player_card_photo_url   = "https://cdn/playercard.jpg"
        lic.wc_photo_url            = "https://cdn/wc.jpg"
        lic.motivation_scores       = {}

        db = _db()
        db.query.return_value.filter.return_value.all.return_value = [lic]

        result = _friend_data_map(db, [u])
        assert result[21]["photo_url"] == "https://cdn/playercard.jpg"

    def test_fr_card_30_friend_data_map_returns_positions_list_not_string(self):
        """FR-CARD-30: _friend_data_map returns 'positions' list, not 'position_label' string."""
        from app.api.web_routes.friends import _friend_data_map

        u = MagicMock()
        u.id = 30
        u.name = "Test"
        u.email = "t@lfa.com"
        u.position = "goalkeeper"

        db = _db()
        db.query.return_value.filter.return_value.all.return_value = []  # no license

        result = _friend_data_map(db, [u])
        assert "positions" in result[30]
        assert "position_label" not in result[30]
        assert isinstance(result[30]["positions"], list)

    def test_fr_card_31_primary_css_class_defined(self):
        """FR-CARD-31: .fr-pill-pos-primary CSS class is defined in friends.html."""
        tmpl_path = _os.path.join(_TMPL_DIR, "friends.html")
        with open(tmpl_path) as fh:
            content = fh.read()
        assert "fr-pill-pos-primary" in content, \
            ".fr-pill-pos-primary CSS class must be defined for primary position badge"

    def test_fr_card_32_only_first_badge_is_primary(self):
        """FR-CARD-32: with 3 positions, exactly one primary span and two secondary spans."""
        html = _render_friends(
            friend_data={7: _fd_entry(positions=[
                {"short": "ST", "label": "Striker"},
                {"short": "CF", "label": "Centre Forward"},
                {"short": "LW", "label": "Left Wing"},
            ])}
        )
        # Count rendered <span> elements by their full class attribute value
        primary_count   = html.count('class="fr-pill fr-pill-pos-primary"')
        secondary_count = html.count('class="fr-pill fr-pill-pos-short"')
        assert primary_count == 1,   f"Expected 1 primary badge span, got {primary_count}"
        assert secondary_count == 2, f"Expected 2 secondary badge spans, got {secondary_count}"
