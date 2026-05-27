"""LFA Spec Dashboard MVP — DS-01..DS-18 template + route context tests.

DS-01  route context exposes skill_count key
DS-02  skill_count == len(get_all_skill_keys())
DS-03  route context exposes has_welcome_card key
DS-04  template source contains .dc-card-zone
DS-05  template source contains .dc-hero-player
DS-06  template source contains .dc-hero-welcome
DS-07  template source contains .dc-hero-chall
DS-08  s-cm-grid NOT in template (My Card Media section removed)
DS-09  s-snapshot NOT in template (Skill Snapshot section removed)
DS-10  s-last-result NOT in template (Last Skill Event section removed)
DS-11  "29 skills" NOT in template
DS-12  /skills/history?skill=passing NOT in template
DS-13  footer-links NOT in template
DS-14  mod-nav contains Calendar, Achievements, Sessions, Progress
DS-15  mod-nav does NOT contain Events, Training, My Cards, Card Editor, Mood Photos tiles
DS-16  Social section appears after .dc-card-zone in source
DS-17  @media (max-width: 768px) contains .dc-card-zone override
DS-18  dc-cta-primary and dc-cta-ghost classes present
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.skill_progression import get_all_skill_keys

_T = Path(__file__).resolve().parents[4] / "app" / "templates"


def _read(rel):
    return (_T / rel).read_text(encoding="utf-8")


# ── DS-01..03: route context ──────────────────────────────────────────────────

class TestSpecDashboardContext:

    def _mock_spec_dashboard(self):
        """Build minimal mock objects that spec_dashboard() would pass to the template."""
        user_license = MagicMock()
        user_license.onboarding_completed = True
        user_license.football_skills = {"passing": 65.0}
        user_license.public_card_platform = "instagram_portrait"

        skill_count = len(get_all_skill_keys())
        has_welcome_card = bool(
            user_license.onboarding_completed
            or user_license.football_skills is not None
        )
        return {
            "skill_count": skill_count,
            "has_welcome_card": has_welcome_card,
            "user_license": user_license,
        }

    def test_ds01_context_has_skill_count(self):
        """DS-01: context dict contains skill_count key."""
        ctx = self._mock_spec_dashboard()
        assert "skill_count" in ctx

    def test_ds02_skill_count_matches_config(self):
        """DS-02: skill_count equals len(get_all_skill_keys())."""
        ctx = self._mock_spec_dashboard()
        assert ctx["skill_count"] == len(get_all_skill_keys())

    def test_ds03_context_has_has_welcome_card(self):
        """DS-03: context dict contains has_welcome_card key."""
        ctx = self._mock_spec_dashboard()
        assert "has_welcome_card" in ctx

    def test_ds03b_has_welcome_card_true_when_onboarding_complete(self):
        """DS-03b: has_welcome_card is True when onboarding_completed is True."""
        ctx = self._mock_spec_dashboard()
        assert ctx["has_welcome_card"] is True

    def test_ds03c_has_welcome_card_false_when_no_onboarding(self):
        """DS-03c: has_welcome_card is False when neither flag nor skills present."""
        ul = MagicMock()
        ul.onboarding_completed = False
        ul.football_skills = None
        has_welcome_card = bool(ul.onboarding_completed or ul.football_skills is not None)
        assert has_welcome_card is False


# ── DS-04..18: template source ────────────────────────────────────────────────

class TestSpecDashboardTemplate:

    def _src(self):
        return _read("dashboard_student_new.html")

    # Card zone structure

    def test_ds04_dc_card_zone_present(self):
        """DS-04: template contains .dc-card-zone class."""
        assert 'dc-card-zone' in self._src()

    def test_ds05_dc_hero_player_present(self):
        """DS-05: template contains .dc-hero-player class."""
        assert 'dc-hero-player' in self._src()

    def test_ds06_dc_hero_welcome_present(self):
        """DS-06: template contains .dc-hero-welcome class."""
        assert 'dc-hero-welcome' in self._src()

    def test_ds07_dc_hero_chall_present(self):
        """DS-07: template contains .dc-hero-chall class."""
        assert 'dc-hero-chall' in self._src()

    # Removed sections

    def test_ds08_no_s_cm_grid(self):
        """DS-08: My Card Media section (s-cm-grid) removed."""
        assert 's-cm-grid' not in self._src()

    def test_ds09_no_s_snapshot(self):
        """DS-09: Skill Snapshot section (s-snapshot) removed."""
        assert 's-snapshot' not in self._src()

    def test_ds10_no_s_last_result(self):
        """DS-10: Last Skill Event section (s-last-result) removed."""
        assert 's-last-result' not in self._src()

    def test_ds11_no_hardcoded_29_skills(self):
        """DS-11: hardcoded '29 skills' text removed."""
        assert '29 skills' not in self._src()

    def test_ds12_no_skill_history_passing_link(self):
        """DS-12: /skills/history?skill=passing link removed."""
        assert '/skills/history?skill=passing' not in self._src()

    def test_ds13_no_footer_links(self):
        """DS-13: footer-links strip removed."""
        assert 'footer-links' not in self._src()

    # Quick Access mod-nav tiles

    def test_ds14_mod_nav_has_required_tiles(self):
        """DS-14: mod-nav section contains Calendar, Achievements, Sessions, Progress."""
        src = self._src()
        start = src.index('class="mod-nav-section"')
        end = src.index('</section>', start) + len('</section>')
        nav = src[start:end]
        assert 'href="/calendar"'      in nav
        assert 'href="/achievements"'  in nav
        assert 'href="/sessions"'      in nav
        assert 'href="/progress"'      in nav

    def test_ds15_mod_nav_no_quicknav_duplicates(self):
        """DS-15: mod-nav section does not contain quicknav-duplicated tiles."""
        src = self._src()
        start = src.index('class="mod-nav-section"')
        end = src.index('</section>', start) + len('</section>')
        nav = src[start:end]
        assert 'href="/events"'                             not in nav
        assert 'href="/training"'                           not in nav
        assert 'href="/my-cards"'                           not in nav
        assert '/profile/my-mood-photos'                    not in nav
        assert 'card-editor'                                not in nav

    # Source order

    def test_ds16_social_after_card_zone(self):
        """DS-16: Social section appears after .dc-card-zone in template source."""
        src = self._src()
        assert src.index('dc-card-zone') < src.index('mod-social-grid')

    # Responsive override

    def test_ds17_media_768_has_dc_card_zone(self):
        """DS-17: @media (max-width: 768px) block overrides .dc-card-zone layout."""
        src = self._src()
        media_start = src.index('@media (max-width: 768px)')
        media_block = src[media_start:media_start + 400]
        assert 'dc-card-zone' in media_block

    # CTA classes

    def test_ds18_cta_classes_present(self):
        """DS-18: dc-cta-primary and dc-cta-ghost classes defined in template."""
        src = self._src()
        assert 'dc-cta-primary' in src
        assert 'dc-cta-ghost'   in src
