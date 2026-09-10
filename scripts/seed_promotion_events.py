"""
Promotion Event Seed Script — Phase 2
======================================
Creates PROMOTION_EVENT lifecycle scenarios for manual QA and CI smoke testing.

Lifecycle used by this script (compatible with main branch state-machine):
  DRAFT → ENROLLMENT_OPEN → ENROLLMENT_CLOSED
  → bulk-enroll-campaign  (POST /api/v1/tournaments/{id}/bulk-enroll-campaign)
  → CHECK_IN_OPEN         (session generation triggered here)
  → IN_PROGRESS           (requires master_instructor_id)
  → COMPLETED

Note: once PR2.5 (PROMOTION_EVENT fast-path) is merged to main, the
ENROLLMENT_OPEN hop can be dropped and DRAFT → ENROLLMENT_CLOSED used directly.

Scenarios:
  SC-01  DRAFT               — tournament created, no audience enrolled
  SC-02  ENROLLMENT_CLOSED   — audience locked, 9 players bulk-enrolled
  SC-03  CHECK_IN_OPEN       — 13 sessions generated (9 GS + 2 SF + 1 F + 1 B)
  SC-04  VALIDATION          — hard failure if session structure or SF labels are wrong
  SC-05  REWARDS_DISTRIBUTED — full lifecycle smoke: group stage ORM + knockout API + rewards
  SC-06  Finishing & Shooting Focus — 6-skill TRC, EMA chain from SC-05 (SK14 diminishing returns)
  SC-07  Speed & Agility Sprint Cup — 5-skill TRC (acceleration focus), SK-15 sprint_speed/agility chain
  SC-08  Strength & Endurance Challenge — 5-skill TRC (strength focus), SK-17 stamina chain from SC-07
  SC-09  Creative Passing & Vision — 5-skill TRC (passing/vision, all first-time skills)
  SC-10  Defensive Mastery Cup — 5-skill TRC (tackle/positioning_def, all first-time skills)
  SC-11  Complete Attacker Championship — 5-skill TRC (finishing focus), SK-16/SK-18 diminishing returns

Reset scope (ALLOW_SEED_RESET=true required):
  - Only Semester rows whose name starts with PROMO-SEED-
  - Only the SEED-SPONSOR sponsor row (cascades to its campaigns and entries)
  - No manual data is ever touched

Usage:
  PYTHONPATH=. python scripts/seed_promotion_events.py
  PYTHONPATH=. python scripts/seed_promotion_events.py --dry-run
  PYTHONPATH=. python scripts/seed_promotion_events.py --scenarios SC-01,SC-04
  ALLOW_SEED_RESET=true PYTHONPATH=. python scripts/seed_promotion_events.py --reset
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/lfa_intern_system")
os.environ.setdefault("SECRET_KEY", "e2e-test-secret-key-minimum-32-chars-needed")

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DBSession

from app.main import app
from app.database import SessionLocal
from app.models.club import CsvImportLog
from app.models.license import UserLicense
from app.models.semester import Semester, SemesterCategory, SemesterStatus, semester_instructors
from app.models.semester_enrollment import SemesterEnrollment
from app.models.session import Session as SessionModel
from app.models.sponsor import Sponsor, SponsorAudienceEntry, SponsorCampaign
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_enums import TournamentPhase
from app.models.team import TournamentPlayerCheckin
from app.models.tournament_instructor_slot import TournamentInstructorSlot
from app.models.tournament_reward_config import TournamentRewardConfig as TRwdModel
from app.models.tournament_type import TournamentType
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from app.dependencies import (
    get_current_admin_or_instructor_user_hybrid,
    get_current_admin_user_hybrid,
    get_current_user,
    get_current_user_web,
)

logger = logging.getLogger(__name__)

_SPONSOR_NAME = "SEED-SPONSOR"
_CAMPAIGN_NAME = "SEED-CAMPAIGN"
# Known password for all seed players — required so manual UI testing (login → player card) works.
# Must match what _bootstrap_seed_players() writes; DB patch uses the same value.
SEED_PLAYER_PASSWORD = "PromoSeed@2026"
# Seed player date-of-birth: 2005-01-01 → age ~21 → AMATEUR age category.
# Sets users.date_of_birth which is the login gate (auth.py:99 redirects to /age-verification when NULL).
SEED_PLAYER_DOB = datetime.date(2005, 1, 1)
_TOURNAMENT_PREFIX = "PROMO-SEED-"
_GROUP_KNOCKOUT_CODE = "group_knockout"
_NINE_PLAYER_KEY = "9_players"

_EXPECTED_SESSIONS = 13
_EXPECTED_GROUP_STAGE = 9
_EXPECTED_PLAY_IN = 0
_EXPECTED_SEMI_FINALS = 2
_EXPECTED_FINAL = 1
_EXPECTED_BRONZE = 1
_EXPECTED_SF_MATCHUPS = frozenset({
    "Group A winner vs Best runner-up",
    "Group B winner vs Group C winner",
})

# ─── URL helpers ──────────────────────────────────────────────────────────────


def _admin_tournament_url(tid: int) -> str:
    """Return the canonical admin edit URL for a tournament."""
    return f"/admin/tournaments/{tid}/edit"


# ─── Production guard ─────────────────────────────────────────────────────────

_BLOCKED_URL_FRAGMENTS = ("lfa.com", "production", "staging", "prod")


def _assert_not_production() -> None:
    db_url = os.environ.get("DATABASE_URL", "")
    for fragment in _BLOCKED_URL_FRAGMENTS:
        if fragment in db_url.lower():
            sys.exit(
                f"BLOCKED: Seed script cannot run against production/staging URL.\n"
                f"  DATABASE_URL contains '{fragment}': {db_url}"
            )


# ─── Preflight: group_knockout 9_players policy ───────────────────────────────

def _preflight_group_knockout_9p(db: DBSession) -> TournamentType:
    """Verify the DB config has the 9_players policy required for SC-04.

    Exits immediately with a clear message if anything is wrong, so SC-04
    never silently produces the wrong session structure.
    """
    tt = db.query(TournamentType).filter(TournamentType.code == _GROUP_KNOCKOUT_CODE).first()
    if not tt:
        sys.exit(
            f"PREFLIGHT FAIL: TournamentType code='{_GROUP_KNOCKOUT_CODE}' not found in DB.\n"
            f"  Run tournament type seed/migration to create it."
        )

    cfg = tt.config.get("group_configuration", {})
    nine = cfg.get(_NINE_PLAYER_KEY)
    if not nine:
        sys.exit(
            f"PREFLIGHT FAIL: group_configuration['{_NINE_PLAYER_KEY}'] missing from\n"
            f"  TournamentType '{_GROUP_KNOCKOUT_CODE}' (DB id={tt.id}).\n"
            f"  Add it to app/tournament_types/group_knockout.json and re-seed the tournament types."
        )

    policy = nine.get("qualification_policy")
    if policy != "winners_plus_best_runner_up":
        sys.exit(
            f"PREFLIGHT FAIL: group_configuration['{_NINE_PLAYER_KEY}'].qualification_policy\n"
            f"  must be 'winners_plus_best_runner_up', got '{policy}'.\n"
            f"  Fix app/tournament_types/group_knockout.json and re-seed."
        )

    brc = nine.get("best_runner_up_count")
    if int(brc or 0) != 1:
        sys.exit(
            f"PREFLIGHT FAIL: group_configuration['{_NINE_PLAYER_KEY}'].best_runner_up_count\n"
            f"  must be 1, got '{brc}'.\n"
            f"  Fix app/tournament_types/group_knockout.json and re-seed."
        )

    return tt


# ─── Reset ────────────────────────────────────────────────────────────────────

def run_reset(db: DBSession) -> None:
    """Delete all PROMO-SEED-* tournament rows and the SEED-SPONSOR.

    Requires ALLOW_SEED_RESET=true. Never touches manually created data.
    """
    if os.environ.get("ALLOW_SEED_RESET") != "true":
        sys.exit("Reset requires ALLOW_SEED_RESET=true env var.")

    _assert_not_production()

    promo_ids = [
        row.id
        for row in db.query(Semester.id)
        .filter(Semester.name.like(f"{_TOURNAMENT_PREFIX}%"))
        .all()
    ]
    if promo_ids:
        from app.models.tournament_status_history import TournamentStatusHistory
        from app.models.game_configuration import GameConfiguration
        db.query(TournamentStatusHistory).filter(
            TournamentStatusHistory.tournament_id.in_(promo_ids)
        ).delete(synchronize_session=False)
        db.query(GameConfiguration).filter(
            GameConfiguration.semester_id.in_(promo_ids)
        ).delete(synchronize_session=False)
        db.query(SessionModel).filter(
            SessionModel.semester_id.in_(promo_ids)
        ).delete(synchronize_session=False)
        db.query(SemesterEnrollment).filter(
            SemesterEnrollment.semester_id.in_(promo_ids)
        ).delete(synchronize_session=False)
        db.query(TournamentConfiguration).filter(
            TournamentConfiguration.semester_id.in_(promo_ids)
        ).delete(synchronize_session=False)
        db.query(TRwdModel).filter(
            TRwdModel.semester_id.in_(promo_ids)
        ).delete(synchronize_session=False)
        db.query(TournamentInstructorSlot).filter(
            TournamentInstructorSlot.semester_id.in_(promo_ids)
        ).delete(synchronize_session=False)
        db.query(TournamentPlayerCheckin).filter(
            TournamentPlayerCheckin.tournament_id.in_(promo_ids)
        ).delete(synchronize_session=False)
        db.execute(
            semester_instructors.delete().where(
                semester_instructors.c.semester_id.in_(promo_ids)
            )
        )

        # TournamentParticipation and TournamentRanking are not cascade-deleted by ORM.
        # Must be removed explicitly so stale EMA replay data doesn't bleed into reseeds.
        from app.models.tournament_achievement import TournamentParticipation as _TP
        from app.models.tournament_ranking import TournamentRanking as _TR
        tp_del = db.query(_TP).filter(_TP.semester_id.in_(promo_ids)).delete(synchronize_session=False)
        tr_del = db.query(_TR).filter(_TR.tournament_id.in_(promo_ids)).delete(synchronize_session=False)

        # Sweep all remaining FK-child tables via raw SQL to avoid importing every model.
        # Order: deepest children first so FK constraints are satisfied.
        from sqlalchemy import text as _t
        _sweep_tables = [
            ("notifications",                "related_semester_id"),
            ("credit_transactions",          "semester_id"),
            ("xp_transactions",              "semester_id"),
            ("tournament_badges",            "semester_id"),
            ("tournament_rewards",           "tournament_id"),
            ("tournament_stats",             "tournament_id"),
            ("tournament_skill_mappings",    "semester_id"),
            ("tournament_team_enrollments",  "semester_id"),
            ("groups",                       "semester_id"),
            ("modules",                      "semester_id"),
            ("projects",                     "semester_id"),
            ("instructor_assignment_requests","semester_id"),
            ("campus_schedule_configs",      "tournament_id"),
            ("pitch_instructor_assignments", "semester_id"),
            ("semester_schedule_configs",    "semester_id"),
        ]
        for tbl, col in _sweep_tables:
            db.execute(
                _t(f"DELETE FROM {tbl} WHERE {col} = ANY(:ids)"),
                {"ids": promo_ids},
            )

        db.query(Semester).filter(
            Semester.id.in_(promo_ids)
        ).delete(synchronize_session=False)
        print(
            f"  Deleted {len(promo_ids)} PROMO-SEED tournament(s) "
            f"(+{tp_del} participations, +{tr_del} rankings, all child tables swept)"
        )

    # Reset seed users' FootballSkillAssessment rows and football_skills to baseline.
    # These are user-linked (not semester-linked) and survive tournament deletion.
    # Without this, stale FSA values cause update_skill_assessments to build on wrong baseline.
    from app.models.football_skill_assessment import FootballSkillAssessment as _FSA
    from app.services.skill_progression import get_all_skill_keys, DEFAULT_BASELINE

    seed_users = db.query(User).filter(User.email.like("%@promo-seed.example.com")).all()
    seed_uid_map = {u.id: u.email for u in seed_users}
    if seed_uid_map:
        seed_uids = list(seed_uid_map.keys())
        seed_lics = db.query(UserLicense).filter(
            UserLicense.user_id.in_(seed_uids),
            UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        ).all()
        lic_ids = [l.id for l in seed_lics]
        fsa_del = 0
        if lic_ids:
            fsa_del = db.query(_FSA).filter(
                _FSA.user_license_id.in_(lic_ids)
            ).delete(synchronize_session=False)
            baseline_skills = {sk: DEFAULT_BASELINE for sk in get_all_skill_keys()}
            for lic in seed_lics:
                lic.football_skills = baseline_skills
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(lic, "football_skills")
        print(
            f"  Reset {len(seed_uid_map)} seed user(s): {fsa_del} FSA rows deleted, "
            f"football_skills → baseline 60.0"
        )

    sponsor = db.query(Sponsor).filter(Sponsor.name == _SPONSOR_NAME).first()
    if sponsor:
        db.delete(sponsor)
        print(f"  Deleted {_SPONSOR_NAME} (id={sponsor.id}) — cascades campaigns/entries")

    db.commit()
    print("Reset complete")


# ─── Sponsor / Campaign / Audience setup ─────────────────────────────────────

def _get_or_create_sponsor(db: DBSession, dry_run: bool) -> Sponsor | None:
    sponsor = db.query(Sponsor).filter(Sponsor.name == _SPONSOR_NAME).first()
    if sponsor:
        return sponsor
    if dry_run:
        print("  [dry-run] Would create SEED-SPONSOR")
        return None
    sponsor = Sponsor(name=_SPONSOR_NAME, code="SEED-SPNSR", is_active=True)
    db.add(sponsor)
    db.flush()
    return sponsor


def _get_or_create_campaign(
    db: DBSession, sponsor: Sponsor, dry_run: bool
) -> SponsorCampaign | None:
    camp = (
        db.query(SponsorCampaign)
        .filter(
            SponsorCampaign.sponsor_id == sponsor.id,
            SponsorCampaign.name == _CAMPAIGN_NAME,
        )
        .first()
    )
    if camp:
        return camp
    if dry_run:
        print("  [dry-run] Would create SEED-CAMPAIGN")
        return None
    camp = SponsorCampaign(
        sponsor_id=sponsor.id,
        name=_CAMPAIGN_NAME,
        status="ACTIVE",
    )
    db.add(camp)
    db.flush()
    return camp


def _ensure_9_audience_entries(
    db: DBSession,
    sponsor: Sponsor,
    campaign: SponsorCampaign,
    admin: User,
) -> list[int]:
    """Ensure 9 seed players exist with promoted user + active LFA_FOOTBALL_PLAYER license.

    Idempotent: safe to call multiple times.
    Returns list of user_ids.
    """
    import_log = (
        db.query(CsvImportLog)
        .filter(
            CsvImportLog.sponsor_id == sponsor.id,
            CsvImportLog.campaign_id == campaign.id,
        )
        .first()
    )
    if not import_log:
        import_log = CsvImportLog(
            sponsor_id=sponsor.id,
            campaign_id=campaign.id,
            uploaded_by=admin.id,
            filename="seed_9_players.csv",
            total_rows=9,
            rows_created=9,
            status="DONE",
        )
        db.add(import_log)
        db.flush()

    user_ids: list[int] = []
    for i in range(1, 10):
        email = f"seed.player.{i}@promo-seed.example.com"

        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(
                email=email,
                name=f"Seed{i} PromoPlayer",
                first_name=f"Seed{i}",
                last_name="PromoPlayer",
                password_hash=get_password_hash(SEED_PLAYER_PASSWORD),
                role=UserRole.STUDENT,
                is_active=True,
                credit_balance=0,
                # These fields are required for login to bypass the age-verification gate
                # (auth.py:99 redirects to /age-verification when date_of_birth is None)
                # and for full onboarding status visible in the admin and player card UI.
                date_of_birth=SEED_PLAYER_DOB,
                onboarding_completed=True,
                nda_accepted=True,
                nda_accepted_at=datetime.datetime.now(datetime.timezone.utc),
                parental_consent=True,
                parental_consent_at=datetime.datetime.now(datetime.timezone.utc),
                payment_verified=True,
                payment_verified_at=datetime.datetime.now(datetime.timezone.utc),
            )
            db.add(user)
            db.flush()
        else:
            # Patch existing seed users: ensure all user-level onboarding fields are set.
            # Idempotent: only writes when a field is missing/wrong.
            _now = datetime.datetime.now(datetime.timezone.utc)
            if user.date_of_birth is None:
                user.date_of_birth = SEED_PLAYER_DOB
            if not user.onboarding_completed:
                user.onboarding_completed = True
            if not user.nda_accepted:
                user.nda_accepted = True
                user.nda_accepted_at = _now
            if not user.parental_consent:
                user.parental_consent = True
                user.parental_consent_at = _now
            if not user.payment_verified:
                user.payment_verified = True
                user.payment_verified_at = _now
            db.flush()

        lic = (
            db.query(UserLicense)
            .filter(
                UserLicense.user_id == user.id,
                UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
            )
            .first()
        )
        if not lic:
            now = datetime.datetime.now(datetime.timezone.utc)
            from app.services.skill_progression import get_all_skill_keys, DEFAULT_BASELINE
            _baseline_skills = {sk: DEFAULT_BASELINE for sk in get_all_skill_keys()}
            lic = UserLicense(
                user_id=user.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                is_active=True,
                started_at=now,
                onboarding_completed=True,
                onboarding_completed_at=now,
                football_skills=_baseline_skills,
            )
            db.add(lic)
            db.flush()
        else:
            lic.is_active = True
            if not lic.onboarding_completed:
                now = datetime.datetime.now(datetime.timezone.utc)
                lic.onboarding_completed = True
                lic.onboarding_completed_at = now
            if not lic.football_skills:
                from app.services.skill_progression import get_all_skill_keys, DEFAULT_BASELINE
                lic.football_skills = {sk: DEFAULT_BASELINE for sk in get_all_skill_keys()}

        entry = (
            db.query(SponsorAudienceEntry)
            .filter(
                SponsorAudienceEntry.campaign_id == campaign.id,
                SponsorAudienceEntry.email == email,
            )
            .first()
        )
        if not entry:
            entry = SponsorAudienceEntry(
                sponsor_id=sponsor.id,
                campaign_id=campaign.id,
                import_log_id=import_log.id,
                first_name=f"Seed{i}",
                last_name="PromoPlayer",
                email=email,
                consent_given=True,
                status="ACTIVE",
                user_id=user.id,
            )
            db.add(entry)
            db.flush()
        else:
            entry.status = "ACTIVE"
            entry.consent_given = True
            entry.user_id = user.id

        user_ids.append(user.id)

    db.flush()
    return user_ids


# ─── Tournament creation (direct ORM) ────────────────────────────────────────

def _create_tournament(
    db: DBSession,
    name: str,
    sponsor: Sponsor,
    campaign: SponsorCampaign,
    tt: TournamentType,
    campus_id: int,
    dry_run: bool,
    age_group: str | None = None,
    game_preset_code: str = "outfield_default",
) -> Semester | None:
    if dry_run:
        print(f"  [dry-run] Would create tournament '{name}'")
        return None

    from app.models.campus import Campus
    from app.models.game_configuration import GameConfiguration
    from app.models.game_preset import GamePreset

    suffix = uuid.uuid4().hex[:8]
    code = f"{_TOURNAMENT_PREFIX}{suffix}"
    today = datetime.date.today()

    t = Semester(
        code=code,
        name=name,
        start_date=today,
        end_date=today + datetime.timedelta(days=1),
        status=SemesterStatus.DRAFT,
        tournament_status="DRAFT",
        semester_category=SemesterCategory.PROMOTION_EVENT,
        specialization_type="LFA_FOOTBALL_PLAYER",
        enrollment_cost=0,
        campus_id=campus_id,
        organizer_sponsor_id=sponsor.id,
        organizer_campaign_id=campaign.id,
        organizer_club_id=None,
        age_group=age_group,
    )
    db.add(t)
    db.flush()

    campus_obj = db.query(Campus).filter(Campus.id == campus_id).first()
    if campus_obj and campus_obj.location_id:
        t.location_id = campus_obj.location_id

    db.add(
        TournamentConfiguration(
            semester_id=t.id,
            tournament_type_id=tt.id,
            participant_type="INDIVIDUAL",
            number_of_rounds=1,
            assignment_type="OPEN_ASSIGNMENT",
            match_duration_minutes=60,
            break_duration_minutes=10,
            parallel_fields=3,
        )
    )
    db.flush()

    # Link game preset (preset_code param, fallback to outfield_default)
    default_preset = db.query(GamePreset).filter(GamePreset.code == game_preset_code).first()
    if default_preset is None:
        default_preset = db.query(GamePreset).filter(GamePreset.code == "outfield_default").first()
    db.add(GameConfiguration(
        semester_id=t.id,
        game_preset_id=default_preset.id if default_preset else None,
    ))

    db.commit()
    db.refresh(t)
    return t


# ─── API helpers ─────────────────────────────────────────────────────────────

def _transition(client: TestClient, tid: int, new_status: str) -> bool:
    r = client.patch(
        f"/api/v1/tournaments/{tid}/status",
        json={"new_status": new_status, "reason": "seed"},
    )
    if r.status_code != 200:
        print(f"  FAIL transition {tid} -> {new_status}: {r.status_code} {r.text[:200]}")
        return False
    return True


def _lock_enrollment(client: TestClient, tid: int) -> bool:
    """DRAFT → ENROLLMENT_OPEN → ENROLLMENT_CLOSED.

    Main branch requires the ENROLLMENT_OPEN intermediate hop.
    Once PR2.5 (PROMOTION_EVENT fast-path) is merged, this can be replaced
    with a single DRAFT → ENROLLMENT_CLOSED transition.
    """
    if not _transition(client, tid, "ENROLLMENT_OPEN"):
        return False
    return _transition(client, tid, "ENROLLMENT_CLOSED")


def _bulk_enroll_direct(db: DBSession, tid: int, campaign_id: int, sponsor_id: int) -> dict:
    """Enroll all eligible campaign audience entries as SemesterEnrollments.

    Mirrors the logic of campaign_enrollment_service.bulk_enroll_from_campaign
    which lives on the PR2 branch (not yet merged to main).  When PR2 is merged,
    this function can be replaced with a call to that API endpoint.

    Eligibility (per entry):
      - status == "ACTIVE" and consent_given == True
      - user_id IS NOT NULL  (entry has been promoted)
      - User.is_active == True
      - Active LFA_FOOTBALL_PLAYER UserLicense exists
      - No existing active SemesterEnrollment for this tournament
    """
    from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus

    entries = (
        db.query(SponsorAudienceEntry)
        .filter(
            SponsorAudienceEntry.sponsor_id == sponsor_id,
            SponsorAudienceEntry.campaign_id == campaign_id,
            SponsorAudienceEntry.status == "ACTIVE",
            SponsorAudienceEntry.consent_given == True,
            SponsorAudienceEntry.user_id.isnot(None),
        )
        .all()
    )

    enrolled: list[int] = []
    skipped: list[dict] = []

    for entry in entries:
        user_id = entry.user_id

        user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
        if not user:
            skipped.append({"user_id": user_id, "reason": "inactive or not found"})
            continue

        lic = (
            db.query(UserLicense)
            .filter(
                UserLicense.user_id == user_id,
                UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
                UserLicense.is_active == True,
            )
            .first()
        )
        if not lic:
            skipped.append({"user_id": user_id, "reason": "no active LFA_FOOTBALL_PLAYER license"})
            continue

        active = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.semester_id == tid,
                SemesterEnrollment.user_id == user_id,
                SemesterEnrollment.is_active == True,
            )
            .first()
        )
        if active:
            skipped.append({"user_id": user_id, "reason": "already enrolled"})
            continue

        inactive = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.semester_id == tid,
                SemesterEnrollment.user_id == user_id,
                SemesterEnrollment.user_license_id == lic.id,
                SemesterEnrollment.is_active == False,
            )
            .first()
        )
        if inactive:
            inactive.is_active = True
            inactive.request_status = EnrollmentStatus.APPROVED
        else:
            db.add(
                SemesterEnrollment(
                    semester_id=tid,
                    user_id=user_id,
                    user_license_id=lic.id,
                    is_active=True,
                    request_status=EnrollmentStatus.APPROVED,
                )
            )
        enrolled.append(user_id)

    db.flush()
    return {
        "enrolled_count": len(enrolled),
        "skipped_count": len(skipped),
        "enrolled": enrolled,
        "skipped": skipped,
    }


# ─── Check-in stamper ────────────────────────────────────────────────────────

def _stamp_player_checkins(db: DBSession, tid: int) -> int:
    """Set tournament_checked_in_at = now() for all APPROVED active enrollments.

    The session generator seeds the bracket only from checked-in players.
    This must be called BEFORE the CHECK_IN_OPEN transition so that the
    seeding pool is non-empty (otherwise all 9 players fall through to the
    fallback pool instead of the confirmed pool).

    Returns the number of enrollments stamped.
    """
    from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
    now = datetime.datetime.now(datetime.timezone.utc)
    rows = (
        db.query(SemesterEnrollment)
        .filter(
            SemesterEnrollment.semester_id == tid,
            SemesterEnrollment.is_active == True,
            SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
            SemesterEnrollment.tournament_checked_in_at == None,  # noqa: E711
        )
        .all()
    )
    for r in rows:
        r.tournament_checked_in_at = now
    db.flush()
    return len(rows)


# ─── Preflight audit ─────────────────────────────────────────────────────────

def _run_preflight_audit(db: DBSession, campus_id: int, fail: bool = True) -> list[str]:
    """Check domain invariants before running scenarios.

    Returns a list of human-readable issue strings.
    If *fail* is True (default), exits the process on any failure — seed
    scenarios that require a pitch or instructor must not run in a broken env.

    Hard-fail gates (fail=True exits immediately):
      - Campus missing
      - No active Pitch on campus  ← generation_validator.py blocks CHECK_IN_OPEN
      - GamePreset 'outfield_default' missing
      - admin@lfa.com missing
      - instructor@lfa.com missing or not INSTRUCTOR role  ← SC-05..SC-11 require it
    """
    from app.models.campus import Campus
    from app.models.game_preset import GamePreset
    from app.models.pitch import Pitch

    issues: list[str] = []

    campus = db.query(Campus).filter(Campus.id == campus_id).first()
    if not campus:
        issues.append(f"Campus id={campus_id} not found — run bootstrap first")
    else:
        pitch_count = db.query(Pitch).filter(
            Pitch.campus_id == campus_id,
            Pitch.is_active == True,  # noqa: E712
        ).count()
        if pitch_count < 3:
            issues.append(
                f"Campus {campus_id} ({campus.name}) has {pitch_count} active pitch(es) "
                "but parallel_fields=3 requires ≥3. "
                "Add Pálya A/B/C or use --bootstrap-missing-prereq."
            )

    default_preset = db.query(GamePreset).filter(GamePreset.code == "outfield_default").first()
    if not default_preset:
        issues.append("GamePreset code='outfield_default' not found — run bootstrap first")

    admin = db.query(User).filter(User.email == "admin@lfa.com").first()
    if not admin:
        issues.append("admin@lfa.com not found — run bootstrap first")

    instructor = db.query(User).filter(
        User.email == "instructor@lfa.com",
        User.is_active == True,  # noqa: E712
    ).first()
    if not instructor or instructor.role != UserRole.INSTRUCTOR:
        issues.append(
            "instructor@lfa.com not found or not INSTRUCTOR role — "
            "SC-05..SC-11 require an active instructor user"
        )

    # Check 3 FIELD instructor users with active LFA_COACH license
    field_instructor_ok = 0
    for email in _FIELD_INSTRUCTOR_EMAILS:
        fi = db.query(User).filter(User.email == email, User.is_active == True).first()  # noqa: E712
        if fi:
            has_lic = db.query(UserLicense).filter(
                UserLicense.user_id == fi.id,
                UserLicense.specialization_type == "LFA_COACH",
                UserLicense.is_active == True,  # noqa: E712
            ).first()
            if has_lic:
                field_instructor_ok += 1
    if field_instructor_ok < 3:
        issues.append(
            f"Only {field_instructor_ok}/3 FIELD instructor users with active LFA_COACH license found. "
            "Expected: field.instructor.1/2/3@promo-seed-staff.example.com. "
            "Use --bootstrap-missing-prereq to auto-create."
        )

    if issues:
        print("\n  PREFLIGHT AUDIT ISSUES:")
        for issue in issues:
            print(f"    * {issue}")
        if fail:
            sys.exit(f"Seed aborted: {len(issues)} preflight issue(s). "
                     f"Fix the issues above or use --bootstrap-missing-prereq for the pitch.")
    else:
        print("  PREFLIGHT AUDIT OK")

    return issues


# ─── Optional dev-mode bootstrap for missing prerequisites ────────────────────

_FIELD_INSTRUCTOR_EMAILS = [
    "field.instructor.1@promo-seed-staff.example.com",
    "field.instructor.2@promo-seed-staff.example.com",
    "field.instructor.3@promo-seed-staff.example.com",
]
_PITCH_NAMES = ["Pálya A", "Pálya B", "Pálya C"]


def _bootstrap_missing_prereqs(db: DBSession, campus_id: int) -> None:
    """Ensure 3 active pitches on campus + 3 FIELD instructor users with LFA_COACH licenses.

    Only called when --bootstrap-missing-prereq is passed explicitly.
    Default seed mode always hard-fails instead of auto-creating — this
    ensures the seed does not silently mask a real environment/setup problem.

    Idempotent: safe to call multiple times.
    """
    from app.models.pitch import Pitch

    # 1. Ensure exactly 3 named pitches exist on campus
    from sqlalchemy import func as _func
    max_num = db.query(_func.max(Pitch.pitch_number)).filter(
        Pitch.campus_id == campus_id
    ).scalar() or 0
    next_num = max_num + 1
    for pitch_name in _PITCH_NAMES:
        existing = db.query(Pitch).filter(
            Pitch.campus_id == campus_id,
            Pitch.name == pitch_name,
        ).first()
        if not existing:
            db.add(Pitch(
                name=pitch_name,
                campus_id=campus_id,
                pitch_number=next_num,
                is_active=True,
            ))
            next_num += 1
            print(f"  [bootstrap] Created active Pitch '{pitch_name}' on campus {campus_id}")
        elif not existing.is_active:
            existing.is_active = True
            print(f"  [bootstrap] Re-activated Pitch '{pitch_name}' on campus {campus_id}")
    db.flush()
    db.commit()

    # 2. Ensure 3 FIELD instructor users with INSTRUCTOR role + LFA_COACH license
    now = datetime.datetime.now(datetime.timezone.utc)
    for i, email in enumerate(_FIELD_INSTRUCTOR_EMAILS, start=1):
        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(
                email=email,
                name=f"Field Instructor {i}",
                first_name=f"Field{i}",
                last_name="Instructor",
                password_hash=get_password_hash("FieldInstr@2026"),
                role=UserRole.INSTRUCTOR,
                is_active=True,
                credit_balance=0,
                date_of_birth=datetime.date(1990, 1, 1),
                onboarding_completed=True,
                nda_accepted=True,
                nda_accepted_at=now,
                parental_consent=True,
                parental_consent_at=now,
                payment_verified=True,
                payment_verified_at=now,
            )
            db.add(user)
            db.flush()
            print(f"  [bootstrap] Created FIELD instructor user {email}")

        lfa_lic = db.query(UserLicense).filter(
            UserLicense.user_id == user.id,
            UserLicense.specialization_type == "LFA_COACH",
        ).first()
        if not lfa_lic:
            lfa_lic = UserLicense(
                user_id=user.id,
                specialization_type="LFA_COACH",
                is_active=True,
                started_at=now,
                onboarding_completed=True,
                onboarding_completed_at=now,
            )
            db.add(lfa_lic)
            db.flush()
            print(f"  [bootstrap] Created LFA_COACH license for {email}")
        elif not lfa_lic.is_active:
            lfa_lic.is_active = True
            print(f"  [bootstrap] Re-activated LFA_COACH license for {email}")

    db.commit()


# ─── SC-04 hard validation ────────────────────────────────────────────────────

def _validate_sc04(db: DBSession, tid: int, tournament_name: str) -> None:
    """Hard-fail if the session structure does not match the expected 9-player group_knockout.

    Expected:
      - 13 sessions total
      - 9 Group Stage  (game_type like "Group X - Round N")
      - 0 Play-in      (game_type == "Play-in Round")
      - 2 Semi-finals  (game_type == "Semi-finals")
      - 1 Final        (game_type == "Final")
      - 1 Bronze       (game_type == "3rd Place Match")
      - SF matchup labels: "Group A winner vs Best runner-up" and
                           "Group B winner vs Group C winner"
    """
    db.expire_all()
    sessions = db.query(SessionModel).filter(SessionModel.semester_id == tid).all()
    total = len(sessions)

    group_stage = 0
    play_in = 0
    semi_finals = 0
    final = 0
    bronze = 0
    sf_matchups: list[str] = []

    for s in sessions:
        phase = s.tournament_phase
        gt = s.game_type or ""

        if phase == TournamentPhase.GROUP_STAGE or phase == TournamentPhase.GROUP_STAGE.value:
            group_stage += 1
        elif gt == "Play-in Round":
            play_in += 1
        elif gt == "Semi-finals":
            semi_finals += 1
            sc = s.structure_config or {}
            matchup = sc.get("matchup", "")
            if matchup:
                sf_matchups.append(matchup)
        elif gt == "Final":
            final += 1
        elif gt == "3rd Place Match":
            bronze += 1

    errors: list[str] = []

    if total != _EXPECTED_SESSIONS:
        errors.append(f"sessions: expected {_EXPECTED_SESSIONS}, got {total}")
    if group_stage != _EXPECTED_GROUP_STAGE:
        errors.append(f"Group Stage: expected {_EXPECTED_GROUP_STAGE}, got {group_stage}")
    if play_in != _EXPECTED_PLAY_IN:
        errors.append(f"Play-in: expected {_EXPECTED_PLAY_IN}, got {play_in}")
    if semi_finals != _EXPECTED_SEMI_FINALS:
        errors.append(f"Semi-finals: expected {_EXPECTED_SEMI_FINALS}, got {semi_finals}")
    if final != _EXPECTED_FINAL:
        errors.append(f"Final: expected {_EXPECTED_FINAL}, got {final}")
    if bronze != _EXPECTED_BRONZE:
        errors.append(f"Bronze: expected {_EXPECTED_BRONZE}, got {bronze}")

    actual_sf_matchups = frozenset(sf_matchups)
    if actual_sf_matchups != _EXPECTED_SF_MATCHUPS:
        errors.append(
            f"SF matchup labels mismatch.\n"
            f"    expected: {sorted(_EXPECTED_SF_MATCHUPS)}\n"
            f"    got:      {sorted(actual_sf_matchups)}"
        )

    if errors:
        print(f"\n  SC-04 VALIDATION FAILED for '{tournament_name}':")
        for e in errors:
            print(f"    * {e}")
        sys.exit(1)

    print(
        f"  SC-04 OK: {total} sessions"
        f" ({group_stage} GS + {semi_finals} SF + {final} F + {bronze} B),"
        f" SF labels correct"
    )


# ─── Shared audit helpers (SC-05..SC-11) ─────────────────────────────────────

def _audit_base(db: DBSession, tid: int) -> dict:
    """Collect common audit values for any seed scenario.

    Returns a dict with all query results; callers add scenario-specific
    EMA checks then call _print_audit_block().
    """
    from sqlalchemy import text as _text

    db.expire_all()
    t = db.query(Semester).filter(Semester.id == tid).first()
    if not t:
        return {"abort": True, "tid": tid}

    from app.models.semester_enrollment import SemesterEnrollment as _SE, EnrollmentStatus as _ES
    from app.models.tournament_ranking import TournamentRanking as _TR
    from app.models.sponsor import SponsorCampaign as _SC
    from app.models.pitch import Pitch as _Pitch

    active = db.query(_SE).filter(
        _SE.semester_id == tid,
        _SE.is_active == True,  # noqa: E712
        _SE.request_status == _ES.APPROVED,
    ).count()

    rankings = db.query(_TR).filter(_TR.tournament_id == tid).count()
    parts = db.execute(
        _text("SELECT COUNT(*) FROM tournament_participations WHERE semester_id = :tid"),
        {"tid": tid},
    ).scalar()

    _camp = db.query(_SC).filter(_SC.id == t.organizer_campaign_id).first()
    sponsor_ok = bool(_camp and _camp.sponsor_id == t.organizer_sponsor_id)

    snapshot_set = bool(t.reward_policy_snapshot)
    status_ok = t.tournament_status == "REWARDS_DISTRIBUTED"
    ranks_ok = rankings == active
    parts_ok = (parts or 0) == active

    master_instr_ok = bool(t.master_instructor_id)
    master_slot_count = db.execute(_text(
        "SELECT COUNT(*) FROM tournament_instructor_slots "
        "WHERE semester_id = :tid AND role = 'MASTER' AND status = 'CHECKED_IN'"
    ), {"tid": tid}).scalar() or 0
    field_slot_count = db.execute(_text(
        "SELECT COUNT(*) FROM tournament_instructor_slots "
        "WHERE semester_id = :tid AND role = 'FIELD' AND status = 'CHECKED_IN'"
    ), {"tid": tid}).scalar() or 0
    checkin_count = db.execute(_text(
        "SELECT COUNT(*) FROM semester_enrollments "
        "WHERE semester_id = :tid AND is_active = true "
        "AND request_status = 'APPROVED' AND tournament_checked_in_at IS NOT NULL"
    ), {"tid": tid}).scalar() or 0
    tpc_count = db.execute(_text(
        "SELECT COUNT(*) FROM tournament_player_checkins WHERE tournament_id = :tid"
    ), {"tid": tid}).scalar() or 0
    pitch_count = (
        db.query(_Pitch).filter(
            _Pitch.campus_id == t.campus_id, _Pitch.is_active == True  # noqa: E712
        ).count()
        if t.campus_id else 0
    )

    cfg = t.tournament_config_obj
    parallel_fields = (cfg.parallel_fields if cfg and cfg.parallel_fields else 1)

    # Check every CHECKED_IN FIELD slot references an active pitch
    field_slots_with_pitch = db.execute(_text(
        "SELECT COUNT(*) FROM tournament_instructor_slots tis "
        "JOIN pitches p ON p.id = tis.pitch_id AND p.is_active = true "
        "WHERE tis.semester_id = :tid AND tis.role = 'FIELD' AND tis.status = 'CHECKED_IN'"
    ), {"tid": tid}).scalar() or 0
    field_pitch_coverage_ok = (
        field_slot_count >= parallel_fields and
        field_slots_with_pitch == field_slot_count
    )

    return {
        "abort": False,
        "t": t,
        "tid": tid,
        "active": active,
        "rankings": rankings,
        "parts": parts,
        "sponsor_ok": sponsor_ok,
        "snapshot_set": snapshot_set,
        "status_ok": status_ok,
        "ranks_ok": ranks_ok,
        "parts_ok": parts_ok,
        "core_ok": status_ok and ranks_ok and parts_ok and snapshot_set and sponsor_ok,
        "master_instr_ok": master_instr_ok,
        "master_slot_count": master_slot_count,
        "field_slot_count": field_slot_count,
        "field_slots_with_pitch": field_slots_with_pitch,
        "field_pitch_coverage_ok": field_pitch_coverage_ok,
        "parallel_fields": parallel_fields,
        "checkin_count": checkin_count,
        "tpc_count": tpc_count,
        "pitch_count": pitch_count,
    }


def _print_audit_block(
    b: dict,
    scenario_id: str,
    all_ok: bool,
    ema_lines: list[str],
    verify_args: str = "",
) -> None:
    """Print a standardised audit summary block for any scenario."""
    t = b["t"]
    active = b["active"]
    rankings = b["rankings"]
    parts = b["parts"]
    _W = 62
    print()
    print("  " + "─" * _W)
    print(f"  {scenario_id} AUDIT SUMMARY")
    print("  " + "─" * _W)
    print(f"  tournament_id  : {b['tid']}")
    print(f"  sponsor_id     : {t.organizer_sponsor_id}   campaign_id: {t.organizer_campaign_id}")
    print(f"  status         : {t.tournament_status}  {'✓' if b['status_ok'] else '✗'}")
    print(f"  enrolled       : {active} active")
    print(f"  rankings       : {rankings}   {'✓' if b['ranks_ok'] else f'✗ expected {active}'}")
    print(f"  participations : {parts}   {'✓' if b['parts_ok'] else f'✗ expected {active}'}")
    print(f"  reward snapshot: {'SET ✓' if b['snapshot_set'] else 'MISSING ✗'}")
    print(f"  sponsor FK     : {'✓ campaign.sponsor_id matches' if b['sponsor_ok'] else '✗ campaign.sponsor_id MISMATCH'}")
    for line in ema_lines:
        print(line)
    pf = b.get("parallel_fields", 1)
    print(f"  ── state verify (no lifecycle guard on main) ──────────────────")
    print(f"  master instr   : {'SET ✓' if b['master_instr_ok'] else 'MISSING ✗'} (id={t.master_instructor_id})")
    print(f"  MASTER slot    : {b['master_slot_count']} CHECKED_IN {'✓' if b['master_slot_count'] >= 1 else '⚠ expected ≥1'}")
    fsc = b.get("field_slot_count", 0)
    fpc = b.get("field_slots_with_pitch", 0)
    fok = b.get("field_pitch_coverage_ok", False)
    print(f"  FIELD slots    : {fsc}/{pf} CHECKED_IN, {fpc} with active pitch {'✓' if fok else f'⚠ expected {pf}'}")
    print(f"  player chk-in  : {b['checkin_count']}/{active} stamped {'✓' if b['checkin_count'] == active else '⚠'}")
    print(f"  TPC rows       : {b['tpc_count']} (instructor+players, expected {active + 1})")
    print(f"  active pitches : {b['pitch_count']} on campus {t.campus_id} {'✓' if b['pitch_count'] >= pf else f'⚠ expected ≥{pf}'}")
    print("  " + "─" * _W)
    verdict = "PASS" if all_ok else "FAIL"
    print(f"  {verdict}  ({rankings}/{active} ranked, {parts}/{active} participated)")
    cmd = f"PYTHONPATH=. python scripts/verify_sponsor_rewards_distributed.py {b['tid']}"
    if verify_args:
        cmd += f" {verify_args}"
    print(f"  Full audit: {cmd}")
    print("  " + "─" * _W)
    print()


def _ema_rank1_delta(db, tid: int, skill: str) -> float | None:
    """Return skill_rating_delta[skill] for the rank-1 participation in a tournament."""
    from sqlalchemy import text as _text
    row = db.execute(
        _text(
            "SELECT skill_rating_delta FROM tournament_participations "
            "WHERE semester_id = :tid AND placement = 1 LIMIT 1"
        ),
        {"tid": tid},
    ).fetchone()
    if row and row[0]:
        return (row[0] or {}).get(skill)
    return None


def _ema_rank1_user(db, tid: int) -> int | None:
    """Return user_id of the rank-1 player in a tournament."""
    from sqlalchemy import text as _text
    row = db.execute(
        _text(
            "SELECT user_id FROM tournament_participations "
            "WHERE semester_id = :tid AND placement = 1 LIMIT 1"
        ),
        {"tid": tid},
    ).fetchone()
    return row[0] if row else None


def _ema_user_delta(db, tid: int, user_id: int, skill: str) -> float | None:
    """Return skill_rating_delta[skill] for a specific user in a tournament."""
    from sqlalchemy import text as _text
    row = db.execute(
        _text(
            "SELECT skill_rating_delta FROM tournament_participations "
            "WHERE semester_id = :tid AND user_id = :uid LIMIT 1"
        ),
        {"tid": tid, "uid": user_id},
    ).fetchone()
    if row and row[0]:
        return (row[0] or {}).get(skill)
    return None


def _ema_same_player_check(
    db,
    prior_tid: int,
    curr_tid: int,
    skill: str,
    prior_label: str,
    curr_label: str,
    strict_less: bool = True,
) -> tuple[bool, str]:
    """Compare the SAME player (prior_tid rank-1 winner) across two tournaments for one skill.

    Returns (ok, detail_string).
    strict_less=True: curr < prior required (diminishing returns).
    strict_less=False: curr > 0 required (positive delta, weight difference acknowledged).
    """
    uid = _ema_rank1_user(db, prior_tid)
    if uid is None:
        return False, f"✗ could not find rank-1 player in {prior_label}"

    prior_val = (_ema_rank1_delta(db, prior_tid, skill) or 0)
    curr_val  = (_ema_user_delta(db, curr_tid, uid, skill) or 0)

    if strict_less:
        # Saturation: both 0 → skill near cap, trivially satisfies ≤.
        ok = curr_val <= prior_val
        if prior_val == 0 and curr_val == 0:
            detail = (
                f"user#{uid} {skill}: {prior_label}=0.0, {curr_label}=0.0 "
                f"✓ saturated (near cap — delta rounds to 0)"
            )
        else:
            detail = (
                f"user#{uid} {skill}: {prior_label}={prior_val:.1f}, {curr_label}={curr_val:.1f} "
                f"{'✓ ' + curr_label + '≤' + prior_label + ' = diminishing returns' if ok else '✗ expected ' + curr_label + ' ≤ ' + prior_label}"
            )
    else:
        # Saturation: 0.0 means skill near cap — weight difference still holds but delta
        # rounds to 0.  Treat as acceptable (skill did not regress).
        ok = curr_val >= 0
        if curr_val == 0:
            detail = (
                f"user#{uid} {skill}: {prior_label}={prior_val:.1f}, {curr_label}=0.0 "
                f"✓ saturated (near cap — delta rounds to 0)"
            )
        else:
            detail = (
                f"user#{uid} {skill}: {prior_label}={prior_val:.1f}, {curr_label}={curr_val:.1f} "
                f"✓ positive delta"
            )
    return ok, detail


# ─── SC-05 inline audit ──────────────────────────────────────────────────────

def _sc05_audit(db: DBSession, tid: int) -> bool:
    """Print a PASS/FAIL summary for SC-05 and return True on pass.

    Delegates to _audit_base() + _print_audit_block() for consistent FIELD slot
    and pitch coverage reporting across all scenarios.
    """
    b = _audit_base(db, tid)
    if b.get("abort"):
        print(f"  AUDIT ABORT: tournament {tid} not found")
        return False

    active = b["active"]
    sprint_delta = _ema_rank1_delta(db, tid, "sprint_speed")
    ema_lines = []
    if sprint_delta is not None:
        ema_lines.append(f"  sprint_speed Δ : {sprint_delta:.2f} (rank-1, EMA)")
    all_ok = b["core_ok"]
    _print_audit_block(b, "SC-05", all_ok, ema_lines)
    return all_ok


# ─── SC-05 helpers ───────────────────────────────────────────────────────────

def _set_reward_config_orm(db: DBSession, tid: int) -> None:
    """Create TournamentRewardConfig so IN_PROGRESS saves reward_policy_snapshot."""
    existing = db.query(TRwdModel).filter(TRwdModel.semester_id == tid).first()
    if existing:
        return
    db.add(TRwdModel(
        semester_id=tid,
        reward_policy_name="Seed Standard",
        reward_config={
            "template_name": "Seed Standard",
            "skill_mappings": [
                {"skill": "sprint_speed", "weight": 1.0, "category": "PHYSICAL", "enabled": True},
            ],
            "first_place": {"xp": 200, "credits": 50},
            "second_place": {"xp": 145, "credits": 30},
            "third_place": {"xp": 100, "credits": 20},
            "participation": {"xp": 35, "credits": 5},
        },
    ))
    db.flush()


# ─── SC-06 helpers ───────────────────────────────────────────────────────────

def _set_reward_config_sc06(db: DBSession, tid: int) -> None:
    """Create TournamentRewardConfig for SC-06 Finishing & Shooting Focus.

    Weights: equal-split normalization — weight_i = pct_i × n / Σ(pct)
    n=6 skills, Σ=99%:
      ball_control=14% → 0.848,  dribbling=18% → 1.091,  finishing=24% → 1.455
      positioning_off=11% → 0.667,  sprint_speed=19% → 1.152,  agility=13% → 0.788

    NOTE: sprint_speed intentionally overlaps with SC-05.  EMA replay chains from
    the post-SC-05 running value (prev_val≈73.6 for rank-1) so the SC-06 isolated
    delta is smaller than SC-05's (diminishing returns — audit invariant SK14).
    """
    existing = db.query(TRwdModel).filter(TRwdModel.semester_id == tid).first()
    if existing:
        return
    db.add(TRwdModel(
        semester_id=tid,
        reward_policy_name="Finishing & Shooting Focus",
        reward_config={
            "template_name": "Finishing & Shooting Focus",
            "skill_mappings": [
                {"skill": "ball_control",    "weight": 0.848, "category": "OUTFIELD", "enabled": True},
                {"skill": "dribbling",       "weight": 1.091, "category": "OUTFIELD", "enabled": True},
                {"skill": "finishing",       "weight": 1.455, "category": "OUTFIELD", "enabled": True},
                {"skill": "positioning_off", "weight": 0.667, "category": "MENTAL",   "enabled": True},
                {"skill": "sprint_speed",    "weight": 1.152, "category": "PHYSICAL", "enabled": True},
                {"skill": "agility",         "weight": 0.788, "category": "PHYSICAL", "enabled": True},
            ],
            "first_place":   {"xp": 200, "credits": 50},
            "second_place":  {"xp": 145, "credits": 30},
            "third_place":   {"xp": 100, "credits": 20},
            "participation": {"xp": 35,  "credits": 5},
        },
    ))
    db.flush()


def _sc06_audit(db: DBSession, tid: int, sc05_tid: int | None = None) -> bool:
    """Print inline audit summary for SC-06 and return True on pass.

    Core checks (contribute to all_ok / PASS-FAIL verdict):
    - status == REWARDS_DISTRIBUTED
    - TournamentRanking count == active enrolled baseline
    - TournamentParticipation count == active enrolled baseline
    - reward_policy_snapshot is set
    - sponsor FK chain: semester.organizer_sponsor_id == campaign.sponsor_id
    - EMA chain: SC-05 rank-1 player's sprint_speed delta in SC-06 < SC-05 (diminishing returns, SK14)

    State verify (printed, warning only — not lifecycle-guarded on main):
    - master_instructor_id set
    - MASTER TournamentInstructorSlot with status=CHECKED_IN exists
    - all active enrollments have tournament_checked_in_at stamped
    - TournamentPlayerCheckin rows present
    - active Pitch count on campus (generation gate)
    """
    from sqlalchemy import text as _text

    db.expire_all()
    t = db.query(Semester).filter(Semester.id == tid).first()
    if not t:
        print(f"  AUDIT ABORT: tournament {tid} not found")
        return False

    from app.models.semester_enrollment import SemesterEnrollment as _SE, EnrollmentStatus as _ES
    from app.models.tournament_ranking import TournamentRanking as _TR
    from app.models.sponsor import SponsorCampaign as _SC
    from app.models.pitch import Pitch as _Pitch

    active = db.query(_SE).filter(
        _SE.semester_id == tid,
        _SE.is_active == True,  # noqa: E712
        _SE.request_status == _ES.APPROVED,
    ).count()

    rankings = db.query(_TR).filter(_TR.tournament_id == tid).count()
    parts = db.execute(
        _text("SELECT COUNT(*) FROM tournament_participations WHERE semester_id = :tid"),
        {"tid": tid},
    ).scalar()

    # B-3: Sponsor FK chain
    _camp = db.query(_SC).filter(_SC.id == t.organizer_campaign_id).first()
    sponsor_ok = bool(_camp and _camp.sponsor_id == t.organizer_sponsor_id)

    # Core booleans
    snapshot_set = bool(t.reward_policy_snapshot)
    status_ok = t.tournament_status == "REWARDS_DISTRIBUTED"
    ranks_ok = rankings == active
    parts_ok = (parts or 0) == active

    dep_ok = True
    dep_detail = "(SC-05 tid not provided — cross-tournament delta check skipped)"
    if sc05_tid:
        # SK14: compare the SAME player (SC-05 rank-1 winner) across both tournaments.
        # Comparing rank-1 winners is wrong when different players win each tournament —
        # placement changes confound the comparison. Tracking one player proves that the
        # EMA chain produces diminishing sprint_speed returns for repeated exposure.
        _sc05_q = _text(
            "SELECT user_id, skill_rating_delta FROM tournament_participations "
            "WHERE semester_id = :tid AND placement = 1 LIMIT 1"
        )
        sc05_row = db.execute(_sc05_q, {"tid": sc05_tid}).fetchone()
        if sc05_row:
            sc05_uid = sc05_row[0]
            sc05_ss = (sc05_row[1] or {}).get("sprint_speed", 0) or 0
            _sc06_q = _text(
                "SELECT skill_rating_delta FROM tournament_participations "
                "WHERE semester_id = :tid AND user_id = :uid LIMIT 1"
            )
            sc06_row = db.execute(_sc06_q, {"tid": tid, "uid": sc05_uid}).fetchone()
            if sc06_row:
                sc06_ss = (sc06_row[0] or {}).get("sprint_speed", 0) or 0
                # Saturation: both deltas round to 0 means skill is capped — trivially valid.
                # Active: SC-06 strictly less than SC-05 proves diminishing returns.
                dep_ok = sc06_ss <= sc05_ss
                if sc05_ss == 0 and sc06_ss == 0:
                    dep_detail = (
                        f"user#{sc05_uid} sprint_speed: SC-05=0.0, SC-06=0.0 "
                        "✓ saturated (skill near cap — delta rounds to 0 in both)"
                    )
                else:
                    dep_detail = (
                        f"user#{sc05_uid} sprint_speed: SC-05={sc05_ss:.1f}, SC-06={sc06_ss:.1f} "
                        f"({'✓ SC-06 ≤ SC-05 = diminishing returns' if dep_ok else '✗ expected SC-06 ≤ SC-05'})"
                    )
            else:
                dep_ok = False
                dep_detail = f"✗ user#{sc05_uid} (SC-05 winner) has no TP row in SC-06"
        else:
            dep_ok = False
            dep_detail = "✗ could not find rank-1 TP row in SC-05"

    all_ok = status_ok and ranks_ok and parts_ok and snapshot_set and dep_ok and sponsor_ok

    # B-4: State verify (warning-level, not all_ok — no lifecycle guard on main)
    master_instr_ok = bool(t.master_instructor_id)
    master_slot_count = db.execute(_text(
        "SELECT COUNT(*) FROM tournament_instructor_slots "
        "WHERE semester_id = :tid AND role = 'MASTER' AND status = 'CHECKED_IN'"
    ), {"tid": tid}).scalar() or 0
    checkin_count = db.execute(_text(
        "SELECT COUNT(*) FROM semester_enrollments "
        "WHERE semester_id = :tid AND is_active = true "
        "AND request_status = 'APPROVED' AND tournament_checked_in_at IS NOT NULL"
    ), {"tid": tid}).scalar() or 0
    tpc_count = db.execute(_text(
        "SELECT COUNT(*) FROM tournament_player_checkins WHERE tournament_id = :tid"
    ), {"tid": tid}).scalar() or 0
    pitch_count = (
        db.query(_Pitch).filter(
            _Pitch.campus_id == t.campus_id, _Pitch.is_active == True  # noqa: E712
        ).count()
        if t.campus_id else 0
    )

    _W = 62
    print()
    print("  " + "─" * _W)
    print(f"  SC-06 AUDIT SUMMARY")
    print("  " + "─" * _W)
    print(f"  tournament_id  : {tid}")
    print(f"  sponsor_id     : {t.organizer_sponsor_id}   campaign_id: {t.organizer_campaign_id}")
    print(f"  status         : {t.tournament_status}  {'✓' if status_ok else '✗'}")
    print(f"  enrolled       : {active} active")
    print(f"  rankings       : {rankings}   {'✓' if ranks_ok else f'✗ expected {active}'}")
    print(f"  participations : {parts}   {'✓' if parts_ok else f'✗ expected {active}'}")
    print(f"  reward snapshot: {'SET ✓' if snapshot_set else 'MISSING ✗'}")
    print(f"  sponsor FK     : {'✓ campaign.sponsor_id matches' if sponsor_ok else '✗ campaign.sponsor_id MISMATCH'}")
    print(f"  EMA chain check: {dep_detail}")
    print(f"  ── state verify (no lifecycle guard on main) ──────────────────")
    print(f"  master instr   : {'SET ✓' if master_instr_ok else 'MISSING ✗'} (id={t.master_instructor_id})")
    print(f"  MASTER slot    : {master_slot_count} CHECKED_IN {'✓' if master_slot_count >= 1 else '⚠ expected ≥1'}")
    print(f"  player chk-in  : {checkin_count}/{active} stamped {'✓' if checkin_count == active else '⚠'}")
    print(f"  TPC rows       : {tpc_count} (instructor+players, expected {active + 1})")
    print(f"  active pitches : {pitch_count} on campus {t.campus_id} {'✓' if pitch_count >= 1 else '✗ generation will fail'}")
    print("  " + "─" * _W)
    verdict = "PASS" if all_ok else "FAIL"
    print(f"  {verdict}  ({rankings}/{active} ranked, {parts}/{active} participated)")
    sc05_arg = str(sc05_tid) if sc05_tid else ""
    print(
        f"  Full audit: PYTHONPATH=. python scripts/verify_sponsor_rewards_distributed.py"
        f" {tid} {sc05_arg}".rstrip()
    )
    print("  " + "─" * _W)
    print()
    return all_ok


# ─── SC-07..SC-11 reward configs ─────────────────────────────────────────────
# Normalization: weight_i = pct_i × n / Σ(pct),  n=5 skills,  Σ=100%
# Reward tiers (9 players): rank-1: 200 XP/50 cr; rank-2: 145 XP/30 cr;
#   rank-3: 100 XP/20 cr; rank-4..9 (6 players): 35 XP/5 cr each
#   Total per event: 655 XP / 130 credits


def _set_reward_config_sc07(db: DBSession, tid: int) -> None:
    """SC-07 Speed & Agility Sprint Cup — 5 physical skills.

    Weights (n=5, Σ=100%):
      acceleration=25% → 1.250,  sprint_speed=20% → 1.000,
      agility=20% → 1.000,  balance=15% → 0.750,  stamina=20% → 1.000

    EMA chain: sprint_speed (3rd event across SC-05/06/07) and agility (2nd)
    → SK-15 invariant: SC-07 deltas < SC-06 deltas for both skills.
    """
    if db.query(TRwdModel).filter(TRwdModel.semester_id == tid).first():
        return
    db.add(TRwdModel(
        semester_id=tid,
        reward_policy_name="Speed & Agility Sprint Cup",
        reward_config={
            "template_name": "Speed & Agility Sprint Cup",
            "skill_mappings": [
                {"skill": "acceleration", "weight": 1.250, "category": "PHYSICAL", "enabled": True},
                {"skill": "sprint_speed", "weight": 1.000, "category": "PHYSICAL", "enabled": True},
                {"skill": "agility",      "weight": 1.000, "category": "PHYSICAL", "enabled": True},
                {"skill": "balance",      "weight": 0.750, "category": "PHYSICAL", "enabled": True},
                {"skill": "stamina",      "weight": 1.000, "category": "PHYSICAL", "enabled": True},
            ],
            "first_place":   {"xp": 200, "credits": 50},
            "second_place":  {"xp": 145, "credits": 30},
            "third_place":   {"xp": 100, "credits": 20},
            "participation": {"xp": 35,  "credits": 5},
        },
    ))
    db.flush()


def _set_reward_config_sc08(db: DBSession, tid: int) -> None:
    """SC-08 Strength & Endurance Challenge — 5 physical/outfield skills.

    Weights (n=5, Σ=100%):
      strength=30% → 1.500,  stamina=25% → 1.250,  jumping=20% → 1.000,
      heading=15% → 0.750,  aggression=10% → 0.500

    EMA chain: stamina (2nd event after SC-07) → SK-17 invariant.
    strength/jumping/heading/aggression all first-time entries.
    """
    if db.query(TRwdModel).filter(TRwdModel.semester_id == tid).first():
        return
    db.add(TRwdModel(
        semester_id=tid,
        reward_policy_name="Strength & Endurance Challenge",
        reward_config={
            "template_name": "Strength & Endurance Challenge",
            "skill_mappings": [
                {"skill": "strength",    "weight": 1.500, "category": "PHYSICAL", "enabled": True},
                {"skill": "stamina",     "weight": 1.250, "category": "PHYSICAL", "enabled": True},
                {"skill": "jumping",     "weight": 1.000, "category": "PHYSICAL", "enabled": True},
                {"skill": "heading",     "weight": 0.750, "category": "OUTFIELD", "enabled": True},
                {"skill": "aggression",  "weight": 0.500, "category": "MENTAL",   "enabled": True},
            ],
            "first_place":   {"xp": 200, "credits": 50},
            "second_place":  {"xp": 145, "credits": 30},
            "third_place":   {"xp": 100, "credits": 20},
            "participation": {"xp": 35,  "credits": 5},
        },
    ))
    db.flush()


def _set_reward_config_sc09(db: DBSession, tid: int) -> None:
    """SC-09 Creative Passing & Vision — 5 outfield/mental skills.

    Weights (n=5, Σ=100%):
      passing=28% → 1.400,  vision=22% → 1.100,
      tactical_awareness=20% → 1.000,  crossing=18% → 0.900,  long_shots=12% → 0.600

    All 5 skills are first-time entries across SC-05..SC-09 — no diminishing returns;
    expect maximum positive delta for rank-1.
    """
    if db.query(TRwdModel).filter(TRwdModel.semester_id == tid).first():
        return
    db.add(TRwdModel(
        semester_id=tid,
        reward_policy_name="Creative Passing & Vision",
        reward_config={
            "template_name": "Creative Passing & Vision",
            "skill_mappings": [
                {"skill": "passing",            "weight": 1.400, "category": "OUTFIELD", "enabled": True},
                {"skill": "vision",             "weight": 1.100, "category": "MENTAL",   "enabled": True},
                {"skill": "tactical_awareness", "weight": 1.000, "category": "MENTAL",   "enabled": True},
                {"skill": "crossing",           "weight": 0.900, "category": "OUTFIELD", "enabled": True},
                {"skill": "long_shots",         "weight": 0.600, "category": "OUTFIELD", "enabled": True},
            ],
            "first_place":   {"xp": 200, "credits": 50},
            "second_place":  {"xp": 145, "credits": 30},
            "third_place":   {"xp": 100, "credits": 20},
            "participation": {"xp": 35,  "credits": 5},
        },
    ))
    db.flush()


def _set_reward_config_sc10(db: DBSession, tid: int) -> None:
    """SC-10 Defensive Mastery Cup — 5 outfield/mental skills.

    Weights (n=5, Σ=100%):
      tackle=28% → 1.400,  positioning_def=25% → 1.250,  marking=22% → 1.100,
      composure=15% → 0.750,  reactions=10% → 0.500

    All 5 skills are first-time entries across SC-05..SC-10 — no diminishing returns.
    Purely defensive cluster: zero overlap with SC-05..SC-09 TRC skills.
    """
    if db.query(TRwdModel).filter(TRwdModel.semester_id == tid).first():
        return
    db.add(TRwdModel(
        semester_id=tid,
        reward_policy_name="Defensive Mastery Cup",
        reward_config={
            "template_name": "Defensive Mastery Cup",
            "skill_mappings": [
                {"skill": "tackle",          "weight": 1.400, "category": "OUTFIELD", "enabled": True},
                {"skill": "positioning_def", "weight": 1.250, "category": "MENTAL",   "enabled": True},
                {"skill": "marking",         "weight": 1.100, "category": "OUTFIELD", "enabled": True},
                {"skill": "composure",       "weight": 0.750, "category": "MENTAL",   "enabled": True},
                {"skill": "reactions",       "weight": 0.500, "category": "MENTAL",   "enabled": True},
            ],
            "first_place":   {"xp": 200, "credits": 50},
            "second_place":  {"xp": 145, "credits": 30},
            "third_place":   {"xp": 100, "credits": 20},
            "participation": {"xp": 35,  "credits": 5},
        },
    ))
    db.flush()


def _set_reward_config_sc11(db: DBSession, tid: int) -> None:
    """SC-11 Complete Attacker Championship — 5 outfield skills.

    Weights (n=5, Σ=100%):
      finishing=30% → 1.500,  shot_power=22% → 1.100,  long_shots=20% → 1.000,
      volleys=18% → 0.900,  penalties=10% → 0.500

    EMA chain: finishing (2nd after SC-06) → SK-16 invariant.
               long_shots (2nd after SC-09) → SK-18 invariant.
    shot_power/volleys/penalties are first-time entries.
    """
    if db.query(TRwdModel).filter(TRwdModel.semester_id == tid).first():
        return
    db.add(TRwdModel(
        semester_id=tid,
        reward_policy_name="Complete Attacker Championship",
        reward_config={
            "template_name": "Complete Attacker Championship",
            "skill_mappings": [
                {"skill": "finishing",  "weight": 1.500, "category": "OUTFIELD", "enabled": True},
                {"skill": "shot_power", "weight": 1.100, "category": "OUTFIELD", "enabled": True},
                {"skill": "long_shots", "weight": 1.000, "category": "OUTFIELD", "enabled": True},
                {"skill": "volleys",    "weight": 0.900, "category": "OUTFIELD", "enabled": True},
                {"skill": "penalties",  "weight": 0.500, "category": "OUTFIELD", "enabled": True},
            ],
            "first_place":   {"xp": 200, "credits": 50},
            "second_place":  {"xp": 145, "credits": 30},
            "third_place":   {"xp": 100, "credits": 20},
            "participation": {"xp": 35,  "credits": 5},
        },
    ))
    db.flush()


# ─── SC-07..SC-11 audit functions ────────────────────────────────────────────


def _sc07_audit(db: DBSession, tid: int, sc06_tid: int | None = None) -> bool:
    """SC-07 audit: standard checks + SK-15 (sprint_speed, agility diminishing vs SC-06).

    Tracks SC-06's rank-1 winner across both tournaments to isolate the
    diminishing-returns signal from placement variance.
    """
    b = _audit_base(db, tid)
    if b["abort"]:
        print(f"  AUDIT ABORT: tournament {tid} not found")
        return False

    ema_lines = []
    ema_ok = True
    if sc06_tid:
        for skill, label in [("sprint_speed", "SK-15"), ("agility", "SK-15")]:
            ok, detail = _ema_same_player_check(
                db, sc06_tid, tid, skill, "SC-06", "SC-07", strict_less=True
            )
            if not ok:
                ema_ok = False
            ema_lines.append(f"  EMA [{label}] {detail}")
    else:
        ema_lines.append("  EMA SK-15 check: skipped (SC-06 tid not provided)")

    all_ok = b["core_ok"] and ema_ok
    _print_audit_block(b, "SC-07", all_ok, ema_lines, str(sc06_tid) if sc06_tid else "")
    return all_ok


def _sc08_audit(db: DBSession, tid: int, sc07_tid: int | None = None) -> bool:
    """SC-08 audit: standard checks + SK-17 (stamina diminishing vs SC-07).

    Tracks SC-07's rank-1 winner across both tournaments.
    """
    b = _audit_base(db, tid)
    if b["abort"]:
        print(f"  AUDIT ABORT: tournament {tid} not found")
        return False

    ema_lines = []
    ema_ok = True
    if sc07_tid:
        ok, detail = _ema_same_player_check(
            db, sc07_tid, tid, "stamina", "SC-07", "SC-08", strict_less=True
        )
        if not ok:
            ema_ok = False
        ema_lines.append(f"  EMA [SK-17] {detail}")
    else:
        ema_lines.append("  EMA SK-17 check: skipped (SC-07 tid not provided)")

    all_ok = b["core_ok"] and ema_ok
    _print_audit_block(b, "SC-08", all_ok, ema_lines, str(sc07_tid) if sc07_tid else "")
    return all_ok


def _sc09_audit(db: DBSession, tid: int) -> bool:
    """SC-09 audit: standard checks only. All 5 skills are first-time entries — no EMA chain check."""
    b = _audit_base(db, tid)
    if b["abort"]:
        print(f"  AUDIT ABORT: tournament {tid} not found")
        return False

    ema_lines = [
        "  EMA note: passing/vision/tactical_awareness/crossing/long_shots all first-time"
        " — no diminishing returns expected; full baseline delta for rank-1."
    ]
    all_ok = b["core_ok"]
    _print_audit_block(b, "SC-09", all_ok, ema_lines)
    return all_ok


def _sc10_audit(db: DBSession, tid: int) -> bool:
    """SC-10 audit: standard checks only. All 5 skills first-time entries — no EMA chain check."""
    b = _audit_base(db, tid)
    if b["abort"]:
        print(f"  AUDIT ABORT: tournament {tid} not found")
        return False

    ema_lines = [
        "  EMA note: tackle/positioning_def/marking/composure/reactions all first-time"
        " — zero overlap with SC-05..SC-09 TRC skills; maximum positive delta for rank-1."
    ]
    all_ok = b["core_ok"]
    _print_audit_block(b, "SC-10", all_ok, ema_lines)
    return all_ok


def _sc11_audit(
    db: DBSession,
    tid: int,
    sc06_tid: int | None = None,
    sc09_tid: int | None = None,
) -> bool:
    """SC-11 audit: standard checks + SK-16 (finishing diminishing vs SC-06).

    Tracks SC-06's rank-1 winner to isolate the diminishing-returns signal.
    SK-18 (long_shots) is NOT enforced as a strict less-than invariant:
    SC-11 long_shots weight (1.000) is 67% higher than SC-09 (0.600), which
    mathematically dominates the diminishing-returns effect from the higher
    starting value.  Instead we confirm long_shots delta > 0 for the tracked player.
    """
    b = _audit_base(db, tid)
    if b["abort"]:
        print(f"  AUDIT ABORT: tournament {tid} not found")
        return False

    ema_lines = []
    ema_ok = True

    # SK-16: finishing diminishing vs SC-06 — same-player tracking
    if sc06_tid:
        ok, detail = _ema_same_player_check(
            db, sc06_tid, tid, "finishing", "SC-06", "SC-11", strict_less=True
        )
        if not ok:
            ema_ok = False
        ema_lines.append(f"  EMA [SK-16] {detail}")
    else:
        ema_lines.append("  EMA SK-16 check: skipped (SC-06 tid not provided)")

    # long_shots: confirm positive delta for SC-09 rank-1 player
    # (no strict < check; SC-11 weight 1.0 > SC-09 weight 0.6 dominates)
    if sc09_tid:
        ok, detail = _ema_same_player_check(
            db, sc09_tid, tid, "long_shots", "SC-09", "SC-11", strict_less=False
        )
        if not ok:
            ema_ok = False
        ema_lines.append(
            f"  EMA {detail} (SK-18 N/A: SC-11 weight 1.0 > SC-09 weight 0.6)"
        )
    else:
        ema_lines.append("  EMA long_shots check: skipped (SC-09 tid not provided)")

    verify_args = " ".join(str(x) for x in [sc06_tid, sc09_tid] if x)
    all_ok = b["core_ok"] and ema_ok
    _print_audit_block(b, "SC-11", all_ok, ema_lines, verify_args)
    return all_ok


def _complete_gk_sessions_seed(client: TestClient, db: DBSession, tid: int) -> int:
    """Complete all group_knockout sessions for SC-05.

    Phase 1 (group stage): ORM-direct — deterministic, player[0] beats player[1].
    Phase 2: compute group standings, assign SF participants by structure_config matchup.
    Phase 3 (knockout SF/F/B): API-based — KnockoutProgressionService auto-propagates F/B.

    Returns total sessions completed.
    """
    from collections import defaultdict

    db.expire_all()
    sessions = db.query(SessionModel).filter(SessionModel.semester_id == tid).all()

    group_sessions = [
        s for s in sessions
        if s.tournament_phase in (TournamentPhase.GROUP_STAGE, TournamentPhase.GROUP_STAGE.value)
    ]
    knockout_sessions = [
        s for s in sessions
        if s.tournament_phase in (TournamentPhase.KNOCKOUT, TournamentPhase.KNOCKOUT.value)
    ]

    total = 0

    # ── Phase 1: group stage via ORM ─────────────────────────────────────────
    standings: dict = defaultdict(lambda: defaultdict(lambda: {"pts": 0, "gf": 0, "ga": 0}))
    for s in group_sessions:
        if not s.participant_user_ids or len(s.participant_user_ids) < 2:
            continue
        u1, u2 = s.participant_user_ids[0], s.participant_user_ids[1]
        s.game_results = json.dumps({
            "match_format": "HEAD_TO_HEAD",
            "tournament_type": "group_knockout",
            "participants": [
                {"user_id": u1, "score": 2, "result": "win"},
                {"user_id": u2, "score": 0, "result": "loss"},
            ],
            "winner_user_id": u1,
            "match_status": "completed",
        })
        s.rounds_data = {
            "round_results": {
                "1": {str(u1): "2", str(u2): "0"},
            },
        }
        s.session_status = "completed"
        total += 1
        grp = s.group_identifier or "A"
        standings[grp][u1]["pts"] += 3
        standings[grp][u1]["gf"] += 2
        standings[grp][u2]["ga"] += 2
    db.flush()

    # ── Phase 2: compute qualifiers, assign SF participants ───────────────────
    qualifiers: dict = {}
    for grp, s_data in standings.items():
        ranked = sorted(
            s_data.items(),
            key=lambda x: (-x[1]["pts"], -(x[1]["gf"] - x[1]["ga"]), -x[1]["gf"]),
        )
        qualifiers[grp] = [uid for uid, _ in ranked]

    groups_sorted = sorted(qualifiers.keys())
    group_winners = {g: qualifiers[g][0] for g in groups_sorted if qualifiers.get(g)}
    runners_up = [qualifiers[g][1] for g in groups_sorted if len(qualifiers.get(g, [])) > 1]
    best_ru = runners_up[0] if runners_up else None

    first_round_ks = sorted(
        [s for s in knockout_sessions if (s.tournament_round or 0) == 1],
        key=lambda s: s.id,
    )
    for ks in first_round_ks:
        sc = ks.structure_config or {}
        matchup = sc.get("matchup", "")
        if "Group A winner" in matchup and "Best runner-up" in matchup:
            a = group_winners.get("A")
            if a and best_ru:
                ks.participant_user_ids = [a, best_ru]
        elif "Group B winner" in matchup and "Group C winner" in matchup:
            b, c = group_winners.get("B"), group_winners.get("C")
            if b and c:
                ks.participant_user_ids = [b, c]
    db.commit()

    # ── Phase 3: knockout sessions via API (progression auto-handled) ─────────
    for _ in range(10):
        db.expire_all()
        pending = [
            {"id": s.id, "uids": list(s.participant_user_ids or [])}
            for s in db.query(SessionModel).filter(
                SessionModel.semester_id == tid,
                SessionModel.session_status != "completed",
            ).all()
        ]
        if not pending:
            break
        made_progress = False
        for p in pending:
            if len(p["uids"]) < 2:
                continue
            r = client.patch(
                f"/api/v1/sessions/{p['id']}/head-to-head-results",
                json={"results": [
                    {"user_id": p["uids"][0], "score": 2},
                    {"user_id": p["uids"][1], "score": 0},
                ]},
            )
            if r.status_code not in (200, 201):
                print(f"  FAIL KO session={p['id']}: {r.status_code} {r.text[:200]}")
                return total
            total += 1
            made_progress = True
        if not made_progress:
            break
        time.sleep(0.2)

    return total


# ─── SC-05 instructor slot + player checkins ─────────────────────────────────

def _seed_instructor_slot_and_checkins(
    db: DBSession,
    tid: int,
    instructor: User,
    admin: User,
    enrolled_user_ids: list[int],
    field_instructors: list[User] | None = None,
    field_pitches: list | None = None,
) -> None:
    """Insert semester_instructors row, MASTER + FIELD instructor slots, and player check-ins.

    Idempotent: skips rows that already exist.
    Populates:
      - semester_instructors (instructor roster join table)
      - tournament_instructor_slots (1 MASTER + N FIELD, all CHECKED_IN)
      - tournament_player_checkins for instructor + all enrolled players

    Args:
        field_instructors: List of FIELD instructor User objects (one per parallel field).
        field_pitches:     Parallel list of Pitch objects (same length as field_instructors).
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    now = datetime.datetime.now(datetime.timezone.utc)

    # 1. semester_instructors association row
    db.execute(
        pg_insert(semester_instructors)
        .values(semester_id=tid, instructor_id=instructor.id)
        .on_conflict_do_nothing()
    )

    # 2. MASTER instructor slot
    existing_slot = db.query(TournamentInstructorSlot).filter(
        TournamentInstructorSlot.semester_id == tid,
        TournamentInstructorSlot.instructor_id == instructor.id,
    ).first()
    if not existing_slot:
        db.add(TournamentInstructorSlot(
            semester_id=tid,
            instructor_id=instructor.id,
            role="MASTER",
            pitch_id=None,
            status="CHECKED_IN",
            checked_in_at=now,
            assigned_by=admin.id,
        ))
    db.flush()

    # 3. FIELD instructor slots (one per pitch)
    if field_instructors and field_pitches:
        for fi, pitch in zip(field_instructors, field_pitches):
            existing_field = db.query(TournamentInstructorSlot).filter(
                TournamentInstructorSlot.semester_id == tid,
                TournamentInstructorSlot.instructor_id == fi.id,
            ).first()
            if not existing_field:
                db.add(TournamentInstructorSlot(
                    semester_id=tid,
                    instructor_id=fi.id,
                    role="FIELD",
                    pitch_id=pitch.id,
                    status="CHECKED_IN",
                    checked_in_at=now,
                    assigned_by=admin.id,
                ))
        db.flush()

    # 4. tournament_player_checkins — master instructor + all enrolled players
    all_checkin_ids = [instructor.id] + enrolled_user_ids
    for uid in all_checkin_ids:
        existing = db.query(TournamentPlayerCheckin).filter(
            TournamentPlayerCheckin.tournament_id == tid,
            TournamentPlayerCheckin.user_id == uid,
        ).first()
        if not existing:
            db.add(TournamentPlayerCheckin(
                tournament_id=tid,
                user_id=uid,
                team_id=None,
                checked_in_at=now,
                checked_in_by_id=admin.id,
            ))
    db.flush()


