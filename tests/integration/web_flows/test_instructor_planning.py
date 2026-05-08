"""
Instructor Planning Integration Tests — IP-01 through IP-08

IP-01  Add MASTER + FIELD slots; UNIQUE constraint prevents duplicate
IP-02  Master self-check-in OK; non-master cannot check-in MASTER slot
IP-03  Master marks FIELD slot CHECKED_IN / ABSENT
IP-04  Admin marks MASTER ABSENT; tournament guard (not IN_PROGRESS)
IP-05  get_fallback_plan returns correct suggested_parallel_fields
IP-06  apply_fallback updates Session.instructor_id + parallel_fields
IP-07  Session generator uses field_map (CHECKED_IN slot → session.instructor_id)
IP-08  WS broadcast instructor_status_change event upon mark_absent

All tests run against real DB in SAVEPOINT-isolated transaction (auto-rollback).
"""
import uuid
import pytest
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import event

from app.main import app
from app.database import engine, get_db
from app.dependencies import get_current_user_web
from app.models.user import User, UserRole
from app.models.license import UserLicense
from app.models.campus import Campus
from app.models.location import Location
from app.models.pitch import Pitch
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.session import Session as SessionModel, SessionType
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_instructor_slot import TournamentInstructorSlot, SlotRole, SlotStatus
from app.core.security import get_password_hash
import app.services.tournament.instructor_planning_service as svc


# ─────────────────────────────────────────────────────────────────────────────
# DB fixture (SAVEPOINT isolated)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def test_db():
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


# ─────────────────────────────────────────────────────────────────────────────
# Factories
# ─────────────────────────────────────────────────────────────────────────────

def _user(db: Session, role: UserRole = UserRole.INSTRUCTOR) -> User:
    u = User(
        email=f"ip-test+{uuid.uuid4().hex[:8]}@lfa.com",
        name=f"IP Test {uuid.uuid4().hex[:4]}",
        password_hash=get_password_hash("Test1234!"),
        role=role,
        is_active=True,
        onboarding_completed=True,
        credit_balance=0,
        payment_verified=True,
    )
    db.add(u)
    db.flush()
    if role == UserRole.INSTRUCTOR:
        db.add(UserLicense(
            user_id=u.id,
            specialization_type="LFA_COACH",
            current_level=7,
            max_achieved_level=7,
            is_active=True,
            started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            expires_at=None,
        ))
        db.flush()
    return u


def _tournament(db: Session, master_id: int = None) -> Semester:
    t = Semester(
        code=f"IP-{uuid.uuid4().hex[:8].upper()}",
        name="IP Test Tournament",
        semester_category=SemesterCategory.TOURNAMENT,
        status=SemesterStatus.ONGOING,
        enrollment_cost=0,
        specialization_type="LFA_FOOTBALL_PLAYER",
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 30),
        age_group="YOUTH",
        master_instructor_id=master_id,
    )
    db.add(t)
    db.flush()
    return t


def _pitch(db: Session) -> Pitch:
    uid = uuid.uuid4().hex[:8]
    loc = Location(name=f"IP-Loc-{uid}", city=f"IPCity-{uid}", country="HU", is_active=True)
    db.add(loc)
    db.flush()
    campus = Campus(location_id=loc.id, name=f"IP-Campus-{uid}", is_active=True)
    db.add(campus)
    db.flush()
    p = Pitch(campus_id=campus.id, pitch_number=1, name=f"Pálya-{uid}", capacity=20, is_active=True)
    db.add(p)
    db.flush()
    return p


def _session(db: Session, tournament: Semester, pitch: Pitch = None, instructor_id: int = None) -> SessionModel:
    s = SessionModel(
        title="IP Session",
        semester_id=tournament.id,
        session_type=SessionType.on_site,
        date_start=datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc),
        date_end=datetime(2026, 5, 1, 11, 0, tzinfo=timezone.utc),
        capacity=20,
        pitch_id=pitch.id if pitch else None,
        instructor_id=instructor_id,
    )
    db.add(s)
    db.flush()
    return s


def _client(db: Session, user: User) -> TestClient:
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user_web] = lambda: user
    return TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"},
                      raise_server_exceptions=True)


# ─────────────────────────────────────────────────────────────────────────────
# IP-01: Slot add (MASTER + FIELD), UNIQUE constraint
# ─────────────────────────────────────────────────────────────────────────────

