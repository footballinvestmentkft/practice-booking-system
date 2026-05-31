"""
PG — Public Profile Grid Designer tests.

Covers profile_grid_service + CardDraftService grid extensions +
dashboard designer route + player_profile.html rendering.
All tests use MagicMock — no real DB or HTTP server required.

Test list:
  TestSlotRegistry       PG-02  9 slots across left/right/bottom zones
                         PG-03  empty slot state is_empty=True
  TestSlotYouTube        PG-04  YouTube URL → video_youtube module saved
  TestSlotTikTok         PG-05  TikTok canonical URL → video_tiktok module saved
  TestSlotValidation     PG-06  Invalid URL → ValueError, draft untouched
                         PG-07  TikTok short URL → ValueError
  TestDraftIsolation     PG-08  save draft slot → published_data not modified
                         PG-10  remove draft slot → published_data not modified
  TestPublishGrid        PG-09  publish_draft → profile_grid in published_data
                         PG-11  remove + publish → profile_grid cleared from public
  TestIsPublishedGrid    PG-15  is_published False when slot video_id differs
                         PG-16  is_published False when slot provider differs
  TestPubDataIntegrity   PG-17  publish preserves other published_data keys
  TestSlotIdGuards       PG-18  invalid slot_id → ValueError (route returns 404)
                         PG-19  MAX_SLOTS guard fires when grid already full
  TestDesignerRoute      PG-01  designer GET returns 9 draft_slots in context
  TestPublicTemplate     PG-12  player_profile.html references profile_grid_slots
                         PG-13  legacy highlight_video fallback block present
                         PG-14  right_grid_slots block precedes legacy video block
                         PG-20  TikTok grid slot uses CTA link, no iframe in macro
                         PG-21  YouTube grid slot renders youtube-nocookie iframe
                         PG-22  all iframes carry sandbox attribute
  TestRegressions        PG-23  lfa_public_profile_editor.html retains HVE elements
                         PG-24  psp-tiktok-cta present on public profile (PR #170)
                         PG-25  GL grid layout invariants preserved
  TestTikTokThumbnail    TT-P-01  build_video_module stores thumbnail for TikTok
                         TT-P-02  thumbnail_url ignored for YouTube
                         TT-P-03  HTTP thumbnail raises ValueError
                         TT-P-04  invalid thumbnail URL raises ValueError
                         TT-P-05  no thumbnail → backward-compat fingerprint "tiktok:ID"
                         TT-P-06  thumbnail present → "tiktok:ID:<hash8>" fingerprint
                         TT-P-07  fingerprint changes when thumbnail changes
                         TT-P-08  build_module factory forwards thumbnail for video_tiktok
                         TT-P-09  build_module factory ignores thumbnail for video_youtube
                         TT-P-10  set_draft_slot stores thumbnail in draft profile_grid
                         TT-P-11  set_draft_slot HTTP thumbnail raises ValueError
                         TT-P-12  is_published detects thumbnail change
                         TT-P-13  route 422 on HTTP thumbnail_url
                         TT-P-14  route 200 with HTTPS thumbnail returns thumbnail_url in response
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.models.card_draft import CardDraft
from app.services.card_draft_service import CardDraftService
from app.services.profile_grid_service import (
    MAX_SLOTS,
    SLOT_IDS,
    SLOT_REGISTRY,
    VALID_WIDGET_TYPES,
    _module_fingerprint,
    build_draft_grid_state,
    build_image_module,
    build_module,
    build_published_grid_state,
    build_text_module,
    build_video_module,
    grid_fingerprint,
    move_slot,
    set_slot,
)

# ── URL fixtures ───────────────────────────────────────────────────────────────

_YT_URL  = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
_YT_VID  = "dQw4w9WgXcQ"
_TT_URL  = "https://www.tiktok.com/@user/video/7123456789012345678"
_TT_VID  = "7123456789012345678"
_TEST_UID = 42

# ── Template fixtures (static text loaded once) ────────────────────────────────

_TMPL_DIR = Path(__file__).parent.parent.parent / "app" / "templates"
_PLAYER_HTML = (_TMPL_DIR / "public" / "player_profile.html").read_text(encoding="utf-8")
_EDITOR_HTML = (_TMPL_DIR / "dashboard" / "lfa_public_profile_editor.html").read_text(encoding="utf-8")
_CARD_EDITOR_HTML = (_TMPL_DIR / "dashboard_card_editor.html").read_text(encoding="utf-8")


# ── Draft builder helper ───────────────────────────────────────────────────────

def _draft(
    draft_theme: str = "default",
    draft_variant: str = "fclassic",
    published_theme: str | None = "default",
    published_variant: str | None = "fclassic",
    draft_data: dict | None = None,
    published_data: dict | None = None,
) -> CardDraft:
    d = CardDraft()
    d.id                 = 7
    d.user_id            = _TEST_UID
    d.card_type_id       = "player_card"
    d.instance_name      = "default"
    d.draft_theme        = draft_theme
    d.draft_variant      = draft_variant
    d.draft_platform     = None
    d.draft_data         = draft_data
    d.published_theme    = published_theme
    d.published_variant  = published_variant
    d.published_platform = None
    d.published_data     = published_data
    d.published_at       = datetime.now(timezone.utc) if published_theme else None
    d.created_at         = datetime.now(timezone.utc)
    d.updated_at         = datetime.now(timezone.utc)
    return d


# ── PG-02/03: Slot registry ────────────────────────────────────────────────────

class TestSlotRegistry:

    def test_pg_02_slot_registry_has_15_slots_across_five_zones(self):
        """PG-02: SLOT_REGISTRY has 15 slots: 3×side_a + 3×side_b + 3×side_c + 3×side_d + 3×bottom."""
        assert len(SLOT_REGISTRY) == 15
        assert MAX_SLOTS == 15
        zones = {s["zone"] for s in SLOT_REGISTRY}
        assert zones == {"side_a", "side_b", "side_c", "side_d", "bottom"}
        for zone in zones:
            assert len([s for s in SLOT_REGISTRY if s["zone"] == zone]) == 3

    def test_pg_03_empty_draft_grid_state_all_slots_empty(self):
        """PG-03: build_draft_grid_state on an empty draft returns 15 slots, all is_empty."""
        draft = _draft()
        slots = build_draft_grid_state(draft)
        assert len(slots) == 15
        for slot in slots:
            assert slot["is_empty"] is True
            assert slot["module"] is None


# ── PG-04/05: Slot save ────────────────────────────────────────────────────────

class TestSlotYouTube:

    def test_pg_04_youtube_url_saved_as_video_youtube_module(self):
        """PG-04: set_draft_slot writes a video_youtube module into draft_data.profile_grid."""
        draft = _draft()
        db = MagicMock()
        CardDraftService.set_draft_slot(db, draft, "side_b_1", _YT_URL, "My goal")
        pg = (draft.draft_data or {}).get("profile_grid")
        assert pg is not None
        slots = pg.get("slots", [])
        assert len(slots) == 1
        entry = slots[0]
        assert entry["slot_id"] == "side_b_1"
        assert entry["module"]["type"] == "video_youtube"
        assert entry["module"]["provider"] == "youtube"
        assert entry["module"]["video_id"] == _YT_VID
        assert entry["module"]["title"] == "My goal"
        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(draft)


class TestSlotTikTok:

    def test_pg_05_tiktok_canonical_url_saved_as_video_tiktok_module(self):
        """PG-05: set_draft_slot writes a video_tiktok module for canonical TikTok URL."""
        draft = _draft()
        db = MagicMock()
        CardDraftService.set_draft_slot(db, draft, "side_c_1", _TT_URL, "Skill clip")
        pg = (draft.draft_data or {}).get("profile_grid")
        assert pg is not None
        entry = pg["slots"][0]
        assert entry["slot_id"] == "side_c_1"
        assert entry["module"]["type"] == "video_tiktok"
        assert entry["module"]["provider"] == "tiktok"
        assert entry["module"]["video_id"] == _TT_VID
        assert entry["module"]["source_url"] == _TT_URL


# ── PG-06/07: URL validation ──────────────────────────────────────────────────

class TestSlotValidation:

    def test_pg_06_invalid_url_raises_value_error_draft_untouched(self):
        """PG-06: Non-video URL raises ValueError; draft_data stays None, no commit."""
        draft = _draft(draft_data=None)
        db = MagicMock()
        with pytest.raises(ValueError):
            CardDraftService.set_draft_slot(db, draft, "side_b_1", "https://example.com/not-a-video")
        assert draft.draft_data is None
        db.commit.assert_not_called()

    def test_pg_07_tiktok_short_url_rejected_with_informative_error(self):
        """PG-07: TikTok short URL (vm.tiktok.com) raises ValueError with 'short' in message."""
        draft = _draft(draft_data=None)
        db = MagicMock()
        with pytest.raises(ValueError, match="full TikTok"):
            CardDraftService.set_draft_slot(db, draft, "side_b_1", "https://vm.tiktok.com/ZMeABCDEF/")
        assert draft.draft_data is None


# ── PG-08/10: Draft isolation ─────────────────────────────────────────────────

class TestDraftIsolation:

    def test_pg_08_set_draft_slot_does_not_touch_published_data(self):
        """PG-08: Saving a draft slot never modifies published_data."""
        original_pub = {"highlight_video": {"provider": "youtube", "video_id": "pub123"}}
        draft = _draft(published_data=original_pub)
        db = MagicMock()
        CardDraftService.set_draft_slot(db, draft, "side_b_1", _YT_URL)
        assert draft.published_data == original_pub

    def test_pg_10_remove_draft_slot_does_not_touch_published_data(self):
        """PG-10: Removing a draft slot never modifies published_data."""
        pg = {"version": 1, "slots": [{"slot_id": "side_b_1", "module": {
            "provider": "youtube", "video_id": "abc", "type": "video_youtube", "title": ""
        }}]}
        original_pub = {"profile_grid": pg}
        draft = _draft(draft_data={"profile_grid": pg}, published_data=original_pub)
        db = MagicMock()
        CardDraftService.remove_draft_slot(db, draft, "side_b_1")
        assert draft.published_data == original_pub


# ── PG-09/11: Publish grid ────────────────────────────────────────────────────

class TestPublishGrid:

    def test_pg_09_publish_draft_copies_profile_grid_into_published_data(self):
        """PG-09: publish_draft copies draft_data.profile_grid into published_data."""
        draft = _draft()
        db = MagicMock()
        CardDraftService.set_draft_slot(db, draft, "side_b_1", _YT_URL, commit=False)
        CardDraftService.publish_draft(db, draft)
        pub_pg = (draft.published_data or {}).get("profile_grid")
        assert pub_pg is not None
        assert pub_pg["slots"][0]["slot_id"] == "side_b_1"
        assert pub_pg["slots"][0]["module"]["provider"] == "youtube"

    def test_pg_11_remove_and_publish_clears_profile_grid_from_published_data(self):
        """PG-11: Removing a slot from draft and publishing clears it from published_data."""
        draft = _draft()
        db = MagicMock()
        CardDraftService.set_draft_slot(db, draft, "side_b_1", _YT_URL, commit=False)
        CardDraftService.publish_draft(db, draft, commit=False)
        assert (draft.published_data or {}).get("profile_grid") is not None

        CardDraftService.remove_draft_slot(db, draft, "side_b_1", commit=False)
        CardDraftService.publish_draft(db, draft)
        assert (draft.published_data or {}).get("profile_grid") is None


# ── PG-15/16: is_published with grid ─────────────────────────────────────────

class TestIsPublishedGrid:

    def _pg(self, slot_id: str, provider: str, video_id: str) -> dict:
        return {"version": 1, "slots": [
            {"slot_id": slot_id, "module": {"provider": provider, "video_id": video_id}}
        ]}

    def test_pg_15_is_published_false_when_slot_video_id_differs(self):
        """PG-15: is_published False when draft grid slot video_id != published slot video_id."""
        draft = _draft(
            draft_data=    {"profile_grid": self._pg("side_b_1", "youtube", "draft_vid")},
            published_data={"profile_grid": self._pg("side_b_1", "youtube", "pub_vid")},
        )
        assert CardDraftService.is_published(draft) is False

    def test_pg_16_is_published_false_when_slot_provider_differs(self):
        """PG-16: is_published False when draft provider != published provider (same video_id)."""
        draft = _draft(
            draft_data=    {"profile_grid": self._pg("side_b_1", "youtube", "abc123")},
            published_data={"profile_grid": self._pg("side_b_1", "tiktok",  "abc123")},
        )
        assert CardDraftService.is_published(draft) is False


# ── PG-17: Published data integrity ──────────────────────────────────────────

class TestPubDataIntegrity:

    def test_pg_17_publish_merges_profile_grid_preserving_other_keys(self):
        """PG-17: publish_draft merges profile_grid without wholesale published_data replacement.

        publish_draft only writes highlight_video and profile_grid; other keys survive.
        """
        draft = _draft(published_data={"some_future_key": "preserved_value"})
        draft.draft_data = {"profile_grid": {"version": 1, "slots": [
            {"slot_id": "side_b_1", "module": {"provider": "youtube", "video_id": "grid_vid"}}
        ]}}
        db = MagicMock()
        CardDraftService.publish_draft(db, draft)
        assert draft.published_data.get("some_future_key") == "preserved_value", (
            "publish_draft must not wipe keys outside the highlight_video/profile_grid merge set"
        )
        assert draft.published_data.get("profile_grid") is not None


# ── PG-18/19: Slot ID guards ──────────────────────────────────────────────────

class TestSlotIdGuards:

    def test_pg_18_invalid_slot_id_raises_value_error(self):
        """PG-18: Unknown slot_id raises ValueError (dashboard route returns 404)."""
        draft = _draft()
        db = MagicMock()
        with pytest.raises(ValueError, match="Unknown slot_id"):
            CardDraftService.set_draft_slot(db, draft, "not_a_real_slot", _YT_URL)

    def test_pg_19_max_slots_guard_fires_when_grid_is_full(self):
        """PG-19: set_slot raises ValueError when 15 existing non-overlapping entries are present."""
        # Craft a grid with 15 phantom entries (not in SLOT_IDS) so "side_b_1" is a new slot
        full_grid = {"version": 1, "slots": [
            {"slot_id": f"zone_phantom_{i}", "module": {}} for i in range(15)
        ]}
        with pytest.raises(ValueError, match="Maximum"):
            set_slot(full_grid, "side_b_1", {})


# ── PG-01: Designer GET route ─────────────────────────────────────────────────

class TestDesignerRoute:

    def test_pg_01_designer_get_returns_15_draft_slots_in_context(self):
        """PG-01: GET /dashboard/.../public-profile-editor renders with 15 draft_slots (all empty)."""
        from app.api.web_routes.dashboard import lfa_public_profile_editor

        mock_request = MagicMock()
        mock_user = MagicMock()
        mock_user.id = _TEST_UID
        mock_license = MagicMock()
        mock_license.user_id = _TEST_UID
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_license

        draft = _draft()
        captured: dict = {}

        def fake_response(req, template_name, context):
            captured["context"] = context
            resp = MagicMock()
            resp.status_code = 200
            return resp

        with patch("app.api.web_routes.dashboard._CardDraftService") as MockCDS, \
             patch("app.api.web_routes.dashboard.templates") as mock_tpl, \
             patch("app.api.web_routes.dashboard._get_lfa_license", return_value=mock_license):
            MockCDS.get_player_card_draft.return_value = draft
            MockCDS.is_published.return_value = False
            mock_tpl.TemplateResponse.side_effect = fake_response
            asyncio.run(lfa_public_profile_editor(
                request=mock_request, db=mock_db, user=mock_user,
            ))

        ctx = captured.get("context", {})
        draft_slots = ctx.get("draft_slots", [])
        assert len(draft_slots) == 15, f"Expected 15 draft_slots, got {len(draft_slots)}"
        assert all(s["is_empty"] is True for s in draft_slots)


# ── PG-12..14 / PG-20..22: Public profile template ───────────────────────────

class TestPublicTemplate:

    def test_pg_12_player_profile_references_profile_grid_slots(self):
        """PG-12: player_profile.html uses profile_grid_slots for grid slot rendering.

        Fix B: left rail uses pre-computed left_grid_slots (selectattr-based) to enable
        placeholder suppression; slot.zone inline check replaced by selectattr.
        """
        assert "profile_grid_slots" in _PLAYER_HTML, (
            "player_profile.html must reference the profile_grid_slots context variable"
        )
        assert 'selectattr("zone", "equalto"' in _PLAYER_HTML, (
            "player_profile.html must filter slots by zone via selectattr"
        )
        assert "left_grid_slots" in _PLAYER_HTML, (
            "player_profile.html must define left_grid_slots for placeholder suppression"
        )

    def test_pg_13_legacy_highlight_video_fallback_block_present(self):
        """PG-13: Legacy highlight_video block is retained as fallback when no grid slots."""
        assert "right_grid_slots" in _PLAYER_HTML, (
            "player_profile.html must define right_grid_slots for the override check"
        )
        assert "highlight_video.provider" in _PLAYER_HTML, (
            "Legacy highlight_video rendering must remain in player_profile.html"
        )

    def test_pg_14_right_grid_slots_precedes_legacy_highlight_video(self):
        """PG-14: right_grid_slots override block appears before the legacy Highlight Video section."""
        grid_pos   = _PLAYER_HTML.index("right_grid_slots")
        legacy_pos = _PLAYER_HTML.index("Highlight Video")
        assert grid_pos < legacy_pos, (
            "right_grid_slots block must precede the legacy 'Highlight Video' section"
        )

    def test_pg_20_tiktok_grid_slot_macro_uses_cta_link_not_iframe(self):
        """PG-20: render_slot_module macro TikTok branch uses <a href>, never <iframe>."""
        macro_start = _PLAYER_HTML.index("{% macro render_slot_module")
        macro_end   = _PLAYER_HTML.index("{% endmacro %}", macro_start) + len("{% endmacro %}")
        macro_body  = _PLAYER_HTML[macro_start:macro_end]

        assert 'provider == "tiktok"' in macro_body
        tiktok_pos     = macro_body.index('provider == "tiktok"')
        tiktok_section = macro_body[tiktok_pos:]
        assert "<iframe" not in tiktok_section, (
            "TikTok grid slot must not use <iframe — link-only CTA required (no CSP expansion)"
        )
        assert "source_url" in tiktok_section, (
            "TikTok CTA must use module.source_url as the href"
        )

    def test_pg_21_youtube_grid_slot_macro_renders_nocookie_iframe(self):
        """PG-21: render_slot_module macro YouTube branch renders youtube-nocookie.com iframe."""
        macro_start = _PLAYER_HTML.index("{% macro render_slot_module")
        macro_end   = _PLAYER_HTML.index("{% endmacro %}", macro_start) + len("{% endmacro %}")
        macro_body  = _PLAYER_HTML[macro_start:macro_end]
        assert "youtube-nocookie.com/embed" in macro_body, (
            "YouTube grid slot must use youtube-nocookie.com/embed iframe"
        )
        assert "<iframe" in macro_body

    def test_pg_22_all_iframes_carry_sandbox_attribute(self):
        """PG-22: Every <iframe> in player_profile.html includes a sandbox= attribute."""
        import re
        iframes = re.findall(r'<iframe[^>]+>', _PLAYER_HTML)
        assert len(iframes) > 0, "No iframes found in player_profile.html"
        for iframe in iframes:
            assert "sandbox=" in iframe, (
                f"iframe missing sandbox attribute (XSS risk): {iframe[:120]!r}"
            )


# ── PG-23..25: Regressions ────────────────────────────────────────────────────

class TestRegressions:

    def test_pg_23_hve_card_editor_template_retains_highlight_video_elements(self):
        """PG-23: dashboard_card_editor.html (HVE home) still contains HVE elements (PR #169 regression)."""
        assert "Highlight Video" in _CARD_EDITOR_HTML, (
            "HVE: 'Highlight Video' section missing from dashboard_card_editor.html — regression"
        )
        assert "draft_highlight_video" in _CARD_EDITOR_HTML, (
            "HVE: draft_highlight_video context variable missing from dashboard_card_editor.html"
        )
        # Profile grid editor has its own template; verify it has draft_slots
        assert "draft_slots" in _EDITOR_HTML, (
            "Profile grid draft_slots reference missing from lfa_public_profile_editor.html"
        )

    def test_pg_24_tiktok_cta_class_present_on_public_profile(self):
        """PG-24: psp-tiktok-cta class present in player_profile.html (PR #170 regression)."""
        assert "psp-tiktok-cta" in _PLAYER_HTML, (
            "psp-tiktok-cta class missing — regression from PR #170 TikTok integration"
        )

    def test_pg_25_gl_grid_layout_invariants_preserved(self):
        """PG-25: Core GL grid layout invariants still hold (PR #171 regression)."""
        assert "36px" in _PLAYER_HTML, "GL-01: base laptop grid must use 36px slot columns"
        assert "56px" in _PLAYER_HTML, "GL-04: large desktop (≥1440px) must use 56px slot columns"
        assert "grid-area: l-slot" in _PLAYER_HTML, "GL-02: l-slot grid-area must be defined"
        assert "grid-area: r-slot" in _PLAYER_HTML, "GL-02: r-slot grid-area must be defined"
        assert 'class="psp-l-slot"' in _PLAYER_HTML, "GL-03: psp-l-slot placeholder div must be present"
        assert 'class="psp-r-slot"' in _PLAYER_HTML, "GL-03: psp-r-slot placeholder div must be present"


# ── SN-01..18: Slot Naming — 4B neutral ID system ────────────────────────────

class TestSlotNaming:
    """SN-* — Verify that slot IDs are layout-neutral and the 4B naming scheme is correct."""

    def test_sn_01_slot_registry_has_15_slots(self):
        """SN-01: SLOT_REGISTRY contains exactly 15 slots."""
        assert len(SLOT_REGISTRY) == 15

    def test_sn_02_max_slots_is_15(self):
        """SN-02: MAX_SLOTS == 15."""
        assert MAX_SLOTS == 15

    def test_sn_03_side_a_1_exists(self):
        """SN-03: side_a_1 is a valid slot_id."""
        assert "side_a_1" in SLOT_IDS

    def test_sn_04_side_b_1_exists(self):
        """SN-04: side_b_1 is a valid slot_id."""
        assert "side_b_1" in SLOT_IDS

    def test_sn_05_side_c_1_exists(self):
        """SN-05: side_c_1 is a valid slot_id."""
        assert "side_c_1" in SLOT_IDS

    def test_sn_06_side_d_1_exists(self):
        """SN-06: side_d_1 is a valid slot_id."""
        assert "side_d_1" in SLOT_IDS

    def test_sn_07_bottom_slots_exist(self):
        """SN-07: bottom_a, bottom_b, bottom_c are valid slot_ids."""
        assert "bottom_a" in SLOT_IDS
        assert "bottom_b" in SLOT_IDS
        assert "bottom_c" in SLOT_IDS

    def test_sn_08_no_left_prefix_in_slot_ids(self):
        """SN-08: No slot_id starts with 'left_' — physical naming forbidden."""
        for sid in SLOT_IDS:
            assert not sid.startswith("left_"), f"Physical slot_id found: {sid!r}"

    def test_sn_09_no_right_prefix_in_slot_ids(self):
        """SN-09: No slot_id starts with 'right_' — physical naming forbidden."""
        for sid in SLOT_IDS:
            assert not sid.startswith("right_"), f"Physical slot_id found: {sid!r}"

    def test_sn_10_no_outer_or_inner_in_slot_ids(self):
        """SN-10: No slot_id contains 'outer' or 'inner' — position-relative naming forbidden."""
        for sid in SLOT_IDS:
            assert "outer" not in sid, f"Position-relative slot_id found: {sid!r}"
            assert "inner" not in sid, f"Position-relative slot_id found: {sid!r}"

    def test_sn_11_featured_card_not_in_slot_ids(self):
        """SN-11: 'featured_card' is NOT a slot_id — it is a read-only anchor, not editable."""
        assert "featured_card" not in SLOT_IDS

    def test_sn_12_ui_label_differs_from_slot_id(self):
        """SN-12: Each slot's label is distinct from its slot_id (label is a human string)."""
        for slot in SLOT_REGISTRY:
            assert slot["label"] != slot["slot_id"], (
                f"slot_id and label must differ: {slot['slot_id']!r}"
            )

    def test_sn_13_designer_template_renders_all_five_zones(self):
        """SN-13: lfa_public_profile_editor.html references Side A, Side B, Featured Card, Side C, Side D."""
        assert "Side A" in _EDITOR_HTML, "Side A zone label missing from designer"
        assert "Side B" in _EDITOR_HTML, "Side B zone label missing from designer"
        assert "Featured Card" in _EDITOR_HTML, "Featured Card anchor missing from designer"
        assert "Side C" in _EDITOR_HTML, "Side C zone label missing from designer"
        assert "Side D" in _EDITOR_HTML, "Side D zone label missing from designer"
        assert "Bottom Row" in _EDITOR_HTML, "Bottom Row zone label missing from designer"

    def test_sn_14_public_profile_template_handles_new_lane_zones(self):
        """SN-14: player_profile.html references side_b, side_c zone names for grid rendering."""
        assert "side_b" in _PLAYER_HTML, "side_b zone missing from player_profile.html"
        assert "side_c" in _PLAYER_HTML, "side_c zone missing from player_profile.html"

    def test_sn_15_old_left_1_slot_id_raises_value_error(self):
        """SN-15: validate_slot_id raises ValueError for legacy 'left_1' slot_id (no alias)."""
        from app.services.profile_grid_service import validate_slot_id
        with pytest.raises(ValueError, match="Unknown slot_id"):
            validate_slot_id("left_1")

    def test_sn_16_youtube_saveable_to_side_b_1(self):
        """SN-16: YouTube URL can be saved to side_b_1 without errors."""
        draft = _draft()
        db = MagicMock()
        CardDraftService.set_draft_slot(db, draft, "side_b_1", _YT_URL, "Goal reel")
        entry = (draft.draft_data or {}).get("profile_grid", {}).get("slots", [{}])[0]
        assert entry["slot_id"] == "side_b_1"
        assert entry["module"]["provider"] == "youtube"

    def test_sn_17_tiktok_saveable_to_side_c_1(self):
        """SN-17: TikTok URL can be saved to side_c_1 without errors."""
        draft = _draft()
        db = MagicMock()
        CardDraftService.set_draft_slot(db, draft, "side_c_1", _TT_URL, "Skill clip")
        entry = (draft.draft_data or {}).get("profile_grid", {}).get("slots", [{}])[0]
        assert entry["slot_id"] == "side_c_1"
        assert entry["module"]["provider"] == "tiktok"

    def test_sn_18_dashboard_context_profile_grid_total_slots_is_15(self):
        """SN-18: spec_dashboard context passes profile_grid_total_slots == 15 for LFA."""
        from app.api.web_routes.dashboard import spec_dashboard
        import asyncio
        from unittest.mock import patch

        _BASE = "app.api.web_routes.dashboard"
        user = MagicMock()
        user.id = 77
        user.role = __import__("app.models.user", fromlist=["UserRole"]).UserRole.STUDENT
        user.date_of_birth = None
        lic = MagicMock()
        lic.id = 1
        lic.onboarding_completed = True
        lic.football_skills = None
        lic.public_card_platform = None
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [lic, None, None]
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        db.query.return_value.filter.return_value.all.return_value = []

        with (
            patch(f"{_BASE}.templates") as mock_tmpl,
            patch(f"{_BASE}._CardDraftService") as mock_cds,
            patch(f"{_BASE}._build_published_grid_state", return_value=None),
        ):
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            mock_cds.get_player_card_draft.return_value = MagicMock(published_data={})
            mock_cds.is_published.return_value = False
            asyncio.run(spec_dashboard(
                request=MagicMock(), spec_type="lfa-football-player", db=db, user=user
            ))
            ctx = mock_tmpl.TemplateResponse.call_args.args[1]
        assert ctx["profile_grid_total_slots"] == 15


# ── WT-01..17: Reorder — zone-level drag-and-drop ─────────────────────────────

from app.services.profile_grid_service import reorder_zone  # noqa: E402


class TestReorderZone:

    def test_wt_01_reorder_two_filled_slots_swaps_modules(self):
        """WT-01: reorder_zone with 2 filled slots in reversed order swaps their modules."""
        profile_grid = {"version": 1, "slots": [
            {"slot_id": "side_b_1", "module": {"provider": "youtube", "video_id": "VID_A"}},
            {"slot_id": "side_b_2", "module": {"provider": "tiktok",  "video_id": "VID_B"}},
        ]}
        # Request: put side_b_2 first (TikTok should become side_b_1)
        result = reorder_zone(profile_grid, "side_b", ["side_b_2", "side_b_1"])
        slot_map = {s["slot_id"]: s["module"] for s in result["slots"]}
        assert slot_map["side_b_1"]["provider"] == "tiktok",  "side_b_1 should now be TikTok"
        assert slot_map["side_b_1"]["video_id"] == "VID_B"
        assert slot_map["side_b_2"]["provider"] == "youtube", "side_b_2 should now be YouTube"
        assert slot_map["side_b_2"]["video_id"] == "VID_A"

    def test_wt_02_reorder_one_filled_slot_same_position_is_noop(self):
        """WT-02: reorder_zone with 1 filled slot in the SAME position returns the same object (no-op)."""
        profile_grid = {"version": 1, "slots": [
            {"slot_id": "side_b_1", "module": {"provider": "youtube", "video_id": "VID_A"}},
        ]}
        # side_b_1 stays at position 0 — no movement
        result = reorder_zone(profile_grid, "side_b", ["side_b_1", "side_b_2", "side_b_3"])
        assert result is profile_grid, "Should return the same object when position unchanged"

    def test_wt_03_reorder_zero_filled_slots_is_noop(self):
        """WT-03: reorder_zone with no filled slots in zone returns same profile_grid (no-op)."""
        profile_grid = {"version": 1, "slots": [
            {"slot_id": "side_a_1", "module": {"provider": "youtube", "video_id": "OTHER"}},
        ]}
        result = reorder_zone(profile_grid, "side_b", ["side_b_1", "side_b_2"])
        assert result is profile_grid

    def test_wt_04_reorder_unknown_zone_raises_value_error(self):
        """WT-04: reorder_zone raises ValueError for an unrecognised zone name."""
        profile_grid = {"version": 1, "slots": []}
        with pytest.raises(ValueError, match="Unknown zone"):
            reorder_zone(profile_grid, "not_a_zone", [])

    def test_wt_05_reorder_slot_from_wrong_zone_raises_value_error(self):
        """WT-05: reorder_zone raises ValueError when a slot_id belongs to a different zone."""
        profile_grid = {"version": 1, "slots": [
            {"slot_id": "side_b_1", "module": {"provider": "youtube", "video_id": "V"}},
            {"slot_id": "side_b_2", "module": {"provider": "youtube", "video_id": "W"}},
        ]}
        with pytest.raises(ValueError, match="does not belong to zone"):
            reorder_zone(profile_grid, "side_b", ["side_b_1", "side_a_1"])  # side_a_1 is wrong zone

    def test_wt_06_reorder_three_filled_slots_redistributes_correctly(self):
        """WT-06: reorder_zone with 3 filled slots assigns modules in new order to sorted positions."""
        profile_grid = {"version": 1, "slots": [
            {"slot_id": "side_c_1", "module": {"provider": "youtube", "video_id": "C1"}},
            {"slot_id": "side_c_2", "module": {"provider": "tiktok",  "video_id": "C2"}},
            {"slot_id": "side_c_3", "module": {"provider": "youtube", "video_id": "C3"}},
        ]}
        # New order: C3, C1, C2
        result = reorder_zone(profile_grid, "side_c", ["side_c_3", "side_c_1", "side_c_2"])
        slot_map = {s["slot_id"]: s["module"] for s in result["slots"]}
        assert slot_map["side_c_1"]["video_id"] == "C3"
        assert slot_map["side_c_2"]["video_id"] == "C1"
        assert slot_map["side_c_3"]["video_id"] == "C2"

    def test_wt_07_reorder_none_profile_grid_returns_none(self):
        """WT-07: reorder_zone with profile_grid=None returns None (no-op)."""
        result = reorder_zone(None, "side_b", ["side_b_1"])
        assert result is None

    def test_wt_08_reorder_preserves_other_zone_slots_unchanged(self):
        """WT-08: reorder_zone does not touch slots outside the target zone."""
        profile_grid = {"version": 1, "slots": [
            {"slot_id": "side_a_1", "module": {"provider": "youtube", "video_id": "A1"}},
            {"slot_id": "side_b_1", "module": {"provider": "youtube", "video_id": "B1"}},
            {"slot_id": "side_b_2", "module": {"provider": "tiktok",  "video_id": "B2"}},
        ]}
        result = reorder_zone(profile_grid, "side_b", ["side_b_2", "side_b_1"])
        slot_map = {s["slot_id"]: s["module"] for s in result["slots"]}
        assert slot_map.get("side_a_1", {}).get("video_id") == "A1", (
            "side_a_1 must be preserved unchanged after reordering side_b"
        )

    def test_wt_09_reorder_empty_slot_ids_list_is_noop(self):
        """WT-09: reorder_zone with empty slot_ids list is a no-op."""
        profile_grid = {"version": 1, "slots": [
            {"slot_id": "side_b_1", "module": {"provider": "youtube", "video_id": "VID"}},
        ]}
        result = reorder_zone(profile_grid, "side_b", [])
        assert result is profile_grid


class TestReorderDraftZone:

    def test_wt_10_reorder_draft_zone_mutates_draft_data(self):
        """WT-10: CardDraftService.reorder_draft_zone mutates draft_data and calls db.commit."""
        draft = _draft(draft_data={"profile_grid": {
            "version": 1, "slots": [
                {"slot_id": "side_b_1", "module": {"provider": "youtube", "video_id": "V1"}},
                {"slot_id": "side_b_2", "module": {"provider": "tiktok",  "video_id": "V2"}},
            ],
        }})
        db = MagicMock()
        CardDraftService.reorder_draft_zone(db, draft, "side_b", ["side_b_2", "side_b_1"])
        pg = (draft.draft_data or {}).get("profile_grid", {})
        slot_map = {s["slot_id"]: s["module"] for s in pg.get("slots", [])}
        assert slot_map["side_b_1"]["video_id"] == "V2", "V2 should now be at side_b_1"
        assert slot_map["side_b_2"]["video_id"] == "V1", "V1 should now be at side_b_2"
        db.commit.assert_called_once()

    def test_wt_11_reorder_draft_zone_one_slot_is_noop_no_commit(self):
        """WT-11: reorder_draft_zone with 1 filled slot does NOT call db.commit."""
        draft = _draft(draft_data={"profile_grid": {
            "version": 1, "slots": [
                {"slot_id": "side_b_1", "module": {"provider": "youtube", "video_id": "V1"}},
            ],
        }})
        db = MagicMock()
        CardDraftService.reorder_draft_zone(db, draft, "side_b", ["side_b_1", "side_b_2", "side_b_3"])
        db.commit.assert_not_called()


class TestReorderRoute:

    _BASE = "app.api.web_routes.dashboard"

    def _run_reorder(self, payload_dict, draft, *, license_present=True):
        from app.api.web_routes.dashboard import lfa_profile_editor_reorder_zone
        from app.api.web_routes.dashboard import _ReorderRequest
        mock_license = MagicMock() if license_present else None
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = mock_license
        payload = _ReorderRequest(**payload_dict)
        with patch(f"{self._BASE}._get_lfa_license", return_value=mock_license), \
             patch(f"{self._BASE}._CardDraftService") as MockCDS:
            MockCDS.get_player_card_draft.return_value = draft
            # reorder_draft_zone: actually call the real service method
            MockCDS.reorder_draft_zone.side_effect = lambda db, d, zone, slot_ids, **kw: \
                CardDraftService.reorder_draft_zone(db, d, zone, slot_ids, commit=False)
            return asyncio.run(lfa_profile_editor_reorder_zone(payload=payload, db=db, user=MagicMock()))

    def test_wt_12_reorder_two_filled_slots_returns_reordered(self):
        """WT-12: POST /reorder with 2 filled slots returns {"ok": true, "status": "reordered"}."""
        draft = _draft(draft_data={"profile_grid": {
            "version": 1, "slots": [
                {"slot_id": "side_b_1", "module": {"provider": "youtube", "video_id": "V1"}},
                {"slot_id": "side_b_2", "module": {"provider": "tiktok",  "video_id": "V2"}},
            ],
        }})
        resp = self._run_reorder({"zone": "side_b", "slot_ids": ["side_b_2", "side_b_1"]}, draft)
        body = resp.body
        import json
        data = json.loads(body)
        assert data["ok"] is True
        assert data["status"] == "reordered"

    def test_wt_13_reorder_one_filled_slot_returns_noop(self):
        """WT-13: POST /reorder with 1 filled slot returns {"ok": true, "status": "noop"}."""
        draft = _draft(draft_data={"profile_grid": {
            "version": 1, "slots": [
                {"slot_id": "side_b_1", "module": {"provider": "youtube", "video_id": "V1"}},
            ],
        }})
        resp = self._run_reorder({"zone": "side_b", "slot_ids": ["side_b_1", "side_b_2", "side_b_3"]}, draft)
        import json
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert data["status"] == "noop"

    def test_wt_14_reorder_no_license_returns_404(self):
        """WT-14: POST /reorder with no LFA license returns 404."""
        from app.api.web_routes.dashboard import lfa_profile_editor_reorder_zone, _ReorderRequest
        draft = _draft()
        db = MagicMock()
        payload = _ReorderRequest(zone="side_b", slot_ids=["side_b_1"])
        with patch(f"{self._BASE}._get_lfa_license", return_value=None), \
             patch(f"{self._BASE}._CardDraftService"):
            resp = asyncio.run(lfa_profile_editor_reorder_zone(payload=payload, db=db, user=MagicMock()))
        assert resp.status_code == 404

    def test_wt_15_reorder_unknown_zone_returns_400(self):
        """WT-15: POST /reorder with unknown zone returns 400."""
        draft = _draft(draft_data=None)
        resp = self._run_reorder({"zone": "not_a_zone", "slot_ids": []}, draft)
        assert resp.status_code == 400
        import json
        data = json.loads(resp.body)
        assert data["ok"] is False
        assert "Unknown zone" in data["error"]

    def test_wt_16_reorder_slot_from_wrong_zone_returns_400(self):
        """WT-16: POST /reorder with a slot_id from the wrong zone returns 400."""
        draft = _draft(draft_data={"profile_grid": {
            "version": 1, "slots": [
                {"slot_id": "side_b_1", "module": {"provider": "youtube", "video_id": "V1"}},
                {"slot_id": "side_b_2", "module": {"provider": "tiktok",  "video_id": "V2"}},
            ],
        }})
        resp = self._run_reorder({"zone": "side_b", "slot_ids": ["side_b_1", "side_a_1"]}, draft)
        assert resp.status_code == 400
        import json
        data = json.loads(resp.body)
        assert data["ok"] is False

    def test_wt_17_reorder_empty_draft_returns_noop(self):
        """WT-17: POST /reorder on an empty draft (no profile_grid) returns noop."""
        draft = _draft(draft_data=None)
        resp = self._run_reorder({"zone": "side_b", "slot_ids": ["side_b_1", "side_b_2"]}, draft)
        import json
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert data["status"] == "noop"


# ── WT-18..23: Phase 1 stabilisation — same-order no-op, bottom, Challenge Highlight ──

class TestSameOrderNoop:

    def test_wt_18_reorder_zone_same_order_returns_same_object(self):
        """WT-18: reorder_zone with 2+ filled slots in the SAME order returns the same object (no-op)."""
        profile_grid = {"version": 1, "slots": [
            {"slot_id": "side_b_1", "module": {"provider": "youtube", "video_id": "V1"}},
            {"slot_id": "side_b_2", "module": {"provider": "tiktok",  "video_id": "V2"}},
        ]}
        # Same order as stored — no reorder needed
        result = reorder_zone(profile_grid, "side_b", ["side_b_1", "side_b_2"])
        assert result is profile_grid, "Same-order input must return the same object (no-op)"

    def test_wt_19_reorder_draft_zone_same_order_no_commit(self):
        """WT-19: reorder_draft_zone with same-order slot_ids does NOT call db.commit."""
        draft = _draft(draft_data={"profile_grid": {
            "version": 1, "slots": [
                {"slot_id": "side_c_1", "module": {"provider": "youtube", "video_id": "V1"}},
                {"slot_id": "side_c_2", "module": {"provider": "tiktok",  "video_id": "V2"}},
            ],
        }})
        db = MagicMock()
        CardDraftService.reorder_draft_zone(db, draft, "side_c", ["side_c_1", "side_c_2"])
        db.commit.assert_not_called()

    def test_wt_20_reorder_endpoint_same_order_returns_noop_status(self):
        """WT-20: POST /reorder with same-order slot_ids returns {"ok": true, "status": "noop"}."""
        draft = _draft(draft_data={"profile_grid": {
            "version": 1, "slots": [
                {"slot_id": "side_b_1", "module": {"provider": "youtube", "video_id": "V1"}},
                {"slot_id": "side_b_2", "module": {"provider": "tiktok",  "video_id": "V2"}},
            ],
        }})
        from app.api.web_routes.dashboard import lfa_profile_editor_reorder_zone, _ReorderRequest
        db = MagicMock()
        payload = _ReorderRequest(zone="side_b", slot_ids=["side_b_1", "side_b_2"])
        with patch("app.api.web_routes.dashboard._get_lfa_license", return_value=MagicMock()), \
             patch("app.api.web_routes.dashboard._CardDraftService") as MockCDS:
            MockCDS.get_player_card_draft.return_value = draft
            MockCDS.reorder_draft_zone.side_effect = lambda db, d, zone, slot_ids, **kw: \
                CardDraftService.reorder_draft_zone(db, d, zone, slot_ids, commit=False)
            resp = asyncio.run(lfa_profile_editor_reorder_zone(payload=payload, db=db, user=MagicMock()))
        import json
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert data["status"] == "noop"

    def test_wt_21_reorder_invalid_zone_still_returns_400(self):
        """WT-21: regression — invalid zone must still return 400 after same-order noop change."""
        draft = _draft(draft_data={"profile_grid": {
            "version": 1, "slots": [
                {"slot_id": "side_b_1", "module": {"provider": "youtube", "video_id": "V1"}},
                {"slot_id": "side_b_2", "module": {"provider": "tiktok",  "video_id": "V2"}},
            ],
        }})
        from app.api.web_routes.dashboard import lfa_profile_editor_reorder_zone, _ReorderRequest
        db = MagicMock()
        payload = _ReorderRequest(zone="invalid_zone", slot_ids=["side_b_1", "side_b_2"])
        with patch("app.api.web_routes.dashboard._get_lfa_license", return_value=MagicMock()), \
             patch("app.api.web_routes.dashboard._CardDraftService") as MockCDS:
            MockCDS.get_player_card_draft.return_value = draft
            MockCDS.reorder_draft_zone.side_effect = lambda db, d, zone, slot_ids, **kw: \
                CardDraftService.reorder_draft_zone(db, d, zone, slot_ids, commit=False)
            resp = asyncio.run(lfa_profile_editor_reorder_zone(payload=payload, db=db, user=MagicMock()))
        assert resp.status_code == 400
        import json
        data = json.loads(resp.body)
        assert data["ok"] is False
        assert "Unknown zone" in data["error"]


class TestTemplateStructure:

    def test_wt_22_challenge_highlight_absent_when_right_grid_slots_present(self):
        """WT-22: player_profile.html omits 'Challenge Highlight' when right_grid_slots is truthy."""
        from jinja2 import Environment, FileSystemLoader
        import os
        templates_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "app", "templates"
        )
        env = Environment(loader=FileSystemLoader(templates_dir))
        template_src = env.loader.get_source(env, "public/player_profile.html")[0]
        # Structural check: "Challenge Highlight" block must be inside {% if not right_grid_slots %}
        ch_pos = template_src.find("Challenge Highlight")
        assert ch_pos != -1, "Challenge Highlight must exist in template"
        # The closest preceding {% if %} block referencing right_grid_slots must be "if not"
        prefix = template_src[:ch_pos]
        last_if = prefix.rfind("{%")
        assert last_if != -1
        if_fragment = template_src[last_if:last_if + 60]
        assert "not right_grid_slots" in if_fragment, (
            f"Challenge Highlight must be guarded by '{{% if not right_grid_slots %}}', "
            f"found: {if_fragment!r}"
        )

    def test_wt_23_bottom_zone_skip_present_in_editor_template(self):
        """WT-23: lfa_public_profile_editor.html JS skips bottom zone in SortableJS init."""
        import os
        template_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "app", "templates",
            "dashboard", "lfa_public_profile_editor.html"
        )
        with open(template_path, encoding="utf-8") as f:
            src = f.read()
        assert 'getAttribute("data-zone") === "bottom"' in src, (
            "SortableJS init must skip the bottom zone via data-zone check"
        )
        assert "evt.oldIndex === evt.newIndex" in src, (
            "onEnd must guard against same-position drop via evt.oldIndex === evt.newIndex"
        )


