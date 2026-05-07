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
  SC-01  DRAFT             — tournament created, no audience enrolled
  SC-02  ENROLLMENT_CLOSED — audience locked, 9 players bulk-enrolled
  SC-03  CHECK_IN_OPEN     — 13 sessions generated (9 GS + 2 SF + 1 F + 1 B)
  SC-04  VALIDATION        — hard failure if session structure or SF labels are wrong

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
import logging
import os
import sys
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
from app.models.semester import Semester, SemesterCategory, SemesterStatus
from app.models.semester_enrollment import SemesterEnrollment
from app.models.session import Session as SessionModel
from app.models.sponsor import Sponsor, SponsorAudienceEntry, SponsorCampaign
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_enums import TournamentPhase
from app.models.tournament_type import TournamentType
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from app.dependencies import (
    get_current_admin_or_instructor_user_hybrid,
    get_current_admin_user_hybrid,
    get_current_user_web,
)

logger = logging.getLogger(__name__)

_SPONSOR_NAME = "SEED-SPONSOR"
_CAMPAIGN_NAME = "SEED-CAMPAIGN"
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
        db.query(Semester).filter(
            Semester.id.in_(promo_ids)
        ).delete(synchronize_session=False)
        print(f"  Deleted {len(promo_ids)} PROMO-SEED tournament(s) with their sessions/enrollments")

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
        email = f"seed.player.{i}@promo-seed.test"

        user = db.query(User).filter(User.email == email).first()
        if not user:
            user = User(
                email=email,
                name=f"Seed{i} PromoPlayer",
                first_name=f"Seed{i}",
                last_name="PromoPlayer",
                password_hash=get_password_hash(uuid.uuid4().hex),
                role=UserRole.STUDENT,
                is_active=True,
                credit_balance=0,
            )
            db.add(user)
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
            lic = UserLicense(
                user_id=user.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                is_active=True,
                started_at=now,
            )
            db.add(lic)
            db.flush()
        else:
            lic.is_active = True

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
        )
    )
    db.flush()

    # Link default game preset so session generator can validate min_players
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

def _run_preflight_audit(db: DBSession, campus_id: int, fail: bool = False) -> list[str]:
    """Check domain invariants before running scenarios.

    Returns a list of human-readable issue strings.
    If *fail* is True, exits the process on the first failure.
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
        if pitch_count == 0:
            issues.append(
                f"Campus {campus_id} ({campus.name}) has no active pitches — "
                "run bootstrap to create Pálya A / Pálya B"
            )

    default_preset = db.query(GamePreset).filter(GamePreset.code == "outfield_default").first()
    if not default_preset:
        issues.append("GamePreset code='outfield_default' not found — run bootstrap first")

    admin = db.query(User).filter(User.email == "admin@lfa.com").first()
    if not admin:
        issues.append("admin@lfa.com not found — run bootstrap first")

    if issues:
        print("\n  PREFLIGHT AUDIT ISSUES:")
        for issue in issues:
            print(f"    * {issue}")
        if fail:
            sys.exit(f"Seed aborted: {len(issues)} preflight issue(s)")
    else:
        print("  PREFLIGHT AUDIT OK")

    return issues


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


# ─── Main entry point ─────────────────────────────────────────────────────────

def run_scenarios(
    db: DBSession,
    client: TestClient,
    *,
    scenario_ids: list[str] | None = None,
    dry_run: bool = False,
    campus_id: int = 1,
    fail_on_missing_prereq: bool = False,
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
        fail_on_missing_prereq: If True, exit on any audit failure before seeding.

    Returns:
        Dict mapping scenario_id -> created tournament id (or None in dry-run).
    """
    _assert_not_production()

    tt = _preflight_group_knockout_9p(db)

    # Domain integrity audit (pitches, default preset, admin user)
    _run_preflight_audit(db, campus_id, fail=fail_on_missing_prereq)

    all_scenarios = ["SC-01", "SC-02", "SC-03", "SC-04"]
    to_run = [s for s in all_scenarios if s in (scenario_ids or all_scenarios)]

    admin = db.query(User).filter(User.email == "admin@lfa.com").first()
    if not admin:
        sys.exit("admin@lfa.com not found — run bootstrap first")

    instructor = db.query(User).filter(User.email == "instructor@lfa.com").first()

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
        t = _create_tournament(
            db, f"{_TOURNAMENT_PREFIX}SC-03 CheckIn", sponsor, campaign, tt, campus_id, dry_run
        )
        if t:
            _bulk_enroll_direct(db, t.id, campaign.id, sponsor.id)
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
        t = _create_tournament(
            db, f"{_TOURNAMENT_PREFIX}SC-04 Validate", sponsor, campaign, tt, campus_id, dry_run
        )
        if t:
            _bulk_enroll_direct(db, t.id, campaign.id, sponsor.id)
            db.commit()
            _lock_enrollment(client, t.id)
            stamped = _stamp_player_checkins(db, t.id)
            db.commit()
            print(f"  check-in stamped: {stamped} player(s)")
            _transition(client, t.id, "CHECK_IN_OPEN")
            db.expire_all()

            _validate_sc04(db, t.id, t.name)

            if instructor:
                t_db = db.query(Semester).filter(Semester.id == t.id).first()
                t_db.master_instructor_id = instructor.id
                db.commit()
                db.expire_all()
                if _transition(client, t.id, "IN_PROGRESS"):
                    db.expire_all()
                    print(f"  id={t.id}  status=IN_PROGRESS")
            else:
                print(f"  id={t.id}  status=CHECK_IN_OPEN  (no instructor@lfa.com, skipping IN_PROGRESS)")
        results["SC-04"] = t.id if t else None

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
        "--fail-on-missing-prereq",
        action="store_true",
        help="Abort seeding if preflight audit finds any issues (pitches, preset, admin)",
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

        admin = db.query(User).filter(User.email == "admin@lfa.com").first()
        if not admin:
            sys.exit("admin@lfa.com not found — run bootstrap first")

        app.dependency_overrides[get_current_user_web] = lambda: admin
        app.dependency_overrides[get_current_admin_user_hybrid] = lambda: admin
        app.dependency_overrides[get_current_admin_or_instructor_user_hybrid] = lambda: admin

        client = TestClient(app, follow_redirects=False)

        print("\n" + "=" * 60)
        print("  PROMOTION EVENT SEED — Phase 2")
        if args.dry_run:
            print("  MODE: dry-run (no writes)")
        print("=" * 60)

        results = run_scenarios(
            db,
            client,
            scenario_ids=scenario_ids,
            dry_run=args.dry_run,
            campus_id=args.campus_id,
            fail_on_missing_prereq=args.fail_on_missing_prereq,
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
