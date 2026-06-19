"""
Global Ball Training Hub tests — BTH-01..BTH-18 + BTH-12B + BTH-CC-1 + BTH-CC-2.

Coverage:
  BTH-01   Queue returns assignment_id only — no video_id, frame_ms, storage_path
  BTH-02   assignment_id is a valid UUID
  BTH-03   Cross-user assignment access → 404 (no info-leak)
  BTH-04   Expired assignment submit → 410
  BTH-05   Consumed assignment (second submit) → 409
  BTH-06   Non-existent assignment UUID → 404
  BTH-07   Valid confirm submit → 201, feedback row created, assignment consumed
  BTH-08   Valid no_ball submit → 201, correct decision persisted
  BTH-09   corrected decision → 422 (deferred to PR-1B)
  BTH-10   Queue excludes frames without training_consent (consent gate)
  BTH-11   Queue excludes user's own video frames
  BTH-12   Idempotent assignment creation — service-level advisory lock
  BTH-12B  Endpoint-level queue idempotency — GET × 2 returns same assignment_ids
  BTH-13   Frame capacity: 4 sequential submits, 4 different users → 3 succeed, 1 gets 409
  BTH-14   Consent revoke after assignment issued → submit returns 403
  BTH-15   Consensus task skipped when training_consent revoked
  BTH-16   Feature flag off → 503
  BTH-17   Non-allowlisted student → 403
  BTH-18   Allowlisted student → 200
  BTH-CC-1 Real concurrent frame-capacity: 4 sessions × same frame → 3×201 + 1×409
  BTH-CC-2 Real concurrent same-assignment: 2 sessions × same BTA → 1×201 + 1×409

BTH-01..BTH-18 + BTH-12B: FastAPI TestClient + PostgreSQL savepoint (full DB isolation).
BTH-CC-1, BTH-CC-2: real PostgreSQL concurrency via ThreadPoolExecutor + SessionLocal.
"""
from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete as sa_delete, event as sa_event, select
from sqlalchemy.orm import sessionmaker

from app.core.auth import create_access_token
from app.database import SessionLocal, engine, get_db
from app.main import app
from app.models.juggling import (
    BallTrainingAssignment,
    JugglingBallFeedback,
    JugglingBallTrajectory,
    JugglingConsent,
    JugglingFrameGroundTruth,
    JugglingVideo,
    JugglingVideoStatus,
    UserAnnotationReliability,
)
from app.models.user import User, UserRole
from app.services.juggling import feature_flag as ff_module
import app.api.api_v1.endpoints.users.juggling_ball_training as hub_module
from app.tasks.juggling_feedback_task import run_compute_frame_consensus


# ── DB fixture (savepoint pattern) ───────────────────────────────────────────

