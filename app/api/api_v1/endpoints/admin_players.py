"""
Admin Batch Player Creation Endpoint

Allows admins to create multiple player accounts in a single API call,
with LFA_FOOTBALL_PLAYER licensing pre-applied.

This endpoint is the only sanctioned path for bulk player provisioning.
Direct DB writes in tests are explicitly forbidden — this endpoint ensures
all side effects (password hashing, licensing, validation) go through
the full application stack.

Chunking: inserts are committed in batches of CHUNK_SIZE (default 100) to
limit lock time, WAL pressure, and rollback scope. Each chunk is its own
atomic transaction; a failure in chunk N does not roll back chunks 0..N-1.

Rate guard: at most MAX_CALLS_PER_WINDOW calls in RATE_WINDOW_SECONDS from
the same admin user. Soft in-process throttle — no Redis required.

Authorization: ADMIN only
"""
import time
import threading
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin_user
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from app.services.player_identity_service import issue_football_player_entitlement
from app.services.player_identity_service import update_identity_profile
from app.services.canonical_policy import (
    CanonicalProgram,
    evaluate_profile_age_policy,
    resolve_program_id,
)
from app.services.program_eligibility_service import record_guardian_consent

import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# ── Chunking configuration ────────────────────────────────────────────────────
CHUNK_SIZE: int = 100  # rows per DB transaction
"""
Inserts are committed in CHUNK_SIZE-row batches.
Rationale: a 512-row single transaction holds row-level locks and accumulates
WAL entries for the full duration of the request.  Chunking caps worst-case
lock time at (time per 100 rows) and limits rollback scope to one chunk.
"""

# ── Soft rate guard (in-process, no Redis) ────────────────────────────────────
RATE_WINDOW_SECONDS: int = 60
MAX_CALLS_PER_WINDOW: int = 5
"""
At most MAX_CALLS_PER_WINDOW batch-create calls per admin user in any rolling
RATE_WINDOW_SECONDS window.  Prevents accidental bulk re-seeding loops that
could saturate the DB write path.  Uses an in-process dict — sufficient for a
single-worker deployment; replace with Redis counter for multi-instance.
"""

_rate_lock = threading.Lock()
# {user_id: [(call_timestamp, player_count), ...]}
_rate_calls: Dict[int, List[Tuple[float, int]]] = defaultdict(list)


def _check_rate_limit(user_id: int, incoming_count: int) -> None:
    """Raise HTTP 429 if the admin exceeds the soft rate guard."""
    now = time.monotonic()
    window_start = now - RATE_WINDOW_SECONDS

    with _rate_lock:
        # Prune entries outside the rolling window
        _rate_calls[user_id] = [
            (ts, n) for ts, n in _rate_calls[user_id] if ts >= window_start
        ]
        recent = _rate_calls[user_id]

        if len(recent) >= MAX_CALLS_PER_WINDOW:
            oldest_ts = recent[0][0]
            retry_after = int(RATE_WINDOW_SECONDS - (now - oldest_ts)) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded: at most {MAX_CALLS_PER_WINDOW} "
                    f"batch-create-players calls per {RATE_WINDOW_SECONDS}s. "
                    f"Retry after {retry_after}s."
                ),
                headers={"Retry-After": str(retry_after)},
            )

        _rate_calls[user_id].append((now, incoming_count))


# ── Schemas ───────────────────────────────────────────────────────────────────

class PlayerCreateEntry(BaseModel):
    email: str = Field(..., description="Player email address (must be unique)")
    password: str = Field(..., min_length=6, description="Plain-text password (hashed server-side)")
    name: str = Field(..., min_length=1, max_length=200, description="Display name")
    date_of_birth: str = Field(
        ...,
        description="Required ISO date string YYYY-MM-DD"
    )
    guardian_name: Optional[str] = Field(
        default=None,
        description="Required guardian consent evidence for players under 18",
    )

    @field_validator("email")
    @classmethod
    def email_must_contain_at(cls, v: str) -> str:
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Invalid email format")
        return v.lower().strip()


class BatchCreatePlayersRequest(BaseModel):
    players: List[PlayerCreateEntry] = Field(
        ...,
        min_length=1,
        max_length=1024,
        description="List of players to create (1–1024 per call)"
    )
    specialization: str = Field(
        default="LFA_FOOTBALL_PLAYER",
        description="License specialization type granted to every player"
    )
    skip_existing: bool = Field(
        default=True,
        description=(
            "If True, existing emails are silently skipped and their IDs included in the response. "
            "If False, any duplicate email causes a 409 error."
        ),
    )


class BatchCreatePlayersResponse(BaseModel):
    success: bool
    created_count: int
    skipped_count: int
    failed_count: int
    chunks_committed: int
    player_ids: List[int]
    elapsed_ms: float
    message: str


# ── Internal helpers ──────────────────────────────────────────────────────────

def _resolve_existing(
    db: Session,
    emails: List[str],
    skip_existing: bool,
) -> Dict[str, int]:
    """
    Query for already-existing users by email.
    Returns {email: user_id}.  Raises 409 if skip_existing=False and duplicates found.
    """
    existing_rows = (
        db.query(User.email, User.id)
        .filter(User.email.in_(emails))
        .all()
    )
    existing: Dict[str, int] = {row.email: row.id for row in existing_rows}

    if not skip_existing and existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Duplicate emails (skip_existing=false): {sorted(existing.keys())}",
        )
    return existing


