"""
Playwright E2E — Club & Promotion Flow (PROMO-01..05)
=====================================================

  PROMO-01  Admin creates a club via UI modal → club appears in list
  PROMO-02  Admin uploads CSV → import result panel shows correct created/updated counts
  PROMO-03  CSV with an invalid row → error count visible, valid rows processed
  PROMO-04  Promotion wizard: 2 age groups → 2 Semester(PROMOTION_EVENT) rows in DB
  PROMO-05  Created tournaments each have teams enrolled (TournamentTeamEnrollment)

Run (CI / headless, with video):
    PLAYWRIGHT_VIDEO_DIR=test-results/videos/promo \\
    PYTHONPATH=. pytest tests/e2e/admin_ui/test_promotion_flow_e2e.py -v -s

Run (local / headed):
    PYTEST_HEADLESS=false PYTEST_SLOW_MO=400 \\
    PLAYWRIGHT_VIDEO_DIR=test-results/videos/promo \\
    PYTHONPATH=. pytest tests/e2e/admin_ui/test_promotion_flow_e2e.py -v -s
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.core.security import get_password_hash
from app.models.user import User, UserRole
from app.models.club import Club, CsvImportLog
from app.models.team import Team, TeamMember, TournamentTeamEnrollment
from app.models.license import UserLicense
from app.models.credit_transaction import CreditTransaction
from app.models.semester import Semester, SemesterCategory


# ── Config ─────────────────────────────────────────────────────────────────────

APP_URL = os.environ.get("API_URL", "http://localhost:8000")
DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/lfa_intern_system",
)
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@lfa.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

SCREENSHOTS_DIR = Path(__file__).parent / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)

_E2E_PREFIX = "e2epromo"


# ── DB fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def db_engine():
    engine = create_engine(DB_URL)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def purge_stale_promo_data(db_engine):
    """Delete leftover test clubs/teams/users from previous interrupted runs."""
    S = sessionmaker(bind=db_engine)
    s = S()
    try:
        stale_clubs = s.query(Club).filter(Club.name.like(f"{_E2E_PREFIX}%")).all()
        for club in stale_clubs:
            teams = s.query(Team).filter(Team.club_id == club.id).all()
            for team in teams:
                s.query(TournamentTeamEnrollment).filter(
                    TournamentTeamEnrollment.team_id == team.id
                ).delete(synchronize_session=False)
                s.query(TeamMember).filter(TeamMember.team_id == team.id).delete(synchronize_session=False)
                s.delete(team)
            s.query(CsvImportLog).filter(CsvImportLog.club_id == club.id).delete(synchronize_session=False)
            s.delete(club)
        stale_users = s.query(User).filter(User.email.like(f"{_E2E_PREFIX}%@lfa-test.com")).all()
        for u in stale_users:
            s.query(CreditTransaction).filter(CreditTransaction.user_id == u.id).delete(synchronize_session=False)
            s.query(UserLicense).filter(UserLicense.user_id == u.id).delete(synchronize_session=False)
            s.delete(u)
        stale_sems = s.query(Semester).filter(Semester.name.like(f"{_E2E_PREFIX}%")).all()
        for sem in stale_sems:
            s.query(TournamentTeamEnrollment).filter(
                TournamentTeamEnrollment.semester_id == sem.id
            ).delete(synchronize_session=False)
            s.delete(sem)
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


@pytest.fixture(scope="function")
def db_session(db_engine):
    S = sessionmaker(bind=db_engine)
    s = S()
    yield s
    s.close()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ss(page, name: str) -> None:
    ts = datetime.now().strftime("%H%M%S")
    (SCREENSHOTS_DIR / f"{ts}_PROMO_{name}.png").write_bytes(page.screenshot(full_page=True))


def _admin_login(page) -> None:
    page.goto(f"{APP_URL}/login")
    page.wait_for_load_state("networkidle")
    page.fill("input[name=email]", ADMIN_EMAIL)
    page.fill("input[name=password]", ADMIN_PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(f"{APP_URL}/dashboard*", timeout=10_000)


def _make_club(db: Session, suffix: str = "") -> Club:
    name = f"{_E2E_PREFIX}-{suffix or uuid.uuid4().hex[:8]}"
    club = Club(
        name=name,
        code=f"PROMO-{uuid.uuid4().hex[:8].upper()}",
        city="Budapest",
        country="HU",
        is_active=True,
    )
    db.add(club)
    db.commit()
    db.refresh(club)
    return club


def _make_team(db: Session, club: Club, name: str, age_group: str) -> Team:
    # Each team needs at least one active member — promo wizard skips empty teams
    player = User(
        email=f"{_E2E_PREFIX}-team-{uuid.uuid4().hex[:8]}@lfa-test.com",
        name=f"{name} Player",
        password_hash=get_password_hash("Test1234!"),
        role=UserRole.STUDENT,
        is_active=True,
    )
    db.add(player)
    db.flush()

    team = Team(
        name=name,
        code=f"PT-{uuid.uuid4().hex[:8].upper()}",
        club_id=club.id,
        age_group_label=age_group,
        is_active=True,
        captain_user_id=player.id,
    )
    db.add(team)
    db.flush()

    db.add(TeamMember(team_id=team.id, user_id=player.id, role="CAPTAIN", is_active=True))
    db.commit()
    db.refresh(team)
    return team


def _csv_bytes(club_name: str) -> tuple[bytes, list[str]]:
    """Return (csv_bytes, [email1, email2]) for a 2-row valid CSV."""
    uid = uuid.uuid4().hex[:6]
    emails = [f"{_E2E_PREFIX}{uid}a@lfa-test.com", f"{_E2E_PREFIX}{uid}b@lfa-test.com"]
    rows = [
        f"Player,One{uid},{emails[0]},U15,TestTeam{uid},{club_name},100",
        f"Player,Two{uid},{emails[1]},U15,TestTeam{uid},{club_name},",
    ]
    header = "first_name,last_name,email,age_group,team_name,club_name,initial_credits"
    return ("\n".join([header] + rows)).encode("utf-8"), emails


def _cleanup_club(db: Session, club: Club) -> None:
    db.expire_all()
    teams = db.query(Team).filter(Team.club_id == club.id).all()
    for team in teams:
        db.query(TournamentTeamEnrollment).filter(
            TournamentTeamEnrollment.team_id == team.id
        ).delete(synchronize_session=False)
        db.query(TeamMember).filter(TeamMember.team_id == team.id).delete(synchronize_session=False)
        db.delete(team)
    db.query(CsvImportLog).filter(CsvImportLog.club_id == club.id).delete(synchronize_session=False)
    promo_sems = db.query(Semester).filter(Semester.name.like(f"{club.name}%")).all()
    for sem in promo_sems:
        db.query(TournamentTeamEnrollment).filter(
            TournamentTeamEnrollment.semester_id == sem.id
        ).delete(synchronize_session=False)
        db.delete(sem)
    db.delete(club)
    db.commit()


def _cleanup_imported_users(db: Session) -> None:
    try:
        stale = db.query(User).filter(User.email.like(f"{_E2E_PREFIX}%@lfa-test.com")).all()
        for u in stale:
            db.query(CreditTransaction).filter(
                CreditTransaction.user_id == u.id
            ).delete(synchronize_session=False)
            db.query(UserLicense).filter(
                UserLicense.user_id == u.id
            ).delete(synchronize_session=False)
            db.delete(u)
        db.commit()
    except Exception:
        db.rollback()


# ── PROMO-01: Create club via UI ───────────────────────────────────────────────

class TestPromo01CreateClub:
    """PROMO-01: Admin creates club via modal → redirected to club detail."""

    def test_PROMO_01_create_club_modal(self, page, db_session: Session):
        club_name = f"{_E2E_PREFIX}-UI-{uuid.uuid4().hex[:6]}"
        _admin_login(page)
        page.goto(f"{APP_URL}/admin/clubs")
        page.wait_for_load_state("networkidle")
        _ss(page, "01a_clubs_list")

        # Open the modal
        page.click("button:has-text('+ Create Club')")
        page.wait_for_selector("#create-club-modal", state="visible")

        page.fill("#create-club-modal input[name=name]", club_name)
        page.fill("#create-club-modal input[name=city]", "Budapest")
        page.fill("#create-club-modal input[name=country]", "HU")

        # Submit — JS intercepts, POSTs via fetch, then does window.location.href on redirect
        page.click("#create-club-modal button[type=submit]")
        # Wait for JS-triggered navigation to club detail page
        page.wait_for_url(
            lambda url: "/admin/clubs/" in url and url != f"{APP_URL}/admin/clubs",
            timeout=15_000,
        )
        page.wait_for_load_state("networkidle")
        _ss(page, "01b_after_create")

        assert "/admin/clubs/" in page.url, f"Expected club detail URL, got: {page.url}"
        content = page.content()
        assert club_name in content, "Club name not found on detail page"
        assert "Internal Server Error" not in content

        # DB assertion
        club = db_session.query(Club).filter(Club.name == club_name).first()
        assert club is not None, f"Club '{club_name}' not found in DB"
        assert club.city == "Budapest"
        assert club.code  # auto-generated

        try:
            _cleanup_club(db_session, club)
        except Exception:
            db_session.rollback()


# ── PROMO-02: CSV upload shows result panel ────────────────────────────────────

class TestPromo02CsvImport:
    """PROMO-02: Admin uploads valid CSV → import log created with correct counts."""

    def test_PROMO_02_csv_import_result_panel(self, page, db_session: Session):
        club = _make_club(db_session, suffix="CSV02")
        try:
            _admin_login(page)
            page.goto(f"{APP_URL}/admin/clubs/{club.id}")
            page.wait_for_load_state("networkidle")
            _ss(page, "02a_before_upload")

            csv_data, _ = _csv_bytes(club.name)

            # Set file on hidden input directly (Playwright supports this)
            page.locator("#csv-file-input").set_input_files(
                {"name": "players.csv", "mimeType": "text/csv", "buffer": csv_data}
            )
            # Wait for JS to make submit button visible
            page.wait_for_selector("#upload-submit.visible", timeout=5_000)

            # Submit via the upload button — triggers CSRF-intercepted form submit
            page.locator("#upload-submit").click()
            # Wait for JS-triggered navigation to same page with ?import_log=N
            page.wait_for_url(
                lambda url: "import_log=" in url,
                timeout=20_000,
            )
            page.wait_for_load_state("networkidle")
            _ss(page, "02b_after_upload")

            content = page.content()
            assert "Internal Server Error" not in content

            # DB assertion
            db_session.expire_all()
            log = (
                db_session.query(CsvImportLog)
                .filter(CsvImportLog.club_id == club.id)
                .order_by(CsvImportLog.id.desc())
                .first()
            )
            assert log is not None, "No CsvImportLog found after upload"
            assert log.total_rows == 2
            assert log.rows_created == 2
            assert log.rows_failed == 0

        finally:
            _cleanup_club(db_session, club)
            _cleanup_imported_users(db_session)


# ── PROMO-03: CSV with bad row shows error ─────────────────────────────────────

class TestPromo03CsvError:
    """PROMO-03: CSV with one invalid row → 1 created, 1 failed in log."""

    def test_PROMO_03_csv_with_invalid_row(self, page, db_session: Session):
        club = _make_club(db_session, suffix="CSV03")
        try:
            _admin_login(page)
            page.goto(f"{APP_URL}/admin/clubs/{club.id}")
            page.wait_for_load_state("networkidle")

            uid = uuid.uuid4().hex[:6]
            bad_csv = (
                f"first_name,last_name,email,age_group,team_name,club_name\n"
                f"Valid,Player,{_E2E_PREFIX}{uid}a@lfa-test.com,U12,Alpha,{club.name}\n"
                f"Missing,Email,,U12,Alpha,whatever\n"  # missing email → invalid
            ).encode("utf-8")

            page.locator("#csv-file-input").set_input_files(
                {"name": "mixed.csv", "mimeType": "text/csv", "buffer": bad_csv}
            )
            page.wait_for_selector("#upload-submit.visible", timeout=5_000)
            page.locator("#upload-submit").click()
            page.wait_for_url(lambda url: "import_log=" in url, timeout=20_000)
            page.wait_for_load_state("networkidle")
            _ss(page, "03a_after_mixed_upload")

            content = page.content()
            assert "Internal Server Error" not in content

            # DB: 1 created, 1 failed
            db_session.expire_all()
            log = (
                db_session.query(CsvImportLog)
                .filter(CsvImportLog.club_id == club.id)
                .order_by(CsvImportLog.id.desc())
                .first()
            )
            assert log is not None
            assert log.rows_created == 1
            assert log.rows_failed == 1
            assert len(log.errors) == 1

        finally:
            _cleanup_club(db_session, club)
            _cleanup_imported_users(db_session)


# ── PROMO-04 + 05: Promotion wizard creates tournaments ───────────────────────

class TestPromo04And05PromotionWizard:
    """
    PROMO-04: Promotion wizard with 2 age groups → 2 TEAM Semester rows
    PROMO-05: Each tournament has the club's matching teams enrolled
    """

    def test_PROMO_04_and_05_promotion_wizard(self, page, db_session: Session):
        club = _make_club(db_session, suffix="WIZARD")

        # Pre-create 2 teams per age group (U12 × 2, U15 × 2)
        _make_team(db_session, club, "Wizard U12 A", "U12")
        _make_team(db_session, club, "Wizard U12 B", "U12")
        _make_team(db_session, club, "Wizard U15 A", "U15")
        _make_team(db_session, club, "Wizard U15 B", "U15")

        try:
            _admin_login(page)
            page.goto(f"{APP_URL}/admin/clubs/{club.id}")
            page.wait_for_load_state("networkidle")
            _ss(page, "04a_club_detail_with_teams")

            # Verify teams are shown in page
            content = page.content()
            assert "U12" in content, "U12 age group not shown in club detail"
            assert "U15" in content, "U15 age group not shown in club detail"

            # Open promotion wizard modal
            page.click("button:has-text('Promotion Event')")
            page.wait_for_selector("#promotion-modal", state="visible")
            _ss(page, "04b_promotion_modal_open")

            # Fill tournament name and dates
            page.fill("#promotion-modal input[name=tournament_name]", f"{club.name} Cup 2026")
            page.fill("#promotion-modal input[name=start_date]", "2026-06-01")
            page.fill("#promotion-modal input[name=end_date]", "2026-06-02")

            # Verify age group checkboxes are present and checked
            u12_cb = page.locator('#promotion-modal input[name="age_groups"][value="U12"]')
            u15_cb = page.locator('#promotion-modal input[name="age_groups"][value="U15"]')
            assert u12_cb.count() >= 1, "U12 checkbox not found in promotion modal"
            assert u15_cb.count() >= 1, "U15 checkbox not found in promotion modal"

            # Ensure both checked
            if not u12_cb.first.is_checked():
                u12_cb.first.check()
            if not u15_cb.first.is_checked():
                u15_cb.first.check()

            _ss(page, "04c_checkboxes_checked")

            # Submit the promotion wizard
            page.click("#promotion-modal button[type=submit]")
            # Wait for redirect to /admin/promotion-events
            page.wait_for_url(
                lambda url: "/admin/promotion-events" in url,
                timeout=20_000,
            )
            page.wait_for_load_state("networkidle")
            _ss(page, "04d_after_promotion_redirect")

            content = page.content()
            assert "Internal Server Error" not in content

            # ── PROMO-04: verify 2 promotion events in DB ─────────────────────
            db_session.expire_all()
            tournaments = (
                db_session.query(Semester)
                .filter(
                    Semester.semester_category == SemesterCategory.PROMOTION_EVENT,
                    Semester.name.like(f"{club.name}%"),
                )
                .all()
            )
            assert len(tournaments) == 2, (
                f"Expected 2 promotion events, found {len(tournaments)}"
            )
            age_labels = {t.age_group for t in tournaments}
            # U12 → PRE, U15 → YOUTH via _normalize_club_age_group()
            assert "PRE" in age_labels, f"U12→PRE tournament not created, got: {age_labels}"
            assert "YOUTH" in age_labels, f"U15→YOUTH tournament not created, got: {age_labels}"

            # ── PROMO-04 (P2-A): organizer_club_id set on each event ─────────
            for tournament in tournaments:
                assert tournament.organizer_club_id == club.id, (
                    f"organizer_club_id expected {club.id}, got {tournament.organizer_club_id}"
                )
                assert tournament.organizer_sponsor_id is None

            # ── PROMO-05: each tournament has 2 teams enrolled ────────────────
            for tournament in tournaments:
                enrollments = (
                    db_session.query(TournamentTeamEnrollment)
                    .filter(TournamentTeamEnrollment.semester_id == tournament.id)
                    .all()
                )
                assert len(enrollments) == 2, (
                    f"Tournament '{tournament.name}' (age={tournament.age_group}): "
                    f"{len(enrollments)} enrollments, expected 2"
                )
                for enr in enrollments:
                    assert enr.payment_verified is True
                    assert enr.is_active is True

        finally:
            _cleanup_club(db_session, club)


# ── PROMO-00: Full zero-state golden path ──────────────────────────────────────

class TestPromo00GoldenPath:
    """
    PROMO-00: Full zero-state golden path — valós admin UX, seed nélkül.

    Covers:
      - Nav dropdown: People ▾ → 🏟️ Clubs  (CSS hover, nem hardcoded URL)
      - Clubs list oldal (empty state vagy meglévő lista)
      - Club létrehozás modal-on keresztül
      - Club megjelenik a listában → View gomb kattintás
      - CSV feltöltés → result panel (2 created, Teams megjelenik)
      - Promotion Event gomb megjelenik (teams > 0)
      - Promotion wizard → tournament létrejön, team enrolled
      - DB assertion minden lépésnél

    Egyetlen összefüggő videó bizonyítja a teljes flow-t. Futtatás:
        PLAYWRIGHT_VIDEO_DIR=test-results/videos/promo \\
        PYTEST_HEADLESS=false PYTEST_SLOW_MO=400 \\
        PYTHONPATH=. pytest tests/e2e/admin_ui/test_promotion_flow_e2e.py \\
          -k test_PROMO_00 -v -s
    """

    _PREFIX = "e2epromo00"

    def test_PROMO_00_golden_path(self, page, db_session: Session):
        club_name = f"{self._PREFIX}-GOLDEN-{uuid.uuid4().hex[:6]}"
        uid = uuid.uuid4().hex[:6]
        email1 = f"{self._PREFIX}{uid}a@lfa-test.com"
        email2 = f"{self._PREFIX}{uid}b@lfa-test.com"
        csv_data = (
            "first_name,last_name,email,age_group,team_name\n"
            f"Golden,Alpha,{email1},U15,GoldenU15\n"
            f"Golden,Beta,{email2},U15,GoldenU15\n"
        ).encode("utf-8")

        club: Club | None = None
        try:
            # ── 1. Login ──────────────────────────────────────────────────────
            _admin_login(page)
            _ss(page, "00_login_done")

            # ── 2. Nav: People ▾ → Clubs  (CSS hover dropdown, nem direct URL)
            people_trigger = page.locator(".nav-group-trigger", has_text="People")
            people_trigger.hover()
            clubs_link = page.locator(".nav-dropdown-item", has_text="Clubs")
            clubs_link.wait_for(state="visible", timeout=5_000)
            clubs_link.click()
            page.wait_for_url(
                lambda url: url.rstrip("/").endswith("/admin/clubs"),
                timeout=10_000,
            )
            page.wait_for_load_state("networkidle")
            _ss(page, "00a_clubs_list_via_nav")

            content = page.content()
            assert "Internal Server Error" not in content
            assert "Clubs" in content

            # ── 3. Create club via modal ───────────────────────────────────────
            page.click("button:has-text('+ Create Club')")
            page.wait_for_selector("#create-club-modal", state="visible")
            page.fill("#create-club-modal input[name=name]", club_name)
            page.fill("#create-club-modal input[name=city]", "Budapest")
            page.fill("#create-club-modal input[name=country]", "HU")
            page.click("#create-club-modal button[type=submit]")
            page.wait_for_url(
                lambda url: "/admin/clubs/" in url
                and not url.rstrip("/").endswith("/admin/clubs"),
                timeout=15_000,
            )
            page.wait_for_load_state("networkidle")
            _ss(page, "00b_club_created_detail")

            content = page.content()
            assert club_name in content, f"Club name not on detail page: {page.url}"
            assert "No teams yet" in content, "Expected empty teams section"
            assert "CSV Import" in content, "CSV Import section missing"
            assert "Internal Server Error" not in content

            # DB: club created with auto-generated code
            club = db_session.query(Club).filter(Club.name == club_name).first()
            assert club is not None, f"Club '{club_name}' not in DB"
            assert club.code, "Club code not auto-generated"
            assert club.city == "Budapest"

            # ── 4. Back to list via module strip → verify club in table ────────
            page.locator(".module-link", has_text="Clubs").click()
            page.wait_for_url(
                lambda url: url.rstrip("/").endswith("/admin/clubs"),
                timeout=10_000,
            )
            page.wait_for_load_state("networkidle")
            _ss(page, "00c_list_with_new_club")

            assert club_name in page.content(), "Club not visible in list after creation"

            # ── 5. Click "View" button in the table row ────────────────────────
            club_row = page.locator(f"tr:has-text('{club_name}')")
            club_row.locator("a:has-text('View')").click()
            page.wait_for_url(
                lambda url: f"/admin/clubs/{club.id}" in url,
                timeout=10_000,
            )
            page.wait_for_load_state("networkidle")
            _ss(page, "00d_detail_via_view_btn")
            assert club_name in page.content()

            # ── 6. CSV upload ─────────────────────────────────────────────────
            page.locator("#csv-file-input").set_input_files(
                {"name": "golden.csv", "mimeType": "text/csv", "buffer": csv_data}
            )
            page.wait_for_selector("#upload-submit.visible", timeout=5_000)
            page.locator("#upload-submit").click()
            page.wait_for_url(lambda url: "import_log=" in url, timeout=20_000)
            page.wait_for_load_state("networkidle")
            _ss(page, "00e_after_csv_upload")

            content = page.content()
            assert "Internal Server Error" not in content
            assert "2 created" in content, "Expected '2 created' in import result panel"
            assert "U15" in content, "Expected U15 team badge after import"
            assert page.locator("button:has-text('Promotion Event')").count() > 0, \
                "Promotion Event button not visible after CSV import"

            # DB: import log
            db_session.expire_all()
            log = (
                db_session.query(CsvImportLog)
                .filter(CsvImportLog.club_id == club.id)
                .order_by(CsvImportLog.id.desc())
                .first()
            )
            assert log is not None, "No CsvImportLog in DB"
            assert log.rows_created == 2
            assert log.rows_failed == 0

            # ── 7. Promotion wizard ───────────────────────────────────────────
            page.click("button:has-text('Promotion Event')")
            page.wait_for_selector("#promotion-modal", state="visible")
            _ss(page, "00f_promotion_modal")

            tourn_name = f"{club_name} Cup 2026"
            page.fill("#promotion-modal input[name=tournament_name]", tourn_name)
            page.fill("#promotion-modal input[name=start_date]", "2026-07-01")
            page.fill("#promotion-modal input[name=end_date]", "2026-07-02")

            u15_cb = page.locator('#promotion-modal input[name="age_groups"][value="U15"]')
            assert u15_cb.count() >= 1, "U15 checkbox missing in promotion modal"
            if not u15_cb.first.is_checked():
                u15_cb.first.check()

            page.click("#promotion-modal button[type=submit]")
            page.wait_for_url(lambda url: "/admin/promotion-events" in url, timeout=20_000)
            page.wait_for_load_state("networkidle")
            _ss(page, "00g_promotion_events_page")

            content = page.content()
            assert "Internal Server Error" not in content
            assert tourn_name in content or "Promotion tournaments created" in content, \
                "Expected tournament name or success banner on /admin/promotion-events"

            # ── 8. DB assertions ──────────────────────────────────────────────
            db_session.expire_all()
            tournaments = (
                db_session.query(Semester)
                .filter(
                    Semester.semester_category == SemesterCategory.PROMOTION_EVENT,
                    Semester.name.like(f"{club_name}%"),
                )
                .all()
            )
            assert len(tournaments) == 1, f"Expected 1 promotion event, found {len(tournaments)}"
            # U15 → mapped to YOUTH by _normalize_club_age_group()
            assert tournaments[0].age_group == "YOUTH", (
                f"Expected Semester.age_group='YOUTH' (U15 normalized), got '{tournaments[0].age_group}'"
            )
            # P2-A: organizer_club_id must be set to the source club
            assert tournaments[0].organizer_club_id == club.id, (
                f"organizer_club_id expected {club.id}, got {tournaments[0].organizer_club_id}"
            )

            enrollments = (
                db_session.query(TournamentTeamEnrollment)
                .filter(TournamentTeamEnrollment.semester_id == tournaments[0].id)
                .all()
            )
            assert len(enrollments) == 1, f"Expected 1 enrollment, found {len(enrollments)}"
            assert enrollments[0].payment_verified is True

        finally:
            # Set KEEP_PROMO_DATA=1 to skip cleanup (for local UI/DB verification)
            if not os.environ.get("KEEP_PROMO_DATA"):
                try:
                    target = club or db_session.query(Club).filter(Club.name == club_name).first()
                    if target:
                        _cleanup_club(db_session, target)
                except Exception:
                    db_session.rollback()
                _cleanup_imported_users(db_session)


# ── PROMO-06: Team detail page shows individual members ────────────────────────

class TestPromo06TeamMembers:
    """
    PROMO-06: After CSV import the admin clicks "View members" on the team row
    and sees all imported players listed by name and email on the team detail page.

    This validates the full audit trail:
      CSV → User records → TeamMember rows → /admin/clubs/{id}/teams/{tid} UI
    """

    def test_PROMO_06_team_members_visible(self, page, db_session: Session):
        club_name = f"{_E2E_PREFIX}06-{uuid.uuid4().hex[:6]}"
        club: Club | None = None

        # Two unique players for this test run
        uid = uuid.uuid4().hex[:6]
        p1_email = f"{_E2E_PREFIX}06{uid}a@lfa-test.com"
        p2_email = f"{_E2E_PREFIX}06{uid}b@lfa-test.com"
        team_name = f"PromoSix U15"
        csv_data = (
            "first_name,last_name,email,age_group,team_name,club_name\n"
            f"GoldenSix,Alpha,{p1_email},U15,{team_name},{club_name}\n"
            f"GoldenSix,Beta,{p2_email},U15,{team_name},{club_name}\n"
        ).encode("utf-8")

        try:
            _admin_login(page)

            # ── 1. Create club via modal ───────────────────────────────────
            page.goto(f"{APP_URL}/admin/clubs")
            page.wait_for_load_state("networkidle")
            page.click("button:has-text('+ Create Club')")
            page.wait_for_selector("#create-club-modal", state="visible", timeout=5_000)
            page.fill("#create-club-modal input[name=name]", club_name)
            page.fill("#create-club-modal input[name=city]", "Budapest")
            page.fill("#create-club-modal input[name=country]", "HU")
            page.evaluate("""
                const modal = document.querySelector('#create-club-modal');
                const form = modal.querySelector('form');
                const csrf = (document.cookie.split('; ').find(r => r.startsWith('csrf_token=')) || '').split('=')[1];
                form.querySelector('input[name=csrf_token]') && (form.querySelector('input[name=csrf_token]').value = csrf);
            """)
            page.locator("#create-club-modal button[type=submit]").click()
            page.wait_for_url(
                lambda url: "/admin/clubs/" in url and url.rstrip("/") != f"{APP_URL}/admin/clubs",
                timeout=15_000,
            )
            page.wait_for_load_state("networkidle")

            # Resolve club id from URL
            club_id = int(page.url.rstrip("/").split("/admin/clubs/")[1].split("/")[0].split("?")[0])
            db_session.expire_all()
            club = db_session.query(Club).filter(Club.id == club_id).first()
            assert club is not None

            # ── 2. Upload CSV ─────────────────────────────────────────────
            page.locator("#csv-file-input").set_input_files(
                {"name": "promo06.csv", "mimeType": "text/csv", "buffer": csv_data}
            )
            page.wait_for_selector("#upload-submit.visible", timeout=5_000)
            page.locator("#upload-submit").click()
            page.wait_for_url(lambda url: "import_log=" in url, timeout=20_000)
            page.wait_for_load_state("networkidle")
            _ss(page, "06_after_csv")

            content = page.content()
            assert "Internal Server Error" not in content
            assert "2 created" in content or "rows_created" in content

            # ── 3. Click "View members" for the PromoSix U15 team ─────────
            team_row = page.locator(f".team-row:has-text('{team_name}')")
            assert team_row.count() >= 1, f"Team row '{team_name}' not visible in club detail"
            view_btn = team_row.locator("a.view-team-btn")
            assert view_btn.count() >= 1, "View members button not found in team row"
            view_btn.click()
            page.wait_for_url(
                lambda url: f"/admin/clubs/{club_id}/teams/" in url,
                timeout=10_000,
            )
            page.wait_for_load_state("networkidle")
            _ss(page, "06a_team_detail")

            # ── 4. Assert members visible by name + email + role ──────────
            html = page.content()
            assert "Internal Server Error" not in html

            assert "GoldenSix Alpha" in html, "Player 1 name not visible on team detail page"
            assert "GoldenSix Beta" in html,  "Player 2 name not visible on team detail page"
            assert p1_email in html, f"Player 1 email {p1_email} not visible"
            assert p2_email in html, f"Player 2 email {p2_email} not visible"
            assert "PLAYER" in html, "PLAYER role badge not visible"

            # ── 5. DB assertions ──────────────────────────────────────────
            db_session.expire_all()
            team = db_session.query(Team).filter(
                Team.club_id == club_id,
                Team.name == team_name,
            ).first()
            assert team is not None, f"Team '{team_name}' not in DB"
            members = db_session.query(TeamMember).filter(
                TeamMember.team_id == team.id,
            ).all()
            assert len(members) == 2, f"Expected 2 TeamMember rows, found {len(members)}"
            member_emails = {
                db_session.query(User).filter(User.id == m.user_id).first().email
                for m in members
            }
            assert p1_email in member_emails, "Player 1 not in TeamMember DB"
            assert p2_email in member_emails, "Player 2 not in TeamMember DB"

        finally:
            try:
                target = club or db_session.query(Club).filter(Club.name == club_name).first()
                if target:
                    _cleanup_club(db_session, target)
            except Exception:
                db_session.rollback()
            _cleanup_imported_users(db_session)