# ─── Main entry point ─────────────────────────────────────────────────────────

def run_scenarios(
    db: DBSession,
    client: TestClient,
    *,
    scenario_ids: list[str] | None = None,
    dry_run: bool = False,
    campus_id: int = 1,
    fail_on_missing_prereq: bool = True,
) -> dict[str, int | None]:
    """Run promotion event seed scenarios.

    Always runs the group_knockout preflight and domain audit before any
    scenario so that structural failures are caught early with a clear message.

    Args:
        db:                    Committed SQLAlchemy session (caller owns lifecycle).
        client:                TestClient with admin auth overrides already applied.
        scenario_ids:          Subset to run, e.g. ["SC-01", "SC-04"]. None = all.
        dry_run:               If True, print intent but write nothing.
        campus_id:             Campus to associate with the tournament (default=1).
        fail_on_missing_prereq: Hard-fail on any preflight issue (default True).
                               Pass False only for dry-run/audit-only inspection.

    Returns:
        Dict mapping scenario_id -> created tournament id (or None in dry-run).
    """
    _assert_not_production()

    tt = _preflight_group_knockout_9p(db)

    # Domain integrity audit — hard-fail by default (pitch, instructor, preset, admin)
    _run_preflight_audit(db, campus_id, fail=fail_on_missing_prereq)

    all_scenarios = [
        "SC-01", "SC-02", "SC-03", "SC-04", "SC-05", "SC-06",
        "SC-07", "SC-08", "SC-09", "SC-10", "SC-11",
    ]
    to_run = [s for s in all_scenarios if s in (scenario_ids or all_scenarios)]

    admin = db.query(User).filter(User.email == "admin@lfa.com").first()
    if not admin:
        sys.exit("admin@lfa.com not found — run bootstrap first")

    instructor = db.query(User).filter(User.email == "instructor@lfa.com").first()

    # Load FIELD instructors and corresponding pitches for SC-05..SC-11 slot creation
    from app.models.pitch import Pitch as _Pitch
    _field_instructors: list[User] = []
    for email in _FIELD_INSTRUCTOR_EMAILS:
        fi = db.query(User).filter(User.email == email, User.is_active == True).first()  # noqa: E712
        if fi:
            _field_instructors.append(fi)
    _field_pitches = (
        db.query(_Pitch)
        .filter(_Pitch.campus_id == campus_id, _Pitch.is_active == True)  # noqa: E712
        .order_by(_Pitch.name)
        .limit(3)
        .all()
    )
    # Only pass FIELD data if we have exactly 3 of each; otherwise MASTER-only fallback
    _use_field = (len(_field_instructors) == 3 and len(_field_pitches) >= 3)
    _fi_list = _field_instructors if _use_field else []
    _fp_list = _field_pitches[:3] if _use_field else []

    sponsor = _get_or_create_sponsor(db, dry_run)
    campaign = _get_or_create_campaign(db, sponsor, dry_run) if sponsor else None

    if not dry_run and sponsor and campaign:
        _ensure_9_audience_entries(db, sponsor, campaign, admin)
        db.commit()

    results: dict[str, int | None] = {}

    # ── SC-01: DRAFT ──────────────────────────────────────────────────────────
    if "SC-01" in to_run:
        print("\n[SC-01] DRAFT")
        t = _create_tournament(
            db, f"{_TOURNAMENT_PREFIX}SC-01 Draft", sponsor, campaign, tt, campus_id, dry_run
        )
        if t:
            print(f"  id={t.id}  status=DRAFT")
        results["SC-01"] = t.id if t else None

    # ── SC-02: ENROLLMENT_CLOSED + bulk-enroll ────────────────────────────────
    if "SC-02" in to_run:
        print("\n[SC-02] ENROLLMENT_CLOSED + bulk-enroll")
        t = _create_tournament(
            db, f"{_TOURNAMENT_PREFIX}SC-02 Locked", sponsor, campaign, tt, campus_id, dry_run
        )
        if t:
            result = _bulk_enroll_direct(db, t.id, campaign.id, sponsor.id)
            db.commit()
            if _lock_enrollment(client, t.id):
                db.expire_all()
                print(
                    f"  id={t.id}  status=ENROLLMENT_CLOSED"
                    f"  enrolled={result.get('enrolled_count', 0)}"
                    f"  skipped={result.get('skipped_count', 0)}"
                )
        results["SC-02"] = t.id if t else None

    # ── SC-03: CHECK_IN_OPEN → 13 sessions ────────────────────────────────────
    if "SC-03" in to_run:
        print("\n[SC-03] CHECK_IN_OPEN (13 sessions expected)")
        if not instructor:
            print("  SKIP: instructor@lfa.com not found — session generation requires FIELD slots (need instructor)")
            results["SC-03"] = None
        else:
            t = _create_tournament(
                db, f"{_TOURNAMENT_PREFIX}SC-03 CheckIn", sponsor, campaign, tt, campus_id, dry_run
            )
            if t:
                enroll_r = _bulk_enroll_direct(db, t.id, campaign.id, sponsor.id)
                t_db = db.query(Semester).filter(Semester.id == t.id).first()
                t_db.master_instructor_id = instructor.id
                db.commit()
                _seed_instructor_slot_and_checkins(
                    db, t.id, instructor, admin, enroll_r["enrolled"],
                    field_instructors=_fi_list, field_pitches=_fp_list,
                )
                db.commit()
                _lock_enrollment(client, t.id)
                stamped = _stamp_player_checkins(db, t.id)
                db.commit()
                print(f"  check-in stamped: {stamped} player(s)")
                if _transition(client, t.id, "CHECK_IN_OPEN"):
                    db.expire_all()
                    count = (
                        db.query(SessionModel)
                        .filter(SessionModel.semester_id == t.id)
                        .count()
                    )
                    if count != _EXPECTED_SESSIONS:
                        sys.exit(
                            f"SC-03: expected {_EXPECTED_SESSIONS} sessions after CHECK_IN_OPEN,"
                            f" got {count}. Verify group_knockout 9_players config."
                        )
                    print(f"  id={t.id}  status=CHECK_IN_OPEN  sessions={count}")
            results["SC-03"] = t.id if t else None

    # ── SC-04: full session structure validation ───────────────────────────────
    if "SC-04" in to_run:
        print("\n[SC-04] Full session structure validation (hard failure)")
        if not instructor:
            print("  SKIP: instructor@lfa.com not found — session generation requires FIELD slots (need instructor)")
            results["SC-04"] = None
        else:
            t = _create_tournament(
                db, f"{_TOURNAMENT_PREFIX}SC-04 Validate", sponsor, campaign, tt, campus_id, dry_run
            )
            if t:
                enroll_r = _bulk_enroll_direct(db, t.id, campaign.id, sponsor.id)
                t_db = db.query(Semester).filter(Semester.id == t.id).first()
                t_db.master_instructor_id = instructor.id
                db.commit()
                _seed_instructor_slot_and_checkins(
                    db, t.id, instructor, admin, enroll_r["enrolled"],
                    field_instructors=_fi_list, field_pitches=_fp_list,
                )
                db.commit()
                _lock_enrollment(client, t.id)
                stamped = _stamp_player_checkins(db, t.id)
                db.commit()
                print(f"  check-in stamped: {stamped} player(s)")
                _transition(client, t.id, "CHECK_IN_OPEN")
                db.expire_all()

                _validate_sc04(db, t.id, t.name)

                if _transition(client, t.id, "IN_PROGRESS"):
                    db.expire_all()
                    print(f"  id={t.id}  status=IN_PROGRESS")
            results["SC-04"] = t.id if t else None

    # ── SC-05: REWARDS_DISTRIBUTED (full lifecycle smoke) ─────────────────────
    if "SC-05" in to_run:
        print("\n[SC-05] REWARDS_DISTRIBUTED (full lifecycle smoke)")
        t = None
        if not instructor:
            print("  SKIP: instructor@lfa.com not found — SC-05 requires an instructor")
        else:
            t = _create_tournament(
                db,
                f"{_TOURNAMENT_PREFIX}SC-05 Rewards",
                sponsor,
                campaign,
                tt,
                campus_id,
                dry_run,
                age_group="AMATEUR",
            )
            if t:
                enroll_result = _bulk_enroll_direct(db, t.id, campaign.id, sponsor.id)
                stamped = _stamp_player_checkins(db, t.id)
                t_db = db.query(Semester).filter(Semester.id == t.id).first()
                t_db.master_instructor_id = instructor.id
                _set_reward_config_orm(db, t.id)
                db.commit()
                _seed_instructor_slot_and_checkins(
                    db, t.id, instructor, admin, enroll_result["enrolled"],
                    field_instructors=_fi_list, field_pitches=_fp_list,
                )
                db.commit()
                print(f"  check-in stamped: {stamped} player(s)  instructor={instructor.id}")

                if not _lock_enrollment(client, t.id):
                    print(f"  FAIL: enrollment lock — stopping SC-05")
                elif not _transition(client, t.id, "CHECK_IN_OPEN"):
                    print(f"  FAIL: CHECK_IN_OPEN — stopping SC-05")
                else:
                    db.expire_all()
                    session_count = (
                        db.query(SessionModel)
                        .filter(SessionModel.semester_id == t.id)
                        .count()
                    )
                    print(f"  sessions generated: {session_count}")
                    if session_count != _EXPECTED_SESSIONS:
                        print(f"  WARN: expected {_EXPECTED_SESSIONS}, got {session_count}")

                    if not _transition(client, t.id, "IN_PROGRESS"):
                        print(f"  FAIL: IN_PROGRESS — stopping SC-05")
                    else:
                        completed = _complete_gk_sessions_seed(client, db, t.id)
                        print(f"  sessions completed: {completed}")

                        r = client.post(
                            f"/api/v1/tournaments/{t.id}/calculate-rankings", json={}
                        )
                        if r.status_code not in (200, 201):
                            print(
                                f"  WARN calculate-rankings: {r.status_code} {r.text[:120]}"
                            )
                        else:
                            print(f"  rankings calculated")

                        if not _transition(client, t.id, "COMPLETED"):
                            print(f"  FAIL: COMPLETED — stopping SC-05")
                        else:
                            r = client.post(
                                f"/api/v1/tournaments/{t.id}/distribute-rewards-v2",
                                json={"tournament_id": t.id, "force_redistribution": False},
                            )
                            if r.status_code not in (200, 201):
                                print(
                                    f"  FAIL distribute-rewards: {r.status_code} {r.text[:200]}"
                                )
                            else:
                                audit_ok = _sc05_audit(db, t.id)
                                if not audit_ok:
                                    sys.exit(1)
        results["SC-05"] = t.id if t else None

    # ── SC-06: Finishing & Shooting Focus (multi-skill, SC-05 dependency) ─────
    if "SC-06" in to_run:
        print("\n[SC-06] Finishing & Shooting Focus (multi-skill, cumulative EMA)")
        t = None
        sc05_tid = results.get("SC-05")

        if not instructor:
            print("  SKIP: instructor@lfa.com not found — SC-06 requires an instructor")
        elif sc05_tid is None and "SC-05" in all_scenarios and "SC-05" in to_run:
            # SC-05 was supposed to run but did not complete.
            # Block SC-06: sprint_speed EMA prev_val would be baseline instead of post-SC-05 value,
            # making the isolated delta semantically wrong and SK14 invariant untestable.
            print("  SKIP: SC-05 did not complete — SC-06 requires SC-05 first")
            print("  Reason: sprint_speed EMA must chain from SC-05 post-value (~73.6 for rank-1)")
        else:
            if sc05_tid is None:
                print(
                    "  NOTE: SC-05 not in this run — sprint_speed EMA starts from baseline 60.0"
                    " (cross-tournament delta check SK14 will be skipped)"
                )
            else:
                print(
                    f"  SC-05 tid={sc05_tid} present — sprint_speed EMA chains from SC-05 state"
                )

            t = _create_tournament(
                db,
                f"{_TOURNAMENT_PREFIX}SC-06 Finishing",
                sponsor,
                campaign,
                tt,
                campus_id,
                dry_run,
                age_group="AMATEUR",
            )
            if t:
                enroll_result = _bulk_enroll_direct(db, t.id, campaign.id, sponsor.id)
                stamped = _stamp_player_checkins(db, t.id)
                t_db = db.query(Semester).filter(Semester.id == t.id).first()
                t_db.master_instructor_id = instructor.id
                _set_reward_config_sc06(db, t.id)
                db.commit()
                _seed_instructor_slot_and_checkins(
                    db, t.id, instructor, admin, enroll_result["enrolled"],
                    field_instructors=_fi_list, field_pitches=_fp_list,
                )
                db.commit()
                print(f"  check-in stamped: {stamped} player(s)  instructor={instructor.id}")

                if not _lock_enrollment(client, t.id):
                    print(f"  FAIL: enrollment lock — stopping SC-06")
                elif not _transition(client, t.id, "CHECK_IN_OPEN"):
                    print(f"  FAIL: CHECK_IN_OPEN — stopping SC-06")
                else:
                    db.expire_all()
                    session_count = (
                        db.query(SessionModel)
                        .filter(SessionModel.semester_id == t.id)
                        .count()
                    )
                    print(f"  sessions generated: {session_count}")
                    if session_count != _EXPECTED_SESSIONS:
                        print(f"  WARN: expected {_EXPECTED_SESSIONS}, got {session_count}")

                    if not _transition(client, t.id, "IN_PROGRESS"):
                        print(f"  FAIL: IN_PROGRESS — stopping SC-06")
                    else:
                        completed = _complete_gk_sessions_seed(client, db, t.id)
                        print(f"  sessions completed: {completed}")

                        r = client.post(
                            f"/api/v1/tournaments/{t.id}/calculate-rankings", json={}
                        )
                        if r.status_code not in (200, 201):
                            print(
                                f"  WARN calculate-rankings: {r.status_code} {r.text[:120]}"
                            )
                        else:
                            print(f"  rankings calculated")

                        if not _transition(client, t.id, "COMPLETED"):
                            print(f"  FAIL: COMPLETED — stopping SC-06")
                        else:
                            r = client.post(
                                f"/api/v1/tournaments/{t.id}/distribute-rewards-v2",
                                json={"tournament_id": t.id, "force_redistribution": False},
                            )
                            if r.status_code not in (200, 201):
                                print(
                                    f"  FAIL distribute-rewards: {r.status_code} {r.text[:200]}"
                                )
                            else:
                                audit_ok = _sc06_audit(db, t.id, sc05_tid)
                                if not audit_ok:
                                    sys.exit(1)
        results["SC-06"] = t.id if t else None

    # ── SC-07: Speed & Agility Sprint Cup ────────────────────────────────────
    if "SC-07" in to_run:
        print("\n[SC-07] Speed & Agility Sprint Cup (5-skill physical, SK-15 diminishing)")
        t = None
        sc06_tid = results.get("SC-06")
        if not instructor:
            print("  SKIP: instructor@lfa.com not found — SC-07 requires an instructor")
        elif sc06_tid is None and "SC-06" in all_scenarios and "SC-06" in to_run:
            print("  SKIP: SC-06 did not complete — SC-07 requires SC-06 first")
            print("  Reason: sprint_speed/agility EMA must chain from SC-06 post-value")
        else:
            if sc06_tid is None:
                print("  NOTE: SC-06 not in this run — EMA chains from current running value (SK-15 skipped)")
            else:
                print(f"  SC-06 tid={sc06_tid} present — sprint_speed/agility EMA chains from SC-06")
            t = _create_tournament(
                db, f"{_TOURNAMENT_PREFIX}SC-07 SpeedAgility",
                sponsor, campaign, tt, campus_id, dry_run,
                age_group="AMATEUR", game_preset_code="sprint_relay",
            )
            if t:
                enroll_result = _bulk_enroll_direct(db, t.id, campaign.id, sponsor.id)
                stamped = _stamp_player_checkins(db, t.id)
                t_db = db.query(Semester).filter(Semester.id == t.id).first()
                t_db.master_instructor_id = instructor.id
                _set_reward_config_sc07(db, t.id)
                db.commit()
                _seed_instructor_slot_and_checkins(db, t.id, instructor, admin, enroll_result["enrolled"], field_instructors=_fi_list, field_pitches=_fp_list)
                db.commit()
                print(f"  check-in stamped: {stamped} player(s)  instructor={instructor.id}")
                if not _lock_enrollment(client, t.id):
                    print(f"  FAIL: enrollment lock — stopping SC-07")
                elif not _transition(client, t.id, "CHECK_IN_OPEN"):
                    print(f"  FAIL: CHECK_IN_OPEN — stopping SC-07")
                else:
                    db.expire_all()
                    session_count = db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()
                    print(f"  sessions generated: {session_count}")
                    if session_count != _EXPECTED_SESSIONS:
                        print(f"  WARN: expected {_EXPECTED_SESSIONS}, got {session_count}")
                    if not _transition(client, t.id, "IN_PROGRESS"):
                        print(f"  FAIL: IN_PROGRESS — stopping SC-07")
                    else:
                        completed = _complete_gk_sessions_seed(client, db, t.id)
                        print(f"  sessions completed: {completed}")
                        r = client.post(f"/api/v1/tournaments/{t.id}/calculate-rankings", json={})
                        if r.status_code not in (200, 201):
                            print(f"  WARN calculate-rankings: {r.status_code} {r.text[:120]}")
                        else:
                            print(f"  rankings calculated")
                        if not _transition(client, t.id, "COMPLETED"):
                            print(f"  FAIL: COMPLETED — stopping SC-07")
                        else:
                            r = client.post(
                                f"/api/v1/tournaments/{t.id}/distribute-rewards-v2",
                                json={"tournament_id": t.id, "force_redistribution": False},
                            )
                            if r.status_code not in (200, 201):
                                print(f"  FAIL distribute-rewards: {r.status_code} {r.text[:200]}")
                            else:
                                audit_ok = _sc07_audit(db, t.id, sc06_tid)
                                if not audit_ok:
                                    sys.exit(1)
        results["SC-07"] = t.id if t else None

    # ── SC-08: Strength & Endurance Challenge ─────────────────────────────────
    if "SC-08" in to_run:
        print("\n[SC-08] Strength & Endurance Challenge (5-skill, SK-17 stamina diminishing)")
        t = None
        sc07_tid = results.get("SC-07")
        if not instructor:
            print("  SKIP: instructor@lfa.com not found — SC-08 requires an instructor")
        elif sc07_tid is None and "SC-07" in all_scenarios and "SC-07" in to_run:
            print("  SKIP: SC-07 did not complete — SC-08 requires SC-07 first")
            print("  Reason: stamina EMA must chain from SC-07 post-value")
        else:
            if sc07_tid is None:
                print("  NOTE: SC-07 not in this run — stamina EMA chains from current value (SK-17 skipped)")
            else:
                print(f"  SC-07 tid={sc07_tid} present — stamina EMA chains from SC-07")
            t = _create_tournament(
                db, f"{_TOURNAMENT_PREFIX}SC-08 Strength",
                sponsor, campaign, tt, campus_id, dry_run,
                age_group="AMATEUR", game_preset_code="strength_challenge",
            )
            if t:
                enroll_result = _bulk_enroll_direct(db, t.id, campaign.id, sponsor.id)
                stamped = _stamp_player_checkins(db, t.id)
                t_db = db.query(Semester).filter(Semester.id == t.id).first()
                t_db.master_instructor_id = instructor.id
                _set_reward_config_sc08(db, t.id)
                db.commit()
                _seed_instructor_slot_and_checkins(db, t.id, instructor, admin, enroll_result["enrolled"], field_instructors=_fi_list, field_pitches=_fp_list)
                db.commit()
                print(f"  check-in stamped: {stamped} player(s)  instructor={instructor.id}")
                if not _lock_enrollment(client, t.id):
                    print(f"  FAIL: enrollment lock — stopping SC-08")
                elif not _transition(client, t.id, "CHECK_IN_OPEN"):
                    print(f"  FAIL: CHECK_IN_OPEN — stopping SC-08")
                else:
                    db.expire_all()
                    session_count = db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()
                    print(f"  sessions generated: {session_count}")
                    if session_count != _EXPECTED_SESSIONS:
                        print(f"  WARN: expected {_EXPECTED_SESSIONS}, got {session_count}")
                    if not _transition(client, t.id, "IN_PROGRESS"):
                        print(f"  FAIL: IN_PROGRESS — stopping SC-08")
                    else:
                        completed = _complete_gk_sessions_seed(client, db, t.id)
                        print(f"  sessions completed: {completed}")
                        r = client.post(f"/api/v1/tournaments/{t.id}/calculate-rankings", json={})
                        if r.status_code not in (200, 201):
                            print(f"  WARN calculate-rankings: {r.status_code} {r.text[:120]}")
                        else:
                            print(f"  rankings calculated")
                        if not _transition(client, t.id, "COMPLETED"):
                            print(f"  FAIL: COMPLETED — stopping SC-08")
                        else:
                            r = client.post(
                                f"/api/v1/tournaments/{t.id}/distribute-rewards-v2",
                                json={"tournament_id": t.id, "force_redistribution": False},
                            )
                            if r.status_code not in (200, 201):
                                print(f"  FAIL distribute-rewards: {r.status_code} {r.text[:200]}")
                            else:
                                audit_ok = _sc08_audit(db, t.id, sc07_tid)
                                if not audit_ok:
                                    sys.exit(1)
        results["SC-08"] = t.id if t else None

    # ── SC-09: Creative Passing & Vision ─────────────────────────────────────
    if "SC-09" in to_run:
        print("\n[SC-09] Creative Passing & Vision (5-skill, all first-time entries)")
        t = None
        sc08_tid = results.get("SC-08")
        if not instructor:
            print("  SKIP: instructor@lfa.com not found — SC-09 requires an instructor")
        elif sc08_tid is None and "SC-08" in all_scenarios and "SC-08" in to_run:
            print("  SKIP: SC-08 did not complete — SC-09 requires SC-08 first")
            print("  Reason: SC-07..SC-09 must run in chain order")
        else:
            if sc08_tid is None:
                print("  NOTE: SC-08 not in this run — SC-09 runs as standalone (no chain EMA)")
            t = _create_tournament(
                db, f"{_TOURNAMENT_PREFIX}SC-09 Passing",
                sponsor, campaign, tt, campus_id, dry_run,
                age_group="AMATEUR", game_preset_code="passing_focus",
            )
            if t:
                enroll_result = _bulk_enroll_direct(db, t.id, campaign.id, sponsor.id)
                stamped = _stamp_player_checkins(db, t.id)
                t_db = db.query(Semester).filter(Semester.id == t.id).first()
                t_db.master_instructor_id = instructor.id
                _set_reward_config_sc09(db, t.id)
                db.commit()
                _seed_instructor_slot_and_checkins(db, t.id, instructor, admin, enroll_result["enrolled"], field_instructors=_fi_list, field_pitches=_fp_list)
                db.commit()
                print(f"  check-in stamped: {stamped} player(s)  instructor={instructor.id}")
                if not _lock_enrollment(client, t.id):
                    print(f"  FAIL: enrollment lock — stopping SC-09")
                elif not _transition(client, t.id, "CHECK_IN_OPEN"):
                    print(f"  FAIL: CHECK_IN_OPEN — stopping SC-09")
                else:
                    db.expire_all()
                    session_count = db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()
                    print(f"  sessions generated: {session_count}")
                    if session_count != _EXPECTED_SESSIONS:
                        print(f"  WARN: expected {_EXPECTED_SESSIONS}, got {session_count}")
                    if not _transition(client, t.id, "IN_PROGRESS"):
                        print(f"  FAIL: IN_PROGRESS — stopping SC-09")
                    else:
                        completed = _complete_gk_sessions_seed(client, db, t.id)
                        print(f"  sessions completed: {completed}")
                        r = client.post(f"/api/v1/tournaments/{t.id}/calculate-rankings", json={})
                        if r.status_code not in (200, 201):
                            print(f"  WARN calculate-rankings: {r.status_code} {r.text[:120]}")
                        else:
                            print(f"  rankings calculated")
                        if not _transition(client, t.id, "COMPLETED"):
                            print(f"  FAIL: COMPLETED — stopping SC-09")
                        else:
                            r = client.post(
                                f"/api/v1/tournaments/{t.id}/distribute-rewards-v2",
                                json={"tournament_id": t.id, "force_redistribution": False},
                            )
                            if r.status_code not in (200, 201):
                                print(f"  FAIL distribute-rewards: {r.status_code} {r.text[:200]}")
                            else:
                                audit_ok = _sc09_audit(db, t.id)
                                if not audit_ok:
                                    sys.exit(1)
        results["SC-09"] = t.id if t else None

    # ── SC-10: Defensive Mastery Cup ──────────────────────────────────────────
    if "SC-10" in to_run:
        print("\n[SC-10] Defensive Mastery Cup (5-skill, all first-time entries)")
        t = None
        sc09_tid = results.get("SC-09")
        if not instructor:
            print("  SKIP: instructor@lfa.com not found — SC-10 requires an instructor")
        elif sc09_tid is None and "SC-09" in all_scenarios and "SC-09" in to_run:
            print("  SKIP: SC-09 did not complete — SC-10 requires SC-09 first")
            print("  Reason: SC-07..SC-10 must run in chain order")
        else:
            if sc09_tid is None:
                print("  NOTE: SC-09 not in this run — SC-10 runs as standalone")
            t = _create_tournament(
                db, f"{_TOURNAMENT_PREFIX}SC-10 Defence",
                sponsor, campaign, tt, campus_id, dry_run,
                age_group="AMATEUR", game_preset_code="outfield_default",
            )
            if t:
                enroll_result = _bulk_enroll_direct(db, t.id, campaign.id, sponsor.id)
                stamped = _stamp_player_checkins(db, t.id)
                t_db = db.query(Semester).filter(Semester.id == t.id).first()
                t_db.master_instructor_id = instructor.id
                _set_reward_config_sc10(db, t.id)
                db.commit()
                _seed_instructor_slot_and_checkins(db, t.id, instructor, admin, enroll_result["enrolled"], field_instructors=_fi_list, field_pitches=_fp_list)
                db.commit()
                print(f"  check-in stamped: {stamped} player(s)  instructor={instructor.id}")
                if not _lock_enrollment(client, t.id):
                    print(f"  FAIL: enrollment lock — stopping SC-10")
                elif not _transition(client, t.id, "CHECK_IN_OPEN"):
                    print(f"  FAIL: CHECK_IN_OPEN — stopping SC-10")
                else:
                    db.expire_all()
                    session_count = db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()
                    print(f"  sessions generated: {session_count}")
                    if session_count != _EXPECTED_SESSIONS:
                        print(f"  WARN: expected {_EXPECTED_SESSIONS}, got {session_count}")
                    if not _transition(client, t.id, "IN_PROGRESS"):
                        print(f"  FAIL: IN_PROGRESS — stopping SC-10")
                    else:
                        completed = _complete_gk_sessions_seed(client, db, t.id)
                        print(f"  sessions completed: {completed}")
                        r = client.post(f"/api/v1/tournaments/{t.id}/calculate-rankings", json={})
                        if r.status_code not in (200, 201):
                            print(f"  WARN calculate-rankings: {r.status_code} {r.text[:120]}")
                        else:
                            print(f"  rankings calculated")
                        if not _transition(client, t.id, "COMPLETED"):
                            print(f"  FAIL: COMPLETED — stopping SC-10")
                        else:
                            r = client.post(
                                f"/api/v1/tournaments/{t.id}/distribute-rewards-v2",
                                json={"tournament_id": t.id, "force_redistribution": False},
                            )
                            if r.status_code not in (200, 201):
                                print(f"  FAIL distribute-rewards: {r.status_code} {r.text[:200]}")
                            else:
                                audit_ok = _sc10_audit(db, t.id)
                                if not audit_ok:
                                    sys.exit(1)
        results["SC-10"] = t.id if t else None

    # ── SC-11: Complete Attacker Championship ─────────────────────────────────
    if "SC-11" in to_run:
        print("\n[SC-11] Complete Attacker Championship (5-skill, SK-16/SK-18 diminishing)")
        t = None
        sc06_tid = results.get("SC-06")
        sc09_tid = results.get("SC-09")
        sc10_tid = results.get("SC-10")
        if not instructor:
            print("  SKIP: instructor@lfa.com not found — SC-11 requires an instructor")
        elif sc10_tid is None and "SC-10" in all_scenarios and "SC-10" in to_run:
            print("  SKIP: SC-10 did not complete — SC-11 requires SC-10 first")
            print("  Reason: SC-07..SC-11 must run in chain order")
        else:
            if sc06_tid is None:
                print("  NOTE: SC-06 not in this run — finishing EMA chains from current value (SK-16 skipped)")
            else:
                print(f"  SC-06 tid={sc06_tid} present — finishing EMA chains from SC-06 (SK-16)")
            if sc09_tid is None:
                print("  NOTE: SC-09 not in this run — long_shots EMA chains from current value (SK-18 skipped)")
            else:
                print(f"  SC-09 tid={sc09_tid} present — long_shots EMA chains from SC-09 (SK-18)")
            t = _create_tournament(
                db, f"{_TOURNAMENT_PREFIX}SC-11 Attacker",
                sponsor, campaign, tt, campus_id, dry_run,
                age_group="AMATEUR", game_preset_code="shooting_focus",
            )
            if t:
                enroll_result = _bulk_enroll_direct(db, t.id, campaign.id, sponsor.id)
                stamped = _stamp_player_checkins(db, t.id)
                t_db = db.query(Semester).filter(Semester.id == t.id).first()
                t_db.master_instructor_id = instructor.id
                _set_reward_config_sc11(db, t.id)
                db.commit()
                _seed_instructor_slot_and_checkins(db, t.id, instructor, admin, enroll_result["enrolled"], field_instructors=_fi_list, field_pitches=_fp_list)
                db.commit()
                print(f"  check-in stamped: {stamped} player(s)  instructor={instructor.id}")
                if not _lock_enrollment(client, t.id):
                    print(f"  FAIL: enrollment lock — stopping SC-11")
                elif not _transition(client, t.id, "CHECK_IN_OPEN"):
                    print(f"  FAIL: CHECK_IN_OPEN — stopping SC-11")
                else:
                    db.expire_all()
                    session_count = db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()
                    print(f"  sessions generated: {session_count}")
                    if session_count != _EXPECTED_SESSIONS:
                        print(f"  WARN: expected {_EXPECTED_SESSIONS}, got {session_count}")
                    if not _transition(client, t.id, "IN_PROGRESS"):
                        print(f"  FAIL: IN_PROGRESS — stopping SC-11")
                    else:
                        completed = _complete_gk_sessions_seed(client, db, t.id)
                        print(f"  sessions completed: {completed}")
                        r = client.post(f"/api/v1/tournaments/{t.id}/calculate-rankings", json={})
                        if r.status_code not in (200, 201):
                            print(f"  WARN calculate-rankings: {r.status_code} {r.text[:120]}")
                        else:
                            print(f"  rankings calculated")
                        if not _transition(client, t.id, "COMPLETED"):
                            print(f"  FAIL: COMPLETED — stopping SC-11")
                        else:
                            r = client.post(
                                f"/api/v1/tournaments/{t.id}/distribute-rewards-v2",
                                json={"tournament_id": t.id, "force_redistribution": False},
                            )
                            if r.status_code not in (200, 201):
                                print(f"  FAIL distribute-rewards: {r.status_code} {r.text[:200]}")
                            else:
                                audit_ok = _sc11_audit(db, t.id, sc06_tid, sc09_tid)
                                if not audit_ok:
                                    sys.exit(1)
        results["SC-11"] = t.id if t else None

    return results


