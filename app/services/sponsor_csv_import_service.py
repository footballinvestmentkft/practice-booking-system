"""Sponsor Audience CSV Import Service.

Business rules:
  - Sponsor import != Club import.  No Club, Team, TeamMember, or TournamentTeamEnrollment
    is ever created or modified by this service.
  - Default result: SponsorAudienceEntry (prospect record).  No automatic User creation.
  - Email match to existing User: user_id linked (read-only).  User profile NOT modified.
  - Apply is a single atomic DB transaction.  Preview writes nothing.
  - Consent downgrade via re-import is prevented; consent can only be revoked by explicit
    admin/user action (status → UNSUBSCRIBED / DELETED).
  - DOB is authoritative for age_category.  Explicit CSV value stored in age_raw for audit.

Row lifecycle:
  Valid new (campaign_id, email)  → CREATE SponsorAudienceEntry  → "created"
  Valid existing (campaign_id, email) → UPDATE non-null fields   → "updated"
  Missing required field          → skip row, append error        → "failed"
  Invalid format                  → skip row, append error        → "failed"
"""
from __future__ import annotations

import base64
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.models.club import CsvImportLog
from app.models.sponsor import SponsorAudienceEntry
from app.models.user import User

if TYPE_CHECKING:
    from app.models.sponsor import Sponsor, SponsorCampaign

import logging

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_CSV_BYTES = 1 * 1024 * 1024  # 1 MB hard limit

FORBIDDEN_COLUMNS = frozenset({"club_name", "team_name", "team_code", "team_role", "captain"})

VALID_POSITIONS = frozenset({"STRIKER", "MIDFIELDER", "DEFENDER", "GOALKEEPER"})

VALID_STATUSES = frozenset({"ACTIVE", "SUPPRESSED", "UNSUBSCRIBED", "DELETED"})

VALID_AGE_CATEGORIES = frozenset({"PRE", "YOUTH", "AMATEUR", "PRO"})

