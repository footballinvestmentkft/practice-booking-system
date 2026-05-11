"""
Game State Factory — creates complete DB objects within a session.

All factory functions accept a SQLAlchemy Session and use db.flush() (NOT commit)
so they are compatible with SAVEPOINT-isolated test transactions.
Every created object is rolled back automatically at the end of the test.

Usage:
    from tests.factories.game_factory import PlayerFactory, TournamentFactory

    def test_something(test_db):
        preset = TournamentFactory.ensure_preset(test_db)
        tt = TournamentFactory.ensure_tournament_type(test_db)
        user, license = PlayerFactory.create_lfa_player(test_db)
        tourn = TournamentFactory.create_completed_tournament(
            test_db, preset=preset, tt=tt,
            participants=[(user.id, 1)],
        )
        # TournamentParticipation.skill_rating_delta is now set (EMA computed)
"""
import uuid
from datetime import datetime, timezone, date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models.user import User, UserRole
from app.models.license import UserLicense
from app.models.specialization import SpecializationType
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_type import TournamentType
from app.models.game_configuration import GameConfiguration
from app.models.tournament_reward_config import TournamentRewardConfig
from app.models.game_preset import GamePreset
from app.core.security import get_password_hash
from app.services.tournament.tournament_participation_service import (
    calculate_skill_points_for_placement,
    record_tournament_participation,
)

# ── Default values ─────────────────────────────────────────────────────────────

DEFAULT_FOOTBALL_SKILLS = {
    # outfield (original 11)
    "ball_control": 70.0, "dribbling": 70.0, "finishing": 70.0, "shot_power": 68.0,
    "long_shots": 62.0, "volleys": 58.0, "crossing": 62.0, "passing": 70.0,
    "heading": 65.0, "tackle": 55.0, "marking": 52.0,
    # outfield (Phase 3 new 8)
    "shooting": 60.0, "technique": 60.0, "creativity": 60.0, "long_passing": 60.0,
    "flair": 60.0, "touch": 60.0, "forward_runs": 60.0, "throwing": 60.0,
    # set_pieces (unchanged 3)
    "free_kicks": 60.0, "corners": 60.0, "penalties": 65.0,
    # mental (original 8)
    "positioning_off": 65.0, "positioning_def": 60.0, "vision": 65.0,
    "aggression": 55.0, "reactions": 68.0, "composure": 65.0,
    "consistency": 62.0, "tactical_awareness": 64.0,
    # mental (Phase 3 new 6)
    "anticipation": 60.0, "concentration": 60.0, "decisions": 60.0,
    "determination": 60.0, "teamwork": 60.0, "leadership": 60.0,
    # physical (original 7)
    "acceleration": 68.0, "sprint_speed": 70.0, "agility": 70.0,
    "jumping": 65.0, "strength": 60.0, "stamina": 67.0, "balance": 68.0,
    # physical (Phase 3 new 1)
    "work_rate": 60.0,
}

# IMPORTANT: skill keys MUST be lowercase snake_case to match get_all_skill_keys()
# so _extract_tournament_skills() finds them during EMA computation.
_REWARD_CONFIG = {
    "template_name": "Test Standard Football",
    "custom_config": False,
    "skill_mappings": [
        {"skill": "dribbling", "weight": 1.5, "category": "TECHNICAL", "enabled": True},
        {"skill": "finishing",  "weight": 1.3, "category": "TECHNICAL", "enabled": True},
        {"skill": "passing",   "weight": 1.0, "category": "TECHNICAL", "enabled": True},
    ],
    "first_place":   {"credits": 500, "xp_multiplier": 2.0, "badges": []},
    "second_place":  {"credits": 250, "xp_multiplier": 1.5, "badges": []},
    "third_place":   {"credits": 100, "xp_multiplier": 1.2, "badges": []},
    "participation": {"credits":  50, "xp_multiplier": 1.0, "badges": []},
}

_PRESET_GAME_CONFIG = {
    "version": "1.0",
    "metadata": {"game_category": "FOOTBALL", "difficulty_level": "intermediate", "min_players": 2},
    "skill_config": {
        "skills_tested": ["dribbling", "finishing", "passing"],
        "skill_weights": {"dribbling": 1.5, "finishing": 1.3, "passing": 1.0},
    },
    "format_config": {}, "simulation_config": {},
}


# ── PlayerFactory ──────────────────────────────────────────────────────────────

