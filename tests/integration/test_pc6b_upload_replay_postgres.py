from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import engine
from app.models.user import User, UserRole
from app.models.juggling import JugglingCaptureAttempt, JugglingVideo
from app.services.juggling.capture_policy_service import (
    CapturePolicyError,
    bind_solo_upload,
    issue_solo_attempt,
)
from app.services.public_identity_service import (
    PublicIdentityPolicyError,
    require_public_display_identity,
)
from app.services.juggling.video_service import claim_processing_once, create_pending


def test_concurrent_canonical_solo_upload_init_is_exactly_once(test_db):
    SessionLocal = sessionmaker(bind=engine)
    setup_db = SessionLocal()
    user = User(
        name="PC6B Player", email=f"pc6b-{uuid4().hex}@example.com",
        password_hash="test", role=UserRole.STUDENT, is_active=True,
    )
    setup_db.add(user)
    setup_db.commit()
    user_id = user.id
    attempt_id = issue_solo_attempt(
        setup_db, user_id=user_id, request_idempotency_key=f"request-{uuid4()}",
    ).id
    setup_db.close()
    key = f"capture-{uuid4()}"
    barrier = Barrier(2)

    def create():
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            return str(bind_solo_upload(
                db, user_id=user_id, capture_attempt_id=attempt_id,
                client_reported_metadata={
                    "capture_version": "PC6B_V1", "camera_position": "front",
                }, upload_idempotency_key=key,
            ).id)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: create(), range(2)))

    verification_db = SessionLocal()
    try:
        assert ids[0] == ids[1]
        assert verification_db.query(JugglingVideo).filter_by(
            user_id=user_id, upload_idempotency_key=key,
        ).count() == 1
    finally:
        verification_db.query(JugglingVideo).filter_by(user_id=user_id).delete()
        verification_db.query(JugglingCaptureAttempt).filter_by(user_id=user_id).delete()
        verification_db.query(User).filter_by(id=user_id).delete()
        verification_db.commit()
        verification_db.close()


def test_concurrent_complete_claim_is_exactly_once(test_db):
    SessionLocal = sessionmaker(bind=engine)
    setup_db = SessionLocal()
    user = User(
        name="PC6B Complete Player", email=f"pc6b-complete-{uuid4().hex}@example.com",
        password_hash="test", role=UserRole.STUDENT, is_active=True,
    )
    setup_db.add(user)
    setup_db.flush()
    video = JugglingVideo(
        user_id=user.id, source_type="in_app_capture", upload_source="camera",
        status="uploaded", checksum_sha256="a" * 64,
    )
    setup_db.add(video)
    setup_db.commit()
    user_id, video_id = user.id, video.id
    setup_db.close()
    barrier = Barrier(2)

    def claim():
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            row, claimed = claim_processing_once(str(video_id), db)
            return str(row.id), claimed, row.status
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))

    verification_db = SessionLocal()
    try:
        assert {row[0] for row in results} == {str(video_id)}
        assert sorted(row[1] for row in results) == [False, True]
        assert {row[2] for row in results} == {"processing"}
        assert verification_db.get(JugglingVideo, video_id).status == "processing"
    finally:
        verification_db.query(JugglingVideo).filter_by(id=video_id).delete()
        verification_db.query(User).filter_by(id=user_id).delete()
        verification_db.commit()
        verification_db.close()


def test_generic_video_service_cannot_mint_canonical_capture(test_db):
    with pytest.raises(ValueError, match="CANONICAL_CAPTURE_ATTEMPT_REQUIRED"):
        create_pending(
            user_id=1, source_type="in_app_capture", upload_source="camera",
            client_reported_metadata={"camera_position": "front"}, db=test_db,
        )


def test_capture_attempt_is_bound_to_issuing_player(test_db):
    first = User(
        name="Attempt Owner", email=f"attempt-owner-{uuid4().hex}@example.com",
        password_hash="test", role=UserRole.STUDENT, is_active=True,
    )
    foreign = User(
        name="Foreign Player", email=f"attempt-foreign-{uuid4().hex}@example.com",
        password_hash="test", role=UserRole.STUDENT, is_active=True,
    )
    test_db.add_all([first, foreign])
    test_db.flush()
    attempt = issue_solo_attempt(
        test_db, user_id=first.id, request_idempotency_key=f"request-{uuid4()}",
    )

    with pytest.raises(CapturePolicyError, match="CAPTURE_ATTEMPT_NOT_FOUND"):
        bind_solo_upload(
            test_db, user_id=foreign.id, capture_attempt_id=attempt.id,
            upload_idempotency_key=f"upload-{uuid4()}",
            client_reported_metadata={"camera_position": "front"},
        )


def test_public_nickname_is_globally_case_insensitive_unique(test_db):
    first = User(
        name="Public One", nickname="PlayerOne",
        email=f"public-one-{uuid4().hex}@example.com", password_hash="test",
        role=UserRole.STUDENT, is_active=True,
    )
    test_db.add(first)
    test_db.flush()
    assert require_public_display_identity(test_db, first) == "PlayerOne"

    conflicting = User(
        name="Public Two", nickname="playerone",
        email=f"public-two-{uuid4().hex}@example.com", password_hash="test",
        role=UserRole.STUDENT, is_active=True,
    )
    test_db.add(conflicting)
    with pytest.raises(IntegrityError):
        test_db.flush()
    test_db.rollback()


def test_public_identity_has_no_personal_data_fallback(test_db):
    player = User(
        name="Legal Name Must Stay Private", nickname=None,
        email=f"private-{uuid4().hex}@example.com", password_hash="test",
        role=UserRole.STUDENT, is_active=True,
    )
    test_db.add(player)
    test_db.flush()
    with pytest.raises(PublicIdentityPolicyError, match="PUBLIC_DISPLAY_IDENTITY_REQUIRED"):
        require_public_display_identity(test_db, player)
