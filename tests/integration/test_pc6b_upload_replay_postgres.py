from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from sqlalchemy.orm import sessionmaker

from app.database import engine
from app.models.user import User, UserRole
from app.models.juggling import JugglingVideo
from app.services.juggling.video_service import claim_processing_once, create_pending


def test_concurrent_upload_init_is_exactly_once(test_db):
    SessionLocal = sessionmaker(bind=engine)
    setup_db = SessionLocal()
    user = User(
        name="PC6B Player", email=f"pc6b-{uuid4().hex}@example.com",
        password_hash="test", role=UserRole.STUDENT, is_active=True,
    )
    setup_db.add(user)
    setup_db.commit()
    user_id = user.id
    setup_db.close()
    key = f"capture-{uuid4()}"
    barrier = Barrier(2)

    def create():
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            return str(create_pending(
                user_id=user_id, source_type="in_app_capture", upload_source="camera",
                client_reported_metadata={"capture_version": "PC6B_V1"}, db=db,
                upload_idempotency_key=key,
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
