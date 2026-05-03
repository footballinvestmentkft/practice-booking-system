"""Sponsor Audience — Promote to User service.

Business rules:
  - Only ACTIVE entries with consent_given=True may be promoted.
  - SUPPRESSED / UNSUBSCRIBED / DELETED → skipped, no User created.
  - If entry.user_id is already set → full no-op (idempotent).
    promoted_at / promoted_by are never overwritten.
  - Email matches existing User → user_id linked only. User profile NOT modified.
    DOB is NOT overwritten on existing Users.
  - No existing User → User created (role=STUDENT, random password).
  - License: _ensure_license() returns the existing active license for
    campaign.specialization_type, or creates a new one — for BOTH new and
    existing users.  Existing licenses of other types are never touched.
  - Credit flow (per promoted entry, atomic with the batch commit):
      1. SPONSOR_CREDIT_GRANT  +campaign.credit_grant_amount  (idempotent)
      2. SPECIALIZATION_UNLOCK -campaign.unlock_cost           (idempotent)
      Grant and unlock are ALWAYS issued as a pair; neither can exist without
      the other within a successful promote run.
  - Baseline onboarding (P2-D): written after credits IF all 4 conditions met:
      1. entry.status == ACTIVE  (already checked)
      2. entry.consent_given     (already checked)
      3. entry.date_of_birth IS NOT NULL
      4. entry.position in {STRIKER, MIDFIELDER, DEFENDER, GOALKEEPER}
    AND license.onboarding_completed is False AND license.football_skills is None.
  - foot_dominance default (50) applied only when baseline is actually written.
  - No SemesterEnrollment is ever created here.
  - Single db.commit() at the end of the batch (atomic).
  - If campaign_id is None the credit flow is skipped (legacy/test path only).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models.license import UserLicense
from app.models.sponsor import SponsorAudienceEntry, SponsorCampaign
from app.models.user import User, UserRole
from app.services.credit_service import CreditService
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
    credits_granted: int = 0       # total SPONSOR_CREDIT_GRANT amount across the batch
    unlock_deductions: int = 0     # total SPECIALIZATION_UNLOCK amount across the batch
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


def _ensure_license(
    user: User,
    specialization_type: str,
    now: datetime,
    db: Session,
) -> UserLicense:
    """Return the user's active license for specialization_type, creating one if absent."""
    license = (
        db.query(UserLicense)
        .filter(
            UserLicense.user_id == user.id,
            UserLicense.specialization_type == specialization_type,
            UserLicense.is_active == True,
        )
        .first()
    )
    if license is None:
        license = UserLicense(
            user_id=user.id,
            specialization_type=specialization_type,
            current_level=1,
            max_achieved_level=1,
            started_at=now,
            is_active=True,
        )
        db.add(license)
        db.flush()
    return license


def _apply_credits(
    user: User,
    campaign: SponsorCampaign,
    credit_svc: CreditService,
) -> tuple[int, int]:
    """Issue grant + unlock as an invariant pair. Returns (granted, deducted)."""
    grant_key  = f"sponsor_grant:{campaign.id}:{user.id}"
    unlock_key = f"spec_unlock:{campaign.id}:{user.id}:{campaign.specialization_type}"

    credit_svc.award(
        user=user,
        amount=campaign.credit_grant_amount,
        transaction_type="SPONSOR_CREDIT_GRANT",
        description=f"Sponsor credit grant — {campaign.name}",
        idempotency_key=grant_key,
        sponsor_id=campaign.sponsor_id,
        campaign_id=campaign.id,
    )
    credit_svc.deduct_batch(
        user=user,
        amount=campaign.unlock_cost,
        transaction_type="SPECIALIZATION_UNLOCK",
        description=(
            f"Specialization unlock — {campaign.specialization_type} via {campaign.name}"
        ),
        idempotency_key=unlock_key,
        sponsor_id=campaign.sponsor_id,
        campaign_id=campaign.id,
    )
    return campaign.credit_grant_amount, campaign.unlock_cost


def promote_entries(
    entry_ids: list[int],
    sponsor_id: int,
    db: Session,
    admin_user: User,
    campaign_id: int | None = None,
) -> PromoteResult:
    """Promote selected SponsorAudienceEntry rows to Users.

    Per-entry steps (in order):
      1. Idempotence: entry.user_id already set → already_linked, skip
      2. Status + consent guard → skipped
      3. User link-or-create
      4. License ensure (create if absent for campaign.specialization_type)
      5. Credit grant (+campaign.credit_grant_amount) — idempotent
      6. Specialization unlock (-campaign.unlock_cost) — idempotent, always paired with grant
      7. Baseline onboarding (optional, condition-gated)
      8. Audit fields

    Atomically processes the batch; single db.commit() at end.
    Any unhandled exception propagates and rolls back everything — no partial commits.

    campaign_id: when provided, only entries belonging to that campaign are promoted.
    Also loads the SponsorCampaign for credit config + specialization_type.
    When None, credit flow is skipped (legacy path).
    """
    result = PromoteResult()
    if not entry_ids:
        return result

    now = datetime.now(timezone.utc)

    # Load campaign for credit config (required for P7 flow)
    campaign: SponsorCampaign | None = None
    if campaign_id is not None:
        campaign = (
            db.query(SponsorCampaign)
            .filter(
                SponsorCampaign.id == campaign_id,
                SponsorCampaign.sponsor_id == sponsor_id,
            )
            .first()
        )
        if campaign is None:
            result.errors.append(
                f"Campaign {campaign_id} not found for sponsor {sponsor_id}"
            )
            return result

    credit_svc = CreditService(db)

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
        # ── [1] Idempotence: already promoted ─────────────────────────────────
        if entry.user_id is not None:
            result.already_linked += 1
            continue

        # ── [2] Status + consent guard ─────────────────────────────────────────
        if entry.status != "ACTIVE":
            result.skipped += 1
            continue
        if not entry.consent_given:
            result.skipped += 1
            continue

        # ── [3] User link-or-create ────────────────────────────────────────────
        if entry.email in existing_users:
            user = existing_users[entry.email]
        else:
            user = User(
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
            db.add(user)
            db.flush()

        entry.user_id = user.id

        # ── [4] License ensure ─────────────────────────────────────────────────
        spec_type = (
            campaign.specialization_type
            if campaign is not None
            else "LFA_FOOTBALL_PLAYER"
        )
        license = _ensure_license(user, spec_type, now, db)

        # ── [5] + [6] Credit grant + unlock (always paired) ────────────────────
        if campaign is not None:
            granted, deducted = _apply_credits(user, campaign, credit_svc)
            result.credits_granted    += granted
            result.unlock_deductions  += deducted

        # ── [7] Baseline onboarding ────────────────────────────────────────────
        if _should_write_baseline(entry, license):
            _write_baseline(entry, license, now)
            result.promoted_with_onboarding += 1
        else:
            result.promoted_without_onboarding += 1

        # ── [8] Audit fields ───────────────────────────────────────────────────
        entry.promoted_at = now
        entry.promoted_by = admin_user.id
        result.promoted += 1

    db.commit()

    logger.info(
        "sponsor_audience_promote_done sponsor_id=%s promoted=%s "
        "(with_onboarding=%s without=%s) already_linked=%s skipped=%s "
        "credits_granted=%s unlock_deductions=%s errors=%s",
        sponsor_id, result.promoted,
        result.promoted_with_onboarding, result.promoted_without_onboarding,
        result.already_linked, result.skipped,
        result.credits_granted, result.unlock_deductions,
        len(result.errors),
    )
    return result