# ── WT-24..25: Positional mapping — drag to empty slot ────────────────────────

class TestPositionalReorder:

    def test_wt_24_single_filled_slot_drag_to_different_position_moves_module(self):
        """WT-24: Dragging a single filled slot to a different zone position moves the module.

        Regression for the compaction bug where 1 filled slot was always a no-op.
        """
        profile_grid = {"version": 1, "slots": [
            {"slot_id": "side_b_1", "module": {"provider": "youtube", "video_id": "VID_A"}},
        ]}
        # User drags side_b_1 to visual position 3 — DOM becomes [side_b_2, side_b_3, side_b_1]
        result = reorder_zone(profile_grid, "side_b", ["side_b_2", "side_b_3", "side_b_1"])
        assert result is not profile_grid, "Must NOT be the same object (a real move happened)"
        slot_map = {s["slot_id"]: s.get("module") for s in result["slots"]}
        assert slot_map.get("side_b_3", {}).get("video_id") == "VID_A", (
            "Module must land at side_b_3 (canonical position 2)"
        )
        assert "side_b_1" not in slot_map or slot_map["side_b_1"] is None, (
            "side_b_1 must be empty after the move"
        )
        assert "side_b_2" not in slot_map or slot_map["side_b_2"] is None, (
            "side_b_2 must remain empty"
        )

    def test_wt_25_filled_slot_drag_across_empty_to_last_position(self):
        """WT-25: Drag filled slot across two empty slots to the last position — module moves."""
        profile_grid = {"version": 1, "slots": [
            {"slot_id": "side_a_1", "module": {"provider": "tiktok", "video_id": "TT1"}},
        ]}
        # User drags side_a_1 to the last slot — DOM becomes [side_a_2, side_a_3, side_a_1]
        result = reorder_zone(profile_grid, "side_a", ["side_a_2", "side_a_3", "side_a_1"])
        slot_map = {s["slot_id"]: s.get("module") for s in result["slots"]}
        assert slot_map.get("side_a_3", {}).get("video_id") == "TT1"
        assert "side_a_1" not in slot_map or slot_map["side_a_1"] is None
        assert "side_a_2" not in slot_map or slot_map["side_a_2"] is None


