"""
Notification Service

Centralized service for creating and managing notifications.
Supports multiple notification types and delivery channels (in-app, future: email, push).
"""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session

from ..models.notification import Notification, NotificationType
from ..models.user import User
from ..models.semester import Semester


def create_notification(
    db: Session,
    user_id: int,
    title: str,
    message: str,
    notification_type: NotificationType,
    link: Optional[str] = None,
    related_semester_id: Optional[int] = None,
    related_request_id: Optional[int] = None,
    related_session_id: Optional[int] = None,
    related_booking_id: Optional[int] = None
) -> Notification:
    """
    Create a new notification for a user.

    Args:
        db: Database session
        user_id: ID of the user to notify
        title: Notification title
        message: Notification message body
        notification_type: Type of notification (from NotificationType enum)
        link: Optional deep link to relevant page
        related_semester_id: Optional related semester/tournament ID
        related_request_id: Optional related assignment request ID
        related_session_id: Optional related session ID
        related_booking_id: Optional related booking ID

    Returns:
        Created Notification object
    """
    notification = Notification(
        user_id=user_id,
        title=title,
        message=message,
        type=notification_type,
        is_read=False,
        link=link,
        related_semester_id=related_semester_id,
        related_request_id=related_request_id,
        related_session_id=related_session_id,
        related_booking_id=related_booking_id,
        created_at=datetime.now(timezone.utc)
    )

    db.add(notification)
    # NOTE: Do NOT commit here - let the caller manage the transaction
    # This allows notification creation to be part of a larger transaction
    # db.commit()
    # db.refresh(notification)

    return notification


def create_tournament_application_approved_notification(
    db: Session,
    instructor_id: int,
    tournament: Semester,
    response_message: str,
    request_id: int
) -> Notification:
    """
    Create notification when admin approves an instructor's tournament application.

    For APPLICATION_BASED tournaments, approval means automatic assignment (no further action needed).

    Args:
        db: Database session
        instructor_id: ID of the instructor whose application was approved
        tournament: Tournament (Semester) object
        response_message: Admin's approval message
        request_id: Assignment request ID

    Returns:
        Created Notification object
    """
    title = f"🎉 Tournament Application Approved: {tournament.name}"
    message = (
        f"Congratulations! Your application to lead '{tournament.name}' has been approved!\n\n"
        f"Admin message: {response_message}\n\n"
        f"You are now assigned as the master instructor for this tournament. "
        f"You can view tournament details in your Dashboard."
    )

    return create_notification(
        db=db,
        user_id=instructor_id,
        title=title,
        message=message,
        notification_type=NotificationType.TOURNAMENT_APPLICATION_APPROVED,
        link=f"/instructor-dashboard?tab=my-applications",
        related_semester_id=tournament.id,
        related_request_id=request_id
    )


def create_tournament_application_rejected_notification(
    db: Session,
    instructor_id: int,
    tournament: Semester,
    response_message: str,
    request_id: int
) -> Notification:
    """
    Create notification when admin rejects an instructor's tournament application.

    Args:
        db: Database session
        instructor_id: ID of the instructor whose application was rejected
        tournament: Tournament (Semester) object
        response_message: Admin's rejection message
        request_id: Assignment request ID

    Returns:
        Created Notification object
    """
    title = f"ℹ️ Tournament Application Update: {tournament.name}"
    message = (
        f"Your application to lead '{tournament.name}' has been reviewed.\n\n"
        f"Admin message: {response_message}\n\n"
        f"You can browse other available tournaments in the Open Tournaments tab."
    )

    return create_notification(
        db=db,
        user_id=instructor_id,
        title=title,
        message=message,
        notification_type=NotificationType.TOURNAMENT_APPLICATION_REJECTED,
        link=f"/instructor-dashboard?tab=tournaments",
        related_semester_id=tournament.id,
        related_request_id=request_id
    )


def create_tournament_direct_invitation_notification(
    db: Session,
    instructor_id: int,
    tournament: Semester,
    invitation_message: str,
    request_id: int
) -> Notification:
    """
    Create notification when admin sends a direct invitation (OPEN_ASSIGNMENT).

    Args:
        db: Database session
        instructor_id: ID of the instructor being invited
        tournament: Tournament (Semester) object
        invitation_message: Admin's invitation message
        request_id: Assignment request ID

    Returns:
        Created Notification object
    """
    title = f"📩 Tournament Invitation: {tournament.name}"
    message = (
        f"You have been invited to lead '{tournament.name}'!\n\n"
        f"Admin message: {invitation_message}\n\n"
        f"Please review and accept the invitation in your Inbox."
    )

    return create_notification(
        db=db,
        user_id=instructor_id,
        title=title,
        message=message,
        notification_type=NotificationType.TOURNAMENT_DIRECT_INVITATION,
        link=f"/instructor-dashboard?tab=inbox",
        related_semester_id=tournament.id,
        related_request_id=request_id
    )


