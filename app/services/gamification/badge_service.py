"""
Badge Service - Achievement and Badge Management

Handles awarding achievements, checking requirements, and managing badges.
"""

from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from ...models.user import User
from ...models.gamification import UserAchievement, BadgeType
from ...models.achievement import Achievement
from ...models.audit_log import AuditLog, AuditAction
from ...models.license import UserLicense
from ...models.quiz import QuizAttempt
from ...models.project import ProjectEnrollment, ProjectEnrollmentStatus
from ...models.user_progress import SpecializationProgress

from .xp_service import calculate_user_stats, award_xp
from app.services.player_identity_service import get_active_football_entitlement
from app.services.player_progression_service import PlayerProgressionService

def award_achievement(
    db: Session,
    user_id: int,
    badge_type: BadgeType,
    title: str,
    description: str,
    icon: str,
    semester_count: Optional[int] = None,
    specialization_id: Optional[str] = None
) -> UserAchievement:
    """
    Award an achievement to a user

    Args:
        db: Database session
        user_id: User ID
        badge_type: Type of badge
        title: Achievement title
        description: Achievement description
        icon: Achievement icon (emoji)
        semester_count: Optional semester count
        specialization_id: Optional specialization (PLAYER/COACH/INTERNSHIP) for spec-specific achievements

    Returns:
        UserAchievement object (existing or newly created)
    """
    # Canonical Football Player achievements are materialized only by the PC5
    # outcome authority. The legacy public function remains a compatibility bridge.
    player = db.get(User, user_id)
    if player is not None and get_active_football_entitlement(db, user_id=user_id) is not None:
        canonical_key = f"LEGACY:{specialization_id or 'GLOBAL'}:{badge_type.value}"
        PlayerProgressionService(db).apply_outcome(
            player=player,
            source_player_user_id=user_id,
            source_type="LEGACY_ACHIEVEMENT_COMMAND",
            source_id=canonical_key,
            idempotency_key=f"legacy-achievement:{user_id}:{canonical_key}",
            actor=player,
            achievements=[{
                "key": canonical_key, "badge_type": badge_type.value, "title": title,
                "description": description, "icon": icon,
            }],
            provenance={"legacy_achievement_bridge": True, "semester_count": semester_count},
            context={"requested_specialization_id": specialization_id},
        )
        db.commit()
        return db.query(UserAchievement).filter_by(user_id=user_id, canonical_key=canonical_key).one()

    # Check if user already has this legacy/non-Player achievement
    existing = db.query(UserAchievement).filter(
        UserAchievement.user_id == user_id,
        UserAchievement.badge_type == badge_type.value,
        UserAchievement.specialization_id == specialization_id  # Include spec in uniqueness check
    ).first()

    if existing:
        return existing

    achievement = UserAchievement(
        user_id=user_id,
        badge_type=badge_type.value,
        title=title,
        description=description,
        icon=icon,
        semester_count=semester_count,
        specialization_id=specialization_id  # NEW: Add specialization
    )

    db.add(achievement)
    db.commit()
    db.refresh(achievement)

    return achievement


def check_and_award_semester_achievements(db: Session, user_id: int) -> List[UserAchievement]:
    """
    Check and award semester-based achievements

    Args:
        db: Database session
        user_id: User ID

    Returns:
        List of newly awarded achievements
    """
    stats = calculate_user_stats(db, user_id)
    achievements = []

    # Returning Student (2+ semesters)
    if stats.semesters_participated >= 2:
        achievement = award_achievement(
            db=db,
            user_id=user_id,
            badge_type=BadgeType.RETURNING_STUDENT,
            title=f"\U0001f504 Returning Student",
            description=f"Participated in {stats.semesters_participated} semesters!",
            icon="\U0001f504",
            semester_count=stats.semesters_participated
        )
        achievements.append(achievement)

    # Veteran Student (3+ semesters)
    if stats.semesters_participated >= 3:
        achievement = award_achievement(
            db=db,
            user_id=user_id,
            badge_type=BadgeType.VETERAN_STUDENT,
            title=f"\U0001f3c5 Veteran Student",
            description=f"A seasoned learner with {stats.semesters_participated} semesters!",
            icon="\U0001f3c5",
            semester_count=stats.semesters_participated
        )
        achievements.append(achievement)

    # Master Student (5+ semesters)
    if stats.semesters_participated >= 5:
        achievement = award_achievement(
            db=db,
            user_id=user_id,
            badge_type=BadgeType.MASTER_STUDENT,
            title=f"\U0001f451 Master Student",
            description=f"A true master with {stats.semesters_participated} semesters!",
            icon="\U0001f451",
            semester_count=stats.semesters_participated
        )
        achievements.append(achievement)

    # Attendance Star (80%+ attendance)
    if stats.attendance_rate >= 80.0 and stats.total_bookings >= 10:
        achievement = award_achievement(
            db=db,
            user_id=user_id,
            badge_type=BadgeType.ATTENDANCE_STAR,
            title=f"\u2b50 Attendance Star",
            description=f"Excellent {stats.attendance_rate:.1f}% attendance rate!",
            icon="\u2b50"
        )
        achievements.append(achievement)

    # Feedback Champion (10+ feedback given)
    if stats.feedback_given >= 10:
        achievement = award_achievement(
            db=db,
            user_id=user_id,
            badge_type=BadgeType.FEEDBACK_CHAMPION,
            title=f"\U0001f4ac Feedback Champion",
            description=f"Provided {stats.feedback_given} valuable feedbacks!",
            icon="\U0001f4ac"
        )
        achievements.append(achievement)

    return achievements