# ── CM-01..12: Cross-zone move ─────────────────────────────────────────────────

def _mod(video_id: str, provider: str = "youtube") -> dict:
    return {"provider": provider, "video_id": video_id, "type": f"video_{provider}", "title": ""}


class TestMoveSlot:
    """CM-01..08: Service-level move_slot() tests."""

    def test_cm_01_move_to_empty_target_cross_zone(self):
        """CM-01: side_b_1 → side_a_1 (empty target) — module moves, source cleared."""
        pg = {"version": 1, "slots": [
            {"slot_id": "side_b_1", "module": _mod("VID_B")},
        ]}
        result = move_slot(pg, "side_b_1", "side_a_1")
        slot_map = {s["slot_id"]: s.get("module") for s in result["slots"]}
        assert slot_map.get("side_a_1", {}).get("video_id") == "VID_B", "Module must land at side_a_1"
        assert "side_b_1" not in slot_map, "side_b_1 must be cleared"

    def test_cm_02_move_to_occupied_target_swap(self):
        """CM-02: side_b_1 → side_a_1 (occupied, swap) — modules swap between zones."""
        pg = {"version": 1, "slots": [
            {"slot_id": "side_b_1", "module": _mod("VID_B")},
            {"slot_id": "side_a_1", "module": _mod("VID_A")},
        ]}
        result = move_slot(pg, "side_b_1", "side_a_1", on_conflict="swap")
        slot_map = {s["slot_id"]: s.get("module") for s in result["slots"]}
        assert slot_map["side_a_1"]["video_id"] == "VID_B"
        assert slot_map["side_b_1"]["video_id"] == "VID_A"

    def test_cm_03_move_to_occupied_target_overwrite(self):
        """CM-03: occupied target, overwrite — target gets source module, source cleared."""
        pg = {"version": 1, "slots": [
            {"slot_id": "side_b_1", "module": _mod("VID_B")},
            {"slot_id": "side_a_1", "module": _mod("VID_A")},
        ]}
        result = move_slot(pg, "side_b_1", "side_a_1", on_conflict="overwrite")
        slot_map = {s["slot_id"]: s.get("module") for s in result["slots"]}
        assert slot_map["side_a_1"]["video_id"] == "VID_B"
        assert "side_b_1" not in slot_map, "side_b_1 must be cleared (overwrite, not swap)"

    def test_cm_04_move_to_occupied_target_reject_raises(self):
        """CM-04: occupied target, on_conflict='reject' → ValueError with 'occupied' in message."""
        pg = {"version": 1, "slots": [
            {"slot_id": "side_b_1", "module": _mod("VID_B")},
            {"slot_id": "side_a_1", "module": _mod("VID_A")},
        ]}
        with pytest.raises(ValueError, match="occupied"):
            move_slot(pg, "side_b_1", "side_a_1", on_conflict="reject")

    def test_cm_05_source_empty_is_noop_same_object(self):
        """CM-05: source slot empty → noop, same profile_grid object returned, no mutation."""
        pg = {"version": 1, "slots": [
            {"slot_id": "side_a_1", "module": _mod("OTHER")},
        ]}
        result = move_slot(pg, "side_b_1", "side_a_1")
        assert result is pg, "Must return the same object when source is empty"

    def test_cm_06_source_equals_target_raises(self):
        """CM-06: source_slot_id == target_slot_id → ValueError."""
        pg = {"version": 1, "slots": [
            {"slot_id": "side_b_1", "module": _mod("V")},
        ]}
        with pytest.raises(ValueError, match="must differ"):
            move_slot(pg, "side_b_1", "side_b_1")

    def test_cm_07_invalid_source_slot_raises(self):
        """CM-07: unknown source slot_id → ValueError."""
        pg = {"version": 1, "slots": []}
        with pytest.raises(ValueError, match="Unknown slot_id"):
            move_slot(pg, "not_a_slot", "side_a_1")

    def test_cm_08_invalid_target_slot_raises(self):
        """CM-08: unknown target slot_id → ValueError."""
        pg = {"version": 1, "slots": [
            {"slot_id": "side_b_1", "module": _mod("V")},
        ]}
        with pytest.raises(ValueError, match="Unknown slot_id"):
            move_slot(pg, "side_b_1", "not_a_slot")