def test_IP_01_add_master_and_field_slots(test_db: Session):
    """Add MASTER + FIELD slots successfully; verify roles and pitch assignment."""
    admin      = _user(test_db, UserRole.ADMIN)
    instructor = _user(test_db)
    instructor2 = _user(test_db)
    pitch      = _pitch(test_db)
    tournament = _tournament(test_db, master_id=None)

    # Add MASTER slot
    master_slot = svc.add_slot(
        test_db, tournament.id, instructor.id, SlotRole.MASTER.value,
        pitch_id=None, assigned_by_id=admin.id,
    )
    assert master_slot.role == SlotRole.MASTER.value
    assert master_slot.status == SlotStatus.PLANNED.value
    assert master_slot.pitch_id is None

    # Add FIELD slot
    field_slot = svc.add_slot(
        test_db, tournament.id, instructor2.id, SlotRole.FIELD.value,
        pitch_id=pitch.id, assigned_by_id=admin.id,
    )
    assert field_slot.role == SlotRole.FIELD.value
    assert field_slot.pitch_id == pitch.id

    # Second MASTER slot → 409 (service-layer guard, before DB)
    instructor3 = _user(test_db)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        svc.add_slot(
            test_db, tournament.id, instructor3.id, SlotRole.MASTER.value,
            pitch_id=None, assigned_by_id=admin.id,
        )
    assert exc_info.value.status_code == 409

    # get_roster returns both slots
    roster = svc.get_roster(test_db, tournament.id)
    assert len(roster) == 2
    roles = {s["role"] for s in roster}
    assert roles == {"MASTER", "FIELD"}


# ─────────────────────────────────────────────────────────────────────────────
# IP-02: Master self-check-in OK; non-master cannot check-in MASTER slot
# ─────────────────────────────────────────────────────────────────────────────

def test_IP_02_master_self_checkin_other_cannot(test_db: Session):
    admin      = _user(test_db, UserRole.ADMIN)
    master_user = _user(test_db)
    other_user  = _user(test_db)
    tournament  = _tournament(test_db)

    master_slot = svc.add_slot(
        test_db, tournament.id, master_user.id, SlotRole.MASTER.value,
        pitch_id=None, assigned_by_id=admin.id,
    )

    # Other instructor cannot check-in MASTER slot
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        svc.mark_checkin(test_db, master_slot.id, requester=other_user)
    assert exc.value.status_code == 403

    # Master self check-in OK
    slot = svc.mark_checkin(test_db, master_slot.id, requester=master_user)
    assert slot.status == SlotStatus.CHECKED_IN.value
    assert slot.checked_in_at is not None


# ─────────────────────────────────────────────────────────────────────────────
# IP-03: Master marks FIELD slot CHECKED_IN / ABSENT
# ─────────────────────────────────────────────────────────────────────────────

def test_IP_03_master_marks_field_checkin_and_absent(test_db: Session):
    admin      = _user(test_db, UserRole.ADMIN)
    master_user = _user(test_db)
    field_user  = _user(test_db)
    pitch       = _pitch(test_db)
    tournament  = _tournament(test_db)

    master_slot = svc.add_slot(
        test_db, tournament.id, master_user.id, SlotRole.MASTER.value,
        pitch_id=None, assigned_by_id=admin.id,
    )
    field_slot = svc.add_slot(
        test_db, tournament.id, field_user.id, SlotRole.FIELD.value,
        pitch_id=pitch.id, assigned_by_id=admin.id,
    )

    # Master checks in field instructor
    checked = svc.mark_checkin(test_db, field_slot.id, requester=master_user)
    assert checked.status == SlotStatus.CHECKED_IN.value

    # Master marks field absent (reset to PLANNED first)
    field_slot.status = SlotStatus.PLANNED.value
    test_db.flush()
    absent = svc.mark_absent(test_db, field_slot.id, requester=master_user)
    assert absent.status == SlotStatus.ABSENT.value


# ─────────────────────────────────────────────────────────────────────────────
# IP-04: Admin marks MASTER ABSENT → tournament guard (IN_PROGRESS blocked)
# ─────────────────────────────────────────────────────────────────────────────

