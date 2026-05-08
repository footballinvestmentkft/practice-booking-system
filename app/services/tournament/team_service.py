"""
Team Service

Business logic for team management (CRUD operations, member management).
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import and_
from fastapi import HTTPException, status
from typing import List, Optional

from app.models import Team, TeamMember, User, TeamMemberRole
from app.models.team import TeamInvite, TeamInviteStatus
from app.models.tournament_configuration import TournamentConfiguration
from app.models.team import TournamentTeamEnrollment
from app.models.license import UserLicense
from app.models.credit_transaction import CreditTransaction, TransactionType


def create_team(
    db: Session,
    name: str,
    captain_user_id: int,
    specialization_type: str,
    code: Optional[str] = None
) -> Team:
    """Create a new team"""
    # Check if captain exists
    captain = db.query(User).filter(User.id == captain_user_id).first()
    if not captain:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Captain user not found"
        )

    # Generate code if not provided
    if not code:
        base_code = f"TEAM-{name.upper().replace(' ', '-')[:10]}"
        code = base_code
        counter = 1

        # Auto-increment if code exists (e.g., TEAM-TEST-01, TEAM-TEST-02)
        while db.query(Team).filter(Team.code == code).first():
            code = f"{base_code[:16]}-{counter:02d}"
            counter += 1

    # If explicit code was provided, check if it already exists
    else:
        existing = db.query(Team).filter(Team.code == code).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Team code '{code}' already exists"
            )

    # Create team
    team = Team(
        name=name,
        code=code,
        captain_user_id=captain_user_id,
        specialization_type=specialization_type,
        is_active=True
    )
    db.add(team)
    db.flush()

    # Add captain as team member
    captain_member = TeamMember(
        team_id=team.id,
        user_id=captain_user_id,
        role=TeamMemberRole.CAPTAIN.value,
        is_active=True
    )
    db.add(captain_member)
    db.commit()
    db.refresh(team)

    return team


def get_team(db: Session, team_id: int) -> Optional[Team]:
    """Get team by ID"""
    return db.query(Team).filter(Team.id == team_id).first()


def get_teams(
    db: Session,
    specialization_type: Optional[str] = None,
    is_active: bool = True,
    limit: int = 100
) -> List[Team]:
    """Get teams with optional filtering"""
    query = db.query(Team).filter(Team.is_active == is_active)

    if specialization_type:
        query = query.filter(Team.specialization_type == specialization_type)

    return query.limit(limit).all()


def add_team_member(
    db: Session,
    team_id: int,
    user_id: int,
    role: str = TeamMemberRole.PLAYER.value
) -> TeamMember:
    """Add a member to a team"""
    # Check team exists
    team = get_team(db, team_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found"
        )

    # Check user exists
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    # Check if already a member
    existing = db.query(TeamMember).filter(
        and_(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
            TeamMember.is_active == True
        )
    ).first()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a team member"
        )

    # Add member
    member = TeamMember(
        team_id=team_id,
        user_id=user_id,
        role=role,
        is_active=True
    )
    db.add(member)
    db.commit()
    db.refresh(member)

    return member


def remove_team_member(
    db: Session,
    team_id: int,
    user_id: int
) -> bool:
    """Remove a member from a team"""
    member = db.query(TeamMember).filter(
        and_(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
            TeamMember.is_active == True
        )
    ).first()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team member not found"
        )

    # Don't allow removing captain (must transfer captain first)
    if member.role == TeamMemberRole.CAPTAIN.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove team captain. Transfer captaincy first."
        )

    member.is_active = False
    db.commit()

    return True


def get_team_members(db: Session, team_id: int, is_active: bool = True) -> List[TeamMember]:
    """Get all members of a team"""
    return db.query(TeamMember).filter(
        and_(
            TeamMember.team_id == team_id,
            TeamMember.is_active == is_active
        )
    ).all()


def transfer_captaincy(
    db: Session,
    team_id: int,
    current_captain_id: int,
    new_captain_id: int
) -> Team:
    """Transfer team captaincy to another member"""
    team = get_team(db, team_id)
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    # Verify current captain
    if team.captain_user_id != current_captain_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only current captain can transfer captaincy"
        )

    # Check new captain is a member
    new_captain_member = db.query(TeamMember).filter(
        and_(
            TeamMember.team_id == team_id,
            TeamMember.user_id == new_captain_id,
            TeamMember.is_active == True
        )
    ).first()

    if not new_captain_member:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New captain must be an active team member"
        )

    # Update roles
    old_captain_member = db.query(TeamMember).filter(
        and_(
            TeamMember.team_id == team_id,
            TeamMember.user_id == current_captain_id
        )
    ).first()

    if old_captain_member:
        old_captain_member.role = TeamMemberRole.PLAYER.value

    new_captain_member.role = TeamMemberRole.CAPTAIN.value
    team.captain_user_id = new_captain_id

    db.commit()
    db.refresh(team)

    return team


def delete_team(db: Session, team_id: int) -> bool:
    """Delete a team (soft delete)"""
    team = get_team(db, team_id)
    if not team:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    team.is_active = False
    db.commit()

    return True


# ---------------------------------------------------------------------------
# Business logic: credit-aware team creation + invite flow
# ---------------------------------------------------------------------------

def create_team_with_cost(
    db: Session,
    name: str,
    captain_user_id: int,
    specialization_type: str,
    tournament_id: int,  # tournament_id = Semester.id (DB FK is called semester_id)
    code: Optional[str] = None,
) -> Team:
    """
    Creates a team, auto-enrolls it in the tournament, and deducts credits from captain.
    Uses SELECT FOR UPDATE to prevent race conditions on credit_balance.
    Raises HTTP 402 if captain has insufficient credits.

    tournament_id = Semester.id — tournaments live in the semesters table.
    """
    cfg = db.query(TournamentConfiguration).filter(
        TournamentConfiguration.semester_id == tournament_id
    ).first()
    cost = cfg.team_enrollment_cost if cfg else 0

    if cost > 0:
        # Lock the license row to prevent concurrent over-spend
        license = (
            db.query(UserLicense)
            .filter(
                UserLicense.user_id == captain_user_id,
                UserLicense.is_active == True,
            )
            .with_for_update()
            .first()
        )
        if not license or license.credit_balance < cost:
            available = license.credit_balance if license else 0
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Insufficient credits. Required: {cost}, Available: {available}",
            )
        license.credit_balance -= cost
        db.add(CreditTransaction(
            user_license_id=license.id,
            amount=-cost,
            balance_after=license.credit_balance,
            transaction_type=TransactionType.ENROLLMENT.value,
            description=f"Team creation fee for tournament {tournament_id}",
            idempotency_key=f"team-create-{captain_user_id}-{tournament_id}",
        ))

    team = create_team(db, name, captain_user_id, specialization_type, code)

    # Auto-enroll team in the tournament
    db.add(TournamentTeamEnrollment(
        semester_id=tournament_id,
        team_id=team.id,
        is_active=True,
        payment_verified=(cost == 0),  # free → auto-verified
    ))
    db.commit()
    db.refresh(team)
    return team


def invite_member(
    db: Session,
    team_id: int,
    invited_user_id: int,
    invited_by_id: int,
) -> TeamInvite:
    """
    Captain invites a player to the team.
    Raises 403 if caller is not the team captain.
    Raises 404 if team or invited user not found / user inactive.
    Raises 409 if user is already an active member.
    Returns existing PENDING invite if one already exists (idempotent).
    """
    team = get_team(db, team_id)
    if not team or not team.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    if team.captain_user_id != invited_by_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the team captain can invite players",
        )

    invited_user = db.query(User).filter(User.id == invited_user_id).first()
    if not invited_user or not invited_user.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found or inactive")

    # Already an active member?
    existing_member = db.query(TeamMember).filter(
        and_(
            TeamMember.team_id == team_id,
            TeamMember.user_id == invited_user_id,
            TeamMember.is_active == True,
        )
    ).first()
    if existing_member:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User is already an active team member")

    # Idempotent: return existing PENDING invite
    existing_invite = db.query(TeamInvite).filter(
        and_(
            TeamInvite.team_id == team_id,
            TeamInvite.invited_user_id == invited_user_id,
            TeamInvite.status == TeamInviteStatus.PENDING.value,
        )
    ).first()
    if existing_invite:
        return existing_invite

    invite = TeamInvite(
        team_id=team_id,
        invited_user_id=invited_user_id,
        invited_by_id=invited_by_id,
        status=TeamInviteStatus.PENDING.value,
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    return invite


def respond_to_invite(
    db: Session,
    invite_id: int,
    user_id: int,
    accept: bool,
) -> TeamInvite:
    """
    Invited user accepts or rejects a pending invite.
    Raises 404 if invite not found or not PENDING.
    Raises 403 if caller is not the invitee.
    On accept: adds user as PLAYER via add_team_member().
    """
    invite = db.query(TeamInvite).filter(TeamInvite.id == invite_id).first()
    if not invite or invite.status != TeamInviteStatus.PENDING.value:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending invite not found")

    if invite.invited_user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the invited user can respond")

    if accept:
        add_team_member(db, invite.team_id, user_id, role=TeamMemberRole.PLAYER.value)
        invite.status = TeamInviteStatus.ACCEPTED.value
    else:
        invite.status = TeamInviteStatus.REJECTED.value

    invite.responded_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(invite)
    return invite


def cancel_invite(
    db: Session,
    invite_id: int,
    captain_user_id: int,
) -> bool:
    """
    Captain cancels a pending invite.
    Raises 404 if invite not found or not PENDING.
    Raises 403 if caller is not the team captain.
    """
    invite = db.query(TeamInvite).filter(TeamInvite.id == invite_id).first()
    if not invite or invite.status != TeamInviteStatus.PENDING.value:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending invite not found")

    team = get_team(db, invite.team_id)
    if not team or team.captain_user_id != captain_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the team captain can cancel invites")

    invite.status = TeamInviteStatus.CANCELLED.value
    invite.responded_at = datetime.now(timezone.utc)
    db.commit()
    return True


def admin_enroll_team_in_tournament(
    db: Session,
    team_id: int,
    tournament_id: int,
) -> TournamentTeamEnrollment:
    """
    Admin-only: enroll any active team into a TEAM tournament without requiring
    caller to be the team captain.

    Guards (same as enroll_existing_team_in_tournament minus captain check):
    - team exists and is active
    - tournament exists, participant_type == TEAM, status == ENROLLMENT_OPEN
    - no active enrollment already exists (idempotent: returns existing if found)
    - if cost > 0: deducts from captain's credit balance (SELECT FOR UPDATE)

    Returns the existing enrollment record when called twice (idempotent).
    """
    from app.models.semester import Semester

    team = get_team(db, team_id)
    if not team or not team.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tournament not found")

    cfg = db.query(TournamentConfiguration).filter(
        TournamentConfiguration.semester_id == tournament_id
    ).first()
    if not cfg or cfg.participant_type != "TEAM":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This tournament does not support team enrollment",
        )

    if tournament.tournament_status != "ENROLLMENT_OPEN":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tournament enrollment is not open (current status: {tournament.tournament_status})",
        )

    existing = db.query(TournamentTeamEnrollment).filter(
        and_(
            TournamentTeamEnrollment.semester_id == tournament_id,
            TournamentTeamEnrollment.team_id == team_id,
            TournamentTeamEnrollment.is_active == True,
        )
    ).first()
    if existing:
        return existing

    cost = cfg.team_enrollment_cost if cfg else 0

    if cost > 0:
        license = (
            db.query(UserLicense)
            .filter(
                UserLicense.user_id == team.captain_user_id,
                UserLicense.is_active == True,
            )
            .with_for_update()
            .first()
        )
        if not license or license.credit_balance < cost:
            available = license.credit_balance if license else 0
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Insufficient credits. Required: {cost}, Available: {available}",
            )
        license.credit_balance -= cost
        db.add(CreditTransaction(
            user_license_id=license.id,
            amount=-cost,
            balance_after=license.credit_balance,
            transaction_type=TransactionType.ENROLLMENT.value,
            description=f"Team enrollment fee for tournament {tournament_id}",
            idempotency_key=f"team-enroll-{team_id}-{tournament_id}",
        ))

    enrollment = TournamentTeamEnrollment(
        semester_id=tournament_id,
        team_id=team_id,
        is_active=True,
        payment_verified=(cost == 0),
    )
    db.add(enrollment)
    db.commit()
    db.refresh(enrollment)
    return enrollment


def get_pending_invites_for_user(db: Session, user_id: int) -> List[TeamInvite]:
    """Get all PENDING invites for a user"""
    return db.query(TeamInvite).filter(
        and_(
            TeamInvite.invited_user_id == user_id,
            TeamInvite.status == TeamInviteStatus.PENDING.value,
        )
    ).all()


def get_team_pending_invites(db: Session, team_id: int) -> List[TeamInvite]:
    """Get all PENDING invites for a team (captain view)"""
    return db.query(TeamInvite).filter(
        and_(
            TeamInvite.team_id == team_id,
            TeamInvite.status == TeamInviteStatus.PENDING.value,
        )
    ).all()


def enroll_existing_team_in_tournament(
    db: Session,
    team_id: int,
    captain_user_id: int,
    tournament_id: int,
) -> TournamentTeamEnrollment:
    """
    Enroll an existing team into an ENROLLMENT_OPEN TEAM tournament.

    Guards:
    - caller is the team's captain (TeamMember.role == CAPTAIN)
    - tournament type is TEAM (participant_type)
    - tournament status is ENROLLMENT_OPEN
    - team not already enrolled (no active enrollment)
    - captain has sufficient credits (SELECT FOR UPDATE on UserLicense)

    Deducts team_enrollment_cost from captain's credit_balance.
    Creates CreditTransaction with idempotency key.
    """
    from app.models.semester import Semester

    # Verify team exists
    team = get_team(db, team_id)
    if not team or not team.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    # Verify caller is captain
    if team.captain_user_id != captain_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the team captain can enroll the team in a tournament",
        )

    # Verify tournament exists
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tournament not found")

    # Verify tournament is TEAM type
    cfg = db.query(TournamentConfiguration).filter(
        TournamentConfiguration.semester_id == tournament_id
    ).first()
    if not cfg or cfg.participant_type != "TEAM":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This tournament does not support team enrollment",
        )

    # Verify tournament is ENROLLMENT_OPEN
    if tournament.tournament_status != "ENROLLMENT_OPEN":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tournament enrollment is not open (current status: {tournament.tournament_status})",
        )

    # Verify team not already enrolled
    existing = db.query(TournamentTeamEnrollment).filter(
        and_(
            TournamentTeamEnrollment.semester_id == tournament_id,
            TournamentTeamEnrollment.team_id == team_id,
            TournamentTeamEnrollment.is_active == True,
        )
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Team is already enrolled in this tournament",
        )

    # Verify team has active members — an empty team cannot play
    active_member_count = db.query(TeamMember).filter(
        TeamMember.team_id == team_id,
        TeamMember.is_active == True,
    ).count()
    if active_member_count == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Team has no active players. Add players to the team before enrolling.",
        )

    cost = cfg.team_enrollment_cost if cfg else 0

    if cost > 0:
        license = (
            db.query(UserLicense)
            .filter(
                UserLicense.user_id == captain_user_id,
                UserLicense.is_active == True,
            )
            .with_for_update()
            .first()
        )
        if not license or license.credit_balance < cost:
            available = license.credit_balance if license else 0
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Insufficient credits. Required: {cost}, Available: {available}",
            )
        license.credit_balance -= cost
        db.add(CreditTransaction(
            user_license_id=license.id,
            amount=-cost,
            balance_after=license.credit_balance,
            transaction_type=TransactionType.ENROLLMENT.value,
            description=f"Team enrollment fee for tournament {tournament_id}",
            idempotency_key=f"team-enroll-{team_id}-{tournament_id}",
        ))

    enrollment = TournamentTeamEnrollment(
        semester_id=tournament_id,
        team_id=team_id,
        is_active=True,
        payment_verified=(cost == 0),
    )
    db.add(enrollment)
    db.commit()
    db.refresh(enrollment)
    return enrollment