class TestMoveDraftSlot:
    """CM-09..11: Draft service + publish integration tests."""

    def test_cm_09_move_draft_slot_modifies_draft_data_and_commits(self):
        """CM-09: move_draft_slot with filled source — draft_data updated, db.commit called."""
        draft = _draft(draft_data={"profile_grid": {
            "version": 1, "slots": [
                {"slot_id": "side_b_1", "module": _mod("V1")},
            ],
        }})
        db = MagicMock()
        CardDraftService.move_draft_slot(db, draft, "side_b_1", "side_a_1")
        pg = (draft.draft_data or {}).get("profile_grid", {})
        slot_map = {s["slot_id"]: s.get("module") for s in pg.get("slots", [])}
        assert slot_map.get("side_a_1", {}).get("video_id") == "V1"
        assert "side_b_1" not in slot_map, "side_b_1 must be empty after move"
        db.commit.assert_called_once()

    def test_cm_09b_move_draft_slot_source_empty_no_commit(self):
        """CM-09b: move_draft_slot with empty source — no DB write."""
        draft = _draft(draft_data=None)
        db = MagicMock()
        CardDraftService.move_draft_slot(db, draft, "side_b_1", "side_a_1")
        db.commit.assert_not_called()

    def test_cm_10_publish_after_move_reflects_new_zone_in_published_data(self):
        """CM-10: publish_draft after cross-zone move reflects new zone in published_data."""
        draft = _draft(draft_data={"profile_grid": {
            "version": 1, "slots": [
                {"slot_id": "side_b_1", "module": _mod("V1")},
            ],
        }}, published_data=None)
        db = MagicMock()
        CardDraftService.move_draft_slot(db, draft, "side_b_1", "side_a_1", commit=False)
        CardDraftService.publish_draft(db, draft, commit=False)
        pub_pg = (draft.published_data or {}).get("profile_grid", {})
        slot_map = {s["slot_id"]: s.get("module") for s in pub_pg.get("slots", [])}
        assert slot_map.get("side_a_1", {}).get("video_id") == "V1"
        assert "side_b_1" not in slot_map, "side_b_1 must not appear in published_data"

    def test_cm_11_public_render_new_zone_after_publish(self):
        """CM-11: build_published_grid_state after cross-zone move + publish returns slot in new zone."""
        draft = _draft(draft_data={"profile_grid": {
            "version": 1, "slots": [
                {"slot_id": "side_b_1", "module": _mod("V1")},
            ],
        }}, published_data=None)
        db = MagicMock()
        CardDraftService.move_draft_slot(db, draft, "side_b_1", "side_a_1", commit=False)
        CardDraftService.publish_draft(db, draft, commit=False)
        published_slots = build_published_grid_state(draft)
        assert published_slots is not None
        zones = {s["zone"] for s in published_slots}
        assert "side_a" in zones, "side_a must appear in published grid after move from side_b_1"
        assert "side_b" not in zones, "side_b must be absent — slot was moved out"
        slot_map = {s["slot_id"]: s["module"] for s in published_slots}
        assert slot_map.get("side_a_1", {}).get("video_id") == "V1"


