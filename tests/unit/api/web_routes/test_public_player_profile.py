"""PSP-01..PSP-30 — Public Player Profile page + cancel / next= social actions.

PSP-01  Anonymous user gets 200, friendship_panel state=anonymous
PSP-02  Logged-in user views own profile → state=own_profile
PSP-03  No friendship → state=none, can_add=True in context
PSP-04  Accepted friendship → state=accepted, can_remove=True
PSP-05  Pending sent → state=pending_sent, can_cancel=True
PSP-06  Pending received → state=pending_received, can_accept+can_decline=True
PSP-07  Profile user not found → 404 HTMLResponse
PSP-08  No active LFA license → 404 HTMLResponse
PSP-09  POST /friends/cancel/{id} — requester cancels pending → 303 success redirect
PSP-10  POST /friends/cancel/{id} — non-requester blocked → error redirect
PSP-11  POST /friends/cancel/{id} — non-PENDING row → error redirect
PSP-12  next= param — /players/ prefix accepted in cancel
PSP-13  next= param — /friends prefix accepted in accept
PSP-14  next= param — external URL falls back to default /friends
PSP-15  Portrait variant (fifa) → card_is_landscape=False, card_native_w=820, card_native_h=1080
PSP-16  Landscape variant (showcase) → card_is_landscape=True, card_native_w=720, card_native_h=700
PSP-17  Unknown variant → fallback portrait defaults (is_landscape=False, native_w=820)
PSP-18  Narrow variant (compact) → card_native_w=520
PSP-19  Template: psp-showcase-grid class present
PSP-20  Template: data-card-w and data-card-h attributes present
PSP-21  Template: Left rail Gallery placeholder present
PSP-22  Template: Right rail Highlight Video placeholder present
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi.responses import HTMLResponse, RedirectResponse

from app.models.friendship import FriendshipStatus, get_friendship_panel_ctx

_BASE_PP    = "app.api.web_routes.public_player"
_BASE_FR    = "app.api.web_routes.friends"
_SKILL_SVC  = "app.services.skill_progression_service"
_FRIEND_MOD = "app.models.friendship"
_DRAFT_SVC  = "app.services.card_draft_service.CardDraftService"


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _req():
    m = MagicMock()
    m.client = MagicMock()
    m.client.host = "127.0.0.1"
    return m


def _user(uid=1, name="Alice Smith", email="alice@lfa.com"):
    u = MagicMock()
    u.id           = uid
    u.name         = name
    u.email        = email
    u.nickname     = None
    u.nationality  = "HUN"
    u.is_active    = True
    u.date_of_birth = None
    return u


def _license(user_id=1, completed=True):
    lic = MagicMock()
    lic.user_id                = user_id
    lic.specialization_type    = "LFA_FOOTBALL_PLAYER"
    lic.is_active              = True
    lic.onboarding_completed   = completed
    lic.player_card_photo_url  = None
    lic.motivation_scores      = {"position": "STRIKER"}
    return lic


def _friendship_row(fid=10, requester_id=1, addressee_id=2,
                    status=FriendshipStatus.PENDING):
    f = MagicMock()
    f.id           = fid
    f.requester_id = requester_id
    f.addressee_id = addressee_id
    f.status       = status
    return f


def _profile_db(user=None, license=None):
    """DB mock for the public_player_profile route.

    Query order:
      1. db.query(User).filter(...).first()      → user
      2. db.query(UserLicense).filter(...).first() → license
      3. db.query(TournamentParticipation, Semester).join(...).filter(...).order_by(...).limit(...).all()
         → [] (no events)
    """
    db = MagicMock()
    # Queries 1 + 2: .filter().first()
    db.query.return_value.filter.return_value.first.side_effect = [user, license]
    # Query 3: participations chain uses .join().filter().order_by().limit().all()
    db.query.return_value.join.return_value.filter.return_value \
        .order_by.return_value.limit.return_value.all.return_value = []
    return db


_PANEL_ANONYMOUS  = {"state": "anonymous",        "friendship_id": None, "can_add": False, "can_cancel": False, "can_accept": False, "can_decline": False, "can_remove": False}
_PANEL_OWN        = {"state": "own_profile",       "friendship_id": None, "can_add": False, "can_cancel": False, "can_accept": False, "can_decline": False, "can_remove": False}
_PANEL_NONE       = {"state": "none",              "friendship_id": None, "can_add": True,  "can_cancel": False, "can_accept": False, "can_decline": False, "can_remove": False}
_PANEL_ACCEPTED   = {"state": "accepted",          "friendship_id": 10,   "can_add": False, "can_cancel": False, "can_accept": False, "can_decline": False, "can_remove": True}
_PANEL_SENT       = {"state": "pending_sent",      "friendship_id": 10,   "can_add": False, "can_cancel": True,  "can_accept": False, "can_decline": False, "can_remove": False}
_PANEL_RECEIVED   = {"state": "pending_received",  "friendship_id": 10,   "can_add": False, "can_cancel": False, "can_accept": True,  "can_decline": True,  "can_remove": False}


# ── PSP-01..PSP-08: public_player_profile route ───────────────────────────────

class TestPublicPlayerProfile:

    def _call(self, user=None, license=None, current_user=None, panel=None,
              draft_variant="fifa"):
        from app.api.web_routes.public_player import public_player_profile
        profile_user = user or _user(uid=2)
        lic          = license or _license(user_id=2)
        db           = _profile_db(user=profile_user, license=lic)
        _panel       = panel if panel is not None else _PANEL_NONE
        _draft       = MagicMock()
        _draft.published_variant = draft_variant
        with patch(f"{_BASE_PP}.templates") as mock_tmpl, \
             patch(f"{_SKILL_SVC}.get_skill_profile", return_value={"average_level": 65.0, "skills": {}, "total_tournaments": 3}), \
             patch(f"{_FRIEND_MOD}.get_friendship_panel_ctx", return_value=_panel), \
             patch(f"{_DRAFT_SVC}.get_player_card_draft", return_value=_draft):
            result = _run(public_player_profile(
                request=_req(), user_id=profile_user.id, db=db, current_user=current_user,
            ))
            ctx = mock_tmpl.TemplateResponse.call_args
        return result, ctx

    # PSP-01 — anonymous user: friendship_panel state=anonymous in context
    def test_psp01_anonymous_user_gets_anonymous_panel(self):
        _, ctx = self._call(current_user=None, panel=_PANEL_ANONYMOUS)
        context = ctx[0][2] if ctx else ctx.args[2]
        assert context["friendship_panel"]["state"] == "anonymous"
        assert context["current_user"] is None

    # PSP-02 — own profile: state=own_profile in context
    def test_psp02_own_profile_state(self):
        viewer = _user(uid=2)
        _, ctx = self._call(current_user=viewer, panel=_PANEL_OWN)
        context = ctx[0][2] if ctx else ctx.args[2]
        assert context["friendship_panel"]["state"] == "own_profile"

    # PSP-03 — no friendship: state=none, can_add=True
    def test_psp03_no_friendship_can_add(self):
        viewer = _user(uid=9)
        _, ctx = self._call(current_user=viewer, panel=_PANEL_NONE)
        context = ctx[0][2] if ctx else ctx.args[2]
        assert context["friendship_panel"]["state"] == "none"
        assert context["friendship_panel"]["can_add"] is True

    # PSP-04 — accepted: can_remove=True
    def test_psp04_accepted_friend_can_remove(self):
        viewer = _user(uid=9)
        _, ctx = self._call(current_user=viewer, panel=_PANEL_ACCEPTED)
        context = ctx[0][2] if ctx else ctx.args[2]
        assert context["friendship_panel"]["state"] == "accepted"
        assert context["friendship_panel"]["can_remove"] is True

    # PSP-05 — pending sent: can_cancel=True
    def test_psp05_pending_sent_can_cancel(self):
        viewer = _user(uid=9)
        _, ctx = self._call(current_user=viewer, panel=_PANEL_SENT)
        context = ctx[0][2] if ctx else ctx.args[2]
        assert context["friendship_panel"]["state"] == "pending_sent"
        assert context["friendship_panel"]["can_cancel"] is True

    # PSP-06 — pending received: can_accept + can_decline
    def test_psp06_pending_received_accept_decline(self):
        viewer = _user(uid=9)
        _, ctx = self._call(current_user=viewer, panel=_PANEL_RECEIVED)
        context = ctx[0][2] if ctx else ctx.args[2]
        assert context["friendship_panel"]["state"] == "pending_received"
        assert context["friendship_panel"]["can_accept"] is True
        assert context["friendship_panel"]["can_decline"] is True

    # PSP-07 — user not found → 404
    def test_psp07_user_not_found_returns_404(self):
        from app.api.web_routes.public_player import public_player_profile
        db = _profile_db(user=None, license=None)
        result = _run(public_player_profile(
            request=_req(), user_id=999, db=db, current_user=None,
        ))
        assert isinstance(result, HTMLResponse)
        assert result.status_code == 404

    # PSP-08 — no license → 404
    def test_psp08_no_license_returns_404(self):
        from app.api.web_routes.public_player import public_player_profile
        db = _profile_db(user=_user(uid=2), license=None)
        # first().side_effect has user at [0], None at [1] → license missing
        result = _run(public_player_profile(
            request=_req(), user_id=2, db=db, current_user=None,
        ))
        assert isinstance(result, HTMLResponse)
        assert result.status_code == 404


# ── PSP-09..PSP-11: POST /friends/cancel/{friendship_id} ─────────────────────

class TestCancelFriendRequest:

    def _db_with_row(self, row):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = row
        return db

    # PSP-09 — requester cancels PENDING → success redirect
    def test_psp09_requester_can_cancel_pending(self):
        from app.api.web_routes.friends import cancel_friend_request
        user = _user(uid=1)
        row  = _friendship_row(fid=10, requester_id=1, addressee_id=2,
                               status=FriendshipStatus.PENDING)
        db   = self._db_with_row(row)
        result = _run(cancel_friend_request(friendship_id=10, next=None, db=db, user=user))
        assert isinstance(result, RedirectResponse)
        assert "success=request_cancelled" in result.headers["location"]
        db.delete.assert_called_once_with(row)
        db.commit.assert_called_once()

    # PSP-10 — non-requester blocked → error redirect
    def test_psp10_non_requester_blocked(self):
        from app.api.web_routes.friends import cancel_friend_request
        user = _user(uid=3)   # addressee, not requester
        row  = _friendship_row(fid=10, requester_id=1, addressee_id=2,
                               status=FriendshipStatus.PENDING)
        db   = self._db_with_row(row)
        result = _run(cancel_friend_request(friendship_id=10, next=None, db=db, user=user))
        assert isinstance(result, RedirectResponse)
        assert "error=not_found" in result.headers["location"]
        db.delete.assert_not_called()

    # PSP-11 — non-PENDING row → error redirect
    def test_psp11_non_pending_blocked(self):
        from app.api.web_routes.friends import cancel_friend_request
        user = _user(uid=1)
        row  = _friendship_row(fid=10, requester_id=1, addressee_id=2,
                               status=FriendshipStatus.ACCEPTED)
        db   = self._db_with_row(row)
        result = _run(cancel_friend_request(friendship_id=10, next=None, db=db, user=user))
        assert isinstance(result, RedirectResponse)
        assert "error=not_pending" in result.headers["location"]
        db.delete.assert_not_called()


# ── PSP-12..PSP-14: _safe_next whitelist ─────────────────────────────────────

class TestSafeNext:
    """_safe_next whitelist validation via cancel + accept routes."""

    def _db_cancel(self, row):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = row
        return db

    # PSP-12 — /players/ prefix is accepted
    def test_psp12_players_prefix_accepted(self):
        from app.api.web_routes.friends import cancel_friend_request
        user = _user(uid=1)
        row  = _friendship_row(fid=10, requester_id=1, addressee_id=2,
                               status=FriendshipStatus.PENDING)
        db   = self._db_cancel(row)
        result = _run(cancel_friend_request(
            friendship_id=10, next="/players/2", db=db, user=user,
        ))
        assert result.headers["location"] == "/players/2"

    # PSP-13 — /friends prefix is accepted
    def test_psp13_friends_prefix_accepted(self):
        from app.api.web_routes.friends import accept_friend_request
        user = _user(uid=2)
        row  = _friendship_row(fid=10, requester_id=1, addressee_id=2,
                               status=FriendshipStatus.PENDING)
        db   = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = row
        with patch(f"{_BASE_FR}.notification_service"):
            result = _run(accept_friend_request(
                friendship_id=10, next="/friends/requests", db=db, user=user,
            ))
        assert result.headers["location"] == "/friends/requests"

    # PSP-14 — external URL falls back to default
    def test_psp14_external_url_rejected(self):
        from app.api.web_routes.friends import cancel_friend_request
        user = _user(uid=1)
        row  = _friendship_row(fid=10, requester_id=1, addressee_id=2,
                               status=FriendshipStatus.PENDING)
        db   = self._db_cancel(row)
        result = _run(cancel_friend_request(
            friendship_id=10, next="https://evil.com/steal", db=db, user=user,
        ))
        # External URL rejected — falls back to /friends?success=request_cancelled
        loc = result.headers["location"]
        assert "evil.com" not in loc
        assert loc.startswith("/friends")


# ── PSP model: get_friendship_panel_ctx helper ────────────────────────────────

class TestGetFriendshipPanelCtx:
    """Verify all states returned by get_friendship_panel_ctx()."""

    def _db_row(self, row=None):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = row
        return db

    def test_anonymous_state(self):
        ctx = get_friendship_panel_ctx(MagicMock(), None, 2)
        assert ctx["state"] == "anonymous"
        assert ctx["can_add"] is False

    def test_own_profile_state(self):
        ctx = get_friendship_panel_ctx(MagicMock(), 1, 1)
        assert ctx["state"] == "own_profile"

    def test_no_friendship_row(self):
        db = self._db_row(None)
        ctx = get_friendship_panel_ctx(db, 1, 2)
        assert ctx["state"] == "none"
        assert ctx["can_add"] is True

    def test_accepted_state(self):
        row = _friendship_row(fid=5, requester_id=1, addressee_id=2,
                              status=FriendshipStatus.ACCEPTED)
        db = self._db_row(row)
        ctx = get_friendship_panel_ctx(db, 1, 2)
        assert ctx["state"] == "accepted"
        assert ctx["can_remove"] is True
        assert ctx["friendship_id"] == 5

    def test_pending_sent_state(self):
        row = _friendship_row(fid=7, requester_id=1, addressee_id=2,
                              status=FriendshipStatus.PENDING)
        db = self._db_row(row)
        ctx = get_friendship_panel_ctx(db, 1, 2)
        assert ctx["state"] == "pending_sent"
        assert ctx["can_cancel"] is True
        assert ctx["can_accept"] is False

    def test_pending_received_state(self):
        row = _friendship_row(fid=7, requester_id=2, addressee_id=1,
                              status=FriendshipStatus.PENDING)
        db = self._db_row(row)
        ctx = get_friendship_panel_ctx(db, 1, 2)
        assert ctx["state"] == "pending_received"
        assert ctx["can_accept"] is True
        assert ctx["can_decline"] is True

    def test_declined_allows_re_add(self):
        row = _friendship_row(fid=3, requester_id=2, addressee_id=1,
                              status=FriendshipStatus.DECLINED)
        db = self._db_row(row)
        ctx = get_friendship_panel_ctx(db, 1, 2)
        assert ctx["state"] == "declined"
        assert ctx["can_add"] is True

    def test_blocked_no_actions(self):
        row = _friendship_row(fid=3, requester_id=2, addressee_id=1,
                              status=FriendshipStatus.BLOCKED)
        db = self._db_row(row)
        ctx = get_friendship_panel_ctx(db, 1, 2)
        assert ctx["state"] == "blocked"
        assert ctx["can_add"] is False
        assert ctx["can_cancel"] is False
        assert ctx["can_remove"] is False


# ── PSP-15..PSP-18: card variant context ──────────────────────────────────────

class TestProfileVariantContext:
    """Variant-aware card sizing context injected by GET /players/{user_id}."""

    def _call_variant(self, draft_variant, license_variant=None):
        from app.api.web_routes.public_player import public_player_profile
        profile_user = _user(uid=2)
        lic = _license(user_id=2)
        lic.published_card_variant = license_variant
        db  = _profile_db(user=profile_user, license=lic)
        _draft = MagicMock()
        _draft.published_variant = draft_variant
        with patch(f"{_BASE_PP}.templates") as mock_tmpl, \
             patch(f"{_SKILL_SVC}.get_skill_profile", return_value={"average_level": 65.0, "skills": {}, "total_tournaments": 3}), \
             patch(f"{_FRIEND_MOD}.get_friendship_panel_ctx", return_value=_PANEL_NONE), \
             patch(f"{_DRAFT_SVC}.get_player_card_draft", return_value=_draft):
            _run(public_player_profile(request=_req(), user_id=2, db=db, current_user=None))
            ctx = mock_tmpl.TemplateResponse.call_args
        return ctx[0][2] if ctx else ctx.args[2]

    # PSP-15 — portrait (fifa): is_landscape=False, native_w=820, native_h=1080
    def test_psp15_portrait_fifa_context(self):
        ctx = self._call_variant("fifa")
        assert ctx["card_variant_id"] == "fifa"
        assert ctx["card_is_landscape"] is False
        assert ctx["card_native_w"] == 820
        assert ctx["card_native_h"] == 1080

    # PSP-16 — landscape (showcase): is_landscape=True, native_w=720, native_h=700
    def test_psp16_showcase_landscape_context(self):
        ctx = self._call_variant("showcase")
        assert ctx["card_variant_id"] == "showcase"
        assert ctx["card_is_landscape"] is True
        assert ctx["card_native_w"] == 720
        assert ctx["card_native_h"] == 700

    # PSP-17 — unknown variant: fallback to portrait defaults
    def test_psp17_unknown_variant_fallback_portrait(self):
        ctx = self._call_variant("unknown_xyz")
        assert ctx["card_is_landscape"] is False
        assert ctx["card_native_w"] == 820
        assert ctx["card_native_h"] == 1080

    # PSP-18 — narrow variant (compact): native_w=520
    def test_psp18_compact_narrow_width(self):
        ctx = self._call_variant("compact")
        assert ctx["card_is_landscape"] is False
        assert ctx["card_native_w"] == 520
        assert ctx["card_native_h"] == 1080


# ── PSP-19..PSP-22: template structural assertions ────────────────────────────

import os as _os

class TestProfileLayoutTemplate:
    """Structural string assertions on the rendered template source."""

    _TEMPLATE_PATH = _os.path.normpath(_os.path.join(
        _os.path.dirname(__file__),
        "..", "..", "..", "..",
        "app", "templates", "public", "player_profile.html",
    ))

    def _html(self):
        with open(self._TEMPLATE_PATH, encoding="utf-8") as f:
            return f.read()

    # PSP-19 — grid class present
    def test_psp19_showcase_grid_class(self):
        assert "psp-showcase-grid" in self._html()

    # PSP-20 — data-card-w and data-card-h template variables present
    def test_psp20_card_data_attrs(self):
        html = self._html()
        assert 'data-card-w="{{ card_native_w }}"' in html
        assert 'data-card-h="{{ card_native_h }}"' in html

    # PSP-21 — left rail Gallery placeholder present
    def test_psp21_left_gallery_placeholder(self):
        html = self._html()
        assert "Gallery" in html
        assert "Coming Soon" in html

    # PSP-22 — right rail Highlight Video placeholder present
    def test_psp22_right_highlight_video_placeholder(self):
        assert "Highlight Video" in self._html()

    # PSP-23 — card route sets Cache-Control: no-store (structural: route source has header)
    def test_psp23_card_route_cache_control_no_store(self):
        route_path = _os.path.normpath(_os.path.join(
            _os.path.dirname(__file__),
            "..", "..", "..", "..",
            "app", "api", "web_routes", "public_player.py",
        ))
        with open(route_path, encoding="utf-8") as f:
            src = f.read()
        assert "Cache-Control" in src
        assert "no-store" in src
        assert "Pragma" in src
        assert "no-cache" in src

    # PSP-24 — profile template iframe src has versioned ?v= conditional
    def test_psp24_iframe_src_version_param_conditional(self):
        html = self._html()
        assert "{% if card_published_v %}?v={{ card_published_v }}{% endif %}" in html

    # PSP-28 — template conditional: ?v= appears only when card_published_v is truthy
    def test_psp28_iframe_no_hardcoded_v_param(self):
        html = self._html()
        # Must be conditional, not a hardcoded bare ?v= suffix
        assert '"/card?v=' not in html
        assert "{% if card_published_v %}" in html


# ── PSP-25..PSP-27, PSP-29..PSP-30: card_published_v context ─────────────────

class TestCardPublishedVersion:
    """card_published_v: unix timestamp from card_drafts.published_at."""

    def _call_with_published_at(self, published_at):
        from app.api.web_routes.public_player import public_player_profile
        profile_user = _user(uid=2)
        lic = _license(user_id=2)
        lic.published_card_variant = None
        db  = _profile_db(user=profile_user, license=lic)
        _draft = MagicMock()
        _draft.published_variant = "fifa"
        _draft.published_at      = published_at
        with patch(f"{_BASE_PP}.templates") as mock_tmpl, \
             patch(f"{_SKILL_SVC}.get_skill_profile", return_value={"average_level": 65.0, "skills": {}, "total_tournaments": 3}), \
             patch(f"{_FRIEND_MOD}.get_friendship_panel_ctx", return_value=_PANEL_NONE), \
             patch(f"{_DRAFT_SVC}.get_player_card_draft", return_value=_draft):
            _run(public_player_profile(request=_req(), user_id=2, db=db, current_user=None))
            ctx = mock_tmpl.TemplateResponse.call_args
        return ctx[0][2] if ctx else ctx.args[2]

    # PSP-25 — card_published_v > 0 when published_at is set
    def test_psp25_published_at_gives_positive_version(self):
        from datetime import datetime, timezone
        published_at = datetime(2026, 5, 24, 17, 18, 21, tzinfo=timezone.utc)
        ctx = self._call_with_published_at(published_at)
        assert ctx["card_published_v"] == int(published_at.timestamp())
        assert ctx["card_published_v"] > 0

    # PSP-26 — card_published_v == 0 when published_at is None
    def test_psp26_no_published_at_gives_zero(self):
        ctx = self._call_with_published_at(None)
        assert ctx["card_published_v"] == 0

    # PSP-27 — preview= param on card route: source still contains preview handling
    def test_psp27_card_route_preview_param_present(self):
        route_path = _os.path.normpath(_os.path.join(
            _os.path.dirname(__file__),
            "..", "..", "..", "..",
            "app", "api", "web_routes", "public_player.py",
        ))
        with open(route_path, encoding="utf-8") as f:
            src = f.read()
        assert "preview" in src
        assert "published_variant" in src

    # PSP-29 — published_variant from card_drafts takes priority over license value
    def test_psp29_draft_published_variant_wins_over_license(self):
        from app.api.web_routes.public_player import public_player_profile
        profile_user = _user(uid=2)
        lic = _license(user_id=2)
        lic.published_card_variant = "compact"   # legacy fallback
        db  = _profile_db(user=profile_user, license=lic)
        _draft = MagicMock()
        _draft.published_variant = "atlas"       # card_drafts primary source
        _draft.published_at      = None
        with patch(f"{_BASE_PP}.templates") as mock_tmpl, \
             patch(f"{_SKILL_SVC}.get_skill_profile", return_value={"average_level": 65.0, "skills": {}, "total_tournaments": 3}), \
             patch(f"{_FRIEND_MOD}.get_friendship_panel_ctx", return_value=_PANEL_NONE), \
             patch(f"{_DRAFT_SVC}.get_player_card_draft", return_value=_draft):
            _run(public_player_profile(request=_req(), user_id=2, db=db, current_user=None))
            ctx = mock_tmpl.TemplateResponse.call_args[0][2]
        assert ctx["card_variant_id"] == "atlas"

    # PSP-30 — showcase_bg also gets landscape h=700 (consistent with showcase)
    def test_psp30_showcase_bg_landscape_native_h_700(self):
        from app.api.web_routes.public_player import public_player_profile
        profile_user = _user(uid=2)
        lic = _license(user_id=2)
        lic.published_card_variant = None
        db  = _profile_db(user=profile_user, license=lic)
        _draft = MagicMock()
        _draft.published_variant = "showcase_bg"
        _draft.published_at      = None
        with patch(f"{_BASE_PP}.templates") as mock_tmpl, \
             patch(f"{_SKILL_SVC}.get_skill_profile", return_value={"average_level": 65.0, "skills": {}, "total_tournaments": 3}), \
             patch(f"{_FRIEND_MOD}.get_friendship_panel_ctx", return_value=_PANEL_NONE), \
             patch(f"{_DRAFT_SVC}.get_player_card_draft", return_value=_draft):
            _run(public_player_profile(request=_req(), user_id=2, db=db, current_user=None))
            ctx = mock_tmpl.TemplateResponse.call_args[0][2]
        assert ctx["card_is_landscape"] is True
        assert ctx["card_native_h"] == 700
        assert ctx["card_native_w"] == 720
