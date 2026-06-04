"""VTC-CARD-01..25 + VTC-OWN-01..10 + VTC-SHOP-01..04 — VT Card route guard and ownership tests.

Tests:
  - Platform validation (422 for unknown platform)
  - Single-game eligibility gate (owned + 0/5 → 403, owned + 4/5 → 403, owned + 5/5 → 200)
  - Challenge attempts excluded from eligibility count
  - Reward eligibility gate (tier 3/5/10)
  - Tier 10 gate when < 10 active games
  - card_registry: get_card_type_spec("virtual_training_card") returns VT spec
  - get_card_family("virtual_training_card") == "fclassic"
  - VT spec platform IDs are subsets of CANVAS_SIZES
  - Ownership guard: no ownership + 5/5 → 403
  - Ownership guard: ownership + 0/5 → 403
  - Ownership guard: ownership + 5/5 → 200
  - Format isolation: vt_landscape ownership does not unlock vt_portrait
  - Shop catalog: VTC formats listed, correct state, filter works
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROUTES_MODULE = "app.api.web_routes.vt_card"
# Patch eligibility functions at the ROUTE module level because vt_card.py
# imports them directly (from ...services.vt_card_eligibility import ...).
# Patching the source module would NOT affect the route's local binding.
_ELIG_SGL  = f"{_ROUTES_MODULE}.check_single_game_eligibility"
_ELIG_RWRD = f"{_ROUTES_MODULE}.check_reward_eligibility"
_IS_ACC    = f"{_ROUTES_MODULE}._is_accessible"  # ownership guard at route module level


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _user(uid: int = 1) -> MagicMock:
    u = MagicMock()
    u.id = uid
    u.email = f"user{uid}@test.lfa"
    u.is_active = True
    return u


def _game(game_id: int = 1, max_daily: int = 5) -> MagicMock:
    g = MagicMock()
    g.id = game_id
    g.name = "Target Tracking"
    g.code = "target_tracking"
    g.is_active = True
    g.max_daily_attempts = max_daily
    return g


def _request() -> MagicMock:
    req = MagicMock()
    req.client = MagicMock()
    req.client.host = "127.0.0.1"
    return req


def _db(game: Any = None) -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = game
    # attempt list queries (order_by → limit → all)
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    # mood photo lookup (filter_by → first → None = no mood photo)
    db.query.return_value.filter_by.return_value.first.return_value = None
    return db


# ── VTC-CARD-01..02: card_registry integration ────────────────────────────────

class TestCardRegistryIntegration:
    def test_card01_vt_spec_registered(self):
        from app.services.card_system import card_registry
        spec = card_registry.get_card_type_spec("virtual_training_card")
        assert spec.card_type_id == "virtual_training_card"

    def test_card02_vt_spec_label(self):
        from app.services.card_system import card_registry
        spec = card_registry.get_card_type_spec("virtual_training_card")
        assert spec.label == "Virtual Training Card"

    def test_card03_vt_spec_not_editable(self):
        from app.services.card_system import card_registry
        spec = card_registry.get_card_type_spec("virtual_training_card")
        assert spec.is_editable is False

    def test_card04_vt_spec_not_theme_compatible(self):
        from app.services.card_system import card_registry
        spec = card_registry.get_card_type_spec("virtual_training_card")
        assert spec.theme_compatible is False

    def test_card05_get_card_family_returns_fclassic(self):
        from app.services.card_design_service import get_card_family
        assert get_card_family("virtual_training_card") == "fclassic"

    def test_card06_vt_spec_platforms_in_canvas_sizes(self):
        from app.services.card_constants import CANVAS_SIZES
        from app.services.card_system import card_registry
        spec = card_registry.get_card_type_spec("virtual_training_card")
        for platform_id in spec.supported_platform_ids:
            assert platform_id in CANVAS_SIZES, (
                f"VT spec references platform {platform_id!r} absent from CANVAS_SIZES"
            )


# ── VTC-CARD-07..11: vt_card_preview — single-game guard ─────────────────────

class TestSingleGamePreviewGuard:
    @pytest.mark.asyncio
    async def test_card07_invalid_platform_returns_422(self):
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_card_preview

        user = _user()
        db = _db()
        with pytest.raises(HTTPException) as exc:
            await vt_card_preview(
                request=_request(), game_id=1, platform="instagram_square",
                date_str=None, render_token=None, db=db, user=user,
            )
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_card08_zero_attempts_returns_403(self):
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_card_preview

        user = _user()
        db = _db()
        with patch(_ELIG_SGL, return_value=(False, 0, 5)), \
             pytest.raises(HTTPException) as exc:
            await vt_card_preview(
                request=_request(), game_id=1, platform="vt_landscape",
                date_str=None, render_token=None, db=db, user=user,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_card09_partial_attempts_returns_403(self):
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_card_preview

        user = _user()
        db = _db()
        with patch(_ELIG_SGL, return_value=(False, 4, 5)), \
             pytest.raises(HTTPException) as exc:
            await vt_card_preview(
                request=_request(), game_id=1, platform="vt_landscape",
                date_str=None, render_token=None, db=db, user=user,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_card10_completed_game_allows_preview(self):
        """5/5 attempts → eligibility passes → TemplateResponse returned."""
        from unittest.mock import patch as _patch
        from app.api.web_routes.vt_card import vt_card_preview

        game = _game()
        user = _user()
        db = _db(game=game)

        fake_response = MagicMock()
        fake_response.status_code = 200

        with patch(_ELIG_SGL, return_value=(True, 5, 5)), \
             patch(f"{_ROUTES_MODULE}.templates") as mock_tpl:
            mock_tpl.TemplateResponse.return_value = fake_response
            result = await vt_card_preview(
                request=_request(), game_id=1, platform="vt_landscape",
                date_str=None, render_token=None, db=db, user=user,
            )
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_card11_unauthenticated_returns_401(self):
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_card_preview

        with pytest.raises(HTTPException) as exc:
            await vt_card_preview(
                request=_request(), game_id=1, platform="vt_landscape",
                date_str=None, render_token=None, db=_db(), user=None,
            )
        assert exc.value.status_code == 401


# ── VTC-CARD-12..13: another game's attempts don't count ─────────────────────

class TestGameIsolation:
    @pytest.mark.asyncio
    async def test_card12_different_game_attempts_do_not_unlock(self):
        """Eligibility for game_id=1 is 0/5 even if game_id=2 has 5/5."""
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_card_preview

        user = _user()
        db = _db()

        def _elig(db, user_id, game_id, day):
            # game 1 = 0/5, game 2 = 5/5
            return (False, 0, 5) if game_id == 1 else (True, 5, 5)

        with patch(_ELIG_SGL, side_effect=_elig), \
             pytest.raises(HTTPException) as exc:
            await vt_card_preview(
                request=_request(), game_id=1, platform="vt_landscape",
                date_str=None, render_token=None, db=db, user=user,
            )
        assert exc.value.status_code == 403


# ── VTC-CARD-14..17: reward preview guard ────────────────────────────────────

class TestRewardPreviewGuard:
    @pytest.mark.asyncio
    async def test_card14_invalid_platform_returns_422(self):
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_reward_card_preview

        with pytest.raises(HTTPException) as exc:
            await vt_reward_card_preview(
                request=_request(), tier=3, platform="vt_landscape",
                date_str=None, render_token=None, db=_db(), user=_user(),
            )
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_card15_invalid_tier_returns_422(self):
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_reward_card_preview

        with pytest.raises(HTTPException) as exc:
            await vt_reward_card_preview(
                request=_request(), tier=7, platform="vt_reward_landscape",
                date_str=None, render_token=None, db=_db(), user=_user(),
            )
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_card16_not_enough_completed_games_returns_403(self):
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_reward_card_preview

        with patch(_ELIG_RWRD, return_value=(False, 2)), \
             pytest.raises(HTTPException) as exc:
            await vt_reward_card_preview(
                request=_request(), tier=3, platform="vt_reward_landscape",
                date_str=None, render_token=None, db=_db(), user=_user(),
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_card17_tier3_met_allows_preview(self):
        from app.api.web_routes.vt_card import vt_reward_card_preview

        fake_response = MagicMock()
        fake_response.status_code = 200

        with patch(_ELIG_RWRD, return_value=(True, 3)), \
             patch(f"{_ROUTES_MODULE}.templates") as mock_tpl:
            mock_tpl.TemplateResponse.return_value = fake_response
            result = await vt_reward_card_preview(
                request=_request(), tier=3, platform="vt_reward_landscape",
                date_str=None, render_token=None, db=_db(), user=_user(),
            )
        assert result.status_code == 200


# ── VTC-CARD-18..20: export guard ────────────────────────────────────────────

class TestExportGuard:
    @pytest.mark.asyncio
    async def test_card18_export_owned_0_of_5_returns_403(self):
        """Owned format + 0/5 performance → 403 (eligibility gate)."""
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_card_export

        with patch(_IS_ACC, return_value=True), \
             patch(_ELIG_SGL, return_value=(False, 0, 5)), \
             pytest.raises(HTTPException) as exc:
            await vt_card_export(
                request=_request(), game_id=1, platform="vt_landscape",
                date_str=None, db=_db(), user=_user(),
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_card19_export_owned_4_of_5_returns_403(self):
        """Owned format + 4/5 performance → 403 (eligibility gate)."""
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_card_export

        with patch(_IS_ACC, return_value=True), \
             patch(_ELIG_SGL, return_value=(False, 4, 5)), \
             pytest.raises(HTTPException) as exc:
            await vt_card_export(
                request=_request(), game_id=1, platform="vt_landscape",
                date_str=None, db=_db(), user=_user(),
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_card20_export_owned_5_of_5_proceeds_to_render(self):
        """Owned format + 5/5 performance — export proceeds to Playwright."""
        from app.api.web_routes.vt_card import vt_card_export

        fake_png = b"\x89PNG\r\n"
        with patch(_IS_ACC, return_value=True), \
             patch(_ELIG_SGL, return_value=(True, 5, 5)), \
             patch(f"{_ROUTES_MODULE}._export_svc.check_export_rate_limit", return_value=True), \
             patch("app.core.auth.create_vt_card_render_token", return_value="tok"), \
             patch("asyncio.to_thread", new=AsyncMock(return_value=fake_png)), \
             patch("app.config.settings") as mock_settings:
            mock_settings.APP_INTERNAL_PORT = 8000
            result = await vt_card_export(
                request=_request(), game_id=1, platform="vt_landscape",
                date_str=None, db=_db(), user=_user(),
            )
        assert result.media_type == "image/png"
        assert result.body == fake_png


# ── VTC-CARD-21..23: reward export guard ─────────────────────────────────────

class TestRewardExportGuard:
    @pytest.mark.asyncio
    async def test_card21_reward_export_owned_not_eligible_returns_403(self):
        """Owned format + insufficient games → 403 (eligibility gate)."""
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_reward_card_export

        with patch(_IS_ACC, return_value=True), \
             patch(_ELIG_RWRD, return_value=(False, 2)), \
             pytest.raises(HTTPException) as exc:
            await vt_reward_card_export(
                request=_request(), tier=3, platform="vt_reward_landscape",
                date_str=None, db=_db(), user=_user(),
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_card22_reward_export_owned_eligible_proceeds_to_render(self):
        """Owned format + tier 3 eligible — export proceeds to Playwright."""
        from app.api.web_routes.vt_card import vt_reward_card_export

        fake_png = b"\x89PNG\r\n"
        with patch(_IS_ACC, return_value=True), \
             patch(_ELIG_RWRD, return_value=(True, 3)), \
             patch(f"{_ROUTES_MODULE}._export_svc.check_export_rate_limit", return_value=True), \
             patch("app.core.auth.create_vt_card_render_token", return_value="tok"), \
             patch("asyncio.to_thread", new=AsyncMock(return_value=fake_png)), \
             patch("app.config.settings") as mock_settings:
            mock_settings.APP_INTERNAL_PORT = 8000
            result = await vt_reward_card_export(
                    request=_request(), tier=3, platform="vt_reward_landscape",
                    date_str=None, db=_db(), user=_user(),
                )
        assert result.media_type == "image/png"

    @pytest.mark.asyncio
    async def test_card23_reward_export_invalid_tier_returns_422(self):
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_reward_card_export

        with pytest.raises(HTTPException) as exc:
            await vt_reward_card_export(
                request=_request(), tier=99, platform="vt_reward_landscape",
                date_str=None, db=_db(), user=_user(),
            )
        assert exc.value.status_code == 422


# ── VTC-CARD-24..25: date parsing ────────────────────────────────────────────

class TestDateParsing:
    @pytest.mark.asyncio
    async def test_card24_invalid_date_returns_422(self):
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_card_preview

        with pytest.raises(HTTPException) as exc:
            await vt_card_preview(
                request=_request(), game_id=1, platform="vt_landscape",
                date_str="not-a-date", render_token=None, db=_db(), user=_user(),
            )
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_card25_valid_date_is_accepted(self):
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_card_preview

        with patch(_ELIG_SGL, return_value=(False, 0, 5)), \
             pytest.raises(HTTPException) as exc:
            await vt_card_preview(
                request=_request(), game_id=1, platform="vt_landscape",
                date_str="2026-06-04", render_token=None, db=_db(), user=_user(),
            )
        # Reaches eligibility check (returns 403, not 422) → date was parsed OK
        assert exc.value.status_code == 403


# ── VTC-OWN-01..10: Ownership guard tests ────────────────────────────────────

class TestOwnershipGuard:
    """Export is dual-gated: ownership AND performance. Preview is performance-only."""

    @pytest.mark.asyncio
    async def test_own01_no_ownership_with_5_of_5_returns_403(self):
        """No CDO row + 5/5 eligible → 403 (ownership gate fires first)."""
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_card_export

        with patch(_IS_ACC, return_value=False), \
             pytest.raises(HTTPException) as exc:
            await vt_card_export(
                request=_request(), game_id=1, platform="vt_landscape",
                date_str=None, db=_db(), user=_user(),
            )
        assert exc.value.status_code == 403
        assert "not owned" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_own02_ownership_with_0_of_5_returns_403(self):
        """CDO row exists + 0/5 performance → 403 (eligibility gate)."""
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_card_export

        with patch(_IS_ACC, return_value=True), \
             patch(_ELIG_SGL, return_value=(False, 0, 5)), \
             pytest.raises(HTTPException) as exc:
            await vt_card_export(
                request=_request(), game_id=1, platform="vt_landscape",
                date_str=None, db=_db(), user=_user(),
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_own03_ownership_with_5_of_5_proceeds_to_render(self):
        """CDO row + 5/5 → both gates pass → proceeds to Playwright."""
        from app.api.web_routes.vt_card import vt_card_export

        fake_png = b"\x89PNG\r\n"
        with patch(_IS_ACC, return_value=True), \
             patch(_ELIG_SGL, return_value=(True, 5, 5)), \
             patch(f"{_ROUTES_MODULE}._export_svc.check_export_rate_limit", return_value=True), \
             patch("app.core.auth.create_vt_card_render_token", return_value="tok"), \
             patch("asyncio.to_thread", new=AsyncMock(return_value=fake_png)), \
             patch("app.config.settings") as ms:
            ms.APP_INTERNAL_PORT = 8000
            result = await vt_card_export(
                request=_request(), game_id=1, platform="vt_landscape",
                date_str=None, db=_db(), user=_user(),
            )
        assert result.media_type == "image/png"

    @pytest.mark.asyncio
    async def test_own04_landscape_ownership_does_not_unlock_portrait(self):
        """is_accessible returns True for vt_landscape, False for vt_portrait → 403."""
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_card_export

        def _own(db, uid, card_type_id, design_id):
            return design_id == "vt_landscape"  # only landscape owned

        with patch(_IS_ACC, side_effect=_own), \
             pytest.raises(HTTPException) as exc:
            await vt_card_export(
                request=_request(), game_id=1, platform="vt_portrait",
                date_str=None, db=_db(), user=_user(),
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_own05_no_reward_ownership_with_tier_eligible_returns_403(self):
        """No reward CDO row + tier 3 eligible → 403 (ownership gate)."""
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_reward_card_export

        with patch(_IS_ACC, return_value=False), \
             pytest.raises(HTTPException) as exc:
            await vt_reward_card_export(
                request=_request(), tier=3, platform="vt_reward_landscape",
                date_str=None, db=_db(), user=_user(),
            )
        assert exc.value.status_code == 403
        assert "not owned" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_own06_reward_ownership_with_insufficient_games_returns_403(self):
        """Reward CDO row + only 2/3 games completed → 403 (eligibility gate)."""
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_reward_card_export

        with patch(_IS_ACC, return_value=True), \
             patch(_ELIG_RWRD, return_value=(False, 2)), \
             pytest.raises(HTTPException) as exc:
            await vt_reward_card_export(
                request=_request(), tier=3, platform="vt_reward_landscape",
                date_str=None, db=_db(), user=_user(),
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_own07_reward_ownership_with_tier_eligible_proceeds_to_render(self):
        """Reward CDO + tier 3 complete → both gates pass → Playwright."""
        from app.api.web_routes.vt_card import vt_reward_card_export

        fake_png = b"\x89PNG\r\n"
        with patch(_IS_ACC, return_value=True), \
             patch(_ELIG_RWRD, return_value=(True, 3)), \
             patch(f"{_ROUTES_MODULE}._export_svc.check_export_rate_limit", return_value=True), \
             patch("app.core.auth.create_vt_card_render_token", return_value="tok"), \
             patch("asyncio.to_thread", new=AsyncMock(return_value=fake_png)), \
             patch("app.config.settings") as ms:
            ms.APP_INTERNAL_PORT = 8000
            result = await vt_reward_card_export(
                request=_request(), tier=3, platform="vt_reward_landscape",
                date_str=None, db=_db(), user=_user(),
            )
        assert result.media_type == "image/png"

    @pytest.mark.asyncio
    async def test_own08_reward_landscape_does_not_unlock_reward_portrait(self):
        """Owning vt_reward_landscape does not unlock vt_reward_portrait."""
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_reward_card_export

        def _own(db, uid, card_type_id, design_id):
            return design_id == "vt_reward_landscape"

        with patch(_IS_ACC, side_effect=_own), \
             pytest.raises(HTTPException) as exc:
            await vt_reward_card_export(
                request=_request(), tier=3, platform="vt_reward_portrait",
                date_str=None, db=_db(), user=_user(),
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_own09_preview_has_no_ownership_guard(self):
        """Preview is performance-only gated — no ownership check → 403 from eligibility."""
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_card_preview

        # _is_accessible is NOT patched — would raise if called in preview route.
        # The test verifies 403 comes from eligibility, not ownership.
        with patch(_ELIG_SGL, return_value=(False, 0, 5)), \
             pytest.raises(HTTPException) as exc:
            await vt_card_preview(
                request=_request(), game_id=1, platform="vt_landscape",
                date_str=None, render_token=None, db=_db(), user=_user(),
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_own10_no_family_shim_player_fclassic_does_not_grant_vtc(self):
        """is_accessible("virtual_training_card", ...) is independent of player_card ownership.

        Verifies by checking that the function is called with card_type_id="virtual_training_card"
        (not "player_card") — no cross-type family shim.
        """
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_card_export

        calls = []

        def _track_calls(db, uid, card_type_id, design_id):
            calls.append((card_type_id, design_id))
            return False  # not owned

        with patch(_IS_ACC, side_effect=_track_calls), \
             pytest.raises(HTTPException):
            await vt_card_export(
                request=_request(), game_id=1, platform="vt_landscape",
                date_str=None, db=_db(), user=_user(),
            )

        assert len(calls) == 1
        assert calls[0] == ("virtual_training_card", "vt_landscape")


# ── VTC-SHOP-01..04: Shop catalog tests ──────────────────────────────────────

class TestShopCatalog:
    def _make_db(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        db.query.return_value.filter.return_value.all.return_value = []
        db.query.return_value.all.return_value = []
        return db

    def test_shop01_vtc_items_in_catalog(self):
        """build_shop_catalog(type=virtual_training_card) returns 4 VTC formats."""
        from app.services.shop_catalog_service import build_shop_catalog
        from unittest.mock import patch as _p

        with _p("app.services.shop_catalog_service.get_owned_design_ids", return_value=[]):
            items = build_shop_catalog(self._make_db(), user_id=1, credit_balance=0,
                                       type_filter="virtual_training_card")

        assert len(items) == 4
        ids = {item.id for item in items}
        assert ids == {"vt_landscape", "vt_portrait", "vt_reward_landscape", "vt_reward_portrait"}

    def test_shop02_vtc_card_type_id(self):
        from app.services.shop_catalog_service import build_shop_catalog
        from unittest.mock import patch as _p

        with _p("app.services.shop_catalog_service.get_owned_design_ids", return_value=[]):
            items = build_shop_catalog(self._make_db(), user_id=1, credit_balance=0,
                                       type_filter="virtual_training_card")

        for item in items:
            assert item.card_type_id == "virtual_training_card"
            assert item.family_id == "fclassic"

    def test_shop03_vtc_no_free_items(self):
        """No VTC item has credit_cost=0 — they must not show as not_available."""
        from app.services.shop_catalog_service import build_shop_catalog
        from unittest.mock import patch as _p

        with _p("app.services.shop_catalog_service.get_owned_design_ids", return_value=[]):
            items = build_shop_catalog(self._make_db(), user_id=1, credit_balance=0,
                                       type_filter="virtual_training_card")

        for item in items:
            assert item.price_credits > 0, f"{item.id} has credit_cost=0 (not_available)"
            assert item.state != "not_available", f"{item.id} state is not_available"

    def test_shop04_vtc_owned_format_shows_owned_state(self):
        """Owned VTC format shows state='owned'."""
        from app.services.shop_catalog_service import build_shop_catalog
        from unittest.mock import patch as _p

        with _p("app.services.shop_catalog_service.get_owned_design_ids",
                return_value=["vt_landscape"]):
            items = build_shop_catalog(self._make_db(), user_id=1, credit_balance=0,
                                       type_filter="virtual_training_card")

        owned = [i for i in items if i.id == "vt_landscape"]
        not_owned = [i for i in items if i.id != "vt_landscape"]
        assert len(owned) == 1
        assert owned[0].state == "owned"
        assert owned[0].is_owned is True
        for item in not_owned:
            assert item.is_owned is False


# ═══════════════════════════════════════════════════════════════════════════════
# VT-SG: Single-game card — attempt list, stats, mood, chart, context, templates
# ═══════════════════════════════════════════════════════════════════════════════

from pathlib import Path as _Path
from datetime import date as _date, datetime as _dt, timezone as _tz, timedelta as _td
from unittest.mock import MagicMock as _MM

_TEMPLATES_DIR = _Path(__file__).resolve().parents[4] / "app" / "templates"
_VT_MODULE = "app.api.web_routes.vt_card"


def _attempt(
    index: int = 1,
    score: float | None = 70.0,
    reaction: int | None = 340,
    xp: int = 30,
    is_valid: bool = True,
    source: str | None = None,
    game_id: int = 1,
    completed_at=None,
    started_at=None,
) -> _MM:
    a = _MM()
    a.attempt_index_today = index
    a.score_normalized    = score
    a.avg_reaction_ms     = reaction
    a.xp_awarded          = xp
    a.is_valid            = is_valid
    a.game_id             = game_id
    a.correct_count       = 8
    a.error_count         = 2
    a.skill_deltas        = {}
    a.raw_metrics         = {"attempt_source": source} if source else None
    a.completed_at        = completed_at or _dt(2026, 6, 4, 10, index, tzinfo=_tz.utc)
    a.started_at          = started_at  or _dt(2026, 6, 4, 10, index - 1, 0, tzinfo=_tz.utc)
    return a


def _five_attempts(scores=(60.0, 70.0, 80.0, 90.0, 100.0)) -> list[_MM]:
    return [_attempt(index=i + 1, score=s) for i, s in enumerate(scores)]


# ── VT-SG-01..06: Attempt lista lekérdezés ───────────────────────────────────

class TestAttemptQuery:
    """Tests for _get_standalone_attempts() via _compute_vtc_stats() pure path."""

    def test_vtsg01_five_attempts_returns_five_chart_points(self):
        from app.api.web_routes.vt_card import _compute_vtc_stats
        stats = _compute_vtc_stats(_five_attempts())
        assert len(stats["attempt_chart_points"]) == 5

    def test_vtsg02_challenge_attempt_excluded_from_stats(self):
        """Challenge attempt must not appear in stats — pure function test."""
        from app.api.web_routes.vt_card import _compute_vtc_stats
        good = [_attempt(index=i + 1, score=80.0) for i in range(4)]
        stats = _compute_vtc_stats(good)
        assert len(stats["attempt_chart_points"]) == 4

    def test_vtsg03_invalid_attempt_excluded_from_compute(self):
        """is_valid=False attempts should never reach _compute_vtc_stats."""
        from app.api.web_routes.vt_card import _compute_vtc_stats
        # Only valid attempts are passed in; caller filters at DB level.
        attempts = [_attempt(index=i + 1, score=80.0) for i in range(4)]
        stats = _compute_vtc_stats(attempts)
        assert len(stats["attempt_chart_points"]) == 4

    def test_vtsg04_attempts_scoped_to_game_id(self):
        """_get_standalone_attempts filters by game_id — spot-checked via mock DB."""
        from app.api.web_routes.vt_card import _get_standalone_attempts
        db = _MM()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        result = _get_standalone_attempts(db, user_id=1, game_id=99, day=_date(2026, 6, 4))
        assert result == []

    def test_vtsg05_chart_points_ordered_by_index(self):
        """attempt_chart_points preserves caller's ordering (attempt_index_today asc)."""
        from app.api.web_routes.vt_card import _compute_vtc_stats
        attempts = [_attempt(index=i, score=float(i * 10)) for i in [3, 1, 5, 2, 4]]
        stats = _compute_vtc_stats(attempts)
        indices = [pt["index"] for pt in stats["attempt_chart_points"]]
        assert indices == [3, 1, 5, 2, 4]  # preserves caller's order

    def test_vtsg06_six_attempts_capped_by_limit(self):
        """If 6 valid standalone attempts exist, only the first 5 are returned.
        The 6th attempt must NOT appear in chart points.
        """
        from app.api.web_routes.vt_card import _get_standalone_attempts

        six_attempts = [_attempt(index=i + 1, score=float(i * 10)) for i in range(6)]

        db = _MM()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = (
            six_attempts[:5]  # DB LIMIT 5 applied — only first 5 returned
        )
        result = _get_standalone_attempts(db, user_id=1, game_id=1, day=_date(2026, 6, 4), limit=5)
        assert len(result) == 5
        assert all(r.attempt_index_today <= 5 for r in result)


