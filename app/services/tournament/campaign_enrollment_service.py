"""
Campaign enrollment service — PROMOTION_EVENT only.

Bulk-enrolls sponsor campaign audience entries as SemesterEnrollment rows.
This is the bridge between promote_entries() and the tournament lifecycle:

  CSV import → promote → bulk_enroll_from_campaign → Lock Audience → Check-in → Start

No credit deduction. enrollment_cost is always 0 for PROMOTION_EVENT.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TypedDict

from sqlalchemy.orm import Session

from app.models.license import UserLicense
from app.models.semester import Semester, SemesterCategory
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.sponsor import SponsorAudienceEntry
from app.models.user import User

# Statuses in which bulk-enroll is permitted.
# ENROLLMENT_OPEN is included to support recovery of tournaments that entered it
# via legacy data or direct API (PROMOTION_EVENT should use DRAFT → ENROLLMENT_CLOSED,
# but ENROLLMENT_OPEN is treated as equivalent for this operation).
# CHECK_IN_OPEN and later are frozen: participant list is locked for session generation.
_ALLOWED_STATUSES = {"DRAFT", "ENROLLMENT_OPEN", "ENROLLMENT_CLOSED"}


class _SkippedEntry(TypedDict):
    user_id: int
    reason: str


class BulkEnrollResult(TypedDict):
    enrolled_count: int
    skipped_count: int
    enrolled: list[int]
    skipped: list[_SkippedEntry]


def bulk_enroll_from_campaign(
    db: Session,
    tournament_id: int,
    admin_user_id: int,
) -> BulkEnrollResult:
    """Enroll all eligible campaign audience entries for a PROMOTION_EVENT.

    Eligibility (per entry):
      - SponsorAudienceEntry.sponsor_id  == tournament.organizer_sponsor_id
      - SponsorAudienceEntry.campaign_id == tournament.organizer_campaign_id
      - status == "ACTIVE"
      - consent_given == True
      - user_id IS NOT NULL  (entry has been promoted)
      - User.is_active == True
      - Active LFA_FOOTBALL_PLAYER UserLicense exists
      - No existing active SemesterEnrollment for this tournament

    Inactive SemesterEnrollment (previously unenrolled, same user+semester+license):
      Re-activated rather than inserting a new row — avoids UniqueConstraint violation
      on (user_id, semester_id, user_license_id) and preserves audit history.

    Idempotent: calling multiple times produces the same final state.
    No commit is performed — caller owns the transaction.
    """
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not tournament:
        raise ValueError(f"Tournament {tournament_id} not found")

    if tournament.semester_category != SemesterCategory.PROMOTION_EVENT:
        raise ValueError(
            f"bulk_enroll_from_campaign is only available for PROMOTION_EVENT tournaments "
            f"(got {tournament.semester_category})"
        )

    effective_status = tournament.tournament_status or "DRAFT"
    if effective_status not in _ALLOWED_STATUSES:
        raise ValueError(
            f"Cannot bulk-enroll: tournament status is '{tournament.tournament_status}'. "
            f"Bulk enrollment is only allowed in DRAFT, ENROLLMENT_OPEN, or ENROLLMENT_CLOSED. "
            f"CHECK_IN_OPEN and later statuses are frozen."
        )

    if not tournament.organizer_sponsor_id or not tournament.organizer_campaign_id:
        raise ValueError(
            "Cannot bulk-enroll: tournament must have both organizer_sponsor_id and "
            "organizer_campaign_id set."
        )

    # Fetch eligible audience entries — sponsor+campaign filter prevents cross-leakage.
    entries = (
        db.query(SponsorAudienceEntry)
        .filter(
            SponsorAudienceEntry.sponsor_id == tournament.organizer_sponsor_id,
            SponsorAudienceEntry.campaign_id == tournament.organizer_campaign_id,
            SponsorAudienceEntry.status == "ACTIVE",
            SponsorAudienceEntry.consent_given == True,
            SponsorAudienceEntry.user_id.isnot(None),
        )
        .all()
    )

    enrolled: list[int] = []
    skipped: list[_SkippedEntry] = []
    now = datetime.now(timezone.utc)

    for entry in entries:
        user_id = entry.user_id

        # User must be active.
        user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
        if not user:
            skipped.append({"user_id": user_id, "reason": "user inactive or not found"})
            continue

        # Active LFA_FOOTBALL_PLAYER license required (created by promote_entries).
        license_row = (
            db.query(UserLicense)
            .filter(
                UserLicense.user_id == user_id,
                UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
                UserLicense.is_active == True,
            )
            .first()
        )
        if not license_row:
            skipped.append({"user_id": user_id, "reason": "no active LFA_FOOTBALL_PLAYER license"})
            continue

        # Already enrolled and active — idempotent skip.
        active_enrollment = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.semester_id == tournament_id,
                SemesterEnrollment.user_id == user_id,
                SemesterEnrollment.is_active == True,
            )
            .first()
        )
        if active_enrollment:
            skipped.append({"user_id": user_id, "reason": "already enrolled"})
            continue

        # Inactive enrollment with the same (user, semester, license) triple:
        # re-activate to avoid UniqueConstraint violation and preserve history.
        inactive_enrollment = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.semester_id == tournament_id,
                SemesterEnrollment.user_id == user_id,
                SemesterEnrollment.user_license_id == license_row.id,
                SemesterEnrollment.is_active == False,
            )
            .first()
        )
        if inactive_enrollment:
            inactive_enrollment.is_active = True
            inactive_enrollment.request_status = EnrollmentStatus.APPROVED
            inactive_enrollment.approved_at = now
            inactive_enrollment.approved_by = admin_user_id
            inactive_enrollment.enrolled_at = now
            inactive_enrollment.payment_verified = True
            db.flush()
            enrolled.append(user_id)
            continue

        # Fresh enrollment — enrollment_cost = 0, no credit deduction.
        new_enrollment = SemesterEnrollment(
            user_id=user_id,
            semester_id=tournament_id,
            user_license_id=license_row.id,
            request_status=EnrollmentStatus.APPROVED,
            is_active=True,
            payment_verified=True,
            approved_at=now,
            approved_by=admin_user_id,
            enrolled_at=now,
            requested_at=now,
        )
        db.add(new_enrollment)
        db.flush()
        enrolled.append(user_id)

    return BulkEnrollResult(
        enrolled_count=len(enrolled),
        skipped_count=len(skipped),
        enrolled=enrolled,
        skipped=skipped,
    )
