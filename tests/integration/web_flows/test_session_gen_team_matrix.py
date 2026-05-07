"""
Session Generation — TEAM × tournament_type matrix tests

SGM-01  TEAM + LEAGUE    → sessions generated, participant_team_ids set
SGM-02  TEAM + KNOCKOUT  → sessions generated, participant_team_ids set (round 1)
SGM-03  TEAM + SWISS     → sessions generated, participant_team_ids set
SGM-04  TEAM + GROUP_KNOCKOUT → sessions generated, participant_team_ids set
SGM-05  INDIVIDUAL + LEAGUE → sessions generated, participant_user_ids set (regression guard)
SGM-06  TEAM + LEAGUE, 0 teams enrolled → generation fails with "teams" in error, not "players"

GENVAL-LOC-01  GenerationValidator: tournament without location_id AND campus_id → (False, "Location or Campus")

Every test uses TournamentSessionGenerator directly (no HTTP) with a real DB session.
"""
import uuid
import pytest
from datetime import date, datetime, timezone
from sqlalchemy.orm import Session

from app.models.user import User, UserRole
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_type import TournamentType
from app.models.team import Team, TeamMember, TournamentTeamEnrollment
from app.models.game_configuration import GameConfiguration
from app.models.game_preset import GamePreset
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.license import UserLicense
from app.models.location import Location, LocationType
from app.models.campus import Campus
from app.models.pitch import Pitch
from app.services.tournament_session_generator import TournamentSessionGenerator
from app.core.security import get_password_hash


# ── SAVEPOINT-isolated DB fixture ─────────────────────────────────────────────

@pytest.fixture(scope="function")
def test_db():
    from app.database import engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import event

    # Use connection-based outer transaction so that any db.commit() inside
    # generate_sessions() (which commits sessions_generated=True) is still
    # part of the outer transaction and gets rolled back at the end.
    connection = engine.connect()
    outer_tx = connection.begin()
    TestingSession = sessionmaker(bind=connection, autocommit=False, autoflush=False)
    db = TestingSession()
    connection.begin_nested()  # SAVEPOINT

    @event.listens_for(db, "after_transaction_end")
    def restart_savepoint(session, transaction):
        if transaction.nested and not transaction._parent.nested:
            connection.begin_nested()

    yield db
    db.close()
    if outer_tx.is_active:
        outer_tx.rollback()
    connection.close()


# ── Factories ──────────────────────────────────────────────────────────────────

_PFX = "sgm"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _user(db: Session, role=UserRole.STUDENT) -> User:
    u = User(
        email=f"{_PFX}-{_uid()}@lfa-test.com",
        name=f"SGM User {_uid()}",
        password_hash=get_password_hash("pw"),
        role=role,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _instructor(db: Session) -> User:
    u = _user(db, role=UserRole.INSTRUCTOR)
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


def _tournament_type(db: Session, code: str, tt_format: str = "HEAD_TO_HEAD",
                     min_players: int = 2) -> TournamentType:
    existing = db.query(TournamentType).filter(TournamentType.code == code).first()
    if existing:
        return existing
    tt = TournamentType(
        code=code,
        display_name=f"SGM {code}",
        description="Auto-created for SGM tests",
        format=tt_format,
        min_players=min_players,
        max_players=64,
        requires_power_of_two=(code == "knockout"),
        session_duration_minutes=60,
        break_between_sessions_minutes=10,
        config={"code": code},
    )
    db.add(tt)
    db.flush()
    return tt


def _preset(db: Session) -> GamePreset:
    existing = db.query(GamePreset).filter(GamePreset.code == "sgm-default").first()
    if existing:
        return existing
    gp = GamePreset(
        code="sgm-default",
        name="SGM Default",
        description="Auto-created for SGM tests",
        is_active=True,
        game_config={"metadata": {"min_players": 0}, "skills_tested": [], "skill_weights": {}},
    )
    db.add(gp)
    db.flush()
    return gp


def _tournament(
    db: Session,
    tt: TournamentType,
    participant_type: str = "INDIVIDUAL",
    instructor: User = None,
) -> Semester:
    if instructor is None:
        instructor = _instructor(db)
    preset = _preset(db)

    uid = _uid()
    loc = Location(
        name=f"SGM Location {uid}",
        city=f"SGMCity-{uid}",
        country="HU",
        is_active=True,
        location_type=LocationType.CENTER,
    )
    db.add(loc)
    db.flush()
    camp = Campus(location_id=loc.id, name=f"SGM Campus {uid}", is_active=True)
    db.add(camp)
    db.flush()
    # Session generation requires ≥1 active pitch on the campus (domain invariant)
    db.add(Pitch(campus_id=camp.id, pitch_number=1, name="Pálya A", capacity=22, is_active=True))
    db.flush()

    t = Semester(
        name=f"SGM Cup {_uid()}",
        code=f"SGM-{_uid()}",
        master_instructor_id=instructor.id,
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 30),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.TOURNAMENT,
        tournament_status="IN_PROGRESS",
        campus_id=camp.id,
    )
    db.add(t)
    db.flush()

    cfg = TournamentConfiguration(
        semester_id=t.id,
        tournament_type_id=tt.id,
        participant_type=participant_type,
        max_players=32,
        number_of_rounds=1,
        parallel_fields=1,
    )
    db.add(cfg)

    game_cfg = GameConfiguration(
        semester_id=t.id,
        game_preset_id=preset.id,
    )
    db.add(game_cfg)
    db.flush()
    return t