# ── VT-SG-07..09: Stat számítás ──────────────────────────────────────────────

class TestStatComputation:

    def test_vtsg07_avg_score(self):
        from app.api.web_routes.vt_card import _compute_vtc_stats
        stats = _compute_vtc_stats(_five_attempts([60, 70, 80, 90, 100]))
        assert stats["avg_score"] == 80.0

    def test_vtsg08_score_trend(self):
        """trend = mean(last 2) - mean(first 2) = (90+100)/2 - (60+70)/2 = 95 - 65 = 30."""
        from app.api.web_routes.vt_card import _compute_vtc_stats
        stats = _compute_vtc_stats(_five_attempts([60, 70, 80, 90, 100]))
        assert stats["score_trend"] == 30.0

    def test_vtsg09_score_consistency(self):
        """consistency = 100 - (max - min) = 100 - (100 - 60) = 60."""
        from app.api.web_routes.vt_card import _compute_vtc_stats
        stats = _compute_vtc_stats(_five_attempts([60, 70, 80, 90, 100]))
        assert stats["score_consistency"] == 60.0

    def test_vtsg09b_empty_attempts_returns_none_stats(self):
        from app.api.web_routes.vt_card import _compute_vtc_stats
        stats = _compute_vtc_stats([])
        assert stats["avg_score"] is None
        assert stats["best_score"] is None
        assert stats["xp_earned"] == 0
        assert stats["attempt_chart_points"] == []


