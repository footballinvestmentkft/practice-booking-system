"""
Unit tests for the card editor route context — Phase 2 platform hardcoding refactor.

Verifies that the dashboard card editor route passes the correct authoritative
context keys to the template, eliminating hardcoded JS and platform button HTML.

CE-CTX-01: canvas_sizes keys in context match CANVAS_SIZES from card_constants
CE-CTX-02: platforms list length matches CARD_EDITOR_PLATFORM_IDS length
CE-CTX-03: platforms list includes facebook_post (closes previous UI gap)
CE-CTX-04: platforms order matches CARD_EDITOR_PLATFORM_IDS order
"""
import asyncio
from unittest.mock import MagicMock, patch

from app.api.web_routes.dashboard import lfa_player_card_editor
from app.models.user import UserRole
from app.services.card_constants import CANVAS_SIZES, CARD_EDITOR_PLATFORM_IDS


_BASE = "app.api.web_routes.dashboard"


def _run(coro):
    return asyncio.run(coro)


def _req():
    return MagicMock()


def _student(uid=7):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.STUDENT
    u.name = "Test Player"
    u.email = "player@test.com"
    u.credit_balance = 100
    return u


def _license(uid=7):
    lic = MagicMock()
    lic.user_id = uid
    lic.specialization_type = "LFA_FOOTBALL_PLAYER"
    lic.onboarding_completed = True
    lic.football_skills = {"passing": 60}
    lic.card_theme = "default"
    lic.card_variant = "fifa"
    lic.public_card_platform = None
    lic.player_card_photo_url = None
    return lic


def _mock_db(license_return):
    db = MagicMock()
    query_mock = MagicMock()
    filter_mock = MagicMock()
    filter_mock.first.return_value = license_return
    query_mock.filter.return_value = filter_mock
    db.query.return_value = query_mock
    return db


def _call_editor(user=None, lic=None):
    """Call the card editor route and return the TemplateResponse call args."""
    if user is None:
        user = _student()
    if lic is None:
        lic = _license(uid=user.id)
    db = _mock_db(license_return=lic)

    with patch(f"{_BASE}.templates") as mock_tmpl, \
         patch(f"{_BASE}.SemesterEnrollment") as mock_se:
        mock_tmpl.TemplateResponse.return_value = MagicMock()
        # Simulate no enrollment so effective_onboarding uses onboarding_completed
        mock_se_q = MagicMock()
        mock_se_q.filter.return_value.first.return_value = None
        mock_se.id = MagicMock()

        with patch(f"{_BASE}.db") if False else patch(f"{_BASE}.UserLicense") as _mock_lic_cls:
            _mock_lic_cls.user_id = MagicMock()
            # Use the pre-built db mock directly
            _run(lfa_player_card_editor(_req(), db=db, user=user))

        tmpl, ctx = mock_tmpl.TemplateResponse.call_args.args
        return tmpl, ctx


class TestCardEditorContext:
    """Card editor route must pass authoritative context keys to template."""

    def _get_ctx(self):
        user = _student()
        lic = _license(uid=user.id)
        db = _mock_db(license_return=lic)

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            with patch(f"{_BASE}.SemesterEnrollment") as mock_se:
                # Simulate no semester enrollment
                inner = MagicMock()
                inner.filter.return_value.first.return_value = None
                mock_se.id = inner
                db.query.return_value.filter.return_value.first.side_effect = [
                    lic,   # license query
                    None,  # enrollment query
                ]
                _run(lfa_player_card_editor(_req(), db=db, user=user))
            _, ctx = mock_tmpl.TemplateResponse.call_args.args
        return ctx

    def test_ce_ctx01_canvas_sizes_keys_match_card_constants(self):
        """canvas_sizes context keys must exactly match CANVAS_SIZES from card_constants."""
        ctx = self._get_ctx()
        assert "canvas_sizes" in ctx, (
            "card editor route must pass 'canvas_sizes' to template"
        )
        assert set(ctx["canvas_sizes"].keys()) == set(CANVAS_SIZES.keys()), (
            f"canvas_sizes keys mismatch.\n"
            f"Context has:   {sorted(ctx['canvas_sizes'].keys())}\n"
            f"Expected:      {sorted(CANVAS_SIZES.keys())}"
        )

    def test_ce_ctx02_platforms_length_matches_card_editor_platform_ids(self):
        """platforms list length must equal CARD_EDITOR_PLATFORM_IDS length."""
        ctx = self._get_ctx()
        assert "platforms" in ctx, (
            "card editor route must pass 'platforms' to template"
        )
        assert len(ctx["platforms"]) == len(CARD_EDITOR_PLATFORM_IDS), (
            f"Expected {len(CARD_EDITOR_PLATFORM_IDS)} platforms, "
            f"got {len(ctx['platforms'])}"
        )

    def test_ce_ctx03_platforms_includes_facebook_post(self):
        """facebook_post must appear in platforms — closes the dashboard editor UI gap.

        Previously, facebook_post was absent from the hardcoded platform picker HTML
        and the hardcoded JS _CANVAS_SIZES object. This test ensures the gap stays closed.
        """
        ctx = self._get_ctx()
        platform_ids = [p["id"] for p in ctx["platforms"]]
        assert "facebook_post" in platform_ids, (
            "facebook_post is missing from editor platforms context. "
            "This is a functional gap: users cannot select the Facebook Post format. "
            "Ensure CARD_EDITOR_PLATFORM_IDS includes 'facebook_post'."
        )

    def test_ce_ctx04_platforms_order_matches_card_editor_platform_ids(self):
        """platforms order must match CARD_EDITOR_PLATFORM_IDS order."""
        ctx = self._get_ctx()
        actual_ids = [p["id"] for p in ctx["platforms"]]
        assert actual_ids == list(CARD_EDITOR_PLATFORM_IDS), (
            f"Platform order mismatch.\n"
            f"Expected: {list(CARD_EDITOR_PLATFORM_IDS)}\n"
            f"Actual:   {actual_ids}"
        )
