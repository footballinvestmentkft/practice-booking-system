from .user import User, UserRole
from .instructor_specialization import InstructorSpecialization
from .instructor_availability import InstructorSpecializationAvailability
from .instructor_assignment import (
    InstructorAvailabilityWindow,
    InstructorAssignmentRequest,
    AssignmentRequestStatus,
    LocationMasterInstructor,
    InstructorPosition,
    PositionStatus,
    PositionApplication,
    ApplicationStatus,
    InstructorAssignment,
    SportDirectorAssignment,
)
from .location import Location
from .campus import Campus
from .pitch import Pitch
from .pitch_instructor_assignment import (
    PitchInstructorAssignment,
    PitchAssignmentType,
    PitchAssignmentStatus,
)
from .semester import Semester, SemesterStatus, SemesterCategory
from .semester_schedule_config import SemesterScheduleConfig
from .group import Group, group_users
from .session import Session, SessionType, EventCategory, SessionParticipantType, DeliveryMode
from .event_reward_log import EventRewardLog
from .booking import Booking, BookingStatus
from .attendance import Attendance, AttendanceStatus
from .feedback import Feedback
from .notification import Notification, NotificationType
from .message import Message, MessagePriority
from .gamification import UserAchievement, UserStats, BadgeType, configure_relationships
from .achievement import Achievement, AchievementCategory
from .quiz import Quiz, QuizQuestion, QuizAnswerOption, QuizAttempt, QuizUserAnswer, SessionQuiz, QuestionType, QuizCategory, QuizDifficulty
from .project import Project, ProjectEnrollment, ProjectMilestone, ProjectMilestoneProgress, ProjectSession, ProjectStatus, ProjectEnrollmentStatus, ProjectProgressStatus, MilestoneStatus
from .license import LicenseMetadata, UserLicense, LicenseProgression, LicenseType, LicenseLevel, LicenseSystemHelper, configure_license_relationships
from .semester_enrollment import SemesterEnrollment
from .performance_review import StudentPerformanceReview, InstructorSessionReview
from .football_skill_assessment import FootballSkillAssessment
from .belt_promotion import BeltPromotion
from .credit_transaction import CreditTransaction, TransactionType
from .xp_transaction import XPTransaction
from .skill_reward import SkillReward
from .invoice_request import InvoiceRequest, InvoiceRequestStatus
from .coupon import Coupon, CouponType
from .invitation_code import InvitationCode
from .session_group import SessionGroupAssignment, SessionGroupStudent
from .session_segment import SessionSegment
from .session_segment_result import SessionSegmentResult
from .audit_log import AuditLog
from .system_event import SystemEvent, SystemEventLevel, SystemEventType
from .match_structure import MatchStructure, MatchResult, MatchFormat, ScoringType
from .club import Club, CsvImportLog
from .sponsor import Sponsor, SponsorCampaign, SponsorContact, SponsorAudienceEntry
from .tournament_instructor_slot import TournamentInstructorSlot, SlotRole, SlotStatus

# 🎓 New Track-Based Modular Education System
from .track import Track, Module, ModuleComponent
from .certificate import CertificateTemplate, IssuedCertificate
from .user_progress import UserTrackProgress, UserModuleProgress, TrackProgressStatus, ModuleProgressStatus

# 🏆 Tournament System
from .tournament_enums import TournamentType, ParticipantType, TeamMemberRole
from .tournament_type import TournamentType as TournamentTypeModel  # DB model for tournament types
from .game_preset import GamePreset  # Game preset configurations
from .team import Team, TeamMember, TournamentTeamEnrollment, TeamInvite, TeamInviteStatus
from .tournament_ranking import TournamentRanking, TournamentStats, TournamentReward
from .tournament_status_history import TournamentStatusHistory
from .tournament_configuration import TournamentConfiguration  # P2: Separate tournament config table
from .campus_schedule_config import CampusScheduleConfig  # Per-campus schedule overrides for tournaments
from .tournament_reward_config import TournamentRewardConfig  # P1: Separate reward config table
from .game_configuration import GameConfiguration  # P3: Separate game config table
from .tournament_achievement import (
    TournamentSkillMapping,
    TournamentParticipation,
    TournamentBadge,
    TournamentBadgeType,
    TournamentBadgeCategory,
    TournamentBadgeRarity,
    SkillPointConversionRate
)