# Maps U-labels and canonical values to canonical age category
AGE_LABEL_MAP: dict[str, str] = {
    "U6": "PRE", "U7": "PRE", "U8": "PRE", "U9": "PRE",
    "U10": "PRE", "U11": "PRE", "U12": "PRE",
    "U13": "YOUTH", "U14": "YOUTH", "U15": "YOUTH",
    "U16": "YOUTH", "U17": "YOUTH", "U18": "YOUTH",
    "U19": "AMATEUR", "U20": "AMATEUR", "U21": "AMATEUR",
    "U22": "AMATEUR", "U23": "AMATEUR",
    "ADULT": "AMATEUR", "SENIOR": "AMATEUR",
    "PRE": "PRE", "YOUTH": "YOUTH", "AMATEUR": "AMATEUR", "PRO": "PRO",
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ── Data transfer objects ──────────────────────────────────────────────────────

@dataclass
class PreviewRow:
    row_num: int
    email: str
    first_name: str
    last_name: str
    age_category: str | None
    consent_given: bool
    action: str          # "create" | "update" | "fail"
    reason: str          # non-empty only for "fail"
    warnings: list[str] = field(default_factory=list)


@dataclass
class PreviewResult:
    filename: str
    total_rows: int
    valid_rows: int
    failed_rows: int
    rows: list[PreviewRow]
    global_warnings: list[str]           # e.g. forbidden columns detected
    age_breakdown: dict[str, int]        # {"PRE": 3, "YOUTH": 5, "UNKNOWN": 2}
    consent_breakdown: dict[str, int]    # {"contactable": 6, "suppressed": 4}
    csv_b64: str                         # base64-encoded original CSV for apply step


# ── CSV parsing ────────────────────────────────────────────────────────────────

def _parse_csv(content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return [
        {k.strip().lower(): (v.strip() if v else "") for k, v in row.items()}
        for row in reader
    ]


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_row(row: dict, row_num: int) -> tuple[bool, str]:
    """Return (is_valid, error_reason). Empty reason = valid."""
    first = row.get("first_name", "").strip()
    last  = row.get("last_name",  "").strip()
    email = row.get("email",      "").strip().lower()

    if not first:
        return False, f"Row {row_num}: missing first_name"
    if not last:
        return False, f"Row {row_num}: missing last_name"
    if not email:
        return False, f"Row {row_num}: missing email"
    if not _EMAIL_RE.match(email):
        return False, f"Row {row_num}: invalid email '{email}'"
    return True, ""


# ── Age derivation ─────────────────────────────────────────────────────────────

def _derive_age_category(
    dob_str: str,
    raw_age: str,
) -> tuple[str | None, str | None, list[str]]:
    """Return (canonical_age_category, age_raw_to_store, warnings).

    DOB is authoritative when both DOB and age_category are provided.
    """
    warnings: list[str] = []
    age_raw = raw_age.strip() or None

    canonical_from_dob: str | None = None
    canonical_from_csv: str | None = None

    # Derive from DOB
    dob_clean = dob_str.strip()
    if dob_clean:
        try:
            dob = datetime.strptime(dob_clean, "%Y-%m-%d").date()
            today = date.today()
            age_years = (today - dob).days // 365
            if age_years <= 12:
                canonical_from_dob = "PRE"
            elif age_years <= 18:
                canonical_from_dob = "YOUTH"
            elif age_years <= 35:
                canonical_from_dob = "AMATEUR"
            else:
                canonical_from_dob = "PRO"
        except ValueError:
            warnings.append(f"invalid date_of_birth '{dob_clean}' (expected YYYY-MM-DD) — ignored")

    # Derive from explicit age_category
    raw_upper = raw_age.strip().upper()
    if raw_upper:
        canonical_from_csv = AGE_LABEL_MAP.get(raw_upper)
        if canonical_from_csv is None:
            warnings.append(
                f"unknown age_category '{raw_age}' — not mapped to canonical value, stored as NULL"
            )

    # Conflict check: DOB wins
    if canonical_from_dob and canonical_from_csv and canonical_from_dob != canonical_from_csv:
        warnings.append(
            f"age_category conflict: DOB derives '{canonical_from_dob}' "
            f"but CSV says '{raw_age}' ({canonical_from_csv}) — DOB used"
        )

    canonical = canonical_from_dob if canonical_from_dob else canonical_from_csv
    return canonical, age_raw, warnings


# ── Position / foot dominance ────────────────────────────────────────────────

def _parse_position(raw: str) -> tuple[str | None, list[str]]:
    """Return (canonical_position | None, warnings).

    Only canonical values stored.  Invalid or empty → None + warning.
    """
    warnings: list[str] = []
    val = raw.strip().upper()
    if not val:
        warnings.append(
            "position missing — entry will not be tournament-ready after promote"
        )
        return None, warnings
    if val in VALID_POSITIONS:
        return val, warnings
    warnings.append(
        f"unknown position '{raw.strip()}' — expected STRIKER/MIDFIELDER/DEFENDER/GOALKEEPER, "
        f"stored as NULL; entry will not be tournament-ready after promote"
    )
    return None, warnings


def _parse_foot_dominance(raw: str) -> tuple[int | None, list[str]]:
    """Return (int 0–100 | None, warnings).  Invalid → None + warning."""
    warnings: list[str] = []
    val = raw.strip()
    if not val:
        return None, warnings
    try:
        num = int(val)
        if 0 <= num <= 100:
            return num, warnings
        warnings.append(
            f"foot_dominance '{val}' out of range (0–100) — ignored"
        )
    except ValueError:
        warnings.append(
            f"foot_dominance '{val}' is not an integer — ignored"
        )
    return None, warnings


# ── Consent / status ──────────────────────────────────────────────────────────

def _parse_consent(row: dict) -> bool:
    val = row.get("consent_given", "").strip().lower()
    return val in {"1", "true", "yes"}


def _status_for_consent(consent: bool) -> str:
    return "ACTIVE" if consent else "SUPPRESSED"


# ── Preview (no DB writes) ────────────────────────────────────────────────────

def preview_rows(
    content: bytes,
    campaign_id: int,
    db: Session,
    filename: str = "upload.csv",
) -> PreviewResult:
    """Parse and validate CSV.  Returns PreviewResult.  Writes nothing to DB."""
    rows = _parse_csv(content)

    # Detect forbidden columns
    global_warnings: list[str] = []
    if rows:
        found_forbidden = FORBIDDEN_COLUMNS & set(rows[0].keys())
        if found_forbidden:
            global_warnings.append(
                f"The following columns are not supported in sponsor audience import "
                f"and were ignored: {', '.join(sorted(found_forbidden))}"
            )

    preview_rows_list: list[PreviewRow] = []
    age_breakdown: dict[str, int] = {}
    consent_breakdown = {"contactable": 0, "suppressed": 0}

    for i, row in enumerate(rows):
        row_num = i + 1
        valid, reason = _validate_row(row, row_num)
        if not valid:
            preview_rows_list.append(PreviewRow(
                row_num=row_num, email=row.get("email", ""),
                first_name=row.get("first_name", ""), last_name=row.get("last_name", ""),
                age_category=None, consent_given=False,
                action="fail", reason=reason,
            ))
            continue

        email = row["email"].strip().lower()
        consent = _parse_consent(row)
        canonical, _, row_warnings = _derive_age_category(
            row.get("date_of_birth", ""),
            row.get("age_category", ""),
        )

        # PRE with no parent_email → warning
        if canonical == "PRE" and not row.get("parent_email", "").strip():
            row_warnings.append(
                "age_category is PRE (under-13) but parent_email is missing — "
                "parental contact strongly recommended"
            )

        # DOB missing → not tournament-ready after promote
        if not row.get("date_of_birth", "").strip():
            row_warnings.append(
                "date_of_birth missing — entry will not be tournament-ready after promote"
            )

        # Position / foot dominance
        _, pos_warnings = _parse_position(row.get("position", ""))
        row_warnings.extend(pos_warnings)
        _, fd_warnings = _parse_foot_dominance(row.get("foot_dominance", ""))
        row_warnings.extend(fd_warnings)

        # Determine create vs update (scoped to campaign)
        existing = (
            db.query(SponsorAudienceEntry)
            .filter(
                SponsorAudienceEntry.campaign_id == campaign_id,
                SponsorAudienceEntry.email == email,
            )
            .first()
        )
        action = "update" if existing else "create"

        # Consent downgrade warning in preview
        if existing and existing.consent_given and not consent:
            row_warnings.append(
                f"consent downgrade would be prevented — existing consent=True kept"
            )

        preview_rows_list.append(PreviewRow(
            row_num=row_num, email=email,
            first_name=row["first_name"].strip(),
            last_name=row["last_name"].strip(),
            age_category=canonical, consent_given=consent,
            action=action, reason="", warnings=row_warnings,
        ))

        age_key = canonical or "UNKNOWN"
        age_breakdown[age_key] = age_breakdown.get(age_key, 0) + 1
        if consent:
            consent_breakdown["contactable"] += 1
        else:
            consent_breakdown["suppressed"] += 1

    valid_count  = sum(1 for r in preview_rows_list if r.action != "fail")
    failed_count = sum(1 for r in preview_rows_list if r.action == "fail")

    return PreviewResult(
        filename=filename,
        total_rows=len(rows),
        valid_rows=valid_count,
        failed_rows=failed_count,
        rows=preview_rows_list,
        global_warnings=global_warnings,
        age_breakdown=age_breakdown,
        consent_breakdown=consent_breakdown,
        csv_b64=base64.b64encode(content).decode(),
    )


# ── Apply (single atomic transaction) ────────────────────────────────────────

def apply_import(
    content: bytes,
    sponsor: "Sponsor",
    db: Session,
    admin_user: User,
    *,
    campaign_id: int,
    filename: str = "upload.csv",
) -> CsvImportLog:
    """Process all valid rows in a single DB transaction.

    campaign_id is required — raises ValueError if not provided.
    Raises on unhandled DB error — caller must not catch silently.
    Validation failures (missing fields, bad email) are recorded in log.errors
    and do NOT abort the transaction.
    """
    if campaign_id is None:
        raise ValueError(
            "apply_import requires campaign_id.  "
            "Create a SponsorCampaign first, then pass its id."
        )

    rows = _parse_csv(content)

    log = CsvImportLog(
        sponsor_id=sponsor.id,
        campaign_id=campaign_id,
        club_id=None,
        uploaded_by=admin_user.id,
        filename=filename,
        total_rows=len(rows),
        rows_created=0,
        rows_updated=0,
        rows_skipped=0,
        rows_failed=0,
        status="PROCESSING",
        errors=[],
    )
    db.add(log)
    db.flush()  # get log.id before referencing in entries

    errors: list[dict] = []

    for i, row in enumerate(rows):
        row_num = i + 1
        valid, reason = _validate_row(row, row_num)
        if not valid:
            errors.append({"row": row_num, "email": row.get("email", ""), "reason": reason, "type": "error"})
            log.rows_failed += 1
            continue

        email = row["email"].strip().lower()
        consent = _parse_consent(row)
        canonical, age_raw, row_warnings = _derive_age_category(
            row.get("date_of_birth", ""),
            row.get("age_category", ""),
        )

        position, pos_warnings = _parse_position(row.get("position", ""))
        row_warnings.extend(pos_warnings)
        foot_dominance, fd_warnings = _parse_foot_dominance(row.get("foot_dominance", ""))
        row_warnings.extend(fd_warnings)

        if not row.get("date_of_birth", "").strip():
            row_warnings.append(
                "date_of_birth missing — entry will not be tournament-ready after promote"
            )

        for w in row_warnings:
            errors.append({"row": row_num, "email": email, "reason": w, "type": "warning"})

        entry, action = _upsert_entry(
            db, row, email, canonical, age_raw, consent,
            sponsor, campaign_id, log.id, admin_user.id,
            position=position, foot_dominance=foot_dominance,
        )

        if action == "created":
            log.rows_created += 1
        else:
            log.rows_updated += 1

        db.flush()

    log.errors = errors
    log.status = "DONE"
    db.commit()  # single atomic commit — any unhandled exception above rolls back everything

    logger.info(
        "sponsor_audience_import_done sponsor=%s campaign=%s created=%d updated=%d failed=%d",
        sponsor.name, campaign_id, log.rows_created, log.rows_updated, log.rows_failed,
    )
    return log


# ── Row-level upsert ──────────────────────────────────────────────────────────

def _upsert_entry(
    db: Session,
    row: dict,
    email: str,
    canonical_age: str | None,
    age_raw: str | None,
    consent: bool,
    sponsor: "Sponsor",
    campaign_id: int,
    log_id: int,
    admin_id: int,
    *,
    position: str | None = None,
    foot_dominance: int | None = None,
) -> tuple[SponsorAudienceEntry, str]:
    """Create or update a SponsorAudienceEntry. Returns (entry, action).

    Upsert key: (campaign_id, email) — the same email can appear in
    multiple campaigns for the same sponsor.
    """
    now = datetime.now(timezone.utc)

    existing = (
        db.query(SponsorAudienceEntry)
        .filter(
            SponsorAudienceEntry.campaign_id == campaign_id,
            SponsorAudienceEntry.email == email,
        )
        .first()
    )

    # Consent downgrade protection
    effective_consent = consent
    if existing and existing.consent_given and not consent:
        effective_consent = True  # keep existing True

    new_status = _status_for_consent(effective_consent)

    # Don't overwrite UNSUBSCRIBED/DELETED with ACTIVE/SUPPRESSED from import
    if existing and existing.status in ("UNSUBSCRIBED", "DELETED"):
        new_status = existing.status  # preserve — import cannot restore

    # Email match to existing User (read-only link; User profile NOT modified)
    user_id: int | None = None
    if existing:
        user_id = existing.user_id
    else:
        matched_user = db.query(User).filter(User.email == email).first()
        if matched_user:
            user_id = matched_user.id

    dob_str = row.get("date_of_birth", "").strip()
    dob: date | None = None
    if dob_str:
        try:
            dob = datetime.strptime(dob_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    if existing is None:
        entry = SponsorAudienceEntry(
            sponsor_id=sponsor.id,
            campaign_id=campaign_id,
            import_log_id=log_id,
            user_id=user_id,
            first_name=row["first_name"].strip(),
            last_name=row["last_name"].strip(),
            email=email,
            phone=row.get("phone", "").strip() or None,
            date_of_birth=dob,
            age_category=canonical_age,
            age_raw=age_raw,
            parent_email=row.get("parent_email", "").strip() or None,
            consent_given=effective_consent,
            consent_source=row.get("consent_source", "").strip() or None,
            campaign_source=row.get("campaign_source", "").strip() or None,
            target_segment=row.get("target_segment", "").strip() or None,
            notes=row.get("notes", "").strip() or None,
            status=new_status,
            imported_by=admin_id,
            position=position,
            foot_dominance=foot_dominance,
        )
        db.add(entry)
        return entry, "created"

    # Update non-null fields (never overwrite with empty string)
    def _set(attr: str, val: object) -> None:
        if val is not None and val != "":
            setattr(existing, attr, val)

    _set("first_name",    row["first_name"].strip())
    _set("last_name",     row["last_name"].strip())
    _set("phone",         row.get("phone", "").strip() or None)
    _set("date_of_birth", dob)
    _set("age_category",  canonical_age)
    _set("age_raw",       age_raw)
    _set("parent_email",  row.get("parent_email", "").strip() or None)
    _set("consent_source",  row.get("consent_source",  "").strip() or None)
    _set("campaign_source", row.get("campaign_source", "").strip() or None)
    _set("target_segment",  row.get("target_segment",  "").strip() or None)
    _set("notes",           row.get("notes",           "").strip() or None)
    _set("position",        position)
    _set("foot_dominance",  foot_dominance)

    existing.consent_given    = effective_consent
    existing.status           = new_status
    existing.import_log_id    = log_id
    existing.last_imported_at = now
    if user_id and not existing.user_id:
        existing.user_id = user_id

    return existing, "updated"