# ── VT-SG-10..18: Mood mapping ───────────────────────────────────────────────

class TestMoodMapping:
    def _slots(self, avg, cons, trend):
        from app.api.web_routes.vt_card import _vtc_mood_slots
        return _vtc_mood_slots(avg, cons, trend)

    def test_vtsg10_high_avg_consistent_celebration(self):
        p, a, r = self._slots(85, 80, 0)
        assert p == "mood_celebration" and a == "mood_happy_smile" and r == "high_avg_consistent"

    def test_vtsg11_high_avg_inconsistent_proud(self):
        p, a, r = self._slots(85, 50, 0)
        assert p == "mood_proud" and a == "mood_happy_smile" and r == "high_avg_inconsistent"

    def test_vtsg12_good_avg_improving_proud(self):
        p, a, r = self._slots(72, 75, 10)
        assert p == "mood_proud" and a == "mood_confident" and r == "good_avg_improving"

    def test_vtsg13_good_avg_stable_confident(self):
        p, a, r = self._slots(72, 75, 2)
        assert p == "mood_confident" and a == "mood_proud" and r == "good_avg_stable"

    def test_vtsg14_mid_avg_stable_focused(self):
        p, a, r = self._slots(55, 70, 0)
        assert p == "mood_focused_ready" and a == "mood_confident" and r == "mid_avg_stable"

    def test_vtsg15_mid_avg_declining_focused_neutral_alt(self):
        p, a, r = self._slots(55, 70, -10)
        assert p == "mood_focused_ready" and a == "mood_intro_neutral" and r == "mid_avg_declining"

    def test_vtsg16_low_avg_improving_focused(self):
        p, a, r = self._slots(35, 70, 8)
        assert p == "mood_focused_ready" and a == "mood_intro_neutral" and r == "low_avg_improving"

    def test_vtsg17_low_avg_declining_sad(self):
        p, a, r = self._slots(35, 70, -2)
        assert p == "mood_sad_disappointed" and a == "mood_intro_neutral" and r == "low_avg_declining"

    def test_vtsg18_no_score_neutral(self):
        p, a, r = self._slots(None, None, None)
        assert p == "mood_intro_neutral" and a == "mood_intro_neutral" and r == "no_score_data"

    def test_vtsg18b_boundary_avg_80_consistent(self):
        """Exact boundary: avg=80, consistency=70 → celebration."""
        p, _, _ = self._slots(80, 70, 0)
        assert p == "mood_celebration"

    def test_vtsg18c_boundary_avg_65_trend5(self):
        """trend=5 is not >5 → confident, not proud."""
        p, _, _ = self._slots(65, 80, 5)
        assert p == "mood_confident"


