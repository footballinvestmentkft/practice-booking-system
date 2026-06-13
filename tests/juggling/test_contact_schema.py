"""
Juggling contact event schema tests — CM-01..CM-20.

CM-01  migration upgrade creates juggling_contact_events table
CM-02  migration downgrade removes table and restores juggling_videos
CM-03  video FK rejects unknown video_id
CM-04  created_by_user_id NOT NULL
CM-05  created_by_user_id RESTRICT blocks delete of creator
CM-06  user → video → event CASCADE delete chain works end-to-end
CM-07  duplicate device_event_id raises UniqueViolation
CM-08  invalid annotation_source rejected by check constraint
CM-09  invalid annotation_confidence rejected by check constraint
CM-10  invalid annotation_review_status rejected by check constraint
CM-11  invalid taxonomy_review_status rejected by check constraint
CM-12  negative timestamp_ms rejected by check constraint
CM-13  version < 1 rejected by check constraint
CM-14  model_confidence outside 0–1 rejected by check constraint
CM-15  soft delete (deleted_at set) preserves the row
CM-16  invalid annotation_status on juggling_videos rejected
CM-17  annotation_status defaults to NULL (not set) on new juggling_videos rows
CM-18  downgrade leaves no residual constraint/index from this migration
CM-19  production route count unchanged (no new endpoints)
CM-20  training consent dual-gate policy documented in migration file
"""
from __future__ import annotations

import pathlib
import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.database import engine
from app.models.juggling import (
    JugglingConsent,
    JugglingContactEvent,
    JugglingVideo,
    JugglingVideoStatus,
)
from app.models.user import User, UserRole

# ── Constants ─────────────────────────────────────────────────────────────────

_REV_HEAD    = "2026_06_13_1000"
_REV_PREV    = "2026_06_11_1100"

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "2026_06_13_1000_add_juggling_contact_events.py"
)


# ── Alembic helpers ───────────────────────────────────────────────────────────

def _cfg() -> Config:
    return Config("alembic.ini")


def _current_revision() -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _table_exists(name: str) -> bool:
    with engine.connect() as conn:
        return sa.inspect(conn).has_table(name)


def _columns(table: str) -> set[str]:
    with engine.connect() as conn:
        return {c["name"] for c in sa.inspect(conn).get_columns(table)}


def _index_names(table: str) -> set[str]:
    with engine.connect() as conn:
        return {idx["name"] for idx in sa.inspect(conn).get_indexes(table)}


def _constraint_names(table: str) -> set[str]:
    with engine.connect() as conn:
        insp = sa.inspect(conn)
        names: set[str] = set()
        for c in insp.get_check_constraints(table):
            if c.get("name"):
                names.add(c["name"])
        return names


# ── Transactional session fixture (SAVEPOINT isolation) ───────────────────────

