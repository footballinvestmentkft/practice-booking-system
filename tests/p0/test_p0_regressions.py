"""P0 recovery regressions that do not require a live HTTP server."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.api_v1.endpoints.adaptive_learning import end_learning_session
from app.api.api_v1.endpoints.attendance import create_attendance, update_attendance
from app.api.api_v1.endpoints.auth import login
from app.api.api_v1.endpoints.progression import (
    UpdateProgressRequest,
    get_user_progress,
    update_user_progress,
)
from app.models.attendance import AttendanceStatus
from app.models.session import EventCategory
from app.models.user import UserRole
from app.schemas.attendance import AttendanceCreate, AttendanceUpdate
from app.schemas.auth import Login
from app.services.certificate_service import CertificateService
from app.services.adaptive_learning import AdaptiveLearningService
from app.services.authorization_policy import AuthorizationPolicy


def test_f01_login_never_emits_password_or_hash(capsys):
    secret = "p0-secret-password"
    stored_hash = "$2b$12$p0-sensitive-hash-material"
    user = SimpleNamespace(
        id=41,
        email="p0-login@example.com",
        password_hash=stored_hash,
        is_active=True,
        role=UserRole.STUDENT,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user

    with (
        patch("app.api.api_v1.endpoints.auth.verify_password", return_value=True),
        patch("app.api.api_v1.endpoints.auth.AuditService.log"),
        patch(
            "app.services.gamification.GamificationService.check_and_unlock_achievements",
            return_value=[],
        ),
    ):
        login(Login(email=user.email, password=secret), db=db)

    emitted = capsys.readouterr().out
    assert secret not in emitted
    assert stored_hash not in emitted


def test_f02_api_cannot_complete_another_users_adaptive_session():
    foreign_session = SimpleNamespace(
        id=7,
        user_id=999,
        questions_presented=2,
        questions_correct=2,
        performance_trend=0.5,
        target_difficulty=0.6,
        ended_at=None,
        status="ACTIVE",
        xp_earned=0,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = foreign_session
    actor = SimpleNamespace(id=42, role=UserRole.STUDENT)

    with pytest.raises(HTTPException) as exc:
        end_learning_session(session_id=7, db=db, current_user=actor)

    assert exc.value.status_code == 404
    assert foreign_session.ended_at is None
    db.commit.assert_not_called()


def test_f02_service_rejects_foreign_session_reads_and_answers():
    foreign_session = SimpleNamespace(id=7, user_id=999)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = foreign_session
    service = AdaptiveLearningService(db)

    assert service.get_next_question(user_id=42, session_id=7) is None
    assert service.record_answer(
        user_id=42,
        session_id=7,
        question_id=100,
        is_correct=True,
        time_spent_seconds=1,
    ) == {}
    db.commit.assert_not_called()


def test_f03_instructor_cannot_update_unassigned_session_attendance():
    attendance = SimpleNamespace(
        id=10,
        session_id=20,
        status=AttendanceStatus.absent,
        marked_by=None,
    )
    foreign_session = SimpleNamespace(
        id=20,
        instructor_id=999,
        event_category=None,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [
        attendance,
        foreign_session,
    ]
    actor = SimpleNamespace(id=42, role=UserRole.INSTRUCTOR)

    with pytest.raises(HTTPException) as exc:
        update_attendance(
            attendance_id=10,
            attendance_update=AttendanceUpdate(status=AttendanceStatus.present),
            db=db,
            current_user=actor,
        )

    assert exc.value.status_code == 403
    assert attendance.status == AttendanceStatus.absent
    db.commit.assert_not_called()


def test_f03_instructor_cannot_create_attendance_for_unassigned_session():
    foreign_session = SimpleNamespace(
        id=20,
        instructor_id=999,
        event_category=EventCategory.TRAINING,
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = foreign_session
    actor = SimpleNamespace(id=42, role=UserRole.INSTRUCTOR)

    with pytest.raises(HTTPException) as exc:
        create_attendance(
            attendance_data=AttendanceCreate(
                user_id=5,
                session_id=20,
                booking_id=30,
                status=AttendanceStatus.present,
            ),
            db=db,
            current_user=actor,
        )

    assert exc.value.status_code == 403
    db.commit.assert_not_called()


def test_f03_canonical_attendance_policy_preserves_admin_override():
    session = SimpleNamespace(instructor_id=99)
    assert AuthorizationPolicy.can_manage_attendance(
        SimpleNamespace(id=1, role=UserRole.ADMIN), session
    )
    assert AuthorizationPolicy.can_manage_attendance(
        SimpleNamespace(id=99, role=UserRole.INSTRUCTOR), session
    )
    assert not AuthorizationPolicy.can_manage_attendance(
        SimpleNamespace(id=42, role=UserRole.INSTRUCTOR), session
    )


def test_f07_fake_progress_read_fails_closed():
    with pytest.raises(HTTPException) as exc:
        get_user_progress(
            current_user=SimpleNamespace(id=42, role=UserRole.STUDENT),
            db=MagicMock(),
        )
    assert exc.value.status_code == 501


def test_f07_fake_progress_write_fails_closed_without_commit():
    db = MagicMock()
    with pytest.raises(HTTPException) as exc:
        update_user_progress(
            request=UpdateProgressRequest(track="internship", level="medior"),
            current_user=SimpleNamespace(id=42, role=UserRole.STUDENT),
            db=db,
        )
    assert exc.value.status_code == 501
    db.commit.assert_not_called()


def test_f22_certificate_renderer_returns_a_real_pdf():
    certificate = SimpleNamespace(
        id="CERT-ROW-1",
        unique_identifier="LFA-PLAYER-2026-ABC123",
        issue_date=datetime(2026, 9, 8, tzinfo=timezone.utc),
        completion_date=datetime(2026, 9, 1, tzinfo=timezone.utc),
        cert_metadata={
            "track_name": "LFA Football Player",
            "final_grade": 91.5,
        },
        user=SimpleNamespace(name="Audit Learner"),
        template=SimpleNamespace(
            title="LFA Certificate",
            track=SimpleNamespace(name="LFA Football Player"),
        ),
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = certificate

    rendered = CertificateService(db).generate_certificate_pdf("CERT-ROW-1")

    assert rendered.startswith(b"%PDF-")
    assert rendered.rstrip().endswith(b"%%EOF")
    assert b"PDF certificate content placeholder" not in rendered
    assert b"LFA-PLAYER-2026-ABC123" in rendered