def check_and_award_first_time_achievements(db: Session, user_id: int) -> List[UserAchievement]:
    """
    Check and award first-time achievements for quiz completion

    Args:
        db: Database session
        user_id: User ID

    Returns:
        List of newly awarded achievements
    """
    achievements = []

    # First Quiz Achievement
    quiz_count = db.query(QuizAttempt).filter(
        QuizAttempt.user_id == user_id,
        QuizAttempt.passed == True
    ).count()

    if quiz_count == 1:  # Exactly first successful quiz
        achievement = award_achievement(
            db=db,
            user_id=user_id,
            badge_type=BadgeType.FIRST_QUIZ_COMPLETED,
            title="\U0001f9e0 First Quiz Master",
            description="Completed your very first quiz successfully!",
            icon="\U0001f9e0"
        )
        achievements.append(achievement)
        # Award bonus XP
        award_xp(db, user_id, 100, "First quiz completed")
        print(f"\U0001f389 User {user_id} earned First Quiz Master achievement!")

    return achievements


def check_first_project_enrollment(db: Session, user_id: int, project_id: int) -> List[UserAchievement]:
    """
    Check for first project enrollment achievement

    Args:
        db: Database session
        user_id: User ID
        project_id: Project ID

    Returns:
        List of newly awarded achievements
    """
    achievements = []

    # Check if this is user's first project enrollment
    enrollment_count = db.query(ProjectEnrollment).filter(
        ProjectEnrollment.user_id == user_id,
        ProjectEnrollment.status == ProjectEnrollmentStatus.ACTIVE
    ).count()

    if enrollment_count == 1:  # First enrollment
        achievement = award_achievement(
            db=db,
            user_id=user_id,
            badge_type=BadgeType.FIRST_PROJECT_ENROLLED,
            title="\U0001f4dd Project Pioneer",
            description="Successfully enrolled in your first project!",
            icon="\U0001f4dd"
        )
        achievements.append(achievement)
        # Award bonus XP
        award_xp(db, user_id, 150, "First project enrollment")
        print(f"\U0001f389 User {user_id} earned Project Pioneer achievement!")

        # Check for same-day combo achievement
        combo_achievements = _check_quiz_enrollment_combo(db, user_id)
        achievements.extend(combo_achievements)

    return achievements


def _check_quiz_enrollment_combo(db: Session, user_id: int) -> List[UserAchievement]:
    """
    Check for quiz completion and project enrollment on the same day

    Args:
        db: Database session
        user_id: User ID

    Returns:
        List of newly awarded achievements
    """
    achievements = []
    today = datetime.now(timezone.utc).date()

    # Check if user has a successful quiz attempt today
    quiz_today = db.query(QuizAttempt).filter(
        QuizAttempt.user_id == user_id,
        QuizAttempt.passed == True,
        func.date(QuizAttempt.completed_at) == today
    ).first()

    # Check if user has a project enrollment today
    enrollment_today = db.query(ProjectEnrollment).filter(
        ProjectEnrollment.user_id == user_id,
        ProjectEnrollment.status == ProjectEnrollmentStatus.ACTIVE,
        func.date(ProjectEnrollment.enrolled_at) == today
    ).first()

    if quiz_today and enrollment_today:
        # Check if combo achievement already exists
        existing_combo = db.query(UserAchievement).filter(
            UserAchievement.user_id == user_id,
            UserAchievement.badge_type == BadgeType.QUIZ_ENROLLMENT_COMBO.value
        ).first()

        if not existing_combo:
            achievement = award_achievement(
                db=db,
                user_id=user_id,
                badge_type=BadgeType.QUIZ_ENROLLMENT_COMBO,
                title="\U0001f3af Complete Journey",
                description="Completed quiz and enrolled in project on the same day!",
                icon="\U0001f3af"
            )
            achievements.append(achievement)
            # Award bonus XP
            award_xp(db, user_id, 75, "Quiz + enrollment combo")
            print(f"\U0001f389 User {user_id} earned Complete Journey combo achievement!")

    return achievements