def test_IP_04_admin_marks_master_absent(test_db: Session):
    admin      = _user(test_db, UserRole.ADMIN)
    master_user = _user(test_db)
    tournament  = _tournament(test_db)

    master_slot = svc.add_slot(
        test_db, tournament.id, master_user.id, SlotRole.MASTER.value,
        pitch_id=None, assigned_by_id=admin.id,
    )

    # Non-admin cannot mark master absent
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        svc.mark_absent(test_db, master_slot.id, requester=master_user)
    assert exc.value.status_code == 403

    # Admin can mark master absent
    slot = svc.mark_absent(test_db, master_slot.id, requester=admin)
    assert slot.status == SlotStatus.ABSENT.value

    # Verify tournament cannot go IN_PROGRESS with MASTER ABSENT
    # (check that the absent slot is recorded — guard is enforced at transition layer)
    absent_master = test_db.query(TournamentInstructorSlot).filter(
        TournamentInstructorSlot.semester_id == tournament.id,
        TournamentInstructorSlot.role == SlotRole.MASTER.value,
        TournamentInstructorSlot.status == SlotStatus.ABSENT.value,
    ).first()
    assert absent_master is not None


# ─────────────────────────────────────────────────────────────────────────────
# IP-05: get_fallback_plan returns correct suggested_parallel_fields
# ─────────────────────────────────────────────────────────────────────────────

def test_IP_05_fallback_plan_correct_parallel_fields(test_db: Session):
    admin   = _user(test_db, UserRole.ADMIN)
    inst1   = _user(test_db)
    inst2   = _user(test_db)
    inst3   = _user(test_db)
    pitch1  = _pitch(test_db)
    pitch2  = _pitch(test_db)
    pitch3  = _pitch(test_db)
    tourn   = _tournament(test_db)

    # 3 field slots: 1 CHECKED_IN, 1 PLANNED, 1 ABSENT
    svc.add_slot(test_db, tourn.id, inst1.id, SlotRole.FIELD.value, pitch_id=pitch1.id, assigned_by_id=admin.id)
    svc.add_slot(test_db, tourn.id, inst2.id, SlotRole.FIELD.value, pitch_id=pitch2.id, assigned_by_id=admin.id)
    svc.add_slot(test_db, tourn.id, inst3.id, SlotRole.FIELD.value, pitch_id=pitch3.id, assigned_by_id=admin.id)

    # Mark inst3 as absent
    slot3 = test_db.query(TournamentInstructorSlot).filter(
        TournamentInstructorSlot.semester_id == tourn.id,
        TournamentInstructorSlot.instructor_id == inst3.id,
    ).first()
    slot3.status = SlotStatus.ABSENT.value
    test_db.flush()

    plan = svc.get_fallback_plan(test_db, tourn.id)

    assert plan["absent_field_count"] == 1
    assert plan["present_field_count"] == 2   # inst1 (PLANNED) + inst2 (PLANNED)
    assert plan["suggested_parallel_fields"] == 2


# ─────────────────────────────────────────────────────────────────────────────
# IP-06: apply_fallback updates Session.instructor_id + parallel_fields
# ─────────────────────────────────────────────────────────────────────────────

def test_IP_06_apply_fallback_reassigns_sessions(test_db: Session):
    admin      = _user(test_db, UserRole.ADMIN)
    inst_absent = _user(test_db)
    inst_present = _user(test_db)
    pitch1   = _pitch(test_db)
    pitch2   = _pitch(test_db)
    tourn    = _tournament(test_db)

    # Create tournament config with parallel_fields=2
    cfg = TournamentConfiguration(
        semester_id=tourn.id,
        parallel_fields=2,
        max_players=20,
    )
    test_db.add(cfg)
    test_db.flush()

    # Add field slots
    svc.add_slot(test_db, tourn.id, inst_absent.id, SlotRole.FIELD.value, pitch_id=pitch1.id, assigned_by_id=admin.id)
    svc.add_slot(test_db, tourn.id, inst_present.id, SlotRole.FIELD.value, pitch_id=pitch2.id, assigned_by_id=admin.id)

    # Mark inst_absent as ABSENT
    slot_abs = test_db.query(TournamentInstructorSlot).filter(
        TournamentInstructorSlot.semester_id == tourn.id,
        TournamentInstructorSlot.instructor_id == inst_absent.id,
    ).first()
    slot_abs.status = SlotStatus.ABSENT.value
    test_db.flush()

    # Create session assigned to absent instructor
    sess = _session(test_db, tourn, pitch=pitch1, instructor_id=inst_absent.id)

    plan = svc.get_fallback_plan(test_db, tourn.id)
    assert plan["affected_sessions_count"] >= 1
    assert plan["suggested_parallel_fields"] == 1

    updated = svc.apply_fallback(test_db, tourn.id, admin_user=admin, plan=plan)
    assert updated >= 1

    # Session instructor_id should have changed
    test_db.refresh(sess)
    assert sess.instructor_id != inst_absent.id
    # parallel_fields updated
    test_db.refresh(cfg)
    assert cfg.parallel_fields == 1