def _make_team(db: Session, tournament: Semester) -> Team:
    captain = _user(db)
    team = Team(
        name=f"Team {_uid()}",
        code=f"T-{_uid()}",
        captain_user_id=captain.id,
        is_active=True,
    )
    db.add(team)
    db.flush()
    db.add(TeamMember(team_id=team.id, user_id=captain.id, role="CAPTAIN", is_active=True))
    db.add(TournamentTeamEnrollment(
        semester_id=tournament.id,
        team_id=team.id,
        payment_verified=True,
        is_active=True,
    ))
    db.flush()
    return team


def _enroll_player(db: Session, tournament: Semester) -> User:
    u = _user(db)
    lic = UserLicense(
        user_id=u.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        onboarding_completed=True,
        is_active=True,
    )
    db.add(lic)
    db.flush()
    db.add(SemesterEnrollment(
        semester_id=tournament.id,
        user_id=u.id,
        user_license_id=lic.id,
        is_active=True,
        request_status=EnrollmentStatus.APPROVED,
    ))
    db.flush()
    return u


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestSessionGenTeamMatrix:
    """
    Parametrized cross-product: participant_type × tournament_type_code.
    All TEAM + HEAD_TO_HEAD combinations must correctly use TournamentTeamEnrollment.
    """

    def _run_gen(self, db: Session, tournament: Semester) -> tuple:
        """Run TournamentSessionGenerator and return (success, msg, sessions)."""
        gen = TournamentSessionGenerator(db)
        result = gen.generate_sessions(
            tournament_id=tournament.id,
            parallel_fields=1,
            session_duration_minutes=60,
            break_minutes=10,
            number_of_rounds=1,
        )
        db.rollback()  # don't persist generated sessions between tests
        return result

    # ── SGM-01: TEAM + LEAGUE ────────────────────────────────────────────────
    def test_SGM_01_team_league_generates_sessions(self, test_db: Session):
        """TEAM LEAGUE with 2 enrolled teams → sessions with participant_team_ids."""
        tt = _tournament_type(test_db, "league", min_players=2)
        t = _tournament(test_db, tt, participant_type="TEAM")
        _make_team(test_db, t)
        _make_team(test_db, t)

        success, msg, sessions = self._run_gen(test_db, t)

        assert success, f"Expected success, got: {msg}"
        assert len(sessions) >= 1, "Expected at least 1 session"
        # Round 1 matches must have participant_team_ids, not participant_user_ids
        first_match = sessions[0]
        team_ids_field = first_match.get("participant_team_ids")
        user_ids_field = first_match.get("participant_user_ids")
        assert team_ids_field, f"Expected participant_team_ids, got: {first_match}"
        assert not user_ids_field, f"participant_user_ids should be None for TEAM, got: {user_ids_field}"

    # ── SGM-02: TEAM + KNOCKOUT ──────────────────────────────────────────────
    def test_SGM_02_team_knockout_generates_sessions(self, test_db: Session):
        """TEAM KNOCKOUT with 4 enrolled teams → sessions with participant_team_ids in R1."""
        tt = _tournament_type(test_db, "knockout", min_players=4)
        t = _tournament(test_db, tt, participant_type="TEAM")
        for _ in range(4):
            _make_team(test_db, t)

        success, msg, sessions = self._run_gen(test_db, t)

        assert success, f"Expected success, got: {msg}"
        assert len(sessions) >= 1
        round1_sessions = [s for s in sessions if s.get("tournament_round") == 1]
        for s in round1_sessions:
            assert s.get("participant_team_ids"), \
                f"R1 session missing participant_team_ids: {s}"

    # ── SGM-03: TEAM + SWISS ─────────────────────────────────────────────────
    def test_SGM_03_team_swiss_generates_sessions(self, test_db: Session):
        """TEAM SWISS with 4 enrolled teams → sessions with participant_team_ids."""
        tt = _tournament_type(test_db, "swiss", min_players=4)
        t = _tournament(test_db, tt, participant_type="TEAM")
        for _ in range(4):
            _make_team(test_db, t)

        success, msg, sessions = self._run_gen(test_db, t)

        assert success, f"Expected success, got: {msg}"
        assert len(sessions) >= 1
        h2h_sessions = [s for s in sessions if s.get("participant_team_ids")]
        assert h2h_sessions, "Expected sessions with participant_team_ids"

    # ── SGM-04: TEAM + GROUP_KNOCKOUT ────────────────────────────────────────
    def test_SGM_04_team_group_knockout_generates_sessions(self, test_db: Session):
        """TEAM GROUP_KNOCKOUT with 8 enrolled teams → group stage sessions."""
        tt = _tournament_type(test_db, "group_knockout", min_players=8)
        t = _tournament(test_db, tt, participant_type="TEAM")
        for _ in range(8):
            _make_team(test_db, t)

        success, msg, sessions = self._run_gen(test_db, t)

        assert success, f"Expected success, got: {msg}"
        assert len(sessions) >= 1

    # ── SGM-05: INDIVIDUAL + LEAGUE (regression guard) ──────────────────────
    def test_SGM_05_individual_league_unchanged(self, test_db: Session):
        """INDIVIDUAL LEAGUE still uses participant_user_ids (regression guard)."""
        tt = _tournament_type(test_db, "league", min_players=2)
        t = _tournament(test_db, tt, participant_type="INDIVIDUAL")
        _enroll_player(test_db, t)
        _enroll_player(test_db, t)

        success, msg, sessions = self._run_gen(test_db, t)

        assert success, f"Expected success, got: {msg}"
        assert len(sessions) >= 1
        first_match = sessions[0]
        user_ids_field = first_match.get("participant_user_ids")
        team_ids_field = first_match.get("participant_team_ids")
        assert user_ids_field, f"Expected participant_user_ids for INDIVIDUAL, got: {first_match}"
        assert not team_ids_field, f"participant_team_ids should be None for INDIVIDUAL"

    # ── SGM-06: TEAM + LEAGUE, no teams enrolled → error uses "teams" ────────
    def test_SGM_06_team_no_teams_enrolled_error_says_teams(self, test_db: Session):
        """TEAM LEAGUE with 0 teams → error message says 'teams', not 'players'."""
        tt = _tournament_type(test_db, "league", min_players=2)
        t = _tournament(test_db, tt, participant_type="TEAM")
        # No teams enrolled

        success, msg, sessions = self._run_gen(test_db, t)

        assert not success, "Expected failure with 0 teams"
        assert "teams" in msg.lower(), \
            f"Error should mention 'teams', not 'players'. Got: {msg!r}"
        assert "players" not in msg.lower(), \
            f"Error must NOT say 'players' for a TEAM tournament. Got: {msg!r}"

    # ── SGM-07: INDIVIDUAL + KNOCKOUT ────────────────────────────────────────
    def test_SGM_07_individual_knockout_generates_sessions(self, test_db: Session):
        """INDIVIDUAL KNOCKOUT with 4 enrolled players → R1 sessions have participant_user_ids."""
        tt = _tournament_type(test_db, "knockout", min_players=4)
        t = _tournament(test_db, tt, participant_type="INDIVIDUAL")
        for _ in range(4):
            _enroll_player(test_db, t)

        success, msg, sessions = self._run_gen(test_db, t)

        assert success, f"Expected success, got: {msg}"
        assert len(sessions) >= 1
        round1_sessions = [s for s in sessions if s.get("tournament_round") == 1]
        for s in round1_sessions:
            user_ids = s.get("participant_user_ids")
            team_ids = s.get("participant_team_ids")
            assert user_ids, f"R1 session missing participant_user_ids: {s}"
            assert not team_ids, f"participant_team_ids must be None for INDIVIDUAL, got: {team_ids}"

    # ── SGM-08: INDIVIDUAL + SWISS ───────────────────────────────────────────
    def test_SGM_08_individual_swiss_generates_sessions(self, test_db: Session):
        """INDIVIDUAL SWISS with 4 enrolled players → sessions have participant_user_ids."""
        tt = _tournament_type(test_db, "swiss", min_players=4)
        t = _tournament(test_db, tt, participant_type="INDIVIDUAL")
        for _ in range(4):
            _enroll_player(test_db, t)

        success, msg, sessions = self._run_gen(test_db, t)

        assert success, f"Expected success, got: {msg}"
        assert len(sessions) >= 1
        h2h_sessions = [s for s in sessions if s.get("participant_user_ids")]
        assert h2h_sessions, "Expected sessions with participant_user_ids"
        for s in h2h_sessions:
            assert not s.get("participant_team_ids"), \
                f"participant_team_ids must be None for INDIVIDUAL, got: {s.get('participant_team_ids')}"

    # ── SGM-09: INDIVIDUAL + GROUP_KNOCKOUT ──────────────────────────────────
    def test_SGM_09_individual_group_knockout_generates_sessions(self, test_db: Session):
        """INDIVIDUAL GROUP_KNOCKOUT with 8 enrolled players → group stage has participant_user_ids."""
        tt = _tournament_type(test_db, "group_knockout", min_players=8)
        t = _tournament(test_db, tt, participant_type="INDIVIDUAL")
        for _ in range(8):
            _enroll_player(test_db, t)

        success, msg, sessions = self._run_gen(test_db, t)

        assert success, f"Expected success, got: {msg}"
        assert len(sessions) >= 1
        # Group stage sessions have group_identifier set (e.g. "A", "B")
        group_sessions = [s for s in sessions if s.get("group_identifier") is not None]
        assert group_sessions, "Expected group stage sessions with group_identifier"
        h2h_group_sessions = [s for s in group_sessions if s.get("participant_user_ids")]
        assert h2h_group_sessions, "Expected group stage H2H sessions with participant_user_ids"
        for s in h2h_group_sessions:
            assert not s.get("participant_team_ids"), \
                f"participant_team_ids must be None for INDIVIDUAL, got: {s.get('participant_team_ids')}"

    # ── SGM-10: INDIVIDUAL + SWISS, odd player count (bye handling) ──────────
    def test_SGM_10_individual_swiss_odd_count_bye_handling(self, test_db: Session):
        """INDIVIDUAL SWISS with 5 players (odd) → 5th player gets bye, sessions still generated."""
        tt = _tournament_type(test_db, "swiss", min_players=4)
        t = _tournament(test_db, tt, participant_type="INDIVIDUAL")
        for _ in range(5):  # odd: 5th player gets BYE each round
            _enroll_player(test_db, t)

        success, msg, sessions = self._run_gen(test_db, t)

        assert success, f"Expected success with odd player count, got: {msg}"
        # 5 players: ceil(log2(5))=3 rounds, 2 matches/round (player[4] BYE) → 6 sessions
        assert len(sessions) >= 1, f"Expected sessions even with odd player count, got {len(sessions)}"
        for s in sessions:
            user_ids = s.get("participant_user_ids")
            assert user_ids and len(user_ids) == 2, \
                f"Each session must have exactly 2 participant_user_ids, got: {user_ids}"
            assert not s.get("participant_team_ids"), \
                f"participant_team_ids must be None for INDIVIDUAL, got: {s.get('participant_team_ids')}"


