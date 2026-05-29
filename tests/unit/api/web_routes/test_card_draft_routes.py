"""
CD-RT — Card draft route tests (Phase 4D-2).

Verifies that the dashboard card-editor GET and card-* POST routes read/write
via CardDraftService (not UserLicense legacy columns).

All tests use MagicMock — no real DB or HTTP server required.

Test groups:
  CD-RT-01: card-editor GET reads active_card_theme from card_draft.draft_theme
  CD-RT-02: card-editor GET reads active_card_variant from card_draft.draft_variant
  CD-RT-03: card-editor GET reads active_card_platform from card_draft.draft_platform
  CD-RT-04: card-editor GET reads published state from card_draft.published_*
  CD-RT-05: card-platform POST calls CardDraftService.update_draft_platform
  CD-RT-06: card-platform POST stores NULL for "default" platform
  CD-RT-07: publish-card POST calls CardDraftService.publish_draft
  CD-RT-08: publish-card POST returns draft.published_* in response body
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from app.models.card_draft import CardDraft


# ── Helpers ───────────────────────────────────────────────────────────────────

def _draft(
    draft_theme: str = "midnight",
    draft_variant: str = "compact",
    draft_platform: str | None = "instagram_square",
    published_theme: str | None = "midnight",
    published_variant: str | None = "compact",
    published_platform: str | None = None,
) -> CardDraft:
    d = CardDraft()
    d.id               = 7
    d.user_id          = 42
    d.card_type_id     = "player_card"
    d.instance_name    = "default"
    d.draft_theme      = draft_theme
    d.draft_variant    = draft_variant
    d.draft_platform   = draft_platform
    d.draft_data       = None
    d.published_theme  = published_theme
    d.published_variant  = published_variant
    d.published_platform = published_platform
    d.published_data   = None
    d.published_at     = datetime.now(timezone.utc)
    d.created_at       = datetime.now(timezone.utc)
    d.updated_at       = datetime.now(timezone.utc)
    return d


_CDS_PATH = "app.api.web_routes.dashboard._CardDraftService"


# ── CD-RT-01..04: card-editor GET context ────────────────────────────────────

class TestCardEditorGetContext:

    def _invoke_editor_get(self, draft: CardDraft):
        """
        Call the lfa_player_card_editor handler directly with mocked deps.
        Returns the TemplateResponse context dict.
        """
        import asyncio
        from app.api.web_routes.dashboard import lfa_player_card_editor

        mock_request = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 42
        mock_user.credit_balance = 500

        mock_license = MagicMock()
        mock_license.onboarding_completed = True
        mock_license.user_id = 42

        mock_db = MagicMock()
        # UserLicense query
        mock_db.query.return_value.filter.return_value.first.return_value = mock_license
        # SemesterEnrollment query for onboarding check
        mock_db.query.return_value.filter.return_value.first.return_value = mock_license

        captured = {}

        def fake_template_response(template_name, context):
            captured["context"] = context
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch(_CDS_PATH) as MockCDS, \
             patch("app.api.web_routes.dashboard.templates") as mock_tpl, \
             patch("app.api.web_routes.dashboard.SemesterEnrollment"), \
             patch("app.services.card_variant_service.get_all_variants", return_value=[]), \
             patch("app.services.card_color_service.get_owned_color_ids", return_value=set()), \
             patch("app.services.card_platform_service.build_platform_list", return_value=[]), \
             patch("app.services.card_constants.ANIMATED_EXPORT_CAPABLE", []), \
             patch("app.services.card_constants.CANVAS_SIZES", {}), \
             patch("app.services.card_constants.CARD_EDITOR_PLATFORM_IDS", []), \
             patch("app.services.highlight_video_service.build_youtube_embed_url", return_value=None):
            MockCDS.get_player_card_draft.return_value = draft
            mock_tpl.TemplateResponse.side_effect = fake_template_response

            # Patch the DB query chain so license lookup works
            db_mock = MagicMock()
            db_mock.query.return_value.filter.return_value.first.return_value = mock_license

            try:
                asyncio.run(lfa_player_card_editor(
                    request=mock_request,
                    db=db_mock,
                    user=mock_user,
                ))
            except Exception:
                pass  # template/redirect errors are OK; we only need the context

        return captured.get("context", {})

    def test_cd_rt_01_active_theme_from_draft(self):
        """CD-RT-01: active_card_theme comes from card_draft.draft_theme.

        Uses a free theme ('midnight') so the CE-2 owned-only filter never
        discards it — the test goal is draft-persistence, not premium unlock.
        """
        draft = _draft(draft_theme="midnight")
        ctx = self._invoke_editor_get(draft)
        assert ctx.get("active_card_theme") == "midnight", (
            f"active_card_theme must equal draft.draft_theme='midnight', got {ctx.get('active_card_theme')!r}"
        )

    def test_cd_rt_02_active_variant_from_draft(self):
        """CD-RT-02: active_card_variant comes from card_draft.draft_variant."""
        draft = _draft(draft_variant="showcase")
        ctx = self._invoke_editor_get(draft)
        assert ctx.get("active_card_variant") == "showcase"

    def test_cd_rt_03_active_platform_from_draft(self):
        """CD-RT-03: active_card_platform comes from card_draft.draft_platform."""
        draft = _draft(draft_platform="tiktok")
        ctx = self._invoke_editor_get(draft)
        assert ctx.get("active_card_platform") == "tiktok"

    def test_cd_rt_03b_null_platform_renders_as_default(self):
        """CD-RT-03b: NULL draft_platform renders as 'default' in template context."""
        draft = _draft(draft_platform=None)
        ctx = self._invoke_editor_get(draft)
        assert ctx.get("active_card_platform") == "default"

    def test_cd_rt_04_published_state_from_draft(self):
        """CD-RT-04: published_card_* context keys come from card_draft.published_*."""
        draft = _draft(
            published_theme="crimson",
            published_variant="showcase",
            published_platform=None,
        )
        ctx = self._invoke_editor_get(draft)
        assert ctx.get("published_card_theme") == "crimson"
        assert ctx.get("published_card_variant") == "showcase"
        assert ctx.get("published_card_platform") == "default"  # None → "default"


# ── CD-RT-05/06: card-platform POST ──────────────────────────────────────────

class TestCardPlatformPost:

    def _call_card_platform(self, platform_value: str):
        import asyncio
        from app.api.web_routes.dashboard import student_set_card_platform, _CardPlatformRequest

        mock_user = MagicMock()
        mock_user.id = 42
        mock_license = MagicMock()
        mock_license.user_id = 42
        mock_db = MagicMock()

        draft = _draft()

        with patch(_CDS_PATH) as MockCDS, \
             patch("app.api.web_routes.dashboard._get_lfa_license", return_value=mock_license):
            MockCDS.get_player_card_draft.return_value = draft
            payload = MagicMock()
            payload.platform = platform_value

            response = asyncio.run(student_set_card_platform(
                payload=payload,
                db=mock_db,
                user=mock_user,
            ))

        return MockCDS, response

    def test_cd_rt_05_calls_update_draft_platform(self):
        """CD-RT-05: card-platform POST calls CardDraftService.update_draft_platform."""
        MockCDS, _ = self._call_card_platform("instagram_square")
        MockCDS.update_draft_platform.assert_called_once()
        _, draft_arg, platform_arg = MockCDS.update_draft_platform.call_args[0]
        assert platform_arg == "instagram_square"

    def test_cd_rt_06_default_stored_as_null(self):
        """CD-RT-06: 'default' platform is stored as NULL in card_draft."""
        MockCDS, _ = self._call_card_platform("default")
        _, draft_arg, platform_arg = MockCDS.update_draft_platform.call_args[0]
        assert platform_arg is None, (
            "'default' platform must be stored as NULL (backward-compatible)"
        )


# ── CD-RT-07/08: publish-card POST ───────────────────────────────────────────

class TestPublishCardPost:

    def _call_publish_card(self, draft: CardDraft):
        import asyncio
        from app.api.web_routes.dashboard import student_publish_card

        mock_user = MagicMock()
        mock_user.id = 42
        mock_license = MagicMock()
        mock_db = MagicMock()

        with patch(_CDS_PATH) as MockCDS, \
             patch("app.api.web_routes.dashboard._get_lfa_license", return_value=mock_license):
            MockCDS.get_player_card_draft.return_value = draft
            MockCDS.publish_draft.return_value = draft

            response = asyncio.run(student_publish_card(
                db=mock_db,
                user=mock_user,
            ))

        return MockCDS, response

    def test_cd_rt_07_calls_publish_draft(self):
        """CD-RT-07: publish-card POST calls CardDraftService.publish_draft."""
        draft = _draft(
            draft_theme="gold",
            draft_variant="compact",
            draft_platform="tiktok",
        )
        MockCDS, _ = self._call_publish_card(draft)
        MockCDS.publish_draft.assert_called_once()

    def test_cd_rt_08_response_contains_published_fields(self):
        """CD-RT-08: publish-card response body reflects draft.published_* fields."""
        import json
        draft = _draft(
            published_theme="gold",
            published_variant="compact",
            published_platform="tiktok",
        )
        _, response = self._call_publish_card(draft)
        body = json.loads(response.body)
        assert body["ok"] is True
        assert body["published"]["theme"] == "gold"
        assert body["published"]["variant"] == "compact"
        assert body["published"]["platform"] == "tiktok"
