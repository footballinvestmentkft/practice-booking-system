"""
Tournament services package.

This package contains modular tournament-specific business logic,
extracted from the monolithic tournament_service.py for better
maintainability and testability.

Modules:
    - validation: Tournament validation logic (age categories, enrollment rules)
    - core: Tournament CRUD operations
    - instructor_service: Instructor assignment logic
    - enrollment_service: Enrollment business logic
"""

from .validation import (
    get_visible_tournament_age_groups,
    validate_tournament_enrollment_age,
    validate_tournament_ready_for_enrollment,
    validate_enrollment_deadline,
    check_duplicate_enrollment,
    validate_tournament_session_type,
    validate_tournament_attendance_status,
    get_allowed_age_groups,
)

from .core import (
    create_tournament_semester,
    create_tournament_sessions,
    get_tournament_summary,
    delete_tournament,
)

from .instructor_service import (
    send_instructor_request,
    accept_instructor_request,
    decline_instructor_request,
)

from .enrollment_service import (
    auto_book_students,
)

__all__ = [
    # Validation functions
    "get_visible_tournament_age_groups",
    "validate_tournament_enrollment_age",
    "validate_tournament_ready_for_enrollment",
    "validate_enrollment_deadline",
    "check_duplicate_enrollment",
    "validate_tournament_session_type",
    "validate_tournament_attendance_status",
    "get_allowed_age_groups",
    # Core CRUD functions
    "create_tournament_semester",
    "create_tournament_sessions",
    "get_tournament_summary",
    "delete_tournament",
    # Instructor assignment functions
    "send_instructor_request",
    "accept_instructor_request",
    "decline_instructor_request",
    # Enrollment functions
    "auto_book_students",
]