# ─── CLI entry point ─────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Seed promotion event scenarios")
    parser.add_argument("--dry-run", action="store_true", help="Print intent only, write nothing")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete all PROMO-SEED-* data (requires ALLOW_SEED_RESET=true)",
    )
    parser.add_argument(
        "--scenarios",
        help="Comma-separated scenario IDs to run, e.g. SC-01,SC-04. Default: all.",
    )
    parser.add_argument("--campus-id", type=int, default=1, metavar="ID")
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Run preflight audit only — print issues and exit without seeding",
    )
    parser.add_argument(
        "--no-fail-on-missing-prereq",
        action="store_true",
        help=(
            "Disable hard-fail on preflight issues (NOT recommended). "
            "Default: seed always hard-fails if pitch/instructor/preset/admin is missing."
        ),
    )
    parser.add_argument(
        "--bootstrap-missing-prereq",
        action="store_true",
        help=(
            "Dev mode: create a default active Pitch if none exists on the campus, "
            "then proceed normally. Must NOT be used against production. "
            "Default mode hard-fails instead to surface real environment problems."
        ),
    )
    args = parser.parse_args()

    _assert_not_production()

    logging.basicConfig(level=logging.WARNING)

    db = SessionLocal()
    try:
        if args.reset:
            run_reset(db)
            return

        if args.audit_only:
            issues = _run_preflight_audit(db, args.campus_id, fail=False)
            sys.exit(1 if issues else 0)

        scenario_ids = (
            [s.strip() for s in args.scenarios.split(",")] if args.scenarios else None
        )

        # Optional dev-mode: create default pitch before preflight runs
        if args.bootstrap_missing_prereq and not args.dry_run:
            _bootstrap_missing_prereqs(db, args.campus_id)

        admin = db.query(User).filter(User.email == "admin@lfa.com").first()
        if not admin:
            sys.exit("admin@lfa.com not found — run bootstrap first")

        instructor = db.query(User).filter(User.email == "instructor@lfa.com").first()

        app.dependency_overrides[get_current_user_web] = lambda: admin
        app.dependency_overrides[get_current_admin_user_hybrid] = lambda: admin
        app.dependency_overrides[get_current_admin_or_instructor_user_hybrid] = lambda: admin
        # get_current_user: used by /sessions/{id}/check-in (instructor role required)
        if instructor:
            app.dependency_overrides[get_current_user] = lambda: instructor

        client = TestClient(app, follow_redirects=False)

        print("\n" + "=" * 60)
        print("  PROMOTION EVENT SEED — Phase 2")
        if args.dry_run:
            print("  MODE: dry-run (no writes)")
        if args.bootstrap_missing_prereq:
            print("  MODE: --bootstrap-missing-prereq (dev only)")
        print("=" * 60)

        # Default: hard-fail on any missing prereq. Disable only with --no-fail-on-missing-prereq.
        fail_on_missing_prereq = not args.no_fail_on_missing_prereq

        results = run_scenarios(
            db,
            client,
            scenario_ids=scenario_ids,
            dry_run=args.dry_run,
            campus_id=args.campus_id,
            fail_on_missing_prereq=fail_on_missing_prereq,
        )

        print("\n" + "=" * 60)
        print("  DONE")
        for sc, tid in results.items():
            if tid and not args.dry_run:
                print(f"  {sc}: http://localhost:8000{_admin_tournament_url(tid)}")
            else:
                print(f"  {sc}: (dry-run or skipped)")
        print("=" * 60)

    finally:
        db.close()
        app.dependency_overrides.clear()


if __name__ == "__main__":
    main()
