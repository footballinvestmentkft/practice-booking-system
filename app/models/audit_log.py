"""
Audit Log Model

Tracks all important user actions and system events for security and compliance.
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, func
from sqlalchemy.orm import relationship

from ..database import Base


class AuditLog(Base):
    """
    Audit log entry for tracking user actions and system events.

    Captures:
    - Who did it (user_id)
    - What they did (action)
    - What resource was affected (resource_type, resource_id)
    - Additional context (details JSON)
    - Request metadata (IP, user agent, HTTP method/path, status code)
    - When it happened (timestamp)
    """
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(255), nullable=False, index=True)
    resource_type = Column(String(50), nullable=True)
    resource_id = Column(Integer, nullable=True)
    details = Column(JSON, nullable=True)
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(500), nullable=True)
    request_method = Column(String(10), nullable=True)
    request_path = Column(String(500), nullable=True)
    status_code = Column(Integer, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    # Relationships
    user = relationship("User", backref="audit_logs", foreign_keys=[user_id])

    def __repr__(self):
        return f"<AuditLog(id={self.id}, user_id={self.user_id}, action='{self.action}', timestamp={self.timestamp})>"


# Common audit action constants
class AuditAction:
    """Standard audit action types"""
    # Authentication
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    LOGIN_FAILED = "LOGIN_FAILED"
    PASSWORD_CHANGE = "PASSWORD_CHANGE"
    PASSWORD_RESET = "PASSWORD_RESET"

    # User Management
    USER_CREATED = "USER_CREATED"
    USER_UPDATED = "USER_UPDATED"
    USER_DELETED = "USER_DELETED"
    USER_ROLE_CHANGED = "USER_ROLE_CHANGED"

    # Specializations
    SPECIALIZATION_SELECTED = "SPECIALIZATION_SELECTED"
    SPECIALIZATION_CHANGED = "SPECIALIZATION_CHANGED"
    PARENTAL_CONSENT_GRANTED = "PARENTAL_CONSENT_GRANTED"

    # Licenses
    LICENSE_ISSUED = "LICENSE_ISSUED"
    LICENSE_VIEWED = "LICENSE_VIEWED"
    LICENSE_DOWNLOADED = "LICENSE_DOWNLOADED"
    LICENSE_UPGRADE_REQUESTED = "LICENSE_UPGRADE_REQUESTED"
    LICENSE_UPGRADE_APPROVED = "LICENSE_UPGRADE_APPROVED"
    LICENSE_UPGRADE_REJECTED = "LICENSE_UPGRADE_REJECTED"
    LICENSE_REVOKED = "LICENSE_REVOKED"
    LICENSE_VERIFIED = "LICENSE_VERIFIED"
    PAYMENT_VERIFIED = "PAYMENT_VERIFIED"
    PAYMENT_UNVERIFIED = "PAYMENT_UNVERIFIED"

    # Invoice/Credit Management
    INVOICE_REQUESTED = "INVOICE_REQUESTED"

    # Projects
    PROJECT_CREATED = "PROJECT_CREATED"
    PROJECT_UPDATED = "PROJECT_UPDATED"
    PROJECT_DELETED = "PROJECT_DELETED"
    PROJECT_ENROLLED = "PROJECT_ENROLLED"
    PROJECT_UNENROLLED = "PROJECT_UNENROLLED"
    PROJECT_MILESTONE_COMPLETED = "PROJECT_MILESTONE_COMPLETED"

    # Quizzes
    QUIZ_STARTED = "QUIZ_STARTED"
    QUIZ_SUBMITTED = "QUIZ_SUBMITTED"
    QUIZ_CREATED = "QUIZ_CREATED"
    QUIZ_UPDATED = "QUIZ_UPDATED"
    QUIZ_DELETED = "QUIZ_DELETED"

    # Sessions
    SESSION_CREATED = "SESSION_CREATED"
    SESSION_UPDATED = "SESSION_UPDATED"
    SESSION_DELETED = "SESSION_DELETED"
    SESSION_BOOKING_CREATED = "SESSION_BOOKING_CREATED"
    SESSION_BOOKING_CANCELLED = "SESSION_BOOKING_CANCELLED"

    # Tournaments
    TOURNAMENT_ENROLLED = "TOURNAMENT_ENROLLED"
    TOURNAMENT_UNENROLLED = "TOURNAMENT_UNENROLLED"
    OPS_SCENARIO_TRIGGERED = "OPS_SCENARIO_TRIGGERED"

    # Semesters (MINI_SEASON / ACADEMY_SEASON programs)
    SEMESTER_ENROLLED = "SEMESTER_ENROLLED"
    SEMESTER_WITHDRAWN = "SEMESTER_WITHDRAWN"
    INSTRUCTOR_UPDATED = "INSTRUCTOR_UPDATED"

    # Certificates
    CERTIFICATE_ISSUED = "CERTIFICATE_ISSUED"
    CERTIFICATE_DOWNLOADED = "CERTIFICATE_DOWNLOADED"
    CERTIFICATE_VIEWED = "CERTIFICATE_VIEWED"

    # Skills Assessments
    FOOTBALL_SKILLS_UPDATED = "FOOTBALL_SKILLS_UPDATED"

    # Admin Actions
    ADMIN_ACCESS = "ADMIN_ACCESS"
    SETTINGS_CHANGED = "SETTINGS_CHANGED"
    DATA_EXPORT = "DATA_EXPORT"
    BULK_OPERATION = "BULK_OPERATION"

    # Adaptive Learning Admin
    AL_QUIZ_METADATA_UPDATED = "AL_QUIZ_METADATA_UPDATED"
    AL_QUIZ_STATUS_CHANGED   = "AL_QUIZ_STATUS_CHANGED"
    AL_QUESTION_UPDATED      = "AL_QUESTION_UPDATED"
    AL_OPTION_UPDATED        = "AL_OPTION_UPDATED"

    # Juggling Contact Annotation (PR-1)
    JUGGLING_CONTACT_CREATED          = "JUGGLING_CONTACT_CREATED"
    JUGGLING_CONTACT_UPDATED          = "JUGGLING_CONTACT_UPDATED"
    JUGGLING_CONTACT_SOFT_DELETED     = "JUGGLING_CONTACT_SOFT_DELETED"
    JUGGLING_CONTACT_REVIEWED         = "JUGGLING_CONTACT_REVIEWED"
    JUGGLING_CONTACT_TAXONOMY_SET     = "JUGGLING_CONTACT_TAXONOMY_SET"
    JUGGLING_ANNOTATION_FINISHED      = "JUGGLING_ANNOTATION_FINISHED"
    JUGGLING_POSE_SNAPSHOT_CREATED    = "JUGGLING_POSE_SNAPSHOT_CREATED"
    JUGGLING_POSE_SNAPSHOT_UPDATED    = "JUGGLING_POSE_SNAPSHOT_UPDATED"
