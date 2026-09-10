"""CSV Import Service — parse, validate, and bulk-upsert players from a CSV file.

Row lifecycle:
  • Valid new email    → CREATE User + UserLicense + optional CreditTransaction → "created"
  • Valid existing     → UPDATE name/dob/position, skip credits if idempotency_key exists → "updated"
  • Missing required   → skip row, append to errors → "failed"
  • Invalid format     → skip row, append to errors → "failed"

Chunking: 100 rows per DB transaction; a chunk failure does not roll back earlier chunks.
Idempotency: credits keyed on f"csv-import-{log_id}-{email}".
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.models.user import User, UserRole
from app.models.credit_transaction import CreditTransaction, TransactionType
from app.models.club import CsvImportLog
from app.models.team import Team, TeamMember
from app.core.security import get_password_hash
from app.services.club_service import get_or_create_club
from app.services.tournament.team_service import add_team_member
from app.services.canonical_policy import evaluate_profile_age_policy
from app.services.program_eligibility_service import record_guardian_consent
from app.services.player_identity_service import (
    issue_football_player_entitlement,
    update_identity_profile,
)

if TYPE_CHECKING:
    from app.models.club import Club

import logging

logger = logging.getLogger(__name__)

CHUNK_SIZE = 100

VALID_POSITIONS = {"GK", "DF", "MF", "FW", "CF", "WG", "DM", "AM"}

# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_csv(content: bytes) -> list[dict]:
    """Decode CSV bytes and return list of row dicts (headers normalised to lowercase)."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        rows.append({k.strip().lower(): (v.strip() if v else "") for k, v in row.items()})
    return rows


# ── Validation ────────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_row(row: dict, row_number: int) -> tuple[bool, str]:
    """Return (is_valid, error_reason). Empty string = valid."""
    first = row.get("first_name", "").strip()
    last = row.get("last_name", "").strip()
    email = row.get("email", "").strip().lower()

    if not first:
        return False, f"Row {row_number}: missing first_name"
    if not last:
        return False, f"Row {row_number}: missing last_name"
    if not email:
        return False, f"Row {row_number}: missing email"
    if not _EMAIL_RE.match(email):
        return False, f"Row {row_number}: invalid email '{email}'"

    dob = row.get("date_of_birth", "").strip()
    if not dob:
        return False, f"Row {row_number}: missing date_of_birth"
    try:
        parsed_dob = datetime.strptime(dob, "%Y-%m-%d").date()
    except ValueError:
        return False, f"Row {row_number}: invalid date_of_birth '{dob}' (expected YYYY-MM-DD)"
    profile = evaluate_profile_age_policy(
        parsed_dob, bool(row.get("guardian_name", "").strip())
    )
    if not profile.usable:
        return False, f"Row {row_number}: {profile.reason}"

    position = row.get("position", "").strip().upper()
    if position and position not in VALID_POSITIONS:
        return False, f"Row {row_number}: unknown position '{position}'"

    credits_str = row.get("initial_credits", "").strip()
    if credits_str:
        try:
            c = int(credits_str)
            if c < 0:
                return False, f"Row {row_number}: initial_credits must be >= 0"
        except ValueError:
            return False, f"Row {row_number}: initial_credits is not an integer"

    return True, ""


# ── Import orchestrator ────────────────────────────────────────────────────────

