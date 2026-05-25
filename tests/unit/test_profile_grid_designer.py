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
    build_draft_grid_state,
    build_published_grid_state,
    grid_fingerprint,
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
    draft_variant: str = "fifa",
    published_theme: str | None = "default",
    published_variant: str | None = "fifa",
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


def _filled_pg(*entries) -> dict:
    """Build a v1 profile_grid with the given (slot_id, provider, video_id) tuples."""
    return {"version": 1, "slots": [
        {"slot_id": sid, "module": {"provider": prov, "video_id": vid, "type": f"video_{prov}", "title": ""}}
        for sid, prov, vid in entries
    ]}


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

    def test_wt_02_reorder_one_filled_slot_is_noop(self):
        """WT-02: reorder_zone with 1 filled slot returns the same profile_grid object (no-op)."""
        profile_grid = {"version": 1, "slots": [
            {"slot_id": "side_b_1", "module": {"provider": "youtube", "video_id": "VID_A"}},
        ]}
        result = reorder_zone(profile_grid, "side_b", ["side_b_1", "side_b_2", "side_b_3"])
        assert result is profile_grid, "Should return the same object for no-op"

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
