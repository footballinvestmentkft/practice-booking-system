"""
Admin Biometric Review API tests — PR-7B.

BCA-ADM-01  GET /review-queue — admin + flag on → 200 + list
BCA-ADM-02  GET /review-queue — non-admin → 403
BCA-ADM-03  GET /review-queue — flag off → 503
BCA-ADM-04  GET /review-queue — response contains no face_match_score
BCA-ADM-05  GET /{user_id}/history — admin → 200
BCA-ADM-06  GET /{user_id}/history — non-admin → 403
BCA-ADM-07  GET /{user_id}/history — response contains no face_match_score (AST)
BCA-ADM-08  POST /override — approved → 200 + EVT_ADMIN_OVERRIDE + verified status
BCA-ADM-09  POST /override — rejected → 200 + EVT_ADMIN_OVERRIDE + rejected status
BCA-ADM-10  POST /override — self-approval → 403 self_override_forbidden
BCA-ADM-11  POST /override — target not manual_review_required → 409
BCA-ADM-12  POST /override — actor_user_id NOT NULL in audit row
BCA-ADM-13  POST /override — response contains no face_match_score
BCA-ADM-14  POST /override rejected → user.manual_review_required=False
BCA-ADM-15  POST /override approved → user.manual_review_required=False
BCA-ADM-16  POST /override — target no active disclosure → 403 biometric_disclosure_required
BCA-ADM-17  POST /override — target stale disclosure → 403 biometric_disclosure_update_required
BCA-ADM-18  POST /override — target no active consent → 403 biometric_consent_required
BCA-ADM-19  POST /override — reason max_length 200 validation
BCA-ADM-20  Admin schemas AST: no score / embedding / raw biometric fields
BCA-ADM-21  EVT_ADMIN_OVERRIDE audit row has no face_match_score
BCA-ADM-22  Route count 883 stable
"""
from __future__ import annotations

import ast
import asyncio
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.api_v1.endpoints.admin_biometric_review import (
    admin_get_review_queue,
    admin_get_user_history,
    admin_override_biometric,
)
from app.models.biometric import BiometricVerificationLog, UserBiometricDisclosure
from app.models.user import User, UserRole
from app.schemas.biometric import (
    AdminBiometricOverrideOut,
    AdminBiometricOverrideRequest,
    AdminBiometricReviewQueueOut,
)
from app.services.biometric.audit_log import EVT_ADMIN_OVERRIDE


def _run(fn):
    import inspect
    if inspect.iscoroutine(fn):
        return asyncio.run(fn)
    return fn


def _mock_request(ip: str = "10.0.0.1"):
    req = MagicMock()
    req.headers.get = lambda key, default=None: None
    req.client.host = ip
    return req


def _grant_consent(db, user):
    from app.services.biometric.consent_service import grant_consent
    grant_consent(db=db, user=user, consent_version="v1.0")
    db.flush()


def _grant_disclosure(db, user):
    from app.services.biometric.disclosure_service import accept_disclosure
    accept_disclosure(db=db, user=user, disclosure_version="v1.0")
    db.flush()


def _set_manual_review(db, user):
    user.face_match_status      = "manual_review_required"
    user.manual_review_required = True
    db.flush()


def _make_admin(db, uid_offset: int = 0) -> User:
    admin = User(
        name=f"Admin {uid_offset}",
        email=f"admin_review_{uid_offset}@test.com",
        password_hash="hashed",
        role=UserRole.ADMIN,
        date_of_birth=date(1980, 1, 1),
    )
    db.add(admin)
    db.flush()
    return admin


def _make_student(db, uid_offset: int = 0) -> User:
    student = User(
        name=f"Student {uid_offset}",
        email=f"student_review_{uid_offset}@test.com",
        password_hash="hashed",
        role=UserRole.STUDENT,
        date_of_birth=date(1998, 1, 1),
    )
    db.add(student)
    db.flush()
    return student


# ── BCA-ADM-01 — GET /review-queue admin + flag on ───────────────────────────

def test_bca_adm01_review_queue_admin_flag_on(db, biometric_feature_enabled):
    admin = _make_admin(db, 1)
    student = _make_student(db, 1)
    _grant_disclosure(db, student)
    _grant_consent(db, student)
    _set_manual_review(db, student)

    result = _run(admin_get_review_queue(db=db, current_admin=admin))
    assert isinstance(result, AdminBiometricReviewQueueOut)
    user_ids = [item.user_id for item in result.items]
    assert student.id in user_ids


# ── BCA-ADM-02 — non-admin → 403 ─────────────────────────────────────────────