def import_rows(
    db: Session,
    rows: list[dict],
    import_log: CsvImportLog,
    admin_user: User,
    default_club_id: int | None = None,
) -> CsvImportLog:
    """Process rows in CHUNK_SIZE batches. Mutates import_log counters in-place.

    Each chunk is its own atomic transaction committed to DB.
    Returns the updated import_log (not yet committed — caller must commit).
    """
    import_log.total_rows = len(rows)
    import_log.status = "PROCESSING"
    db.flush()

    errors: list[dict] = []

    for chunk_start in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[chunk_start: chunk_start + CHUNK_SIZE]
        try:
            _process_chunk(
                db,
                chunk=chunk,
                row_offset=chunk_start,
                import_log=import_log,
                admin_user=admin_user,
                default_club_id=default_club_id,
                errors=errors,
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("csv_import_chunk_failed chunk=%d error=%s", chunk_start, exc)
            # Count all rows in the chunk as failed
            for i, row in enumerate(chunk):
                row_num = chunk_start + i + 1
                errors.append({"row": row_num, "email": row.get("email", ""), "reason": str(exc)})
            import_log.rows_failed += len(chunk)

    import_log.errors = errors
    import_log.status = "DONE"
    return import_log


def _process_chunk(
    db: Session,
    *,
    chunk: list[dict],
    row_offset: int,
    import_log: CsvImportLog,
    admin_user: User,
    default_club_id: int | None,
    errors: list[dict],
) -> None:
    for i, row in enumerate(chunk):
        row_num = row_offset + i + 1
        valid, reason = validate_row(row, row_num)
        if not valid:
            errors.append({"row": row_num, "email": row.get("email", ""), "reason": reason})
            import_log.rows_failed += 1
            continue

        email = row["email"].strip().lower()
        try:
            user, action = _upsert_user(db, row, admin_user)

            # Resolve / create club (row club_name > default)
            club: Club | None = None
            club_name = row.get("club_name", "").strip()
            if club_name:
                club = get_or_create_club(db, name=club_name, created_by_id=admin_user.id)
            elif default_club_id:
                from app.models.club import Club as ClubModel
                club = db.query(ClubModel).filter(ClubModel.id == default_club_id).first()

            # Resolve / create team
            team_name = row.get("team_name", "").strip()
            if team_name and club:
                age_group = row.get("age_group", "").strip() or None
                team = _upsert_team(db, club=club, team_name=team_name, age_group_label=age_group)
                _assign_user_to_team(db, user=user, team=team)

            # Grant initial credits (idempotent)
            credits_str = row.get("initial_credits", "").strip()
            initial_credits = int(credits_str) if credits_str else 0
            if initial_credits > 0:
                _maybe_grant_credits(
                    db,
                    user=user,
                    credits=initial_credits,
                    import_log_id=import_log.id,
                    admin_user=admin_user,
                )

            db.flush()

            if action == "created":
                import_log.rows_created += 1
            elif action == "updated":
                import_log.rows_updated += 1
            else:
                import_log.rows_skipped += 1

        except Exception as exc:
            logger.warning("csv_import_row_failed row=%d email=%s error=%s", row_num, email, exc)
            errors.append({"row": row_num, "email": email, "reason": str(exc)})
            import_log.rows_failed += 1


# ── Row-level helpers ─────────────────────────────────────────────────────────

def _upsert_user(db: Session, row: dict, admin_user: User) -> tuple[User, str]:
    """Create or update a User + UserLicense. Returns (user, action)."""
    email = row["email"].strip().lower()
    first = row["first_name"].strip()
    last = row["last_name"].strip()
    full_name = f"{first} {last}"
    dob_str = row.get("date_of_birth", "").strip()
    position = row.get("position", "").strip().upper() or None

    dob: datetime | None = None
    if dob_str:
        dob = datetime.strptime(dob_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        # UPDATE name / dob / position (non-destructive)
        existing.name = full_name
        existing.first_name = first
        existing.last_name = last
        if dob:
            update_identity_profile(db, user=existing, date_of_birth=dob)
        if position:
            existing.position = position
        db.flush()
        return existing, "updated"

    # CREATE new user
    password = uuid.uuid4().hex  # auto-generated; admin can reset
    user = User(
        email=email,
        name=full_name,
        first_name=first,
        last_name=last,
        password_hash=get_password_hash(password),
        role=UserRole.STUDENT,
        is_active=True,
        onboarding_completed=False,
        payment_verified=False,
        date_of_birth=None,
        position=position,
        created_by=admin_user.id,
    )
    db.add(user)
    db.flush()
    profile = evaluate_profile_age_policy(dob, bool(row.get("guardian_name", "").strip()))
    if profile.age is not None and profile.age < 18:
        record_guardian_consent(
            db,
            user=user,
            guardian_name=row["guardian_name"].strip(),
            granted_by_user_id=admin_user.id,
            evidence_reference="CSV_PLAYER_IMPORT",
        )
    update_identity_profile(db, user=user, date_of_birth=dob)

    issue_football_player_entitlement(
        db,
        user=user,
        payment_verified=False,
    )

    return user, "created"


def _upsert_team(db: Session, *, club: "Club", team_name: str, age_group_label: str | None) -> Team:
    """Return existing team or create a new one (club + team_name unique)."""
    existing = (
        db.query(Team)
        .filter(Team.club_id == club.id, Team.name == team_name)
        .first()
    )
    if existing:
        return existing

    from app.services.tournament.team_service import create_team

    # Need a placeholder captain — will be first member added; for now create team without captain
    code_base = re.sub(r"[^A-Za-z0-9]+", "-", team_name.strip().upper())[:12]
    code_candidate = code_base
    suffix = 0
    while db.query(Team).filter(Team.code == code_candidate).first():
        suffix += 1
        code_candidate = f"{code_base}-{suffix:02d}"

    team = Team(
        name=team_name,
        code=code_candidate,
        captain_user_id=None,
        club_id=club.id,
        age_group_label=age_group_label,
        is_active=True,
    )
    db.add(team)
    db.flush()
    return team


def _assign_user_to_team(db: Session, *, user: User, team: Team) -> None:
    """Add user to team if not already an active member. First member becomes captain."""
    from app.models.team import TeamMember

    existing_member = (
        db.query(TeamMember)
        .filter(TeamMember.team_id == team.id, TeamMember.user_id == user.id, TeamMember.is_active.is_(True))
        .first()
    )
    if existing_member:
        return

    is_first = not db.query(TeamMember).filter(
        TeamMember.team_id == team.id, TeamMember.is_active.is_(True)
    ).first()

    role = "CAPTAIN" if is_first else "PLAYER"
    member = TeamMember(team_id=team.id, user_id=user.id, role=role, is_active=True)
    db.add(member)

    # Set captain on team if first member
    if is_first:
        team.captain_user_id = user.id

    db.flush()


def _maybe_grant_credits(
    db: Session,
    *,
    user: User,
    credits: int,
    import_log_id: int,
    admin_user: User,
) -> None:
    """Grant initial credits to user (idempotent per user — skips if any csv-initial grant exists)."""
    idem_key = f"csv-initial-credits-{user.id}"
    existing_tx = (
        db.query(CreditTransaction)
        .filter(CreditTransaction.idempotency_key == idem_key)
        .first()
    )
    if existing_tx:
        return  # Already granted in a previous import

    new_balance = (user.credit_balance or 0) + credits
    user.credit_balance = new_balance
    if not user.credit_purchased:
        user.credit_purchased = 0
    user.credit_purchased += credits

    db.add(
        CreditTransaction(
            user_id=user.id,
            transaction_type=TransactionType.ADMIN_ADJUSTMENT.value,
            amount=credits,
            balance_after=new_balance,
            description=f"Initial credits from CSV import (log #{import_log_id})",
            idempotency_key=idem_key,
            performed_by_user_id=admin_user.id,
        )
    )
    db.flush()
