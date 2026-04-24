"""
Integration tests for ENABLE_SKILL_TIER_NOTIFICATIONS feature (Sprint P5).

Uses real PostgreSQL with SAVEPOINT isolation (test_db fixture).

Tests call record_tournament_participation() and assert Notification rows.

Expected baseline delta (solo 1st-place tournament, same as PROP-I-01):
  old_pct=60.0 (DEFAULT_BASELINE, no prior assessment), delta≈+8.0, new_pct=68.0
  → 60 < 65 <= 68 → Intermediate threshold crossed  (threshold=65)

TIER-I-01  flag=True, solo 1st place → 1 Notification row (type=skill_tier_reached)
TIER-I-02  flag=False → 0 Notification rows created
TIER-I-03  Two consecutive tournaments: 1st crosses 65, 2nd stays at ~74 → 1 total
TIER-I-04  Notification fields: title contains skill name, message contains tier+percentage
"""
import uuid
import pytest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy.orm import Session

from app.models.user import User, UserRole
from app.models.semester import Semester, SemesterStatus
from app.models.tournament_achievement import TournamentSkillMapping
from app.models.license import UserLicense
from app.models.notification import Notification, NotificationType
from app.services.tournament.tournament_participation_service import record_tournament_participation
from app.core.security import get_password_hash


_BASE = "app.services.tournament.tournament_participation_service"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _player(test_db: Session) -> User:
    user = User(
        email=f"tier-player+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Tier Notification Test Player",
        password_hash=get_password_hash("pass"),
        role=UserRole.STUDENT,
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


def _license(test_db: Session, user: User) -> UserLicense:
    lic = UserLicense(
        user_id=user.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        current_level=1,
        max_achieved_level=1,
        started_at=datetime.now(timezone.utc),
        is_active=True,
        # football_skills=None → baseline DEFAULT_BASELINE=60.0
    )
    test_db.add(lic)
    test_db.commit()
    test_db.refresh(lic)
    return lic


def _tournament(test_db: Session) -> Semester:
    sem = Semester(
        code=f"TIER-{uuid.uuid4().hex[:8]}",
        name="Tier Notification Test Tournament",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=90),
        status=SemesterStatus.ONGOING,
    )
    test_db.add(sem)
    test_db.commit()
    test_db.refresh(sem)
    return sem


def _skill_mapping(test_db: Session, tournament: Semester, skill: str = "dribbling") -> None:
    mapping = TournamentSkillMapping(
        semester_id=tournament.id,
        skill_name=skill,
        skill_category="football_skill",
        weight=1.0,
    )
    test_db.add(mapping)
    test_db.commit()


def _run_tournament(test_db: Session, player: User, tournament: Semester) -> None:
    """Call record_tournament_participation and flush."""
    record_tournament_participation(
        db=test_db,
        user_id=player.id,
        tournament_id=tournament.id,
        placement=1,
        skill_points={},
        base_xp=0,
        credits=0,
    )
    test_db.flush()


def _notifications(test_db: Session, player: User) -> list:
    return (
        test_db.query(Notification)
        .filter(
            Notification.user_id == player.id,
            Notification.type == NotificationType.SKILL_TIER_REACHED,
        )
        .all()
    )


# ── TIER-I-01: flag=True, tier crossed → Notification row created ─────────────

def test_tier_i01_flag_on_creates_notification(test_db: Session):
    """
    Full record_tournament_participation() flow with flag=True.
    Expected: old_pct=60 (DEFAULT_BASELINE), delta≈+8, new_pct=68 → threshold 65 crossed →
    exactly 1 Notification row with type=SKILL_TIER_REACHED.
    """
    player = _player(test_db)
    _license(test_db, player)
    tournament = _tournament(test_db)
    _skill_mapping(test_db, tournament, "dribbling")

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        mock_settings.ENABLE_SKILL_TIER_NOTIFICATIONS = True
        mock_settings.SKILL_TIER_THRESHOLDS = {
            65: "Intermediate", 75: "Advanced", 90: "Expert"
        }
        _run_tournament(test_db, player, tournament)

    notifs = _notifications(test_db, player)
    assert len(notifs) == 1, f"Expected 1 skill_tier_reached notification, got {len(notifs)}"
    assert notifs[0].type == NotificationType.SKILL_TIER_REACHED


