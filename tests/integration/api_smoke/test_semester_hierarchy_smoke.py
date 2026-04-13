"""
SMOKE-19 — Semester Hierarchy (parent_semester_id) Integration Tests
=====================================================================

Validates the access-control gate added to POST /semester-enrollments/enroll
and the EventRewardLog creation via the reward_service.

Tests:
  SMOKE-19a: Standalone semester (no parent) → enrollment succeeds (no gate)
  SMOKE-19b: Nested semester (parent_id set), student ENROLLED in parent → succeeds
  SMOKE-19c: Nested semester, student NOT enrolled in parent → 403
  SMOKE-19d: Nested semester, student has INACTIVE enrollment in parent → 403
  SMOKE-19e: award_session_completion creates EventRewardLog row in DB
  SMOKE-19f: award_session_completion is idempotent (second call updates, no duplicate)
  SMOKE-19g: XP priority — explicit session_reward_config.base_xp overrides category default
  SMOKE-19h: XP priority — event_category MATCH default (100 XP) when no config
  SMOKE-19i: XP priority — event_category TRAINING default (50 XP) when no config
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models.semester import Semester, SemesterCategory
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.session import Session as SessionModel, SessionType, EventCategory
from app.models.event_reward_log import EventRewardLog
from app.models.user import User, UserRole
from app.services.reward_service import award_session_completion
from tests.fixtures.builders import build_enrollment, build_semester, build_user_license


# ── Helpers ───────────────────────────────────────────────────────────────────

def _uid() -> str:
    return uuid.uuid4().hex[:8].upper()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_session(db: Session, semester_id: int, event_category: EventCategory,
                  reward_config: dict | None = None) -> SessionModel:
    """Create a minimal Session in the given semester."""
    now = _now()
    s = SessionModel(
        title=f"Test Session {_uid()}",
        semester_id=semester_id,
        session_type=SessionType.on_site,
        date_start=now + timedelta(hours=1),
        date_end=now + timedelta(hours=2),
        event_category=event_category,
        session_reward_config=reward_config,
    )
    db.add(s)
    db.flush()
    db.refresh(s)
    return s


def _auth(admin_token: str) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def smoke_student(test_db: Session):
    """Create a fresh student user for this test module (independent of conftest seeding)."""
    from app.models.license import UserLicense
    uid = _uid()
    user = User(
        name=f"Smoke Hierarchy Student {uid}",
        email=f"smoke.hierarchy.{uid}@example.com",
        password_hash=get_password_hash("student123"),
        role=UserRole.STUDENT,
        is_active=True,
        date_of_birth=date(2000, 1, 1),
        credit_balance=1000,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    yield user

    # Teardown: remove licenses then user (enrollments removed by hierarchy_data teardown)
    test_db.query(UserLicense).filter(UserLicense.user_id == user.id).delete(synchronize_session=False)
    test_db.delete(user)
    test_db.commit()


@pytest.fixture()
def hierarchy_data(test_db: Session, smoke_student):
    """
    Creates:
      parent_sem  (ACADEMY_SEASON, no parent)
      child_sem   (MINI_SEASON, parent_semester_id = parent_sem.id)
      student_license (for smoke_student)
      parent_enrollment (is_active=True, approved)
    """
    uid = _uid()

    parent_sem = build_semester(
        test_db,
        code=f"PAR-{uid}",
        name=f"Parent Academy {uid}",
        semester_category=SemesterCategory.ACADEMY_SEASON,
    )
    test_db.flush()

    child_sem = build_semester(
        test_db,
        code=f"CHD-{uid}",
        name=f"Child Mini Season {uid}",
        semester_category=SemesterCategory.MINI_SEASON,
        parent_semester_id=parent_sem.id,
    )
    test_db.flush()

    student_lic = build_user_license(
        test_db, user_id=smoke_student.id, specialization_type="PLAYER"
    )
    test_db.flush()

    parent_enr = build_enrollment(
        test_db,
        user_id=smoke_student.id,
        semester_id=parent_sem.id,
        user_license_id=student_lic.id,
        approved=True,
    )
    test_db.commit()  # commit so TestClient (separate DB connection) can see the data

    yield {
        "parent_sem": parent_sem,
        "child_sem": child_sem,
        "student_lic": student_lic,
        "parent_enr": parent_enr,
        "student": smoke_student,
    }

    # Teardown: remove enrollments, sessions, then semesters
    test_db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id.in_([parent_sem.id, child_sem.id])
    ).delete(synchronize_session=False)
    test_db.query(SessionModel).filter(
        SessionModel.semester_id.in_([parent_sem.id, child_sem.id])
    ).delete(synchronize_session=False)
    test_db.delete(child_sem)
    test_db.delete(parent_sem)
    test_db.commit()


# ── SMOKE-19a to 19d: Enrollment gate ─────────────────────────────────────────

class TestSmoke19EnrollmentHierarchyGate:
    """Parent semester access-control gate on POST /semester-enrollments/enroll."""

    def test_19a_standalone_semester_no_gate(
        self, api_client: TestClient, admin_token: str,
        test_db: Session, smoke_student
    ):
        """Standalone semester (no parent_semester_id) → enrollment succeeds without gate."""
        uid = _uid()
        sem = build_semester(
            test_db, code=f"SA-{uid}", name=f"Standalone {uid}",
            semester_category=SemesterCategory.TOURNAMENT,
        )
        lic = build_user_license(test_db, user_id=smoke_student.id, specialization_type="PLAYER")
        test_db.commit()

        resp = api_client.post(
            "/api/v1/semester-enrollments/enroll",
            json={
                "user_id": smoke_student.id,
                "semester_id": sem.id,
                "user_license_id": lic.id,
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True

        # Cleanup
        test_db.query(SemesterEnrollment).filter(
            SemesterEnrollment.semester_id == sem.id
        ).delete(synchronize_session=False)
        test_db.delete(sem)
        test_db.commit()

    def test_19b_nested_with_parent_enrollment_succeeds(
        self, api_client: TestClient, admin_token: str,
        test_db: Session, hierarchy_data: dict
    ):
        """Child semester + student already enrolled in parent → 200."""
        d = hierarchy_data
        resp = api_client.post(
            "/api/v1/semester-enrollments/enroll",
            json={
                "user_id": d["student"].id,
                "semester_id": d["child_sem"].id,
                "user_license_id": d["student_lic"].id,
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True

    def test_19c_nested_without_parent_enrollment_returns_403(
        self, api_client: TestClient, admin_token: str,
        test_db: Session, smoke_student
    ):
        """Child semester + student NOT enrolled in parent → 403."""
        uid = _uid()
        parent = build_semester(
            test_db, code=f"PAR2-{uid}", name=f"Parent2 {uid}",
            semester_category=SemesterCategory.ACADEMY_SEASON,
        )
        test_db.flush()
        child = build_semester(
            test_db, code=f"CHD2-{uid}", name=f"Child2 {uid}",
            semester_category=SemesterCategory.MINI_SEASON,
            parent_semester_id=parent.id,
        )
        lic = build_user_license(test_db, user_id=smoke_student.id, specialization_type="PLAYER")
        test_db.commit()  # commit so TestClient can see the data

        # No parent enrollment created → gate should block
        resp = api_client.post(
            "/api/v1/semester-enrollments/enroll",
            json={
                "user_id": smoke_student.id,
                "semester_id": child.id,
                "user_license_id": lic.id,
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 403, resp.text
        body = resp.json()
        msg = body.get("error", {}).get("message", body.get("detail", ""))
        assert "parent program" in msg.lower()

        # Cleanup
        test_db.query(SemesterEnrollment).filter(
            SemesterEnrollment.semester_id.in_([parent.id, child.id])
        ).delete(synchronize_session=False)
        test_db.delete(child)
        test_db.delete(parent)
        test_db.commit()

    def test_19d_inactive_parent_enrollment_returns_403(
        self, api_client: TestClient, admin_token: str,
        test_db: Session, hierarchy_data: dict
    ):
        """Parent enrollment is_active=False → child enrollment still blocked."""
        d = hierarchy_data

        # Deactivate the parent enrollment
        enr = test_db.query(SemesterEnrollment).filter(
            SemesterEnrollment.id == d["parent_enr"].id
        ).first()
        enr.is_active = False
        test_db.commit()

        resp = api_client.post(
            "/api/v1/semester-enrollments/enroll",
            json={
                "user_id": d["student"].id,
                "semester_id": d["child_sem"].id,
                "user_license_id": d["student_lic"].id,
            },
            headers=_auth(admin_token),
        )
        assert resp.status_code == 403, resp.text

        # Restore for hierarchy_data teardown
        enr.is_active = True
        test_db.commit()


# ── SMOKE-19e to 19i: EventRewardLog + reward_service ─────────────────────────

class TestSmoke19RewardService:
    """award_session_completion creates and upserts EventRewardLog rows."""

    def test_19e_award_creates_event_reward_log(self, test_db: Session, hierarchy_data: dict):
        """award_session_completion creates a new EventRewardLog row."""
        d = hierarchy_data
        sess = _make_session(test_db, d["child_sem"].id, EventCategory.TRAINING)
        test_db.commit()

        log = award_session_completion(
            test_db, user_id=d["student"].id, session=sess, multiplier=1.0
        )
        assert log.id is not None
        assert log.user_id == d["student"].id
        assert log.session_id == sess.id
        assert log.xp_earned == 50          # TRAINING default
        assert log.multiplier_applied == 1.0

        # Verify persisted in DB
        test_db.expire_all()
        persisted = test_db.query(EventRewardLog).filter(EventRewardLog.id == log.id).first()
        assert persisted is not None
        assert persisted.xp_earned == 50

    def test_19f_award_is_idempotent(self, test_db: Session, hierarchy_data: dict):
        """Calling award_session_completion twice updates existing row, no duplicate."""
        d = hierarchy_data
        sess = _make_session(test_db, d["child_sem"].id, EventCategory.MATCH)
        test_db.commit()

        log1 = award_session_completion(
            test_db, user_id=d["student"].id, session=sess, multiplier=1.0
        )
        log2 = award_session_completion(
            test_db, user_id=d["student"].id, session=sess, multiplier=1.5
        )

        assert log1.id == log2.id, "Re-award must update existing row, not insert a new one"
        assert log2.multiplier_applied == 1.5
        assert log2.xp_earned == int(100 * 1.5)  # MATCH default * 1.5

        count = test_db.query(EventRewardLog).filter(
            EventRewardLog.user_id == d["student"].id,
            EventRewardLog.session_id == sess.id,
        ).count()
        assert count == 1

    def test_19g_explicit_reward_config_overrides_category_default(
        self, test_db: Session, hierarchy_data: dict
    ):
        """session_reward_config.base_xp takes priority over category default XP."""
        d = hierarchy_data
        sess = _make_session(
            test_db,
            d["child_sem"].id,
            EventCategory.TRAINING,
            reward_config={"v": 1, "base_xp": 200, "skill_areas": []},
        )
        test_db.commit()

        log = award_session_completion(
            test_db, user_id=d["student"].id, session=sess, multiplier=1.0
        )
        assert log.xp_earned == 200, "Config base_xp must override category default (50)"

    def test_19h_match_category_default_xp_is_100(
        self, test_db: Session, hierarchy_data: dict
    ):
        """EventCategory.MATCH → 100 XP when no session_reward_config set."""
        d = hierarchy_data
        sess = _make_session(test_db, d["child_sem"].id, EventCategory.MATCH)
        test_db.commit()

        log = award_session_completion(
            test_db, user_id=d["student"].id, session=sess
        )
        assert log.xp_earned == 100

    def test_19i_training_category_default_xp_is_50(
        self, test_db: Session, hierarchy_data: dict
    ):
        """EventCategory.TRAINING → 50 XP when no session_reward_config set."""
        d = hierarchy_data
        sess = _make_session(test_db, d["child_sem"].id, EventCategory.TRAINING)
        test_db.commit()

        log = award_session_completion(
            test_db, user_id=d["student"].id, session=sess
        )
        assert log.xp_earned == 50
