"""
Sponsor Audience P2-D — Baseline Onboarding Tests (SPON-D-01 through SPON-D-07)

  SPON-D-01  Full tournament-ready promote → 29-key football_skills, onboarding_completed=True
  SPON-D-02  Invalid position on entry → User created, onboarding NOT set
  SPON-D-03  Missing DOB → User created, onboarding NOT set
  SPON-D-04  Existing User with onboarding already set → football_skills NOT overwritten
  SPON-D-05  _build_baseline_football_skills() passes effective_onboarding gate (29 keys, correct structure)
  SPON-D-06  CSV parse: invalid position → NULL + warning; valid position → canonical stored
  SPON-D-07  Existing User DOB not modified on promote

DONE = pytest tests/integration/web_flows/test_sponsor_baseline.py -v
"""
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.license import UserLicense
from app.models.sponsor import Sponsor, SponsorAudienceEntry, SponsorCampaign
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from app.services.sponsor_promote_service import (
    PromoteResult,
    _build_baseline_football_skills,
    _should_write_baseline,
    promote_entries,
)
from app.services.sponsor_csv_import_service import (
    _parse_position,
    _parse_foot_dominance,
    apply_import,
)
from app.skills_config import get_all_skill_keys


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_admin(db: Session) -> User:
    u = User(
        email=f"admin-d+{uuid.uuid4().hex[:8]}@lfa.com",
        name="D Admin",
        password_hash=get_password_hash("Admin1234!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_sponsor(db: Session, admin: User) -> Sponsor:
    s = Sponsor(
        name=f"D Sponsor {uuid.uuid4().hex[:6]}",
        code=f"DSP-{uuid.uuid4().hex[:5].upper()}",
        is_active=True,
        created_by=admin.id,
    )
    db.add(s)
    db.flush()
    return s


def _make_campaign(db: Session, sponsor: Sponsor, admin: User) -> SponsorCampaign:
    c = SponsorCampaign(
        sponsor_id=sponsor.id,
        name=f"BL Campaign {uuid.uuid4().hex[:4]}",
        campaign_type="IMPORT",
        status="ACTIVE",
        created_by=admin.id,
    )
    db.add(c)
    db.flush()
    return c


def _make_entry(
    db: Session,
    sponsor: Sponsor,
    admin: User,
    *,
    status: str = "ACTIVE",
    consent_given: bool = True,
    date_of_birth: date | None = date(2005, 6, 15),
    position: str | None = "STRIKER",
    foot_dominance: int | None = 70,
    email: str | None = None,
    user_id: int | None = None,
) -> SponsorAudienceEntry:
    from app.models.club import CsvImportLog
    campaign = _make_campaign(db, sponsor, admin)
    log = CsvImportLog(
        sponsor_id=sponsor.id,
        campaign_id=campaign.id,
        filename="test.csv",
        total_rows=1,
        uploaded_by=admin.id,
    )
    db.add(log)
    db.flush()

    e = SponsorAudienceEntry(
        sponsor_id=sponsor.id,
        campaign_id=campaign.id,
        import_log_id=log.id,
        first_name="Test",
        last_name="Player",
        email=email or f"d+{uuid.uuid4().hex[:8]}@test.com",
        status=status,
        consent_given=consent_given,
        date_of_birth=date_of_birth,
        position=position,
        foot_dominance=foot_dominance,
        user_id=user_id,
    )
    db.add(e)
    db.flush()
    return e


# ── SPON-D-01 ─────────────────────────────────────────────────────────────────

class TestBaselineWritten:
    """SPON-D-01: full tournament-ready promote writes 29-key baseline."""

    def test_spon_d_01_onboarding_set_on_full_promote(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        entry = _make_entry(test_db, sponsor, admin)
        test_db.commit()

        result = promote_entries([entry.id], sponsor.id, test_db, admin)

        assert result.promoted == 1
        assert result.promoted_with_onboarding == 1
        assert result.promoted_without_onboarding == 0

        test_db.expire(entry)
        assert entry.user_id is not None

        lic = test_db.query(UserLicense).filter(UserLicense.user_id == entry.user_id).first()
        assert lic is not None
        assert lic.onboarding_completed is True
        assert lic.football_skills is not None

        # Exactly 29 keys, all matching skills_config
        expected_keys = set(get_all_skill_keys())
        assert set(lic.football_skills.keys()) == expected_keys

        # Structure check on one skill
        sample = lic.football_skills["ball_control"]
        assert sample["system_baseline"] == 60.0
        assert sample["current_level"] == 60.0
        assert sample["total_delta"] == 0.0
        assert "last_updated" in sample

        # foot_dominance applied
        assert lic.right_foot_score == 70.0
        assert lic.left_foot_score == 30.0


# ── SPON-D-02 ─────────────────────────────────────────────────────────────────

class TestInvalidPositionNoOnboarding:
    """SPON-D-02: invalid/NULL position → User created, baseline NOT set."""

    def test_spon_d_02_null_position_skips_baseline(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        # position=None simulates CSV with invalid/missing position
        entry = _make_entry(test_db, sponsor, admin, position=None)
        test_db.commit()

        result = promote_entries([entry.id], sponsor.id, test_db, admin)

        assert result.promoted == 1
        assert result.promoted_with_onboarding == 0
        assert result.promoted_without_onboarding == 1

        test_db.expire(entry)
        assert entry.user_id is not None

        lic = test_db.query(UserLicense).filter(UserLicense.user_id == entry.user_id).first()
        assert lic is not None
        assert lic.onboarding_completed is False
        assert lic.football_skills is None


# ── SPON-D-03 ─────────────────────────────────────────────────────────────────

class TestMissingDobNoOnboarding:
    """SPON-D-03: missing DOB → User created, baseline NOT set."""

    def test_spon_d_03_missing_dob_skips_baseline(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        entry = _make_entry(test_db, sponsor, admin, date_of_birth=None)
        test_db.commit()

        result = promote_entries([entry.id], sponsor.id, test_db, admin)

        assert result.promoted == 1
        assert result.promoted_with_onboarding == 0

        test_db.expire(entry)
        lic = test_db.query(UserLicense).filter(UserLicense.user_id == entry.user_id).first()
        assert lic.onboarding_completed is False
        assert lic.football_skills is None


# ── SPON-D-04 ─────────────────────────────────────────────────────────────────

class TestExistingOnboardingNotOverwritten:
    """SPON-D-04: existing User with onboarding already complete → football_skills NOT overwritten."""

    def test_spon_d_04_existing_onboarding_preserved(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)

        existing_user = User(
            email=f"existing-d+{uuid.uuid4().hex[:6]}@lfa.com",
            name="Existing Player",
            password_hash=get_password_hash("Pass1234!"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(existing_user)
        test_db.flush()

        original_skills = {"ball_control": {"current_level": 75.0, "system_baseline": 60.0}}
        existing_lic = UserLicense(
            user_id=existing_user.id,
            specialization_type="LFA_FOOTBALL_PLAYER",
            current_level=1,
            max_achieved_level=1,
            started_at=datetime.now(timezone.utc),
            is_active=True,
            onboarding_completed=True,
            football_skills=original_skills,
        )
        test_db.add(existing_lic)
        test_db.flush()

        entry = _make_entry(test_db, sponsor, admin, email=existing_user.email)
        test_db.commit()

        result = promote_entries([entry.id], sponsor.id, test_db, admin)

        assert result.promoted == 1
        assert result.promoted_with_onboarding == 0
        assert result.promoted_without_onboarding == 1

        test_db.expire(existing_lic)
        assert existing_lic.football_skills == original_skills


# ── SPON-D-05 ─────────────────────────────────────────────────────────────────

class TestBaselineStructure:
    """SPON-D-05: _build_baseline_football_skills() produces valid structure that passes effective_onboarding gate."""

    def test_spon_d_05_baseline_passes_effective_onboarding_gate(self, test_db: Session):
        skills = _build_baseline_football_skills()

        # 29 keys
        expected = set(get_all_skill_keys())
        assert set(skills.keys()) == expected
        assert len(skills) == 29

        # Every value is a dict with all required sub-keys
        required_sub_keys = {
            "system_baseline", "self_assessment", "baseline", "current_level",
            "total_delta", "tournament_delta", "assessment_delta",
            "last_updated", "assessment_count", "tournament_count",
        }
        for key, val in skills.items():
            assert isinstance(val, dict), f"skill {key} must be a dict"
            assert required_sub_keys.issubset(val.keys()), f"skill {key} missing sub-keys"
            assert val["system_baseline"] == 60.0
            assert val["current_level"] == 60.0
            assert val["total_delta"] == 0.0

        # Verify passes effective_onboarding gate: football_skills is not None
        assert skills is not None  # gate: license.football_skills is not None → passes


# ── SPON-D-06 ─────────────────────────────────────────────────────────────────

class TestCsvPositionParse:
    """SPON-D-06: CSV parse — invalid position → NULL + warning; valid → canonical."""

    def test_spon_d_06_invalid_position_returns_none_with_warning(self):
        pos, warnings = _parse_position("Forward")
        assert pos is None
        assert len(warnings) == 1
        assert "unknown position" in warnings[0]
        assert "Forward" in warnings[0]

    def test_spon_d_06_empty_position_returns_none_with_warning(self):
        pos, warnings = _parse_position("")
        assert pos is None
        assert len(warnings) == 1
        assert "missing" in warnings[0]

    def test_spon_d_06_valid_position_stored_canonical(self):
        for raw, expected in [
            ("striker", "STRIKER"),
            ("MIDFIELDER", "MIDFIELDER"),
            ("Defender", "DEFENDER"),
            ("goalkeeper", "GOALKEEPER"),
        ]:
            pos, warnings = _parse_position(raw)
            assert pos == expected, f"Expected {expected} for '{raw}'"
            assert warnings == []

    def test_spon_d_06_foot_dominance_valid(self):
        fd, warnings = _parse_foot_dominance("70")
        assert fd == 70
        assert warnings == []

    def test_spon_d_06_foot_dominance_out_of_range(self):
        fd, warnings = _parse_foot_dominance("150")
        assert fd is None
        assert "out of range" in warnings[0]

    def test_spon_d_06_foot_dominance_not_int(self):
        fd, warnings = _parse_foot_dominance("left")
        assert fd is None
        assert "not an integer" in warnings[0]


# ── SPON-D-07 ─────────────────────────────────────────────────────────────────

class TestExistingUserDobPreserved:
    """SPON-D-07: existing User DOB not modified on promote."""

    def test_spon_d_07_existing_user_dob_not_overwritten(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)

        original_dob = date(1990, 3, 20)
        entry_dob = date(2005, 6, 15)  # different from user's real DOB

        existing_user = User(
            email=f"dob-d+{uuid.uuid4().hex[:6]}@lfa.com",
            name="Dob Player",
            date_of_birth=datetime(1990, 3, 20),
            password_hash=get_password_hash("Pass1234!"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(existing_user)
        test_db.flush()

        entry = _make_entry(test_db, sponsor, admin, email=existing_user.email, date_of_birth=entry_dob)
        test_db.commit()

        promote_entries([entry.id], sponsor.id, test_db, admin)

        test_db.expire(existing_user)
        # DOB on User must NOT have changed
        stored_dob = existing_user.date_of_birth
        if hasattr(stored_dob, "date"):
            stored_dob = stored_dob.date()
        assert stored_dob == original_dob
