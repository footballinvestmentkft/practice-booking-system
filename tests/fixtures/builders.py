"""
Test data builders — lightweight factory functions for integration and E2E tests.

Design rules
────────────
• Every builder takes a SQLAlchemy session as first arg and optional keyword
  overrides for any column.
• Builders call db.flush() — NOT db.commit() — so the returned object has an
  assigned PK without permanently committing.  This composes correctly with:
    - SAVEPOINT-isolated function-scoped integration test fixtures
    - Module-scoped api_smoke fixtures (which own their own commit lifecycle)
• Names are unique by default (UUID suffix) so tests can call a builder
  multiple times without unique-constraint errors.

Usage examples
──────────────
    from tests.fixtures.builders import build_semester, build_project, build_enrollment

    def test_project_visible(test_db, client, admin_token):
        sem = build_semester(test_db)
        proj = build_project(test_db, sem.id, title="Sprint Project")
        lic  = build_user_license(test_db, user_id=42)
        enr  = build_enrollment(test_db, user_id=42, semester_id=sem.id,
                                 user_license_id=lic.id)
        response = client.get(f"/api/v1/projects/{proj.id}", ...)
        assert response.status_code == 200

    # Seed canonical E2E users + semester into any DB session:
    from tests.fixtures.builders import seed_baseline_to_db
    baseline = seed_baseline_to_db(test_db)
    # baseline["admin"], baseline["instructor"], baseline["student"], baseline["semester"]
"""

import uuid
from datetime import date, datetime, timedelta, timezone

from app.models.license import UserLicense
from app.models.project import Project, ProjectDifficulty, ProjectStatus
from app.models.semester import Semester
from app.models.semester_enrollment import EnrollmentStatus, SemesterEnrollment
from app.models.session import Session as SessionModel


# ── Internal helpers ──────────────────────────────────────────────────────────

def _uid(prefix: str = "t") -> str:
    """8-char UUID suffix — keeps generated codes and titles unique across tests."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _now_naive() -> datetime:
    """UTC now as a timezone-naive datetime (matches DB convention in this project)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Entity builders ───────────────────────────────────────────────────────────

def build_semester(db, *, code: str | None = None, name: str | None = None, **kwargs) -> Semester:
    """
    Create a minimal valid Semester and flush to assign an ID.

    Defaults: rolling ±30/+150 day window so session dates ~7 days out are valid.
    Override any Semester column via kwargs.
    """
    obj = Semester(
        **{
            "code":       code or _uid("SEM"),
            "name":       name or "Test Semester",
            "start_date": date.today() - timedelta(days=30),
            "end_date":   date.today() + timedelta(days=150),
            **kwargs,
        }
    )
    db.add(obj)
    db.flush()
    db.refresh(obj)
    return obj


def build_session(
    db,
    semester_id: int,
    *,
    title: str | None = None,
    **kwargs,
) -> SessionModel:
    """
    Create a minimal valid Session and flush to assign an ID.

    Defaults to an on-site session starting 7 days from now (within semester boundary).
    Override any Session column via kwargs.
    """
    now = _now_naive()
    obj = SessionModel(
        **{
            "title":       title or _uid("Session"),
            "semester_id": semester_id,
            "date_start":  now + timedelta(days=7),
            "date_end":    now + timedelta(days=7, hours=1),
            **kwargs,
        }
    )
    db.add(obj)
    db.flush()
    db.refresh(obj)
    return obj


def build_project(
    db,
    semester_id: int,
    *,
    title: str | None = None,
    **kwargs,
) -> Project:
    """
    Create a minimal valid Project and flush to assign an ID.

    Defaults to ACTIVE status, INTERMEDIATE difficulty.
    Override any Project column via kwargs.
    """
    obj = Project(
        **{
            "title":       title or _uid("Project"),
            "semester_id": semester_id,
            "status":      ProjectStatus.ACTIVE.value,
            "difficulty":  ProjectDifficulty.INTERMEDIATE.value,
            **kwargs,
        }
    )
    db.add(obj)
    db.flush()
    db.refresh(obj)
    return obj


