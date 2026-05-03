"""Sponsor Audience — Promote to User service.

Business rules:
  - Only ACTIVE entries with consent_given=True may be promoted.
  - SUPPRESSED / UNSUBSCRIBED / DELETED → skipped, no User created.
  - If entry.user_id is already set → full no-op (idempotent).
    promoted_at / promoted_by are never overwritten.
  - Email matches existing User → user_id linked only. User profile NOT modified.
  - No existing User → User + UserLicense created (role=STUDENT, random password).
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

if TYPE_CHECKING:
    pass

import logging

logger = logging.getLogger(__name__)


@dataclass
class PromoteResult:
    promoted: int = 0              # new User created OR existing User linked for first time
    already_linked: int = 0        # entry.user_id was already set → full no-op
    skipped: int = 0               # status != ACTIVE or consent_given == False
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.promoted + self.already_linked + self.skipped


def promote_entries(
    entry_ids: list[int],
    sponsor_id: int,
    db: Session,
    admin_user: User,
) -> PromoteResult:
    """Promote selected SponsorAudienceEntry rows to Users.

    Atomically processes the batch; single db.commit() at end.
    Any unhandled exception propagates and rolls back everything.
    """
    result = PromoteResult()
    if not entry_ids:
        return result

    now = datetime.now(timezone.utc)

    # Load all requested entries belonging to this sponsor in one query
    entries = (
        db.query(SponsorAudienceEntry)
        .filter(
            SponsorAudienceEntry.id.in_(entry_ids),
            SponsorAudienceEntry.sponsor_id == sponsor_id,
        )
        .all()
    )

    # Report IDs that were not found or belong to another sponsor
    found_ids = {e.id for e in entries}
    for missing in set(entry_ids) - found_ids:
        result.errors.append(f"Entry {missing}: not found for this sponsor")

    # Collect emails that need User lookup (only candidates for promotion)
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
        if entry.email in existing_users:
            # Email matches existing User — link only, no profile modification
            entry.user_id = existing_users[entry.email].id
        else:
            new_user = User(
                email=entry.email,
                name=f"{entry.first_name} {entry.last_name}",
                first_name=entry.first_name,
                last_name=entry.last_name,
                password_hash=get_password_hash(uuid.uuid4().hex),
                role=UserRole.STUDENT,
                is_active=True,
                onboarding_completed=False,
                payment_verified=False,
                created_by=admin_user.id,
            )
            db.add(new_user)
            db.flush()
            db.add(UserLicense(
                user_id=new_user.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                current_level=1,
                max_achieved_level=1,
                started_at=now,
                is_active=True,
            ))
            db.flush()
            entry.user_id = new_user.id

        # ── Set audit fields (only on first promotion) ────────────────────────
        entry.promoted_at = now
        entry.promoted_by = admin_user.id
        result.promoted += 1

    db.commit()

    logger.info(
        "sponsor_audience_promote_done sponsor_id=%s promoted=%s "
        "already_linked=%s skipped=%s errors=%s",
        sponsor_id, result.promoted, result.already_linked,
        result.skipped, len(result.errors),
    )
    return result
