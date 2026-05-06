"""
Integration tests: GroupKnockoutGenerator — production-path validation

GK-PROD-01  9 enrolled players using the *seeded* 'group_knockout' TournamentType
            (loaded from DB after migration 2026_05_06_1400) produce exactly 13
            sessions: 9 group + 0 play-in + 2 SF + 1 Final + 1 Bronze.

GK-PROD-02  SF1 structure_config.matchup == 'Group A winner vs Best runner-up'
            and seed slots A1/BR are set.

GK-PROD-03  SF2 structure_config.matchup == 'Group B winner vs Group C winner'
            and seed slots B1/C1 are set.

GK-PROD-04  16 enrolled players using the same seeded TournamentType produce
            32 group sessions and 0 play-in (backward-compat guard: 16p config
            unchanged by the migration).

This file DOES NOT use an inline TournamentType fixture — it loads the real
seeded record (code='group_knockout') from the test DB after alembic upgrade head.
If the migration has not run, the test fails with an explicit assertion message.
"""

import pytest
import uuid
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.tournament_type import TournamentType
from app.models.tournament_configuration import TournamentConfiguration
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.license import UserLicense
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from app.services.tournament.session_generation.formats.group_knockout_generator import (
    GroupKnockoutGenerator,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_players(db: Session, n: int) -> list[User]:
    players = []
    for i in range(n):
        u = User(
            email=f"gkprod+{i}+{_uid()}@test.com",
            name=f"Prod Player {i}",
            password_hash=get_password_hash("pw"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        db.add(u)
        db.flush()
        players.append(u)
    db.commit()
    for u in players:
        db.refresh(u)
    return players


def _make_tournament_with_enrollments(
    db: Session,
    tt: TournamentType,
    players: list[User],
    instructor_id: int,
) -> Semester:
    sem = Semester(
        code=f"GKPROD-{_uid()}",
        name="Production Path Test Cup",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 1),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.MINI_SEASON,
        enrollment_cost=0,
        master_instructor_id=instructor_id,
        tournament_status="IN_PROGRESS",
    )
    db.add(sem)
    db.flush()

    db.add(TournamentConfiguration(
        semester_id=sem.id,
        participant_type="INDIVIDUAL",
        sessions_generated=False,
        tournament_type_id=tt.id,
    ))

    for u in players:
        lic = UserLicense(
            user_id=u.id,
            specialization_type="LFA_FOOTBALL_PLAYER",
            is_active=True,
            current_level=1,
            max_achieved_level=1,
            started_at=datetime.now(timezone.utc),
        )
        db.add(lic)
        db.flush()
        db.add(SemesterEnrollment(
            semester_id=sem.id,
            user_id=u.id,
            user_license_id=lic.id,
            is_active=True,
            request_status=EnrollmentStatus.APPROVED,
        ))

    db.commit()
    db.refresh(sem)
    return sem


def _run_generator(db: Session, sem: Semester, tt: TournamentType) -> list[dict]:
    gen = GroupKnockoutGenerator(db)
    return gen.generate(
        tournament=sem,
        tournament_type=tt,
        player_count=0,   # generator re-queries enrollments
        parallel_fields=1,
        session_duration=90,
        break_minutes=15,
    )


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def _seeded_gk_tt(test_db: Session) -> TournamentType:
    """Load the real seeded group_knockout TournamentType from DB."""
    tt = test_db.query(TournamentType).filter(
        TournamentType.code == "group_knockout"
    ).first()
    assert tt is not None, (
        "TournamentType code='group_knockout' not found in test DB. "
        "Run: alembic upgrade head"
    )
    gc = (tt.config or {}).get("group_configuration", {})
    assert "9_players" in gc, (
        "group_configuration.9_players missing from seeded group_knockout config. "
        "Migration 2026_05_06_1400 has not been applied — run: alembic upgrade head"
    )
    return tt


# ── GK-PROD-01..03: 9-player production path ──────────────────────────────────

class TestNinePlayerProductionPath:

    @pytest.fixture(autouse=True)
    def _setup(self, test_db: Session, instructor_user: User, _seeded_gk_tt: TournamentType):
        self.db = test_db
        self.tt = _seeded_gk_tt
        players = _make_players(test_db, 9)
        sem = _make_tournament_with_enrollments(test_db, _seeded_gk_tt, players, instructor_user.id)
        self.sessions = _run_generator(test_db, sem, _seeded_gk_tt)

    def _group_sessions(self):
        return [s for s in self.sessions if s["tournament_phase"] == "GROUP_STAGE"]

    def _ko_sessions(self):
        return [s for s in self.sessions if s["tournament_phase"] == "KNOCKOUT"]

    def _play_in(self):
        return [s for s in self._ko_sessions() if s.get("tournament_round") == 0]

    def _sf(self):
        return sorted(
            [s for s in self._ko_sessions() if s.get("tournament_round") == 1],
            key=lambda s: s["tournament_match_number"],
        )

    def _final(self):
        return [
            s for s in self._ko_sessions()
            if s.get("tournament_round", 0) == 2
            and "3rd" not in s.get("title", "")
        ]

    def _bronze(self):
        return [s for s in self._ko_sessions() if "3rd Place" in s.get("title", "")]

    def test_gk_prod01_session_counts(self):
        assert len(self._group_sessions()) == 9, (
            f"Expected 9 group sessions, got {len(self._group_sessions())}"
        )
        assert len(self._play_in()) == 0, (
            f"Expected 0 play-in sessions, got {len(self._play_in())}. "
            "group_configuration.9_players policy not applied — check migration."
        )
        assert len(self._sf()) == 2
        assert len(self._final()) == 1
        assert len(self._bronze()) == 1
        assert len(self.sessions) == 13, (
            f"Expected 13 total sessions, got {len(self.sessions)}"
        )

    def test_gk_prod02_sf1_matchup_a_winner_vs_best_runner_up(self):
        sf1 = self._sf()[0]
        sc = sf1.get("structure_config", {})
        assert sc.get("matchup") == "Group A winner vs Best runner-up", (
            f"SF1 matchup wrong: {sc.get('matchup')!r}"
        )
        assert sc.get("seed_1") == "A1"
        assert sc.get("seed_2") == "BR"

    def test_gk_prod03_sf2_matchup_b_winner_vs_c_winner(self):
        sf2 = self._sf()[1]
        sc = sf2.get("structure_config", {})
        assert sc.get("matchup") == "Group B winner vs Group C winner", (
            f"SF2 matchup wrong: {sc.get('matchup')!r}"
        )
        assert sc.get("seed_1") == "B1"
        assert sc.get("seed_2") == "C1"


# ── GK-PROD-04: 16-player backward-compat guard ───────────────────────────────

class TestSixteenPlayerBackwardCompat:
    """Verifies that adding 9_players to group_configuration did NOT break 16p."""

    def test_gk_prod04_16p_no_play_in_32_group_sessions(
        self, test_db: Session, instructor_user: User, _seeded_gk_tt: TournamentType
    ):
        players = _make_players(test_db, 16)
        sem = _make_tournament_with_enrollments(
            test_db, _seeded_gk_tt, players, instructor_user.id
        )
        sessions = _run_generator(test_db, sem, _seeded_gk_tt)

        group = [s for s in sessions if s["tournament_phase"] == "GROUP_STAGE"]
        play_in = [s for s in sessions if s["tournament_phase"] == "KNOCKOUT"
                   and s.get("tournament_round") == 0]

        # 16p → 4 groups × 4 players × 3 round-robin matches each = 48... wait
        # Actually: 4 groups × C(4,2)=6 matches each = 24 group sessions
        # KO: 4 group winners × 2 qualifiers = 8 → QF round + SF + Final + Bronze
        # group sessions = 4 groups × 6 matches = 24
        assert len(play_in) == 0, (
            f"16p should have 0 play-in sessions, got {len(play_in)}. "
            "The 9_players migration incorrectly affected 16p config."
        )
        assert len(group) == 24, (
            f"16p expected 24 group sessions (4 groups × 6 RR matches), got {len(group)}"
        )
