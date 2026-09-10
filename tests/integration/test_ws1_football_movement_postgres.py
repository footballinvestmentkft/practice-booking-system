"""Disposable-PostgreSQL evidence for WS1 movement invariants."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from threading import Barrier
from uuid import uuid4

import pytest

from app.database import SessionLocal
from app.models.license import UserLicense
from app.models.semester import Semester
from app.models.semester_enrollment import SemesterEnrollment
from app.models.user import User, UserRole
from app.models.ws1_domain import FootballCategoryMovementEvent, FootballSeasonCategoryAssignment
from app.services.football_category_movement_service import (
    FootballMovementAuthorizationError,
    FootballMovementConcurrencyError,
    FootballMovementError,
    FootballMovementReplayConflict,
    move_football_category,
)
from app.services.program_eligibility_service import record_guardian_consent


pytestmark = pytest.mark.postgres


def _user(db, role: UserRole, *, dob: date | None = None) -> User:
    token = uuid4().hex
    user = User(
        name=f"WS1 {role.value}",
        email=f"ws1-{token}@ws1.example.com",
        password_hash="test-only-hash",
        role=role,
        is_active=True,
        date_of_birth=datetime.combine(dob, datetime.min.time()) if dob else datetime(1990, 1, 1),
        credit_balance=0,
    )
    db.add(user)
    db.flush()
    return user


def _license(db, user: User, program: str, level: int = 1) -> UserLicense:
    row = UserLicense(
        user_id=user.id,
        specialization_type=program,
        canonical_program_id=program,
        current_level=level,
        max_achieved_level=level,
        started_at=datetime.now(timezone.utc),
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _scenario(*, dob: date, master_role: UserRole = UserRole.ADMIN, coach_level: int = 8):
    db = SessionLocal()
    token = uuid4().hex[:10]
    player = _user(db, UserRole.STUDENT, dob=dob)
    record_guardian_consent(db, user=player, guardian_name="WS1 Guardian")
    player_license = _license(db, player, "LFA_FOOTBALL_PLAYER")
    actor = _user(db, master_role)
    if master_role is UserRole.INSTRUCTOR:
        _license(db, actor, "LFA_COACH", coach_level)
    semester = Semester(
        code=f"WS1-{token}",
        name="WS1 disposable movement",
        start_date=date(2026, 7, 1),
        end_date=date(2027, 6, 30),
        specialization_type="LFA_PLAYER_YOUTH",
        age_group="YOUTH",
        master_instructor_id=actor.id,
    )
    db.add(semester)
    db.flush()
    enrollment = SemesterEnrollment(
        user_id=player.id,
        semester_id=semester.id,
        user_license_id=player_license.id,
        is_active=True,
        age_category="PRE",
    )
    db.add(enrollment)
    db.commit()
    ids = player.id, actor.id, enrollment.id
    db.close()
    return ids


def test_movement_replay_is_exactly_once_and_audited():
    player_id, actor_id, enrollment_id = _scenario(dob=date(2013, 7, 1))
    db = SessionLocal()
    actor = db.get(User, actor_id)
    result = move_football_category(
        db,
        enrollment_id=enrollment_id,
        actor=actor,
        target_category="YOUTH",
        expected_version=1,
        base_participation_retained=True,
        reason="Target-qualified development assignment",
        idempotency_key=f"ws1-replay-{uuid4().hex}",
        on_date=date(2026, 9, 9),
    )
    key = result.event.idempotency_key
    db.commit()
    event_id = result.event.id

    replay = move_football_category(
        db,
        enrollment_id=enrollment_id,
        actor=actor,
        target_category="YOUTH",
        expected_version=1,
        base_participation_retained=True,
        reason="Target-qualified development assignment",
        idempotency_key=key,
        on_date=date(2026, 9, 9),
    )
    assert replay.created is False
    assert replay.event.id == event_id
    assert replay.assignment.season_base_category == "PRE"
    assert replay.assignment.effective_category == "YOUTH"
    assert replay.assignment.version == 2
    assert db.query(FootballCategoryMovementEvent).filter_by(idempotency_key=key).count() == 1
    db.close()


def test_replay_payload_conflict_is_rejected():
    _, actor_id, enrollment_id = _scenario(dob=date(2013, 7, 1))
    db = SessionLocal()
    actor = db.get(User, actor_id)
    key = f"ws1-conflict-{uuid4().hex}"
    move_football_category(
        db, enrollment_id=enrollment_id, actor=actor, target_category="YOUTH",
        expected_version=1, base_participation_retained=False, reason="First", idempotency_key=key,
        on_date=date(2026, 9, 9),
    )
    db.commit()
    with pytest.raises(FootballMovementReplayConflict):
        move_football_category(
            db, enrollment_id=enrollment_id, actor=actor, target_category="YOUTH",
            expected_version=1, base_participation_retained=True, reason="Different", idempotency_key=key,
            on_date=date(2026, 9, 9),
        )
    db.rollback()
    db.close()


def test_head_authorized_assistant_and_self_denied():
    _, head_id, enrollment_id = _scenario(
        dob=date(2013, 7, 1), master_role=UserRole.INSTRUCTOR, coach_level=4
    )
    db = SessionLocal()
    result = move_football_category(
        db, enrollment_id=enrollment_id, actor=db.get(User, head_id), target_category="YOUTH",
        expected_version=1, base_participation_retained=False, reason="Youth Head approval",
        idempotency_key=f"ws1-head-{uuid4().hex}", on_date=date(2026, 9, 9),
    )
    assert result.event.authorization_basis == "SEMESTER_HEAD_COACH"
    db.rollback()
    db.close()

    player_id, assistant_id, enrollment_id = _scenario(
        dob=date(2013, 7, 1), master_role=UserRole.INSTRUCTOR, coach_level=3
    )
    db = SessionLocal()
    with pytest.raises(FootballMovementAuthorizationError):
        move_football_category(
            db, enrollment_id=enrollment_id, actor=db.get(User, assistant_id), target_category="YOUTH",
            expected_version=1, base_participation_retained=False, reason="Assistant attempt",
            idempotency_key=f"ws1-assistant-{uuid4().hex}", on_date=date(2026, 9, 9),
        )
    db.rollback()
    with pytest.raises(FootballMovementAuthorizationError):
        move_football_category(
            db, enrollment_id=enrollment_id, actor=db.get(User, player_id), target_category="YOUTH",
            expected_version=1, base_participation_retained=False, reason="Self attempt",
            idempotency_key=f"ws1-self-{uuid4().hex}", on_date=date(2026, 9, 9),
        )
    db.rollback()
    db.close()


def test_downward_to_base_allowed_below_base_denied_and_rollback_atomic():
    player_id, actor_id, enrollment_id = _scenario(dob=date(2007, 7, 1))
    db = SessionLocal()
    actor = db.get(User, actor_id)
    promoted = move_football_category(
        db, enrollment_id=enrollment_id, actor=actor, target_category="PRO",
        expected_version=1, base_participation_retained=False, reason="Admin promotion",
        idempotency_key=f"ws1-pro-{uuid4().hex}", on_date=date(2026, 9, 9),
    )
    db.commit()
    assert promoted.assignment.season_base_category == "AMATEUR"

    down = move_football_category(
        db, enrollment_id=enrollment_id, actor=actor, target_category="AMATEUR",
        expected_version=2, base_participation_retained=True, reason="Admin revocation to base",
        idempotency_key=f"ws1-down-{uuid4().hex}", on_date=date(2026, 9, 9),
    )
    assert down.event.event_type == "REVOCATION"
    assert down.assignment.base_participation_retained is False
    db.commit()
    with pytest.raises(FootballMovementError):
        move_football_category(
            db, enrollment_id=enrollment_id, actor=actor, target_category="YOUTH",
            expected_version=3, base_participation_retained=False, reason="Below base",
            idempotency_key=f"ws1-below-{uuid4().hex}", on_date=date(2026, 9, 9),
        )
    db.rollback()

    move_football_category(
        db, enrollment_id=enrollment_id, actor=actor, target_category="PRO",
        expected_version=3, base_participation_retained=False, reason="Rollback proof",
        idempotency_key=f"ws1-rollback-{uuid4().hex}", on_date=date(2026, 9, 9),
    )
    db.rollback()
    persisted = db.query(FootballSeasonCategoryAssignment).filter_by(player_user_id=player_id).one()
    assert (persisted.effective_category, persisted.version) == ("AMATEUR", 3)
    assert db.query(FootballCategoryMovementEvent).filter_by(reason="Rollback proof").count() == 0
    db.close()


def test_downward_movement_requires_admin_even_for_qualified_head():
    _, admin_id, enrollment_id = _scenario(dob=date(2007, 7, 1))
    db = SessionLocal()
    move_football_category(
        db, enrollment_id=enrollment_id, actor=db.get(User, admin_id), target_category="PRO",
        expected_version=1, base_participation_retained=False, reason="Admin promotion setup",
        idempotency_key=f"ws1-admin-up-{uuid4().hex}", on_date=date(2026, 9, 9),
    )
    db.commit()
    db.close()

    db = SessionLocal()
    enrollment = db.get(SemesterEnrollment, enrollment_id)
    head = _user(db, UserRole.INSTRUCTOR)
    _license(db, head, "LFA_COACH", 8)
    enrollment.semester.master_instructor_id = head.id
    db.commit()
    head_id = head.id
    db.close()

    db = SessionLocal()
    with pytest.raises(FootballMovementAuthorizationError):
        move_football_category(
            db, enrollment_id=enrollment_id, actor=db.get(User, head_id), target_category="AMATEUR",
            expected_version=2, base_participation_retained=False, reason="Head downward attempt",
            idempotency_key=f"ws1-head-down-{uuid4().hex}", on_date=date(2026, 9, 9),
        )
    db.rollback()
    db.close()


def test_concurrent_commands_allow_one_version_winner():
    player_id, actor_id, enrollment_id = _scenario(dob=date(2012, 7, 1))
    barrier = Barrier(2)

    def run(target: str):
        db = SessionLocal()
        try:
            actor = db.get(User, actor_id)
            barrier.wait(timeout=5)
            result = move_football_category(
                db, enrollment_id=enrollment_id, actor=actor, target_category=target,
                expected_version=1, base_participation_retained=False, reason=f"Concurrent {target}",
                idempotency_key=f"ws1-concurrent-{target}-{uuid4().hex}", on_date=date(2026, 9, 9),
            )
            db.commit()
            return result.event.id
        except FootballMovementConcurrencyError:
            db.rollback()
            return "VERSION_CONFLICT"
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(run, ["AMATEUR", "PRO"]))
    assert sum(value == "VERSION_CONFLICT" for value in outcomes) == 1
    db = SessionLocal()
    assignment = db.query(FootballSeasonCategoryAssignment).filter_by(player_user_id=player_id).one()
    assert assignment.version == 2
    assert db.query(FootballCategoryMovementEvent).filter_by(assignment_id=assignment.id).count() == 1
    db.close()
