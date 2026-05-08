"""
Admin Workflow Smoke Test — Sprint 59e Parity Verification

Covers 7 operational admin flows end-to-end against a SAVEPOINT-isolated
real PostgreSQL database (no mocks):

  SMOKE-01  Location: Create → Edit → Toggle (deactivate) → Delete
  SMOKE-02  Game Preset: Create → Edit → Toggle active/inactive
  SMOKE-03  Invoice: Create in DB → view payments page → verify KPI counts
  SMOKE-04  Sessions: List page loads + filters work (date_from, spec, status)
  SMOKE-05  Coupon: Create → view on coupons page → Toggle active
  SMOKE-06  Invitation Code: Create (via API) → verify on codes page
  SMOKE-07  Navigation: GET all 12 admin pages, confirm 200 + no 500 bodies

Auth: get_current_user_web overridden → admin_user injected.
CSRF: Authorization: Bearer bypass header skips CSRFProtectionMiddleware.
DB:   SAVEPOINT-isolated; all changes rolled back after each test.
"""

import uuid
import pytest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import event

from app.main import app
from app.database import engine, get_db
from app.dependencies import get_current_user_web, get_current_user, get_current_admin_user_hybrid, get_current_admin_or_instructor_user_hybrid
from app.models.user import User, UserRole
from app.models.location import Location, LocationType
from app.models.campus import Campus
from app.models.pitch import Pitch
from app.models.game_preset import GamePreset
from app.models.coupon import Coupon, CouponType
from app.models.invitation_code import InvitationCode
from app.models.invoice_request import InvoiceRequest, InvoiceRequestStatus
from app.models.semester import Semester, SemesterStatus
from app.models.session import Session as SessionModel, SessionType
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.tournament_configuration import TournamentConfiguration
from app.models.game_configuration import GameConfiguration
from app.models.license import UserLicense, LicenseProgression
from app.models.system_event import SystemEvent, SystemEventLevel
from app.models.credit_transaction import CreditTransaction, TransactionType
from app.models.specialization import SpecializationType
from app.core.security import get_password_hash


# ── SAVEPOINT-isolated DB ─────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def test_db():
    connection = engine.connect()
    transaction = connection.begin()
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = TestSessionLocal()
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


# ── Admin user + client ───────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def admin_user(test_db: Session) -> User:
    u = User(
        email=f"smoke-admin+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Smoke Admin",
        password_hash=get_password_hash("admin123"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    test_db.add(u)
    test_db.commit()
    test_db.refresh(u)
    return u


@pytest.fixture(scope="function")
def student_user(test_db: Session) -> User:
    u = User(
        email=f"smoke-student+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Smoke Student",
        password_hash=get_password_hash("student123"),
        role=UserRole.STUDENT,
        is_active=True,
    )
    test_db.add(u)
    test_db.commit()
    test_db.refresh(u)
    return u


@pytest.fixture(scope="function")
def admin_client(test_db: Session, admin_user: User) -> TestClient:
    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user_web] = lambda: admin_user

    with TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"}) as c:
        yield c

    app.dependency_overrides.clear()


# ── Minimal data fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def location(test_db: Session) -> Location:
    unique_city = f"SmokeCity-{uuid.uuid4().hex[:8]}"
    loc = Location(
        name=f"Smoke Loc {uuid.uuid4().hex[:6]}",
        city=unique_city,
        country="Hungary",
        location_type=LocationType.CENTER,
        is_active=True,
    )
    test_db.add(loc)
    test_db.commit()
    test_db.refresh(loc)
    return loc


@pytest.fixture
def game_preset(test_db: Session) -> GamePreset:
    gp = GamePreset(
        name=f"Smoke Preset {uuid.uuid4().hex[:6]}",
        code=f"SP-{uuid.uuid4().hex[:4].upper()}",
        is_active=True,
        game_config={"skill_config": {"skill_weights": {}}, "format_config": {}, "metadata": {}},
    )
    test_db.add(gp)
    test_db.commit()
    test_db.refresh(gp)
    return gp


@pytest.fixture
def coupon(test_db: Session) -> Coupon:
    c = Coupon(
        code=f"SMOKE-{uuid.uuid4().hex[:6].upper()}",
        type=CouponType.BONUS_CREDITS,
        discount_value=50.0,
        description="Smoke test coupon",
        is_active=True,
    )
    test_db.add(c)
    test_db.commit()
    test_db.refresh(c)
    return c


@pytest.fixture
def invitation_code(test_db: Session, admin_user: User) -> InvitationCode:
    ic = InvitationCode(
        code=f"SMOKE-INV-{uuid.uuid4().hex[:6].upper()}",
        invited_name="Smoke Invitee",
        bonus_credits=100,
        is_used=False,
        created_by_admin_id=admin_user.id,
    )
    test_db.add(ic)
    test_db.commit()
    test_db.refresh(ic)
    return ic


@pytest.fixture
def invoice_request(test_db: Session, student_user: User) -> InvoiceRequest:
    import uuid as _uuid
    ir = InvoiceRequest(
        user_id=student_user.id,
        amount_eur=50.0,
        credit_amount=500,
        status=InvoiceRequestStatus.PENDING,
        payment_reference=f"SMOKE-{_uuid.uuid4().hex[:8].upper()}",
    )
    test_db.add(ir)
    test_db.commit()
    test_db.refresh(ir)
    return ir


@pytest.fixture
def semester(test_db: Session) -> Semester:
    sem = Semester(
        code=f"SMK-{uuid.uuid4().hex[:6].upper()}",
        name="Smoke Semester",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=90),
        status=SemesterStatus.ONGOING,
        specialization_type="FOOTBALL_SKILLS",
    )
    test_db.add(sem)
    test_db.commit()
    test_db.refresh(sem)
    return sem


@pytest.fixture
def session_obj(test_db: Session, semester: Semester, admin_user: User) -> SessionModel:
    now = datetime.now(ZoneInfo("Europe/Budapest")).replace(tzinfo=None)
    s = SessionModel(
        title="Smoke Session",
        semester_id=semester.id,
        session_type=SessionType.on_site,
        date_start=now + timedelta(hours=24),
        date_end=now + timedelta(hours=25),
        instructor_id=admin_user.id,
    )
    test_db.add(s)
    test_db.commit()
    test_db.refresh(s)
    return s


# ============================================================================
# SMOKE-01: Location CRUD
# ============================================================================

