"""Sponsor Audience — Promote to User service.

Business rules:
  - Only ACTIVE entries with consent_given=True may be promoted.
  - SUPPRESSED / UNSUBSCRIBED / DELETED → skipped, no User created.
  - If entry.user_id is already set → full no-op (idempotent).
    promoted_at / promoted_by are never overwritten.
  - Email matches existing User → user_id linked only. User profile NOT modified.
    DOB is NOT overwritten on existing Users.
  - No existing User → User + UserLicense created (role=STUDENT, random password).
    date_of_birth copied from entry (may be None).
  - Baseline onboarding (P2-D): written after promote IF all 4 conditions met:
      1. entry.status == ACTIVE  (already checked by promote gate)
      2. entry.consent_given     (already checked)
      3. entry.date_of_birth IS NOT NULL
      4. entry.position in {STRIKER, MIDFIELDER, DEFENDER, GOALKEEPER}
    AND license.onboarding_completed is False AND license.football_skills is None.
  - Existing User: baseline only if their LFA_FOOTBALL_PLAYER license has no onboarding at all.
    No license → baseline skipped (no new license created for existing users).
  - foot_dominance default (50) applied only when baseline is actually written.
  - No SemesterEnrollment is ever created here.
  - Single db.commit() at the end of the batch (atomic).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models.license import UserLicense
from app.models.sponsor import SponsorAudienceEntry
from app.models.user import User, UserRole
from app.services.skill_progression import SYSTEM_BASELINE
from app.skills_config import get_all_skill_keys

if TYPE_CHECKING:
    pass

import logging

logger = logging.getLogger(__name__)

_VALID_POSITIONS = frozenset({"STRIKER", "MIDFIELDER", "DEFENDER", "GOALKEEPER"})


@dataclass
class PromoteResult:
    promoted: int = 0              # new User created OR existing User linked for first time
    already_linked: int = 0        # entry.user_id was already set → full no-op
    skipped: int = 0               # status != ACTIVE or consent_given == False
    promoted_with_onboarding: int = 0    # subset of promoted where baseline was written
    promoted_without_onboarding: int = 0  # subset of promoted where baseline was skipped
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.promoted + self.already_linked + self.skipped


def _build_baseline_football_skills() -> dict:
    """29-key football_skills dict matching the real onboarding structure."""
    now_iso = datetime.now(timezone.utc).isoformat()
    skills = {}
    for key in get_all_skill_keys():
        skills[key] = {
            "system_baseline":  SYSTEM_BASELINE,
            "self_assessment":  SYSTEM_BASELINE,
            "baseline":         SYSTEM_BASELINE,
            "current_level":    SYSTEM_BASELINE,
            "total_delta":      0.0,
            "tournament_delta": 0.0,
            "assessment_delta": 0.0,
            "last_updated":     now_iso,
            "assessment_count": 0,
            "tournament_count": 0,
        }
    return skills


def _should_write_baseline(entry: SponsorAudienceEntry, license: UserLicense) -> bool:
    """True only when all 4 entry conditions pass and license has no onboarding at all."""
    if entry.date_of_birth is None:
        return False
    if entry.position not in _VALID_POSITIONS:
        return False
    if license.onboarding_completed:
        return False
    if license.football_skills is not None:
        return False
    return True


def _write_baseline(entry: SponsorAudienceEntry, license: UserLicense, now: datetime) -> None:
    """Write baseline football_skills + onboarding flags onto license (no commit)."""
    from sqlalchemy.orm.attributes import flag_modified

    foot_val = float(entry.foot_dominance) if entry.foot_dominance is not None else 50.0

    license.football_skills = _build_baseline_football_skills()
    license.right_foot_score = foot_val
    license.left_foot_score = 100.0 - foot_val
    license.onboarding_completed = True
    license.onboarding_completed_at = now

    flag_modified(license, "football_skills")


def promote_entries(
    entry_ids: list[int],
    sponsor_id: int,
    db: Session,
    admin_user: User,
    campaign_id: int | None = None,
) -> PromoteResult:
    """Promote selected SponsorAudienceEntry rows to Users with optional baseline onboarding.

    Atomically processes the batch; single db.commit() at end.
    Any unhandled exception propagates and rolls back everything.

    campaign_id: when provided, only entries belonging to that campaign are promoted.
    Entries from other campaigns in the same sponsor are silently excluded and reported
    as errors — prevents cross-campaign promote via crafted POST bodies.
    """
    result = PromoteResult()
    if not entry_ids:
        return result

    now = datetime.now(timezone.utc)

    # Load all requested entries scoped to this sponsor+campaign in one query
    q = (
        db.query(SponsorAudienceEntry)
        .filter(
            SponsorAudienceEntry.id.in_(entry_ids),
            SponsorAudienceEntry.sponsor_id == sponsor_id,
        )
    )
    if campaign_id is not None:
        q = q.filter(SponsorAudienceEntry.campaign_id == campaign_id)
    entries = q.all()

    # Report IDs that were not found or belong to another sponsor/campaign
    found_ids = {e.id for e in entries}
    for missing in set(entry_ids) - found_ids:
        result.errors.append(f"Entry {missing}: not found for this sponsor/campaign")

    # Collect emails that need User lookup (only promotion candidates)
    candidate_emails = {
        e.email
        for e in entries
        if e.user_id is None and e.status == "ACTIVE" and e.consent_given
    }

    # Pre-fetch existing Users by email — one query for the whole batch
    existing_users: dict[str, User] = {}
    if candidate_emails:
        for u in db.query(User).filter(User.email.in_(candidate_emails)).all():
            existing_users[u.email] = u

    for entry in entries:
        # ── Idempotence: already promoted ─────────────────────────────────────
        if entry.user_id is not None:
            result.already_linked += 1
            continue

        # ── Status guard ──────────────────────────────────────────────────────
        if entry.status != "ACTIVE":
            result.skipped += 1
            continue

        # ── Explicit consent guard (belt-and-suspenders) ──────────────────────
        if not entry.consent_given:
            result.skipped += 1
            continue

        # ── Link or create User ───────────────────────────────────────────────
        license: UserLicense | None = None

        if entry.email in existing_users:
            # Email matches existing User — link only, no profile modification (DOB not touched)
            existing_user = existing_users[entry.email]
            entry.user_id = existing_user.id
            # Find existing LFA_FOOTBALL_PLAYER license (no new license created)
            license = (
                db.query(UserLicense)
                .filter(
                    UserLicense.user_id == existing_user.id,
                    UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
                    UserLicense.is_active == True,
                )
                .first()
            )
        else:
            new_user = User(
                email=entry.email,
                name=f"{entry.first_name} {entry.last_name}",
                first_name=entry.first_name,
                last_name=entry.last_name,
                date_of_birth=entry.date_of_birth,
                password_hash=get_password_hash(uuid.uuid4().hex),
                role=UserRole.STUDENT,
                is_active=True,
                onboarding_completed=False,
                payment_verified=False,
                created_by=admin_user.id,
            )
            db.add(new_user)
            db.flush()
            license = UserLicense(
                user_id=new_user.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                current_level=1,
                max_achieved_level=1,
                started_at=now,
                is_active=True,
            )
            db.add(license)
            db.flush()
            entry.user_id = new_user.id

        # ── Baseline onboarding (P2-D) ────────────────────────────────────────
        if license is not None and _should_write_baseline(entry, license):
            _write_baseline(entry, license, now)
            result.promoted_with_onboarding += 1
        else:
            result.promoted_without_onboarding += 1

        # ── Set audit fields (only on first promotion) ────────────────────────
        entry.promoted_at = now
        entry.promoted_by = admin_user.id
        result.promoted += 1

    db.commit()

    logger.info(
        "sponsor_audience_promote_done sponsor_id=%s promoted=%s "
        "(with_onboarding=%s without=%s) already_linked=%s skipped=%s errors=%s",
        sponsor_id, result.promoted,
        result.promoted_with_onboarding, result.promoted_without_onboarding,
        result.already_linked, result.skipped, len(result.errors),
    )
    return result