class TestMoveRoute:
    """CM-12: Endpoint guard + happy path tests."""

    _BASE = "app.api.web_routes.dashboard"

    def _run_move(self, payload_dict, draft, *, license_present=True):
        from app.api.web_routes.dashboard import lfa_profile_editor_move_slot, _MoveRequest
        db = MagicMock()
        payload = _MoveRequest(**payload_dict)
        with patch(f"{self._BASE}._get_lfa_license", return_value=MagicMock() if license_present else None), \
             patch(f"{self._BASE}._CardDraftService") as MockCDS:
            MockCDS.get_player_card_draft.return_value = draft
            MockCDS.move_draft_slot.side_effect = (
                lambda db, d, src, tgt, on_conflict="swap", **kw:
                    CardDraftService.move_draft_slot(db, d, src, tgt, on_conflict=on_conflict, commit=False)
            )
            return asyncio.run(lfa_profile_editor_move_slot(payload=payload, db=db, user=MagicMock()))

    def test_cm_12a_move_no_license_returns_404(self):
        """CM-12a: POST /move with no LFA license returns 404."""
        draft = _draft()
        resp = self._run_move({"source_slot_id": "side_b_1", "target_slot_id": "side_a_1"}, draft, license_present=False)
        assert resp.status_code == 404

    def test_cm_12b_move_successful_returns_moved(self):
        """CM-12b: POST /move with filled source returns {"ok": true, "status": "moved"}."""
        draft = _draft(draft_data={"profile_grid": {
            "version": 1, "slots": [
                {"slot_id": "side_b_1", "module": _mod("V1")},
            ],
        }})
        resp = self._run_move({"source_slot_id": "side_b_1", "target_slot_id": "side_a_1"}, draft)
        import json
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert data["status"] == "moved"
        assert data["source_slot_id"] == "side_b_1"
        assert data["target_slot_id"] == "side_a_1"

    def test_cm_12c_move_empty_source_returns_noop(self):
        """CM-12c: POST /move with empty source returns {"ok": true, "status": "noop"}."""
        draft = _draft(draft_data=None)
        resp = self._run_move({"source_slot_id": "side_b_1", "target_slot_id": "side_a_1"}, draft)
        import json
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert data["status"] == "noop"

    def test_cm_12d_move_invalid_source_returns_400(self):
        """CM-12d: POST /move with unknown source slot_id returns 400."""
        draft = _draft(draft_data={"profile_grid": {
            "version": 1, "slots": [
                {"slot_id": "side_b_1", "module": _mod("V1")},
            ],
        }})
        resp = self._run_move({"source_slot_id": "not_a_slot", "target_slot_id": "side_a_1"}, draft)
        assert resp.status_code == 400
        import json
        data = json.loads(resp.body)
        assert data["ok"] is False

    def test_cm_12e_move_invalid_target_returns_400(self):
        """CM-12e: POST /move with unknown target slot_id returns 400."""
        draft = _draft(draft_data={"profile_grid": {
            "version": 1, "slots": [
                {"slot_id": "side_b_1", "module": _mod("V1")},
            ],
        }})
        resp = self._run_move({"source_slot_id": "side_b_1", "target_slot_id": "not_a_slot"}, draft)
        assert resp.status_code == 400
        import json
        data = json.loads(resp.body)
        assert data["ok"] is False

    def test_cm_12f_move_reject_conflict_returns_400(self):
        """CM-12f: POST /move with on_conflict='reject' and occupied target returns 400."""
        draft = _draft(draft_data={"profile_grid": {
            "version": 1, "slots": [
                {"slot_id": "side_b_1", "module": _mod("V1")},
                {"slot_id": "side_a_1", "module": _mod("V2")},
            ],
        }})
        resp = self._run_move({
            "source_slot_id": "side_b_1",
            "target_slot_id": "side_a_1",
            "on_conflict":    "reject",
        }, draft)
        assert resp.status_code == 400
        import json
        data = json.loads(resp.body)
        assert data["ok"] is False
        assert "occupied" in data["error"]


