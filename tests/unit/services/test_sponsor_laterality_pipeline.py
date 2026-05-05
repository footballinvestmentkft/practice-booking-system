"""
Sponsor Promote → Foot Score → Laterality Bridge Pipeline Tests.

Tests: LAT-SPON-01 through LAT-SPON-10

Scope:
  - foot_dominance (SponsorAudienceEntry) correctly maps to
    UserLicense.right_foot_score / left_foot_score via promote_entries()
  - Baseline write conditions (dob, position, consent guard)
  - right_foot_score / left_foot_score correctly weights lateral_components
    after a tournament with a foot-specific passing preset
  - Idempotence and email-dedup of promote_entries()

foot_dominance scale: 0 = left-dominant, 100 = right-dominant, 50 = balanced.

All tests use test_db (function-scoped SAVEPOINT) — each test creates its own
sponsor / campaign / entries / presets inline; no session-scoped seeds needed.
"""
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.club import CsvImportLog
from app.models.game_configuration import GameConfiguration
from app.models.game_preset import GamePreset
from app.models.license import UserLicense
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.sponsor import Sponsor, SponsorCampaign, SponsorAudienceEntry
from app.models.tournament_achievement import TournamentSkillMapping
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from app.services.sponsor_promote_service import promote_entries
from app.services.tournament.tournament_reward_orchestrator import distribute_rewards_for_user