# ── SGM-11..12: Multi-leg generation ──────────────────────────────────────────

class TestMultiLegGeneration:
    """
    SGM-11  INDIVIDUAL H2H league, number_of_legs=2 → 2× session count vs legs=1
    SGM-12  TEAM H2H league, number_of_legs=2, track_home_away=True
             → leg_number populated in DB sessions; home/away reversed in leg 2
    """

    def _run_gen(self, db: Session, tournament: Semester, **kwargs) -> tuple:
        """Run TournamentSessionGenerator with extra kwargs and return (success, msg, sessions)."""
        gen = TournamentSessionGenerator(db)
        result = gen.generate_sessions(
            tournament_id=tournament.id,
            parallel_fields=1,
            session_duration_minutes=60,
            break_minutes=10,
            number_of_rounds=1,
            **kwargs,
        )
        db.rollback()
        return result

    def test_SGM_11_individual_league_double_leg(self, test_db: Session):
        """INDIVIDUAL H2H league with 4 players and legs=2 → 2× single-leg session count.

        Two separate tournaments are used (one per leg count) because
        generate_sessions() marks sessions_generated=True and commits, preventing
        a second generation on the same tournament object within the same savepoint.
        """
        tt = _tournament_type(test_db, "league", min_players=2)

        # Tournament A — single leg baseline
        t1 = _tournament(test_db, tt, participant_type="INDIVIDUAL")
        for _ in range(4):
            _enroll_player(test_db, t1)
        success1, msg1, s1 = self._run_gen(test_db, t1, number_of_legs=1)
        assert success1, f"Single-leg generation failed: {msg1}"

        # Tournament B — double leg
        t2 = _tournament(test_db, tt, participant_type="INDIVIDUAL")
        for _ in range(4):
            _enroll_player(test_db, t2)
        success2, msg2, s2 = self._run_gen(test_db, t2, number_of_legs=2)
        assert success2, f"Double-leg generation failed: {msg2}"

        assert len(s2) == 2 * len(s1), (
            f"Expected 2×{len(s1)}={2*len(s1)} sessions for legs=2, got {len(s2)}"
        )

    def test_SGM_12_team_league_home_away_tracking(self, test_db: Session):
        """TEAM H2H league, legs=2, track_home_away=True → leg_number set, leg2 reverses pairings."""
        tt = _tournament_type(test_db, "league", min_players=2)
        t = _tournament(test_db, tt, participant_type="TEAM")
        team1 = _make_team(test_db, t)
        team2 = _make_team(test_db, t)

        # Generate with 2 legs + home/away
        gen = TournamentSessionGenerator(test_db)
        success, msg, sessions = gen.generate_sessions(
            tournament_id=t.id,
            parallel_fields=1,
            session_duration_minutes=60,
            break_minutes=10,
            number_of_legs=2,
            track_home_away=True,
        )
        test_db.rollback()

        assert success, f"Generation failed: {msg}"
        # 2 teams → 1 match per leg → 2 sessions total
        assert len(sessions) == 2, f"Expected 2 sessions (1 per leg), got {len(sessions)}"

        leg1 = [s for s in sessions if s.get("leg_number") == 1]
        leg2 = [s for s in sessions if s.get("leg_number") == 2]
        assert len(leg1) == 1
        assert len(leg2) == 1

        # With home/away: leg 2 participant order should be reversed vs leg 1
        ids1 = leg1[0].get("participant_team_ids", [])
        ids2 = leg2[0].get("participant_team_ids", [])
        assert ids1 and ids2, "participant_team_ids must be set for TEAM sessions"
        assert ids1 != ids2, "Leg 2 should have reversed team order (home/away tracking)"
        assert ids1 == list(reversed(ids2)), (
            f"Leg 2 must be the reverse of leg 1. Leg1={ids1}, Leg2={ids2}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# GENVAL-LOC-01: GenerationValidator rejects tournament without location/campus
# ─────────────────────────────────────────────────────────────────────────────

def test_GENVAL_LOC_01_no_location_or_campus_blocked(test_db: Session):
    """GENVAL-LOC-01: can_generate_sessions() returns (False, ...) when both
    location_id and campus_id are NULL on the tournament.
    """
    from app.services.tournament.session_generation.validators.generation_validator import GenerationValidator

    tt = _tournament_type(test_db, f"genval-loc-{_uid()}")
    t = _tournament(test_db, tt, participant_type="INDIVIDUAL")
    # Clear location/campus to test the validator guard
    t.campus_id = None
    t.location_id = None
    test_db.flush()
    assert t.location_id is None
    assert t.campus_id is None

    # Enroll enough players so the enrollment check passes
    for _ in range(2):
        player = _user(test_db)
        from app.models.license import UserLicense
        from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
        lic = UserLicense(
            user_id=player.id,
            specialization_type="LFA_FOOTBALL_PLAYER",
            started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            is_active=True,
            onboarding_completed=True,
        )
        test_db.add(lic)
        test_db.flush()
        enr = SemesterEnrollment(
            user_id=player.id,
            semester_id=t.id,
            user_license_id=lic.id,
            request_status=EnrollmentStatus.APPROVED,
            is_active=True,
        )
        test_db.add(enr)
    test_db.flush()

    validator = GenerationValidator(test_db)
    can_gen, reason = validator.can_generate_sessions(t.id)

    assert can_gen is False, f"Expected False, got True (reason: {reason})"
    assert "location" in reason.lower() or "campus" in reason.lower(), (
        f"Error message must mention location/campus, got: {reason}"
    )