def build_user_license(
    db,
    user_id: int,
    *,
    specialization_type: str = "PLAYER",
    **kwargs,
) -> UserLicense:
    """
    Create a UserLicense and flush to assign an ID.

    Idempotent: if a license already exists for (user_id, specialization_type),
    returns the existing one to respect the uq_user_license_spec UniqueConstraint.

    UserLicense is a prerequisite for SemesterEnrollment.
    `started_at` has no DB default — always supplied here.
    Override any column via kwargs.
    """
    # Mirror the model's canonicalization before querying.  Otherwise a legacy
    # alias such as PLAYER misses an existing GANCUJU_PLAYER row and only
    # collides with the unique constraint during flush.
    from app.services.canonical_policy import resolve_program_id

    resolution = resolve_program_id(specialization_type)
    canonical_specialization = (
        resolution.canonical_program.value if resolution.usable else specialization_type
    )
    existing = (
        db.query(UserLicense)
        .filter(
            UserLicense.user_id == user_id,
            UserLicense.specialization_type == canonical_specialization,
        )
        .first()
    )
    if existing:
        return existing

    obj = UserLicense(
        **{
            "user_id":             user_id,
            "specialization_type": canonical_specialization,
            "current_level":       1,
            "max_achieved_level":  1,
            "started_at":          _now_naive(),
            "is_active":           True,
            **kwargs,
        }
    )
    db.add(obj)
    db.flush()
    db.refresh(obj)
    return obj


def build_enrollment(
    db,
    user_id: int,
    semester_id: int,
    user_license_id: int,
    *,
    approved: bool = True,
    **kwargs,
) -> SemesterEnrollment:
    """
    Create a SemesterEnrollment and flush to assign an ID.

    Default: APPROVED status, is_active=True.
    Pass approved=False for a PENDING enrollment.
    Override any column via kwargs.
    """
    now = _now_naive()
    defaults: dict = {
        "user_id":         user_id,
        "semester_id":     semester_id,
        "user_license_id": user_license_id,
        "request_status":  EnrollmentStatus.APPROVED if approved else EnrollmentStatus.PENDING,
        "is_active":       approved,
        "requested_at":    now,
        "enrolled_at":     now,
    }
    if approved:
        defaults["approved_at"] = now

    obj = SemesterEnrollment(**{**defaults, **kwargs})
    db.add(obj)
    db.flush()
    db.refresh(obj)
    return obj


# Alias: in this codebase a "tournament" is a Semester with tournament fields.
# Use build_tournament when the intent is a tournament lifecycle, not a plain
# teaching semester, to make test intent explicit.
def build_tournament(db, *, code: str | None = None, name: str | None = None, **kwargs) -> Semester:
    """
    Alias for build_semester with a tournament-oriented name default.

    Add tournament-specific fields (e.g. tournament_status, specialization_type)
    via kwargs.
    """
    return build_semester(
        db,
        code=code or _uid("TOURN"),
        name=name or "Test Tournament",
        **kwargs,
    )


# ── Baseline seeder ───────────────────────────────────────────────────────────

def seed_baseline_to_db(db) -> dict:
    """
    Seed (or retrieve) the canonical E2E baseline users and semester in any DB session.

    Idempotent: looks up each entity by its unique key before creating.
    Uses db.flush() so it composes with SAVEPOINT-isolated integration tests.

    Returns a dict with the User and Semester objects:
        {
            "admin":      User,   # admin@lfa.com
            "instructor": User,   # grandmaster@lfa.com
            "student":    User,   # rdias@manchestercity.com
            "semester":   Semester,  # E2E-CI-2026
        }

    Typical integration test usage:
        def test_something(test_db):
            baseline = seed_baseline_to_db(test_db)
            lic = build_user_license(test_db, user_id=baseline["student"].id)
            enr = build_enrollment(test_db, baseline["student"].id, baseline["semester"].id, lic.id)
    """
    from app.core.security import get_password_hash
    from app.models.user import User

    from tests.fixtures.e2e_baseline import SEMESTERS, USERS

    result: dict = {}
    _role_key = {"ADMIN": "admin", "INSTRUCTOR": "instructor", "STUDENT": "student"}

    for spec in USERS:
        existing = db.query(User).filter(User.email == spec["email"]).first()
        if existing:
            result[_role_key[spec["role"].value]] = existing
        else:
            user = User(
                name=spec["name"],
                email=spec["email"],
                password_hash=get_password_hash(spec["password"]),
                role=spec["role"],
                is_active=True,
            )
            db.add(user)
            db.flush()
            db.refresh(user)
            result[_role_key[spec["role"].value]] = user

    today = date.today()
    spec = SEMESTERS[0]
    sem = db.query(Semester).filter(Semester.code == spec["code"]).first()
    if not sem:
        sem = Semester(
            code=spec["code"],
            name=spec["name"],
            start_date=today - timedelta(days=180),
            end_date=today + timedelta(days=180),
        )
        db.add(sem)
        db.flush()
        db.refresh(sem)

    result["semester"] = sem
    return result