# ── TIER-I-02: flag=False → 0 notification rows ───────────────────────────────

def test_tier_i02_flag_off_no_notification(test_db: Session):
    """Same tournament call with ENABLE_SKILL_TIER_NOTIFICATIONS=False → no row."""
    player = _player(test_db)
    _license(test_db, player)
    tournament = _tournament(test_db)
    _skill_mapping(test_db, tournament, "dribbling")

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        mock_settings.ENABLE_SKILL_TIER_NOTIFICATIONS = False
        mock_settings.SKILL_TIER_THRESHOLDS = {
            65: "Intermediate", 75: "Advanced", 90: "Expert"
        }
        _run_tournament(test_db, player, tournament)

    notifs = _notifications(test_db, player)
    assert len(notifs) == 0, f"Expected 0 notifications with flag off, got {len(notifs)}"


# ── TIER-I-03: two consecutive tournaments → exactly 1 notification total ─────

def test_tier_i03_second_tournament_no_duplicate_notification(test_db: Session):
    """
    Tournament 1: old=60 (DEFAULT_BASELINE), new=68 → threshold 65 crossed → 1 notification.
    Tournament 2: old=68, new≈74 → 65 not re-crossed (68 > 65 already), 75 not crossed → 0.
    Total: exactly 1 notification.
    """
    player = _player(test_db)
    _license(test_db, player)

    tournament_1 = _tournament(test_db)
    _skill_mapping(test_db, tournament_1, "dribbling")

    tournament_2 = _tournament(test_db)
    _skill_mapping(test_db, tournament_2, "dribbling")

    mock_thresholds = {65: "Intermediate", 75: "Advanced", 90: "Expert"}

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        mock_settings.ENABLE_SKILL_TIER_NOTIFICATIONS = True
        mock_settings.SKILL_TIER_THRESHOLDS = mock_thresholds

        _run_tournament(test_db, player, tournament_1)

        # After tournament 1: expect 1 notification (Intermediate crossed)
        notifs_after_1 = _notifications(test_db, player)
        assert len(notifs_after_1) == 1, (
            f"Expected 1 notification after tournament 1, got {len(notifs_after_1)}"
        )

        _run_tournament(test_db, player, tournament_2)

    # After tournament 2: still exactly 1 notification (no new threshold crossed)
    notifs_total = _notifications(test_db, player)
    assert len(notifs_total) == 1, (
        f"Expected 1 total notification after 2 tournaments, got {len(notifs_total)}"
    )


# ── TIER-I-04: notification field content assertions ──────────────────────────

def test_tier_i04_notification_fields_contain_expected_content(test_db: Session):
    """
    Notification title must contain the skill name (human-readable).
    Notification message must contain the tier name and percentage.
    """
    player = _player(test_db)
    _license(test_db, player)
    tournament = _tournament(test_db)
    _skill_mapping(test_db, tournament, "dribbling")  # skill_name → "Dribbling" readable

    with patch(f"{_BASE}.settings") as mock_settings:
        mock_settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION = True
        mock_settings.ENABLE_SKILL_TIER_NOTIFICATIONS = True
        mock_settings.SKILL_TIER_THRESHOLDS = {
            65: "Intermediate", 75: "Advanced", 90: "Expert"
        }
        _run_tournament(test_db, player, tournament)

    notifs = _notifications(test_db, player)
    assert len(notifs) == 1
    notif = notifs[0]

    assert "Dribbling" in notif.title, (
        f"Title should contain skill name 'Dribbling', got: {notif.title!r}"
    )
    assert "Intermediate" in notif.message, (
        f"Message should contain tier name 'Intermediate', got: {notif.message!r}"
    )
    assert "68" in notif.message, (
        f"Message should contain percentage '68', got: {notif.message!r}"
    )
    assert notif.related_semester_id == tournament.id, (
        f"related_semester_id should be {tournament.id}, got {notif.related_semester_id}"
    )