def check_newcomer_welcome(db: Session, user_id: int) -> List[UserAchievement]:
    """
    Check for newcomer welcome achievement

    Args:
        db: Database session
        user_id: User ID

    Returns:
        List of newly awarded achievements
    """
    achievements = []

    # Get user creation date
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return achievements

    # Check if user was created within last 24 hours
    now = datetime.now(timezone.utc)
    # Ensure both datetimes are timezone-aware for comparison
    user_created = user.created_at
    if user_created and user_created.tzinfo is None:
        user_created = user_created.replace(tzinfo=timezone.utc)

    if user_created and (now - user_created) <= timedelta(hours=24):
        # Check if welcome achievement already exists
        existing_welcome = db.query(UserAchievement).filter(
            UserAchievement.user_id == user_id,
            UserAchievement.badge_type == BadgeType.NEWCOMER_WELCOME.value
        ).first()

        if not existing_welcome:
            achievement = award_achievement(
                db=db,
                user_id=user_id,
                badge_type=BadgeType.NEWCOMER_WELCOME,
                title="\U0001f31f Welcome Newcomer",
                description="Welcome to the learning journey!",
                icon="\U0001f31f"
            )
            achievements.append(achievement)
            # Award bonus XP
            award_xp(db, user_id, 50, "Welcome newcomer")
            print(f"\U0001f389 User {user_id} earned Welcome Newcomer achievement!")

    return achievements