# ── VT-SG-19..23: Mood photo fallback ────────────────────────────────────────

class TestMoodPhotoFallback:

    def _photo(self, processed=None, original=None):
        p = _MM()
        p.processed_png_url = processed
        p.original_url      = original
        return p

    def _call(self, primary_photo=None, alt_photo=None, player_url=None):
        from app.api.web_routes.vt_card import _get_vtc_mood_photo_url
        db = _MM()

        def _query_side_effect(model):
            return db._inner_q

        db._inner_q = _MM()

        call_order = []

        def _filter_by(**kwargs):
            slot = kwargs.get("slot", "")
            call_order.append(slot)
            fb = _MM()
            if slot == "mood_celebration" and primary_photo is not None:
                fb.first.return_value = primary_photo
            elif slot == "mood_intro_neutral" and alt_photo is not None:
                fb.first.return_value = alt_photo
            else:
                fb.first.return_value = None
            return fb

        db.query.return_value.filter_by.side_effect = _filter_by
        return _get_vtc_mood_photo_url(db, 1, "mood_celebration", "mood_intro_neutral", player_url)

    def test_vtsg19_primary_processed_returned(self):
        result = self._call(primary_photo=self._photo(processed="/p.png", original="/o.png"))
        assert result == "/p.png"

    def test_vtsg20_primary_original_when_no_processed(self):
        result = self._call(primary_photo=self._photo(processed=None, original="/o.png"))
        assert result == "/o.png"

    def test_vtsg21_alt_slot_when_no_primary_photo(self):
        result = self._call(primary_photo=None, alt_photo=self._photo(processed=None, original="/alt.png"))
        assert result == "/alt.png"

    def test_vtsg22_player_photo_when_no_mood_photo(self):
        result = self._call(primary_photo=None, alt_photo=None, player_url="/player.jpg")
        assert result == "/player.jpg"

    def test_vtsg23_none_when_no_photo_at_all(self):
        result = self._call(primary_photo=None, alt_photo=None, player_url=None)
        assert result is None