@pytest.fixture()
def db():
    """
    Per-test transactional session with SAVEPOINT rollback.
    Full isolation — every test starts with a clean slate.
    """
    connection  = engine.connect()
    transaction = connection.begin()
    Session     = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session     = Session()
    connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, txn):
        if txn.nested and not txn._parent.nested:
            session.begin_nested()

    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def user(db):
    u = User(
        name="CM Test User",
        email=f"cm_test_{uuid.uuid4().hex[:8]}@test.com",
        password_hash="hashed",
        role=UserRole.STUDENT,
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture()
def video(db, user):
    v = JugglingVideo(
        id=uuid.uuid4(),
        user_id=user.id,
        source_type="uploaded_video",
        upload_source="gallery",
        status=JugglingVideoStatus.uploaded.value,
    )
    db.add(v)
    db.flush()
    return v


def _make_event(video_id, user_id, **overrides) -> dict:
    defaults = dict(
        id=uuid.uuid4(),
        video_id=video_id,
        created_by_user_id=user_id,
        device_event_id=uuid.uuid4(),
        timestamp_ms=1000,
        contact_type="foot_right_instep",
        annotation_confidence="certain",
        annotation_source="manual_user",
    )
    defaults.update(overrides)
    return defaults


# ── CM-01: migration upgrade ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _ensure_head():
    """Always restore DB to head after each migration test."""
    yield
    if _current_revision() != _REV_HEAD:
        command.upgrade(_cfg(), _REV_HEAD)


def test_cm01_upgrade_creates_table():
    """CM-01: After upgrade the juggling_contact_events table exists with required columns."""
    assert _table_exists("juggling_contact_events"), \
        "juggling_contact_events table missing after upgrade"
    cols = _columns("juggling_contact_events")
    required = {
        "id", "video_id", "created_by_user_id", "device_event_id",
        "timestamp_ms", "contact_type", "side", "annotation_confidence",
        "annotation_review_status", "taxonomy_review_status", "annotation_source",
        "excluded_from_training", "excluded_from_count",
        "model_confidence", "user_confirmed", "corrected_from_event_id",
        "custom_label", "custom_description", "taxonomy_version",
        "consent_snapshot", "note", "ball_height_approx_px",
        "version", "created_at", "updated_at", "deleted_at",
    }
    missing = required - cols
    assert not missing, f"Missing columns: {missing}"


# ── CM-02: migration downgrade ────────────────────────────────────────────────

def test_cm02_downgrade_removes_table():
    """CM-02: downgrade to 2026_06_11_1100 removes juggling_contact_events and annotation columns."""
    command.downgrade(_cfg(), _REV_PREV)
    assert not _table_exists("juggling_contact_events"), \
        "juggling_contact_events should not exist after downgrade"
    video_cols = _columns("juggling_videos")
    for col in ("annotation_status", "annotation_finished_at", "total_juggling_count"):
        assert col not in video_cols, f"{col} should be removed from juggling_videos after downgrade"
    assert _current_revision() == _REV_PREV


# ── CM-03: video FK ───────────────────────────────────────────────────────────

def test_cm03_invalid_video_fk(db, user):
    """CM-03: video FK rejects unknown video_id."""
    evt = JugglingContactEvent(**_make_event(uuid.uuid4(), user.id))
    db.add(evt)
    with pytest.raises(IntegrityError, match="fk_juggling_contact_events_video_id|foreign key"):
        db.flush()


# ── CM-04: created_by_user_id NOT NULL ───────────────────────────────────────

def test_cm04_created_by_user_id_not_null(db, video):
    """CM-04: created_by_user_id NOT NULL raises IntegrityError when omitted."""
    row = _make_event(video.id, video.user_id)
    del row["created_by_user_id"]
    evt = JugglingContactEvent(**row)
    evt.created_by_user_id = None
    db.add(evt)
    with pytest.raises(IntegrityError, match="not.null|null value"):
        db.flush()


# ── CM-05: created_by_user_id RESTRICT ───────────────────────────────────────

def test_cm05_created_by_user_id_restrict(db):
    """CM-05: deleting user who created an event is blocked by FK RESTRICT."""
    creator = User(
        name="Creator",
        email=f"creator_{uuid.uuid4().hex[:8]}@test.com",
        password_hash="hashed",
        role=UserRole.STUDENT,
    )
    db.add(creator)
    db.flush()

    owner = User(
        name="Owner",
        email=f"owner_{uuid.uuid4().hex[:8]}@test.com",
        password_hash="hashed",
        role=UserRole.STUDENT,
    )
    db.add(owner)
    db.flush()

    video = JugglingVideo(
        id=uuid.uuid4(),
        user_id=owner.id,
        source_type="uploaded_video",
        upload_source="gallery",
        status=JugglingVideoStatus.uploaded.value,
    )
    db.add(video)
    db.flush()

    evt = JugglingContactEvent(**_make_event(video.id, creator.id))
    db.add(evt)
    db.flush()

    db.delete(creator)
    with pytest.raises(IntegrityError, match="restrict|fk_juggling_contact_events_created_by_user_id"):
        db.flush()


# ── CM-06: user → video → event CASCADE ──────────────────────────────────────

def test_cm06_cascade_delete_chain(db):
    """CM-06: deleting user cascades through video to contact events."""
    u = User(
        name="Cascade User",
        email=f"cascade_{uuid.uuid4().hex[:8]}@test.com",
        password_hash="hashed",
        role=UserRole.STUDENT,
    )
    db.add(u)
    db.flush()

    v = JugglingVideo(
        id=uuid.uuid4(),
        user_id=u.id,
        source_type="uploaded_video",
        upload_source="gallery",
        status=JugglingVideoStatus.uploaded.value,
    )
    db.add(v)
    db.flush()

    evt = JugglingContactEvent(**_make_event(v.id, u.id))
    evt_id = evt.id
    db.add(evt)
    db.flush()

    db.delete(u)
    db.flush()

    remaining = db.query(JugglingContactEvent).filter_by(id=evt_id).first()
    assert remaining is None, "Contact event should be cascade-deleted with user"


# ── CM-07: duplicate device_event_id ─────────────────────────────────────────

def test_cm07_duplicate_device_event_id(db, video, user):
    """CM-07: same (video_id, device_event_id) raises unique violation."""
    shared_device_id = uuid.uuid4()
    e1 = JugglingContactEvent(**_make_event(video.id, user.id,
                                            device_event_id=shared_device_id))
    e2 = JugglingContactEvent(**_make_event(video.id, user.id,
                                            device_event_id=shared_device_id))
    db.add(e1)
    db.flush()
    db.add(e2)
    with pytest.raises(IntegrityError, match="uq_juggling_contact_device_event|unique"):
        db.flush()


# ── CM-08: invalid annotation_source ─────────────────────────────────────────

def test_cm08_invalid_annotation_source(db, video, user):
    """CM-08: bad annotation_source value violates check constraint."""
    evt = JugglingContactEvent(
        **_make_event(video.id, user.id, annotation_source="robot_guess")
    )
    db.add(evt)
    with pytest.raises(IntegrityError, match="ck_juggling_contact_annotation_source|check"):
        db.flush()


# ── CM-09: invalid annotation_confidence ─────────────────────────────────────

def test_cm09_invalid_annotation_confidence(db, video, user):
    """CM-09: bad annotation_confidence value violates check constraint."""
    evt = JugglingContactEvent(
        **_make_event(video.id, user.id, annotation_confidence="maybe")
    )
    db.add(evt)
    with pytest.raises(IntegrityError, match="ck_juggling_contact_annotation_confidence|check"):
        db.flush()


# ── CM-10: invalid annotation_review_status ───────────────────────────────────

def test_cm10_invalid_annotation_review_status(db, video, user):
    """CM-10: bad annotation_review_status violates check constraint."""
    evt = JugglingContactEvent(
        **_make_event(video.id, user.id, annotation_review_status="approved")
    )
    db.add(evt)
    with pytest.raises(IntegrityError, match="ck_juggling_contact_annotation_review_status|check"):
        db.flush()


# ── CM-11: invalid taxonomy_review_status ─────────────────────────────────────

def test_cm11_invalid_taxonomy_review_status(db, video, user):
    """CM-11: bad taxonomy_review_status violates check constraint."""
    evt = JugglingContactEvent(
        **_make_event(video.id, user.id, taxonomy_review_status="unknown_status")
    )
    db.add(evt)
    with pytest.raises(IntegrityError, match="ck_juggling_contact_taxonomy_review_status|check"):
        db.flush()


# ── CM-12: negative timestamp_ms ─────────────────────────────────────────────

def test_cm12_negative_timestamp_ms(db, video, user):
    """CM-12: negative timestamp_ms violates check constraint."""
    evt = JugglingContactEvent(
        **_make_event(video.id, user.id, timestamp_ms=-1)
    )
    db.add(evt)
    with pytest.raises(IntegrityError, match="ck_juggling_contact_timestamp_ms_nonneg|check"):
        db.flush()


# ── CM-13: version < 1 ────────────────────────────────────────────────────────

def test_cm13_version_lt_1(db, video, user):
    """CM-13: version=0 violates check constraint."""
    evt = JugglingContactEvent(
        **_make_event(video.id, user.id, version=0)
    )
    db.add(evt)
    with pytest.raises(IntegrityError, match="ck_juggling_contact_version_positive|check"):
        db.flush()


# ── CM-14: model_confidence outside 0–1 ──────────────────────────────────────

def test_cm14_model_confidence_out_of_range(db, video, user):
    """CM-14: model_confidence > 1 violates check constraint."""
    evt = JugglingContactEvent(
        **_make_event(video.id, user.id, model_confidence=1.5)
    )
    db.add(evt)
    with pytest.raises(IntegrityError, match="ck_juggling_contact_model_confidence_range|check"):
        db.flush()


# ── CM-15: soft delete preserves row ─────────────────────────────────────────

def test_cm15_soft_delete_preserves_row(db, video, user):
    """CM-15: setting deleted_at keeps the row in the table."""
    evt = JugglingContactEvent(**_make_event(video.id, user.id))
    db.add(evt)
    db.flush()

    evt.deleted_at = datetime.now(timezone.utc)
    db.flush()

    found = db.query(JugglingContactEvent).filter_by(id=evt.id).first()
    assert found is not None, "Row should still exist after soft delete"
    assert found.deleted_at is not None


# ── CM-16: invalid video annotation_status ────────────────────────────────────

def test_cm16_invalid_video_annotation_status(db, user):
    """CM-16: invalid annotation_status on juggling_videos is rejected."""
    v = JugglingVideo(
        id=uuid.uuid4(),
        user_id=user.id,
        source_type="uploaded_video",
        upload_source="gallery",
        status=JugglingVideoStatus.uploaded.value,
        annotation_status="INVALID_STATUS",
    )
    db.add(v)
    with pytest.raises(IntegrityError, match="ck_juggling_videos_annotation_status|check"):
        db.flush()


# ── CM-17: annotation_status NULL by default ──────────────────────────────────

def test_cm17_annotation_status_null_by_default(db, user):
    """CM-17: new juggling_videos rows have annotation_status=NULL."""
    v = JugglingVideo(
        id=uuid.uuid4(),
        user_id=user.id,
        source_type="uploaded_video",
        upload_source="gallery",
        status=JugglingVideoStatus.uploaded.value,
    )
    db.add(v)
    db.flush()
    db.refresh(v)
    assert v.annotation_status is None


# ── CM-18: downgrade leaves no residual constraints/indexes ──────────────────

def test_cm18_downgrade_no_residual_schema():
    """CM-18: after downgrade, no juggling contact constraints/indexes remain."""
    command.downgrade(_cfg(), _REV_PREV)
    assert not _table_exists("juggling_contact_events")

    video_constraints = _constraint_names("juggling_videos")
    assert "ck_juggling_videos_annotation_status" not in video_constraints

    video_indexes = _index_names("juggling_videos")
    assert "ix_juggling_videos_annotation_status" not in video_indexes


# ── CM-19: route count unchanged ─────────────────────────────────────────────

def test_cm19_production_route_count_unchanged():
    """CM-19: PR-1 introduces no new API routes."""
    from app.main import app
    routes = [r for r in app.routes if hasattr(r, "methods")]
    assert len(routes) == 1010, (
        f"Route count changed: expected 1010, got {len(routes)}. "
        "PR-1 must not introduce any new endpoints."
    )


# ── CM-20: dual-gate policy documented ───────────────────────────────────────

def test_cm20_dual_gate_policy_documented():
    """CM-20: migration file documents the training consent dual-gate invariant."""
    src = _MIGRATION_PATH.read_text()
    assert "consent_snapshot" in src, "consent_snapshot not mentioned in migration"
    assert "training_consent" in src, "training_consent not mentioned in migration"
    assert "dual" in src.lower() or "dual-gate" in src.lower(), \
        "dual-gate policy not documented in migration"
    assert "IMMUTABLE" in src or "immutable" in src.lower(), \
        "snapshot immutability not stated in migration"
    assert "JugglingConsent" in src or "current" in src.lower(), \
        "current consent check not referenced in migration"
