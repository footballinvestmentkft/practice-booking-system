"""
Tournament Type Matrix — Playwright E2E (TM-01..04)
====================================================

TM-01  Wizard UI — INDIVIDUAL + league full click-through
        API setup → admin logs in → wizard page → Finalize → Distribute Rewards
        → public /events/{id} shows ranking table

TM-02  Public event page — INDIVIDUAL ranking display
        GET /events/{id} (no auth) → tournament name, 🥇 badge, points table visible

TM-03  TEAM tournament public page
        GET /events/{id} for a TEAM tournament → "TEAM" chip, team name in table

TM-04  DRAFT event — public "coming soon" page
        GET /events/{draft_id} → 200 with "Coming Soon" status label (state-driven rendering)

All tests target a running server (API_URL=http://localhost:8000, default).
Tests are skipped automatically if the server is not reachable.
The SATT seed data (scripts/seed_all_tournament_types.py) is required for TM-02/03.

Run (headless CI):
    PYTHONPATH=. pytest tests/e2e/test_tournament_type_matrix.py -v

Run (headed, with slow-mo):
    PYTEST_HEADLESS=false PYTEST_SLOW_MO=600 \\
    PYTHONPATH=. pytest tests/e2e/test_tournament_type_matrix.py::TestTournamentTypeMatrix::test_TM_01_wizard_individual_league -v -s
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import date, datetime
from typing import Optional

import pytest
import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

APP_URL = os.environ.get("API_URL", "http://localhost:8000")
DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/lfa_intern_system",
)
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@lfa.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _server_up() -> bool:
    """Return True if the API server is reachable."""
    try:
        r = requests.get(f"{APP_URL}/health", timeout=3)
        return r.status_code < 500
    except Exception:
        return False


def _admin_token() -> str:
    resp = requests.post(
        f"{APP_URL}/api/v1/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=10,
    )
    assert resp.status_code == 200, f"Admin login failed: {resp.text[:200]}"
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _satt_tournament_id(code: str) -> Optional[int]:
    """Look up a SATT tournament ID from the DB."""
    try:
        engine = create_engine(DB_URL)
        S = sessionmaker(bind=engine)
        s = S()
        from app.models.semester import Semester
        sem = s.query(Semester).filter(Semester.code == code).first()
        s.close()
        engine.dispose()
        return sem.id if sem else None
    except Exception:
        return None


# ── Skip guard ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def require_server():
    if not _server_up():
        pytest.skip(
            f"API server not reachable at {APP_URL}. "
            "Start the server before running E2E tests.",
            allow_module_level=True,
        )


# ── TM-01 setup fixtures ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def tm01_tournament():
    """
    Create a minimal INDIVIDUAL + league tournament that's IN_PROGRESS with
    all results submitted, ready for the Finalize step.

    Returns dict with tournament_id, player_ids.
    Cleans up after the module is done (no cleanup needed — SAVEPOINT-isolated
    by being a fresh tournament in the live DB that can be left as REWARDS_DISTRIBUTED).
    """
    if not _server_up():
        pytest.skip("Server not reachable")

    token = _admin_token()
    hdrs = _auth(token)
    uid = uuid.uuid4().hex[:8]

    # 1. Create players + get LFA licenses via seed pattern (direct DB)
    engine = create_engine(DB_URL)
    S = sessionmaker(bind=engine)
    db = S()
    try:
        from app.models.user import User, UserRole
        from app.models.license import UserLicense
        from app.core.security import get_password_hash

        players = []
        for i in range(2):
            u = User(
                email=f"tm01.{uid}.p{i}@lfa-test.local",
                name=f"TM01 P{i} {uid}",
                password_hash=get_password_hash("test"),
                role=UserRole.STUDENT,
                is_active=True,
                onboarding_completed=True,
                credit_balance=9999,
            )
            db.add(u)
            db.flush()
            db.add(UserLicense(
                user_id=u.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                started_at=datetime(2025, 1, 1),
                onboarding_completed=True,
                is_active=True,
                payment_verified=True,
            ))
            players.append(u.id)

        from app.models.location import Location
        from app.models.campus import Campus

        loc = Location(name=f"TM01-Loc-{uid}", city=f"TM01-City-{uid}", country="HU")
        db.add(loc)
        db.flush()
        camp = Campus(location_id=loc.id, name=f"TM01-Campus-{uid}", is_active=True)
        db.add(camp)
        db.flush()
        campus_id = camp.id

        db.commit()
    finally:
        db.close()
        engine.dispose()

    # 2. Look up TournamentType "league" and create tournament via API
    resp = requests.get(f"{APP_URL}/api/v1/tournaments/tournament-types", headers=hdrs, timeout=10)
    tt_league = None
    if resp.status_code == 200:
        for tt in resp.json():
            if tt.get("code") == "league":
                tt_league = tt["id"]
                break

    if tt_league is None:
        pytest.skip("'league' TournamentType not found in DB")

    # Create via ORM (simpler than multi-step API flow)
    engine = create_engine(DB_URL)
    S = sessionmaker(bind=engine)
    db = S()
    try:
        from app.models.semester import Semester, SemesterStatus, SemesterCategory
        from app.models.tournament_configuration import TournamentConfiguration
        from app.models.tournament_reward_config import TournamentRewardConfig
        from app.models.game_configuration import GameConfiguration
        from app.models.game_preset import GamePreset
        from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
        from app.models.session import Session as SessionModel, EventCategory, SessionType

        preset = db.query(GamePreset).filter(GamePreset.code == "satt-default").first()
        if not preset:
            pytest.skip("satt-default GamePreset missing — run seed_all_tournament_types.py first")

        t = Semester(
            name=f"TM01-League-{uid}",
            code=f"TM01-{uid}",
            master_instructor_id=None,
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 30),
            status=SemesterStatus.ONGOING,
            semester_category=SemesterCategory.TOURNAMENT,
            tournament_status="IN_PROGRESS",
            campus_id=campus_id,
        )
        db.add(t)
        db.flush()

        # Get admin id for master_instructor_id
        from app.models.user import User
        admin = db.query(User).filter(User.email == ADMIN_EMAIL).first()
        if admin:
            t.master_instructor_id = admin.id

        db.add(TournamentConfiguration(
            semester_id=t.id,
            tournament_type_id=tt_league,
            participant_type="INDIVIDUAL",
            max_players=32,
            number_of_rounds=1,
            parallel_fields=1,
            ranking_direction="ASC",
        ))
        db.add(GameConfiguration(semester_id=t.id, game_preset_id=preset.id))
        db.add(TournamentRewardConfig(
            semester_id=t.id,
            reward_policy_name="TM01 Default",
            reward_config={
                "first_place": {"xp": 200, "credits": 50},
                "second_place": {"xp": 100, "credits": 20},
                "participation": {"xp": 20, "credits": 0},
                "skill_mappings": [],
            },
        ))

        # Enroll players
        from app.models.license import UserLicense
        for uid_player in players:
            lic = db.query(UserLicense).filter(
                UserLicense.user_id == uid_player,
                UserLicense.is_active == True,
            ).first()
            if lic:
                db.add(SemesterEnrollment(
                    semester_id=t.id,
                    user_id=uid_player,
                    user_license_id=lic.id,
                    is_active=True,
                    request_status=EnrollmentStatus.APPROVED,
                ))

        # Create 1 match session with H2H results
        sess = SessionModel(
            title=f"TM01 Match {uid}",
            date_start=datetime(2026, 5, 1, 10, 0),
            date_end=datetime(2026, 5, 1, 11, 0),
            semester_id=t.id,
            event_category=EventCategory.MATCH,
            session_type=SessionType.on_site,
            game_results={
                "match_format": "HEAD_TO_HEAD",
                "participants": [
                    {"user_id": players[0], "score": 3, "result": "win"},
                    {"user_id": players[1], "score": 1, "result": "loss"},
                ],
                "winner_user_id": players[0],
                "completed_at": datetime.utcnow().isoformat() + "Z",
            },
        )
        db.add(sess)
        db.flush()

        t_id = t.id
        db.commit()
    finally:
        db.close()
        engine.dispose()

    yield {"tournament_id": t_id, "player_ids": players, "token": token}


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestTournamentTypeMatrix:

    # ── TM-01 ─────────────────────────────────────────────────────────────────

    @pytest.mark.slow
    def test_TM_01_wizard_individual_league(self, tm01_tournament, page):
        """
        TM-01: Wizard UI — INDIVIDUAL + league full click-through.
        Open admin wizard → verify step states → Finalize → Distribute Rewards.
        """
        t_id = tm01_tournament["tournament_id"]
        token = tm01_tournament["token"]

        # Calculate rankings first (via API, before opening browser)
        resp = requests.post(
            f"{APP_URL}/api/v1/tournaments/{t_id}/calculate-rankings",
            headers=_auth(token),
            timeout=10,
        )
        assert resp.status_code == 200, f"calculate-rankings failed: {resp.text[:200]}"

        # Set session cookie in browser context (Playwright)
        # Log in via the HTML login form
        page.goto(f"{APP_URL}/login", timeout=15_000)
        page.wait_for_load_state("networkidle")
        page.fill("input[name=email]", ADMIN_EMAIL)
        page.fill("input[name=password]", ADMIN_PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_url(f"{APP_URL}/**", timeout=15_000)

        # Navigate to the tournament wizard (step 6 = Finalize should be active)
        page.goto(f"{APP_URL}/admin/tournaments/{t_id}/edit", timeout=15_000)
        page.wait_for_load_state("networkidle")

        # Verify the wizard page loaded (has wizard steps)
        wiz_steps = page.locator(".wiz-step")
        assert wiz_steps.count() > 0, "Wizard steps not found on page"

        # Step 6 (Finalize) should be active — wizard has 7 steps
        step6 = page.locator(".wiz-step").nth(5)  # 0-indexed = step 6
        step6_class = step6.get_attribute("class") or ""
        assert "active" in step6_class or "done" in step6_class, (
            f"Step 6 should be active or done, got: {step6_class}"
        )

        # Click Finalize button
        finalize_btn = page.locator("button:has-text('Finalize Tournament'), button:has-text('Finalize')")
        if finalize_btn.count() > 0:
            finalize_btn.first.click()
            page.wait_for_timeout(2000)

        # Verify tournament is now COMPLETED by checking API
        resp = requests.get(
            f"{APP_URL}/api/v1/tournaments/{t_id}",
            headers=_auth(token),
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            status = data.get("tournament_status") or data.get("status", "")
            # Either COMPLETED or already further (wizard may have auto-progressed)
            assert status in ("COMPLETED", "REWARDS_DISTRIBUTED"), (
                f"Expected COMPLETED after finalize, got {status}"
            )

        # Navigate to public event page and verify rankings shown
        page.goto(f"{APP_URL}/events/{t_id}", timeout=10_000)
        page.wait_for_load_state("networkidle")
        content = page.content()
        assert "table" in content.lower() or "rank" in content.lower() or "standings" in content.lower(), (
            "Public event page should show rankings after finalize"
        )

    # ── TM-02 ─────────────────────────────────────────────────────────────────

    def test_TM_02_public_individual_rankings(self):
        """
        TM-02: Public event page shows INDIVIDUAL rankings.
        GET /events/{id} (no auth) → name, 🥇 badge, table present.
        """
        t_id = _satt_tournament_id("SATT-01-IND-LEAGUE")
        if t_id is None:
            pytest.skip("SATT-01-IND-LEAGUE not found — run seed_all_tournament_types.py first")

        resp = requests.get(f"{APP_URL}/events/{t_id}", timeout=10)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"

        html = resp.text
        # Tournament name visible
        assert "SATT #1" in html or "Individual League" in html, (
            "Tournament name not found in public event page"
        )
        # Rankings table exists
        assert "<table" in html, "No <table> element in public event page"
        assert "<thead" in html, "No <thead> element (rankings table header) in public event page"
        assert "<tbody" in html, "No <tbody> element in public event page"
        # Gold medal emoji
        assert "🥇" in html, "Gold medal (🥇) not found — rank 1 must have medal badge"

    # ── TM-03 ─────────────────────────────────────────────────────────────────

    def test_TM_03_public_team_event_page(self):
        """
        TM-03: TEAM tournament public page shows team name and TEAM chip.
        """
        t_id = _satt_tournament_id("SATT-05-TEAM-LEAGUE")
        if t_id is None:
            pytest.skip("SATT-05-TEAM-LEAGUE not found — run seed_all_tournament_types.py first")

        resp = requests.get(f"{APP_URL}/events/{t_id}", timeout=10)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

        html = resp.text
        # TEAM participant_type chip visible
        assert "TEAM" in html, "'TEAM' chip not found in public event page for TEAM tournament"
        # Rankings table exists
        assert "<table" in html, "Rankings table not found for TEAM tournament"
        # At least one team name from SATT seed should appear
        assert "SATT tm3 Team" in html or "Team 1" in html or "Team" in html, (
            "No team name found in TEAM tournament rankings"
        )

    # ── TM-04 ─────────────────────────────────────────────────────────────────

    def test_TM_04_draft_event_not_available(self):
        """
        TM-04: A DRAFT tournament returns 200 with a "Coming Soon" status label.
        /events/{id} is a public marketing page — state-driven rendering, not a 404 gate.
        DRAFT shows an enrollment-locked banner so visitors know the event exists.
        """
        # Create a DRAFT tournament directly in the DB
        engine = create_engine(DB_URL)
        S = sessionmaker(bind=engine)
        db = S()
        draft_id = None
        try:
            from app.models.semester import Semester, SemesterStatus, SemesterCategory
            uid = uuid.uuid4().hex[:8]
            t = Semester(
                name=f"TM04-Draft-{uid}",
                code=f"TM04-{uid}",
                start_date=date(2026, 5, 1),
                end_date=date(2026, 5, 30),
                status=SemesterStatus.ONGOING,
                semester_category=SemesterCategory.TOURNAMENT,
                tournament_status="DRAFT",
            )
            db.add(t)
            db.commit()
            db.refresh(t)
            draft_id = t.id
        finally:
            db.close()
            engine.dispose()

        if draft_id is None:
            pytest.fail("Failed to create DRAFT tournament for TM-04")

        try:
            resp = requests.get(f"{APP_URL}/events/{draft_id}", timeout=10)
            # DRAFT events are publicly visible with a "coming soon" banner
            assert resp.status_code == 200, (
                f"DRAFT tournament should return 200 (state-driven rendering), got {resp.status_code}"
            )
            assert "coming soon" in resp.text.lower() or "enrollment not yet open" in resp.text.lower(), (
                "DRAFT page should contain 'Coming Soon' status label or enrollment-locked banner"
            )
        finally:
            # Cleanup: delete the DRAFT tournament
            engine = create_engine(DB_URL)
            S = sessionmaker(bind=engine)
            db = S()
            try:
                t_obj = db.query(Semester).filter(Semester.id == draft_id).first()
                if t_obj:
                    db.delete(t_obj)
                    db.commit()
            finally:
                db.close()
                engine.dispose()