def check_and_award_specialization_achievements(
    db: Session,
    user_id: int,
    specialization_id: str
) -> List[UserAchievement]:
    """
    Check and award specialization-specific achievements based on user's progress

    Args:
        db: Database session
        user_id: User ID
        specialization_id: PLAYER, COACH, or INTERNSHIP

    Returns:
        List of newly awarded achievements
    """
    progress = db.query(SpecializationProgress).filter(
        SpecializationProgress.student_id == user_id,
        SpecializationProgress.specialization_id == specialization_id
    ).first()

    if not progress:
        return []

    achievements = []

    # PLAYER-specific achievements
    if specialization_id == 'PLAYER':
        # Level-based achievements
        if progress.current_level >= 2:
            ach = award_achievement(
                db=db,
                user_id=user_id,
                badge_type=BadgeType.FIRST_LEVEL_UP,
                title=f"\u26bd First Belt Promotion",
                description=f"Reached {progress.current_level} level as GanCuju Player!",
                icon="\u26bd",
                specialization_id='PLAYER'
            )
            achievements.append(ach)

        if progress.current_level >= 3:
            ach = award_achievement(
                db=db,
                user_id=user_id,
                badge_type=BadgeType.SKILL_MILESTONE,
                title="\U0001f94b Yellow Belt Warrior",
                description="Achieved Rugalmas Nad level!",
                icon="\U0001f94b",
                specialization_id='PLAYER'
            )
            achievements.append(ach)

        if progress.current_level >= 5:
            ach = award_achievement(
                db=db,
                user_id=user_id,
                badge_type=BadgeType.ADVANCED_SKILL,
                title="\U0001f3c6 Technical Excellence",
                description="Reached Eros Gyoker level!",
                icon="\U0001f3c6",
                specialization_id='PLAYER'
            )
            achievements.append(ach)

        if progress.current_level >= 8:
            ach = award_achievement(
                db=db,
                user_id=user_id,
                badge_type=BadgeType.MASTER_LEVEL,
                title="\U0001f409 Sarkany Bolcsesseg Master",
                description="Achieved the highest GanCuju Player level!",
                icon="\U0001f409",
                specialization_id='PLAYER'
            )
            achievements.append(ach)

        # Session-based achievements
        if progress.completed_sessions >= 5:
            ach = award_achievement(
                db=db,
                user_id=user_id,
                badge_type=BadgeType.PLAYER_DEDICATION,
                title=f"\u26a1 Player Development",
                description=f"Completed {progress.completed_sessions} player sessions!",
                icon="\u26a1",
                specialization_id='PLAYER'
            )
            achievements.append(ach)

    # COACH-specific achievements
    elif specialization_id == 'COACH':
        # Level-based achievements
        if progress.current_level >= 2:
            ach = award_achievement(
                db=db,
                user_id=user_id,
                badge_type=BadgeType.FIRST_LEVEL_UP,
                title="\U0001f468\u200d\U0001f3eb First Coaching License",
                description=f"Reached level {progress.current_level} as Coach!",
                icon="\U0001f468\u200d\U0001f3eb",
                specialization_id='COACH'
            )
            achievements.append(ach)

        if progress.current_level >= 3:
            ach = award_achievement(
                db=db,
                user_id=user_id,
                badge_type=BadgeType.SKILL_MILESTONE,
                title="\U0001f9e0 Leadership Basics",
                description="Achieved Youth Football Asszisztens level!",
                icon="\U0001f9e0",
                specialization_id='COACH'
            )
            achievements.append(ach)

        if progress.current_level >= 5:
            ach = award_achievement(
                db=db,
                user_id=user_id,
                badge_type=BadgeType.ADVANCED_SKILL,
                title="\U0001f4cb Tactical Mind",
                description="Reached Amateur Football Asszisztens level!",
                icon="\U0001f4cb",
                specialization_id='COACH'
            )
            achievements.append(ach)

        if progress.current_level >= 8:
            ach = award_achievement(
                db=db,
                user_id=user_id,
                badge_type=BadgeType.MASTER_LEVEL,
                title="\U0001f3c5 PRO Football Coach Master",
                description="Achieved the highest coaching license!",
                icon="\U0001f3c5",
                specialization_id='COACH'
            )
            achievements.append(ach)

        # Session-based achievements
        if progress.completed_sessions >= 5:
            ach = award_achievement(
                db=db,
                user_id=user_id,
                badge_type=BadgeType.COACH_DEDICATION,
                title="\u265f Coach Development",
                description=f"Completed {progress.completed_sessions} coaching sessions!",
                icon="\u265f",
                specialization_id='COACH'
            )
            achievements.append(ach)

    # INTERNSHIP-specific achievements
    elif specialization_id == 'INTERNSHIP':
        # Level-based achievements
        if progress.current_level >= 2:
            ach = award_achievement(
                db=db,
                user_id=user_id,
                badge_type=BadgeType.FIRST_LEVEL_UP,
                title="\U0001f680 Startup Spirit",
                description="Reached Growth Hacker level!",
                icon="\U0001f680",
                specialization_id='INTERNSHIP'
            )
            achievements.append(ach)

        if progress.current_level >= 3:
            ach = award_achievement(
                db=db,
                user_id=user_id,
                badge_type=BadgeType.MASTER_LEVEL,
                title="\U0001f3af Startup Leader",
                description="Achieved the highest internship level!",
                icon="\U0001f3af",
                specialization_id='INTERNSHIP'
            )
            achievements.append(ach)

        # Session/Project-based achievements
        if progress.completed_sessions >= 3:
            ach = award_achievement(
                db=db,
                user_id=user_id,
                badge_type=BadgeType.INTERNSHIP_DEDICATION,
                title="\U0001f4bc Professional Growth",
                description=f"Completed {progress.completed_sessions} internship sessions!",
                icon="\U0001f4bc",
                specialization_id='INTERNSHIP'
            )
            achievements.append(ach)

        if progress.completed_projects >= 1:
            ach = award_achievement(
                db=db,
                user_id=user_id,
                badge_type=BadgeType.PROJECT_COMPLETE,
                title="\U0001f31f Real World Experience",
                description="Completed your first internship project!",
                icon="\U0001f31f",
                specialization_id='INTERNSHIP'
            )
            achievements.append(ach)

    return achievements