# ── WB: Widget Builder MVP ─────────────────────────────────────────────────────

# ── WB-01..06: build_text_module ──────────────────────────────────────────────

class TestBuildTextModule:

    def test_wb_01_valid_content_and_heading(self):
        """WB-01: build_text_module with valid content + heading returns correct dict."""
        mod = build_text_module("Hello world", "My heading")
        assert mod["type"] == "text_bio"
        assert mod["content"] == "Hello world"
        assert mod["heading"] == "My heading"
        assert "updated_at" in mod

    def test_wb_02_heading_defaults_to_empty(self):
        """WB-02: build_text_module without heading defaults to empty string."""
        mod = build_text_module("Some content")
        assert mod["heading"] == ""

    def test_wb_03_content_at_max_length_accepted(self):
        """WB-03: content exactly 300 chars is accepted."""
        mod = build_text_module("x" * 300)
        assert len(mod["content"]) == 300

    def test_wb_04_content_exceeds_max_raises_value_error(self):
        """WB-04: content > 300 chars raises ValueError."""
        with pytest.raises(ValueError, match="300"):
            build_text_module("x" * 301)

    def test_wb_05_html_tags_stripped_from_content(self):
        """WB-05: HTML tags are stripped from content."""
        mod = build_text_module("<b>Bold</b> text")
        assert mod["content"] == "Bold text"
        assert "<b>" not in mod["content"]

    def test_wb_06_empty_content_raises_value_error(self):
        """WB-06: empty or whitespace-only content raises ValueError."""
        with pytest.raises(ValueError, match="required"):
            build_text_module("   ")
        with pytest.raises(ValueError, match="required"):
            build_text_module("")


# ── WB-07..12: build_image_module ─────────────────────────────────────────────

class TestBuildImageModule:

    def test_wb_07_valid_https_url_and_alt_text(self):
        """WB-07: build_image_module with HTTPS URL + alt_text returns correct dict."""
        mod = build_image_module("https://cdn.example.com/photo.jpg", "Player photo")
        assert mod["type"] == "image_url"
        assert mod["url"] == "https://cdn.example.com/photo.jpg"
        assert mod["alt_text"] == "Player photo"
        assert mod["caption"] == ""
        assert "updated_at" in mod

    def test_wb_08_http_url_raises_value_error(self):
        """WB-08: HTTP (non-HTTPS) URL raises ValueError."""
        with pytest.raises(ValueError, match="HTTPS"):
            build_image_module("http://example.com/img.jpg", "Alt")

    def test_wb_09_no_scheme_raises_value_error(self):
        """WB-09: URL without scheme raises ValueError."""
        with pytest.raises(ValueError):
            build_image_module("example.com/img.jpg", "Alt")

    def test_wb_10_empty_alt_text_raises_value_error(self):
        """WB-10: empty alt_text raises ValueError."""
        with pytest.raises(ValueError, match="required"):
            build_image_module("https://cdn.example.com/photo.jpg", "")

    def test_wb_11_alt_text_max_length(self):
        """WB-11: alt_text > 200 chars raises ValueError."""
        with pytest.raises(ValueError, match="200"):
            build_image_module("https://cdn.example.com/photo.jpg", "a" * 201)

    def test_wb_12_caption_optional(self):
        """WB-12: caption is optional; defaults to empty string."""
        mod = build_image_module("https://cdn.example.com/photo.jpg", "Alt")
        assert mod["caption"] == ""
        mod2 = build_image_module("https://cdn.example.com/photo.jpg", "Alt", "Nice photo")
        assert mod2["caption"] == "Nice photo"


# ── WB-13..17: grid_fingerprint content-aware ─────────────────────────────────

class TestGridFingerprintContentAware:

    def _pg(self, slot_id, module):
        return {"version": 1, "slots": [{"slot_id": slot_id, "module": module}]}

    def test_wb_13_text_bio_content_change_changes_fingerprint(self):
        """WB-13: changing text_bio content produces a different fingerprint."""
        mod1 = {"type": "text_bio", "content": "Hello", "heading": ""}
        mod2 = {"type": "text_bio", "content": "World", "heading": ""}
        fp1 = grid_fingerprint(self._pg("side_a_1", mod1))
        fp2 = grid_fingerprint(self._pg("side_a_1", mod2))
        assert fp1 != fp2

    def test_wb_14_text_bio_heading_change_changes_fingerprint(self):
        """WB-14: changing text_bio heading produces a different fingerprint."""
        mod1 = {"type": "text_bio", "content": "Same", "heading": "Old"}
        mod2 = {"type": "text_bio", "content": "Same", "heading": "New"}
        fp1 = grid_fingerprint(self._pg("side_a_1", mod1))
        fp2 = grid_fingerprint(self._pg("side_a_1", mod2))
        assert fp1 != fp2

    def test_wb_15_image_url_change_changes_fingerprint(self):
        """WB-15: changing image URL produces a different fingerprint."""
        mod1 = {"type": "image_url", "url": "https://a.com/1.jpg", "alt_text": "Alt", "caption": ""}
        mod2 = {"type": "image_url", "url": "https://a.com/2.jpg", "alt_text": "Alt", "caption": ""}
        fp1 = grid_fingerprint(self._pg("side_b_1", mod1))
        fp2 = grid_fingerprint(self._pg("side_b_1", mod2))
        assert fp1 != fp2

    def test_wb_16_image_alt_caption_change_changes_fingerprint(self):
        """WB-16: changing alt_text or caption of image_url changes fingerprint."""
        base = {"type": "image_url", "url": "https://a.com/img.jpg", "alt_text": "Old", "caption": ""}
        mod2 = dict(base, alt_text="New")
        fp1 = grid_fingerprint(self._pg("side_c_1", base))
        fp2 = grid_fingerprint(self._pg("side_c_1", mod2))
        assert fp1 != fp2

    def test_wb_17_video_fingerprint_backward_compat_unchanged(self):
        """WB-17: old video module (provider + video_id, no type) fingerprint unchanged."""
        old_mod = {"provider": "youtube", "video_id": "abc123"}
        pg = self._pg("side_a_1", old_mod)
        fp = grid_fingerprint(pg)
        # Must contain the legacy "provider:video_id" form
        assert any("youtube:abc123" in entry for entry in fp)


# ── WB-18..21: build_module factory ──────────────────────────────────────────

class TestBuildModuleFactory:

    def test_wb_18_text_bio_dispatch(self):
        """WB-18: build_module('text_bio', payload) builds text_bio module."""
        mod = build_module("text_bio", {"content": "Hello", "heading": "Hi"})
        assert mod["type"] == "text_bio"
        assert mod["content"] == "Hello"

    def test_wb_19_image_url_dispatch(self):
        """WB-19: build_module('image_url', payload) builds image_url module."""
        mod = build_module("image_url", {
            "url": "https://cdn.example.com/img.jpg",
            "alt_text": "A photo",
        })
        assert mod["type"] == "image_url"
        assert mod["url"] == "https://cdn.example.com/img.jpg"

    def test_wb_20_video_youtube_dispatch(self):
        """WB-20: build_module('video_youtube', payload) builds video_youtube module."""
        mod = build_module("video_youtube", {"video_url": _YT_URL})
        assert mod["type"] == "video_youtube"
        assert mod["video_id"] == _YT_VID

    def test_wb_21_unknown_widget_type_raises_value_error(self):
        """WB-21: build_module with unknown widget_type raises ValueError."""
        with pytest.raises(ValueError, match="Unknown widget_type"):
            build_module("stat_badge", {"value": "47"})


# ── WB-22..30: POST /slots endpoint (widget-type-aware) ──────────────────────

class TestSlotRouteWidgetTypes:

    _BASE = "app.api.web_routes.dashboard"

    def _run_slot(self, payload_dict, draft, *, license_present=True):
        from app.api.web_routes.dashboard import lfa_profile_editor_set_slot, _SlotWidgetRequest
        import json as _json
        db = MagicMock()
        payload = _SlotWidgetRequest(**payload_dict)
        with patch(f"{self._BASE}._get_lfa_license", return_value=MagicMock() if license_present else None), \
             patch(f"{self._BASE}._CardDraftService") as MockCDS:
            MockCDS.get_player_card_draft.return_value = draft
            MockCDS.set_draft_slot.side_effect = (
                lambda db, d, slot_id, video_url=None, title="", *, widget_type=None, payload=None, thumbnail_url=None, commit=True:
                    CardDraftService.set_draft_slot(
                        db, d, slot_id, video_url, title,
                        widget_type=widget_type, payload=payload, thumbnail_url=thumbnail_url, commit=False,
                    )
            )
            resp = asyncio.run(lfa_profile_editor_set_slot(
                slot_id="side_a_1",
                payload=payload,
                db=db,
                user=MagicMock(),
            ))
        return resp

    def test_wb_22_text_bio_save_returns_ok(self):
        """WB-22: POST /slots text_bio valid payload returns 200 ok."""
        import json
        draft = _draft()
        resp = self._run_slot(
            {"widget_type": "text_bio", "content": "My bio text", "heading": "About"},
            draft,
        )
        assert resp.status_code == 200
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert data["widget_type"] == "text_bio"

    def test_wb_23_image_url_save_returns_ok(self):
        """WB-23: POST /slots image_url valid payload returns 200 ok."""
        import json
        draft = _draft()
        resp = self._run_slot(
            {"widget_type": "image_url", "url": "https://cdn.example.com/img.jpg", "alt_text": "Photo"},
            draft,
        )
        assert resp.status_code == 200
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert data["widget_type"] == "image_url"

    def test_wb_24_unknown_widget_type_returns_422(self):
        """WB-24: POST /slots unknown widget_type returns 422."""
        import json
        draft = _draft()
        resp = self._run_slot({"widget_type": "stat_badge"}, draft)
        assert resp.status_code == 422
        data = json.loads(resp.body)
        assert data["ok"] is False

    def test_wb_25_text_bio_missing_content_returns_422(self):
        """WB-25: POST /slots text_bio without content returns 422."""
        import json
        draft = _draft()
        resp = self._run_slot({"widget_type": "text_bio", "heading": "Hi"}, draft)
        assert resp.status_code == 422
        data = json.loads(resp.body)
        assert data["ok"] is False

    def test_wb_26_image_url_missing_alt_text_returns_422(self):
        """WB-26: POST /slots image_url without alt_text returns 422."""
        import json
        draft = _draft()
        resp = self._run_slot({"widget_type": "image_url", "url": "https://cdn.example.com/img.jpg"}, draft)
        assert resp.status_code == 422
        data = json.loads(resp.body)
        assert data["ok"] is False

    def test_wb_27_backward_compat_video_url_no_widget_type(self):
        """WB-27: POST /slots with only video_url (no widget_type) saves as video (backward compat)."""
        import json
        draft = _draft()
        resp = self._run_slot({"video_url": _YT_URL, "title": "My goal"}, draft)
        assert resp.status_code == 200
        data = json.loads(resp.body)
        assert data["ok"] is True

    def test_wb_28_no_widget_type_no_video_url_returns_422(self):
        """WB-28: POST /slots with neither widget_type nor video_url returns 422."""
        import json
        draft = _draft()
        resp = self._run_slot({}, draft)
        assert resp.status_code == 422
        data = json.loads(resp.body)
        assert data["ok"] is False

    def test_wb_29_publish_draft_text_bio_in_published_data(self):
        """WB-29: publish_draft after text_bio save → published_data contains text_bio module."""
        draft = _draft()
        db = MagicMock()
        CardDraftService.set_draft_slot(
            db, draft, "side_a_1",
            widget_type="text_bio",
            payload={"content": "Hello world", "heading": ""},
            commit=False,
        )
        CardDraftService.publish_draft(db, draft, commit=False)
        pub_pg = (draft.published_data or {}).get("profile_grid", {})
        slots = {s["slot_id"]: s["module"] for s in pub_pg.get("slots", [])}
        assert slots.get("side_a_1", {}).get("type") == "text_bio"
        assert slots["side_a_1"]["content"] == "Hello world"

    def test_wb_30_draft_isolation_text_bio(self):
        """WB-30: text_bio save updates draft_data but not published_data."""
        original_pub = {"highlight_video": {"provider": "youtube", "video_id": "pub123"}}
        draft = _draft(published_data=original_pub)
        db = MagicMock()
        CardDraftService.set_draft_slot(
            db, draft, "side_a_1",
            widget_type="text_bio",
            payload={"content": "Draft only", "heading": ""},
            commit=False,
        )
        assert draft.published_data == original_pub, "published_data must not change on draft save"
        assert (draft.draft_data or {}).get("profile_grid") is not None