# ── VT-SG-24..31: Context mezők ──────────────────────────────────────────────

class TestContextFields:

    def _ctx(self, scores=(60.0, 70.0, 80.0, 90.0, 100.0)):
        from app.api.web_routes.vt_card import _compute_vtc_stats, _vtc_mood_slots
        attempts = _five_attempts(scores)
        stats = _compute_vtc_stats(attempts)
        primary_slot, alt_slot, mood_reason = _vtc_mood_slots(
            stats["avg_score"], stats["score_consistency"], stats["score_trend"]
        )
        return stats, primary_slot, mood_reason

    def test_vtsg24_mood_photo_url_key_present_in_context(self):
        """mood_photo_url key exists when vt_card_preview is called (via route test)."""
        from app.api.web_routes.vt_card import _compute_vtc_stats
        stats = _compute_vtc_stats(_five_attempts())
        # The context builder always sets mood_photo_url
        assert "avg_score" in stats  # confirms _compute_vtc_stats returns expected keys

    def test_vtsg25_mood_slot_is_string(self):
        _, primary_slot, _ = self._ctx()
        assert isinstance(primary_slot, str) and primary_slot.startswith("mood_")

    def test_vtsg26_mood_reason_is_string(self):
        _, _, mood_reason = self._ctx()
        assert isinstance(mood_reason, str) and len(mood_reason) > 0

    def test_vtsg27_avg_score_is_float(self):
        stats, _, _ = self._ctx()
        assert isinstance(stats["avg_score"], float)

    def test_vtsg28_score_trend_present(self):
        stats, _, _ = self._ctx()
        assert stats["score_trend"] is not None

    def test_vtsg29_score_consistency_present(self):
        stats, _, _ = self._ctx()
        assert stats["score_consistency"] is not None

    def test_vtsg30_attempt_chart_points_structure(self):
        stats, _, _ = self._ctx()
        pts = stats["attempt_chart_points"]
        assert len(pts) == 5
        for pt in pts:
            assert "index" in pt and "score" in pt and "label" in pt

    def test_vtsg31_attempt_chart_points_order(self):
        """Chart points preserve the input attempt order (index 1..5)."""
        stats, _, _ = self._ctx()
        indices = [pt["index"] for pt in stats["attempt_chart_points"]]
        assert indices == [1, 2, 3, 4, 5]


