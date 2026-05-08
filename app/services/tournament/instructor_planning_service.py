"""
Instructor Planning Service

Two-phase instructor management for tournament events:

  Phase 1 — Planning (before event):
    Admin builds a roster of MASTER + FIELD instructor slots.
    Each slot starts in PLANNED status.

  Phase 2 — Reality (game day):
    Master self-checks-in.
    Master marks Field instructors present/absent.
    Admin marks Master absent (event blocked until resolved).

Authority chain:
  Admin
    └─ manages MASTER slot (add/remove/mark absent)
        └─ Master
              ├─ self check-in
              └─ manages FIELD slots (mark checkin/absent)
                  └─ Field instructor
                        └─ manages student attendance (via Attendance model)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.models.user import User, UserRole
from app.models.semester import Semester
from app.models.pitch import Pitch
from app.models.session import Session as SessionModel
from app.models.tournament_instructor_slot import (
    TournamentInstructorSlot,
    SlotRole,
    SlotStatus,
)
from app.models.tournament_configuration import TournamentConfiguration


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_tournament_or_404(db: Session, semester_id: int) -> Semester:
    t = db.query(Semester).filter(Semester.id == semester_id).first()
    if not t:
        raise HTTPException(status_code=404, detail=f"Tournament {semester_id} not found")
    return t


def _get_slot_or_404(db: Session, slot_id: int) -> TournamentInstructorSlot:
    slot = db.query(TournamentInstructorSlot).filter(
        TournamentInstructorSlot.id == slot_id
    ).first()
    if not slot:
        raise HTTPException(status_code=404, detail=f"Instructor slot {slot_id} not found")
    return slot


def _get_master_slot(db: Session, semester_id: int) -> Optional[TournamentInstructorSlot]:
    return db.query(TournamentInstructorSlot).filter(
        TournamentInstructorSlot.semester_id == semester_id,
        TournamentInstructorSlot.role == SlotRole.MASTER.value,
    ).first()


def _is_admin(user: User) -> bool:
    return user.role == UserRole.ADMIN


# ─────────────────────────────────────────────────────────────────────────────
# Roster management
# ─────────────────────────────────────────────────────────────────────────────

def add_slot(
    db: Session,
    semester_id: int,
    instructor_id: int,
    role: str,
    pitch_id: Optional[int],
    assigned_by_id: int,
    notes: Optional[str] = None,
) -> TournamentInstructorSlot:
    """Add an instructor to the tournament roster."""
    from app.services.tournament.instructor_eligibility_service import (
        is_eligible_master_instructor,
        is_eligible_field_instructor,
        resolve_tournament_age_groups,
    )

    tournament = _get_tournament_or_404(db, semester_id)

    # Validate instructor exists
    instructor = db.query(User).filter(User.id == instructor_id).first()
    if not instructor:
        raise HTTPException(status_code=404, detail=f"User {instructor_id} not found")

    # Eligibility check — must happen before role business rules
    age_groups = resolve_tournament_age_groups(tournament)
    if role == SlotRole.MASTER.value:
        eligible, reason = is_eligible_master_instructor(db, instructor_id, age_groups)
        if not eligible:
            raise HTTPException(
                status_code=400,
                detail=f"Instructor not eligible as master: {reason}",
            )
    elif role == SlotRole.FIELD.value:
        eligible, reason = is_eligible_field_instructor(db, instructor_id, age_groups)
        if not eligible:
            raise HTTPException(
                status_code=400,
                detail=f"Instructor not eligible as field instructor: {reason}",
            )

    # Role business rules
    if role == SlotRole.MASTER.value:
        existing_master = _get_master_slot(db, semester_id)
        if existing_master:
            raise HTTPException(
                status_code=409,
                detail="Tournament already has a MASTER instructor slot. Remove it first.",
            )
        pitch_id = None  # MASTER has no pitch

    elif role == SlotRole.FIELD.value:
        if not pitch_id:
            raise HTTPException(
                status_code=422,
                detail="FIELD instructor slot requires a pitch_id.",
            )
        # Validate pitch exists
        pitch = db.query(Pitch).filter(Pitch.id == pitch_id).first()
        if not pitch:
            raise HTTPException(status_code=404, detail=f"Pitch {pitch_id} not found")
    else:
        raise HTTPException(status_code=422, detail=f"Invalid role: {role}. Use MASTER or FIELD.")

    # Pre-flight check: instructor already on roster?
    existing = db.query(TournamentInstructorSlot).filter(
        TournamentInstructorSlot.semester_id == semester_id,
        TournamentInstructorSlot.instructor_id == instructor_id,
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Instructor is already on the roster for this tournament.",
        )

    # Pre-flight check: pitch already taken by another FIELD slot?
    if role == SlotRole.FIELD.value and pitch_id:
        existing_pitch = db.query(TournamentInstructorSlot).filter(
            TournamentInstructorSlot.semester_id == semester_id,
            TournamentInstructorSlot.role == SlotRole.FIELD.value,
            TournamentInstructorSlot.pitch_id == pitch_id,
        ).first()
        if existing_pitch:
            raise HTTPException(
                status_code=409,
                detail=f"Pitch {pitch_id} already has a field instructor assigned.",
            )

    # Pre-flight check: FIELD slot count must not exceed parallel_fields
    if role == SlotRole.FIELD.value:
        cfg = tournament.tournament_config_obj
        if cfg is not None:
            existing_field_count = db.query(TournamentInstructorSlot).filter(
                TournamentInstructorSlot.semester_id == semester_id,
                TournamentInstructorSlot.role == SlotRole.FIELD.value,
            ).count()
            if existing_field_count >= cfg.parallel_fields:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Cannot add more field instructors than configured parallel fields "
                        f"({cfg.parallel_fields}). Increase parallel fields in Schedule "
                        f"Configuration first."
                    ),
                )

    slot = TournamentInstructorSlot(
        semester_id=semester_id,
        instructor_id=instructor_id,
        role=role,
        pitch_id=pitch_id,
        status=SlotStatus.PLANNED.value,
        assigned_by=assigned_by_id,
        notes=notes,
    )
    db.add(slot)
    db.flush()

    # Sync MASTER slot → Semester.master_instructor_id (status validator reads this)
    if role == SlotRole.MASTER.value:
        tournament.master_instructor_id = instructor_id
        db.flush()

    return slot


def remove_slot(db: Session, slot_id: int, by_user: User) -> None:
    """Remove an instructor slot. Admin only."""
    if not _is_admin(by_user):
        raise HTTPException(status_code=403, detail="Only admins can remove instructor slots.")
    slot = _get_slot_or_404(db, slot_id)

    # If removing the MASTER slot, clear Semester.master_instructor_id
    if slot.role == SlotRole.MASTER.value:
        tournament = db.query(Semester).filter(Semester.id == slot.semester_id).first()
        if tournament and tournament.master_instructor_id == slot.instructor_id:
            tournament.master_instructor_id = None
            db.flush()

    db.delete(slot)
    db.flush()


def get_roster(db: Session, semester_id: int) -> List[Dict[str, Any]]:
    """Return full roster with instructor User objects and pitch info."""
    slots = (
        db.query(TournamentInstructorSlot)
        .filter(TournamentInstructorSlot.semester_id == semester_id)
        .order_by(TournamentInstructorSlot.role, TournamentInstructorSlot.id)
        .all()
    )
    result = []
    for slot in slots:
        pitch_name = slot.pitch.name if slot.pitch else None
        result.append({
            "slot_id":         slot.id,
            "instructor_id":   slot.instructor_id,
            "instructor_name": slot.instructor.name if slot.instructor else None,
            "instructor_email": slot.instructor.email if slot.instructor else None,
            "role":            slot.role,
            "pitch_id":        slot.pitch_id,
            "pitch_name":      pitch_name,
            "status":          slot.status,
            "checked_in_at":   slot.checked_in_at.isoformat() if slot.checked_in_at else None,
            "notes":           slot.notes,
        })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Check-in / absent
# ─────────────────────────────────────────────────────────────────────────────

def mark_checkin(db: Session, slot_id: int, requester: User) -> TournamentInstructorSlot:
    """
    Mark a slot as CHECKED_IN.

    Authority:
      - MASTER slot: only the master instructor themselves OR admin
      - FIELD slot: master instructor of the same tournament OR admin
    """
    slot = _get_slot_or_404(db, slot_id)

    if _is_admin(requester):
        pass  # Admin can always check-in
    elif slot.role == SlotRole.MASTER.value:
        # Master self-checkin
        if slot.instructor_id != requester.id:
            raise HTTPException(
                status_code=403,
                detail="Only the master instructor (or admin) can check in the MASTER slot.",
            )
    elif slot.role == SlotRole.FIELD.value:
        # Master of the same tournament checks in field instructor
        master_slot = _get_master_slot(db, slot.semester_id)
        if not master_slot or master_slot.instructor_id != requester.id:
            raise HTTPException(
                status_code=403,
                detail="Only the master instructor (or admin) can check in field instructors.",
            )
    else:
        raise HTTPException(status_code=403, detail="Insufficient permissions.")

    if slot.status == SlotStatus.ABSENT.value:
        raise HTTPException(
            status_code=409,
            detail="Cannot check in an instructor already marked absent.",
        )

    slot.status = SlotStatus.CHECKED_IN.value
    slot.checked_in_at = datetime.now(timezone.utc)
    db.flush()
    return slot


def mark_absent(db: Session, slot_id: int, requester: User) -> TournamentInstructorSlot:
    """
    Mark a slot as ABSENT.

    Authority:
      - MASTER slot: only admin can mark master absent
      - FIELD slot: master instructor of the same tournament OR admin
    """
    slot = _get_slot_or_404(db, slot_id)

    if slot.role == SlotRole.MASTER.value:
        if not _is_admin(requester):
            raise HTTPException(
                status_code=403,
                detail="Only an admin can mark the MASTER instructor absent.",
            )
    elif slot.role == SlotRole.FIELD.value:
        if not _is_admin(requester):
            master_slot = _get_master_slot(db, slot.semester_id)
            if not master_slot or master_slot.instructor_id != requester.id:
                raise HTTPException(
                    status_code=403,
                    detail="Only the master instructor (or admin) can mark field instructors absent.",
                )
    else:
        raise HTTPException(status_code=403, detail="Insufficient permissions.")

    slot.status = SlotStatus.ABSENT.value
    db.flush()
    return slot


# ─────────────────────────────────────────────────────────────────────────────
# Fallback plan
# ─────────────────────────────────────────────────────────────────────────────

def get_fallback_plan(db: Session, semester_id: int) -> Dict[str, Any]:
    """
    Calculate a semi-automatic fallback plan when field instructors are absent.

    Returns a plan dict (does NOT apply anything):
      {
        "absent_field_count":       N,
        "present_field_count":      M,
        "suggested_parallel_fields": M,  # (1 minimum → master takes over)
        "session_reassignment":     {session_id: new_instructor_id, ...},
        "affected_sessions_count":  K,
      }
    """
    tournament = _get_tournament_or_404(db, semester_id)

    field_slots = db.query(TournamentInstructorSlot).filter(
        TournamentInstructorSlot.semester_id == semester_id,
        TournamentInstructorSlot.role == SlotRole.FIELD.value,
    ).all()

    absent_slots  = [s for s in field_slots if s.status == SlotStatus.ABSENT.value]
    present_slots = [s for s in field_slots
                     if s.status in (SlotStatus.CHECKED_IN.value,
                                     SlotStatus.CONFIRMED.value,
                                     SlotStatus.PLANNED.value)]

    absent_field_count  = len(absent_slots)
    present_field_count = len(present_slots)

    # Suggested parallel_fields = number of present field instructors (min 1)
    suggested_parallel_fields = max(present_field_count, 1)

    # Build pitch → instructor map for present slots (prefer CHECKED_IN)
    present_slots_sorted = sorted(
        present_slots,
        key=lambda s: (
            0 if s.status == SlotStatus.CHECKED_IN.value else
            1 if s.status == SlotStatus.CONFIRMED.value else 2
        )
    )
    present_instructors = [s.instructor_id for s in present_slots_sorted]

    master_instructor_id = tournament.master_instructor_id

    # Reassign sessions that currently belong to absent instructors
    absent_instructor_ids = {s.instructor_id for s in absent_slots}

    sessions_to_reassign = db.query(SessionModel).filter(
        SessionModel.semester_id == semester_id,
        SessionModel.instructor_id.in_(absent_instructor_ids),
    ).all() if absent_instructor_ids else []

    reassignment: Dict[int, int] = {}
    for i, session in enumerate(sessions_to_reassign):
        if present_instructors:
            new_instructor = present_instructors[i % len(present_instructors)]
        else:
            new_instructor = master_instructor_id
        if new_instructor and new_instructor != session.instructor_id:
            reassignment[session.id] = new_instructor

    return {
        "absent_field_count":        absent_field_count,
        "present_field_count":       present_field_count,
        "suggested_parallel_fields": suggested_parallel_fields,
        "session_reassignment":      reassignment,
        "affected_sessions_count":   len(reassignment),
    }


def apply_fallback(
    db: Session,
    semester_id: int,
    admin_user: User,
    plan: Dict[str, Any],
) -> int:
    """
    Apply the fallback plan produced by get_fallback_plan().

    1. Update TournamentConfiguration.parallel_fields
    2. Reassign Session.instructor_id per plan
    3. Returns count of updated sessions.
    """
    if not _is_admin(admin_user):
        raise HTTPException(status_code=403, detail="Only admins can apply the fallback plan.")

    _get_tournament_or_404(db, semester_id)

    # 1. Update parallel_fields
    config = db.query(TournamentConfiguration).filter(
        TournamentConfiguration.semester_id == semester_id
    ).first()
    if config:
        config.parallel_fields = plan["suggested_parallel_fields"]

    # 2. Reassign sessions
    reassignment: Dict[int, int] = plan.get("session_reassignment", {})
    updated = 0
    for session_id_str, new_instructor_id in reassignment.items():
        session = db.query(SessionModel).filter(
            SessionModel.id == int(session_id_str),
            SessionModel.semester_id == semester_id,
        ).first()
        if session:
            session.instructor_id = new_instructor_id
            updated += 1

    db.flush()
    return updated
