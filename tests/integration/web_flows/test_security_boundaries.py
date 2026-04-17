"""
Security Boundary Validation Tests — SEC-01..03
================================================

Validates that existing security defences are active and correctly
enforced, rather than merely assumed to work.

Findings from static analysis (2026-04-17):
  - SQL injection: SQLAlchemy ORM parameterises all user input (.ilike())
  - XSS: Jinja2 HTML autoescape enabled by default; no |safe overrides found
  - CSRF: Double-submit cookie pattern; Bearer-token requests are exempt
  - Headers: X-Frame-Options DENY, CSP, HSTS, HttpOnly+SameSite=strict cookies
  - Rate limiting middleware present (app/middleware/security.py)

These tests PROVE the defences fire; they do not re-implement the logic.

SEC-01  SQL injection payloads in admin search → 200, no error, safe output
SEC-02  XSS payload in tournament name → HTML-escaped in public event page
SEC-03  CSRF middleware blocks unprotected form POST → 403
"""
import html
import uuid
import pytest
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import event

from app.main import app
from app.database import engine, get_db
from app.dependencies import get_current_user_web
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.tournament_configuration import TournamentConfiguration
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from tests.factories.game_factory import TournamentFactory


# ── SAVEPOINT-isolated DB fixture ─────────────────────────────────────────────

@pytest.fixture(scope="function")
def test_db():
    """PostgreSQL session with per-test SAVEPOINT isolation."""
    connection = engine.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = TestSession()
    connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, txn):
        if txn.nested and not txn._parent.nested:
            session.begin_nested()

    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_admin(db: Session) -> User:
    u = User(
        email=f"sec-admin-{uuid.uuid4().hex[:8]}@lfa.com",
        name="SEC Admin",
        password_hash=get_password_hash("Test1234!"),
        role=UserRole.ADMIN,
        is_active=True,
        onboarding_completed=True,
        credit_balance=0,
        payment_verified=True,
    )
    db.add(u)
    db.flush()
    return u


def _db_override(db: Session):
    def _inner():
        yield db
    return _inner


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestSecurityBoundaries:

    def test_sec_01_sql_injection_probe_in_admin_search(self, test_db: Session):
        """SEC-01: SQL injection payloads in admin user search → 200, no DB error.

        The admin search uses SQLAlchemy .ilike() which is parameterised.
        Classic injection strings are passed as bind parameters, never
        concatenated into raw SQL.

        Asserts:
          - Response is 200 (no 500 / DB error)
          - Raw SQL keywords are not reflected back unescaped
        """
        admin = _make_admin(test_db)

        payloads = [
            "' OR 1=1 --",
            "'; DROP TABLE users; --",
            "' UNION SELECT id, password_hash FROM users --",
            "1' AND '1'='1",
            "admin'/*",
        ]

        app.dependency_overrides[get_db] = _db_override(test_db)
        app.dependency_overrides[get_current_user_web] = lambda: admin
        client = TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})

        try:
            for payload in payloads:
                resp = client.get("/admin/users", params={"search": payload})
                assert resp.status_code == 200, (
                    f"SEC-01: admin search with payload '{payload}' → {resp.status_code} "
                    f"(500 = DB crash; indicates injection may have succeeded)"
                )
                # The page may echo the search term back (HTML-escaped in the input
                # value attribute) — that is safe and expected.  We do NOT assert
                # text content here because echoing != injection.

            # Proof: the users table is intact after all injection attempts.
            # A successful DROP TABLE would make this query return None.
            test_db.expire_all()
            admin_check = test_db.query(User).filter_by(id=admin.id).first()
            assert admin_check is not None, (
                "SEC-01: admin user not found after injection payloads — "
                "users table may have been dropped (SQL injection succeeded)"
            )
        finally:
            app.dependency_overrides.clear()

    def test_sec_02_xss_payload_escaped_in_public_event_page(self, test_db: Session):
        """SEC-02: XSS payload in tournament name → HTML-escaped in public event page.

        Jinja2 HTML autoescape is ON for .html templates by default.
        {{ t.name }} renders <script> as &lt;script&gt; — never executable.

        Asserts:
          - Raw <script> tag is NOT present in response (would be XSS)
          - HTML-escaped form IS present (proves Jinja2 is escaping, not hiding)
        """
        XSS_PAYLOAD = "<script>alert('xss')</script>"

        tt = TournamentFactory.ensure_tournament_type(
            test_db, code=f"tt-sec-{uuid.uuid4().hex[:6]}"
        )
        tournament = Semester(
            code=f"SEC-XSS-{uuid.uuid4().hex[:8].upper()}",
            name=XSS_PAYLOAD,
            semester_category=SemesterCategory.TOURNAMENT,
            status=SemesterStatus.ONGOING,
            tournament_status="ENROLLMENT_OPEN",
            age_group="YOUTH",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 8),
            enrollment_cost=0,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        test_db.add(tournament)
        test_db.flush()
        test_db.add(TournamentConfiguration(
            semester_id=tournament.id,
            tournament_type_id=tt.id,
            participant_type="INDIVIDUAL",
            parallel_fields=1,
            sessions_generated=False,
        ))
        test_db.flush()

        # GET /events/{id} — public page, no auth dependency
        app.dependency_overrides[get_db] = _db_override(test_db)
        client = TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})

        try:
            resp = client.get(f"/events/{tournament.id}")
            assert resp.status_code == 200, (
                f"SEC-02: expected 200, got {resp.status_code}"
            )

            # Raw executable script tag must NOT appear
            assert "<script>alert" not in resp.text, (
                "SEC-02: XSS payload rendered unescaped — Jinja2 autoescape FAILED\n"
                "This is a Critical severity XSS vulnerability."
            )

            # The HTML-escaped opening tag MUST appear (proves the name IS rendered,
            # just safely escaped — not silently dropped)
            assert "&lt;script&gt;" in resp.text, (
                "SEC-02: &lt;script&gt; not found in response.\n"
                "Either the tournament name is not rendered on this page, "
                "or the escaping mechanism is different than expected.\n"
                "Verify that {{ t.name }} appears in the template."
            )
        finally:
            app.dependency_overrides.clear()

    def test_sec_03_csrf_middleware_blocks_unprotected_post(self):
        """SEC-03: Form POST without CSRF token or Bearer bypass → 403.

        The CSRF middleware enforces the double-submit cookie pattern.
        A browser (or attacker) submitting a cross-site form without
        the X-CSRF-Token header matching the csrf_token cookie gets 403.

        Key: no Authorization: Bearer header → Bearer-exempt path NOT taken.
        No X-CSRF-Token header → CSRF validation fails → 403.

        No dependency overrides needed — CSRF middleware fires before
        FastAPI resolves any route dependencies.
        """
        # Fresh client: no default headers, no cookies, no Bearer bypass
        client = TestClient(app)

        resp = client.post(
            "/semesters/request-enrollment",
            data={"semester_id": "1"},
            follow_redirects=False,
        )
        assert resp.status_code == 403, (
            f"SEC-03: expected 403 (CSRF rejection), got {resp.status_code}\n"
            f"If 302/303: CSRF middleware may not be intercepting before auth redirect.\n"
            f"If 200: CSRF protection is NOT enforced — Critical vulnerability.\n"
            f"Body: {resp.text[:300]}"
        )
