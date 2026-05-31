"""
PROOF: Club → Team → Member Name (click) → User Profile page with 44 skills
"""
import os
import time
import pytest
from playwright.sync_api import Page, expect

APP_URL = os.environ.get("API_URL", "http://localhost:8000")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@lfa.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

SCREENSHOT_DIR = "tests/e2e/admin_ui/screenshots"

# Known test data seeded via DB:
# - Club id=59: PROMO_VALIDATION_CLUB
# - Team id=943: Validation U15 (club_id=59)
# - User id=723: Smoke Test Student, license_id=113
#   onboarding_completed=True, football_skills (44 skills) seeded
CLUB_ID = 59
TEAM_ID = 943


def ss(page: Page, name: str) -> None:
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    ts = time.strftime("%H%M%S")
    path = f"{SCREENSHOT_DIR}/{ts}_PROOF_{name}.png"
    page.screenshot(path=path, full_page=True)
    print(f"  📸 {path}")


class TestUserProfileProof:

    def test_PROOF_click_flow_club_team_member_profile(self, page: Page):
        """
        Full click flow:
        1. Login
        2. /admin/clubs → clubs list
        3. /admin/clubs/59 (PROMO_VALIDATION_CLUB) → club detail
        4. click "View members" for Validation U15 → team detail
        5. click "Smoke Test Student" name → profile page
        6. Assert: 44 skill bars visible, overall rating shown
        """

        # ── 1. Login ────────────────────────────────────────────────────────
        page.goto(f"{APP_URL}/login")
        page.fill("input[name=email]", ADMIN_EMAIL)
        page.fill("input[name=password]", ADMIN_PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(f"{APP_URL}/dashboard*", timeout=10_000)
        ss(page, "00_login_done")

        # ── 2. Clubs list ────────────────────────────────────────────────────
        page.goto(f"{APP_URL}/admin/clubs")
        page.wait_for_load_state("networkidle")
        ss(page, "01_clubs_list")
        assert "PROMO_VALIDATION_CLUB" in page.content(), "Club not in list"

        # ── 3. Club detail ───────────────────────────────────────────────────
        page.goto(f"{APP_URL}/admin/clubs/{CLUB_ID}")
        page.wait_for_load_state("networkidle")
        ss(page, "02_club_detail")
        assert "Validation U15" in page.content(), "Team U15 not in club detail"

        # ── 4. Team detail (click View members link) ─────────────────────────
        page.click(f"a[href='/admin/clubs/{CLUB_ID}/teams/{TEAM_ID}']")
        page.wait_for_url(f"{APP_URL}/admin/clubs/{CLUB_ID}/teams/{TEAM_ID}", timeout=8_000)
        page.wait_for_load_state("networkidle")
        ss(page, "03_team_detail")
        assert "Smoke Test Student" in page.content(), "Smoke Test Student not in team detail"
        assert "smoke.student@example.com" in page.content(), "Email not in team detail"

        # ── 5. Click member name → user profile ──────────────────────────────
        page.click("a:has-text('Smoke Test Student')")
        page.wait_for_url(
            f"{APP_URL}/admin/users/723/profile*",
            timeout=8_000,
        )
        page.wait_for_load_state("networkidle")
        ss(page, "04_user_profile")

        content = page.content()

        # ── 6. Assertions: profile content ───────────────────────────────────
        assert "Smoke Test Student" in content, "Name not in profile"
        assert "smoke.student@example.com" in content, "Email not in profile"
        assert "LFA Football Player" in content, "Specialization not in profile"
        assert "🌐 Public Card" in content, "Public profile link missing"

        # 44 skill bars: check skill-cat-card elements
        skill_cards = page.locator(".skill-cat-card")
        skill_card_count = skill_cards.count()
        assert skill_card_count == 4, f"Expected 4 skill categories, got {skill_card_count}"

        skill_rows = page.locator(".skill-row")
        skill_row_count = skill_rows.count()
        assert skill_row_count == 44, f"Expected 44 skill rows, got {skill_row_count}"

        # Overall rating visible (FClassic redesign uses fclassic-overall-num)
        assert "fclassic-overall-num" in content or "overall-val" in content or "overall-bar-wrap" in content, \
            "Overall rating section missing"

        # Back link works
        assert "← Validation U15" in content, "Back link missing"

        ss(page, "05_profile_skills_scrolled")
        # Scroll to see skill grid
        page.evaluate("window.scrollTo(0, 400)")
        page.wait_for_timeout(300)
        ss(page, "06_profile_skills_visible")

        print(f"\n✅ PROOF COMPLETE: 4 skill categories, {skill_row_count} skill rows rendered")
        print(f"   Club 59 → Team 943 → User 723 → /admin/users/723/profile")