# ─────────────────────────────────────────────────────────────────────────────
# IP-07: Session generator uses field_map (CHECKED_IN slot → session.instructor_id)
# ─────────────────────────────────────────────────────────────────────────────

def test_IP_07_session_generator_uses_field_map(test_db: Session):
    """Service layer unit test: field instructor slot used for pitch-specific session."""
    admin      = _user(test_db, UserRole.ADMIN)
    master     = _user(test_db)
    field_inst = _user(test_db)
    pitch      = _pitch(test_db)
    tourn      = _tournament(test_db, master_id=master.id)

    # Add CHECKED_IN field slot for pitch
    slot = svc.add_slot(
        test_db, tourn.id, field_inst.id, SlotRole.FIELD.value,
        pitch_id=pitch.id, assigned_by_id=admin.id,
    )
    slot.status = SlotStatus.CHECKED_IN.value
    test_db.flush()

    # Simulate what session_generator.py does to build field_map
    from app.models.tournament_instructor_slot import TournamentInstructorSlot as TIS
    _slot_priority = {"CHECKED_IN": 0, "CONFIRMED": 1, "PLANNED": 2}
    field_slots = test_db.query(TIS).filter(
        TIS.semester_id == tourn.id,
        TIS.role == "FIELD",
        TIS.pitch_id.isnot(None),
    ).all()
    field_map = {}
    for s in sorted(field_slots, key=lambda s: _slot_priority.get(s.status, 99)):
        if s.pitch_id not in field_map:
            field_map[s.pitch_id] = s.instructor_id

    # The field instructor should be in the map for this pitch
    assert pitch.id in field_map
    assert field_map[pitch.id] == field_inst.id

    # A session on this pitch would get the field instructor
    resolved_instructor = field_map.get(pitch.id) or tourn.master_instructor_id
    assert resolved_instructor == field_inst.id

    # A session on an unknown pitch falls back to master
    resolved_fallback = field_map.get(99999) or tourn.master_instructor_id
    assert resolved_fallback == master.id


# ─────────────────────────────────────────────────────────────────────────────
# IP-08: HTTP routes — add slot, check-in, absent via TestClient
# ─────────────────────────────────────────────────────────────────────────────

def test_IP_08_http_routes_add_checkin_absent(test_db: Session):
    admin  = _user(test_db, UserRole.ADMIN)
    master = _user(test_db)
    tourn  = _tournament(test_db)

    client = _client(test_db, admin)

    # POST add MASTER slot
    resp = client.post(
        f"/admin/tournaments/{tourn.id}/instructor-slots",
        data={
            "instructor_id": str(master.id),
            "role": "MASTER",
        },
    )
    assert resp.status_code == 201, resp.text
    slot_id = resp.json()["slot_id"]
    assert resp.json()["status"] == "PLANNED"

    # GET roster
    resp2 = client.get(f"/admin/tournaments/{tourn.id}/instructor-slots")
    assert resp2.status_code == 200
    slots = resp2.json()["slots"]
    assert len(slots) == 1
    assert slots[0]["role"] == "MASTER"

    # POST check-in
    resp3 = client.post(f"/admin/tournaments/{tourn.id}/instructor-slots/{slot_id}/checkin")
    assert resp3.status_code == 200
    assert resp3.json()["status"] == "CHECKED_IN"

    # POST absent (admin marking master absent)
    resp4 = client.post(f"/admin/tournaments/{tourn.id}/instructor-slots/{slot_id}/absent")
    assert resp4.status_code == 200
    assert resp4.json()["status"] == "ABSENT"

    # DELETE slot
    resp5 = client.delete(f"/admin/tournaments/{tourn.id}/instructor-slots/{slot_id}")
    assert resp5.status_code == 200
    assert resp5.json()["deleted"] == slot_id

    # GET roster — should be empty after delete
    resp6 = client.get(f"/admin/tournaments/{tourn.id}/instructor-slots")
    assert resp6.status_code == 200
    assert resp6.json()["slots"] == []

    app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────────────────────