# Configure relationships after all models are imported
configure_relationships()
configure_license_relationships()

__all__ = [
    "User",
    "UserRole",
    "InstructorSpecialization",
    "InstructorSpecializationAvailability",
    "InstructorAvailabilityWindow",
    "InstructorAssignmentRequest",
    "AssignmentRequestStatus",
    "LocationMasterInstructor",
    "InstructorPosition",
    "PositionStatus",
    "PositionApplication",
    "ApplicationStatus",
    "InstructorAssignment",
    "SportDirectorAssignment",
    "Location",
    "Campus",
    "Pitch",
    "PitchInstructorAssignment",
    "PitchAssignmentType",
    "PitchAssignmentStatus",
    "Semester",
    "SemesterStatus",
    "SemesterCategory",
    "Group",
    "group_users",
    "Session",
    "SessionType",
    "EventCategory",
    "SessionParticipantType",
    "DeliveryMode",
    "EventRewardLog",
    "Booking",
    "BookingStatus",
    "Attendance",
    "AttendanceStatus",
    "Feedback",
    "Notification",
    "NotificationType",
    "Message",
    "MessagePriority",
    "UserAchievement",
    "UserStats",
    "BadgeType",
    "Achievement",
    "AchievementCategory",
    "Quiz",
    "QuizQuestion",
    "QuizAnswerOption",
    "QuizAttempt",
    "QuizUserAnswer",
    "SessionQuiz",
    "QuestionType",
    "QuizCategory",
    "QuizDifficulty",
    "Project",
    "ProjectEnrollment",
    "ProjectMilestone",
    "ProjectMilestoneProgress",
    "ProjectSession",
    "ProjectStatus",
    "ProjectEnrollmentStatus",
    "ProjectProgressStatus",
    "MilestoneStatus",
    "LicenseMetadata",
    "UserLicense",
    "LicenseProgression",
    "LicenseType",
    "LicenseLevel",
    "LicenseSystemHelper",
    "SemesterEnrollment",
    "FootballSkillAssessment",
    "BeltPromotion",
    "CreditTransaction",
    "TransactionType",
    "XPTransaction",
    "SkillReward",
    "InvoiceRequest",
    "InvoiceRequestStatus",
    "Coupon",
    "CouponType",
    "StudentPerformanceReview",
    "InstructorSessionReview",
    "SessionGroupAssignment",
    "SessionGroupStudent",
    "SessionSegment",
    "SessionSegmentResult",
    "AuditLog",
    "SystemEvent",
    "SystemEventLevel",
    "SystemEventType",
    "MatchStructure",
    "MatchResult",
    "MatchFormat",
    "ScoringType",
    # Tournament System
    "TournamentType",
    "TournamentTypeModel",
    "GamePreset",
    "ParticipantType",
    "TeamMemberRole",
    "Team",
    "TeamMember",
    "TournamentTeamEnrollment",
    "TeamInvite",
    "TeamInviteStatus",
    "TournamentRanking",
    "TournamentStats",
    "TournamentReward",
    "TournamentStatusHistory",
    "TournamentConfiguration",
    "CampusScheduleConfig",
    "TournamentRewardConfig",
    "GameConfiguration",
    "TournamentSkillMapping",
    "TournamentParticipation",
    "TournamentBadge",
    "TournamentBadgeType",
    "TournamentBadgeCategory",
    "TournamentBadgeRarity",
    "SkillPointConversionRate",
    # Club system
    "Club",
    "CsvImportLog",
    # Sponsor system (P2-A / P3)
    "Sponsor",
    "SponsorCampaign",
    "SponsorContact",
    "SponsorAudienceEntry",
    # Instructor planning
    "TournamentInstructorSlot",
    "SlotRole",
    "SlotStatus",
]