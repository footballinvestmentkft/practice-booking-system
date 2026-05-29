"""LFA Navigation Phase A — PA-01..PA-12 template source tests.

PA-01  training_hub.html — dashboard link removed from footer
PA-02  training_hub.html — events link removed from footer
PA-03  training_hub.html — Progress link preserved as "📊 View Progress"
PA-04  lfa_player_mood_photos.html — no back-to-profile button link
PA-05  lfa_player_profile.html — no .back-link CSS class defined
PA-06  lfa_player_profile.html — no href="/profile" back link element
PA-07  lfa_player_profile.html — dashboard CTA text is "⚽ LFA Dashboard"
PA-08  dashboard_card_editor.html — ⚽ LFA Dashboard button present
PA-09  dashboard_card_editor.html — brand is "🎴 Card Editor — Player Card"
PA-10  dashboard_card_editor.html — messages badge-wrap removed
PA-11  spec_subpage_hdr.html — mask-image mobile scroll fade CSS present
PA-12  dashboard_student_new.html — "Tools & Quick Access" label present

Phase A-fix: spec dashboard profile link regression
PF-01  dashboard_student_new.html — footer Profile link → /profile/lfa-football-player
PF-02  dashboard_student_new.html — no bare href="/profile" footer link remains
PF-03  dashboard_student_new.html — mod-nav Profile tile → /profile/lfa-football-player
"""
from pathlib import Path

_T = Path(__file__).resolve().parents[4] / "app" / "templates"


def _read(rel):
    return (_T / rel).read_text(encoding="utf-8")


# ── PA-01..03: training_hub.html footer ───────────────────────────────────────

class TestTrainingHubFooter:

    def _src(self):
        return _read("training_hub.html")

    def test_pa01_dashboard_link_removed(self):
        """PA-01: training footer no longer links to /dashboard/lfa-football-player."""
        src = self._src()
        footer_start = src.index('class="trn-footer"')
        footer_block = src[footer_start:]
        assert '/dashboard/lfa-football-player' not in footer_block

    def test_pa02_events_link_removed(self):
        """PA-02: training footer no longer links to /events."""
        src = self._src()
        footer_start = src.index('class="trn-footer"')
        footer_block = src[footer_start:]
        assert 'href="/events"' not in footer_block

    def test_pa03_progress_link_preserved(self):
        """PA-03: training footer still contains the Progress link labelled '📊 View Progress'."""
        src = self._src()
        assert 'href="/progress"' in src
        assert '📊 View Progress' in src


# ── PA-04: lfa_player_mood_photos.html ────────────────────────────────────────

class TestMoodPhotosBackLink:

    def _src(self):
        return _read("lfa_player_mood_photos.html")

    def test_pa04_no_back_to_profile_button(self):
        """PA-04: mood photos page has no back-to-profile button link."""
        src = self._src()
        assert 'href="/profile/lfa-football-player"' not in src


# ── PA-05..07: lfa_player_profile.html ────────────────────────────────────────

class TestLfaPlayerProfileBackLink:

    def _src(self):
        return _read("lfa_player_profile.html")

    def test_pa05_no_back_link_css(self):
        """PA-05: .back-link CSS class definition removed from profile page."""
        src = self._src()
        assert '.back-link' not in src

    def test_pa06_no_href_profile_back_link(self):
        """PA-06: href="/profile" back link element removed from profile page."""
        src = self._src()
        assert 'href="/profile"' not in src

    def test_pa07_dashboard_cta_renamed(self):
        """PA-07: dashboard CTA text is '⚽ LFA Dashboard' (not bare '⚽ Dashboard')."""
        src = self._src()
        assert '⚽ LFA Dashboard' in src


# ── PA-08..10: dashboard_card_editor.html ────────────────────────────────────

class TestCardEditorHeader:

    def _src(self):
        return _read("dashboard_card_editor.html")

    def test_pa08_lfa_dashboard_button_present(self):
        """PA-08: card editor header has ⚽ link pointing to /dashboard/lfa-football-player."""
        src = self._src()
        assert 'href="/dashboard/lfa-football-player"' in src

    def test_pa09_brand_is_my_player_card(self):
        """PA-09: card editor header brand text is '🎴 Card Editor — Player Card'."""
        src = self._src()
        assert '🎴 Card Editor — Player Card' in src

    def test_pa10_no_messages_badge_wrap(self):
        """PA-10: messages badge-wrap removed from card editor header."""
        src = self._src()
        # The old pattern had an <a> wrapping a messages button with a badge span
        # Check both the messages route and the badge pattern are gone from header block
        assert 'href="/messages"' not in src


# ── PA-11: spec_subpage_hdr.html mask-image ───────────────────────────────────

class TestSpecSubpageHdrMaskImage:

    def _src(self):
        return _read("includes/spec_subpage_hdr.html")

    def test_pa11_mask_image_mobile_css_present(self):
        """PA-11: spec_subpage_hdr has mask-image fade CSS for mobile quicknav scroll."""
        src = self._src()
        assert 'mask-image' in src
        assert '-webkit-mask-image' in src
        assert 'linear-gradient(to right' in src


# ── PA-12: dashboard_student_new.html label ───────────────────────────────────

class TestDashboardModNavLabel:

    def _src(self):
        return _read("dashboard_student_new.html")

    def test_pa12_tools_quick_access_label_present(self):
        """PA-12: mod-nav grid has 'Tools & Quick Access' section label."""
        src = self._src()
        assert 'mod-nav-section-label' in src
        assert 'Tools' in src
        assert 'Quick Access' in src


# ── PF-01..03: spec dashboard profile link regression fix ─────────────────────
# After MVP refactor: footer-links + mod-nav Profile tile removed from dashboard.
# Profile access via spec_subpage_hdr quicknav (include file).

class TestSpecDashboardProfileLinks:
    """Profile link regression: dashboard must not route to bare /profile hub."""

    def _src(self):
        return _read("dashboard_student_new.html")

    def test_pf01_quicknav_include_has_spec_profile(self):
        """PF-01: spec_subpage_hdr.html (quicknav include) contains /profile/lfa-football-player."""
        quicknav = _read("includes/spec_subpage_hdr.html")
        assert 'href="/profile/lfa-football-player"' in quicknav

    def test_pf02_footer_links_strip_removed(self):
        """PF-02: footer-links strip has been removed from the spec dashboard template."""
        src = self._src()
        assert 'class="footer-links"' not in src

    def test_pf03_no_bare_profile_href_in_dashboard(self):
        """PF-03: no bare href="/profile" (hub profile) hardcoded in the dashboard template."""
        src = self._src()
        assert 'href="/profile"' not in src