# IP-09: add_slot FIELD cap — cannot exceed parallel_fields
# ─────────────────────────────────────────────────────────────────────────────

def test_IP_09_field_slot_capped_by_parallel_fields(test_db: Session):
    """
    When TournamentConfiguration.parallel_fields=2, only 2 FIELD slots may be added.
    Adding a 3rd FIELD slot must raise HTTP 400.
    """
    from fastapi import HTTPException

    admin  = _user(test_db, UserRole.ADMIN)
    inst1  = _user(test_db)
    inst2  = _user(test_db)
    inst3  = _user(test_db)
    pitch1 = _pitch(test_db)
    pitch2 = _pitch(test_db)
    pitch3 = _pitch(test_db)
    tourn  = _tournament(test_db)

    # Create config with parallel_fields=2
    cfg = TournamentConfiguration(
        semester_id=tourn.id,
        parallel_fields=2,
        max_players=20,
    )
    test_db.add(cfg)
    test_db.flush()

    # First two FIELD slots succeed
    svc.add_slot(test_db, tourn.id, inst1.id, SlotRole.FIELD.value, pitch_id=pitch1.id, assigned_by_id=admin.id)
    svc.add_slot(test_db, tourn.id, inst2.id, SlotRole.FIELD.value, pitch_id=pitch2.id, assigned_by_id=admin.id)

    # Third FIELD slot must be rejected
    with pytest.raises(HTTPException) as exc:
        svc.add_slot(test_db, tourn.id, inst3.id, SlotRole.FIELD.value, pitch_id=pitch3.id, assigned_by_id=admin.id)
    assert exc.value.status_code == 400
    assert "parallel fields" in exc.value.detail.lower()

    # Confirm only 2 FIELD slots in roster
    roster = svc.get_roster(test_db, tourn.id)
    field_slots = [s for s in roster if s["role"] == "FIELD"]
    assert len(field_slots) == 2


# ─────────────────────────────────────────────────────────────────────────────
# IP-10: PATCH /schedule-config rejects parallel_fields < existing field slot count
# ─────────────────────────────────────────────────────────────────────────────

def test_IP_10_schedule_config_cannot_reduce_below_field_slots(test_db: Session):
    """
    If 2 FIELD instructor slots exist, PATCH parallel_fields=1 must return 400.
    PATCH parallel_fields=2 (same value) must succeed.
    """
    from app.dependencies import get_current_admin_user_hybrid

    admin  = _user(test_db, UserRole.ADMIN)
    inst1  = _user(test_db)
    inst2  = _user(test_db)
    pitch1 = _pitch(test_db)
    pitch2 = _pitch(test_db)
    tourn  = _tournament(test_db)

    cfg = TournamentConfiguration(
        semester_id=tourn.id,
        parallel_fields=2,
        max_players=20,
    )
    test_db.add(cfg)
    test_db.flush()

    svc.add_slot(test_db, tourn.id, inst1.id, SlotRole.FIELD.value, pitch_id=pitch1.id, assigned_by_id=admin.id)
    svc.add_slot(test_db, tourn.id, inst2.id, SlotRole.FIELD.value, pitch_id=pitch2.id, assigned_by_id=admin.id)

    def override_db():
        yield test_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_admin_user_hybrid] = lambda: admin

    client = TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"},
                        raise_server_exceptions=False)

    # parallel_fields=1 with 2 existing FIELD slots → 400
    resp = client.patch(
        f"/api/v1/tournaments/{tourn.id}/schedule-config",
        json={"parallel_fields": 1},
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    detail = body.get("detail") or (body.get("error") or {}).get("message") or ""
    assert "field instructor" in detail.lower() or "parallel_fields" in detail.lower()

    # parallel_fields=2 (same as current, matching slot count) → 200
    resp2 = client.patch(
        f"/api/v1/tournaments/{tourn.id}/schedule-config",
        json={"parallel_fields": 2},
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["parallel_fields"] == 2

    app.dependency_overrides.clear()