def create_tournament_instructor_accepted_notification(
    db: Session,
    admin_id: int,
    instructor: User,
    tournament: Semester
) -> Notification:
    """
    Create notification when instructor accepts a tournament assignment.
    This notifies the admin who assigned them.

    Args:
        db: Database session
        admin_id: ID of the admin to notify
        instructor: Instructor (User) object who accepted
        tournament: Tournament (Semester) object

    Returns:
        Created Notification object
    """
    title = f"✅ Instructor Confirmed: {tournament.name}"
    message = (
        f"{instructor.name} has accepted the assignment for '{tournament.name}'.\n\n"
        f"The tournament is now ready to proceed with instructor confirmed."
    )

    return create_notification(
        db=db,
        user_id=admin_id,
        title=title,
        message=message,
        notification_type=NotificationType.TOURNAMENT_INSTRUCTOR_ACCEPTED,
        link=f"/admin-dashboard?tournament_id={tournament.id}",
        related_semester_id=tournament.id
    )


def create_skill_tier_notification(
    db: Session,
    user_id: int,
    skill_name: str,
    tier_name: str,
    new_pct: float,
    tournament_id: Optional[int] = None,
) -> Notification:
    """
    Notify a player that their skill has crossed a tier boundary.
    Caller is responsible for committing the session.
    """
    readable = skill_name.replace("_", " ").title()
    return create_notification(
        db=db,
        user_id=user_id,
        title=f"Skill Milestone: {readable} — {tier_name}",
        message=(
            f"Your {readable} skill has reached {tier_name} level "
            f"({new_pct:.0f}%). Keep competing to progress further!"
        ),
        notification_type=NotificationType.SKILL_TIER_REACHED,
        link=f"/players/{user_id}/card",
        related_semester_id=tournament_id,
    )


def mark_notification_as_read(
    db: Session,
    notification_id: int,
    user_id: int
) -> Optional[Notification]:
    """
    Mark a notification as read.

    Args:
        db: Database session
        notification_id: ID of the notification
        user_id: ID of the user (for authorization check)

    Returns:
        Updated Notification object, or None if not found or unauthorized
    """
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == user_id
    ).first()

    if not notification:
        return None

    notification.is_read = True
    notification.read_at = datetime.now(timezone.utc)

    # NOTE: Do NOT commit here - let the caller manage the transaction
    # db.commit()
    # db.refresh(notification)

    return notification


def get_unread_notification_count(db: Session, user_id: int) -> int:
    """
    Get count of unread notifications for a user.

    Args:
        db: Database session
        user_id: ID of the user

    Returns:
        Count of unread notifications
    """
    return db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.is_read == False
    ).count()


def get_notifications(
    db: Session,
    user_id: int,
    limit: int = 50,
    unread_only: bool = False
) -> list[Notification]:
    """
    Get notifications for a user.

    Args:
        db: Database session
        user_id: ID of the user
        limit: Maximum number of notifications to return (default: 50)
        unread_only: If True, only return unread notifications (default: False)

    Returns:
        List of Notification objects, ordered by created_at DESC
    """
    query = db.query(Notification).filter(Notification.user_id == user_id)

    if unread_only:
        query = query.filter(Notification.is_read == False)

    return query.order_by(Notification.created_at.desc()).limit(limit).all()


def mark_all_as_read(db: Session, user_id: int) -> int:
    """
    Mark all notifications as read for a user.

    Args:
        db: Database session
        user_id: ID of the user

    Returns:
        Number of notifications marked as read
    """
    count = db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.is_read == False
    ).update({
        "is_read": True,
        "read_at": datetime.now(timezone.utc)
    }, synchronize_session=False)

    # NOTE: Do NOT commit here - let the caller manage the transaction
    return count


def delete_notification(
    db: Session,
    notification_id: int,
    user_id: int
) -> bool:
    """
    Delete a notification.

    Args:
        db: Database session
        notification_id: ID of the notification
        user_id: ID of the user (for authorization check)

    Returns:
        True if deleted successfully, False if not found or unauthorized
    """
    notification = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == user_id
    ).first()

    if not notification:
        return False

    db.delete(notification)
    # NOTE: Do NOT commit here - let the caller manage the transaction

    return True


def delete_old_notifications(
    db: Session,
    days: int = 90
) -> int:
    """
    Delete old notifications (older than specified days).

    Args:
        db: Database session
        days: Delete notifications older than this many days (default: 90)

    Returns:
        Number of notifications deleted
    """
    from datetime import timedelta

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

    count = db.query(Notification).filter(
        Notification.created_at < cutoff_date
    ).delete(synchronize_session=False)

    # NOTE: Do NOT commit here - let the caller manage the transaction
    return count