def test_bca_adm02_non_admin_403(db, biometric_feature_enabled):
    from app.dependencies import get_current_admin_user
    student_as_admin = _make_student(db, 2)
    with pytest.raises(HTTPException) as exc_info:
        get_current_admin_user(current_user=student_as_admin)
    assert exc_info.value.status_code == 403


# ── BCA-ADM-03 — flag off → 503 ──────────────────────────────────────────────

def test_bca_adm03_flag_off_503():
    from app.services.biometric.feature_flag import require_biometric_enabled

    async def _call():
        await require_biometric_enabled()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_call())
    assert exc_info.value.status_code == 503


# ── BCA-ADM-04 — review queue response no score ───────────────────────────────

def test_bca_adm04_review_queue_no_score(db, biometric_feature_enabled):
    admin = _make_admin(db, 4)
    student = _make_student(db, 4)
    _grant_disclosure(db, student)
    _grant_consent(db, student)
    _set_manual_review(db, student)

    result = _run(admin_get_review_queue(db=db, current_admin=admin))
    for item in result.items:
        d = item.model_dump()
        assert "face_match_score" not in d
        assert "embedding" not in d
        assert "embedding_ciphertext" not in d


# ── BCA-ADM-05 — GET /history admin → 200 ────────────────────────────────────

def test_bca_adm05_history_admin_200(db, biometric_feature_enabled):
    admin = _make_admin(db, 5)
    student = _make_student(db, 5)

    result = _run(admin_get_user_history(
        user_id=student.id, db=db, current_admin=admin
    ))
    assert result.user_id == student.id
    assert isinstance(result.events, list)


# ── BCA-ADM-06 — GET /history non-admin → 403 ────────────────────────────────

def test_bca_adm06_history_non_admin_403(db, biometric_feature_enabled):
    from app.dependencies import get_current_admin_user
    student_as_admin = _make_student(db, 6)
    with pytest.raises(HTTPException) as exc_info:
        get_current_admin_user(current_user=student_as_admin)
    assert exc_info.value.status_code == 403


# ── BCA-ADM-07 — history response AST no score ───────────────────────────────

def test_bca_adm07_history_schema_ast_no_score():
    import app.schemas.biometric as mod
    src = open(mod.__file__).read()
    tree = ast.parse(src)
    forbidden = {"face_match_score", "embedding", "embedding_ciphertext",
                 "yaw", "roll", "pitch", "landmarks"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and "Admin" in node.name and "History" in node.name:
            for item in ast.walk(node):
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    assert item.value not in forbidden, (
                        f"Forbidden field '{item.value}' in {node.name}"
                    )


# ── BCA-ADM-08 — POST /override approved ─────────────────────────────────────

def test_bca_adm08_override_approved(db, biometric_feature_enabled):
    admin = _make_admin(db, 8)
    student = _make_student(db, 8)
    _grant_disclosure(db, student)
    _grant_consent(db, student)
    _set_manual_review(db, student)

    result = _run(admin_override_biometric(
        user_id=student.id,
        payload=AdminBiometricOverrideRequest(decision="approved"),
        request=_mock_request(),
        db=db,
        current_admin=admin,
    ))
    db.commit()

    assert isinstance(result, AdminBiometricOverrideOut)
    assert result.result == "approved"
    assert result.user_id == student.id

    db.refresh(student)
    assert student.face_match_status == "verified"

    logs = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.user_id == student.id,
        BiometricVerificationLog.event_type == EVT_ADMIN_OVERRIDE,
        BiometricVerificationLog.event_result == "approved",
    ).all()
    assert logs, "EVT_ADMIN_OVERRIDE(approved) must be written"


# ── BCA-ADM-09 — POST /override rejected ─────────────────────────────────────

def test_bca_adm09_override_rejected(db, biometric_feature_enabled):
    admin = _make_admin(db, 9)
    student = _make_student(db, 9)
    _grant_disclosure(db, student)
    _grant_consent(db, student)
    _set_manual_review(db, student)

    result = _run(admin_override_biometric(
        user_id=student.id,
        payload=AdminBiometricOverrideRequest(decision="rejected"),
        request=_mock_request(),
        db=db,
        current_admin=admin,
    ))
    db.commit()

    assert result.result == "rejected"
    db.refresh(student)
    assert student.face_match_status == "rejected"

    logs = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.user_id == student.id,
        BiometricVerificationLog.event_type == EVT_ADMIN_OVERRIDE,
        BiometricVerificationLog.event_result == "rejected",
    ).all()
    assert logs, "EVT_ADMIN_OVERRIDE(rejected) must be written"