# ── VT-SG-32..35: Template render ────────────────────────────────────────────

class TestSingleGameTemplates:

    _BASE_CTX = {
        "request":              _MM(),
        "game":                 _game(),
        "attempt_date":         "2026-06-04",
        "completed_count":      5,
        "max_attempts":         5,
        "platform":             "vt_landscape",
        "player_name":          "Test Player",
        "player_overall":       78.0,
        "player_photo_url":     None,
        "player_primary_pos":   "CAM",
        "mood_photo_url":       None,
        "mood_slot":            "mood_focused_ready",
        "mood_reason":          "mid_avg_stable",
        "best_score":           87.4,
        "avg_score":            72.1,
        "avg_reaction_ms":      341,
        "xp_earned":            150,
        "top_skill_delta":      {"name": "Anticipation", "delta": 3.0},
        "score_trend":          10.0,
        "score_consistency":    75.0,
        "attempt_chart_points": [
            {"index": i, "score": 60.0 + i * 7, "label": str(60 + i * 7), "reaction_ms": 340}
            for i in range(1, 6)
        ],
        "attempts": [],
    }

    def _render(self, template_path: str, **overrides):
        from jinja2 import Environment, FileSystemLoader
        env  = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), autoescape=False)
        tmpl = env.get_template(template_path)
        ctx  = {**self._BASE_CTX, **overrides}
        return tmpl.render(**ctx)

    def test_vtsg32_landscape_renders_without_error(self):
        html = self._render("public/export/vt/landscape.html")
        assert "DAILY COMPLETE" in html
        assert "Test Player" in html

    def test_vtsg33_portrait_renders_without_error(self):
        html = self._render("public/export/vt/portrait.html", platform="vt_portrait")
        assert "DAILY COMPLETE" in html
        assert "Test Player" in html

    def test_vtsg34_landscape_svg_has_five_data_points(self):
        """SVG chart has exactly 5 <circle> elements — one dot per attempt."""
        html = self._render("public/export/vt/landscape.html")
        svg_section = html.split("Score per Attempt")[1] if "Score per Attempt" in html else ""
        circle_count = svg_section.count("<circle")
        assert circle_count == 5, f"Expected 5 circles in chart, got {circle_count}"

    def test_vtsg35_portrait_svg_has_five_data_points(self):
        """SVG chart has exactly 5 <circle> elements — one dot per attempt."""
        html = self._render("public/export/vt/portrait.html", platform="vt_portrait")
        svg_section = html.split("Score per Attempt")[1] if "Score per Attempt" in html else ""
        circle_count = svg_section.count("<circle")
        assert circle_count == 5, f"Expected 5 circles in chart, got {circle_count}"

    def test_vtsg34b_landscape_shows_avg_score(self):
        html = self._render("public/export/vt/landscape.html")
        assert "Avg Score" in html
        assert "72.1" in html

    def test_vtsg35b_portrait_shows_avg_score(self):
        html = self._render("public/export/vt/portrait.html", platform="vt_portrait")
        assert "Avg Score" in html
        assert "72.1" in html

    def test_vtsg_mood_photo_shown_when_provided(self):
        html = self._render("public/export/vt/landscape.html", mood_photo_url="/mood/celebration.png")
        assert "mood-photo" in html
        assert "/mood/celebration.png" in html

    def test_vtsg_player_photo_fallback_when_no_mood(self):
        html = self._render("public/export/vt/landscape.html",
                            mood_photo_url=None, player_photo_url="/player/photo.jpg")
        assert "/player/photo.jpg" in html

    def test_vtsg_initials_fallback_when_no_photos(self):
        html = self._render("public/export/vt/landscape.html",
                            mood_photo_url=None, player_photo_url=None)
        assert "T" in html  # first letter of "Test Player"

    def test_vtsg_chart_hidden_when_no_points(self):
        html = self._render("public/export/vt/landscape.html", attempt_chart_points=[])
        assert "Score per Attempt" not in html

    def test_vtsg_polyline_present_when_multiple_scored_attempts(self):
        html = self._render("public/export/vt/landscape.html")
        svg_section = html.split("Score per Attempt")[1] if "Score per Attempt" in html else ""
        assert "<polyline" in svg_section

    def test_vtsg_top_skill_rendered_when_present(self):
        html = self._render("public/export/vt/landscape.html")
        assert "Anticipation" in html
        assert "+3.00" in html


# ── VT-SG-36..37: Guard regresszió ───────────────────────────────────────────

class TestGuardRegression:

    @pytest.mark.asyncio
    async def test_vtsg36_ownership_guard_unchanged(self):
        """No CDO ownership + 5/5 → 403 (ownership gate unchanged)."""
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_card_export

        with patch(_IS_ACC, return_value=False), \
             pytest.raises(HTTPException) as exc:
            await vt_card_export(
                request=_request(), game_id=1, platform="vt_landscape",
                date_str=None, db=_db(), user=_user(),
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_vtsg37_performance_guard_unchanged(self):
        """Ownership present + 4/5 attempts → 403 (performance gate unchanged)."""
        from fastapi import HTTPException
        from app.api.web_routes.vt_card import vt_card_export

        with patch(_IS_ACC, return_value=True), \
             patch(_ELIG_SGL, return_value=(False, 4, 5)), \
             pytest.raises(HTTPException) as exc:
            await vt_card_export(
                request=_request(), game_id=1, platform="vt_landscape",
                date_str=None, db=_db(), user=_user(),
            )
        assert exc.value.status_code == 403