# ── WB-31..35: Public render (player_profile.html template) ──────────────────

class TestPublicRenderWidgetTypes:

    def test_wb_31_player_html_text_bio_class_present(self):
        """WB-31: player_profile.html render_slot_module contains psp-slot-text-bio class."""
        assert "psp-slot-text-bio" in _PLAYER_HTML

    def test_wb_32_player_html_image_url_class_present(self):
        """WB-32: player_profile.html render_slot_module contains psp-slot-image class."""
        assert "psp-slot-image" in _PLAYER_HTML

    def test_wb_33_backward_compat_youtube_render_present(self):
        """WB-33: player_profile.html still has YouTube nocookie iframe (backward compat)."""
        assert "youtube-nocookie.com/embed" in _PLAYER_HTML
        assert 'module.provider == "youtube"' in _PLAYER_HTML or "module.provider ==" in _PLAYER_HTML

    def test_wb_34_unknown_module_fallback_present(self):
        """WB-34: player_profile.html has a fallback block for unknown module.type (no 500)."""
        assert "psp-slot-unknown" in _PLAYER_HTML

    def test_wb_35_text_bio_content_escaped(self):
        """WB-35: player_profile.html applies | e (escape) filter to text_bio content."""
        # Both content and alt_text must be escaped.
        assert "module.content | e" in _PLAYER_HTML
        assert "module.alt_text | e" in _PLAYER_HTML


# ── TT-P-01..14: TikTok Custom Thumbnail (Option B) ──────────────────────────

import hashlib as _hashlib  # noqa: E402

_THUMB_URL = "https://cdn.example.com/tiktok_thumb.jpg"
_THUMB_URL_HTTP = "http://cdn.example.com/tiktok_thumb.jpg"


class TestTikTokThumbnail:

    # ── Service layer ──────────────────────────────────────────────────────────

    def test_tt_p_01_build_video_module_stores_thumbnail_for_tiktok(self):
        """TT-P-01: build_video_module with thumbnail_url stores custom_thumbnail_url for TikTok."""
        mod = build_video_module(_TT_URL, "Clip", thumbnail_url=_THUMB_URL)
        assert mod["type"] == "video_tiktok"
        assert mod["custom_thumbnail_url"] == _THUMB_URL

    def test_tt_p_02_thumbnail_url_ignored_for_youtube(self):
        """TT-P-02: build_video_module ignores thumbnail_url for YouTube (key absent)."""
        mod = build_video_module(_YT_URL, "Goal", thumbnail_url=_THUMB_URL)
        assert mod["type"] == "video_youtube"
        assert "custom_thumbnail_url" not in mod

    def test_tt_p_03_http_thumbnail_raises_value_error(self):
        """TT-P-03: HTTP (non-HTTPS) thumbnail URL raises ValueError."""
        with pytest.raises(ValueError, match="HTTPS"):
            build_video_module(_TT_URL, thumbnail_url=_THUMB_URL_HTTP)

    def test_tt_p_04_invalid_thumbnail_url_raises_value_error(self):
        """TT-P-04: Malformed thumbnail URL (no host) raises ValueError."""
        with pytest.raises(ValueError, match="HTTPS"):
            build_video_module(_TT_URL, thumbnail_url="not-a-url")

    def test_tt_p_05_no_thumbnail_fingerprint_backward_compat(self):
        """TT-P-05: TikTok module without thumbnail produces 'tiktok:VIDEO_ID' fingerprint."""
        mod = {"type": "video_tiktok", "provider": "tiktok", "video_id": _TT_VID}
        fp = _module_fingerprint(mod)
        assert fp == f"tiktok:{_TT_VID}"

    def test_tt_p_06_thumbnail_fingerprint_includes_hash(self):
        """TT-P-06: TikTok module with custom_thumbnail_url produces 'tiktok:ID:<hash8>' fingerprint."""
        mod = {
            "type": "video_tiktok", "provider": "tiktok", "video_id": _TT_VID,
            "custom_thumbnail_url": _THUMB_URL,
        }
        fp = _module_fingerprint(mod)
        expected_h = _hashlib.sha256(_THUMB_URL.encode()).hexdigest()[:8]
        assert fp == f"tiktok:{_TT_VID}:{expected_h}"

    def test_tt_p_07_fingerprint_changes_when_thumbnail_changes(self):
        """TT-P-07: Changing custom_thumbnail_url produces a different fingerprint."""
        mod_a = {"type": "video_tiktok", "provider": "tiktok", "video_id": _TT_VID,
                 "custom_thumbnail_url": _THUMB_URL}
        mod_b = {"type": "video_tiktok", "provider": "tiktok", "video_id": _TT_VID,
                 "custom_thumbnail_url": "https://other.example.com/new_thumb.jpg"}
        assert _module_fingerprint(mod_a) != _module_fingerprint(mod_b)

    def test_tt_p_08_build_module_factory_forwards_thumbnail_for_tiktok(self):
        """TT-P-08: build_module factory passes thumbnail_url to build_video_module for video_tiktok."""
        from app.services.profile_grid_service import build_module
        mod = build_module("video_tiktok", {
            "video_url": _TT_URL,
            "title": "Clip",
            "thumbnail_url": _THUMB_URL,
        })
        assert mod["type"] == "video_tiktok"
        assert mod["custom_thumbnail_url"] == _THUMB_URL

    def test_tt_p_09_build_module_factory_ignores_thumbnail_for_youtube(self):
        """TT-P-09: build_module factory ignores thumbnail_url for video_youtube."""
        from app.services.profile_grid_service import build_module
        mod = build_module("video_youtube", {
            "video_url": _YT_URL,
            "thumbnail_url": _THUMB_URL,
        })
        assert "custom_thumbnail_url" not in mod

    def test_tt_p_10_set_draft_slot_stores_thumbnail(self):
        """TT-P-10: set_draft_slot with thumbnail_url stores custom_thumbnail_url in draft profile_grid."""
        draft = _draft()
        db = MagicMock()
        CardDraftService.set_draft_slot(
            db, draft, "side_c_1", _TT_URL, "Clip",
            thumbnail_url=_THUMB_URL,
        )
        pg = (draft.draft_data or {}).get("profile_grid", {})
        slot_entry = next((s for s in pg.get("slots", []) if s["slot_id"] == "side_c_1"), {})
        mod = slot_entry.get("module", {})
        assert mod.get("custom_thumbnail_url") == _THUMB_URL

    def test_tt_p_11_set_draft_slot_http_thumbnail_raises(self):
        """TT-P-11: set_draft_slot with HTTP thumbnail raises ValueError (rejected at service layer)."""
        draft = _draft()
        db = MagicMock()
        with pytest.raises(ValueError, match="HTTPS"):
            CardDraftService.set_draft_slot(
                db, draft, "side_c_1", _TT_URL,
                thumbnail_url=_THUMB_URL_HTTP,
            )

    def test_tt_p_12_is_published_detects_thumbnail_change(self):
        """TT-P-12: is_published returns False when custom_thumbnail_url changes between draft and published."""
        pub_pg = {"version": 1, "slots": [
            {"slot_id": "side_c_1", "module": {
                "type": "video_tiktok", "provider": "tiktok", "video_id": _TT_VID,
            }},
        ]}
        draft_pg = {"version": 1, "slots": [
            {"slot_id": "side_c_1", "module": {
                "type": "video_tiktok", "provider": "tiktok", "video_id": _TT_VID,
                "custom_thumbnail_url": _THUMB_URL,
            }},
        ]}
        draft = _draft(
            draft_data={"profile_grid": draft_pg},
            published_data={"profile_grid": pub_pg},
        )
        assert CardDraftService.is_published(draft) is False

    # ── Route layer ────────────────────────────────────────────────────────────

    def _run_slot(self, body_dict, draft):
        from app.api.web_routes.dashboard import lfa_profile_editor_set_slot, _SlotWidgetRequest
        db = MagicMock()
        payload = _SlotWidgetRequest(**body_dict)
        with patch("app.api.web_routes.dashboard._get_lfa_license", return_value=MagicMock()), \
             patch("app.api.web_routes.dashboard._CardDraftService") as MockCDS:
            MockCDS.get_player_card_draft.return_value = draft
            MockCDS.set_draft_slot.side_effect = (
                lambda db, d, sid, vu=None, t="", *, widget_type=None, payload=None, thumbnail_url=None, commit=True:
                    CardDraftService.set_draft_slot(
                        db, d, sid, vu, t,
                        widget_type=widget_type, payload=payload, thumbnail_url=thumbnail_url, commit=False,
                    )
            )
            return asyncio.run(lfa_profile_editor_set_slot(
                slot_id="side_c_1", payload=payload, db=db, user=MagicMock()
            ))

    def test_tt_p_13_route_422_on_http_thumbnail(self):
        """TT-P-13: POST /slots with HTTP thumbnail_url returns 422 before service call."""
        import json
        draft = _draft()
        resp = self._run_slot({
            "widget_type": "video_tiktok",
            "video_url": _TT_URL,
            "thumbnail_url": _THUMB_URL_HTTP,
        }, draft)
        assert resp.status_code == 422
        data = json.loads(resp.body)
        assert data["ok"] is False
        assert "HTTPS" in data["error"] or "https" in data["error"].lower()

    def test_tt_p_14_route_200_thumbnail_in_response(self):
        """TT-P-14: POST /slots with valid HTTPS thumbnail returns 200 and thumbnail_url in response body."""
        import json
        draft = _draft()
        resp = self._run_slot({
            "widget_type": "video_tiktok",
            "video_url": _TT_URL,
            "title": "Clip",
            "thumbnail_url": _THUMB_URL,
        }, draft)
        assert resp.status_code == 200
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert data["thumbnail_url"] == _THUMB_URL