def check_and_unlock_achievements(
    db: Session,
    user_id: int,
    trigger_action: str,
    context: dict = None
) -> List[Achievement]:
    """
    Check and unlock achievements based on user action.

    This is the NEW achievement system that uses the achievements table.

    Args:
        db: Database session
        user_id: User ID
        trigger_action: Action that triggered check (e.g., "login", "complete_quiz")
        context: Additional context (e.g., {"score": 100, "level": 2})

    Returns:
        List of newly unlocked Achievement objects

    Example:
        check_and_unlock_achievements(
            db=db,
            user_id=1,
            trigger_action="login"
        )
    """
    unlocked_achievements = []

    # Get all active achievements
    all_achievements = db.query(Achievement).filter(
        Achievement.is_active == True
    ).all()

    # Get user's existing achievements
    user_achievements = db.query(UserAchievement).filter(
        UserAchievement.user_id == user_id,
        UserAchievement.achievement_id.isnot(None)  # Only check new-style achievements
    ).all()

    existing_achievement_ids = {ua.achievement_id for ua in user_achievements}

    # Check each achievement
    for achievement in all_achievements:
        # Skip if already unlocked
        if achievement.id in existing_achievement_ids:
            continue

        # Check if requirements met
        if _check_achievement_requirements(
            db, user_id, achievement, trigger_action, context or {}
        ):
            # Unlock achievement
            user_achievement = UserAchievement(
                user_id=user_id,
                achievement_id=achievement.id,
                badge_type=achievement.code,  # Use code as badge_type for compatibility
                title=achievement.name,
                description=achievement.description,
                icon=achievement.icon,
                earned_at=datetime.now(timezone.utc)
            )
            db.add(user_achievement)

            # Award XP
            if achievement.xp_reward > 0:
                award_xp(
                    db,
                    user_id,
                    achievement.xp_reward,
                    f"Achievement: {achievement.name}"
                )

            unlocked_achievements.append(achievement)

    # Commit if we unlocked anything
    if unlocked_achievements:
        db.commit()

        # Refresh to get relationships
        for achievement in unlocked_achievements:
            db.refresh(achievement)

    return unlocked_achievements


def _check_achievement_requirements(
    db: Session,
    user_id: int,
    achievement: Achievement,
    trigger_action: str,
    context: dict
) -> bool:
    """
    Check if achievement requirements are met.

    Args:
        db: Database session
        user_id: User ID
        achievement: Achievement to check
        trigger_action: Action that triggered check
        context: Additional context data

    Returns:
        True if requirements met, False otherwise
    """
    requirements = achievement.requirements or {}
    required_action = requirements.get("action")

    # Action doesn't match requirement
    if required_action and required_action != trigger_action:
        return False

    # Check count-based requirements (e.g., "complete 5 quizzes")
    if "count" in requirements:
        required_count = requirements["count"]
        actual_count = _get_user_action_count(db, user_id, trigger_action)

        return actual_count >= required_count

    # Check level-based requirements (e.g., "reach level 5")
    if "level" in requirements:
        required_level = requirements["level"]

        # Check from context first (just-in-time level change)
        if context.get("level") == required_level:
            return True

        # Query user's maximum level across all specializations
        max_level = db.query(
            func.max(UserLicense.current_level)
        ).filter(
            UserLicense.user_id == user_id
        ).scalar()

        return max_level >= required_level if max_level else False

    # Check score-based requirements (e.g., "perfect score")
    if "min_score" in requirements:
        required_score = requirements["min_score"]
        actual_score = context.get("score", 0)

        return actual_score >= required_score

    # Check specialization count (e.g., "have 2+ specializations")
    if "specialization_count" in requirements:
        required_count = requirements["specialization_count"]

        specialization_count = db.query(
            func.count(func.distinct(UserLicense.specialization_type))
        ).filter(
            UserLicense.user_id == user_id
        ).scalar()

        return specialization_count >= required_count if specialization_count else False

    # Default: if no specific requirements, consider met
    return True


def _get_user_action_count(db: Session, user_id: int, action: str) -> int:
    """
    Get count of specific action for user from audit logs.

    Args:
        db: Database session
        user_id: User ID
        action: Action to count (e.g., "login", "complete_quiz")

    Returns:
        Count of times user performed this action
    """
    # Map achievement action to audit log action
    action_mapping = {
        "login": AuditAction.LOGIN,
        "complete_quiz": AuditAction.QUIZ_SUBMITTED,
        "select_specialization": AuditAction.SPECIALIZATION_SELECTED,
        "license_earned": AuditAction.LICENSE_ISSUED,
        "project_enroll": AuditAction.PROJECT_ENROLLED,
        "project_complete": AuditAction.PROJECT_MILESTONE_COMPLETED,
        "quiz_perfect_score": AuditAction.QUIZ_SUBMITTED  # Will check score in context
    }

    audit_action = action_mapping.get(action)

    # If no mapping found, return 0
    if not audit_action:
        return 0

    # Count occurrences in audit log
    count = db.query(AuditLog).filter(
        AuditLog.user_id == user_id,
        AuditLog.action == audit_action
    ).count()

    return count