"""Sponsor Audience Cleanup Service (P2-E).

Business rules:
  - suppress_entry:      status → SUPPRESSED.  Only for non-DELETED, non-SUPPRESSED entries.
  - soft_delete_entry:   status → DELETED.  Works on any non-DELETED entry.
  - unlink_entry:        user_id / promoted_at / promoted_by → NULL.  Status UNCHANGED.
                         User and UserLicense are NEVER modified or deleted.
  - rollback_import:     status → DELETED for entries where import_log_id = X AND user_id IS NULL.
                         Promoted entries (user_id IS NOT NULL) → skipped, counted in result.
  - Single db.commit() per operation (atomic).
  - No tournament enrollment / ranking / reward logic is touched here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.models.club import CsvImportLog
from app.models.sponsor import SponsorAudienceEntry

if TYPE_CHECKING:
    from app.models.user import User

logger = logging.getLogger(__name__)


@dataclass
class CleanupResult:
    suppressed: int = 0
    deleted: int = 0
    unlinked: int = 0
    skipped: int = 0               # promoted entries skipped by rollback
    already_deleted: int = 0       # entries that were already DELETED before rollback
    errors: list[str] = field(default_factory=list)
    unlinked_user_id: int | None = None   # set by unlink_entry for UI warning


def _load_entry(entry_id: int, sponsor_id: int, db: Session) -> SponsorAudienceEntry | None:
    return (
        db.query(SponsorAudienceEntry)
        .filter(
            SponsorAudienceEntry.id == entry_id,
            SponsorAudienceEntry.sponsor_id == sponsor_id,
        )
        .first()
    )


def suppress_entry(
    entry_id: int,
    sponsor_id: int,
    db: Session,
    admin_user: "User",
) -> CleanupResult:
    """Change entry status to SUPPRESSED."""
    result = CleanupResult()
    entry = _load_entry(entry_id, sponsor_id, db)
    if not entry:
        result.errors.append(f"Entry {entry_id} not found for this sponsor")
        return result
    if entry.status == "DELETED":
        result.errors.append(f"Entry {entry_id} is already DELETED")
        return result
    if entry.status == "SUPPRESSED":
        result.errors.append(f"Entry {entry_id} is already SUPPRESSED")
        return result
    entry.status = "SUPPRESSED"
    db.commit()
    result.suppressed = 1
    logger.info(
        "sponsor_audience_suppress entry=%s sponsor=%s by=%s",
        entry_id, sponsor_id, admin_user.id,
    )
    return result


def soft_delete_entry(
    entry_id: int,
    sponsor_id: int,
    db: Session,
    admin_user: "User",
) -> CleanupResult:
    """Change entry status to DELETED (soft delete, keeps row for audit)."""
    result = CleanupResult()
    entry = _load_entry(entry_id, sponsor_id, db)
    if not entry:
        result.errors.append(f"Entry {entry_id} not found for this sponsor")
        return result
    if entry.status == "DELETED":
        result.errors.append(f"Entry {entry_id} is already DELETED")
        return result
    entry.status = "DELETED"
    db.commit()
    result.deleted = 1
    logger.info(
        "sponsor_audience_delete entry=%s sponsor=%s by=%s",
        entry_id, sponsor_id, admin_user.id,
    )
    return result


def unlink_entry(
    entry_id: int,
    sponsor_id: int,
    db: Session,
    admin_user: "User",
) -> CleanupResult:
    """Clear user_id / promoted_at / promoted_by from entry. Status is NOT changed.

    The linked User and UserLicense are never touched.
    """
    result = CleanupResult()
    entry = _load_entry(entry_id, sponsor_id, db)
    if not entry:
        result.errors.append(f"Entry {entry_id} not found for this sponsor")
        return result
    if entry.user_id is None:
        result.errors.append(f"Entry {entry_id} has no linked User to unlink")
        return result
    result.unlinked_user_id = entry.user_id
    entry.user_id = None
    entry.promoted_at = None
    entry.promoted_by = None
    # Status intentionally unchanged per P2-E decision #2
    db.commit()
    result.unlinked = 1
    logger.info(
        "sponsor_audience_unlink entry=%s was_user=%s sponsor=%s by=%s",
        entry_id, result.unlinked_user_id, sponsor_id, admin_user.id,
    )
    return result


def rollback_import(
    log_id: int,
    sponsor_id: int,
    db: Session,
    admin_user: "User",
) -> CleanupResult:
    """Soft-delete all unpromoted entries from a CSV import.

    Only entries where user_id IS NULL are affected (status → DELETED).
    Promoted entries (user_id IS NOT NULL) are skipped and counted in result.skipped.
    """
    result = CleanupResult()
    log = (
        db.query(CsvImportLog)
        .filter(
            CsvImportLog.id == log_id,
            CsvImportLog.sponsor_id == sponsor_id,
        )
        .first()
    )
    if not log:
        result.errors.append(f"Import log {log_id} not found for this sponsor")
        return result

    entries = (
        db.query(SponsorAudienceEntry)
        .filter(SponsorAudienceEntry.import_log_id == log_id)
        .all()
    )

    for entry in entries:
        if entry.user_id is not None:
            result.skipped += 1
        elif entry.status == "DELETED":
            result.already_deleted += 1
        else:
            entry.status = "DELETED"
            result.deleted += 1

    db.commit()
    logger.info(
        "sponsor_audience_rollback log=%s sponsor=%s deleted=%s skipped=%s by=%s",
        log_id, sponsor_id, result.deleted, result.skipped, admin_user.id,
    )
    return result