class PlayerFactory:
    """Factory for creating fully-onboarded LFA Football Players."""

    @staticmethod
    def create_lfa_player(
        db: Session,
        *,
        email: Optional[str] = None,
        name: Optional[str] = None,
        password: str = "Player1234!",
        football_skills: Optional[dict] = None,
    ) -> tuple[User, UserLicense]:
        """
        Create a User + active LFA_FOOTBALL_PLAYER UserLicense with football_skills.

        Returns (User, UserLicense). Uses db.flush() — no commit.
        """
        email = email or f"factory-player+{uuid.uuid4().hex[:8]}@test.lfa"
        name = name or f"Factory Player {email[:20]}"
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        user = User(
            email=email,
            name=name,
            password_hash=get_password_hash(password),
            role=UserRole.STUDENT,
            is_active=True,
            onboarding_completed=True,
            specialization=SpecializationType.LFA_FOOTBALL_PLAYER,
            credit_balance=900,
            payment_verified=True,
            payment_verified_at=now,
        )
        db.add(user)
        db.flush()

        license = UserLicense(
            user_id=user.id,
            specialization_type="LFA_FOOTBALL_PLAYER",
            current_level=1,
            max_achieved_level=1,
            started_at=now,
            is_active=True,
            onboarding_completed=True,
            onboarding_completed_at=now,
            payment_verified=True,
            payment_verified_at=now,
            football_skills=football_skills or DEFAULT_FOOTBALL_SKILLS,
        )
        db.add(license)
        db.flush()

        return user, license


# ── TournamentFactory ──────────────────────────────────────────────────────────

class TournamentFactory:
    """Factory for creating game presets, tournament types, and completed tournaments."""

    @staticmethod
    def ensure_preset(db: Session, code: str = "factory_outfield") -> GamePreset:
        """Get or create a GamePreset within the current session. Uses flush."""
        existing = db.query(GamePreset).filter(GamePreset.code == code).first()
        if existing:
            return existing
        preset = GamePreset(
            code=code,
            name=f"Factory Preset ({code})",
            game_config=_PRESET_GAME_CONFIG,
            is_active=True,
            is_recommended=False,
            is_locked=False,
        )
        db.add(preset)
        db.flush()
        return preset

    @staticmethod
    def ensure_tournament_type(db: Session, code: str = "factory_league") -> TournamentType:
        """Get or create a TournamentType within the current session. Uses flush."""
        existing = db.query(TournamentType).filter(TournamentType.code == code).first()
        if existing:
            return existing
        tt = TournamentType(
            code=code,
            display_name=f"Factory League ({code})",
            description="Auto-created by TournamentFactory for tests",
            format="HEAD_TO_HEAD",
            min_players=2,
            max_players=32,
            requires_power_of_two=False,
            session_duration_minutes=60,
            break_between_sessions_minutes=15,
            config={"code": code, "format": "HEAD_TO_HEAD"},
        )
        db.add(tt)
        db.flush()
        return tt

    @staticmethod
    def create_completed_tournament(
        db: Session,
        *,
        preset: GamePreset,
        tt: TournamentType,
        participants: list[tuple[int, Optional[int]]],
        name: Optional[str] = None,
        code: Optional[str] = None,
        start_date: Optional[date] = None,
    ) -> Semester:
        """
        Create a COMPLETED Semester + all required related rows + TournamentParticipation
        for each participant with EMA skill_rating_delta computed.

        Args:
            participants: list of (user_id, placement).
                          placement=1/2/3 → EMA delta computed.
                          placement=None → participant only, no EMA delta.

        IMPORTANT: call in chronological order when creating multiple tournaments
        for the same users, so EMA replays prior history correctly.

        Uses db.flush() — no commit.
        """
        code = code or f"FACT-{uuid.uuid4().hex[:12].upper()}"
        name = name or f"Factory Tournament {code[-8:]}"
        start_date = start_date or date(2026, 1, 1)

        tourn = Semester(
            code=code,
            name=name,
            semester_category=SemesterCategory.TOURNAMENT,
            status=SemesterStatus.COMPLETED,
            tournament_status="FINALIZED",
            age_group="YOUTH",
            location_id=None,
            campus_id=None,
            start_date=start_date,
            end_date=start_date + timedelta(days=7),
            enrollment_cost=0,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        db.add(tourn)
        db.flush()

        db.add(TournamentConfiguration(
            semester_id=tourn.id,
            tournament_type_id=tt.id,
            scoring_type=None,
            ranking_direction="DESC",
            participant_type="INDIVIDUAL",
            is_multi_day=False,
            max_players=32,
            parallel_fields=1,
            sessions_generated=False,
        ))
        db.add(GameConfiguration(
            semester_id=tourn.id,
            game_preset_id=preset.id,
            game_config={"metadata": {"min_players": 2}, "match_rules": {}},
        ))
        db.add(TournamentRewardConfig(
            semester_id=tourn.id,
            reward_policy_name="Test Standard Football",
            reward_config=_REWARD_CONFIG,
        ))
        # Flush reward_config before record_tournament_participation reads it
        db.flush()

        for user_id, placement in participants:
            skill_pts = calculate_skill_points_for_placement(db, tourn.id, placement)
            record_tournament_participation(
                db=db,
                user_id=user_id,
                tournament_id=tourn.id,
                placement=placement,
                skill_points=skill_pts,
                base_xp=0,
                credits=0,
            )
        db.flush()

        return tourn