def _commit_chunk(
    db: Session,
    chunk: List[PlayerCreateEntry],
    existing: Dict[str, int],
    specialization: str,
    now: datetime,
    admin_user_id: int | None = None,
) -> Tuple[List[int], int, int, int]:
    """
    Insert one chunk of players + licenses, commit, return
    (player_ids_in_chunk, created, skipped, failed).
    """
    ids: List[int] = []
    created = skipped = failed = 0

    for entry in chunk:
        if entry.email in existing:
            ids.append(existing[entry.email])
            skipped += 1
            continue

        try:
            dob = datetime.strptime(entry.date_of_birth, "%Y-%m-%d").date()
            profile = evaluate_profile_age_policy(dob, bool(entry.guardian_name))
            if not profile.usable:
                raise ValueError(profile.reason)

            parts = entry.name.split()
            user = User(
                email=entry.email,
                password_hash=get_password_hash(entry.password),
                name=entry.name,
                first_name=parts[0],
                last_name=parts[-1] if len(parts) > 1 else "Test",
                role=UserRole.STUDENT,
                is_active=True,
                date_of_birth=None,
                created_at=now,
            )
            db.add(user)
            db.flush()  # populate user.id before license FK
            if profile.age is not None and profile.age < 18:
                record_guardian_consent(
                    db,
                    user=user,
                    guardian_name=entry.guardian_name,
                    granted_by_user_id=admin_user_id,
                    evidence_reference="ADMIN_BATCH_PLAYER_CREATE",
                )
            update_identity_profile(db, user=user, date_of_birth=dob)

            issue_football_player_entitlement(
                db,
                user=user,
                payment_verified=True,
            )

            # Update lookup so later chunks in the same call don't re-insert
            existing[entry.email] = user.id
            ids.append(user.id)
            created += 1

        except Exception as exc:
            logger.error(f"Failed to stage player {entry.email}: {exc}")
            db.rollback()
            failed += 1

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(f"Chunk commit failed: {exc}")
        # Count every entry in this chunk that was not already skipped as failed
        failed += created
        created = 0
        # Remove ids added in this failed chunk from the return list
        ids = [i for i in ids if i in existing.values() and entry.email in existing]

    return ids, created, skipped, failed


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post(
    "/batch-create-players",
    response_model=BatchCreatePlayersResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Admin: Batch-create licensed player accounts",
    description=(
        "Creates multiple player accounts (STUDENT role) with a pre-activated license. "
        "Inserts are committed in chunks of ~100 rows to limit lock time and WAL pressure. "
        "\n\n**Authorization:** Admin only."
        "\n\n**Idempotency:** With `skip_existing=true` (default), reruns are safe."
        "\n\n**Rate limit:** At most 5 calls per 60 s per admin user."
    ),
)
def batch_create_players(
    request: BatchCreatePlayersRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
) -> BatchCreatePlayersResponse:
    t_start = time.perf_counter()

    resolution = resolve_program_id(request.specialization)
    if (
        not resolution.usable
        or resolution.canonical_program is not CanonicalProgram.LFA_FOOTBALL_PLAYER
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CANONICAL_FOOTBALL_PROGRAM_REQUIRED",
        )

    # ── Rate guard ────────────────────────────────────────────────────────────
    _check_rate_limit(current_user.id, len(request.players))

    # ── Pre-resolve existing emails (single batch query over all emails) ──────
    all_emails = [p.email for p in request.players]
    existing: Dict[str, int] = _resolve_existing(db, all_emails, request.skip_existing)

    now = datetime.now(timezone.utc)
    player_ids: List[int] = []
    total_created = total_skipped = total_failed = 0
    chunks_committed = 0

    # ── Chunked inserts ───────────────────────────────────────────────────────
    players = request.players
    for chunk_start in range(0, len(players), CHUNK_SIZE):
        chunk = players[chunk_start: chunk_start + CHUNK_SIZE]
        ids, created, skipped, failed = _commit_chunk(
            db, chunk, existing, request.specialization, now, current_user.id
        )
        player_ids.extend(ids)
        total_created += created
        total_skipped += skipped
        total_failed += failed
        if created > 0 or skipped > 0:
            chunks_committed += 1
        logger.debug(
            f"batch-create-players chunk [{chunk_start}:{chunk_start + len(chunk)}] "
            f"created={created} skipped={skipped} failed={failed}"
        )

    elapsed_ms = (time.perf_counter() - t_start) * 1000
    total = len(request.players)

    logger.info(
        f"batch-create-players DONE: created={total_created}, skipped={total_skipped}, "
        f"failed={total_failed}, chunks={chunks_committed}, "
        f"elapsed={elapsed_ms:.0f}ms, by={current_user.email}"
    )

    return BatchCreatePlayersResponse(
        success=total_failed == 0,
        created_count=total_created,
        skipped_count=total_skipped,
        failed_count=total_failed,
        chunks_committed=chunks_committed,
        player_ids=player_ids,
        elapsed_ms=round(elapsed_ms, 1),
        message=(
            f"{total_created} players created in {chunks_committed} chunks, "
            f"{total_skipped} skipped (already exist), "
            f"{total_failed} failed. "
            f"Total IDs returned: {len(player_ids)}. "
            f"Elapsed: {elapsed_ms:.0f}ms."
        ),
    )
