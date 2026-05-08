"""
Readiness Validator Guard — Integration Tests
=============================================
Guards introduced to close three confirmed backend bypass gaps:

  GAP-1  CHECK_IN_OPEN blocked when Schedule Config is missing (SCHEDULE_CONFIG_MISSING).
  GAP-2  IN_PROGRESS blocked when Reward Config is missing (REWARD_CONFIG_MISSING).
  GAP-3  IN_PROGRESS session regen failure raises HTTP 400, not silent print.
  GAP-4  (tech debt) — not tested here; see readiness_validator.py TODO comment.

Tests:
  RV-01  missing_schedule_config_blocks_check_in_open
  RV-02  valid_schedule_config_allows_check_in_open
  RV-03  missing_reward_config_blocks_in_progress
  RV-04  regen_failure_blocks_in_progress
  RV-05  full_configured_tournament_lifecycle (happy path: CHECK_IN_OPEN → IN_PROGRESS)
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from app.database import engine
from app.main import app
from app.database import get_db
from app.dependencies import get_current_admin_user_hybrid
from app.models.campus import Campus
from app.models.license import UserLicense
from app.models.location import Location
from app.models.pitch import Pitch
from app.models.semester import Semester, SemesterCategory, SemesterStatus
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_reward_config import TournamentRewardConfig
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from app.models.session import Session as SessionModel, EventCategory
from app.models.sponsor import Sponsor
from app.models.tournament_achievement import TournamentParticipation
from app.models.tournament_ranking import TournamentRanking
from tests.factories.game_factory import TournamentFactory


# ── SAVEPOINT-isolated DB fixture ─────────────────────────────────────────────

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


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _make_admin(db: Session) -> User:
    u = User(
        email=f"rv-admin+{uuid.uuid4().hex[:8]}@lfa.com",
        name="RV Admin",
        password_hash=get_password_hash("admin123"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_instructor(db: Session) -> User:
    """Create an eligible instructor with LFA_COACH level-7 license (PRO threshold)."""
    u = User(
        email=f"rv-instructor+{uuid.uuid4().hex[:8]}@lfa.com",
        name="RV Instructor",
        password_hash=get_password_hash("admin123"),
        role=UserRole.INSTRUCTOR,
        is_active=True,
    )
    db.add(u)
    db.flush()
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


def _make_campus_with_pitch(db: Session) -> Campus:
    location = Location(
        name=f"RV Location {uuid.uuid4().hex[:6]}",
        city=f"RVCity-{uuid.uuid4().hex[:8]}",
        country="HU",
    )
    db.add(location)
    db.flush()
    campus = Campus(
        name=f"RV Campus {uuid.uuid4().hex[:6]}",
        location_id=location.id,
        is_active=True,
    )
    db.add(campus)
    db.flush()
    db.add(Pitch(
        campus_id=campus.id,
        pitch_number=1,
        name="RV Pálya A",
        capacity=22,
        is_active=True,
    ))
    db.flush()
    return campus


def _enroll_player(db: Session, tournament_id: int, checked_in: bool = False) -> User:
    """Create a player, add an LFA_FOOTBALL_PLAYER license, and enroll + approve them."""
    player = User(
        email=f"rv-player-{uuid.uuid4().hex[:8]}@lfa.com",
        name="RV Player",
        password_hash=get_password_hash("pw"),
        role=UserRole.STUDENT,
        is_active=True,
    )
    db.add(player)
    db.flush()
    lic = UserLicense(
        user_id=player.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        current_level=1,
        max_achieved_level=1,
        is_active=True,
        started_at=datetime.utcnow(),
        expires_at=None,
    )
    db.add(lic)
    db.flush()
    db.add(SemesterEnrollment(
        user_id=player.id,
        semester_id=tournament_id,
        user_license_id=lic.id,
        is_active=True,
        request_status=EnrollmentStatus.APPROVED,
        payment_verified=True,
        tournament_checked_in_at=datetime.utcnow() if checked_in else None,
    ))
    db.flush()
    return player


def _make_reward_config(db: Session, tournament_id: int) -> TournamentRewardConfig:
    rc = TournamentRewardConfig(
        semester_id=tournament_id,
        reward_policy_name="test",
        reward_config={
            "template_name": "Standard",
            "skill_mappings": [{"skill_key": "dribbling", "weight": 1.0, "enabled": True}],
            "first_place": {"credits": 100, "xp": 500},
            "participation": {"credits": 10, "xp": 50},
        },
    )
    db.add(rc)
    db.flush()
    return rc


def _make_tournament(
    db: Session,
    *,
    campus: Campus,
    instructor: User,
    tt,
    tournament_status: str,
    match_duration_minutes: int | None,
    break_duration_minutes: int | None,
    parallel_fields: int | None = None,
    with_reward_config: bool = False,
    sessions_generated: bool = False,
) -> Semester:
    """
    Create a tournament row + TournamentConfiguration directly in the DB.
    Does NOT go through the lifecycle API, so any status can be set directly.
    """
    code = f"RV-{uuid.uuid4().hex[:10].upper()}"
    t = Semester(
        code=code,
        name=f"RV Test Tournament {code[-6:]}",
        semester_category=SemesterCategory.TOURNAMENT,
        status=SemesterStatus.DRAFT,
        tournament_status=tournament_status,
        age_group="PRO",
        campus_id=campus.id,
        start_date=date.today() + timedelta(days=7),
        end_date=date.today() + timedelta(days=8),
        enrollment_cost=0,
        specialization_type="LFA_FOOTBALL_PLAYER",
        master_instructor_id=instructor.id,
    )
    db.add(t)
    db.flush()

    cfg = TournamentConfiguration(
        semester_id=t.id,
        tournament_type_id=tt.id,
        participant_type="INDIVIDUAL",
        number_of_rounds=1,
        max_players=32,
        match_duration_minutes=match_duration_minutes,
        break_duration_minutes=break_duration_minutes,
        parallel_fields=parallel_fields if parallel_fields is not None else 1,
        sessions_generated=sessions_generated,
    )
    db.add(cfg)
    db.flush()

    if with_reward_config:
        _make_reward_config(db, t.id)

    db.commit()
    db.expire_all()
    return db.query(Semester).filter(Semester.id == t.id).first()


def _admin_client(db: Session, admin: User) -> TestClient:
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin_user_hybrid] = lambda: admin
    return TestClient(app, raise_server_exceptions=False)


# ── RV-01 — GAP-1: missing schedule config blocks CHECK_IN_OPEN ───────────────


class TestMissingScheduleConfigBlocksCheckInOpen:
    """
    RV-01: ENROLLMENT_CLOSED → CHECK_IN_OPEN is blocked (HTTP 400) when
    match_duration_minutes or break_duration_minutes is NULL.
    Error code SCHEDULE_CONFIG_MISSING must appear in the response detail.
    Tournament status must remain ENROLLMENT_CLOSED.
    """

    def test_rv_01_null_match_and_break_blocked(self, test_db):
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        tt = TournamentFactory.ensure_tournament_type(
            test_db, code=f"rv01-{uuid.uuid4().hex[:6]}"
        )
        tournament = _make_tournament(
            test_db,
            campus=campus,
            instructor=instructor,
            tt=tt,
            tournament_status="ENROLLMENT_CLOSED",
            match_duration_minutes=None,   # ← missing
            break_duration_minutes=None,   # ← missing
        )
        _enroll_player(test_db, tournament.id)
        _enroll_player(test_db, tournament.id)

        client = _admin_client(test_db, admin)
        try:
            response = client.patch(
                f"/api/v1/tournaments/{tournament.id}/status",
                json={"new_status": "CHECK_IN_OPEN", "reason": "test"},
            )

            assert response.status_code == 400, (
                f"Expected 400, got {response.status_code}: {response.text}"
            )
            body = response.json()
            detail = body.get("detail") or body.get("error", {}).get("message", "")
            assert "SCHEDULE_CONFIG_MISSING" in detail, (
                f"Expected 'SCHEDULE_CONFIG_MISSING' in error, got: {detail!r}"
            )

            # Status must not have changed
            test_db.expire_all()
            t_after = test_db.query(Semester).filter(Semester.id == tournament.id).first()
            assert t_after.tournament_status == "ENROLLMENT_CLOSED", (
                f"Status changed unexpectedly to {t_after.tournament_status!r}"
            )
        finally:
            app.dependency_overrides.clear()

    def test_rv_01b_null_match_only_blocked(self, test_db):
        """Only match_duration_minutes missing → still blocked."""
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        tt = TournamentFactory.ensure_tournament_type(
            test_db, code=f"rv01b-{uuid.uuid4().hex[:6]}"
        )
        tournament = _make_tournament(
            test_db,
            campus=campus,
            instructor=instructor,
            tt=tt,
            tournament_status="ENROLLMENT_CLOSED",
            match_duration_minutes=None,  # ← missing
            break_duration_minutes=10,
        )
        _enroll_player(test_db, tournament.id)
        _enroll_player(test_db, tournament.id)

        client = _admin_client(test_db, admin)
        try:
            response = client.patch(
                f"/api/v1/tournaments/{tournament.id}/status",
                json={"new_status": "CHECK_IN_OPEN", "reason": "test"},
            )
            assert response.status_code == 400
            detail = (response.json().get("detail") or
                      response.json().get("error", {}).get("message", ""))
            assert "SCHEDULE_CONFIG_MISSING" in detail
        finally:
            app.dependency_overrides.clear()


# ── RV-02 — GAP-1 happy path: valid schedule config allows CHECK_IN_OPEN ──────


class TestValidScheduleConfigAllowsCheckInOpen:
    """
    RV-02: ENROLLMENT_CLOSED → CHECK_IN_OPEN succeeds when schedule config is set,
    instructor is eligible, and enough players are enrolled.
    """

    def test_rv_02_check_in_open_succeeds(self, test_db):
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        # Must use a known code ("knockout") — session generator dispatches by code.
        tt = TournamentFactory.ensure_tournament_type(test_db, code="knockout")
        tournament = _make_tournament(
            test_db,
            campus=campus,
            instructor=instructor,
            tt=tt,
            tournament_status="ENROLLMENT_CLOSED",
            match_duration_minutes=60,   # ← explicitly set
            break_duration_minutes=10,   # ← explicitly set
            parallel_fields=1,
        )
        # Enroll 4 players — safe margin above min_players for knockout type.
        for _ in range(4):
            _enroll_player(test_db, tournament.id)

        client = _admin_client(test_db, admin)
        try:
            response = client.patch(
                f"/api/v1/tournaments/{tournament.id}/status",
                json={"new_status": "CHECK_IN_OPEN", "reason": "test"},
            )

            # Must NOT be blocked by schedule config or instructor guard.
            if response.status_code == 400:
                detail = (response.json().get("detail") or
                          response.json().get("error", {}).get("message", ""))
                assert "SCHEDULE_CONFIG_MISSING" not in detail, (
                    f"Schedule config guard incorrectly blocked a configured tournament: {detail!r}"
                )
                assert "INSTRUCTOR" not in detail.upper() or "INSTRUCTOR_ELIGIBILITY" not in detail, (
                    f"Instructor guard incorrectly blocked: {detail!r}"
                )

            # Successful transition returns 200
            assert response.status_code == 200, (
                f"Expected 200 (CHECK_IN_OPEN), got {response.status_code}: {response.text}"
            )

            test_db.expire_all()
            t_after = test_db.query(Semester).filter(Semester.id == tournament.id).first()
            assert t_after.tournament_status == "CHECK_IN_OPEN"
        finally:
            app.dependency_overrides.clear()


# ── RV-03 — GAP-2: missing reward config blocks IN_PROGRESS ───────────────────


class TestMissingRewardConfigBlocksInProgress:
    """
    RV-03: CHECK_IN_OPEN → IN_PROGRESS is blocked (HTTP 400) when reward_config_obj
    is None (no TournamentRewardConfig row linked).
    Error code REWARD_CONFIG_MISSING must appear in the response detail.
    """

    def test_rv_03_no_reward_config_blocked(self, test_db):
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        tt = TournamentFactory.ensure_tournament_type(
            test_db, code=f"rv03-{uuid.uuid4().hex[:6]}"
        )
        # Tournament at CHECK_IN_OPEN, no reward config, sessions_generated=True
        # (so regen block is skipped — isolates GAP-2 from GAP-3)
        tournament = _make_tournament(
            test_db,
            campus=campus,
            instructor=instructor,
            tt=tt,
            tournament_status="CHECK_IN_OPEN",
            match_duration_minutes=60,
            break_duration_minutes=10,
            parallel_fields=1,
            with_reward_config=False,   # ← missing
            sessions_generated=True,    # prevents regen trigger
        )
        _enroll_player(test_db, tournament.id)
        _enroll_player(test_db, tournament.id)

        client = _admin_client(test_db, admin)
        try:
            response = client.patch(
                f"/api/v1/tournaments/{tournament.id}/status",
                json={"new_status": "IN_PROGRESS", "reason": "test"},
            )

            assert response.status_code == 400, (
                f"Expected 400, got {response.status_code}: {response.text}"
            )
            body = response.json()
            detail = body.get("detail") or body.get("error", {}).get("message", "")
            assert "REWARD_CONFIG_MISSING" in detail, (
                f"Expected 'REWARD_CONFIG_MISSING' in error, got: {detail!r}"
            )
        finally:
            app.dependency_overrides.clear()


# ── RV-04 — GAP-3: session regen failure blocks IN_PROGRESS ───────────────────


class TestRegenFailureBlocksInProgress:
    """
    RV-04: When session regeneration fails at IN_PROGRESS, the endpoint must
    return HTTP 400 — not silently succeed.

    Setup: INDIVIDUAL_RANKING tournament (sessions_generated=False forces regen),
    reward config set (GAP-2 passes), generator mocked to return failure.
    """

    def test_rv_04_regen_failure_returns_400(self, test_db):
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)

        # Use a tournament type with HEAD_TO_HEAD format; sessions_generated=False
        # triggers regen at IN_PROGRESS because has_checkins check returns False
        # for an empty DB — but we'll force regen via INDIVIDUAL_RANKING path instead
        # (no tournament_type_id → format defaults to INDIVIDUAL_RANKING, needs_regen=True).
        tt = TournamentFactory.ensure_tournament_type(
            test_db, code=f"rv04-{uuid.uuid4().hex[:6]}"
        )
        # Create WITHOUT tournament_type_id so format resolves to INDIVIDUAL_RANKING
        code = f"RV04-{uuid.uuid4().hex[:8].upper()}"
        t = Semester(
            code=code,
            name=f"RV-04 Regen Fail {code[-6:]}",
            semester_category=SemesterCategory.TOURNAMENT,
            status=SemesterStatus.DRAFT,
            tournament_status="CHECK_IN_OPEN",
            age_group="PRO",
            campus_id=campus.id,
            start_date=date.today() + timedelta(days=7),
            end_date=date.today() + timedelta(days=8),
            enrollment_cost=0,
            specialization_type="LFA_FOOTBALL_PLAYER",
            master_instructor_id=instructor.id,
        )
        test_db.add(t)
        test_db.flush()

        # No tournament_type_id → format = INDIVIDUAL_RANKING
        test_db.add(TournamentConfiguration(
            semester_id=t.id,
            tournament_type_id=None,
            participant_type="INDIVIDUAL",
            number_of_rounds=1,
            max_players=32,
            match_duration_minutes=60,
            break_duration_minutes=10,
            parallel_fields=1,
            sessions_generated=False,  # triggers regen at IN_PROGRESS
        ))
        test_db.flush()
        _make_reward_config(test_db, t.id)  # GAP-2 must pass
        test_db.commit()
        test_db.expire_all()
        tournament_id = t.id

        # Enroll 2 players (status_validator IN_PROGRESS min check: fallback=2)
        _enroll_player(test_db, tournament_id)
        _enroll_player(test_db, tournament_id)
        test_db.commit()

        client = _admin_client(test_db, admin)
        try:
            # Mock TournamentSessionGenerator so can_generate passes but generate fails
            with patch("app.services.tournament_session_generator.TournamentSessionGenerator") as MockGen:
                mock_instance = MockGen.return_value
                mock_instance.can_generate_sessions.return_value = (True, "ok")
                mock_instance.generate_sessions.return_value = (
                    False, "simulated regen failure for RV-04", []
                )

                response = client.patch(
                    f"/api/v1/tournaments/{tournament_id}/status",
                    json={"new_status": "IN_PROGRESS", "reason": "test"},
                )

            assert response.status_code == 400, (
                f"Expected 400 (regen failure must block), got {response.status_code}: {response.text}"
            )
            body = response.json()
            detail = body.get("detail") or body.get("error", {}).get("message", "")
            assert "regenerat" in detail.lower() or "failed" in detail.lower(), (
                f"Expected regen failure message, got: {detail!r}"
            )
        finally:
            app.dependency_overrides.clear()


# ── RV-05 — Happy path: full lifecycle CHECK_IN_OPEN → IN_PROGRESS ─────────────


class TestFullConfiguredTournamentLifecycle:
    """
    RV-05: Happy path — ENROLLMENT_CLOSED → CHECK_IN_OPEN → IN_PROGRESS both
    succeed when schedule config, reward config, eligible instructor, and enough
    enrolled players are all present.

    IN_PROGRESS uses HEAD_TO_HEAD with sessions_generated=True (set by CHECK_IN_OPEN
    transition) and no check-ins, so regen is skipped — the reward config snapshot
    block runs and succeeds.
    """

    def test_rv_05_full_lifecycle_succeeds(self, test_db):
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        # Must use a known code ("knockout") — session generator dispatches by code.
        tt = TournamentFactory.ensure_tournament_type(test_db, code="knockout")
        tournament = _make_tournament(
            test_db,
            campus=campus,
            instructor=instructor,
            tt=tt,
            tournament_status="ENROLLMENT_CLOSED",
            match_duration_minutes=60,
            break_duration_minutes=10,
            parallel_fields=1,
            with_reward_config=True,    # reward config present for IN_PROGRESS
        )
        # Enroll 4 players — safe margin above min_players for knockout type.
        for _ in range(4):
            _enroll_player(test_db, tournament.id)

        client = _admin_client(test_db, admin)
        try:
            # Step 1: ENROLLMENT_CLOSED → CHECK_IN_OPEN
            r1 = client.patch(
                f"/api/v1/tournaments/{tournament.id}/status",
                json={"new_status": "CHECK_IN_OPEN", "reason": "open check-in"},
            )
            assert r1.status_code == 200, (
                f"CHECK_IN_OPEN failed: {r1.status_code}: {r1.text}"
            )
            test_db.expire_all()
            t1 = test_db.query(Semester).filter(Semester.id == tournament.id).first()
            assert t1.tournament_status == "CHECK_IN_OPEN"

            # Step 2: CHECK_IN_OPEN → IN_PROGRESS (no check-ins → regen skipped)
            r2 = client.patch(
                f"/api/v1/tournaments/{tournament.id}/status",
                json={"new_status": "IN_PROGRESS", "reason": "start tournament"},
            )
            assert r2.status_code == 200, (
                f"IN_PROGRESS failed: {r2.status_code}: {r2.text}"
            )
            test_db.expire_all()
            t2 = test_db.query(Semester).filter(Semester.id == tournament.id).first()
            assert t2.tournament_status == "IN_PROGRESS"
        finally:
            app.dependency_overrides.clear()


# ── Finalization hardening helpers ────────────────────────────────────────────


def _make_match_session(
    db: Session,
    tournament_id: int,
    session_status: str,
    instructor_id: int,
) -> SessionModel:
    """Create an auto-generated MATCH session with the given session_status."""
    from datetime import date, timedelta
    s = SessionModel(
        title=f"FH Match {uuid.uuid4().hex[:6]}",
        date_start=datetime.now(timezone.utc) + timedelta(days=1),
        date_end=datetime.now(timezone.utc) + timedelta(days=1, hours=2),
        semester_id=tournament_id,
        instructor_id=instructor_id,
        auto_generated=True,
        event_category=EventCategory.MATCH,
        session_status=session_status,
    )
    db.add(s)
    db.flush()
    return s


def _make_ranking(
    db: Session,
    tournament_id: int,
    user_id: int,
    rank: int = 1,
) -> TournamentRanking:
    r = TournamentRanking(
        tournament_id=tournament_id,
        user_id=user_id,
        participant_type="INDIVIDUAL",
        rank=rank,
        points=0,
    )
    db.add(r)
    db.flush()
    return r


def _make_participation(
    db: Session,
    tournament_id: int,
    user_id: int,
) -> TournamentParticipation:
    p = TournamentParticipation(
        user_id=user_id,
        semester_id=tournament_id,
        xp_awarded=0,
        credits_awarded=0,
        foot_context="neutral",
    )
    db.add(p)
    db.flush()
    return p


def _make_sponsor(db: Session, is_active: bool = True) -> Sponsor:
    s = Sponsor(
        name=f"FH Sponsor {uuid.uuid4().hex[:6]}",
        code=f"FHS-{uuid.uuid4().hex[:8].upper()}",
        is_active=is_active,
    )
    db.add(s)
    db.flush()
    return s


def _make_completed_tournament(
    db: Session,
    *,
    campus: Campus,
    instructor: User,
    tt,
    with_snapshot: bool = True,
) -> tuple[Semester, list[User]]:
    """
    Create a tournament at IN_PROGRESS with schedule config + reward config,
    enroll 4 players, and set tournament_status=COMPLETED.
    Returns (tournament, [player1..player4]).
    """
    tournament = _make_tournament(
        db,
        campus=campus,
        instructor=instructor,
        tt=tt,
        tournament_status="COMPLETED",
        match_duration_minutes=60,
        break_duration_minutes=10,
        parallel_fields=1,
        with_reward_config=True,
    )
    if with_snapshot:
        # Simulate the snapshot written by lifecycle at IN_PROGRESS entry.
        rc = tournament.reward_config_obj
        rc.reward_policy_snapshot = tournament.reward_config
        db.flush()
    players = [_enroll_player(db, tournament.id) for _ in range(4)]
    db.commit()
    db.expire_all()
    t = db.query(Semester).filter(Semester.id == tournament.id).first()
    return t, players


# ── FH-01 — scheduled MATCH session blocks COMPLETED ─────────────────────────


class TestScheduledMatchBlocksCompleted:
    """FH-01: IN_PROGRESS → COMPLETED is blocked when a MATCH session is still
    in session_status='scheduled'. Code SESSIONS_INCOMPLETE must appear."""

    def test_fh_01_scheduled_match_blocks_completed(self, test_db):
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        tt = TournamentFactory.ensure_tournament_type(test_db, code=f"fh01-{uuid.uuid4().hex[:6]}")

        tournament = _make_tournament(
            test_db,
            campus=campus, instructor=instructor, tt=tt,
            tournament_status="IN_PROGRESS",
            match_duration_minutes=60, break_duration_minutes=10,
            with_reward_config=True, sessions_generated=True,
        )
        player = _enroll_player(test_db, tournament.id)
        _make_match_session(test_db, tournament.id, session_status="scheduled", instructor_id=instructor.id)
        _make_ranking(test_db, tournament.id, player.id, rank=1)
        test_db.commit()

        client = _admin_client(test_db, admin)
        try:
            resp = client.patch(
                f"/api/v1/tournaments/{tournament.id}/status",
                json={"new_status": "COMPLETED", "reason": "test"},
            )
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
            body = resp.json()
            detail = body.get("detail") or body.get("error", {}).get("message", "")
            assert "SESSIONS_INCOMPLETE" in detail, f"Expected SESSIONS_INCOMPLETE in: {detail!r}"
            test_db.expire_all()
            t = test_db.query(Semester).filter(Semester.id == tournament.id).first()
            assert t.tournament_status == "IN_PROGRESS"
        finally:
            app.dependency_overrides.clear()


# ── FH-02 — in_progress MATCH session blocks COMPLETED ───────────────────────


class TestInProgressMatchBlocksCompleted:
    """FH-02: IN_PROGRESS → COMPLETED is blocked when a MATCH session is still
    in session_status='in_progress'. Code SESSIONS_INCOMPLETE must appear."""

    def test_fh_02_in_progress_match_blocks_completed(self, test_db):
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        tt = TournamentFactory.ensure_tournament_type(test_db, code=f"fh02-{uuid.uuid4().hex[:6]}")

        tournament = _make_tournament(
            test_db,
            campus=campus, instructor=instructor, tt=tt,
            tournament_status="IN_PROGRESS",
            match_duration_minutes=60, break_duration_minutes=10,
            with_reward_config=True, sessions_generated=True,
        )
        player = _enroll_player(test_db, tournament.id)
        _make_match_session(test_db, tournament.id, session_status="in_progress", instructor_id=instructor.id)
        _make_ranking(test_db, tournament.id, player.id, rank=1)
        test_db.commit()

        client = _admin_client(test_db, admin)
        try:
            resp = client.patch(
                f"/api/v1/tournaments/{tournament.id}/status",
                json={"new_status": "COMPLETED", "reason": "test"},
            )
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
            body = resp.json()
            detail = body.get("detail") or body.get("error", {}).get("message", "")
            assert "SESSIONS_INCOMPLETE" in detail, f"Expected SESSIONS_INCOMPLETE in: {detail!r}"
        finally:
            app.dependency_overrides.clear()


# ── FH-03 — incomplete rankings blocks COMPLETED ──────────────────────────────


class TestIncompleteRankingsBlocksCompleted:
    """FH-03: IN_PROGRESS → COMPLETED is blocked when ranking count < enrolled
    participant count. Code RANKINGS_INCOMPLETE must appear."""

    def test_fh_03_incomplete_rankings_blocks_completed(self, test_db):
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        tt = TournamentFactory.ensure_tournament_type(test_db, code=f"fh03-{uuid.uuid4().hex[:6]}")

        tournament = _make_tournament(
            test_db,
            campus=campus, instructor=instructor, tt=tt,
            tournament_status="IN_PROGRESS",
            match_duration_minutes=60, break_duration_minutes=10,
            with_reward_config=True, sessions_generated=True,
        )
        players = [_enroll_player(test_db, tournament.id) for _ in range(4)]
        # All sessions completed
        _make_match_session(test_db, tournament.id, session_status="completed", instructor_id=instructor.id)
        # Only 1 of 4 players ranked
        _make_ranking(test_db, tournament.id, players[0].id, rank=1)
        test_db.commit()

        client = _admin_client(test_db, admin)
        try:
            resp = client.patch(
                f"/api/v1/tournaments/{tournament.id}/status",
                json={"new_status": "COMPLETED", "reason": "test"},
            )
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
            body = resp.json()
            detail = body.get("detail") or body.get("error", {}).get("message", "")
            assert "RANKINGS_INCOMPLETE" in detail, f"Expected RANKINGS_INCOMPLETE in: {detail!r}"
        finally:
            app.dependency_overrides.clear()


# ── FH-happy — all sessions completed + full rankings → COMPLETED succeeds ────


class TestCompletedHappyPath:
    """FH-happy: all MATCH sessions completed + rankings cover all enrolled
    players → IN_PROGRESS → COMPLETED succeeds (HTTP 200)."""

    def test_fh_happy_completed_succeeds(self, test_db):
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        tt = TournamentFactory.ensure_tournament_type(test_db, code=f"fhhappy-{uuid.uuid4().hex[:6]}")

        tournament = _make_tournament(
            test_db,
            campus=campus, instructor=instructor, tt=tt,
            tournament_status="IN_PROGRESS",
            match_duration_minutes=60, break_duration_minutes=10,
            with_reward_config=True, sessions_generated=True,
        )
        players = [_enroll_player(test_db, tournament.id) for _ in range(4)]
        _make_match_session(test_db, tournament.id, session_status="completed", instructor_id=instructor.id)
        for i, p in enumerate(players):
            _make_ranking(test_db, tournament.id, p.id, rank=i + 1)
        test_db.commit()

        client = _admin_client(test_db, admin)
        try:
            resp = client.patch(
                f"/api/v1/tournaments/{tournament.id}/status",
                json={"new_status": "COMPLETED", "reason": "test"},
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            test_db.expire_all()
            t = test_db.query(Semester).filter(Semester.id == tournament.id).first()
            assert t.tournament_status == "COMPLETED"
        finally:
            app.dependency_overrides.clear()


# ── RD-01 — snapshot missing blocks REWARDS_DISTRIBUTED ──────────────────────


class TestSnapshotMissingBlocksRewardsDistributed:
    """RD-01: COMPLETED → REWARDS_DISTRIBUTED blocked when reward_policy_snapshot
    is None. Code SNAPSHOT_MISSING must appear."""

    def test_rd_01_snapshot_missing_blocks(self, test_db):
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        tt = TournamentFactory.ensure_tournament_type(test_db, code=f"rd01-{uuid.uuid4().hex[:6]}")

        # with_snapshot=False — reward_policy_snapshot remains None
        tournament, players = _make_completed_tournament(
            test_db, campus=campus, instructor=instructor, tt=tt, with_snapshot=False,
        )
        for i, p in enumerate(players):
            _make_ranking(test_db, tournament.id, p.id, rank=i + 1)
            _make_participation(test_db, tournament.id, p.id)
        test_db.commit()

        client = _admin_client(test_db, admin)
        try:
            resp = client.patch(
                f"/api/v1/tournaments/{tournament.id}/status",
                json={"new_status": "REWARDS_DISTRIBUTED", "reason": "test"},
            )
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
            body = resp.json()
            detail = body.get("detail") or body.get("error", {}).get("message", "")
            assert "SNAPSHOT_MISSING" in detail, f"Expected SNAPSHOT_MISSING in: {detail!r}"
        finally:
            app.dependency_overrides.clear()


# ── RD-02 — no participation records blocks REWARDS_DISTRIBUTED ───────────────


class TestNoParticipationBlocksRewardsDistributed:
    """RD-02: COMPLETED → REWARDS_DISTRIBUTED blocked when 0 TournamentParticipation
    rows exist. Code PARTICIPATION_RECORDS_MISSING must appear."""

    def test_rd_02_no_participation_blocks(self, test_db):
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        tt = TournamentFactory.ensure_tournament_type(test_db, code=f"rd02-{uuid.uuid4().hex[:6]}")

        tournament, players = _make_completed_tournament(
            test_db, campus=campus, instructor=instructor, tt=tt,
        )
        for i, p in enumerate(players):
            _make_ranking(test_db, tournament.id, p.id, rank=i + 1)
        # No TournamentParticipation rows
        test_db.commit()

        client = _admin_client(test_db, admin)
        try:
            resp = client.patch(
                f"/api/v1/tournaments/{tournament.id}/status",
                json={"new_status": "REWARDS_DISTRIBUTED", "reason": "test"},
            )
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
            body = resp.json()
            detail = body.get("detail") or body.get("error", {}).get("message", "")
            assert "PARTICIPATION_RECORDS_MISSING" in detail, (
                f"Expected PARTICIPATION_RECORDS_MISSING in: {detail!r}"
            )
        finally:
            app.dependency_overrides.clear()


# ── RD-03 — partial participation blocks REWARDS_DISTRIBUTED ──────────────────


class TestPartialParticipationBlocksRewardsDistributed:
    """RD-03: COMPLETED → REWARDS_DISTRIBUTED blocked when only 2 of 4 players
    have TournamentParticipation records. Code PARTICIPATION_INCOMPLETE must appear."""

    def test_rd_03_partial_participation_blocks(self, test_db):
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        tt = TournamentFactory.ensure_tournament_type(test_db, code=f"rd03-{uuid.uuid4().hex[:6]}")

        tournament, players = _make_completed_tournament(
            test_db, campus=campus, instructor=instructor, tt=tt,
        )
        for i, p in enumerate(players):
            _make_ranking(test_db, tournament.id, p.id, rank=i + 1)
        # Only 2 of 4 participation records
        _make_participation(test_db, tournament.id, players[0].id)
        _make_participation(test_db, tournament.id, players[1].id)
        test_db.commit()

        client = _admin_client(test_db, admin)
        try:
            resp = client.patch(
                f"/api/v1/tournaments/{tournament.id}/status",
                json={"new_status": "REWARDS_DISTRIBUTED", "reason": "test"},
            )
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
            body = resp.json()
            detail = body.get("detail") or body.get("error", {}).get("message", "")
            assert "PARTICIPATION_INCOMPLETE" in detail, (
                f"Expected PARTICIPATION_INCOMPLETE in: {detail!r}"
            )
        finally:
            app.dependency_overrides.clear()


# ── RD-04 — incomplete rankings blocks REWARDS_DISTRIBUTED ────────────────────


class TestIncompleteRankingsBlocksRewardsDistributed:
    """RD-04: COMPLETED → REWARDS_DISTRIBUTED blocked when ranking count < enrolled
    count (1 of 4 ranked). Code RANKINGS_INCOMPLETE must appear."""

    def test_rd_04_incomplete_rankings_blocks(self, test_db):
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        tt = TournamentFactory.ensure_tournament_type(test_db, code=f"rd04-{uuid.uuid4().hex[:6]}")

        tournament, players = _make_completed_tournament(
            test_db, campus=campus, instructor=instructor, tt=tt,
        )
        # Only 1 of 4 players ranked
        _make_ranking(test_db, tournament.id, players[0].id, rank=1)
        for p in players:
            _make_participation(test_db, tournament.id, p.id)
        test_db.commit()

        client = _admin_client(test_db, admin)
        try:
            resp = client.patch(
                f"/api/v1/tournaments/{tournament.id}/status",
                json={"new_status": "REWARDS_DISTRIBUTED", "reason": "test"},
            )
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
            body = resp.json()
            detail = body.get("detail") or body.get("error", {}).get("message", "")
            assert "RANKINGS_INCOMPLETE" in detail, f"Expected RANKINGS_INCOMPLETE in: {detail!r}"
        finally:
            app.dependency_overrides.clear()


# ── SP-01 — PROMOTION_EVENT without sponsor blocks CHECK_IN_OPEN ──────────────


class TestPromotionEventNoSponsorBlocksCheckInOpen:
    """SP-01: ENROLLMENT_CLOSED → CHECK_IN_OPEN blocked for PROMOTION_EVENT
    tournament when organizer_sponsor_id is None. Code PROMOTION_SPONSOR_MISSING."""

    def test_sp_01_no_sponsor_blocks(self, test_db):
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        tt = TournamentFactory.ensure_tournament_type(test_db, code=f"sp01-{uuid.uuid4().hex[:6]}")

        # Create PROMOTION_EVENT with no organizer_sponsor_id
        code = f"SP01-{uuid.uuid4().hex[:8].upper()}"
        t = Semester(
            code=code,
            name=f"SP-01 Promo Event {code[-6:]}",
            semester_category=SemesterCategory.PROMOTION_EVENT,
            status=SemesterStatus.DRAFT,
            tournament_status="ENROLLMENT_CLOSED",
            age_group="PRO",
            campus_id=campus.id,
            start_date=date.today() + timedelta(days=7),
            end_date=date.today() + timedelta(days=8),
            enrollment_cost=0,
            specialization_type="LFA_FOOTBALL_PLAYER",
            master_instructor_id=instructor.id,
            organizer_sponsor_id=None,  # ← missing
        )
        test_db.add(t)
        test_db.flush()
        test_db.add(TournamentConfiguration(
            semester_id=t.id,
            tournament_type_id=tt.id,
            participant_type="INDIVIDUAL",
            number_of_rounds=1,
            max_players=32,
            match_duration_minutes=60,
            break_duration_minutes=10,
            parallel_fields=1,
        ))
        test_db.flush()
        _enroll_player(test_db, t.id)
        _enroll_player(test_db, t.id)
        test_db.commit()
        test_db.expire_all()

        client = _admin_client(test_db, admin)
        try:
            resp = client.patch(
                f"/api/v1/tournaments/{t.id}/status",
                json={"new_status": "CHECK_IN_OPEN", "reason": "test"},
            )
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
            body = resp.json()
            detail = body.get("detail") or body.get("error", {}).get("message", "")
            assert "PROMOTION_SPONSOR_MISSING" in detail, (
                f"Expected PROMOTION_SPONSOR_MISSING in: {detail!r}"
            )
            test_db.expire_all()
            t_after = test_db.query(Semester).filter(Semester.id == t.id).first()
            assert t_after.tournament_status == "ENROLLMENT_CLOSED"
        finally:
            app.dependency_overrides.clear()


# ── SP-02 — PROMOTION_EVENT with inactive sponsor blocks CHECK_IN_OPEN ─────────


class TestPromotionEventInactiveSponsorBlocksCheckInOpen:
    """SP-02: ENROLLMENT_CLOSED → CHECK_IN_OPEN blocked for PROMOTION_EVENT
    tournament when the referenced Sponsor has is_active=False.
    Code PROMOTION_SPONSOR_INACTIVE must appear."""

    def test_sp_02_inactive_sponsor_blocks(self, test_db):
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        tt = TournamentFactory.ensure_tournament_type(test_db, code=f"sp02-{uuid.uuid4().hex[:6]}")

        sponsor = _make_sponsor(test_db, is_active=False)

        code = f"SP02-{uuid.uuid4().hex[:8].upper()}"
        t = Semester(
            code=code,
            name=f"SP-02 Promo Event {code[-6:]}",
            semester_category=SemesterCategory.PROMOTION_EVENT,
            status=SemesterStatus.DRAFT,
            tournament_status="ENROLLMENT_CLOSED",
            age_group="PRO",
            campus_id=campus.id,
            start_date=date.today() + timedelta(days=7),
            end_date=date.today() + timedelta(days=8),
            enrollment_cost=0,
            specialization_type="LFA_FOOTBALL_PLAYER",
            master_instructor_id=instructor.id,
            organizer_sponsor_id=sponsor.id,  # ← inactive sponsor
        )
        test_db.add(t)
        test_db.flush()
        test_db.add(TournamentConfiguration(
            semester_id=t.id,
            tournament_type_id=tt.id,
            participant_type="INDIVIDUAL",
            number_of_rounds=1,
            max_players=32,
            match_duration_minutes=60,
            break_duration_minutes=10,
            parallel_fields=1,
        ))
        test_db.flush()
        _enroll_player(test_db, t.id)
        _enroll_player(test_db, t.id)
        test_db.commit()
        test_db.expire_all()

        client = _admin_client(test_db, admin)
        try:
            resp = client.patch(
                f"/api/v1/tournaments/{t.id}/status",
                json={"new_status": "CHECK_IN_OPEN", "reason": "test"},
            )
            assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
            body = resp.json()
            detail = body.get("detail") or body.get("error", {}).get("message", "")
            assert "PROMOTION_SPONSOR_INACTIVE" in detail, (
                f"Expected PROMOTION_SPONSOR_INACTIVE in: {detail!r}"
            )
        finally:
            app.dependency_overrides.clear()