# ── BCA-ADM-10 — self-approval → 403 ─────────────────────────────────────────

def test_bca_adm10_self_override_403(db, biometric_feature_enabled):
    admin = _make_admin(db, 10)
    _grant_disclosure(db, admin)
    _grant_consent(db, admin)
    _set_manual_review(db, admin)

    with pytest.raises(HTTPException) as exc_info:
        _run(admin_override_biometric(
            user_id=admin.id,
            payload=AdminBiometricOverrideRequest(decision="approved"),
            request=_mock_request(),
            db=db,
            current_admin=admin,
        ))
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "self_override_forbidden"


# ── BCA-ADM-11 — target not manual_review_required → 409 ─────────────────────

def test_bca_adm11_not_review_required_409(db, biometric_feature_enabled):
    admin = _make_admin(db, 11)
    student = _make_student(db, 11)
    _grant_disclosure(db, student)
    _grant_consent(db, student)
    # Do NOT set manual_review_required — face_match_status is NULL

    with pytest.raises(HTTPException) as exc_info:
        _run(admin_override_biometric(
            user_id=student.id,
            payload=AdminBiometricOverrideRequest(decision="approved"),
            request=_mock_request(),
            db=db,
            current_admin=admin,
        ))
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "override_not_applicable"


# ── BCA-ADM-12 — actor_user_id NOT NULL in audit ─────────────────────────────

def test_bca_adm12_actor_user_id_not_null_in_audit(db, biometric_feature_enabled):
    admin = _make_admin(db, 12)
    student = _make_student(db, 12)
    _grant_disclosure(db, student)
    _grant_consent(db, student)
    _set_manual_review(db, student)

    _run(admin_override_biometric(
        user_id=student.id,
        payload=AdminBiometricOverrideRequest(decision="approved"),
        request=_mock_request(),
        db=db,
        current_admin=admin,
    ))
    db.commit()

    log = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.user_id == student.id,
        BiometricVerificationLog.event_type == EVT_ADMIN_OVERRIDE,
    ).first()
    assert log is not None
    assert log.actor_user_id == admin.id, "actor_user_id must be set (NOT NULL)"
    assert log.actor_user_id is not None


# ── BCA-ADM-13 — override response no score ──────────────────────────────────

def test_bca_adm13_override_response_no_score(db, biometric_feature_enabled):
    admin = _make_admin(db, 13)
    student = _make_student(db, 13)
    _grant_disclosure(db, student)
    _grant_consent(db, student)
    _set_manual_review(db, student)

    result = _run(admin_override_biometric(
        user_id=student.id,
        payload=AdminBiometricOverrideRequest(decision="approved"),
        request=_mock_request(),
        db=db,
        current_admin=admin,
    ))

    d = result.model_dump()
    assert "face_match_score" not in d
    assert "embedding" not in d
    assert set(d.keys()) == {"result", "user_id", "decided_at"}


# ── BCA-ADM-14 — rejected → manual_review_required=False ─────────────────────

def test_bca_adm14_rejected_manual_review_false(db, biometric_feature_enabled):
    admin = _make_admin(db, 14)
    student = _make_student(db, 14)
    _grant_disclosure(db, student)
    _grant_consent(db, student)
    _set_manual_review(db, student)
    assert student.manual_review_required is True

    _run(admin_override_biometric(
        user_id=student.id,
        payload=AdminBiometricOverrideRequest(decision="rejected"),
        request=_mock_request(),
        db=db,
        current_admin=admin,
    ))
    db.commit()
    db.refresh(student)
    assert student.manual_review_required is False


# ── BCA-ADM-15 — approved → manual_review_required=False ─────────────────────

def test_bca_adm15_approved_manual_review_false(db, biometric_feature_enabled):
    admin = _make_admin(db, 15)
    student = _make_student(db, 15)
    _grant_disclosure(db, student)
    _grant_consent(db, student)
    _set_manual_review(db, student)
    assert student.manual_review_required is True

    _run(admin_override_biometric(
        user_id=student.id,
        payload=AdminBiometricOverrideRequest(decision="approved"),
        request=_mock_request(),
        db=db,
        current_admin=admin,
    ))
    db.commit()
    db.refresh(student)
    assert student.manual_review_required is False


# ── BCA-ADM-16 — no active disclosure → 403 ──────────────────────────────────