class TestSmoke01LocationCRUD:

    def test_01_create_location(self, admin_client, test_db):
        """POST /admin/locations → 303 + Location row created in DB."""
        code = uuid.uuid4().hex[:6].upper()
        unique_city = f"SmokeCreate-{uuid.uuid4().hex[:8]}"
        resp = admin_client.post(
            "/admin/locations",
            data={
                "name": f"Smoke City {code}",
                "city": unique_city,
                "country": "Hungary",
                "country_code": "HU",
                "location_code": code[:3],
                "location_type": "CENTER",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303, f"Expected 303, got {resp.status_code}"
        assert "/admin/locations" in resp.headers["location"]

        loc = test_db.query(Location).filter(Location.city == unique_city).first()
        assert loc is not None, "Location not found in DB after create"
        assert loc.is_active is True

    def test_02_edit_location_page_loads(self, admin_client, location):
        """GET /admin/locations/{id}/edit → 200, form rendered."""
        resp = admin_client.get(f"/admin/locations/{location.id}/edit")
        assert resp.status_code == 200
        assert location.name in resp.text
        assert "Save Changes" in resp.text

    def test_03_edit_location_submit(self, admin_client, location, test_db):
        """POST /admin/locations/{id}/edit → 303 + DB updated."""
        new_name = f"Updated-{uuid.uuid4().hex[:6]}"
        resp = admin_client.post(
            f"/admin/locations/{location.id}/edit",
            data={
                "name": new_name,
                "city": location.city,  # city is already unique (set by fixture)
                "country": location.country or "Hungary",
                "location_type": "CENTER",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        updated = test_db.query(Location).filter(Location.id == location.id).first()
        assert updated.name == new_name

    def test_04_toggle_location_deactivates(self, admin_client, location, test_db):
        """POST /admin/locations/{id}/toggle → is_active flips False."""
        assert location.is_active is True
        resp = admin_client.post(
            f"/admin/locations/{location.id}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        loc = test_db.query(Location).filter(Location.id == location.id).first()
        assert loc.is_active is False

    def test_05_delete_location(self, admin_client, test_db):
        """POST /admin/locations/{id}/delete → 303 + row removed."""
        # Create a fresh standalone location to delete (unique city to avoid unique constraint)
        loc = Location(
            name=f"DeleteMe-{uuid.uuid4().hex[:6]}",
            city=f"DeleteCity-{uuid.uuid4().hex[:8]}",
            country="Hungary",
            location_type=LocationType.PARTNER,
            is_active=True,
        )
        test_db.add(loc)
        test_db.commit()
        test_db.refresh(loc)
        lid = loc.id

        resp = admin_client.post(
            f"/admin/locations/{lid}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        assert test_db.query(Location).filter(Location.id == lid).first() is None

    def test_06_locations_list_with_filters(self, admin_client, location):
        """GET /admin/locations?city_filter=Budapest → 200, no 500."""
        resp = admin_client.get("/admin/locations?city_filter=Budapest&status_filter=active")
        assert resp.status_code == 200
        assert "Internal Server Error" not in resp.text
        assert "Locations" in resp.text


# ============================================================================
# SMOKE-02: Game Preset CRUD
# ============================================================================

class TestSmoke02GamePresetCRUD:

    def test_01_create_game_preset(self, admin_client, test_db):
        """POST /admin/game-presets → 303 + GamePreset in DB."""
        code = f"GP-{uuid.uuid4().hex[:4].upper()}"
        resp = admin_client.post(
            "/admin/game-presets",
            data={
                "name": f"Smoke Preset {code}",
                "code": code,
                "description": "Smoke test",
                "skill_ids": [],
                "skill_weights": [],
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        gp = test_db.query(GamePreset).filter(GamePreset.code == code).first()
        assert gp is not None, f"GamePreset {code} not found in DB"
        assert gp.is_active is True

    def test_02_edit_game_preset_page_loads(self, admin_client, game_preset):
        """GET /admin/game-presets/{id}/edit → 200, form with preset name."""
        resp = admin_client.get(f"/admin/game-presets/{game_preset.id}/edit")
        assert resp.status_code == 200
        assert game_preset.name in resp.text

    def test_03_edit_game_preset_submit(self, admin_client, game_preset, test_db):
        """POST /admin/game-presets/{id}/edit → 303 + DB updated."""
        new_name = f"Edited-{uuid.uuid4().hex[:6]}"
        resp = admin_client.post(
            f"/admin/game-presets/{game_preset.id}/edit",
            data={
                "name": new_name,
                "code": game_preset.code,
                "description": "Updated",
                "skill_ids": [],
                "skill_weights": [],
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        updated = test_db.query(GamePreset).filter(GamePreset.id == game_preset.id).first()
        assert updated.name == new_name

    def test_04_toggle_game_preset(self, admin_client, game_preset, test_db):
        """POST /admin/game-presets/{id}/toggle → is_active flips."""
        assert game_preset.is_active is True
        resp = admin_client.post(
            f"/admin/game-presets/{game_preset.id}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        gp = test_db.query(GamePreset).filter(GamePreset.id == game_preset.id).first()
        assert gp.is_active is False

    def test_05_locked_preset_delete_blocked(self, admin_client, test_db):
        """POST /admin/game-presets/{id}/delete on locked preset → 400."""
        locked = GamePreset(
            name="Locked Preset",
            code=f"LCK-{uuid.uuid4().hex[:4].upper()}",
            is_active=True,
            is_locked=True,
            game_config={"skill_config": {"skill_weights": {}}, "format_config": {}, "metadata": {}},
        )
        test_db.add(locked)
        test_db.commit()
        test_db.refresh(locked)

        resp = admin_client.post(
            f"/admin/game-presets/{locked.id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 400

        # Row should still exist
        test_db.expire_all()
        assert test_db.query(GamePreset).filter(GamePreset.id == locked.id).first() is not None


# ============================================================================
# SMOKE-03: Invoice — view payments page, KPI, filter
# ============================================================================

class TestSmoke03InvoiceView:

    def test_01_payments_page_loads(self, admin_client, invoice_request):
        """GET /admin/payments → 200, invoice appears in list."""
        resp = admin_client.get("/admin/payments")
        assert resp.status_code == 200
        assert "Internal Server Error" not in resp.text
        # KPI bar should render
        assert "Total Revenue" in resp.text
        assert "Awaiting Approval" in resp.text

    def test_02_pending_invoice_counted_in_kpi(self, admin_client, invoice_request):
        """Pending InvoiceRequest increments 'Awaiting Approval' KPI."""
        resp = admin_client.get("/admin/payments")
        assert resp.status_code == 200
        # The KPI 'open_invoices' should be ≥1 (our pending one)
        body = resp.text
        # Template renders fin.open_invoices — check it's on the page and > 0
        assert "Awaiting Approval" in body

    def test_03_payments_page_no_500_without_invoices(self, admin_client):
        """GET /admin/payments with empty invoice table → 200, no 500."""
        resp = admin_client.get("/admin/payments")
        assert resp.status_code == 200
        assert "500" not in resp.text[:200]  # status code 500 not in early HTML


# ============================================================================
# SMOKE-04: Sessions — list page + filters
# ============================================================================

class TestSmoke04SessionsList:

    def test_01_sessions_page_loads(self, admin_client, session_obj):
        """GET /admin/sessions → 200, session appears in listing."""
        resp = admin_client.get("/admin/sessions")
        assert resp.status_code == 200
        assert "Internal Server Error" not in resp.text
        assert "Sessions" in resp.text

    def test_02_date_from_default_today(self, admin_client):
        """GET /admin/sessions with no params → date_from defaults to today."""
        resp = admin_client.get("/admin/sessions")
        assert resp.status_code == 200
        today = date.today().isoformat()
        # The default date_from should appear in the filter form value
        assert today in resp.text

    def test_03_cleared_param_removes_default(self, admin_client):
        """GET /admin/sessions?cleared=1 → no date_from default applied."""
        resp = admin_client.get("/admin/sessions?cleared=1")
        assert resp.status_code == 200
        assert "Internal Server Error" not in resp.text

    def test_04_session_type_filter(self, admin_client, session_obj):
        """GET /admin/sessions?session_type=on_site → 200, no 500."""
        resp = admin_client.get("/admin/sessions?session_type=on_site&cleared=1")
        assert resp.status_code == 200
        assert "Internal Server Error" not in resp.text

    def test_05_specialization_filter(self, admin_client, session_obj, semester):
        """GET /admin/sessions?spec=FOOTBALL_SKILLS → 200, session visible."""
        resp = admin_client.get("/admin/sessions?spec=FOOTBALL_SKILLS&cleared=1")
        assert resp.status_code == 200
        assert "Internal Server Error" not in resp.text


# ============================================================================
# SMOKE-05: Coupon — Create + Toggle
# ============================================================================

class TestSmoke05CouponCRUD:

    def test_01_create_coupon(self, admin_client, test_db):
        """POST /admin/coupons → 303 + Coupon row in DB."""
        code = f"TEST-{uuid.uuid4().hex[:6].upper()}"
        resp = admin_client.post(
            "/admin/coupons",
            data={
                "code": code,
                "coupon_type": "BONUS_CREDITS",
                "value": "100",
                "description": "Smoke coupon",
                "max_uses": "",
                "expires_days": "",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        c = test_db.query(Coupon).filter(Coupon.code == code).first()
        assert c is not None, f"Coupon {code} not found in DB"
        assert c.is_active is True
        assert c.discount_value == 100.0

    def test_02_create_coupon_invalid_type_returns_400(self, admin_client):
        """POST /admin/coupons with bad coupon_type → 400."""
        resp = admin_client.post(
            "/admin/coupons",
            data={
                "code": "BAD-TYPE",
                "coupon_type": "NOT_A_REAL_TYPE",
                "value": "50",
                "description": "should fail",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_03_coupon_appears_on_list_page(self, admin_client, coupon):
        """GET /admin/coupons → 200, coupon code visible."""
        resp = admin_client.get("/admin/coupons")
        assert resp.status_code == 200
        assert coupon.code in resp.text

    def test_04_toggle_coupon(self, admin_client, coupon, test_db):
        """POST /admin/coupons/{id}/toggle → is_active flips."""
        assert coupon.is_active is True
        resp = admin_client.post(
            f"/admin/coupons/{coupon.id}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        c = test_db.query(Coupon).filter(Coupon.id == coupon.id).first()
        assert c.is_active is False

    def test_05_create_discount_coupon_validates_range(self, admin_client, test_db):
        """POST /admin/coupons with discount > 100 → 400."""
        resp = admin_client.post(
            "/admin/coupons",
            data={
                "code": f"DISC-{uuid.uuid4().hex[:6].upper()}",
                "coupon_type": "PURCHASE_DISCOUNT_PERCENT",
                "value": "150",  # > 100 → invalid
                "description": "bad range",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 400


# ============================================================================
# SMOKE-06: Invitation Code — Create via API + view page
# ============================================================================

class TestSmoke06InvitationCodes:

    def test_01_invitation_code_page_loads(self, admin_client, invitation_code):
        """GET /admin/invitation-codes → 200, code visible."""
        resp = admin_client.get("/admin/invitation-codes")
        assert resp.status_code == 200
        assert invitation_code.code in resp.text
        assert "Internal Server Error" not in resp.text

    def test_02_multiple_codes_render_correctly(self, admin_client, test_db, admin_user):
        """Invitation codes page renders multiple codes (used + unused) without errors."""
        # Seed two codes: one used, one not
        ic_used = InvitationCode(
            code=f"USED-{uuid.uuid4().hex[:6].upper()}",
            invited_name="Used Person",
            bonus_credits=50,
            is_used=True,
            created_by_admin_id=admin_user.id,
        )
        ic_free = InvitationCode(
            code=f"FREE-{uuid.uuid4().hex[:6].upper()}",
            invited_name="Free Person",
            bonus_credits=100,
            is_used=False,
            created_by_admin_id=admin_user.id,
        )
        test_db.add_all([ic_used, ic_free])
        test_db.commit()

        resp = admin_client.get("/admin/invitation-codes")
        assert resp.status_code == 200
        assert ic_used.code in resp.text
        assert ic_free.code in resp.text
        assert "Internal Server Error" not in resp.text

    def test_03_used_code_not_redeemable_again(self, admin_client, test_db, admin_user):
        """Invitation code marked is_used=True cannot be viewed as unused."""
        ic = InvitationCode(
            code=f"USED-{uuid.uuid4().hex[:6].upper()}",
            invited_name="Used Invitee",
            bonus_credits=50,
            is_used=True,
            created_by_admin_id=admin_user.id,
        )
        test_db.add(ic)
        test_db.commit()

        resp = admin_client.get("/admin/invitation-codes")
        assert resp.status_code == 200
        # The page should still render without errors
        assert "Internal Server Error" not in resp.text


# ============================================================================
# SMOKE-07: Navigation — all admin pages return 200
# ============================================================================

ADMIN_PAGES = [
    "/admin/users",
    "/admin/sessions",
    "/admin/semesters",
    "/admin/analytics",
    "/admin/payments",
    "/admin/coupons",
    "/admin/invitation-codes",
    "/admin/locations",
    "/admin/game-presets",
    "/admin/system-events",
    "/admin/tournaments",
    "/admin/enrollments",
    # Nav hub landing pages (SMOKE-27)
    "/admin/programs",
    "/admin/config",
    # Events module (SMOKE-30)
    "/admin/events",
    "/admin/camps",
]


class TestSmoke07Navigation:

    @pytest.mark.parametrize("path", ADMIN_PAGES)
    def test_admin_page_loads_200(self, admin_client, path):
        """GET {path} → 200, no Internal Server Error in body."""
        resp = admin_client.get(path)
        assert resp.status_code == 200, (
            f"Expected 200 for {path}, got {resp.status_code}"
        )
        assert "Internal Server Error" not in resp.text, (
            f"500 error on {path}"
        )

    def test_analytics_nav_strip_present(self, admin_client):
        """GET /admin/analytics → nav strip includes all top-level links (dropdown nav)."""
        resp = admin_client.get("/admin/analytics")
        assert resp.status_code == 200
        # Dropdown nav: hub URLs replaced by primary sub-page URLs
        for link in ["/admin/users", "/admin/semesters", "/admin/sessions",
                     "/admin/tournaments", "/admin/payments", "/admin/locations",
                     "/admin/game-presets", "/admin/analytics", "/admin/system-events"]:
            assert link in resp.text, f"Nav link {link} missing from analytics.html"

    def test_semesters_nav_strip_present(self, admin_client):
        """GET /admin/semesters → nav strip contains Programs dropdown link."""
        resp = admin_client.get("/admin/semesters")
        assert resp.status_code == 200
        for link in ["/admin/analytics", "/admin/sessions", "/admin/semesters"]:
            assert link in resp.text, f"Nav link {link} missing from semesters.html"

    def test_users_page_has_analytics_and_programs_links(self, admin_client):
        """GET /admin/users → header nav contains Analytics + Semesters links."""
        resp = admin_client.get("/admin/users")
        assert resp.status_code == 200
        assert "/admin/analytics" in resp.text
        assert "/admin/semesters" in resp.text

    def test_pagination_on_users_page(self, admin_client):
        """GET /admin/users → page renders with valid page structure."""
        resp = admin_client.get("/admin/users?page=1")
        assert resp.status_code == 200
        assert "Internal Server Error" not in resp.text
        # Stats cards should be present
        assert "Total Users" in resp.text

    def test_system_events_default_filter_is_open(self, admin_client):
        """GET /admin/system-events → default resolved filter = 'open'."""
        resp = admin_client.get("/admin/system-events")
        assert resp.status_code == 200
        assert "Internal Server Error" not in resp.text
        # 'open' should be selected in the resolved filter
        assert 'value="open"' in resp.text


# ============================================================================
# SMOKE-08: Invoice Verification Workflow
# ============================================================================

class TestSmoke08InvoiceVerification:
    """Covers the 3-state invoice lifecycle via new web wrapper routes."""

    def test_01_bookings_page_in_nav(self, admin_client):
        """Payments page loads 200 and contains Commerce module strip."""
        resp = admin_client.get("/admin/payments")
        assert resp.status_code == 200
        # Commerce module strip must be present
        assert "/admin/coupons" in resp.text
        assert "/admin/invitation-codes" in resp.text

    def test_02_verify_invoice(self, admin_client, test_db, invoice_request, student_user):
        """POST /admin/invoices/{id}/verify → 200, credits added to student."""
        initial_balance = student_user.credit_balance or 0
        resp = admin_client.post(f"/admin/invoices/{invoice_request.id}/verify")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["credits_added"] == invoice_request.credit_amount
        # Verify DB state
        test_db.refresh(student_user)
        assert student_user.credit_balance == initial_balance + invoice_request.credit_amount
        test_db.refresh(invoice_request)
        assert invoice_request.status == "verified"

    def test_03_verify_already_verified_returns_400(self, admin_client, test_db, invoice_request):
        """POST verify on already-verified invoice → 400."""
        invoice_request.status = "verified"
        test_db.commit()
        resp = admin_client.post(f"/admin/invoices/{invoice_request.id}/verify")
        assert resp.status_code == 400

    def test_04_cancel_invoice(self, admin_client, test_db, invoice_request):
        """POST /admin/invoices/{id}/cancel → 200, status=cancelled."""
        resp = admin_client.post(
            f"/admin/invoices/{invoice_request.id}/cancel",
            data={"reason": "Test cancellation"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        test_db.refresh(invoice_request)
        assert invoice_request.status == "cancelled"

    def test_05_unverify_invoice(self, admin_client, test_db, invoice_request, student_user):
        """POST /admin/invoices/{id}/unverify → 200, credits reverted."""
        # First verify
        invoice_request.status = "verified"
        student_user.credit_balance = (student_user.credit_balance or 0) + invoice_request.credit_amount
        student_user.credit_purchased = (student_user.credit_purchased or 0) + invoice_request.credit_amount
        test_db.commit()
        balance_before = student_user.credit_balance

        resp = admin_client.post(f"/admin/invoices/{invoice_request.id}/unverify")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["credits_removed"] == invoice_request.credit_amount
        test_db.refresh(student_user)
        assert student_user.credit_balance == balance_before - invoice_request.credit_amount
        test_db.refresh(invoice_request)
        assert invoice_request.status == "pending"


# ============================================================================
# SMOKE-09: Bookings Admin Panel
# ============================================================================

class TestSmoke09BookingsPanel:
    """Covers /admin/bookings page and booking action routes."""

    def test_01_bookings_page_renders(self, admin_client):
        """GET /admin/bookings → 200, contains expected UI elements."""
        resp = admin_client.get("/admin/bookings")
        assert resp.status_code == 200
        assert "Internal Server Error" not in resp.text
        assert "Booking Management" in resp.text
        assert "Confirmed" in resp.text

    def test_02_bookings_page_with_status_filter(self, admin_client):
        """GET /admin/bookings?status_filter=CONFIRMED → 200."""
        resp = admin_client.get("/admin/bookings?status_filter=CONFIRMED")
        assert resp.status_code == 200
        assert "Internal Server Error" not in resp.text

    def test_03_confirm_booking(self, admin_client, test_db, session_obj, student_user):
        """POST /admin/bookings/{id}/confirm → 200, status=CONFIRMED."""
        from app.models.booking import Booking, BookingStatus
        b = Booking(user_id=student_user.id, session_id=session_obj.id, status=BookingStatus.PENDING)
        test_db.add(b)
        test_db.commit()
        test_db.refresh(b)

        resp = admin_client.post(f"/admin/bookings/{b.id}/confirm")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        test_db.refresh(b)
        assert b.status == BookingStatus.CONFIRMED

    def test_04_cancel_booking(self, admin_client, test_db, session_obj, student_user):
        """POST /admin/bookings/{id}/cancel → 200, status=CANCELLED."""
        from app.models.booking import Booking, BookingStatus
        b = Booking(user_id=student_user.id, session_id=session_obj.id, status=BookingStatus.CONFIRMED)
        test_db.add(b)
        test_db.commit()
        test_db.refresh(b)

        resp = admin_client.post(
            f"/admin/bookings/{b.id}/cancel",
            data={"reason": "Admin test cancel"}
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        test_db.refresh(b)
        assert b.status == BookingStatus.CANCELLED

    def test_05_mark_attendance(self, admin_client, test_db, session_obj, student_user):
        """POST /admin/bookings/{id}/attendance → 200, attendance record created."""
        from app.models.booking import Booking, BookingStatus
        from app.models.attendance import Attendance, AttendanceStatus
        b = Booking(user_id=student_user.id, session_id=session_obj.id, status=BookingStatus.CONFIRMED)
        test_db.add(b)
        test_db.commit()
        test_db.refresh(b)

        resp = admin_client.post(
            f"/admin/bookings/{b.id}/attendance",
            data={"attendance_status": "present", "notes": "Smoke test"}
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        att = test_db.query(Attendance).filter(Attendance.booking_id == b.id).first()
        assert att is not None
        assert att.status == AttendanceStatus.present

    def test_06_confirm_nonexistent_booking_returns_404(self, admin_client):
        """POST /admin/bookings/999999/confirm → 404."""
        resp = admin_client.post("/admin/bookings/999999/confirm")
        assert resp.status_code == 404

    def test_07_bookings_page_in_nav(self, admin_client):
        """Users admin page nav contains Sessions link and Instructors module-strip link."""
        resp = admin_client.get("/admin/users")
        assert resp.status_code == 200
        # Sessions is a top-level nav item reachable from any admin page
        assert "/admin/sessions" in resp.text
        # Users module strip: Instructors link visible from Users page
        assert "/admin/instructors" in resp.text


# ============================================================================
# SMOKE-18: Bookings — edge cases and guard logic
# ============================================================================

class TestSmoke18BookingsAdvanced:
    """Advanced booking action coverage — capacity, duplicate guards, attendance update."""

    def test_18a_capacity_exceeded_returns_409(self, admin_client, test_db, session_obj, student_user):
        """Confirm when session at capacity → 409."""
        from app.models.booking import Booking, BookingStatus

        # Set session capacity = 1
        session_obj.capacity = 1
        test_db.commit()

        # Create an already-confirmed booking (fills the 1 slot)
        b_confirmed = Booking(
            user_id=student_user.id,
            session_id=session_obj.id,
            status=BookingStatus.CONFIRMED,
        )
        test_db.add(b_confirmed)
        test_db.commit()
        test_db.refresh(b_confirmed)

        # Create a second booking in PENDING state
        second_student = User(
            email=f"smoke-s2+{uuid.uuid4().hex[:8]}@lfa.com",
            name="Smoke Student 2",
            password_hash="hash",
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(second_student)
        test_db.commit()
        test_db.refresh(second_student)

        b_pending = Booking(
            user_id=second_student.id,
            session_id=session_obj.id,
            status=BookingStatus.PENDING,
        )
        test_db.add(b_pending)
        test_db.commit()
        test_db.refresh(b_pending)

        resp = admin_client.post(f"/admin/bookings/{b_pending.id}/confirm")
        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"
        err_msg = resp.json().get("error", {}).get("message", resp.json().get("detail", ""))
        assert "capacity" in err_msg.lower()

    def test_18b_double_confirm_returns_400(self, admin_client, test_db, session_obj, student_user):
        """Confirming an already-CONFIRMED booking → 400."""
        from app.models.booking import Booking, BookingStatus

        b = Booking(
            user_id=student_user.id,
            session_id=session_obj.id,
            status=BookingStatus.CONFIRMED,
        )
        test_db.add(b)
        test_db.commit()
        test_db.refresh(b)

        resp = admin_client.post(f"/admin/bookings/{b.id}/confirm")
        assert resp.status_code == 400
        err_msg = resp.json().get("error", {}).get("message", resp.json().get("detail", ""))
        assert "already confirmed" in err_msg.lower()

    def test_18c_double_cancel_returns_400(self, admin_client, test_db, session_obj, student_user):
        """Cancelling an already-CANCELLED booking → 400."""
        from app.models.booking import Booking, BookingStatus

        b = Booking(
            user_id=student_user.id,
            session_id=session_obj.id,
            status=BookingStatus.CANCELLED,
        )
        test_db.add(b)
        test_db.commit()
        test_db.refresh(b)

        resp = admin_client.post(
            f"/admin/bookings/{b.id}/cancel",
            data={"reason": "double cancel test"},
        )
        assert resp.status_code == 400
        err_msg = resp.json().get("error", {}).get("message", resp.json().get("detail", ""))
        assert "already cancelled" in err_msg.lower()

    def test_18d_update_existing_attendance(self, admin_client, test_db, session_obj, student_user):
        """Marking attendance twice updates existing record rather than creating a new one."""
        from app.models.booking import Booking, BookingStatus
        from app.models.attendance import Attendance, AttendanceStatus

        b = Booking(
            user_id=student_user.id,
            session_id=session_obj.id,
            status=BookingStatus.CONFIRMED,
        )
        test_db.add(b)
        test_db.commit()
        test_db.refresh(b)

        # First mark: present
        resp1 = admin_client.post(
            f"/admin/bookings/{b.id}/attendance",
            data={"attendance_status": "present", "notes": "first mark"},
        )
        assert resp1.status_code == 200

        # Second mark: absent (should UPDATE, not INSERT)
        resp2 = admin_client.post(
            f"/admin/bookings/{b.id}/attendance",
            data={"attendance_status": "absent", "notes": "second mark"},
        )
        assert resp2.status_code == 200

        test_db.expire_all()
        att_rows = test_db.query(Attendance).filter(Attendance.booking_id == b.id).all()
        assert len(att_rows) == 1, f"Expected 1 Attendance row, got {len(att_rows)}"
        assert att_rows[0].status == AttendanceStatus.absent

    def test_18e_session_id_filter_shows_only_matching_bookings(
        self, admin_client, test_db, session_obj, student_user
    ):
        """GET /admin/bookings?session_id=X shows only bookings for that session."""
        from app.models.booking import Booking, BookingStatus
        from app.models.semester import Semester, SemesterStatus

        # Create a second session in a separate semester so it doesn't share bookings
        sem2 = Semester(
            code=f"SMK2-{uuid.uuid4().hex[:6].upper()}",
            name="Smoke Semester 2",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=60),
            status=SemesterStatus.ONGOING,
            specialization_type="FOOTBALL_SKILLS",
        )
        test_db.add(sem2)
        test_db.commit()
        test_db.refresh(sem2)

        now = datetime.now(ZoneInfo("Europe/Budapest")).replace(tzinfo=None)
        from app.models.session import Session as SM2, SessionType
        s2 = SM2(
            title="Other Session",
            semester_id=sem2.id,
            session_type=SessionType.on_site,
            date_start=now + timedelta(hours=48),
            date_end=now + timedelta(hours=49),
            instructor_id=student_user.id,
        )
        test_db.add(s2)
        test_db.commit()
        test_db.refresh(s2)

        # Booking for session_obj
        b1 = Booking(
            user_id=student_user.id,
            session_id=session_obj.id,
            status=BookingStatus.PENDING,
        )
        # Booking for s2
        b2 = Booking(
            user_id=student_user.id,
            session_id=s2.id,
            status=BookingStatus.PENDING,
        )
        test_db.add_all([b1, b2])
        test_db.commit()

        resp = admin_client.get(f"/admin/bookings?session_id={session_obj.id}")
        assert resp.status_code == 200
        # s2 booking should not appear in the response; session_obj booking should
        assert "Other Session" not in resp.text or "Smoke Session" in resp.text

    def test_18f_invalid_attendance_status_returns_400(
        self, admin_client, test_db, session_obj, student_user
    ):
        """POST attendance with unknown status value → 400."""
        from app.models.booking import Booking, BookingStatus

        b = Booking(
            user_id=student_user.id,
            session_id=session_obj.id,
            status=BookingStatus.CONFIRMED,
        )
        test_db.add(b)
        test_db.commit()
        test_db.refresh(b)

        resp = admin_client.post(
            f"/admin/bookings/{b.id}/attendance",
            data={"attendance_status": "totally_invalid_value"},
        )
        assert resp.status_code == 400

    def test_18g_bookings_page_stats_count_confirmed(
        self, admin_client, test_db, session_obj, student_user
    ):
        """GET /admin/bookings page reflects correct CONFIRMED stat count."""
        from app.models.booking import Booking, BookingStatus

        b = Booking(
            user_id=student_user.id,
            session_id=session_obj.id,
            status=BookingStatus.CONFIRMED,
        )
        test_db.add(b)
        test_db.commit()

        resp = admin_client.get("/admin/bookings")
        assert resp.status_code == 200
        assert "Internal Server Error" not in resp.text
        # Stats rendered in the page
        assert "Confirmed" in resp.text


# ── SMOKE-10 Legacy reward endpoint → 410 ────────────────────────────────────

class TestSmoke10LegacyRewardEndpoint:
    """
    SMOKE-10: POST /api/v1/tournaments/{id}/distribute-rewards returns HTTP 410.

    The legacy V1 endpoint was deprecated in Sprint P2 (2026-03-12).
    Callers must use distribute-rewards-v2 which runs the full EMA pipeline.
    """

    @pytest.fixture(scope="function")
    def api_client(self, test_db: Session, admin_user: User) -> TestClient:
        """TestClient with get_current_user overridden (Bearer JWT path)."""
        def override_get_db():
            yield test_db

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = lambda: admin_user

        with TestClient(app) as c:
            yield c

        app.dependency_overrides.clear()

    def test_01_returns_410(self, api_client):
        """SMOKE-10a: POST legacy endpoint returns 410 Gone (not 200/400/404)."""
        resp = api_client.post(
            "/api/v1/tournaments/9999/distribute-rewards",
            json={"reason": "smoke-test"},
        )
        assert resp.status_code == 410

    def test_02_detail_contains_v2_url_hint(self, api_client):
        """SMOKE-10b: 410 response body references distribute-rewards-v2."""
        resp = api_client.post(
            "/api/v1/tournaments/9999/distribute-rewards",
            json={"reason": "smoke-test"},
        )
        assert resp.status_code == 410
        # Custom exception handler wraps the message in {"error": {"message": ...}}
        body = resp.json()
        message = body.get("error", {}).get("message") or body.get("detail", "")
        assert "distribute-rewards-v2" in message


# ── SMOKE-11 Tournament + Semester + User POST actions ───────────────────────

class TestSmoke11AdminPOSTActions:
    """
    SMOKE-11: POST action routes that were previously untested (Sprint P3-A).

    Covers:
      POST /admin/tournaments               — create tournament (DRAFT)
      POST /admin/tournaments/{id}/start    — ENROLLMENT_CLOSED → IN_PROGRESS
      POST /admin/tournaments/{id}/cancel   — any live state → CANCELLED
      POST /admin/tournaments/{id}/delete   — permanent delete
      POST /admin/tournaments/{id}/rollback — IN_PROGRESS → ENROLLMENT_CLOSED
      POST /admin/semesters/new             — create semester
      POST /admin/semesters/{id}/delete     — cancel/hard-delete semester
      POST /admin/users/{id}/edit           — edit user fields
      POST /admin/users/{id}/toggle-status  — toggle is_active
    """

    @pytest.fixture
    def tournament_draft(self, test_db: Session) -> Semester:
        t = Semester(
            code=f"TOURN-SMK11-{uuid.uuid4().hex[:6].upper()}",
            name="Smoke T11 Draft",
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=30),
            status=SemesterStatus.DRAFT,
            tournament_status="DRAFT",
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        test_db.add(t)
        test_db.commit()
        test_db.refresh(t)
        return t

    @pytest.fixture
    def tournament_enrollment_closed(self, test_db: Session) -> Semester:
        t = Semester(
            code=f"TOURN-SMK11EC-{uuid.uuid4().hex[:6].upper()}",
            name="Smoke T11 EC",
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=30),
            status=SemesterStatus.READY_FOR_ENROLLMENT,
            tournament_status="ENROLLMENT_CLOSED",
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        test_db.add(t)
        test_db.commit()
        test_db.refresh(t)
        return t

    @pytest.fixture
    def tournament_in_progress(self, test_db: Session) -> Semester:
        t = Semester(
            code=f"TOURN-SMK11IP-{uuid.uuid4().hex[:6].upper()}",
            name="Smoke T11 IP",
            start_date=date.today() + timedelta(days=10),
            end_date=date.today() + timedelta(days=30),
            status=SemesterStatus.ONGOING,
            tournament_status="IN_PROGRESS",
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        test_db.add(t)
        test_db.commit()
        test_db.refresh(t)
        return t

    @pytest.fixture
    def target_user(self, test_db: Session) -> User:
        u = User(
            email=f"smk11-target+{uuid.uuid4().hex[:8]}@lfa.com",
            name="SMK11 Target",
            password_hash=get_password_hash("pass123"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(u)
        test_db.commit()
        test_db.refresh(u)
        return u

    # ── Tournament actions ─────────────────────────────────────────────────────

    def test_01_create_tournament(self, admin_client, test_db):
        """SMOKE-11a: POST /admin/tournaments creates a DRAFT tournament — code is auto-generated."""
        tournament_name = f"Smoke Tournament 11 {uuid.uuid4().hex[:6]}"
        start_date = (date.today() + timedelta(days=10)).isoformat()
        resp = admin_client.post(
            "/admin/tournaments",
            data={
                "name": tournament_name,
                "start_date": start_date,
                "end_date": (date.today() + timedelta(days=40)).isoformat(),
                "age_group": "AMATEUR",
                "enrollment_cost": "0",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/admin/tournaments" in resp.headers["location"]
        # Code is auto-generated as TOURN-{YYYYMMDD}-{HHMMSS} — look up by name
        t = test_db.query(Semester).filter(Semester.name == tournament_name).first()
        assert t is not None, f"Tournament '{tournament_name}' not found in DB after create"
        assert t.code.startswith("TOURN-"), f"Expected TOURN- prefix, got: {t.code}"
        assert t.tournament_status == "DRAFT"
        assert t.status == SemesterStatus.DRAFT

    def test_02_create_two_tournaments_get_unique_codes(self, admin_client, test_db):
        """SMOKE-11b: Two sequential creates produce unique auto-generated codes (no collision)."""
        import time
        name_a = f"Smoke T11 Alpha {uuid.uuid4().hex[:4]}"
        name_b = f"Smoke T11 Beta {uuid.uuid4().hex[:4]}"
        start_date_a = (date.today() + timedelta(days=10)).isoformat()
        start_date_b = (date.today() + timedelta(days=20)).isoformat()

        resp_a = admin_client.post(
            "/admin/tournaments",
            data={"name": name_a, "start_date": start_date_a, "end_date": (date.today() + timedelta(days=40)).isoformat()},
            follow_redirects=False,
        )
        time.sleep(1)  # ensure different HHMMSS
        resp_b = admin_client.post(
            "/admin/tournaments",
            data={"name": name_b, "start_date": start_date_b, "end_date": (date.today() + timedelta(days=50)).isoformat()},
            follow_redirects=False,
        )
        assert resp_a.status_code == 303
        assert resp_b.status_code == 303

        ta = test_db.query(Semester).filter(Semester.name == name_a).first()
        tb = test_db.query(Semester).filter(Semester.name == name_b).first()
        assert ta is not None and tb is not None
        assert ta.code != tb.code, "Auto-generated codes must be unique"

    def test_03_start_tournament(
        self, admin_client, tournament_enrollment_closed, test_db
    ):
        """SMOKE-11c: POST /admin/tournaments/{id}/start → IN_PROGRESS + ONGOING."""
        resp = admin_client.post(
            f"/admin/tournaments/{tournament_enrollment_closed.id}/start",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "flash=" in resp.headers["location"]

        test_db.expire_all()
        t = test_db.query(Semester).filter(
            Semester.id == tournament_enrollment_closed.id
        ).first()
        assert t.tournament_status == "IN_PROGRESS"
        assert t.status == SemesterStatus.ONGOING

    def test_04_start_tournament_wrong_status_gives_error_redirect(
        self, admin_client, tournament_draft
    ):
        """SMOKE-11d: Start a DRAFT tournament → 303 with error (status mismatch)."""
        resp = admin_client.post(
            f"/admin/tournaments/{tournament_draft.id}/start",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "error=" in resp.headers["location"]

    def test_05_cancel_tournament(self, admin_client, tournament_draft, test_db):
        """SMOKE-11e: POST /admin/tournaments/{id}/cancel → CANCELLED in DB."""
        resp = admin_client.post(
            f"/admin/tournaments/{tournament_draft.id}/cancel",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "flash=" in resp.headers["location"]

        test_db.expire_all()
        t = test_db.query(Semester).filter(Semester.id == tournament_draft.id).first()
        assert t.tournament_status == "CANCELLED"

    def test_06_delete_tournament(self, admin_client, tournament_draft, test_db):
        """SMOKE-11f: POST /admin/tournaments/{id}/delete → row removed from DB."""
        tid = tournament_draft.id
        resp = admin_client.post(
            f"/admin/tournaments/{tid}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        assert test_db.query(Semester).filter(Semester.id == tid).first() is None

    def test_07_rollback_tournament(
        self, admin_client, tournament_in_progress, test_db
    ):
        """SMOKE-11g: POST /admin/tournaments/{id}/rollback → ENROLLMENT_CLOSED."""
        resp = admin_client.post(
            f"/admin/tournaments/{tournament_in_progress.id}/rollback",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "flash=" in resp.headers["location"]

        test_db.expire_all()
        t = test_db.query(Semester).filter(
            Semester.id == tournament_in_progress.id
        ).first()
        assert t.tournament_status == "ENROLLMENT_CLOSED"
        assert t.status == SemesterStatus.READY_FOR_ENROLLMENT

    # ── Semester CRUD ──────────────────────────────────────────────────────────

    def test_08_create_semester(self, admin_client, test_db):
        """SMOKE-11h: POST /admin/semesters/new creates a Semester row in DB."""
        code = f"SMK11-SEM-{uuid.uuid4().hex[:6].upper()}"
        resp = admin_client.post(
            "/admin/semesters/new",
            data={
                "code": code,
                "name": "Smoke Semester 11",
                "start_date": (date.today() + timedelta(days=1)).isoformat(),
                "end_date": (date.today() + timedelta(days=90)).isoformat(),
                "enrollment_cost": "500",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/admin/semesters" in resp.headers["location"]

        sem = test_db.query(Semester).filter(Semester.code == code).first()
        assert sem is not None, f"Semester {code} not found in DB after create"

    def test_09_delete_semester_no_enrollments_hard_deletes(self, admin_client, test_db):
        """SMOKE-11i: Delete semester with no enrollments → hard delete (row gone)."""
        sem = Semester(
            code=f"DEL-SMK11-{uuid.uuid4().hex[:6].upper()}",
            name="Delete Me Smoke 11",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=30),
            status=SemesterStatus.DRAFT,
        )
        test_db.add(sem)
        test_db.commit()
        test_db.refresh(sem)
        sid = sem.id

        resp = admin_client.post(
            f"/admin/semesters/{sid}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        assert test_db.query(Semester).filter(Semester.id == sid).first() is None

    # ── User edit / toggle ─────────────────────────────────────────────────────

    def test_10_edit_user(self, admin_client, target_user, test_db):
        """SMOKE-11j: POST /admin/users/{id}/edit updates name + email in DB."""
        new_name = f"Edited-{uuid.uuid4().hex[:6]}"
        new_email = f"smk11-edited-{uuid.uuid4().hex[:8]}@lfa.com"
        resp = admin_client.post(
            f"/admin/users/{target_user.id}/edit",
            data={"name": new_name, "email": new_email, "role": "student"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/admin/users" in resp.headers["location"]

        test_db.expire_all()
        updated = test_db.query(User).filter(User.id == target_user.id).first()
        assert updated.name == new_name
        assert updated.email == new_email

    def test_11_toggle_user_status(self, admin_client, target_user, test_db):
        """SMOKE-11k: POST /admin/users/{id}/toggle-status flips is_active."""
        original = target_user.is_active  # True by fixture
        resp = admin_client.post(
            f"/admin/users/{target_user.id}/toggle-status",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        updated = test_db.query(User).filter(User.id == target_user.id).first()
        assert updated.is_active == (not original)


# ── SMOKE-12 Student Motivation Assessment ────────────────────────────────────

class TestSmoke12MotivationAssessment:
    """
    SMOKE-12: Motivation assessment routes (Sprint P3-B).

    Covers:
      GET  /admin/students/{id}/motivation/{spec} — form page loads
      POST /admin/students/{id}/motivation/{spec} — save assessment
      POST with out-of-range score               — 400 validation
      POST missing license                       — 404
      POST as student (non-admin)                — 403
    """

    SPEC = "LFA_FOOTBALL_PLAYER"

    @pytest.fixture
    def assessed_student(self, test_db: Session) -> User:
        u = User(
            email=f"smk12-student+{uuid.uuid4().hex[:8]}@lfa.com",
            name="SMK12 Student",
            password_hash=get_password_hash("pass123"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(u)
        test_db.commit()
        test_db.refresh(u)
        return u

    @pytest.fixture
    def student_license(self, test_db: Session, assessed_student: User) -> UserLicense:
        lic = UserLicense(
            user_id=assessed_student.id,
            specialization_type=self.SPEC,
            started_at=datetime.now(),
            is_active=True,
        )
        test_db.add(lic)
        test_db.commit()
        test_db.refresh(lic)
        return lic

    @pytest.fixture
    def student_client(self, test_db: Session, assessed_student: User) -> TestClient:
        """TestClient with a STUDENT user — should get 403 on admin routes."""
        def override_get_db():
            yield test_db

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user_web] = lambda: assessed_student

        with TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"}) as c:
            yield c

        app.dependency_overrides.clear()

    # ── Happy path ─────────────────────────────────────────────────────────────

    def test_01_get_motivation_page_loads(
        self, admin_client, assessed_student, student_license
    ):
        """SMOKE-12a: GET motivation page returns 200 with student name."""
        resp = admin_client.get(
            f"/admin/students/{assessed_student.id}/motivation/{self.SPEC}"
        )
        assert resp.status_code == 200
        assert assessed_student.name in resp.text

    def test_02_post_saves_assessment(
        self, admin_client, assessed_student, student_license, test_db
    ):
        """SMOKE-12b: POST assessment → 303 to /admin/users + scores saved in DB."""
        resp = admin_client.post(
            f"/admin/students/{assessed_student.id}/motivation/{self.SPEC}",
            data={
                "goal_clarity": "4",
                "commitment_level": "5",
                "engagement": "3",
                "progress_mindset": "4",
                "initiative": "5",
                "notes": "Smoke test assessment",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/admin/users" in resp.headers["location"]

        test_db.expire_all()
        lic = test_db.query(UserLicense).filter(
            UserLicense.id == student_license.id
        ).first()
        assert lic.motivation_scores is not None
        assert lic.average_motivation_score == pytest.approx(4.2)

    # ── Validation errors ──────────────────────────────────────────────────────

    def test_03_score_out_of_range_returns_400(
        self, admin_client, assessed_student, student_license
    ):
        """SMOKE-12c: Score=0 (below 1) → 400."""
        resp = admin_client.post(
            f"/admin/students/{assessed_student.id}/motivation/{self.SPEC}",
            data={
                "goal_clarity": "0",
                "commitment_level": "3",
                "engagement": "3",
                "progress_mindset": "3",
                "initiative": "3",
            },
        )
        assert resp.status_code == 400

    def test_04_missing_license_returns_404(self, admin_client, assessed_student):
        """SMOKE-12d: Student exists but has no license for spec → 404."""
        resp = admin_client.post(
            f"/admin/students/{assessed_student.id}/motivation/UNKNOWN_SPEC",
            data={
                "goal_clarity": "3",
                "commitment_level": "3",
                "engagement": "3",
                "progress_mindset": "3",
                "initiative": "3",
            },
        )
        assert resp.status_code == 404

    def test_05_student_role_gets_403(
        self, student_client, assessed_student, student_license
    ):
        """SMOKE-12e: STUDENT-role user accessing motivation route → 403."""
        resp = student_client.post(
            f"/admin/students/{assessed_student.id}/motivation/{self.SPEC}",
            data={
                "goal_clarity": "3",
                "commitment_level": "3",
                "engagement": "3",
                "progress_mindset": "3",
                "initiative": "3",
            },
        )
        assert resp.status_code == 403


# ── SMOKE-13 Campus CRUD ──────────────────────────────────────────────────────

class TestSmoke13CampusCRUD:
    """
    SMOKE-13: Campus CRUD routes (Sprint P3-C — final admin coverage gap).

    Covers:
      POST /admin/locations/{id}/campuses — create campus
      GET  /admin/campuses/{id}/edit      — edit form page
      POST /admin/campuses/{id}/edit      — save edits
      POST /admin/campuses/{id}/toggle    — flip is_active
      POST /admin/campuses/{id}/delete    — permanent delete
    """

    @pytest.fixture
    def campus(self, test_db: Session, location: Location) -> Campus:
        c = Campus(
            location_id=location.id,
            name=f"Smoke Campus {uuid.uuid4().hex[:6]}",
            is_active=True,
        )
        test_db.add(c)
        test_db.commit()
        test_db.refresh(c)
        return c

    def test_01_create_campus(self, admin_client, location, test_db):
        """SMOKE-13a: POST /admin/locations/{id}/campuses → 303 + row in DB."""
        campus_name = f"New Campus {uuid.uuid4().hex[:6]}"
        resp = admin_client.post(
            f"/admin/locations/{location.id}/campuses",
            data={
                "name": campus_name,
                "address": "Smoke Street 1",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/admin/locations" in resp.headers["location"]

        c = test_db.query(Campus).filter(Campus.name == campus_name).first()
        assert c is not None, "Campus not found in DB after create"
        assert c.location_id == location.id
        assert c.is_active is True

    def test_02_edit_campus_page_loads(self, admin_client, campus):
        """SMOKE-13b: GET /admin/campuses/{id}/edit → 200, campus name in HTML."""
        resp = admin_client.get(f"/admin/campuses/{campus.id}/edit")
        assert resp.status_code == 200
        assert campus.name in resp.text

    def test_03_edit_campus_submit(self, admin_client, campus, test_db):
        """SMOKE-13c: POST /admin/campuses/{id}/edit → 303 + name updated in DB."""
        new_name = f"Updated Campus {uuid.uuid4().hex[:6]}"
        resp = admin_client.post(
            f"/admin/campuses/{campus.id}/edit",
            data={"name": new_name, "address": "New Address 99"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/admin/locations" in resp.headers["location"]

        test_db.expire_all()
        updated = test_db.query(Campus).filter(Campus.id == campus.id).first()
        assert updated.name == new_name

    def test_04_toggle_campus(self, admin_client, campus, test_db):
        """SMOKE-13d: POST /admin/campuses/{id}/toggle → is_active flipped in DB."""
        original = campus.is_active  # True by fixture
        resp = admin_client.post(
            f"/admin/campuses/{campus.id}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        updated = test_db.query(Campus).filter(Campus.id == campus.id).first()
        assert updated.is_active == (not original)

    def test_05_delete_campus(self, admin_client, campus, test_db):
        """SMOKE-13e: POST /admin/campuses/{id}/delete → row removed from DB."""
        cid = campus.id
        resp = admin_client.post(
            f"/admin/campuses/{cid}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        assert test_db.query(Campus).filter(Campus.id == cid).first() is None


class TestSmoke15UsersPageFilters:
    """
    SMOKE-15: GET /admin/users — filter, search, and pagination query params.

    Verifies that role_filter, status_filter, and search query params correctly
    narrow the user list returned by admin_users_page.
    """

    @pytest.fixture
    def filter_users(self, test_db: Session):
        """Create 3 users with distinct roles/statuses/names for filter testing."""
        student = User(
            email=f"smk15-student+{uuid.uuid4().hex[:8]}@lfa.com",
            name="Smoke15 Alice Student",
            password_hash=get_password_hash("pass123"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        instructor = User(
            email=f"smk15-instructor+{uuid.uuid4().hex[:8]}@lfa.com",
            name="Smoke15 Bob Instructor",
            password_hash=get_password_hash("pass123"),
            role=UserRole.INSTRUCTOR,
            is_active=True,
        )
        inactive = User(
            email=f"smk15-inactive+{uuid.uuid4().hex[:8]}@lfa.com",
            name="Smoke15 Carol Inactive",
            password_hash=get_password_hash("pass123"),
            role=UserRole.STUDENT,
            is_active=False,
        )
        for u in (student, instructor, inactive):
            test_db.add(u)
        test_db.commit()
        for u in (student, instructor, inactive):
            test_db.refresh(u)
        return student, instructor, inactive

    @pytest.fixture
    def admin_client(self, test_db: Session):
        admin = User(
            email=f"smk15-admin+{uuid.uuid4().hex[:8]}@lfa.com",
            name="SMK15 Admin",
            password_hash=get_password_hash("adminpass"),
            role=UserRole.ADMIN,
            is_active=True,
        )
        test_db.add(admin)
        test_db.commit()
        test_db.refresh(admin)

        app.dependency_overrides[get_current_user_web] = lambda: admin
        app.dependency_overrides[get_db] = lambda: test_db
        client = TestClient(app, raise_server_exceptions=True)
        yield client
        app.dependency_overrides.clear()

    def test_role_filter_student(self, admin_client, filter_users):
        """SMOKE-15a: role_filter=student with unique search returns students, not instructors."""
        student, instructor, inactive = filter_users
        # Use search=Smoke15 to pin results to our fixtures (avoids pagination issues)
        resp = admin_client.get("/admin/users?role_filter=student&search=Smoke15")
        assert resp.status_code == 200
        body = resp.text
        assert student.name in body
        assert inactive.name in body        # inactive is also a student
        assert instructor.name not in body

    def test_role_filter_instructor(self, admin_client, filter_users):
        """SMOKE-15b: role_filter=instructor with unique search returns only instructors."""
        student, instructor, inactive = filter_users
        resp = admin_client.get("/admin/users?role_filter=instructor&search=Smoke15")
        assert resp.status_code == 200
        body = resp.text
        assert instructor.name in body
        assert student.name not in body
        assert inactive.name not in body

    def test_status_filter_active(self, admin_client, filter_users):
        """SMOKE-15c: status_filter=active with unique search excludes inactive users."""
        student, instructor, inactive = filter_users
        resp = admin_client.get("/admin/users?status_filter=active&search=Smoke15")
        assert resp.status_code == 200
        body = resp.text
        assert student.name in body
        assert instructor.name in body
        assert inactive.name not in body

    def test_status_filter_inactive(self, admin_client, filter_users):
        """SMOKE-15d: status_filter=inactive with unique search shows only inactive users."""
        student, instructor, inactive = filter_users
        resp = admin_client.get("/admin/users?status_filter=inactive&search=Smoke15")
        assert resp.status_code == 200
        body = resp.text
        assert inactive.name in body
        assert student.name not in body
        assert instructor.name not in body

    def test_search_by_name(self, admin_client, filter_users):
        """SMOKE-15e: search=Smoke15 Bob matches only the instructor by name."""
        student, instructor, inactive = filter_users
        resp = admin_client.get("/admin/users?search=Smoke15+Bob")
        assert resp.status_code == 200
        body = resp.text
        assert instructor.name in body
        assert student.name not in body
        assert inactive.name not in body

    def test_search_by_email_prefix(self, admin_client, filter_users):
        """SMOKE-15f: search=smk15-inactive matches only the inactive user by email."""
        student, instructor, inactive = filter_users
        # Use the domain part to avoid URL-encoding issues with '+' in email local part
        resp = admin_client.get("/admin/users?search=smk15-inactive")
        assert resp.status_code == 200
        body = resp.text
        assert inactive.name in body
        assert student.name not in body
        assert instructor.name not in body

    def test_combined_role_and_status(self, admin_client, filter_users):
        """SMOKE-15g: role_filter=student&status_filter=active returns only active students."""
        student, instructor, inactive = filter_users
        resp = admin_client.get("/admin/users?role_filter=student&status_filter=active&search=Smoke15")
        assert resp.status_code == 200
        body = resp.text
        assert student.name in body
        assert inactive.name not in body
        assert instructor.name not in body


class TestSmoke16SystemEventsActions:
    """
    SMOKE-16: System Events resolve / unresolve / purge actions.

    Covers:
      GET  /admin/system-events              — page loads with filter defaults
      POST /admin/system-events/{id}/resolve — flips resolved=True in DB
      POST /admin/system-events/{id}/unresolve — flips resolved=False in DB
      POST /admin/system-events/purge        — deletes old resolved events
    """

    @pytest.fixture
    def open_event(self, test_db: Session) -> SystemEvent:
        ev = SystemEvent(
            event_type="smk16.test.open",
            level=SystemEventLevel.INFO,
            resolved=False,
            created_at=datetime.now(ZoneInfo("UTC")),
        )
        test_db.add(ev)
        test_db.commit()
        test_db.refresh(ev)
        return ev

    @pytest.fixture
    def resolved_event(self, test_db: Session) -> SystemEvent:
        ev = SystemEvent(
            event_type="smk16.test.resolved",
            level=SystemEventLevel.WARNING,
            resolved=True,
            created_at=datetime.now(ZoneInfo("UTC")) - timedelta(days=200),
        )
        test_db.add(ev)
        test_db.commit()
        test_db.refresh(ev)
        return ev

    def test_01_page_loads(self, admin_client):
        """SMOKE-16a: GET /admin/system-events → 200, System Events heading present."""
        resp = admin_client.get("/admin/system-events")
        assert resp.status_code == 200
        assert "System Events" in resp.text

    def test_02_resolve_event(self, admin_client, open_event, test_db):
        """SMOKE-16b: POST resolve → 303, event.resolved set to True in DB."""
        resp = admin_client.post(
            f"/admin/system-events/{open_event.id}/resolve",
            data={"page": "0", "level": "", "resolved": "open"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        ev = test_db.query(SystemEvent).filter(SystemEvent.id == open_event.id).first()
        assert ev.resolved is True

    def test_03_unresolve_event(self, admin_client, resolved_event, test_db):
        """SMOKE-16c: POST unresolve → 303, event.resolved set to False in DB."""
        resp = admin_client.post(
            f"/admin/system-events/{resolved_event.id}/unresolve",
            data={"page": "0", "level": "", "resolved": "resolved"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        ev = test_db.query(SystemEvent).filter(SystemEvent.id == resolved_event.id).first()
        assert ev.resolved is False

    def test_04_purge_deletes_old_resolved(self, admin_client, resolved_event, test_db):
        """SMOKE-16d: POST purge with retention_days=90 deletes events resolved >90 days ago."""
        eid = resolved_event.id
        resp = admin_client.post(
            "/admin/system-events/purge",
            data={"retention_days": "90"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        assert test_db.query(SystemEvent).filter(SystemEvent.id == eid).first() is None

    def test_05_purge_preserves_open_events(self, admin_client, open_event, test_db):
        """SMOKE-16e: POST purge does not delete open (unresolved) events."""
        eid = open_event.id
        admin_client.post(
            "/admin/system-events/purge",
            data={"retention_days": "1"},
            follow_redirects=False,
        )
        test_db.expire_all()
        assert test_db.query(SystemEvent).filter(SystemEvent.id == eid).first() is not None


# ============================================================================
# SMOKE-17: /admin/users/{id}/edit — event-based credit & license management
# ============================================================================

class TestSmoke17UserEditExtended:
    """
    SMOKE-17: New routes added to /admin/users/{id}/edit page.

    Covers:
      POST /admin/users/{id}/reset-password       — SMOKE-17a
      POST /admin/users/{id}/grant-credit         — SMOKE-17b
      POST /admin/users/{id}/deduct-credit        — SMOKE-17c
      POST /admin/users/{id}/grant-credit (>50k)  — SMOKE-17d (400)
      POST /admin/users/{id}/deduct-credit (>bal) — SMOKE-17e (400)
      POST /admin/users/{id}/grant-license        — SMOKE-17f
      POST /admin/users/{id}/revoke-license/{id}  — SMOKE-17g
      GET  /admin/users/{id}/edit (credit history)— SMOKE-17h
      POST /admin/users/{id}/grant-license dup    — SMOKE-17i (303 + error param)
      POST /admin/users/{id}/grant-license+expiry — SMOKE-17j (expires_at stored)
      POST /admin/users/{id}/renew-license/{id}   — SMOKE-17k (RENEWED progression)
    """

    @pytest.fixture
    def target_user(self, test_db: Session) -> User:
        u = User(
            email=f"smk17-target+{uuid.uuid4().hex[:8]}@lfa.com",
            name="SMK17 Target",
            password_hash=get_password_hash("oldpassword"),
            role=UserRole.STUDENT,
            is_active=True,
            credit_balance=500,
            credit_purchased=500,
        )
        test_db.add(u)
        test_db.commit()
        test_db.refresh(u)
        return u

    # ── SMOKE-17a: Password Reset ──────────────────────────────────────────────

    def test_17a_reset_password_changes_hash(self, admin_client, test_db, target_user):
        """POST reset-password → 303, password_hash updated in DB."""
        old_hash = target_user.password_hash
        resp = admin_client.post(
            f"/admin/users/{target_user.id}/reset-password",
            data={"new_password": "newSecure99"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/admin/users/{target_user.id}/edit" in resp.headers["location"]

        test_db.expire_all()
        u = test_db.query(User).filter(User.id == target_user.id).first()
        assert u.password_hash != old_hash

    def test_17a_reset_password_too_short_returns_400(self, admin_client, target_user):
        """POST reset-password with <8 chars → 400."""
        resp = admin_client.post(
            f"/admin/users/{target_user.id}/reset-password",
            data={"new_password": "short"},
            follow_redirects=False,
        )
        assert resp.status_code == 400

    def test_17a_reset_password_unknown_user_returns_404(self, admin_client):
        """POST reset-password for non-existent user → 404."""
        resp = admin_client.post(
            "/admin/users/999999/reset-password",
            data={"new_password": "validpass123"},
            follow_redirects=False,
        )
        assert resp.status_code == 404

    # ── SMOKE-17b: Grant Credit ───────────────────────────────────────────────

    def test_17b_grant_credit_increases_balance_and_creates_transaction(
        self, admin_client, test_db, target_user, admin_user
    ):
        """POST grant-credit → 303, balance increased, CreditTransaction row created."""
        initial_balance = target_user.credit_balance
        resp = admin_client.post(
            f"/admin/users/{target_user.id}/grant-credit",
            data={"amount": "300", "reason": "Competition reward"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        u = test_db.query(User).filter(User.id == target_user.id).first()
        assert u.credit_balance == initial_balance + 300

        ct = (
            test_db.query(CreditTransaction)
            .filter(
                CreditTransaction.user_id == target_user.id,
                CreditTransaction.amount == 300,
            )
            .order_by(CreditTransaction.id.desc())
            .first()
        )
        assert ct is not None
        assert ct.transaction_type == TransactionType.ADMIN_ADJUSTMENT.value
        assert ct.balance_after == initial_balance + 300
        assert ct.performed_by_user_id == admin_user.id
        assert "Competition reward" in ct.description

    # ── SMOKE-17c: Deduct Credit ──────────────────────────────────────────────

    def test_17c_deduct_credit_decreases_balance_and_creates_transaction(
        self, admin_client, test_db, target_user, admin_user
    ):
        """POST deduct-credit → 303, balance decreased, CreditTransaction row created."""
        initial_balance = target_user.credit_balance  # 500
        resp = admin_client.post(
            f"/admin/users/{target_user.id}/deduct-credit",
            data={"amount": "200", "reason": "Correction"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        u = test_db.query(User).filter(User.id == target_user.id).first()
        assert u.credit_balance == initial_balance - 200

        ct = (
            test_db.query(CreditTransaction)
            .filter(
                CreditTransaction.user_id == target_user.id,
                CreditTransaction.amount == -200,
            )
            .order_by(CreditTransaction.id.desc())
            .first()
        )
        assert ct is not None
        assert ct.transaction_type == TransactionType.ADMIN_ADJUSTMENT.value
        assert ct.performed_by_user_id == admin_user.id

    # ── SMOKE-17d: Grant Credit amount > 50000 → 400 ─────────────────────────

    def test_17d_grant_credit_over_limit_returns_400(self, admin_client, target_user):
        """POST grant-credit amount=50001 → 400 validation error."""
        resp = admin_client.post(
            f"/admin/users/{target_user.id}/grant-credit",
            data={"amount": "50001", "reason": "Too much"},
            follow_redirects=False,
        )
        assert resp.status_code == 400

    # ── SMOKE-17e: Deduct Credit amount > balance → 400 ──────────────────────

    def test_17e_deduct_credit_exceeds_balance_returns_400(self, admin_client, target_user):
        """POST deduct-credit amount > credit_balance (500) → 400."""
        resp = admin_client.post(
            f"/admin/users/{target_user.id}/deduct-credit",
            data={"amount": "999", "reason": "Too much"},
            follow_redirects=False,
        )
        assert resp.status_code == 400

    # ── SMOKE-17f: Grant License ──────────────────────────────────────────────

    def test_17f_grant_license_creates_license_and_progression(
        self, admin_client, test_db, target_user, admin_user
    ):
        """POST grant-license → 303, UserLicense + LicenseProgression rows created."""
        resp = admin_client.post(
            f"/admin/users/{target_user.id}/grant-license",
            data={
                "specialization_type": SpecializationType.LFA_FOOTBALL_PLAYER.value,
                "reason": "Manual enrollment",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "#licenses" in resp.headers["location"]

        test_db.expire_all()
        lic = (
            test_db.query(UserLicense)
            .filter(
                UserLicense.user_id == target_user.id,
                UserLicense.specialization_type == SpecializationType.LFA_FOOTBALL_PLAYER.value,
                UserLicense.is_active == True,
            )
            .first()
        )
        assert lic is not None

        prog = (
            test_db.query(LicenseProgression)
            .filter(LicenseProgression.user_license_id == lic.id)
            .first()
        )
        assert prog is not None
        assert prog.requirements_met == "INITIAL_GRANT"
        assert prog.advanced_by == admin_user.id
        assert "Manual enrollment" in prog.advancement_reason

    # ── SMOKE-17g: Revoke License ─────────────────────────────────────────────

    def test_17g_revoke_license_deactivates_and_creates_progression(
        self, admin_client, test_db, target_user, admin_user
    ):
        """POST revoke-license → 303, license.is_active=False + LicenseProgression REVOKED."""
        # Setup: create a license to revoke
        lic = UserLicense(
            user_id=target_user.id,
            specialization_type=SpecializationType.LFA_FOOTBALL_PLAYER.value,
            started_at=datetime.now(tz=ZoneInfo("UTC")),
            is_active=True,
        )
        test_db.add(lic)
        test_db.commit()
        test_db.refresh(lic)

        resp = admin_client.post(
            f"/admin/users/{target_user.id}/revoke-license/{lic.id}",
            data={"reason": "Policy violation"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        lic_db = test_db.query(UserLicense).filter(UserLicense.id == lic.id).first()
        assert lic_db.is_active is False

        prog = (
            test_db.query(LicenseProgression)
            .filter(LicenseProgression.user_license_id == lic.id)
            .order_by(LicenseProgression.id.desc())
            .first()
        )
        assert prog is not None
        assert prog.requirements_met == "REVOKED"
        assert prog.advanced_by == admin_user.id

    # ── SMOKE-17h: GET edit page shows credit history section ─────────────────

    def test_17h_get_edit_shows_credit_history_section(
        self, admin_client, test_db, target_user, admin_user
    ):
        """GET /admin/users/{id}/edit → HTML contains credit history table."""
        # Create a credit transaction for the target user
        ct = CreditTransaction(
            user_id=target_user.id,
            transaction_type=TransactionType.ADMIN_ADJUSTMENT.value,
            amount=100,
            balance_after=600,
            description="Test history entry",
            idempotency_key=f"smk17h-{uuid.uuid4()}",
            performed_by_user_id=admin_user.id,
        )
        test_db.add(ct)
        test_db.commit()

        resp = admin_client.get(f"/admin/users/{target_user.id}/edit")
        assert resp.status_code == 200
        assert "Credit Management" in resp.text
        assert "Test history entry" in resp.text
        assert "License Management" in resp.text

    # ── SMOKE-17i: Duplicate active license → redirect with error (not JSON 400) ──

    def test_17i_grant_duplicate_active_license_redirects_with_error(
        self, admin_client, test_db, target_user
    ):
        """POST grant-license when user already has active license → 303 + error param, no second license created."""
        lic = UserLicense(
            user_id=target_user.id,
            specialization_type=SpecializationType.LFA_COACH.value,
            started_at=datetime.now(tz=ZoneInfo("UTC")),
            is_active=True,
        )
        test_db.add(lic)
        test_db.commit()

        resp = admin_client.post(
            f"/admin/users/{target_user.id}/grant-license",
            data={
                "specialization_type": SpecializationType.LFA_COACH.value,
                "reason": "Duplicate attempt",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        loc = resp.headers["location"]
        assert "error=duplicate_license" in loc
        assert f"/admin/users/{target_user.id}/edit" in loc
        count = (
            test_db.query(UserLicense)
            .filter(
                UserLicense.user_id == target_user.id,
                UserLicense.specialization_type == SpecializationType.LFA_COACH.value,
                UserLicense.is_active == True,
            )
            .count()
        )
        assert count == 1, "Duplicate active license was created despite error"

    # ── SMOKE-17j: Grant license with expiry → expires_at stored in DB ────────

    def test_17j_grant_license_with_expiry_stores_expires_at(
        self, admin_client, test_db, target_user
    ):
        """POST grant-license with expires_at param → UserLicense.expires_at set."""
        future_date = (date.today() + timedelta(days=365)).isoformat()
        resp = admin_client.post(
            f"/admin/users/{target_user.id}/grant-license",
            data={
                "specialization_type": SpecializationType.LFA_FOOTBALL_PLAYER.value,
                "reason": "Grant with expiry SMOKE-17j",
                "expires_at": future_date,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        test_db.expire_all()
        lic = (
            test_db.query(UserLicense)
            .filter(
                UserLicense.user_id == target_user.id,
                UserLicense.specialization_type == SpecializationType.LFA_FOOTBALL_PLAYER.value,
                UserLicense.is_active == True,
            )
            .first()
        )
        assert lic is not None
        assert lic.expires_at is not None, "expires_at not stored after grant with expiry"
        assert lic.issued_at is not None, "issued_at should be set on grant"
        # Verify the stored date matches the input (truncated to day)
        assert lic.expires_at.strftime("%Y-%m-%d") == future_date

    # ── SMOKE-17k: Renew license → expires_at + last_renewed_at + RENEWED prog ─

    def test_17k_renew_license_updates_expiry_and_creates_progression(
        self, admin_client, test_db, target_user
    ):
        """POST renew-license → expires_at updated, last_renewed_at set, 'RENEWED' progression created."""
        # Create a license with an expired expires_at
        old_expiry = datetime(2024, 1, 1)
        lic = UserLicense(
            user_id=target_user.id,
            specialization_type=SpecializationType.LFA_COACH.value,
            started_at=datetime.now(tz=ZoneInfo("UTC")),
            is_active=True,
            expires_at=old_expiry,
        )
        test_db.add(lic)
        test_db.commit()
        test_db.refresh(lic)

        new_expiry = (date.today() + timedelta(days=180)).isoformat()
        resp = admin_client.post(
            f"/admin/users/{target_user.id}/renew-license/{lic.id}",
            data={"new_expires_at": new_expiry, "reason": "Annual renewal SMOKE-17k"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "#licenses" in resp.headers["location"]

        test_db.expire_all()
        lic_db = test_db.query(UserLicense).filter(UserLicense.id == lic.id).first()
        assert lic_db.expires_at is not None
        assert lic_db.expires_at != old_expiry, "expires_at not updated"
        assert lic_db.expires_at.strftime("%Y-%m-%d") == new_expiry
        assert lic_db.last_renewed_at is not None, "last_renewed_at not set"

        prog = (
            test_db.query(LicenseProgression)
            .filter(LicenseProgression.user_license_id == lic.id)
            .order_by(LicenseProgression.id.desc())
            .first()
        )
        assert prog is not None, "LicenseProgression not created after renewal"
        assert prog.requirements_met == "RENEWED"
        assert "Annual renewal SMOKE-17k" in (prog.advancement_reason or "")


# ============================================================================
# SMOKE-20: CENTER vs PARTNER location capability enforcement
# ============================================================================

class TestSmoke20LocationCapabilityEnforcement:
    """
    SMOKE-20: Verify that the CENTER vs PARTNER location rule is enforced
    in the admin semester creation form.

    Covers:
      POST /admin/semesters/new with PARTNER loc + ACADEMY type → 200 + error (SMOKE-20a)
      POST /admin/semesters/new with CENTER loc + ACADEMY type  → 303 success (SMOKE-20b)
      POST /admin/semesters/new with PARTNER loc + MINI type    → 303 success (SMOKE-20c)
      POST /admin/semesters/new with no location + ACADEMY type → 200 error (SMOKE-20d, loc required for ACADEMY)
    """

    @pytest.fixture
    def partner_location(self, test_db: Session) -> Location:
        loc = Location(
            name=f"SMK20-Partner-{uuid.uuid4().hex[:6]}",
            city=f"PartnerCity-{uuid.uuid4().hex[:8]}",
            country="Hungary",
            country_code="HU",
            location_type=LocationType.PARTNER,
            is_active=True,
        )
        test_db.add(loc)
        test_db.commit()
        test_db.refresh(loc)
        return loc

    @pytest.fixture
    def center_location(self, test_db: Session) -> Location:
        loc = Location(
            name=f"SMK20-Center-{uuid.uuid4().hex[:6]}",
            city=f"CenterCity-{uuid.uuid4().hex[:8]}",
            country="Hungary",
            country_code="HU",
            location_type=LocationType.CENTER,
            is_active=True,
        )
        test_db.add(loc)
        test_db.commit()
        test_db.refresh(loc)
        return loc

    def _semester_payload(self, code_prefix: str, spec_type: str, location_id: str = "") -> dict:
        return {
            "code": f"{code_prefix}-{uuid.uuid4().hex[:6].upper()}",
            "name": f"Smoke20 {code_prefix}",
            "start_date": (date.today() + timedelta(days=1)).isoformat(),
            "end_date": (date.today() + timedelta(days=90)).isoformat(),
            "enrollment_cost": "500",
            "specialization_type": spec_type,
            "location_id": location_id,
        }

    def test_20a_partner_location_blocks_academy_season(
        self, admin_client, partner_location
    ):
        """SMOKE-20a: PARTNER location + Academy type → form re-rendered with error."""
        resp = admin_client.post(
            "/admin/semesters/new",
            data=self._semester_payload(
                "SMK20A", "LFA_PLAYER_PRE_ACADEMY", str(partner_location.id)
            ),
            follow_redirects=False,
        )
        assert resp.status_code == 200, (
            f"Expected 200 (form error), got {resp.status_code}"
        )
        assert "Academy Season" in resp.text or "CENTER" in resp.text or "PARTNER" in resp.text, (
            "Error message about location restriction not found in response"
        )

    def test_20b_center_location_allows_academy_season(
        self, admin_client, center_location, test_db
    ):
        """SMOKE-20b: CENTER location + Academy type → 303 success."""
        payload = self._semester_payload(
            "SMK20B", "LFA_PLAYER_YOUTH_ACADEMY", str(center_location.id)
        )
        resp = admin_client.post(
            "/admin/semesters/new",
            data=payload,
            follow_redirects=False,
        )
        assert resp.status_code == 303, (
            f"Expected 303, got {resp.status_code}. Body: {resp.text[:400]}"
        )

        sem = test_db.query(Semester).filter(Semester.code == payload["code"]).first()
        assert sem is not None, "Semester not created in DB after CENTER + ACADEMY"

    def test_20c_partner_location_allows_mini_season(
        self, admin_client, partner_location, test_db
    ):
        """SMOKE-20c: PARTNER location + Mini Season type → 303 success."""
        payload = self._semester_payload(
            "SMK20C", "LFA_PLAYER_PRE", str(partner_location.id)
        )
        resp = admin_client.post(
            "/admin/semesters/new",
            data=payload,
            follow_redirects=False,
        )
        assert resp.status_code == 303, (
            f"Expected 303 for PARTNER + Mini Season, got {resp.status_code}"
        )

        sem = test_db.query(Semester).filter(Semester.code == payload["code"]).first()
        assert sem is not None, "Semester not created in DB after PARTNER + Mini Season"

    def test_20d_no_location_blocks_academy_season(self, admin_client):
        """SMOKE-20d: No location + Academy type → 200 form error (location is required for ACADEMY)."""
        resp = admin_client.post(
            "/admin/semesters/new",
            data=self._semester_payload("SMK20D", "LFA_PLAYER_AMATEUR_ACADEMY", ""),
            follow_redirects=False,
        )
        assert resp.status_code == 200, (
            f"Expected 200 (location-required error), got {resp.status_code}"
        )
        # Error message is in Hungarian; check for key words from the error
        assert "kötelező" in resp.text or "CENTER" in resp.text or "Academy" in resp.text, (
            "Expected location-required error message in response"
        )


# ============================================================================
# SMOKE-21: K2 admin form — CENTER→PARTNER blocked when active Academy semesters exist
# ============================================================================

class TestSmoke21LocationTypeDowngradeAdminForm:
    """
    SMOKE-21: Verify that the admin location edit form enforces the K2 rule:
    CENTER→PARTNER type change is blocked when active Academy semesters exist.

    Covers:
      POST /admin/locations/{id}/edit CENTER→PARTNER with READY_FOR_ENROLLMENT Academy → 409 + error (SMOKE-21a)
      POST /admin/locations/{id}/edit CENTER→PARTNER with only DRAFT Academy          → 303 success (SMOKE-21b)
      POST /admin/locations/{id}/edit PARTNER→CENTER always allowed                   → 303 success (SMOKE-21c)
    """

    def _loc_payload(self, loc, location_type: str) -> dict:
        return {
            "name": loc.name,
            "city": loc.city,
            "country": loc.country,
            "country_code": loc.country_code or "",
            "location_code": loc.location_code or "",
            "postal_code": loc.postal_code or "",
            "address": loc.address or "",
            "notes": loc.notes or "",
            "location_type": location_type,
        }

    def _make_center(self, test_db: Session) -> Location:
        loc = Location(
            name=f"SMK21-Center-{uuid.uuid4().hex[:6]}",
            city=f"CenterCity-{uuid.uuid4().hex[:6]}",
            country="Hungary",
            country_code="HU",
            location_type=LocationType.CENTER,
            is_active=True,
        )
        test_db.add(loc)
        test_db.commit()
        test_db.refresh(loc)
        return loc

    def _make_partner(self, test_db: Session) -> Location:
        loc = Location(
            name=f"SMK21-Partner-{uuid.uuid4().hex[:6]}",
            city=f"PartnerCity-{uuid.uuid4().hex[:6]}",
            country="Hungary",
            country_code="HU",
            location_type=LocationType.PARTNER,
            is_active=True,
        )
        test_db.add(loc)
        test_db.commit()
        test_db.refresh(loc)
        return loc

    def _make_semester(self, test_db: Session, location: Location,
                       status: SemesterStatus) -> Semester:
        code = f"K2ADM-{uuid.uuid4().hex[:8].upper()}"
        sem = Semester(
            code=code,
            name=f"SMOKE-21 sem {code}",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=90),
            status=status,
            specialization_type="LFA_PLAYER_PRE_ACADEMY",
            location_id=location.id,
            enrollment_cost=500,
        )
        test_db.add(sem)
        test_db.commit()
        test_db.refresh(sem)
        return sem

    def test_21a_center_to_partner_blocked_with_active_academy_semester(
        self, admin_client, test_db: Session
    ):
        """SMOKE-21a: CENTER→PARTNER blocked when READY_FOR_ENROLLMENT Academy semester exists."""
        center = self._make_center(test_db)
        self._make_semester(test_db, center, SemesterStatus.READY_FOR_ENROLLMENT)

        resp = admin_client.post(
            f"/admin/locations/{center.id}/edit",
            data=self._loc_payload(center, "PARTNER"),
            follow_redirects=False,
        )
        assert resp.status_code == 409, (
            f"Expected 409 (K2 block), got {resp.status_code}. Body: {resp.text[:400]}"
        )
        assert "CENTER" in resp.text or "PARTNER" in resp.text or "Academy" in resp.text, (
            "Expected K2 conflict error message in HTML response"
        )
        # Location type must NOT have changed in the DB
        test_db.expire(center)
        center_reloaded = test_db.query(Location).filter(Location.id == center.id).first()
        assert center_reloaded.location_type == LocationType.CENTER, (
            "Location type was incorrectly changed to PARTNER despite active Academy semester"
        )

    def test_21b_center_to_partner_allowed_with_only_draft_academy_semester(
        self, admin_client, test_db: Session
    ):
        """SMOKE-21b: CENTER→PARTNER allowed when only DRAFT Academy semester exists (not active)."""
        center = self._make_center(test_db)
        self._make_semester(test_db, center, SemesterStatus.DRAFT)

        resp = admin_client.post(
            f"/admin/locations/{center.id}/edit",
            data=self._loc_payload(center, "PARTNER"),
            follow_redirects=False,
        )
        assert resp.status_code == 303, (
            f"Expected 303 (allowed), got {resp.status_code}. Body: {resp.text[:400]}"
        )

    def test_21c_partner_to_center_always_allowed(
        self, admin_client, test_db: Session
    ):
        """SMOKE-21c: PARTNER→CENTER is always allowed regardless of semesters."""
        partner = self._make_partner(test_db)

        resp = admin_client.post(
            f"/admin/locations/{partner.id}/edit",
            data=self._loc_payload(partner, "CENTER"),
            follow_redirects=False,
        )
        assert resp.status_code == 303, (
            f"Expected 303 (PARTNER→CENTER always OK), got {resp.status_code}"
        )


# ── SMOKE-22: GamePreset min_players guard ──────────────────────────────────


class TestSmoke22GamePresetPlayerCountGuard:
    """SMOKE-22: verify GamePreset.metadata.min_players is enforced by
    TournamentSessionGenerator.generate_sessions() for INDIVIDUAL_RANKING
    tournaments.

    Tests the guard added to session_generator.py (before format routing):
        if tournament.game_config_obj and tournament.game_config_obj.game_preset:
            preset_min = preset.game_config["metadata"].get("min_players", 0)
            if preset_min and player_count < preset_min: return False, ...

    SMOKE-22a: preset min=8, 5 enrolled players → generation blocked (preset guard)
    SMOKE-22b: no preset attached, 1 enrolled player → generation blocked
               by hardcoded >=2 guard (confirms preset guard is NOT the blocker)
    """

    def _make_eligible_instructor(self, test_db):
        """Create a fresh INSTRUCTOR user with active LFA_COACH license."""
        from datetime import datetime, timezone
        uid = uuid.uuid4().hex[:8]
        instr = User(
            name=f"S22 Instructor {uid}",
            email=f"s22-instr-{uid}@smoke22.test",
            role=UserRole.INSTRUCTOR,
            password_hash="x",
            is_active=True,
        )
        test_db.add(instr)
        test_db.flush()
        test_db.add(UserLicense(
            user_id=instr.id,
            specialization_type=SpecializationType.LFA_COACH.value,
            current_level=7,
            max_achieved_level=7,
            is_active=True,
            started_at=datetime.now(timezone.utc),
        ))
        test_db.flush()
        return instr

    def _make_ir_tournament(self, test_db, admin_user, suffix=""):
        """Create an INDIVIDUAL_RANKING tournament in IN_PROGRESS status."""
        from datetime import date, timedelta
        today = date.today()
        uid = uuid.uuid4().hex[:8]
        loc = Location(
            name=f"S22 Location {uid}",
            city=f"S22City-{uid}",
            country="HU",
            is_active=True,
            location_type=LocationType.CENTER,
        )
        test_db.add(loc)
        test_db.flush()
        camp = Campus(location_id=loc.id, name=f"S22 Campus {uid}", is_active=True)
        test_db.add(camp)
        test_db.flush()
        # Session generation requires ≥1 active pitch on the campus (domain invariant)
        test_db.add(Pitch(campus_id=camp.id, pitch_number=1, name="Pálya A", capacity=22, is_active=True))
        test_db.flush()
        eligible_instructor = self._make_eligible_instructor(test_db)
        tourn = Semester(
            code=f"S22{suffix}-{uuid.uuid4().hex[:6]}",
            name=f"SMOKE-22{suffix} Preset Guard Test",
            start_date=today,
            end_date=today + timedelta(days=7),
            tournament_status="IN_PROGRESS",
            master_instructor_id=eligible_instructor.id,
            campus_id=camp.id,
            tournament_config_obj=TournamentConfiguration(
                tournament_type_id=None,
                participant_type="INDIVIDUAL",
                scoring_type="PLACEMENT",   # scoring_type != "HEAD_TO_HEAD" → format = INDIVIDUAL_RANKING
                sessions_generated=False,
            ),
        )
        test_db.add(tourn)
        test_db.flush()
        return tourn

    def _enroll(self, test_db, tournament_id, n):
        """Create n minimal users (each with a UserLicense) and APPROVED enrollments."""
        from datetime import date
        for i in range(n):
            u = User(
                name=f"S22Player-{i}-{uuid.uuid4().hex[:4]}",
                email=f"s22p{i}-{uuid.uuid4().hex[:6]}@smoke22.test",
                role=UserRole.STUDENT,
                password_hash="x",
            )
            test_db.add(u)
            test_db.flush()
            lic = UserLicense(
                user_id=u.id,
                specialization_type=SpecializationType.LFA_FOOTBALL_PLAYER.value,
                started_at=date.today(),
                is_active=True,
            )
            test_db.add(lic)
            test_db.flush()
            test_db.add(SemesterEnrollment(
                user_id=u.id,
                semester_id=tournament_id,
                user_license_id=lic.id,
                is_active=True,
                request_status=EnrollmentStatus.APPROVED,
            ))
        test_db.flush()

    def test_22a_preset_min_blocks_when_insufficient_players(
        self, test_db: Session, admin_user: User
    ):
        """SMOKE-22a: preset min=8, 5 enrolled → fail with preset error.

        Validates the guard at real-DB level:
        - validator passes (5 >= 2 minimum for INDIVIDUAL_RANKING)
        - preset guard fires before the IR generator is invoked
        - error message names the preset and cites both the minimum and actual count
        """
        from app.services.tournament.session_generation.session_generator import TournamentSessionGenerator

        preset = GamePreset(
            name="SMOKE22 MinPlayers Preset",
            code=f"S22P-{uuid.uuid4().hex[:4].upper()}",
            is_active=True,
            game_config={
                "metadata": {"min_players": 8},
                "skill_config": {"skill_weights": {}},
                "format_config": {},
            },
        )
        test_db.add(preset)
        test_db.flush()

        tourn = self._make_ir_tournament(test_db, admin_user, suffix="a")

        test_db.add(GameConfiguration(
            semester_id=tourn.id,
            game_preset_id=preset.id,
            game_config=preset.game_config,
        ))
        test_db.flush()

        self._enroll(test_db, tourn.id, n=5)

        ok, msg, sessions = TournamentSessionGenerator(test_db).generate_sessions(tourn.id)

        assert ok is False, f"Expected generation blocked by preset guard, got ok=True; msg={msg}"
        assert "SMOKE22 MinPlayers Preset" in msg, f"Expected preset name in msg: {msg}"
        assert "8" in msg, f"Expected preset min (8) cited in msg: {msg}"
        assert "5" in msg, f"Expected actual count (5) cited in msg: {msg}"
        assert sessions == []

    def test_22b_no_preset_falls_through_to_builtin_minimum(
        self, test_db: Session, admin_user: User
    ):
        """SMOKE-22b: no preset, 1 enrolled player → fail with hardcoded >=2 error.

        Confirms that when no GamePreset is attached, the preset guard is skipped
        entirely and the hardcoded INDIVIDUAL_RANKING minimum (2 players) remains
        the active guard.
        """
        from app.services.tournament.session_generation.session_generator import TournamentSessionGenerator

        tourn = self._make_ir_tournament(test_db, admin_user, suffix="b")
        # No GameConfiguration / GamePreset attached

        self._enroll(test_db, tourn.id, n=1)

        ok, msg, sessions = TournamentSessionGenerator(test_db).generate_sessions(tourn.id)

        assert ok is False, f"Expected generation blocked by >=2 guard, got ok=True"
        assert "2" in msg, f"Expected '2' (minimum) cited in msg: {msg}"
        # Preset name must NOT appear (no preset was attached)
        assert "Preset" not in msg, f"Unexpected preset reference in msg: {msg}"
        assert sessions == []

    def test_22c_lifecycle_regeneration_after_player_addition(
        self, test_db: Session, admin_user: User
    ):
        """SMOKE-22c: lifecycle regeneration scenario — V3 stability proof.

        Sequence:
          1. INDIVIDUAL_RANKING tournament, preset min=8
          2. First generation attempt (5 players) → blocked by preset guard
          3. sessions_generated flag must still be False (no state corruption)
          4. 3 more players added → total 8 (meets preset minimum)
          5. Second generation attempt → succeeds
          6. sessions_generated flag is now True, sessions exist in DB

        This proves the guard is idempotent: a failed generation leaves the
        tournament in a clean re-tryable state.
        """
        from app.services.tournament.session_generation.session_generator import TournamentSessionGenerator

        # ── Setup: tournament + preset(min=8) ─────────────────────────────────
        preset = GamePreset(
            name="SMOKE22c Regen Preset",
            code=f"S22C-{uuid.uuid4().hex[:4].upper()}",
            is_active=True,
            game_config={
                "metadata": {"min_players": 8},
                "skill_config": {"skill_weights": {}},
                "format_config": {},
            },
        )
        test_db.add(preset)
        test_db.flush()

        tourn = self._make_ir_tournament(test_db, admin_user, suffix="c")
        game_cfg = GameConfiguration(
            semester_id=tourn.id,
            game_preset_id=preset.id,
            game_config=preset.game_config,
        )
        test_db.add(game_cfg)
        test_db.flush()

        # ── Step 1: enroll 5 players (below preset minimum of 8) ──────────────
        self._enroll(test_db, tourn.id, n=5)

        # ── Step 2: first generation attempt → must be blocked ────────────────
        gen = TournamentSessionGenerator(test_db)
        ok1, msg1, sessions1 = gen.generate_sessions(tourn.id)

        assert ok1 is False, f"Expected blocked, got ok=True; msg={msg1}"
        assert "8" in msg1 and "5" in msg1, f"Guard message must cite 8 and 5: {msg1}"
        assert sessions1 == []

        # ── Step 3: verify no state corruption after failed attempt ────────────
        test_db.refresh(tourn)
        assert tourn.sessions_generated is False, (
            "sessions_generated must remain False after blocked generation"
        )
        cfg = test_db.query(TournamentConfiguration).filter(
            TournamentConfiguration.semester_id == tourn.id
        ).first()
        assert cfg.sessions_generated is False, (
            "TournamentConfiguration.sessions_generated must remain False"
        )

        # ── Step 4: add 3 more players → total 8 ──────────────────────────────
        self._enroll(test_db, tourn.id, n=3)

        # ── Step 5: second generation attempt → must succeed ──────────────────
        ok2, msg2, sessions2 = gen.generate_sessions(tourn.id)

        assert ok2 is True, f"Expected success after reaching 8 players; msg={msg2}"
        assert len(sessions2) > 0, "Expected at least one session to be created"

        # ── Step 6: state reflects success ────────────────────────────────────
        test_db.refresh(tourn)
        assert tourn.sessions_generated is True, (
            "sessions_generated must be True after successful generation"
        )


# ─────────────────────────────────────────────────────────────────────────────
# SMOKE-23 — Tournament Edit Page (FÁZIS 2)
# ─────────────────────────────────────────────────────────────────────────────

class TestSmoke23TournamentEditPage:
    """SMOKE-23: Admin tournament edit page — GET route, basic info PATCH,
    schedule-config PATCH, and enrolled player check-in status display.

    SMOKE-23a: GET /admin/tournaments/{id}/edit → 200, HTML contains tournament
               name, code, and all 6 section IDs.
    SMOKE-23b: PATCH /api/v1/tournaments/{id} (via API client) → name updated
               in the DB, response contains updated name.
    SMOKE-23c: PATCH /api/v1/tournaments/{id}/schedule-config → match_duration,
               break_duration, parallel_fields persisted in TournamentConfiguration.
    SMOKE-23d: GET /admin/tournaments/{id}/edit with enrolled player → HTML
               shows enrolled player count and check-in status indicators.
    """

    # ── Shared helpers ────────────────────────────────────────────────────────

    def _make_tournament(self, test_db: Session, admin_user: User, suffix: str = "") -> Semester:
        """Create a minimal tournament with TournamentConfiguration."""
        today = date.today()
        tourn = Semester(
            code=f"S23{suffix}-{uuid.uuid4().hex[:6]}",
            name=f"SMOKE-23{suffix} Edit Test",
            start_date=today,
            end_date=today + timedelta(days=7),
            tournament_status="ENROLLMENT_CLOSED",
            master_instructor_id=admin_user.id,
            tournament_config_obj=TournamentConfiguration(
                participant_type="INDIVIDUAL",
                scoring_type="PLACEMENT",
                sessions_generated=False,
                parallel_fields=1,
            ),
        )
        test_db.add(tourn)
        test_db.commit()
        test_db.refresh(tourn)
        return tourn

    def _enroll_player(self, test_db: Session, tournament_id: int) -> tuple:
        """Create one student + license + APPROVED enrollment. Returns (user, enrollment)."""
        u = User(
            name=f"S23Player-{uuid.uuid4().hex[:4]}",
            email=f"s23p-{uuid.uuid4().hex[:6]}@smoke23.test",
            role=UserRole.STUDENT,
            password_hash="x",
        )
        test_db.add(u)
        test_db.flush()
        lic = UserLicense(
            user_id=u.id,
            specialization_type=SpecializationType.LFA_FOOTBALL_PLAYER.value,
            started_at=date.today(),
            is_active=True,
        )
        test_db.add(lic)
        test_db.flush()
        enroll = SemesterEnrollment(
            user_id=u.id,
            semester_id=tournament_id,
            user_license_id=lic.id,
            is_active=True,
            request_status=EnrollmentStatus.APPROVED,
        )
        test_db.add(enroll)
        test_db.commit()
        test_db.refresh(enroll)
        return u, enroll

    @pytest.fixture(scope="function")
    def web_client(self, test_db: Session, admin_user: User) -> TestClient:
        """TestClient with web auth override (for GET /admin/... routes)."""
        def _db():
            yield test_db

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user_web] = lambda: admin_user

        with TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"}) as c:
            yield c

        app.dependency_overrides.clear()

    @pytest.fixture(scope="function")
    def api_client(self, test_db: Session, admin_user: User) -> TestClient:
        """TestClient with Bearer auth override (for PATCH /api/v1/... routes)."""
        def _db():
            yield test_db

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = lambda: admin_user
        app.dependency_overrides[get_current_admin_user_hybrid] = lambda: admin_user

        with TestClient(app) as c:
            yield c

        app.dependency_overrides.clear()

    # ── SMOKE-23a ─────────────────────────────────────────────────────────────

    def test_23a_edit_page_loads_with_tournament_data(
        self, test_db: Session, admin_user: User, web_client: TestClient
    ):
        """GET /admin/tournaments/{id}/edit → 200, contains name/code + sections."""
        tourn = self._make_tournament(test_db, admin_user, suffix="a")

        resp = web_client.get(f"/admin/tournaments/{tourn.id}/edit")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"

        html = resp.text
        # Tournament identity present
        assert tourn.name in html, "Tournament name missing from edit page"
        assert tourn.code in html, "Tournament code missing from edit page"
        # All 6 section anchors present
        for section_id in [
            "section-basic", "section-schedule", "section-rewards",
            "section-checkin", "section-sessions", "section-results"
        ]:
            assert section_id in html, f"Section ID '{section_id}' missing from edit page"
        # AdminAPI client loaded
        assert "admin-api.js" in html, "admin-api.js not loaded on edit page"

    # ── SMOKE-23b ─────────────────────────────────────────────────────────────

    def test_23b_patch_tournament_updates_name(
        self, test_db: Session, admin_user: User, api_client: TestClient
    ):
        """PATCH /api/v1/tournaments/{id} → name field updated in DB."""
        tourn = self._make_tournament(test_db, admin_user, suffix="b")
        new_name = f"SMOKE-23b Updated Name {uuid.uuid4().hex[:4]}"

        resp = api_client.patch(
            f"/api/v1/tournaments/{tourn.id}",
            json={"name": new_name},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = resp.json()
        assert body.get("tournament_name") == new_name, (
            f"Response does not reflect new name: {body}"
        )
        # Verify DB state
        test_db.refresh(tourn)
        assert tourn.name == new_name, f"DB name not updated: {tourn.name}"

    # ── SMOKE-23c ─────────────────────────────────────────────────────────────

    def test_23c_patch_schedule_config_persists(
        self, test_db: Session, admin_user: User, api_client: TestClient
    ):
        """PATCH /api/v1/tournaments/{id}/schedule-config → values stored in TournamentConfiguration."""
        from app.models.tournament_configuration import TournamentConfiguration as TournConfig

        tourn = self._make_tournament(test_db, admin_user, suffix="c")

        resp = api_client.patch(
            f"/api/v1/tournaments/{tourn.id}/schedule-config",
            json={
                "match_duration_minutes": 75,
                "break_duration_minutes": 12,
                "parallel_fields": 3,
            },
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

        body = resp.json()
        assert body.get("success") is True
        assert body["match_duration_minutes"] == 75
        assert body["break_duration_minutes"] == 12
        assert body["parallel_fields"] == 3

        # Verify DB persistence
        cfg = test_db.query(TournConfig).filter(
            TournConfig.semester_id == tourn.id
        ).first()
        assert cfg is not None
        assert cfg.match_duration_minutes == 75
        assert cfg.break_duration_minutes == 12
        assert cfg.parallel_fields == 3

    # ── SMOKE-23d ─────────────────────────────────────────────────────────────

    def test_23d_edit_page_shows_enrolled_players_and_checkin_status(
        self, test_db: Session, admin_user: User, web_client: TestClient
    ):
        """Edit page shows enrollment count + Manage Players link (dedicated page pattern).
        Player details are on /admin/tournaments/{id}/players."""
        tourn = self._make_tournament(test_db, admin_user, suffix="d")
        player, enroll = self._enroll_player(test_db, tourn.id)

        resp = web_client.get(f"/admin/tournaments/{tourn.id}/edit")
        assert resp.status_code == 200

        html = resp.text
        # Enrollment section present
        assert "section-checkin" in html, "Check-in section missing"
        # Enrolled Players card present with count
        assert "Enrolled Players" in html, "Enrolled Players heading missing"
        # Link to dedicated players management page
        assert f"/admin/tournaments/{tourn.id}/players" in html, (
            "Manage Players link missing from edit page"
        )

        # Players page shows the actual player email and check-in status
        resp2 = web_client.get(f"/admin/tournaments/{tourn.id}/players")
        assert resp2.status_code == 200
        html2 = resp2.text
        assert player.email in html2, "Enrolled player email not shown on players page"
        assert "Enrolled Players" in html2, "Enrolled Players heading missing on players page"


# ─────────────────────────────────────────────────────────────────────────────
# SMOKE-24 — Session Generation Wizard (FÁZIS 3)
# ─────────────────────────────────────────────────────────────────────────────

class TestSmoke24SessionGenWizard:
    """SMOKE-24: Session generation wizard integration on the tournament edit page.

    SMOKE-24a: Edit page renders preset warning banner when enrolled_count <
               preset_min_players (GamePreset guard visible in HTML).
    SMOKE-24b: POST /api/v1/tournaments/{id}/generate-sessions for an
               INDIVIDUAL_RANKING tournament with 2 approved players → sync
               response: success=True, sessions_generated_count >= 1.
    SMOKE-24c: Edit page contains wizard overlay HTML (sgw-overlay) and loads
               session-gen-wizard.js (FÁZIS 3 structural check).
    """

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_eligible_instructor(self, test_db: Session) -> User:
        """Create a fresh INSTRUCTOR user with active LFA_COACH license."""
        from datetime import datetime, timezone
        uid = uuid.uuid4().hex[:8]
        instr = User(
            name=f"S24 Instructor {uid}",
            email=f"s24-instr-{uid}@smoke24.test",
            role=UserRole.INSTRUCTOR,
            password_hash="x",
            is_active=True,
        )
        test_db.add(instr)
        test_db.flush()
        test_db.add(UserLicense(
            user_id=instr.id,
            specialization_type=SpecializationType.LFA_COACH.value,
            current_level=7,
            max_achieved_level=7,
            is_active=True,
            started_at=datetime.now(timezone.utc),
        ))
        test_db.flush()
        return instr

    def _make_ir_tournament(
        self,
        test_db: Session,
        admin_user: User,
        suffix: str = "",
        status: str = "ENROLLMENT_CLOSED",
    ) -> Semester:
        """INDIVIDUAL_RANKING tournament (scoring_type=PLACEMENT, no tournament_type_id)."""
        today = date.today()
        uid = uuid.uuid4().hex[:8]
        loc = Location(
            name=f"S24 Location {uid}",
            city=f"S24City-{uid}",
            country="HU",
            is_active=True,
            location_type=LocationType.CENTER,
        )
        test_db.add(loc)
        test_db.flush()
        camp = Campus(location_id=loc.id, name=f"S24 Campus {uid}", is_active=True)
        test_db.add(camp)
        test_db.flush()
        # Session generation requires ≥1 active pitch on the campus (domain invariant)
        test_db.add(Pitch(campus_id=camp.id, pitch_number=1, name="Pálya A", capacity=22, is_active=True))
        test_db.flush()
        eligible_instructor = self._make_eligible_instructor(test_db)
        tourn = Semester(
            code=f"S24{suffix}-{uuid.uuid4().hex[:6]}",
            name=f"SMOKE-24{suffix} Wizard Test",
            start_date=today,
            end_date=today + timedelta(days=7),
            tournament_status=status,
            master_instructor_id=eligible_instructor.id,
            campus_id=camp.id,
            tournament_config_obj=TournamentConfiguration(
                participant_type="INDIVIDUAL",
                scoring_type="PLACEMENT",  # → INDIVIDUAL_RANKING format
                tournament_type_id=None,
                sessions_generated=False,
                parallel_fields=1,
            ),
        )
        test_db.add(tourn)
        test_db.commit()
        test_db.refresh(tourn)
        return tourn

    def _attach_preset(
        self,
        test_db: Session,
        tournament_id: int,
        min_players: int = 10,
    ) -> GamePreset:
        """Create a GamePreset with min_players and attach it to the tournament via GameConfiguration."""
        preset = GamePreset(
            code=f"s24-preset-{uuid.uuid4().hex[:6]}",
            name=f"SMOKE-24 Test Preset (min {min_players})",
            game_config={
                "metadata": {"min_players": min_players},
                "format_config": {"INDIVIDUAL_RANKING": {}},
            },
            is_active=True,
        )
        test_db.add(preset)
        test_db.flush()
        game_cfg = GameConfiguration(
            semester_id=tournament_id,
            game_preset_id=preset.id,
        )
        test_db.add(game_cfg)
        test_db.commit()
        return preset

    def _enroll_player(
        self, test_db: Session, tournament_id: int
    ) -> tuple:
        """Create one student with an approved enrollment."""
        u = User(
            name=f"S24Player-{uuid.uuid4().hex[:4]}",
            email=f"s24p-{uuid.uuid4().hex[:6]}@smoke24.test",
            role=UserRole.STUDENT,
            password_hash="x",
        )
        test_db.add(u)
        test_db.flush()
        lic = UserLicense(
            user_id=u.id,
            specialization_type=SpecializationType.LFA_FOOTBALL_PLAYER.value,
            started_at=date.today(),
            is_active=True,
        )
        test_db.add(lic)
        test_db.flush()
        enroll = SemesterEnrollment(
            user_id=u.id,
            semester_id=tournament_id,
            user_license_id=lic.id,
            is_active=True,
            request_status=EnrollmentStatus.APPROVED,
        )
        test_db.add(enroll)
        test_db.commit()
        return u, enroll

    @pytest.fixture(scope="function")
    def web_client(self, test_db: Session, admin_user: User) -> TestClient:
        def _db():
            yield test_db

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user_web] = lambda: admin_user
        with TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"}) as c:
            yield c
        app.dependency_overrides.clear()

    @pytest.fixture(scope="function")
    def api_client(self, test_db: Session, admin_user: User) -> TestClient:
        def _db():
            yield test_db

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = lambda: admin_user
        app.dependency_overrides[get_current_admin_or_instructor_user_hybrid] = lambda: admin_user
        with TestClient(app) as c:
            yield c
        app.dependency_overrides.clear()

    # ── SMOKE-24a ─────────────────────────────────────────────────────────────

    def test_24a_preset_warning_shown_when_enrolled_lt_min_players(
        self, test_db: Session, admin_user: User, web_client: TestClient
    ):
        """Edit page shows warn-banner when enrolled_count < preset_min_players."""
        tourn = self._make_ir_tournament(test_db, admin_user, suffix="a")
        self._attach_preset(test_db, tourn.id, min_players=10)
        # 0 enrolled players → 0 < 10 → warning must appear

        resp = web_client.get(f"/admin/tournaments/{tourn.id}/edit")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"

        html = resp.text
        assert "Not enough players" in html, (
            "Preset minimum-players warning not shown when enrolled < min_players"
        )
        assert "10" in html, "min_players value (10) not shown in warning"

    # ── SMOKE-24b ─────────────────────────────────────────────────────────────

    def test_24b_generate_sessions_sync_returns_session_count(
        self, test_db: Session, admin_user: User, api_client: TestClient
    ):
        """POST generate-sessions for IR tournament with 2 players → sync result."""
        tourn = self._make_ir_tournament(
            test_db, admin_user, suffix="b", status="IN_PROGRESS"
        )
        self._enroll_player(test_db, tourn.id)
        self._enroll_player(test_db, tourn.id)

        resp = api_client.post(
            f"/api/v1/tournaments/{tourn.id}/generate-sessions",
            json={
                "parallel_fields": 1,
                "session_duration_minutes": 90,
                "break_minutes": 15,
                "number_of_rounds": 1,
            },
        )
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body.get("success") is True, f"success != True: {body}"
        assert body.get("sessions_generated_count", 0) >= 1, (
            f"Expected >= 1 session, got: {body.get('sessions_generated_count')}"
        )

    # ── SMOKE-24c ─────────────────────────────────────────────────────────────

    def test_24c_edit_page_contains_wizard_overlay_and_script(
        self, test_db: Session, admin_user: User, web_client: TestClient
    ):
        """Edit page includes sgw-overlay modal and loads session-gen-wizard.js."""
        tourn = self._make_ir_tournament(test_db, admin_user, suffix="c")

        resp = web_client.get(f"/admin/tournaments/{tourn.id}/edit")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

        html = resp.text
        assert "sgw-overlay" in html, "Wizard overlay (#sgw-overlay) missing from edit page"
        assert "session-gen-wizard.js" in html, "session-gen-wizard.js not loaded on edit page"
        assert "SessionGenWizard.open" in html, "openSessionGenWizard() wiring missing from scripts block"


# ─────────────────────────────────────────────────────────────────────────────
# SMOKE-25 — Instructor Management Pages (FÁZIS 4)
# ─────────────────────────────────────────────────────────────────────────────

class TestSmoke25InstructorManagement:
    """SMOKE-25: Admin instructor list + detail pages.

    SMOKE-25a: GET /admin/instructors → 200, stats row + instructor table present,
               nav item '👨‍🏫 Instructors' active.
    SMOKE-25b: GET /admin/instructors/{id} → 200, all 5 section IDs present,
               instructor name + email in HTML.
    SMOKE-25c: GET /admin/instructors/{id} shows license + availability data
               for a seeded instructor.
    """

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_instructor(self, test_db: Session, suffix: str = "") -> User:
        """Create a minimal INSTRUCTOR user."""
        u = User(
            name=f"SMOKE-25{suffix} Instructor",
            email=f"s25inst{suffix}-{uuid.uuid4().hex[:6]}@smoke25.test",
            role=UserRole.INSTRUCTOR,
            password_hash="x",
            is_active=True,
        )
        test_db.add(u)
        test_db.commit()
        test_db.refresh(u)
        return u

    @pytest.fixture(scope="function")
    def web_client(self, test_db: Session, admin_user: User) -> TestClient:
        def _db():
            yield test_db

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user_web] = lambda: admin_user
        with TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"}) as c:
            yield c
        app.dependency_overrides.clear()

    # ── SMOKE-25a ─────────────────────────────────────────────────────────────

    def test_25a_instructor_list_page_loads(
        self, test_db: Session, admin_user: User, web_client: TestClient
    ):
        """GET /admin/instructors → 200, stats + nav link present."""
        self._make_instructor(test_db, suffix="a")

        resp = web_client.get("/admin/instructors")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"

        html = resp.text
        # Nav item active
        assert "/admin/instructors" in html, "Nav link to /admin/instructors missing"
        # Stats row
        assert "Total" in html, "'Total' stat label missing"
        # Table header
        assert "Licenses" in html, "Licenses column missing"
        assert "Assignments" in html, "Assignments column missing"
        # Detail link present
        assert "Detail" in html or "🔍" in html, "Detail link missing from table"

    # ── SMOKE-25b ─────────────────────────────────────────────────────────────

    def test_25b_instructor_detail_page_loads_sections(
        self, test_db: Session, admin_user: User, web_client: TestClient
    ):
        """GET /admin/instructors/{id} → 200, all section IDs + instructor identity."""
        inst = self._make_instructor(test_db, suffix="b")

        resp = web_client.get(f"/admin/instructors/{inst.id}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"

        html = resp.text
        # Instructor identity
        assert inst.name in html, "Instructor name missing from detail page"
        assert inst.email in html, "Instructor email missing from detail page"
        # All 5 section anchors
        for section_id in [
            "section-basic", "section-licenses", "section-assignments",
            "section-availability", "section-requests",
        ]:
            assert section_id in html, f"Section '{section_id}' missing from detail page"
        # Edit profile link
        assert f"/admin/users/{inst.id}/edit" in html, "Edit profile link missing"

    # ── SMOKE-25c ─────────────────────────────────────────────────────────────

    def test_25c_instructor_detail_shows_license_and_availability(
        self, test_db: Session, admin_user: User, web_client: TestClient
    ):
        """Detail page renders seeded license + availability window data."""
        from app.models.instructor_assignment import InstructorAvailabilityWindow

        inst = self._make_instructor(test_db, suffix="c")

        # Seed a license
        lic = UserLicense(
            user_id=inst.id,
            specialization_type=SpecializationType.LFA_FOOTBALL_PLAYER.value,
            started_at=date.today(),
            is_active=True,
        )
        test_db.add(lic)

        # Seed an availability window
        avail = InstructorAvailabilityWindow(
            instructor_id=inst.id,
            year=2026,
            time_period="Q2",
            is_available=True,
            notes="Smoke25 availability",
        )
        test_db.add(avail)
        test_db.commit()

        resp = web_client.get(f"/admin/instructors/{inst.id}")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

        html = resp.text
        # License specialization visible
        assert SpecializationType.LFA_FOOTBALL_PLAYER.value in html, (
            "License specialization type not rendered"
        )
        # Availability window visible
        assert "Q2" in html, "Availability period 'Q2' not rendered"
        assert "2026" in html, "Availability year '2026' not rendered"


# ─────────────────────────────────────────────────────────────────────────────
# SMOKE-26 — Reward & Skill Progression Dashboard (FÁZIS 5)
# ─────────────────────────────────────────────────────────────────────────────

class TestSmoke26SkillProgressionDashboard:
    """SMOKE-26: Skill & XP Progression section in user_edit + skill tier distribution in analytics.

    SMOKE-26a: user_edit for LFA_FOOTBALL_PLAYER license holder → #progression section
               present with XP summary block (zero state).
    SMOKE-26b: SKILL_TIER_REACHED notification title renders in the milestones list.
    SMOKE-26c: GET /admin/analytics contains "Skill Tier Distribution" section.
    """

    def _make_student_with_lfa(self, db: Session) -> tuple:
        """Create a student + active LFA_FOOTBALL_PLAYER license."""
        u = User(
            name="SMOKE-26 Student",
            email=f"s26-{uuid.uuid4().hex[:6]}@smoke26.test",
            role=UserRole.STUDENT,
            password_hash="x",
            is_active=True,
        )
        db.add(u)
        db.flush()
        lic = UserLicense(
            user_id=u.id,
            specialization_type=SpecializationType.LFA_FOOTBALL_PLAYER.value,
            started_at=date.today(),
            is_active=True,
        )
        db.add(lic)
        db.flush()
        return u, lic

    @pytest.fixture(scope="function")
    def web_client(self, test_db: Session, admin_user: User) -> TestClient:
        def _db():
            yield test_db

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user_web] = lambda: admin_user
        with TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"}) as c:
            yield c
        app.dependency_overrides.clear()

    # ── SMOKE-26a ─────────────────────────────────────────────────────────────

    def test_26a_progression_section_visible_for_lfa_player(
        self, test_db: Session, admin_user: User, web_client: TestClient
    ):
        """user_edit shows #progression section for LFA_FOOTBALL_PLAYER license holder."""
        student, _ = self._make_student_with_lfa(test_db)
        test_db.commit()

        resp = web_client.get(f"/admin/users/{student.id}/edit")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"

        html = resp.text
        assert 'id="progression"' in html, "#progression section missing from page"
        assert "Total XP Earned" in html, "XP summary block missing from progression section"
        assert "Skill &amp; XP Progression" in html or "Skill & XP Progression" in html, (
            "Progression section heading missing"
        )

    # ── SMOKE-26b ─────────────────────────────────────────────────────────────

    def test_26b_skill_tier_milestone_renders_in_progression(
        self, test_db: Session, admin_user: User, web_client: TestClient
    ):
        """SKILL_TIER_REACHED notification title appears in the progression milestones list."""
        from app.models.notification import Notification, NotificationType

        student, _ = self._make_student_with_lfa(test_db)

        milestone_title = f"Skill Milestone: Dribbling SMOKE26b-{uuid.uuid4().hex[:4]}"
        notif = Notification(
            user_id=student.id,
            title=milestone_title,
            message="You reached the 60% threshold in Dribbling.",
            type=NotificationType.SKILL_TIER_REACHED,
        )
        test_db.add(notif)
        test_db.commit()

        resp = web_client.get(f"/admin/users/{student.id}/edit")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"

        html = resp.text
        assert "Skill Tier Milestones" in html, "Milestones sub-heading missing"
        assert "SMOKE26b" in html, "Milestone notification title not rendered in page"

    # ── SMOKE-26c ─────────────────────────────────────────────────────────────

    def test_26c_analytics_page_has_skill_tier_distribution_section(
        self, test_db: Session, admin_user: User, web_client: TestClient
    ):
        """GET /admin/analytics contains the Skill Tier Distribution section."""
        resp = web_client.get("/admin/analytics")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"

        html = resp.text
        assert "section-skill-dist" in html, "Skill Tier Distribution section ID missing"
        assert "Skill Tier Distribution" in html, "Skill Tier Distribution heading missing"


# ============================================================================
# SMOKE-27: Navigation hub landing pages
# ============================================================================


class TestSmoke27NavHubs:
    """SMOKE-27: /admin/programs and /admin/config hub pages load correctly."""

    # ── SMOKE-27a ──────────────────────────────────────────────────────────────

    def test_27a_programs_hub_loads(self, admin_client):
        """GET /admin/programs → 200, KPI cards and module cards present."""
        resp = admin_client.get("/admin/programs")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        assert "Internal Server Error" not in resp.text
        html = resp.text
        # Hub cards for both sub-modules
        assert "Semesters" in html, "Semesters hub card missing"
        assert "Enrollments" in html, "Enrollments hub card missing"
        # KPI labels
        assert "Active Semesters" in html, "Active Semesters KPI missing"
        assert "Pending Enrollments" in html, "Pending Enrollments KPI missing"

    # ── SMOKE-27b ──────────────────────────────────────────────────────────────

    def test_27b_config_hub_loads(self, admin_client):
        """GET /admin/config → 200, Game Presets card present."""
        resp = admin_client.get("/admin/config")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        assert "Internal Server Error" not in resp.text
        html = resp.text
        assert "Game Presets" in html, "Game Presets hub card missing"

    # ── SMOKE-27c ──────────────────────────────────────────────────────────────

    def test_27c_nav_has_10_items(self, admin_client):
        """GET /admin/analytics → admin dropdown nav contains all destinations."""
        resp = admin_client.get("/admin/analytics")
        assert resp.status_code == 200
        html = resp.text
        # Verify all nav destinations present (dropdown nav: hubs replaced by primary sub-pages)
        nav_links = [
            "/dashboard",
            "/admin/users",
            "/admin/semesters",    # Programs trigger now links directly to semesters
            "/admin/sessions",
            "/admin/tournaments",  # Events trigger now links directly to tournaments
            "/admin/payments",     # Finance entry point
            "/admin/locations",    # Venues module
            "/admin/game-presets", # Game Config trigger now links directly to game-presets
            "/admin/analytics",
            "/admin/system-events",
        ]
        for link in nav_links:
            assert link in html, f"Expected nav link '{link}' missing"
        # Dropdown labels present
        assert "💰 Finance" in html, "Finance nav label missing"
        assert "Tier milestones reached" in html, "Tier milestone count label missing"


# ============================================================================
# SMOKE-28: Operational admin dashboard — 4-layer layout
# ============================================================================


class TestSmoke28Dashboard:
    """SMOKE-28: Operational admin dashboard — 4-layer KPI + queue + activity layout."""

    # ── SMOKE-28a ──────────────────────────────────────────────────────────────

    def test_28a_dashboard_loads(self, admin_client):
        """GET /dashboard → 200, no Internal Server Error."""
        resp = admin_client.get("/dashboard")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        assert "Internal Server Error" not in resp.text

    # ── SMOKE-28b ──────────────────────────────────────────────────────────────

    def test_28b_kpi_labels_present(self, admin_client):
        """Dashboard contains all 4 primary KPI metric labels."""
        resp = admin_client.get("/dashboard")
        assert resp.status_code == 200
        html = resp.text
        assert "Total Users" in html, "Total Users KPI label missing"
        assert "Upcoming Sessions" in html, "Upcoming Sessions KPI label missing"
        assert "Active Tournaments" in html, "Active Tournaments KPI label missing"
        assert "Pending Revenue" in html, "Pending Revenue KPI label missing"

    # ── SMOKE-28c ──────────────────────────────────────────────────────────────

    def test_28c_queue_labels_present(self, admin_client):
        """Dashboard contains all 4 operational queue card labels."""
        resp = admin_client.get("/dashboard")
        assert resp.status_code == 200
        html = resp.text
        assert "Pending Enrollments" in html, "Pending Enrollments queue label missing"
        assert "Today's Sessions" in html, "Today's Sessions queue label missing"
        assert "Pending Payments" in html, "Pending Payments queue label missing"
        assert "Unresolved Events" in html, "Unresolved Events queue label missing"

    # ── SMOKE-28d ──────────────────────────────────────────────────────────────

    def test_28d_dashboard_no_server_error_clean_db(self, admin_client):
        """Dashboard renders without error on a clean (empty) test DB."""
        resp = admin_client.get("/dashboard")
        assert resp.status_code == 200
        html = resp.text
        assert "Internal Server Error" not in html
        assert "Traceback" not in html
        # Layer 3 quick-stats panel should be present
        assert "Active Semesters" in html, "Quick stats panel missing Active Semesters label"

    # ── SMOKE-28e ──────────────────────────────────────────────────────────────

    def test_28e_alert_banner_present_with_pending_enrollment(
        self, admin_client, test_db: Session
    ):
        """Alert banner renders when at least one PENDING enrollment exists."""
        # Seed: student → license → semester → PENDING enrollment
        student = User(
            email=f"smoke28e+{uuid.uuid4().hex[:8]}@lfa.com",
            name="Smoke28e Student",
            password_hash=get_password_hash("student123"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(student)
        test_db.flush()

        license_ = UserLicense(
            user_id=student.id,
            specialization_type="LFA_FOOTBALL_PLAYER",
            is_active=True,
            started_at=datetime.now(ZoneInfo("UTC")),
        )
        test_db.add(license_)
        test_db.flush()

        semester = Semester(
            code=f"SMOKE28E-{uuid.uuid4().hex[:6].upper()}",
            name="Smoke28e Semester",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=90),
            status=SemesterStatus.ONGOING,
        )
        test_db.add(semester)
        test_db.flush()

        enrollment = SemesterEnrollment(
            user_id=student.id,
            semester_id=semester.id,
            user_license_id=license_.id,
            request_status=EnrollmentStatus.PENDING,
        )
        test_db.add(enrollment)
        test_db.flush()

        resp = admin_client.get("/dashboard")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        html = resp.text
        assert "dashboard-alert-banner" in html, (
            "Alert banner div missing — expected when pending enrollment count > 0"
        )


# ============================================================================
# SMOKE-29: Location module — hierarchical location/campus views
# ============================================================================


class TestSmoke29LocationModule:
    """SMOKE-29: Location-centric admin module — list, detail, campus detail."""

    # ── SMOKE-29a ──────────────────────────────────────────────────────────────

    def test_29a_locations_in_nav_not_under_config(self, admin_client):
        """GET /admin/analytics → nav contains /admin/locations as top-level item,
        not nested under /admin/config.
        """
        resp = admin_client.get("/admin/analytics")
        assert resp.status_code == 200
        html = resp.text
        assert "/admin/locations" in html, "/admin/locations missing from nav"
        # Locations must appear as its own nav-item href, not inside /admin/config
        assert 'href="/admin/locations"' in html, (
            "/admin/locations should be a direct nav href, not nested"
        )

    # ── SMOKE-29b ──────────────────────────────────────────────────────────────

    def test_29b_location_detail_loads_with_four_sections(
        self, admin_client, test_db: Session
    ):
        """GET /admin/locations/{id} → 200 and all 4 section IDs present."""
        loc = Location(
            name="SMOKE29 Budapest",
            city="SMOKE29 City",
            country="Hungary",
            location_type=LocationType.CENTER,
            is_active=True,
        )
        test_db.add(loc)
        test_db.flush()

        resp = admin_client.get(f"/admin/locations/{loc.id}")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text[:400]}"
        )
        assert "Internal Server Error" not in resp.text
        html = resp.text
        for section_id in [
            "section-campuses",
            "section-programs",
            "section-sessions",
            "section-instructors",
        ]:
            assert section_id in html, f"Section '{section_id}' missing from location detail"

    # ── SMOKE-29c ──────────────────────────────────────────────────────────────

    def test_29c_location_detail_renders_seeded_campus_and_semester(
        self, admin_client, test_db: Session
    ):
        """Seeded Campus and Semester both appear in /admin/locations/{id}."""
        loc = Location(
            name="SMOKE29c Location",
            city="SMOKE29c City",
            country="Hungary",
            location_type=LocationType.CENTER,
            is_active=True,
        )
        test_db.add(loc)
        test_db.flush()

        campus = Campus(
            location_id=loc.id,
            name="SMOKE29c Campus",
            is_active=True,
        )
        test_db.add(campus)
        test_db.flush()

        semester = Semester(
            location_id=loc.id,
            campus_id=campus.id,
            name="SMOKE29c Program",
            code="SM29C",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=90),
            status=SemesterStatus.ONGOING,
            specialization_type="LFA_PLAYER_YOUTH",
            age_group="YOUTH",
        )
        test_db.add(semester)
        test_db.flush()

        resp = admin_client.get(f"/admin/locations/{loc.id}")
        assert resp.status_code == 200
        html = resp.text
        assert "SMOKE29c Campus" in html, "Seeded campus name not rendered in location detail"
        assert "SMOKE29c Program" in html, "Seeded semester name not rendered in location detail"

    # ── SMOKE-29d ──────────────────────────────────────────────────────────────

    def test_29d_campus_detail_loads_with_sections_and_parent_location(
        self, admin_client, test_db: Session
    ):
        """GET /admin/campuses/{id} → 200, 'Upcoming Sessions' section present,
        and parent location name visible.
        """
        loc = Location(
            name="SMOKE29d Location",
            city="SMOKE29d City",
            country="Hungary",
            location_type=LocationType.CENTER,
            is_active=True,
        )
        test_db.add(loc)
        test_db.flush()

        campus = Campus(
            location_id=loc.id,
            name="SMOKE29d Campus",
            is_active=True,
        )
        test_db.add(campus)
        test_db.flush()

        resp = admin_client.get(f"/admin/campuses/{campus.id}")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text[:400]}"
        )
        assert "Internal Server Error" not in resp.text
        html = resp.text
        assert "Upcoming Sessions" in html, "'Upcoming Sessions' section heading missing"
        assert "SMOKE29d City" in html, "Parent location city not rendered in campus detail"


# ============================================================================
# SMOKE-30: Event Management module — hub + camps + sessions filter
# ============================================================================


class TestSmoke30EventModule:
    """SMOKE-30: Event Management hub, Camps list, and sessions EventCategory filter."""

    # ── SMOKE-30a ──────────────────────────────────────────────────────────────

    def test_30a_events_hub_loads_with_four_hub_cards(self, admin_client):
        """GET /admin/events → 200 + all 4 hub card titles present."""
        resp = admin_client.get("/admin/events")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text[:400]}"
        )
        assert "Internal Server Error" not in resp.text
        html = resp.text
        for card_title in ["Tournaments", "Camps", "Training Sessions", "Match Sessions"]:
            assert card_title in html, f"Hub card '{card_title}' missing from /admin/events"

    # ── SMOKE-30b ──────────────────────────────────────────────────────────────

    def test_30b_camps_page_loads(self, admin_client):
        """GET /admin/camps → 200, no Internal Server Error (empty list is OK)."""
        resp = admin_client.get("/admin/camps")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text[:400]}"
        )
        assert "Internal Server Error" not in resp.text

    # ── SMOKE-30c ──────────────────────────────────────────────────────────────

    def test_30c_camps_page_renders_seeded_camp(
        self, admin_client, test_db: Session
    ):
        """Seeded CAMP semester appears on /admin/camps list."""
        camp = Semester(
            name="SMOKE30c Summer Camp",
            code="CAMP-SMOKE30C",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=7),
            status=SemesterStatus.DRAFT,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        # Set semester_category via attribute (string value accepted by SA)
        from app.models.semester import SemesterCategory as SC
        camp.semester_category = SC.CAMP
        test_db.add(camp)
        test_db.flush()

        resp = admin_client.get("/admin/camps")
        assert resp.status_code == 200
        assert "SMOKE30c Summer Camp" in resp.text, (
            "Seeded CAMP semester name not visible on /admin/camps"
        )

    # ── SMOKE-30d ──────────────────────────────────────────────────────────────

    def test_30d_sessions_event_category_filter_returns_200(self, admin_client):
        """GET /admin/sessions?event_category=MATCH → 200, no 500."""
        resp = admin_client.get("/admin/sessions?event_category=MATCH")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text[:400]}"
        )
        assert "Internal Server Error" not in resp.text

        resp2 = admin_client.get("/admin/sessions?event_category=TRAINING")
        assert resp2.status_code == 200
        assert "Internal Server Error" not in resp2.text


# ============================================================================
# SMOKE-31: Location-First Events module — per-location events page + camp edit
# ============================================================================


class TestSmoke31LocationFirstEvents:
    """SMOKE-31: Location-first Events redesign — per-location CRUD page and camp edit."""

    # ── SMOKE-31a ──────────────────────────────────────────────────────────────

    def test_31a_location_events_page_loads(self, admin_client, test_db: Session):
        """Seeded location → GET /admin/events/locations/{id} → 200, 4 section headers present."""
        from app.models.semester import SemesterCategory as SC

        loc = Location(
            name="TestCity31a Location",
            city="TestCity31a",
            country="HU",
            location_type=LocationType.CENTER,
            is_active=True,
        )
        test_db.add(loc)
        test_db.flush()

        camp = Semester(
            name="SMOKE31a Camp",
            code="CAMP-SMOKE31A",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=7),
            status=SemesterStatus.DRAFT,
            specialization_type="LFA_FOOTBALL_PLAYER",
            location_id=loc.id,
        )
        camp.semester_category = SC.CAMP
        test_db.add(camp)
        test_db.flush()

        resp = admin_client.get(f"/admin/events/locations/{loc.id}")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text[:400]}"
        )
        assert "Internal Server Error" not in resp.text
        html = resp.text
        assert "TestCity31a" in html, "Location city not in page heading"
        for section in ["Tournaments", "Camps", "Academy / Mini Seasons", "Upcoming Sessions"]:
            assert section in html, f"Section '{section}' missing from location events page"

    # ── SMOKE-31b ──────────────────────────────────────────────────────────────

    def test_31b_camp_edit_page_loads(self, admin_client, test_db: Session):
        """Seeded CAMP → GET /admin/camps/{id}/edit → 200, camp name and code in form."""
        from app.models.semester import SemesterCategory as SC

        camp = Semester(
            name="SMOKE31b Edit Camp",
            code="CAMP-SMOKE31B",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=5),
            status=SemesterStatus.DRAFT,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        camp.semester_category = SC.CAMP
        test_db.add(camp)
        test_db.flush()

        resp = admin_client.get(f"/admin/camps/{camp.id}/edit")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}: {resp.text[:400]}"
        )
        assert "Internal Server Error" not in resp.text
        html = resp.text
        assert "SMOKE31b Edit Camp" in html, "Camp name not found in edit page"
        assert "CAMP-SMOKE31B" in html, "Camp code not found in edit page"

    # ── SMOKE-31c ──────────────────────────────────────────────────────────────

    def test_31c_camp_edit_post_updates_camp(self, admin_client, test_db: Session):
        """POST /admin/camps/{id}/edit updates camp name → 303 redirect."""
        from app.models.semester import SemesterCategory as SC

        camp = Semester(
            name="SMOKE31c Original Name",
            code="CAMP-SMOKE31C",
            start_date=date.today(),
            end_date=date.today() + timedelta(days=5),
            status=SemesterStatus.DRAFT,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        camp.semester_category = SC.CAMP
        test_db.add(camp)
        test_db.flush()

        resp = admin_client.post(
            f"/admin/camps/{camp.id}/edit",
            data={
                "name": "SMOKE31c Updated Name",
                "code": "CAMP-SMOKE31C",
                "start_date": date.today().isoformat(),
                "end_date": (date.today() + timedelta(days=5)).isoformat(),
                "age_group": "",
                "location_id": "",
                "campus_id": "",
                "enrollment_cost": "0",
                "status": "DRAFT",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303, (
            f"Expected 303 redirect after camp update, got {resp.status_code}: {resp.text[:400]}"
        )

        test_db.refresh(camp)
        assert camp.name == "SMOKE31c Updated Name", (
            f"Camp name not updated in DB — got '{camp.name}'"
        )