@pytest.fixture()
def db():
    connection = engine.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = TestSession()
    connection.begin_nested()

    @sa_event.listens_for(session, "after_transaction_end")
    def restart_savepoint(sess, txn):
        if txn.nested and not txn._parent.nested:
            sess.begin_nested()

    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_user(db, suffix: str = "", role: UserRole = UserRole.STUDENT) -> User:
    u = User(
        email=f"bth_{suffix}_{uuid.uuid4().hex[:6]}@test.com",
        name="BTH User",
        password_hash="x",
        role=role,
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _make_video(db, owner: User, status: str = "analyzed") -> JugglingVideo:
    v = JugglingVideo(
        user_id=owner.id,
        source_type="in_app_capture",
        upload_source="camera",
        status=status,
        storage_path=f"/tmp/v_{uuid.uuid4().hex}.mp4",
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


def _make_consent(db, user: User, training: bool = True) -> JugglingConsent:
    c = JugglingConsent(
        user_id=user.id,
        service_consent=True,
        training_consent=training,
        admin_review_consent=False,
        consented_at=datetime.now(timezone.utc),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _make_trajectory(
    db,
    video: JugglingVideo,
    frame_ms: int = 1000,
    confidence: float = 0.5,
    tracking_state: str = "detected",
) -> JugglingBallTrajectory:
    t = JugglingBallTrajectory(
        video_id=video.id,
        frame_ms=frame_ms,
        ball_x=0.5,
        ball_y=0.4,
        confidence=confidence,
        tracking_state=tracking_state,
        image_width_px=1920,
        image_height_px=1080,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _make_assignment(
    db,
    user: User,
    video: JugglingVideo,
    frame_ms: int = 1000,
    expires_delta: timedelta = timedelta(hours=1),
    consumed_at: datetime | None = None,
) -> BallTrainingAssignment:
    now = datetime.now(timezone.utc)
    a = BallTrainingAssignment(
        user_id=user.id,
        video_id=video.id,
        frame_ms=frame_ms,
        expires_at=now + expires_delta,
        consumed_at=consumed_at,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _auth(user: User) -> dict:
    token = create_access_token(data={"sub": user.email})
    return {"Authorization": f"Bearer {token}"}


def _flags_on(monkeypatch):
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: True)
    monkeypatch.setattr(hub_module.settings, "BALL_FEEDBACK_ENABLED", True)


# ── BTH-01: Queue response contains no privacy-sensitive fields ───────────────

def test_bth_01_queue_response_no_private_fields(db, client, monkeypatch):
    _flags_on(monkeypatch)
    reviewer = _make_user(db, "reviewer", UserRole.ADMIN)
    owner = _make_user(db, "owner")
    video = _make_video(db, owner)
    _make_consent(db, owner, training=True)
    _make_trajectory(db, video, frame_ms=1000, confidence=0.4)

    resp = client.get(
        "/api/v1/users/me/ball-training/queue",
        headers=_auth(reviewer),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "tasks" in data

    for task in data["tasks"]:
        assert "video_id" not in task
        assert "frame_ms" not in task
        assert "storage_path" not in task
        assert "assignment_id" in task


# ── BTH-02: assignment_id is a valid UUID ─────────────────────────────────────

def test_bth_02_assignment_id_is_uuid(db, client, monkeypatch):
    _flags_on(monkeypatch)
    reviewer = _make_user(db, "rev2", UserRole.ADMIN)
    owner = _make_user(db, "ow2")
    video = _make_video(db, owner)
    _make_consent(db, owner)
    _make_trajectory(db, video, frame_ms=2000)

    resp = client.get("/api/v1/users/me/ball-training/queue", headers=_auth(reviewer))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    for task in data["tasks"]:
        aid = task["assignment_id"]
        parsed = uuid.UUID(aid)
        assert parsed.version == 4


# ── BTH-03: Cross-user assignment → 404 (no info-leak) ───────────────────────

def test_bth_03_cross_user_assignment_404(db, client, monkeypatch):
    _flags_on(monkeypatch)
    user_a = _make_user(db, "a3", UserRole.ADMIN)
    user_b = _make_user(db, "b3", UserRole.ADMIN)
    owner = _make_user(db, "ow3")
    video = _make_video(db, owner)
    _make_consent(db, owner)
    _make_trajectory(db, video, frame_ms=3000)

    # Create assignment belonging to user_a
    assignment = _make_assignment(db, user_a, video, frame_ms=3000)

    # user_b tries to submit with user_a's assignment_id
    resp = client.post(
        "/api/v1/users/me/ball-training/feedback",
        json={"assignment_id": str(assignment.id), "decision": "confirm"},
        headers=_auth(user_b),
    )
    assert resp.status_code == 404
    # Response must not reveal ownership info
    assert "user" not in resp.text.lower() or "not found" in resp.text.lower()


# ── BTH-04: Expired assignment → 410 ─────────────────────────────────────────

def test_bth_04_expired_assignment_410(db, client, monkeypatch):
    _flags_on(monkeypatch)
    user = _make_user(db, "u4", UserRole.ADMIN)
    owner = _make_user(db, "ow4")
    video = _make_video(db, owner)
    _make_consent(db, owner)
    _make_trajectory(db, video, frame_ms=4000)

    assignment = _make_assignment(
        db, user, video, frame_ms=4000,
        expires_delta=timedelta(seconds=-1),  # already expired
    )

    resp = client.post(
        "/api/v1/users/me/ball-training/feedback",
        json={"assignment_id": str(assignment.id), "decision": "confirm"},
        headers=_auth(user),
    )
    assert resp.status_code == 410


# ── BTH-05: Second submit on same assignment → 409 ───────────────────────────

def test_bth_05_consumed_assignment_409(db, client, monkeypatch):
    _flags_on(monkeypatch)
    user = _make_user(db, "u5", UserRole.ADMIN)
    owner = _make_user(db, "ow5")
    video = _make_video(db, owner)
    _make_consent(db, owner)
    _make_trajectory(db, video, frame_ms=5000)

    assignment = _make_assignment(
        db, user, video, frame_ms=5000,
        consumed_at=datetime.now(timezone.utc),  # already consumed
    )

    resp = client.post(
        "/api/v1/users/me/ball-training/feedback",
        json={"assignment_id": str(assignment.id), "decision": "no_ball"},
        headers=_auth(user),
    )
    assert resp.status_code == 409


# ── BTH-06: Non-existent assignment_id → 404 ─────────────────────────────────

def test_bth_06_nonexistent_assignment_404(db, client, monkeypatch):
    _flags_on(monkeypatch)
    user = _make_user(db, "u6", UserRole.ADMIN)

    resp = client.post(
        "/api/v1/users/me/ball-training/feedback",
        json={"assignment_id": str(uuid.uuid4()), "decision": "confirm"},
        headers=_auth(user),
    )
    assert resp.status_code == 404


# ── BTH-07: Valid confirm submit → 201, feedback persisted, assignment consumed

def test_bth_07_valid_confirm_submit(db, client, monkeypatch):
    _flags_on(monkeypatch)
    user = _make_user(db, "u7", UserRole.ADMIN)
    owner = _make_user(db, "ow7")
    video = _make_video(db, owner)
    _make_consent(db, owner)
    _make_trajectory(db, video, frame_ms=7000)

    assignment = _make_assignment(db, user, video, frame_ms=7000)

    resp = client.post(
        "/api/v1/users/me/ball-training/feedback",
        json={"assignment_id": str(assignment.id), "decision": "confirm"},
        headers=_auth(user),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["decision"] == "confirm"
    assert str(data["assignment_id"]) == str(assignment.id)

    # Verify feedback row created
    fb = db.execute(
        select(JugglingBallFeedback).where(
            JugglingBallFeedback.video_id == video.id,
            JugglingBallFeedback.frame_ms == 7000,
            JugglingBallFeedback.user_id == user.id,
        )
    ).scalar_one_or_none()
    assert fb is not None
    assert fb.decision == "confirm"

    # Verify assignment consumed
    db.refresh(assignment)
    assert assignment.consumed_at is not None


# ── BTH-08: Valid no_ball submit → 201, correct decision ─────────────────────

def test_bth_08_valid_no_ball_submit(db, client, monkeypatch):
    _flags_on(monkeypatch)
    user = _make_user(db, "u8", UserRole.ADMIN)
    owner = _make_user(db, "ow8")
    video = _make_video(db, owner)
    _make_consent(db, owner)
    _make_trajectory(db, video, frame_ms=8000)

    assignment = _make_assignment(db, user, video, frame_ms=8000)

    resp = client.post(
        "/api/v1/users/me/ball-training/feedback",
        json={"assignment_id": str(assignment.id), "decision": "no_ball"},
        headers=_auth(user),
    )
    assert resp.status_code == 201
    assert resp.json()["decision"] == "no_ball"


# ── BTH-09: corrected decision → 422 (deferred to PR-1B) ────────────────────

def test_bth_09_corrected_decision_422(db, client, monkeypatch):
    _flags_on(monkeypatch)
    user = _make_user(db, "u9", UserRole.ADMIN)
    owner = _make_user(db, "ow9")
    video = _make_video(db, owner)
    _make_consent(db, owner)
    _make_trajectory(db, video, frame_ms=9000)
    assignment = _make_assignment(db, user, video, frame_ms=9000)

    resp = client.post(
        "/api/v1/users/me/ball-training/feedback",
        json={
            "assignment_id": str(assignment.id),
            "decision": "corrected",
            "corrected_x": 0.5,
            "corrected_y": 0.4,
        },
        headers=_auth(user),
    )
    assert resp.status_code == 422


# ── BTH-10: Queue excludes frames without training_consent ───────────────────

def test_bth_10_queue_excludes_no_consent(db, client, monkeypatch):
    _flags_on(monkeypatch)
    reviewer = _make_user(db, "rev10", UserRole.ADMIN)
    owner_no = _make_user(db, "ow10_no")
    owner_yes = _make_user(db, "ow10_yes")

    video_no = _make_video(db, owner_no)
    video_yes = _make_video(db, owner_yes)

    _make_consent(db, owner_no, training=False)
    _make_consent(db, owner_yes, training=True)

    _make_trajectory(db, video_no, frame_ms=10001, confidence=0.1)  # high priority
    _make_trajectory(db, video_yes, frame_ms=10002, confidence=0.9)  # lower priority

    resp = client.get("/api/v1/users/me/ball-training/queue", headers=_auth(reviewer))
    assert resp.status_code == 200, resp.text

    # Verify: no assignment was created for video_no's frame (non-consented).
    # We check the DB directly — the client-visible response never contains video_id.
    bad_assignment = db.execute(
        select(BallTrainingAssignment).where(
            BallTrainingAssignment.user_id == reviewer.id,
            BallTrainingAssignment.video_id == video_no.id,
            BallTrainingAssignment.consumed_at.is_(None),
        )
    ).scalar_one_or_none()
    assert bad_assignment is None, (
        "Queue created an assignment for a non-consented video"
    )


# ── BTH-11: Queue excludes user's own video frames ───────────────────────────

def test_bth_11_queue_excludes_own_videos(db, client, monkeypatch):
    _flags_on(monkeypatch)
    user = _make_user(db, "u11", UserRole.ADMIN)
    other = _make_user(db, "ow11")

    own_video = _make_video(db, user)
    other_video = _make_video(db, other)

    _make_consent(db, user, training=True)
    _make_consent(db, other, training=True)

    _make_trajectory(db, own_video, frame_ms=11001, confidence=0.1)
    _make_trajectory(db, other_video, frame_ms=11002, confidence=0.9)

    resp = client.get("/api/v1/users/me/ball-training/queue", headers=_auth(user))
    assert resp.status_code == 200, resp.text

    # Verify: no assignment was created for the user's own video's frame.
    bad = db.execute(
        select(BallTrainingAssignment).where(
            BallTrainingAssignment.user_id == user.id,
            BallTrainingAssignment.video_id == own_video.id,
            BallTrainingAssignment.consumed_at.is_(None),
        )
    ).scalar_one_or_none()
    assert bad is None, "Queue created an assignment for the user's own video"


# ── BTH-12: Idempotent assignment creation — same UUID returned on repeat call ─

def test_bth_12_idempotent_assignment_creation(db):
    """Two calls to _get_or_create_assignment for the same (user, video, frame)
    return the identical assignment_id. Exactly one active row exists in the DB.

    Tests the advisory-lock + check-before-create mechanism directly at the
    service level, independent of queue priority ordering.
    """
    from app.services.juggling.ball_training_service import _get_or_create_assignment

    reviewer = _make_user(db, "rev12")
    owner = _make_user(db, "ow12")
    video = _make_video(db, owner)

    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=1)

    a1 = _get_or_create_assignment(db, reviewer.id, video.id, 12000, expires, now)
    db.flush()

    a2 = _get_or_create_assignment(db, reviewer.id, video.id, 12000, expires, now)

    assert a1.id == a2.id, (
        f"Idempotency violation: same frame returned different IDs: {a1.id} != {a2.id}"
    )

    active = db.execute(
        select(BallTrainingAssignment).where(
            BallTrainingAssignment.user_id == reviewer.id,
            BallTrainingAssignment.video_id == video.id,
            BallTrainingAssignment.frame_ms == 12000,
            BallTrainingAssignment.consumed_at.is_(None),
        )
    ).scalars().all()
    assert len(active) == 1, f"Expected 1 active assignment, found {len(active)}"


# ── BTH-12B: Endpoint-level queue idempotency ─────────────────────────────────

def test_bth_12b_endpoint_queue_idempotent(db, client, monkeypatch):
    """GET /me/ball-training/queue called twice by the same user returns the
    same set of assignment_ids. The second call must not create duplicate
    BallTrainingAssignment rows in the DB.

    Idempotency is verified by:
      1. Both calls return identical assignment_id sets.
      2. The total number of active BTAs for the reviewer equals the number of
         tasks returned (no extra rows created by the second call).
      3. All returned assignments are active (consumed_at IS NULL).

    reviewer_id is stored before the HTTP calls to avoid lazy-loading expired
    ORM objects after the service's db.commit() calls.
    """
    _flags_on(monkeypatch)
    reviewer = _make_user(db, "rev12b", UserRole.ADMIN)
    owner = _make_user(db, "ow12b")
    video = _make_video(db, owner)
    _make_consent(db, owner, training=True)
    _make_trajectory(db, video, frame_ms=12100, confidence=0.0)

    # Cache PK before HTTP calls expire ORM objects via db.commit()
    reviewer_id: int = reviewer.id

    resp1 = client.get("/api/v1/users/me/ball-training/queue", headers=_auth(reviewer))
    assert resp1.status_code == 200, resp1.text
    ids1 = {t["assignment_id"] for t in resp1.json()["tasks"]}
    assert ids1, "First queue call returned no tasks"

    resp2 = client.get("/api/v1/users/me/ball-training/queue", headers=_auth(reviewer))
    assert resp2.status_code == 200, resp2.text
    ids2 = {t["assignment_id"] for t in resp2.json()["tasks"]}

    # Invariant 1: same assignment_ids on both calls.
    assert ids1 == ids2, (
        f"Endpoint idempotency violated: call 1={ids1}, call 2={ids2}"
    )

    # Invariant 2: each returned assignment_id maps to exactly one active BTA row.
    # (If call 2 created duplicates, the count would exceed len(ids1).)
    all_active = db.execute(
        select(BallTrainingAssignment).where(
            BallTrainingAssignment.user_id == reviewer_id,
            BallTrainingAssignment.consumed_at.is_(None),
        )
    ).scalars().all()
    assert len(all_active) == len(ids1), (
        f"Duplicate BTAs detected: {len(all_active)} active rows for "
        f"{len(ids1)} returned tasks (reviewer_id={reviewer_id})"
    )

    # Invariant 3: the returned IDs match the active rows exactly.
    db_ids = {str(row.id) for row in all_active}
    assert db_ids == ids1, (
        f"Returned assignment_ids do not match active DB rows: "
        f"response={ids1}, db={db_ids}"
    )


# ── BTH-13: Frame capacity — 4 sequential submits, 4 users → 3 succeed ───────

def test_bth_13_frame_capacity_three_max(db, client, monkeypatch):
    _flags_on(monkeypatch)
    owner = _make_user(db, "ow13")
    video = _make_video(db, owner)
    _make_consent(db, owner)
    _make_trajectory(db, video, frame_ms=13000)

    users = [_make_user(db, f"u13_{i}", UserRole.ADMIN) for i in range(4)]
    assignments = [_make_assignment(db, u, video, frame_ms=13000) for u in users]

    results = []
    for user, assignment in zip(users, assignments):
        resp = client.post(
            "/api/v1/users/me/ball-training/feedback",
            json={"assignment_id": str(assignment.id), "decision": "confirm"},
            headers=_auth(user),
        )
        results.append(resp.status_code)

    success_count = results.count(201)
    conflict_count = results.count(409)

    assert success_count == 3, f"Expected 3 successes, got {results}"
    assert conflict_count == 1, f"Expected 1 conflict (409), got {results}"

    # Verify exactly 3 feedback rows in DB
    total_fb = db.execute(
        select(JugglingBallFeedback).where(
            JugglingBallFeedback.video_id == video.id,
            JugglingBallFeedback.frame_ms == 13000,
        )
    ).scalars().all()
    assert len(total_fb) == 3, f"Expected 3 feedback rows, found {len(total_fb)}"


# ── BTH-14: Consent revoke after assignment issued → 403 ─────────────────────

def test_bth_14_consent_revoke_blocks_submit(db, client, monkeypatch):
    _flags_on(monkeypatch)
    user = _make_user(db, "u14", UserRole.ADMIN)
    owner = _make_user(db, "ow14")
    video = _make_video(db, owner)
    consent = _make_consent(db, owner, training=True)
    _make_trajectory(db, video, frame_ms=14000)

    assignment = _make_assignment(db, user, video, frame_ms=14000)

    # Revoke consent after assignment was issued
    consent.training_consent = False
    db.commit()

    resp = client.post(
        "/api/v1/users/me/ball-training/feedback",
        json={"assignment_id": str(assignment.id), "decision": "confirm"},
        headers=_auth(user),
    )
    assert resp.status_code == 403

    # Assignment must NOT be consumed (so user can retry if consent is re-granted)
    db.refresh(assignment)
    assert assignment.consumed_at is None


# ── BTH-15: Consensus skipped when training_consent revoked ──────────────────

def test_bth_15_consensus_skipped_on_revoke(db):
    owner = _make_user(db, "ow15")
    video = _make_video(db, owner)
    consent = _make_consent(db, owner, training=True)
    _make_trajectory(db, video, frame_ms=15000)

    # Insert a feedback row directly (simulating prior submission)
    fb = JugglingBallFeedback(
        video_id=video.id,
        frame_ms=15000,
        user_id=owner.id,
        decision="confirm",
        approval_state="pending",
        spam_flags=[],
    )
    db.add(fb)
    db.commit()

    # Revoke consent before running consensus
    consent.training_consent = False
    db.commit()

    # run_compute_frame_consensus should bail out without creating a GT row
    run_compute_frame_consensus(db, str(video.id), 15000)

    gt = db.execute(
        select(JugglingFrameGroundTruth).where(
            JugglingFrameGroundTruth.video_id == video.id,
            JugglingFrameGroundTruth.frame_ms == 15000,
        )
    ).scalar_one_or_none()
    assert gt is None, "GT row must not be created when training_consent is revoked"


# ── BTH-16: Feature flag off → 503 ───────────────────────────────────────────

def test_bth_16_flag_off_503(db, client, monkeypatch):
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: True)
    monkeypatch.setattr(hub_module.settings, "BALL_FEEDBACK_ENABLED", False)

    user = _make_user(db, "u16", UserRole.ADMIN)
    resp = client.get("/api/v1/users/me/ball-training/queue", headers=_auth(user))
    assert resp.status_code == 503


# ── BTH-17: Non-allowlisted student → 403 ────────────────────────────────────

def test_bth_17_non_allowlisted_student_403(db, client, monkeypatch):
    _flags_on(monkeypatch)
    monkeypatch.setattr(hub_module.settings, "BALL_TRAINING_ALLOWED_USER_IDS", "")

    student = _make_user(db, "stu17", UserRole.STUDENT)
    resp = client.get("/api/v1/users/me/ball-training/queue", headers=_auth(student))
    assert resp.status_code == 403


# ── BTH-18: Allowlisted student → 200 ────────────────────────────────────────

def test_bth_18_allowlisted_student_200(db, client, monkeypatch):
    _flags_on(monkeypatch)
    owner = _make_user(db, "ow18")
    student = _make_user(db, "stu18", UserRole.STUDENT)
    _make_consent(db, owner)
    video = _make_video(db, owner)
    _make_trajectory(db, video, frame_ms=18000)

    monkeypatch.setattr(
        hub_module.settings,
        "BALL_TRAINING_ALLOWED_USER_IDS",
        str(student.id),
    )

    resp = client.get("/api/v1/users/me/ball-training/queue", headers=_auth(student))
    assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# Real-concurrency tests (BTH-CC-*)
#
# These tests bypass the savepoint fixture and use real PostgreSQL transactions
# with separate SessionLocal() connections so that advisory locks and FOR UPDATE
# row locks operate as they would in production. Data committed during these
# tests is explicitly cleaned up in a finally block.
# ══════════════════════════════════════════════════════════════════════════════

def _cc_cleanup(video_id: uuid.UUID, all_user_ids: list[int]) -> None:
    """Delete all CC test data from the real DB in FK-safe order."""
    db = SessionLocal()
    try:
        db.execute(sa_delete(JugglingBallFeedback).where(
            JugglingBallFeedback.video_id == video_id
        ))
        db.execute(sa_delete(BallTrainingAssignment).where(
            BallTrainingAssignment.video_id == video_id
        ))
        db.execute(sa_delete(JugglingBallTrajectory).where(
            JugglingBallTrajectory.video_id == video_id
        ))
        db.execute(sa_delete(JugglingConsent).where(
            JugglingConsent.user_id.in_(all_user_ids)
        ))
        db.execute(sa_delete(UserAnnotationReliability).where(
            UserAnnotationReliability.user_id.in_(all_user_ids)
        ))
        db.execute(sa_delete(JugglingVideo).where(JugglingVideo.id == video_id))
        db.execute(sa_delete(User).where(User.id.in_(all_user_ids)))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── BTH-CC-1: Real concurrent frame-capacity ─────────────────────────────────

def test_bth_cc1_concurrent_frame_capacity():
    """Real PostgreSQL concurrency test: 4 users submit feedback for the same
    frame simultaneously via separate DB sessions.

    Locking invariant under test: JugglingBallTrajectory FOR UPDATE serialises
    the fb_count recount so that exactly 3 succeed and 1 is rejected (409).
    Expected: exactly 3 × 201, exactly 1 × 409.
    DB post-condition: exactly 3 non-spam JugglingBallFeedback rows.
    """
    from app.schemas.juggling import BallTrainingFeedbackRequest
    from app.services.juggling.ball_training_service import submit_training_feedback

    _CC1_FRAME = 99001

    setup_db = SessionLocal()
    try:
        owner = _make_user(setup_db, "cc1o")
        video = _make_video(setup_db, owner)
        _make_consent(setup_db, owner, training=True)
        _make_trajectory(setup_db, video, frame_ms=_CC1_FRAME, confidence=0.5)
        reviewers = [_make_user(setup_db, f"cc1u{i}", UserRole.ADMIN) for i in range(4)]
        assignments = [
            _make_assignment(setup_db, u, video, frame_ms=_CC1_FRAME)
            for u in reviewers
        ]
        video_id = video.id
        reviewer_ids = [u.id for u in reviewers]
        assignment_ids = [a.id for a in assignments]
        all_user_ids = [owner.id] + reviewer_ids
    finally:
        setup_db.close()

    results: list[int] = []
    barrier = threading.Barrier(4)

    def _submit(uid: int, aid: uuid.UUID) -> int:
        db = SessionLocal()
        try:
            barrier.wait(timeout=15)
            req = BallTrainingFeedbackRequest(assignment_id=aid, decision="confirm")
            submit_training_feedback(db, uid, req)
            return 201
        except HTTPException as exc:
            return exc.status_code
        except threading.BrokenBarrierError:
            return 500
        except Exception:
            return 500
        finally:
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(_submit, reviewer_ids, assignment_ids))

        ok = results.count(201)
        conflict = results.count(409)
        assert ok == 3, (
            f"Expected 3 × 201 (frame capacity), got {results}"
        )
        assert conflict == 1, (
            f"Expected 1 × 409 (capacity exceeded), got {results}"
        )

        verify_db = SessionLocal()
        try:
            fb_rows = verify_db.execute(
                select(JugglingBallFeedback).where(
                    JugglingBallFeedback.video_id == video_id,
                    JugglingBallFeedback.frame_ms == _CC1_FRAME,
                    JugglingBallFeedback.approval_state != "spam",
                )
            ).scalars().all()
            assert len(fb_rows) == 3, (
                f"Expected 3 non-spam feedback rows in DB, found {len(fb_rows)}"
            )
        finally:
            verify_db.close()

    finally:
        _cc_cleanup(video_id, all_user_ids)


# ── BTH-CC-2: Real concurrent same-assignment submit ─────────────────────────

def test_bth_cc2_concurrent_same_assignment_submit():
    """Real PostgreSQL concurrency test: the same user submits the same
    assignment from two separate DB sessions simultaneously.

    Locking invariant under test: BallTrainingAssignment FOR UPDATE serialises
    the consumed_at check so that exactly one submit succeeds (201) and the
    other is rejected (409, 'Assignment already submitted').
    DB post-condition: exactly 1 JugglingBallFeedback row, consumed_at set once.
    """
    from app.schemas.juggling import BallTrainingFeedbackRequest
    from app.services.juggling.ball_training_service import submit_training_feedback

    _CC2_FRAME = 99002

    setup_db = SessionLocal()
    try:
        owner = _make_user(setup_db, "cc2o")
        video = _make_video(setup_db, owner)
        _make_consent(setup_db, owner, training=True)
        _make_trajectory(setup_db, video, frame_ms=_CC2_FRAME, confidence=0.5)
        reviewer = _make_user(setup_db, "cc2r", UserRole.ADMIN)
        assignment = _make_assignment(setup_db, reviewer, video, frame_ms=_CC2_FRAME)
        video_id = video.id
        assignment_id = assignment.id
        reviewer_id = reviewer.id
        all_user_ids = [owner.id, reviewer.id]
    finally:
        setup_db.close()

    results: list[int] = []
    barrier = threading.Barrier(2)

    def _submit() -> int:
        db = SessionLocal()
        try:
            barrier.wait(timeout=15)
            req = BallTrainingFeedbackRequest(
                assignment_id=assignment_id, decision="confirm"
            )
            submit_training_feedback(db, reviewer_id, req)
            return 201
        except HTTPException as exc:
            return exc.status_code
        except threading.BrokenBarrierError:
            return 500
        except Exception:
            return 500
        finally:
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_submit) for _ in range(2)]
            results = [f.result() for f in futures]

        ok = results.count(201)
        conflict = results.count(409)
        assert ok == 1, (
            f"Expected 1 × 201 (one successful submit), got {results}"
        )
        assert conflict == 1, (
            f"Expected 1 × 409 (duplicate assignment submit), got {results}"
        )

        verify_db = SessionLocal()
        try:
            fb_rows = verify_db.execute(
                select(JugglingBallFeedback).where(
                    JugglingBallFeedback.video_id == video_id,
                    JugglingBallFeedback.frame_ms == _CC2_FRAME,
                    JugglingBallFeedback.user_id == reviewer_id,
                )
            ).scalars().all()
            assert len(fb_rows) == 1, (
                f"Expected exactly 1 feedback row, found {len(fb_rows)}"
            )

            bta = verify_db.execute(
                select(BallTrainingAssignment).where(
                    BallTrainingAssignment.id == assignment_id
                )
            ).scalar_one()
            assert bta.consumed_at is not None, (
                "BallTrainingAssignment.consumed_at must be set after successful submit"
            )
        finally:
            verify_db.close()

    finally:
        _cc_cleanup(video_id, all_user_ids)
