"""Export guard tests.

EG-01  Player Card premium export → 403 without ownership
EG-02  Player Card / fclassic export → allowed with ownership row (CDO required)
EG-03  Player Card premium export → allowed after ownership
EG-04  Welcome Card export, no ownership → 403 (always enforced, no feature flag)
EG-05  Welcome Card export, no ownership → 403
EG-06  Welcome Card export, owned → allowed
EG-07  Challenge Card export, no ownership → 403 (always enforced, no feature flag)
EG-08  Challenge Card export, no ownership → 403
EG-09  Challenge Card export, owned → allowed
EG-10  Admin bypass — admin may export without ownership
EG-11  Export route does NOT call CreditService.deduct
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.responses import Response

_PUB   = "app.api.web_routes.public_player"
_PROF  = "app.api.web_routes.profile"
_VTC   = "app.api.web_routes.vt_challenges"
_SVC   = "app.services.card_design_service"
_CFG   = "app.config"       # settings is imported locally inside the route functions
_AUTH  = "app.core.auth"    # create_challenge_render_token is imported locally


def _run(coro):
    return asyncio.run(coro)


def _make_user(role="STUDENT"):
    from app.models.user import UserRole
    u = MagicMock()
    u.id = 1
    u.role = UserRole.STUDENT if role == "STUDENT" else UserRole.ADMIN
    u.email = "test@test.com"
    return u


def _make_db():
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.filter_by.return_value = q
    q.first.return_value = None
    db.query.return_value = q
    return db


def _make_request(path="/players/1/card/export"):
    r = MagicMock()
    r.url.path = path
    r.client.host = "127.0.0.1"
    r.query_params.get.return_value = None
    return r


def _make_license(variant="compact"):
    lic = MagicMock()
    lic.card_variant = variant
    lic.card_theme = "default"
    lic.published_card_variant = variant
    lic.published_card_theme = "default"
    lic.unlocked_card_variants = []
    return lic


# ── EG-01..03: Player Card export guard ──────────────────────────────────────

class TestPlayerCardExportGuard:

    def _call_export(self, db, user, license_variant="compact", is_accessible=False):
        from app.api.web_routes.public_player import export_player_card

        request = _make_request()
        fake_license = _make_license(license_variant)
        fake_target  = MagicMock()
        fake_target.id = user.id
        fake_draft = MagicMock()
        fake_draft.published_variant = None  # falls back to license.card_variant

        db.query.return_value.filter.return_value.first.side_effect = [
            fake_target, fake_license, fake_draft
        ]

        with patch(f"{_PUB}._export_svc") as mock_svc, \
             patch(f"{_SVC}.is_design_accessible", return_value=is_accessible), \
             patch(f"{_PUB}._export_svc.CANVAS_SIZES", {"instagram_square": (1080, 1080)}), \
             patch(f"{_PUB}._export_svc.check_export_rate_limit", return_value=True), \
             patch(f"{_PUB}._EXPORT_FORMAT_BUCKETS", {"instagram_square": "square"}), \
             patch(f"{_PUB}._get_supported_buckets", return_value=("square",)):
            mock_svc.CANVAS_SIZES = {"instagram_square": (1080, 1080)}
            mock_svc.check_export_rate_limit.return_value = True
            mock_svc._sync_take_screenshot.return_value = b"PNG"
            return _run(export_player_card(
                request=request,
                user_id=user.id,
                platform="instagram_square",
                theme=None,
                db=db,
                current_user=user,
            ))

    def test_eg01_premium_403_without_ownership(self):
        """EG-01: player premium export → 403 if not owned."""
        db = _make_db()
        user = _make_user()

        with pytest.raises(HTTPException) as exc_info:
            self._call_export(db, user, license_variant="compact", is_accessible=False)
        assert exc_info.value.status_code == 403

    def test_eg02_fifa_allowed_with_ownership(self):
        """EG-02: player_card/fclassic with CDO ownership row → no 403."""
        db = _make_db()
        user = _make_user()

        result = self._call_export(db, user, license_variant="fclassic", is_accessible=True)
        assert hasattr(result, "body") or result is not None  # got a response, not 403

    def test_eg03_premium_allowed_with_ownership(self):
        """EG-03: player premium export → 200 after ownership granted."""
        db = _make_db()
        user = _make_user()

        result = self._call_export(db, user, license_variant="compact", is_accessible=True)
        assert result is not None


# ── EG-04..06: Welcome Card export guard ─────────────────────────────────────

class TestWelcomeCardExportGuard:

    def _call_export(self, db, user, is_accessible=False):
        from app.api.web_routes.profile import export_onboarding_welcome_card

        request = _make_request("/profile/onboarding-card/export")
        fake_license = MagicMock()
        fake_license.onboarding_completed = True

        db.query.return_value.filter.return_value.first.return_value = fake_license
        db.query.return_value.filter_by.return_value.first.return_value = fake_license

        with patch(f"{_SVC}.is_design_accessible", return_value=is_accessible), \
             patch(f"{_PROF}._export_svc") as mock_svc, \
             patch(f"{_PROF}._check_welcome_card_auth", return_value=None), \
             patch(f"{_PROF}._create_render_token", return_value="tok"):
            mock_svc.APP_INTERNAL_PORT = 8000
            mock_svc.CANVAS_SIZES = {"instagram_square": (1080, 1080)}
            mock_svc.check_export_rate_limit.return_value = True
            mock_svc._sync_take_screenshot.return_value = b"PNG"
            return _run(export_onboarding_welcome_card(
                request=request,
                platform="instagram_square",
                use_nickname=False,
                db=db,
                user=user,
            ))

    def test_eg04_no_ownership_403(self):
        """EG-04: WC export, no ownership → 403 (always enforced, no feature flag)."""
        db = _make_db()
        user = _make_user()
        with pytest.raises(HTTPException) as exc_info:
            self._call_export(db, user, is_accessible=False)
        assert exc_info.value.status_code == 403

    def test_eg05_no_ownership_403(self):
        """EG-05: WC export without ownership → 403."""
        db = _make_db()
        user = _make_user()
        with pytest.raises(HTTPException) as exc_info:
            self._call_export(db, user, is_accessible=False)
        assert exc_info.value.status_code == 403

    def test_eg06_owned_allowed(self):
        """EG-06: WC export, owned → allowed."""
        db = _make_db()
        user = _make_user()
        result = self._call_export(db, user, is_accessible=True)
        assert result is not None


# ── EG-07..09: Challenge Card export guard ────────────────────────────────────

class TestChallengeCardExportGuard:

    def _call_export(self, db, user, is_accessible=False):
        from app.api.web_routes.vt_challenges import challenge_card_export

        request = _make_request("/challenges/1/card/export")

        fake_ch = MagicMock()
        fake_ch.id = 1
        fake_ch.challenger_id = user.id
        fake_ch.challenged_id = 2
        fake_ch.challenger_attempt_id = None
        fake_ch.challenged_attempt_id = None

        db.query.return_value.filter.return_value.first.return_value = fake_ch

        with patch(f"{_SVC}.is_design_accessible", return_value=is_accessible), \
             patch(f"{_VTC}._export_svc") as mock_svc, \
             patch(f"{_VTC}.validate_challenge_card_phase", return_value=None), \
             patch(f"{_AUTH}.create_challenge_render_token", return_value="tok"):
            mock_svc.check_export_rate_limit.return_value = True
            mock_svc._sync_take_screenshot.return_value = b"PNG"

            with patch(f"{_VTC}.CHALLENGE_CARD_PLATFORMS",
                       {"challenge_post_16_9", "challenge_story_9_16"}), \
                 patch(f"{_VTC}.VALID_CHALLENGE_CARD_PHASES",
                       {"completed_score_win", "skill_delta_result"}):
                return _run(challenge_card_export(
                    challenge_id=1,
                    request=request,
                    platform="challenge_post_16_9",
                    phase="completed_score_win",
                    db=db,
                    user=user,
                ))

    def test_eg07_no_ownership_403(self):
        """EG-07: CC export, no ownership → 403 (always enforced, no feature flag)."""
        db = _make_db()
        user = _make_user()
        with pytest.raises(HTTPException) as exc_info:
            self._call_export(db, user, is_accessible=False)
        assert exc_info.value.status_code == 403

    def test_eg08_no_ownership_403(self):
        """EG-08: CC export without ownership → 403."""
        db = _make_db()
        user = _make_user()
        with pytest.raises(HTTPException) as exc_info:
            self._call_export(db, user, is_accessible=False)
        assert exc_info.value.status_code == 403

    def test_eg09_owned_allowed(self):
        """EG-09: CC export, owned → allowed."""
        db = _make_db()
        user = _make_user()
        result = self._call_export(db, user, is_accessible=True)
        assert result is not None


# ── EG-10: Admin bypass ───────────────────────────────────────────────────────

def test_eg10_admin_bypass_welcome_card():
    """EG-10: admin user can export Welcome Card without ownership."""
    from app.api.web_routes.profile import export_onboarding_welcome_card

    user = _make_user(role="ADMIN")
    db = _make_db()
    request = _make_request()

    fake_license = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = fake_license

    with patch(f"{_SVC}.is_design_accessible", return_value=False), \
         patch(f"{_PROF}._export_svc") as mock_svc, \
         patch(f"{_PROF}._check_welcome_card_auth", return_value=None), \
         patch(f"{_PROF}._create_render_token", return_value="tok"):
        mock_svc.APP_INTERNAL_PORT = 8000
        mock_svc.CANVAS_SIZES = {"instagram_square": (1080, 1080)}
        mock_svc.check_export_rate_limit.return_value = True
        mock_svc._sync_take_screenshot.return_value = b"PNG"

        result = _run(export_onboarding_welcome_card(
            request=request,
            platform="instagram_square",
            use_nickname=False,
            db=db,
            user=user,
        ))
    assert result is not None


# ── EG-11: Export routes do NOT call CreditService.deduct ────────────────────

def test_eg11_export_routes_no_credit_deduction():
    """EG-11: export route source files do not contain CreditService.deduct calls."""
    base = Path(__file__).resolve().parents[4] / "app" / "api" / "web_routes"

    for fname in ("public_player.py", "profile.py", "vt_challenges.py"):
        src = (base / fname).read_text(encoding="utf-8")
        # CreditService.deduct should not appear in export-related code
        # (purchase_design is in card_design_service.py, not these files)
        assert "CreditService" not in src or "deduct" not in src or \
               _deduct_only_in_non_export_context(src), \
               f"{fname} must not call CreditService.deduct in export routes"


def _deduct_only_in_non_export_context(src: str) -> bool:
    """Return True if 'deduct' does not appear in the file (export routes never deduct)."""
    return "CreditService" not in src