# ── WC-01..20: Weather widget ─────────────────────────────────────────────────

_GEO_LAT   = 47.4979
_GEO_LON   = 19.0402
_GEO_ACC   = 50.0
_GEO_LABEL = "Budapest, HU"
_WEATHER_DATA = {
    "temp_c": 18.5, "weathercode": 1, "condition": "Mainly clear",
    "wind_kph": 12.3, "humidity": 55,
}

_RGS_PATCH = "app.services.location.reverse_geocode_service.reverse_geocode"
_WS_PATCH  = "app.services.location.weather_service.fetch_current_weather"


class TestWeatherWidget:
    """WC-01..10: build_weather_module + build_module dispatch + fingerprint."""

    def _build(self, lat=_GEO_LAT, lon=_GEO_LON, accuracy_m=_GEO_ACC,
               units="metric", label=_GEO_LABEL, weather=None):
        from app.services.profile_grid_service import build_weather_module
        _w = weather if weather is not None else _WEATHER_DATA
        with patch(_RGS_PATCH, return_value=label), \
             patch(_WS_PATCH, return_value=_w):
            return build_weather_module(lat, lon, accuracy_m, units)

    def test_wc_01_valid_coords_returns_weather_current_module(self):
        """WC-01: valid GPS input → module type is weather_current."""
        mod = self._build()
        assert mod["type"] == "weather_current"
        assert mod["location_label"] == _GEO_LABEL
        assert mod["weather"] == _WEATHER_DATA
        assert mod["fetch_error"] is None

    def test_wc_02_exact_gps_not_stored_coords_are_1_decimal(self):
        """WC-02: stored lat/lon are 1-decimal rounded, not exact GPS."""
        lat_exact = 47.49791234
        lon_exact = 19.04021234
        mod = self._build(lat=lat_exact, lon=lon_exact)
        assert mod["lat"] == round(lat_exact, 1)
        assert mod["lon"] == round(lon_exact, 1)
        assert mod["lat"] != lat_exact
        assert mod["lon"] != lon_exact

    def test_wc_03_invalid_latitude_raises_value_error(self):
        """WC-03: lat > 90 → ValueError."""
        from app.services.profile_grid_service import build_weather_module
        with pytest.raises(ValueError, match="latitude"):
            build_weather_module(91.0, 0.0, 10.0)

    def test_wc_04_invalid_longitude_raises_value_error(self):
        """WC-04: lon < -180 → ValueError."""
        from app.services.profile_grid_service import build_weather_module
        with pytest.raises(ValueError, match="longitude"):
            build_weather_module(0.0, -181.0, 10.0)

    def test_wc_05_accuracy_above_200m_raises_value_error(self):
        """WC-05: accuracy_m > 200 → ValueError (poor GPS)."""
        from app.services.profile_grid_service import build_weather_module
        with pytest.raises(ValueError, match="accuracy"):
            build_weather_module(_GEO_LAT, _GEO_LON, 201.0)

    def test_wc_06_negative_accuracy_raises_value_error(self):
        """WC-06: accuracy_m < 0 → ValueError."""
        from app.services.profile_grid_service import build_weather_module
        with pytest.raises(ValueError, match="accuracy_m"):
            build_weather_module(_GEO_LAT, _GEO_LON, -1.0)

    def test_wc_07_invalid_units_raises_value_error(self):
        """WC-07: units not in {'metric', 'imperial'} → ValueError."""
        from app.services.profile_grid_service import build_weather_module
        with pytest.raises(ValueError, match="units"):
            build_weather_module(_GEO_LAT, _GEO_LON, 10.0, units="kelvin")

    def test_wc_08_weather_api_failure_stored_as_fetch_error_not_raised(self):
        """WC-08: weather API raises → fetch_error set, weather=None, no exception propagates."""
        import httpx
        from app.services.profile_grid_service import build_weather_module
        with patch(_RGS_PATCH, return_value=_GEO_LABEL), \
             patch(_WS_PATCH, side_effect=httpx.TimeoutException("timeout")):
            mod = build_weather_module(_GEO_LAT, _GEO_LON, _GEO_ACC)
        assert mod["weather"] is None
        assert mod["fetch_error"] is not None
        assert "timeout" in mod["fetch_error"].lower()

    def test_wc_09_module_fingerprint_weather_current(self):
        """WC-09: _module_fingerprint for weather_current returns 'weather:{label}'."""
        mod = {"type": "weather_current", "location_label": _GEO_LABEL}
        fp = _module_fingerprint(mod)
        assert fp == f"weather:{_GEO_LABEL}"

    def test_wc_10_build_module_dispatch_weather_current(self):
        """WC-10: build_module('weather_current', ...) dispatches to build_weather_module."""
        with patch(_RGS_PATCH, return_value=_GEO_LABEL), \
             patch(_WS_PATCH, return_value=_WEATHER_DATA):
            mod = build_module("weather_current", {
                "lat": _GEO_LAT, "lon": _GEO_LON, "accuracy_m": _GEO_ACC,
            })
        assert mod["type"] == "weather_current"


class TestWeatherWidgetRoute:
    """WC-11..15: POST /slots weather_current route handling."""

    _BASE = "app.api.web_routes.dashboard"

    def _run_weather_slot(self, payload_dict, draft):
        from app.api.web_routes.dashboard import lfa_profile_editor_set_slot, _SlotWidgetRequest
        import json as _json
        db = MagicMock()
        payload = _SlotWidgetRequest(**payload_dict)
        with patch(f"{self._BASE}._get_lfa_license", return_value=MagicMock()), \
             patch(f"{self._BASE}._CardDraftService") as MockCDS, \
             patch(_RGS_PATCH, return_value=_GEO_LABEL), \
             patch(_WS_PATCH, return_value=_WEATHER_DATA):
            MockCDS.get_player_card_draft.return_value = draft
            MockCDS.set_draft_slot.side_effect = (
                lambda db, d, slot_id, video_url=None, title="", *, widget_type=None, payload=None, thumbnail_url=None, commit=True:
                    CardDraftService.set_draft_slot(
                        db, d, slot_id, video_url, title,
                        widget_type=widget_type, payload=payload, commit=False,
                    )
            )
            resp = asyncio.run(lfa_profile_editor_set_slot(
                slot_id="side_a_1",
                payload=payload,
                db=db,
                user=MagicMock(),
            ))
        return resp

    def test_wc_11_missing_lat_lon_returns_422(self):
        """WC-11: weather_current without lat/lon → 422."""
        import json
        draft = _draft()
        resp = self._run_weather_slot({"widget_type": "weather_current"}, draft)
        assert resp.status_code == 422
        data = json.loads(resp.body)
        assert data["ok"] is False

    def test_wc_12_accuracy_too_low_returns_422(self):
        """WC-12: weather_current with accuracy_m > 200 → 422 (build_weather_module ValueError)."""
        import json
        from app.services.profile_grid_service import build_weather_module as _bwm
        draft = _draft()
        resp = self._run_weather_slot({
            "widget_type": "weather_current",
            "lat": _GEO_LAT, "lon": _GEO_LON, "accuracy_m": 500.0,
        }, draft)
        assert resp.status_code == 422
        data = json.loads(resp.body)
        assert data["ok"] is False

    def test_wc_13_valid_weather_payload_returns_200(self):
        """WC-13: weather_current valid payload → 200 ok."""
        import json
        draft = _draft()
        resp = self._run_weather_slot({
            "widget_type": "weather_current",
            "lat": _GEO_LAT, "lon": _GEO_LON, "accuracy_m": _GEO_ACC,
        }, draft)
        assert resp.status_code == 200
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert data["widget_type"] == "weather_current"

    def test_wc_14_response_includes_location_label_and_weather(self):
        """WC-14: weather_current 200 response includes location_label and weather fields."""
        import json
        draft = _draft()
        resp = self._run_weather_slot({
            "widget_type": "weather_current",
            "lat": _GEO_LAT, "lon": _GEO_LON, "accuracy_m": _GEO_ACC,
        }, draft)
        data = json.loads(resp.body)
        assert data["location_label"] == _GEO_LABEL
        assert data["weather"]["temp_c"] == _WEATHER_DATA["temp_c"]
        assert data["fetch_error"] is None

    def test_wc_15_weather_timeout_graceful_degradation(self):
        """WC-15: weather API TimeoutException → build_weather_module catches it, saves with fetch_error, route returns 200."""
        import json, httpx
        draft = _draft()
        resp = None
        from app.api.web_routes.dashboard import lfa_profile_editor_set_slot, _SlotWidgetRequest
        db = MagicMock()
        payload = _SlotWidgetRequest(
            widget_type="weather_current",
            lat=_GEO_LAT, lon=_GEO_LON, accuracy_m=_GEO_ACC,
        )
        with patch(f"{self._BASE}._get_lfa_license", return_value=MagicMock()), \
             patch(f"{self._BASE}._CardDraftService") as MockCDS, \
             patch(_RGS_PATCH, return_value=_GEO_LABEL), \
             patch(_WS_PATCH, side_effect=httpx.TimeoutException("timed out")):
            MockCDS.get_player_card_draft.return_value = draft
            MockCDS.set_draft_slot.side_effect = (
                lambda db, d, slot_id, video_url=None, title="", *, widget_type=None, payload=None, thumbnail_url=None, commit=True:
                    CardDraftService.set_draft_slot(
                        db, d, slot_id, video_url, title,
                        widget_type=widget_type, payload=payload, commit=False,
                    )
            )
            resp = asyncio.run(lfa_profile_editor_set_slot(
                slot_id="side_a_1", payload=payload, db=db, user=MagicMock(),
            ))
        # Weather failure is stored as fetch_error — draft is saved, no 5xx.
        assert resp.status_code == 200
        data = json.loads(resp.body)
        assert data["ok"] is True
        assert data["fetch_error"] is not None
        assert data["weather"] is None


class TestWeatherWidgetTemplates:
    """WC-16..20: Template rendering for weather_current."""

    def test_wc_16_player_profile_contains_weather_card_css(self):
        """WC-16: player_profile.html defines .psp-weather-card CSS class."""
        assert "psp-weather-card" in _PLAYER_HTML

    def test_wc_17_player_profile_does_not_render_raw_lat_lon(self):
        """WC-17: player_profile.html weather branch never renders 'module.lat' or 'module.lon'."""
        # The macro must not expose coordinates — privacy constraint.
        import re
        # Check that in the weather_current branch there is no reference to module.lat / module.lon
        # Locate the weather_current block in the macro
        weather_block_match = re.search(
            r"weather_current.*?{% else %}\s*<div class=\"psp-slot-unknown",
            _PLAYER_HTML, re.DOTALL
        )
        assert weather_block_match, "weather_current block not found in render_slot_module macro"
        block = weather_block_match.group(0)
        assert "module.lat" not in block
        assert "module.lon" not in block

    def test_wc_18_player_profile_weather_fallback_for_fetch_error(self):
        """WC-18: player_profile.html weather branch handles fetch_error with fallback div."""
        assert "psp-weather-fallback" in _PLAYER_HTML
        assert "fetch_error" in _PLAYER_HTML

    def test_wc_19_editor_contains_wp_form_weather(self):
        """WC-19: lfa_public_profile_editor.html defines #wp-form-weather."""
        assert "wp-form-weather" in _EDITOR_HTML

    def test_wc_20_editor_contains_geolocation_js(self):
        """WC-20: lfa_public_profile_editor.html defines wpRequestGeolocation JS function."""
        assert "wpRequestGeolocation" in _EDITOR_HTML
        assert "enableHighAccuracy" in _EDITOR_HTML
