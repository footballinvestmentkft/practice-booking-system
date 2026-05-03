"""
E2E Promotion Event Flow Test
==============================
Proves that a clean DB → bootstrap → full promotion event works step-by-step.

Runs against: lfa_intern_system_e2e_clean (fresh DB, already bootstrapped)

Flow tested:
  1. Verify bootstrap state (club, campus, tournament type, teams+players)
  2. Promotion wizard → 2 TEAM tournaments created (U12 + U15)
  3. Tournament detail → teams auto-enrolled
  4. Status → ENROLLMENT_OPEN
  5. Teams page → teams listed
  6. Status → ENROLLMENT_CLOSED (needs min participants)
  7. Status → CHECK_IN_OPEN
  8. Set master_instructor_id (required for IN_PROGRESS)
  9. Status → IN_PROGRESS (triggers session generation)
 10. Verify sessions created

Usage:
    DATABASE_URL="postgresql://postgres:postgres@localhost:5432/lfa_intern_system_e2e_clean" \\
        SECRET_KEY="e2e-test" PYTHONPATH=. python scripts/e2e_promo_flow_test.py
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/lfa_intern_system_e2e_clean")
os.environ.setdefault("SECRET_KEY", "e2e-test-secret-key-minimum-32-chars-needed")

# ── TestClient setup ──────────────────────────────────────────────────────────
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.campus import Campus  # noqa: E402
from app.models.club import Club  # noqa: E402
from app.models.semester import Semester  # noqa: E402
from app.models.session import Session as SessionModel  # noqa: E402
from app.models.team import Team, TeamMember, TournamentTeamEnrollment  # noqa: E402
from app.models.tournament_type import TournamentType  # noqa: E402
from app.models.user import User, UserRole  # noqa: E402
from app.dependencies import (  # noqa: E402
    get_current_user_web,
    get_current_admin_user_hybrid,
    get_current_admin_or_instructor_user_hybrid,
)

# ── Globals ───────────────────────────────────────────────────────────────────
_db = None
_admin_user = None

PASS = 0
FAIL = 0
_errors = []


# ── Formatting ────────────────────────────────────────────────────────────────
def section(title: str) -> None:
    print(f"\n{'='*65}")
    print(f"  {title}")
    print("="*65)


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  ✅  {msg}")


def fail(msg: str, fatal: bool = True) -> None:
    global FAIL
    FAIL += 1
    _errors.append(msg)
    print(f"  ❌  {msg}")
    if fatal:
        _print_summary()
        sys.exit(1)


def info(msg: str) -> None:
    print(f"       {msg}")


# ── Auth override helpers ─────────────────────────────────────────────────────
def _get_admin():
    return _admin_user


def _setup_auth_overrides() -> None:
    app.dependency_overrides[get_current_user_web] = _get_admin
    app.dependency_overrides[get_current_admin_user_hybrid] = _get_admin
    app.dependency_overrides[get_current_admin_or_instructor_user_hybrid] = _get_admin


# ── Assertion helpers ─────────────────────────────────────────────────────────
def assert_redirect(resp, expected_path_fragment: str, label: str) -> None:
    """After a form POST, assert the redirect location contains expected_path_fragment."""
    location = resp.headers.get("location", "")
    if resp.status_code not in (302, 303):
        fail(f"{label}: expected redirect, got HTTP {resp.status_code} | body={resp.text[:200]}")
    if "error=" in location:
        # Decode URL-encoded error
        from urllib.parse import unquote
        err = unquote(location.split("error=")[1].split("&")[0]).replace("+", " ")
        fail(f"{label}: redirect contains error: {err}")
    if expected_path_fragment and expected_path_fragment not in location:
        fail(f"{label}: redirect to {location!r} — expected '{expected_path_fragment}'")
    ok(f"{label} → {resp.status_code} redirect → {location.split('?')[0]}")


def assert_ok(resp, label: str) -> None:
    """Assert a GET response is 200."""
    if resp.status_code != 200:
        fail(f"{label}: expected 200, got {resp.status_code} | {resp.text[:200]}")
    ok(f"{label} → 200 OK")


def assert_api_ok(resp, label: str) -> dict:
    """Assert a JSON API response is 2xx and return parsed body."""
    if resp.status_code not in (200, 201):
        body = {}
        try:
            body = resp.json()
        except Exception:
            pass
        fail(f"{label}: expected 2xx, got {resp.status_code} | {body or resp.text[:200]}")
    body = resp.json()
    ok(f"{label} → {resp.status_code}: {_brief(body)}")
    return body


def _brief(d: dict) -> str:
    """Short one-line summary of a JSON dict for display."""
    if isinstance(d, dict):
        keys = list(d.keys())[:4]
        return "{" + ", ".join(f"{k}: {str(d[k])[:30]!r}" for k in keys) + ("…}" if len(d) > 4 else "}")
    return str(d)[:80]


# ── Summary ───────────────────────────────────────────────────────────────────
def _print_summary() -> None:
    print(f"\n{'='*65}")
    print(f"  RESULT: {PASS} passed  |  {FAIL} failed")
    if _errors:
        print()
        for i, e in enumerate(_errors, 1):
            print(f"  {i}. ❌ {e}")
    print("="*65 + "\n")


# ── Main ──────────────────────────────────────────────────────────────────────
def run() -> None:
    global _db, _admin_user

    _db = SessionLocal()

    # Resolve admin user BEFORE TestClient initializes
    _admin_user = _db.query(User).filter(User.email == "admin@lfa.com").first()
    if not _admin_user:
        print("❌ admin@lfa.com not found — run bootstrap_clean.py first")
        sys.exit(1)

    _setup_auth_overrides()
    client = TestClient(app, follow_redirects=False)

    # ── Obtain CSRF token (like a browser: GET any page first) ────────────────
    _csrf_resp = client.get("/admin/tournaments")
    _csrf_token = _csrf_resp.cookies.get("csrf_token", "")
    if not _csrf_token:
        fail("Could not get CSRF token from GET /admin/tournaments")
    info(f"CSRF token obtained: {_csrf_token[:12]}…")

    def post_form(url: str, data: dict) -> "TestClient response":
        """POST a form with CSRF token + cookie (mirrors browser behaviour)."""
        return client.post(
            url,
            data=data,
            headers={"X-CSRF-Token": _csrf_token},
        )

    try:
        # ── Step 1: Bootstrap state ───────────────────────────────────────────
        section("STEP 1 — Verify bootstrap state")

        club = _db.query(Club).filter(Club.code == "LFA-BOOT").first()
        if not club:
            fail("LFA_BOOTSTRAP_CLUB (code=LFA-BOOT) not found — run bootstrap_clean.py")
        ok(f"Club: {club.name!r}  id={club.id}")

        campus = _db.query(Campus).filter(Campus.is_active == True).first()  # noqa: E712
        if not campus:
            fail("No active Campus found — run bootstrap_clean.py")
        ok(f"Campus: {campus.name!r}  id={campus.id}")

        tt_league = _db.query(TournamentType).filter(TournamentType.code == "league").first()
        if not tt_league:
            fail("TournamentType 'league' not found — run bootstrap_clean.py")
        ok(f"TournamentType: {tt_league.display_name!r}  id={tt_league.id}")

        teams = _db.query(Team).filter(Team.club_id == club.id, Team.is_active == True).all()  # noqa: E712
        ok(f"Teams: {len(teams)} ({', '.join(t.age_group_label for t in teams if t.age_group_label)})")

        for t in teams:
            mc = _db.query(TeamMember).filter(TeamMember.team_id == t.id).count()
            info(f"  {t.name}: {mc} players")
            if mc == 0:
                fail(f"Team {t.name!r} has 0 players — promotion wizard will skip it", fatal=False)

        instructor = _db.query(User).filter(User.email == "instructor@lfa.com").first()
        if not instructor:
            fail("instructor@lfa.com not found — run bootstrap_clean.py")
        ok(f"Instructor: {instructor.email}  id={instructor.id}")

        # ── Step 2: Promotion wizard ──────────────────────────────────────────
        section("STEP 2 — Promotion wizard (POST /admin/clubs/{id}/promotion)")

        resp = post_form(
            f"/admin/clubs/{club.id}/promotion",
            data={
                "tournament_name": "E2E Clean Flow Test",
                "start_date": "2026-07-01",
                "end_date": "2026-07-03",
                "campus_id": str(campus.id),
                "tournament_type_id": str(tt_league.id),
                "game_preset_id": "",           # optional
                "age_groups": ["U12", "U15"],
            },
        )
        assert_redirect(resp, "/admin/promotion-events", "Promotion wizard")

        _db.expire_all()
        tournaments = _db.query(Semester).filter(
            Semester.name.like("E2E Clean Flow Test%")
        ).order_by(Semester.id).all()

        if not tournaments:
            fail("No tournaments found after promotion wizard")
        ok(f"Tournaments created: {len(tournaments)}")
        for t in tournaments:
            info(f"  id={t.id}  name={t.name!r}  status={t.tournament_status}  campus_id={t.campus_id}")

        # ── Step 3: Teams auto-enrolled ───────────────────────────────────────
        section("STEP 3 — Verify teams auto-enrolled")

        for t in tournaments:
            enrollments = _db.query(TournamentTeamEnrollment).filter(
                TournamentTeamEnrollment.semester_id == t.id,
                TournamentTeamEnrollment.is_active == True,  # noqa: E712
            ).all()
            if not enrollments:
                fail(f"Tournament {t.name!r} has 0 enrolled teams — wizard failed to enroll")
            ok(f"  {t.name!r}: {len(enrollments)} teams enrolled")
            for e in enrollments:
                team = _db.query(Team).filter(Team.id == e.team_id).first()
                info(f"    {team.name if team else '?'} (payment_verified={e.payment_verified})")

        # Work with U12 tournament for the rest
        tournament = next((t for t in tournaments if "U12" in t.name), tournaments[0])
        info(f"\n  → Using tournament: {tournament.name!r}  id={tournament.id}")

        # ── Step 4: Open Enrollment ───────────────────────────────────────────
        section("STEP 4 — Status: DRAFT → ENROLLMENT_OPEN")
        info(f"  Precondition: campus_id={tournament.campus_id}")

        resp = client.patch(
            f"/api/v1/tournaments/{tournament.id}/status",
            json={"new_status": "ENROLLMENT_OPEN", "reason": "E2E test"},
        )
        assert_api_ok(resp, "DRAFT → ENROLLMENT_OPEN")

        _db.expire_all()
        _db.refresh(tournament)
        info(f"  New status: {tournament.tournament_status}")

        # ── Step 5: Teams page accessible in ENROLLMENT_OPEN ─────────────────
        section("STEP 5 — GET /admin/tournaments/{id}/teams (accessible in ENROLLMENT_OPEN)")

        resp = client.get(f"/admin/tournaments/{tournament.id}/teams")
        assert_ok(resp, "Teams page")
        if "Bootstrap U12" in resp.text or "Enrolled Teams" in resp.text:
            info("  Teams listed in HTML ✓")

        # ── Step 6: Close Enrollment ──────────────────────────────────────────
        section("STEP 6 — Status: ENROLLMENT_OPEN → ENROLLMENT_CLOSED")

        resp = client.patch(
            f"/api/v1/tournaments/{tournament.id}/status",
            json={"new_status": "ENROLLMENT_CLOSED", "reason": "E2E test"},
        )
        assert_api_ok(resp, "ENROLLMENT_OPEN → ENROLLMENT_CLOSED")

        _db.expire_all()
        _db.refresh(tournament)
        info(f"  New status: {tournament.tournament_status}")

        # ── Step 7: Open Check-In ─────────────────────────────────────────────
        section("STEP 7 — Status: ENROLLMENT_CLOSED → CHECK_IN_OPEN")

        resp = client.patch(
            f"/api/v1/tournaments/{tournament.id}/status",
            json={"new_status": "CHECK_IN_OPEN", "reason": "E2E test"},
        )
        assert_api_ok(resp, "ENROLLMENT_CLOSED → CHECK_IN_OPEN")

        _db.expire_all()
        _db.refresh(tournament)
        info(f"  New status: {tournament.tournament_status}")

        # ── Step 8: Set master_instructor_id ──────────────────────────────────
        section("STEP 8 — Set master_instructor_id (required for IN_PROGRESS)")
        info(f"  Using instructor: {instructor.email}  id={instructor.id}")

        tournament.master_instructor_id = instructor.id
        _db.commit()
        _db.refresh(tournament)
        ok(f"master_instructor_id set → {tournament.master_instructor_id}")

        # ── Step 9: Start Tournament (IN_PROGRESS) ────────────────────────────
        section("STEP 9 — Status: CHECK_IN_OPEN → IN_PROGRESS (triggers session gen)")

        resp = client.patch(
            f"/api/v1/tournaments/{tournament.id}/status",
            json={"new_status": "IN_PROGRESS", "reason": "E2E test"},
        )
        assert_api_ok(resp, "CHECK_IN_OPEN → IN_PROGRESS")

        _db.expire_all()
        _db.refresh(tournament)
        info(f"  New status: {tournament.tournament_status}")

        # ── Step 10: Verify sessions generated ───────────────────────────────
        section("STEP 10 — Verify sessions generated")

        session_count = _db.query(SessionModel).filter(
            SessionModel.semester_id == tournament.id
        ).count()

        if session_count == 0:
            fail("No sessions generated after IN_PROGRESS transition")
        ok(f"Sessions generated: {session_count}")

        sessions = _db.query(SessionModel).filter(
            SessionModel.semester_id == tournament.id
        ).limit(3).all()
        for s in sessions:
            info(f"  Session id={s.id}  round={getattr(s, 'round_number', '?')}  "
                 f"team1={getattr(s, 'team1_id', '?')} vs team2={getattr(s, 'team2_id', '?')}")

        # ── Summary ───────────────────────────────────────────────────────────
        section("FINAL STATE")
        _db.expire_all()
        _db.refresh(tournament)
        info(f"  Tournament:      {tournament.name!r}")
        info(f"  Status:          {tournament.tournament_status}")
        info(f"  Campus:          id={tournament.campus_id}")
        info(f"  Instructor:      id={tournament.master_instructor_id}")
        info(f"  Sessions:        {session_count}")
        enr_count = _db.query(TournamentTeamEnrollment).filter(
            TournamentTeamEnrollment.semester_id == tournament.id
        ).count()
        info(f"  Enrolled teams:  {enr_count}")

    except SystemExit:
        raise
    except Exception as e:
        fail(f"Unexpected exception: {type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        _db.close()

    _print_summary()
    if FAIL > 0:
        sys.exit(1)


if __name__ == "__main__":
    run()