def test_bca_adm16_no_disclosure_403(db, biometric_feature_enabled):
    admin = _make_admin(db, 16)
    student = _make_student(db, 16)
    _grant_consent(db, student)
    _set_manual_review(db, student)
    # No disclosure granted

    with pytest.raises(HTTPException) as exc_info:
        _run(admin_override_biometric(
            user_id=student.id,
            payload=AdminBiometricOverrideRequest(decision="approved"),
            request=_mock_request(),
            db=db,
            current_admin=admin,
        ))
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "biometric_disclosure_required"


# ── BCA-ADM-17 — stale disclosure → 403 ──────────────────────────────────────

def test_bca_adm17_stale_disclosure_403(db, biometric_feature_enabled):
    admin = _make_admin(db, 17)
    student = _make_student(db, 17)
    _grant_consent(db, student)
    _set_manual_review(db, student)

    # Insert old version row directly
    old = UserBiometricDisclosure(
        user_id=student.id,
        disclosure_version="v0.9",
        accepted_at=datetime.now(timezone.utc),
        is_active=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(old)
    db.flush()

    with pytest.raises(HTTPException) as exc_info:
        _run(admin_override_biometric(
            user_id=student.id,
            payload=AdminBiometricOverrideRequest(decision="approved"),
            request=_mock_request(),
            db=db,
            current_admin=admin,
        ))
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "biometric_disclosure_update_required"


# ── BCA-ADM-18 — no active consent → 403 ────────────────────────────────────

def test_bca_adm18_no_consent_403(db, biometric_feature_enabled):
    admin = _make_admin(db, 18)
    student = _make_student(db, 18)
    _grant_disclosure(db, student)
    _set_manual_review(db, student)
    # No consent granted

    with pytest.raises(HTTPException) as exc_info:
        _run(admin_override_biometric(
            user_id=student.id,
            payload=AdminBiometricOverrideRequest(decision="approved"),
            request=_mock_request(),
            db=db,
            current_admin=admin,
        ))
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "biometric_consent_required"


# ── BCA-ADM-19 — reason max_length 200 ───────────────────────────────────────

def test_bca_adm19_reason_max_length():
    with pytest.raises(Exception):
        AdminBiometricOverrideRequest(decision="approved", reason="x" * 201)

    # Valid max length
    req = AdminBiometricOverrideRequest(decision="approved", reason="x" * 200)
    assert len(req.reason) == 200


# ── BCA-ADM-20 — AST schema no score in admin schemas ────────────────────────

def test_bca_adm20_admin_schemas_ast_no_score():
    import app.schemas.biometric as mod
    src = open(mod.__file__).read()
    tree = ast.parse(src)
    forbidden = {
        "face_match_score", "embedding", "embedding_ciphertext",
        "yaw", "roll", "pitch", "landmarks", "frame_data",
    }
    admin_classes = [
        "AdminBiometricReviewItemOut",
        "AdminBiometricReviewQueueOut",
        "AdminBiometricHistoryEventOut",
        "AdminBiometricHistoryOut",
        "AdminBiometricOverrideRequest",
        "AdminBiometricOverrideOut",
    ]
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name in admin_classes:
            for item in ast.walk(node):
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    assert item.value not in forbidden, (
                        f"Forbidden field '{item.value}' in {node.name}"
                    )


# ── BCA-ADM-21 — EVT_ADMIN_OVERRIDE audit row no score ───────────────────────

def test_bca_adm21_override_audit_no_score(db, biometric_feature_enabled):
    admin = _make_admin(db, 21)
    student = _make_student(db, 21)
    _grant_disclosure(db, student)
    _grant_consent(db, student)
    _set_manual_review(db, student)

    _run(admin_override_biometric(
        user_id=student.id,
        payload=AdminBiometricOverrideRequest(decision="approved"),
        request=_mock_request(),
        db=db,
        current_admin=admin,
    ))
    db.commit()

    log = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.user_id == student.id,
        BiometricVerificationLog.event_type == EVT_ADMIN_OVERRIDE,
    ).first()
    assert log is not None
    assert log.face_match_score is None, "face_match_score must be NULL in override audit row"


# ── BCA-ADM-22 — route count 883 ─────────────────────────────────────────────

def test_bca_adm22_route_count_883():
    from app.main import app
    paths = app.openapi().get("paths", {})
    assert len(paths) == 890, f"Expected 890 routes (P4 private media +2), got {len(paths)}"
    assert "/api/v1/admin/biometric/review-queue" in paths
    assert "/api/v1/admin/biometric/{user_id}/history" in paths
    assert "/api/v1/admin/biometric/{user_id}/override" in paths