# ── Helpers ───────────────────────────────────────────────────────────────────

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _make_admin(db: Session) -> User:
    admin = User(
        email=f"spon-admin-{_uid()}@lat.test",
        name="Sponsor Admin",
        password_hash=get_password_hash("Admin123!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(admin)
    db.flush()
    return admin


def _make_import_log(db: Session, sponsor: Sponsor, campaign: SponsorCampaign) -> CsvImportLog:
    """Create a minimal CsvImportLog required by sponsor_audience_entries.import_log_id NOT NULL."""
    log = CsvImportLog(
        sponsor_id=sponsor.id,
        campaign_id=campaign.id,
        filename="lat_test.csv",
        total_rows=1,
        rows_created=1,
        rows_updated=0,
        rows_skipped=0,
        rows_failed=0,
        errors=[],
        status="DONE",
    )
    db.add(log)
    db.flush()
    return log


def _make_sponsor_campaign(db: Session, admin: User) -> tuple:
    """Create Sponsor + SponsorCampaign + CsvImportLog. Returns (sponsor, campaign, import_log)."""
    sponsor = Sponsor(
        name=f"LatTest Sponsor {_uid()}",
        code=f"LAT-{_uid()}",
        is_active=True,
        created_by=admin.id,
    )
    db.add(sponsor)
    db.flush()

    campaign = SponsorCampaign(
        sponsor_id=sponsor.id,
        name=f"LatTest Campaign {_uid()}",
        status="ACTIVE",
        specialization_type="LFA_FOOTBALL_PLAYER",
        credit_grant_amount=200,
        unlock_cost=100,
        created_by=admin.id,
    )
    db.add(campaign)
    db.flush()

    import_log = _make_import_log(db, sponsor, campaign)
    return sponsor, campaign, import_log


def _make_entry(
    db: Session,
    sponsor: Sponsor,
    campaign: SponsorCampaign,
    import_log: CsvImportLog,
    *,
    email: str | None = None,
    foot_dominance: int | None = 50,
    position: str = "STRIKER",
    date_of_birth: date | None = date(2000, 1, 1),
    consent_given: bool = True,
    status: str = "ACTIVE",
) -> SponsorAudienceEntry:
    entry = SponsorAudienceEntry(
        sponsor_id=sponsor.id,
        campaign_id=campaign.id,
        import_log_id=import_log.id,
        email=email or f"lat-{_uid()}@lat.test",
        first_name="Test",
        last_name="User",
        date_of_birth=date_of_birth,
        position=position,
        foot_dominance=foot_dominance,
        consent_given=consent_given,
        status=status,
    )
    db.add(entry)
    db.flush()
    return entry


def _load_license(db: Session, user_id: int) -> UserLicense:
    lic = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    assert lic is not None, f"Active LFA license not found for user_id={user_id}"
    return lic


def _make_passing_preset(db: Session, foot_context: str) -> GamePreset:
    """Create a test-local passing preset with the given foot_context."""
    preset = GamePreset(
        code=f"lat-pass-{foot_context}-{_uid()}",
        name=f"Inline Passing ({foot_context})",
        description=f"Test-local passing preset foot_context={foot_context}",
        game_config={
            "version": "1.0",
            "metadata": {
                "game_category": "FOOTBALL",
                "difficulty_level": "intermediate",
                "min_players": 2,
                "recommended_player_count": {"min": 2, "max": 16},
            },
            "skill_config": {
                "foot_context": foot_context,
                "skills_tested": ["passing"],
                "skill_weights": {"passing": 1.5},
            },
            "format_config": {},
            "simulation_config": {},
        },
        is_active=True,
        is_recommended=False,
        is_locked=False,
    )
    db.add(preset)
    db.flush()
    return preset


def _make_tournament(db: Session, preset_id: int, skills: list) -> Semester:
    sem = Semester(
        code=f"LAT-SPON-{_uid()}",
        name="LatSpon Passing Trial",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.TOURNAMENT,
        enrollment_cost=0,
    )
    db.add(sem)
    db.flush()

    for skill in skills:
        db.add(TournamentSkillMapping(
            semester_id=sem.id,
            skill_name=skill,
            skill_category="football_skill",
            weight=1.0,
        ))

    db.add(GameConfiguration(semester_id=sem.id, game_preset_id=preset_id))
    db.flush()
    db.refresh(sem)
    return sem


# ── LAT-SPON-01..05 — Baseline foot score writing ────────────────────────────

class TestSponsorBaselineFootScore:

    def test_lat_spon_01_foot_dominance_85_right_dominant(self, test_db: Session):
        """LAT-SPON-01: foot_dominance=85 → right_foot_score=85.0, left_foot_score=15.0."""
        admin = _make_admin(test_db)
        sponsor, campaign, import_log = _make_sponsor_campaign(test_db, admin)
        entry = _make_entry(test_db, sponsor, campaign, import_log, foot_dominance=85)

        result = promote_entries(
            [entry.id], sponsor.id, test_db, admin, campaign_id=campaign.id
        )

        assert result.promoted == 1
        assert result.promoted_with_onboarding == 1

        test_db.refresh(entry)
        lic = _load_license(test_db, entry.user_id)
        assert lic.right_foot_score == 85.0
        assert lic.left_foot_score == 15.0

    def test_lat_spon_02_foot_dominance_15_left_dominant(self, test_db: Session):
        """LAT-SPON-02: foot_dominance=15 → right_foot_score=15.0, left_foot_score=85.0."""
        admin = _make_admin(test_db)
        sponsor, campaign, import_log = _make_sponsor_campaign(test_db, admin)
        entry = _make_entry(test_db, sponsor, campaign, import_log, foot_dominance=15)

        promote_entries([entry.id], sponsor.id, test_db, admin, campaign_id=campaign.id)

        test_db.refresh(entry)
        lic = _load_license(test_db, entry.user_id)
        assert lic.right_foot_score == 15.0
        assert lic.left_foot_score == 85.0

    def test_lat_spon_03_foot_dominance_none_defaults_to_50_50(self, test_db: Session):
        """LAT-SPON-03: foot_dominance=None → default 50.0 / 50.0 (balanced fallback)."""
        admin = _make_admin(test_db)
        sponsor, campaign, import_log = _make_sponsor_campaign(test_db, admin)
        entry = _make_entry(test_db, sponsor, campaign, import_log, foot_dominance=None)

        promote_entries([entry.id], sponsor.id, test_db, admin, campaign_id=campaign.id)

        test_db.refresh(entry)
        lic = _load_license(test_db, entry.user_id)
        assert lic.right_foot_score == 50.0
        assert lic.left_foot_score == 50.0

    def test_lat_spon_04_no_dob_baseline_not_written(self, test_db: Session):
        """LAT-SPON-04: date_of_birth=None → baseline NOT written.
        right_foot_score / left_foot_score remain None; football_skills remain None."""
        admin = _make_admin(test_db)
        sponsor, campaign, import_log = _make_sponsor_campaign(test_db, admin)
        entry = _make_entry(
            test_db, sponsor, campaign, import_log,
            foot_dominance=70,
            date_of_birth=None,
        )

        result = promote_entries(
            [entry.id], sponsor.id, test_db, admin, campaign_id=campaign.id
        )

        assert result.promoted == 1
        assert result.promoted_with_onboarding == 0
        assert result.promoted_without_onboarding == 1

        test_db.refresh(entry)
        lic = _load_license(test_db, entry.user_id)
        assert lic.right_foot_score is None
        assert lic.left_foot_score is None
        assert lic.football_skills is None

    def test_lat_spon_05_invalid_position_baseline_not_written_user_created(
        self, test_db: Session
    ):
        """LAT-SPON-05: position=COACH (not in VALID_POSITIONS) → baseline NOT written.
        User and license are still created; credits are still issued."""
        admin = _make_admin(test_db)
        sponsor, campaign, import_log = _make_sponsor_campaign(test_db, admin)
        entry = _make_entry(
            test_db, sponsor, campaign, import_log,
            foot_dominance=70,
            position="COACH",
        )

        result = promote_entries(
            [entry.id], sponsor.id, test_db, admin, campaign_id=campaign.id
        )

        assert result.promoted == 1
        assert result.promoted_with_onboarding == 0
        assert result.promoted_without_onboarding == 1

        test_db.refresh(entry)
        assert entry.user_id is not None

        lic = _load_license(test_db, entry.user_id)
        assert lic is not None
        assert lic.football_skills is None
        assert lic.right_foot_score is None


# ── LAT-SPON-06..08 — Foot score → lateral aggregation bridge ────────────────

class TestSponsorLateralityBridge:
    """Full pipeline: sponsor promote sets foot scores → tournament with
    foot-specific passing preset → lateral_components weighted by those scores."""

    def _promote_and_load(
        self,
        db: Session,
        foot_dominance: int,
        position: str = "MIDFIELDER",
    ) -> tuple:
        """Promote one entry and return (user, license)."""
        admin = _make_admin(db)
        sponsor, campaign, import_log = _make_sponsor_campaign(db, admin)
        entry = _make_entry(
            db, sponsor, campaign, import_log,
            foot_dominance=foot_dominance,
            position=position,
        )
        promote_entries([entry.id], sponsor.id, db, admin, campaign_id=campaign.id)
        db.refresh(entry)
        user = db.query(User).filter(User.id == entry.user_id).first()
        lic = _load_license(db, user.id)
        return user, lic

    def test_lat_spon_06_strong_right_right_preset_updates_right_bucket(
        self, test_db: Session
    ):
        """LAT-SPON-06: foot_dominance=85 + right-foot passing preset
        → passing.lateral_components['right'] bucket created."""
        user, lic = self._promote_and_load(test_db, foot_dominance=85)
        assert lic.right_foot_score == 85.0

        preset = _make_passing_preset(test_db, "right")
        tournament = _make_tournament(test_db, preset.id, ["passing"])

        distribute_rewards_for_user(
            db=test_db,
            user_id=user.id,
            tournament_id=tournament.id,
            placement=1,
            total_participants=4,
        )

        test_db.refresh(lic)
        passing_entry = lic.football_skills.get("passing", {})
        assert isinstance(passing_entry, dict), \
            "passing skill must be a dict after write-back"
        lateral = passing_entry.get("lateral_components", {})
        assert "right" in lateral, f"'right' bucket missing; lateral={lateral}"
        assert lateral["right"]["tournament_count"] == 1

    def test_lat_spon_07_strong_left_left_preset_updates_left_bucket(
        self, test_db: Session
    ):
        """LAT-SPON-07: foot_dominance=15 + left-foot passing preset
        → passing.lateral_components['left'] bucket created."""
        user, lic = self._promote_and_load(test_db, foot_dominance=15)
        assert lic.left_foot_score == 85.0

        preset = _make_passing_preset(test_db, "left")
        tournament = _make_tournament(test_db, preset.id, ["passing"])

        distribute_rewards_for_user(
            db=test_db,
            user_id=user.id,
            tournament_id=tournament.id,
            placement=1,
            total_participants=4,
        )

        test_db.refresh(lic)
        passing_entry = lic.football_skills.get("passing", {})
        lateral = passing_entry.get("lateral_components", {})
        assert "left" in lateral, f"'left' bucket missing; lateral={lateral}"
        assert lateral["left"]["tournament_count"] == 1

    def test_lat_spon_08_balanced_right_then_left_symmetric_aggregate(
        self, test_db: Session
    ):
        """LAT-SPON-08: foot_dominance=50 + right preset then left preset
        → R=L=0.5 weights → current_level = (0.5*right + 0.5*left) / 1.0."""
        user, lic = self._promote_and_load(test_db, foot_dominance=50)
        assert lic.right_foot_score == 50.0
        assert lic.left_foot_score == 50.0

        # Right tournament first
        preset_r = _make_passing_preset(test_db, "right")
        t_r = _make_tournament(test_db, preset_r.id, ["passing"])
        distribute_rewards_for_user(
            db=test_db, user_id=user.id, tournament_id=t_r.id,
            placement=1, total_participants=4,
        )

        # Left tournament second
        preset_l = _make_passing_preset(test_db, "left")
        t_l = _make_tournament(test_db, preset_l.id, ["passing"])
        distribute_rewards_for_user(
            db=test_db, user_id=user.id, tournament_id=t_l.id,
            placement=1, total_participants=4,
        )

        test_db.refresh(lic)
        entry = lic.football_skills.get("passing", {})
        lateral = entry.get("lateral_components", {})
        assert "right" in lateral and "left" in lateral, (
            f"Both right and left buckets expected; got: {list(lateral.keys())}"
        )

        r_level = lateral["right"]["level"]
        l_level = lateral["left"]["level"]
        expected_agg = (0.5 * r_level + 0.5 * l_level) / 1.0

        assert entry["current_level"] == pytest.approx(expected_agg, abs=0.2), (
            f"current_level={entry['current_level']} != symmetric avg={expected_agg} "
            f"(right={r_level}, left={l_level})"
        )


# ── LAT-SPON-09..10 — Idempotence + email dedup ──────────────────────────────

class TestSponsorPromoteEdgeCases:

    def test_lat_spon_09_already_promoted_entry_idempotent(self, test_db: Session):
        """LAT-SPON-09: Second promote call on already-promoted entry → already_linked.
        foot_scores are NOT overwritten."""
        admin = _make_admin(test_db)
        sponsor, campaign, import_log = _make_sponsor_campaign(test_db, admin)
        entry = _make_entry(test_db, sponsor, campaign, import_log, foot_dominance=80)

        r1 = promote_entries([entry.id], sponsor.id, test_db, admin, campaign_id=campaign.id)
        assert r1.promoted == 1

        test_db.refresh(entry)
        lic = _load_license(test_db, entry.user_id)
        assert lic.right_foot_score == 80.0

        # Second promote must be a full no-op
        r2 = promote_entries([entry.id], sponsor.id, test_db, admin, campaign_id=campaign.id)
        assert r2.promoted == 0
        assert r2.already_linked == 1

        test_db.refresh(lic)
        assert lic.right_foot_score == 80.0

    def test_lat_spon_10_existing_user_email_no_duplicate_license_correct(
        self, test_db: Session
    ):
        """LAT-SPON-10: Entry email matches pre-existing User → user not duplicated.
        A new LFA license is created for the existing user with foot scores set."""
        admin = _make_admin(test_db)

        existing_email = f"existing-{_uid()}@lat.test"
        existing_user = User(
            email=existing_email,
            name="Pre-Existing User",
            password_hash=get_password_hash("Test123!"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(existing_user)
        test_db.flush()
        existing_id = existing_user.id

        sponsor, campaign, import_log = _make_sponsor_campaign(test_db, admin)
        entry = _make_entry(
            test_db, sponsor, campaign, import_log,
            email=existing_email,
            foot_dominance=65,
        )

        result = promote_entries(
            [entry.id], sponsor.id, test_db, admin, campaign_id=campaign.id
        )

        assert result.promoted == 1

        # The pre-existing user must be reused — no duplicate
        user_count = (
            test_db.query(User).filter(User.email == existing_email).count()
        )
        assert user_count == 1, (
            f"Expected 1 user with email {existing_email!r}, got {user_count}"
        )

        test_db.refresh(entry)
        assert entry.user_id == existing_id

        lic = _load_license(test_db, existing_id)
        assert lic.right_foot_score == 65.0
        assert lic.left_foot_score == 35.0
