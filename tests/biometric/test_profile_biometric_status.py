"""
Profile biometric status — GET /me contract tests.

BPS-01  GET /me returns face_match_status=None when user has no biometric activity
BPS-02  GET /me returns face_match_status='reference_pending' after liveness submit
BPS-03  GET /me returns face_match_status='verified' after successful verify
BPS-04  GET /me response never contains face_match_score (privacy structural guarantee)
BPS-05  GET /me returns face_reference_photo_status='onboarding_liveness_capture' after liveness
BPS-06  Disclosed + consented user without verify: face_match_status='reference_pending'
         → iOS ProfileView must NOT show the primary registration CTA for this state

These tests validate the data contract that the iOS ProfileView biometricSection
relies on. When face_match_status is 'verified', the iOS UI must suppress the
primary "Biometrikus regisztráció" CTA and show "Biometrikus azonosítás aktív" instead.
"""
from __future__ import annotations

import ast
from datetime import date

import pytest

from app.api.api_v1.endpoints.users.profile import get_current_user_profile
from app.schemas.user import User as UserSchema


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize_me(db, user) -> dict:
    """Call GET /me and return the serialised dict."""
    result = get_current_user_profile(db=db, current_user=user)
    return UserSchema.model_validate(result).model_dump()


# ── BPS-01 — face_match_status=None when no biometric activity ────────────────

def test_bps01_no_biometric_activity_status_is_none(db, student_user):
    data = _serialize_me(db, student_user)
    assert "face_match_status" in data
    assert data["face_match_status"] is None


# ── BPS-02 — face_match_status='reference_pending' after liveness ─────────────

def test_bps02_reference_pending_after_liveness(db, student_user):
    student_user.face_match_status           = "reference_pending"
    student_user.face_reference_photo_status = "onboarding_liveness_capture"
    db.flush()

    data = _serialize_me(db, student_user)
    assert data["face_match_status"] == "reference_pending"


# ── BPS-03 — face_match_status='verified' after successful verify ─────────────

def test_bps03_verified_after_successful_verify(db, student_user):
    student_user.face_match_status           = "verified"
    student_user.face_reference_photo_status = "onboarding_liveness_capture"
    student_user.manual_review_required      = False
    db.flush()

    data = _serialize_me(db, student_user)
    assert data["face_match_status"] == "verified"


# ── BPS-04 — face_match_score NEVER in GET /me (privacy guarantee) ────────────

def test_bps04_face_match_score_absent_from_me_response(db, student_user):
    """
    face_match_score must never appear in /me — not even as null.
    Structural test: checks the Pydantic schema definition via AST.
    """
    import app.schemas.user as _schema_module
    source = ast.parse(ast.unparse(ast.parse(
        open(_schema_module.__file__).read()
    )))

    class_fields: list[str] = []
    for node in ast.walk(source):
        if isinstance(node, ast.ClassDef) and node.name == "User":
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    class_fields.append(item.target.id)

    assert "face_match_score" not in class_fields, (
        "face_match_score must not be a field on the User schema — "
        "it is stored in the audit log only and must never be returned in any API response."
    )

    # Also verify via runtime serialisation
    data = _serialize_me(db, student_user)
    assert "face_match_score" not in data


# ── BPS-05 — face_reference_photo_status in /me after liveness ───────────────

def test_bps05_face_reference_photo_status_in_me(db, student_user):
    student_user.face_match_status           = "reference_pending"
    student_user.face_reference_photo_status = "onboarding_liveness_capture"
    db.flush()

    data = _serialize_me(db, student_user)
    assert data["face_reference_photo_status"] == "onboarding_liveness_capture"


# ── BPS-06 — reference_pending state: UI contract test ───────────────────────

def test_bps07_null_face_match_with_onboarding_photo_status_impossible_in_flow(db, student_user):
    """
    Invariant: face_match_status=NULL + face_reference_photo_status='onboarding_liveness_capture'
    cannot be produced by any normal code path.

    liveness_service.py sets BOTH fields atomically (lines 110-111):
      user.face_reference_photo_status = "onboarding_liveness_capture"   # line 110
      user.face_match_status           = "reference_pending"              # line 111

    sandbox biometric_reset nulls BOTH fields together (lines 153-154).
    No code path nulls face_match_status without also nulling face_reference_photo_status.

    This test verifies: no live user currently has this inconsistent state.
    It also documents the invariant for future code reviewers.
    """
    from sqlalchemy import text
    from app.database import engine
    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT COUNT(*) FROM users "
            "WHERE face_match_status IS NULL "
            "  AND face_reference_photo_status = 'onboarding_liveness_capture'"
        ))
        count = result.scalar()
    assert count == 0, (
        f"Found {count} user(s) with face_match_status=NULL and "
        "face_reference_photo_status='onboarding_liveness_capture'. "
        "This state cannot arise from normal flow and indicates DB inconsistency "
        "or a missing atomicity guarantee. "
        "Fix: ensure any admin endpoint that resets face_match_status also resets "
        "face_reference_photo_status in the same transaction."
    )


def test_bps06_reference_pending_ui_contract(db, student_user):
    """
    When face_match_status='reference_pending', the user has completed the
    liveness flow but no verify result exists yet.

    iOS contract: this state must show 'Biometrikus regisztráció folyamatban'
    (not the primary registration CTA), so the user is not misled into
    re-starting the flow.

    Validated here at the data layer: the field IS present and has the expected
    value that the iOS BiometricRegistrationState.inProgress case matches on.
    """
    student_user.face_match_status = "reference_pending"
    db.flush()

    data = _serialize_me(db, student_user)
    status = data["face_match_status"]

    # The iOS ProfileView considers reference_pending as 'inProgress' —
    # primary CTA must be suppressed in this state.
    assert status == "reference_pending"
    assert status != "verified"
    assert status != "rejected"
