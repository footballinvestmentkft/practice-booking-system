"""
Ball Training Frame tests — AN-3B2F PR-1B.

Coverage:
  CROP-01  canonical_crop_box: centred ball in 1920×1080 → expected pixel box
  CROP-02  canonical_crop_box: ball near corner → left/top clamped to 0
  CROP-03  tap_to_full_frame: centre tap on centred ball → full-frame (0.5, 0.5)
  CROP-04  tap_to_full_frame: top-left tap → expected full-frame coords
  CROP-05  tap_to_full_frame: out-of-range tap clamped, result in [0, 1]
  CROP-06  round-trip error < 1 pixel (1920×1080 full-resolution)
  CROP-07  clamp_unit: values below 0, above 1, exact 0.0 and 1.0
  CROP-08  canonical_crop_box: portrait image (1080×1920)
  CROP-09  canonical_crop_box: all four edges clamped when ball at image corner
  CROP-10  full_frame passthrough: tap_x/tap_y clamp for full-frame mode

  FRM-01   GET /me/ball-training/frame/{id} → 503 when flag off
  FRM-02   GET with unknown UUID → 404
  FRM-03   GET with cross-user assignment → 404 (no info-leak)
  FRM-04   GET with expired assignment → 410
  FRM-05   GET with consumed assignment → 409
  FRM-06   GET with revoked consent → 403
  FRM-07   GET valid assignment → 200, Content-Type: image/jpeg
  FRM-08   display_mode persisted: context_crop for high-confidence trajectory
  FRM-09   display_mode persisted: full_frame for lost tracking state
  FRM-10   display_mode persisted: full_frame for low-confidence trajectory
  FRM-11   Privacy contract: no video_id / frame_ms / storage_path in response headers
  FRM-12   corrected submit after context_crop fetch → corrected_x/y back-calculated
  FRM-13   corrected submit after full_frame fetch → corrected_x/y == tap_x/tap_y
  FRM-14   corrected without prior frame fetch → 422
  FRM-15   corrected round-trip coordinate error < 1 pixel (1920×1080)
  FRM-16   PostgreSQL smoke: frame endpoint reaches DB (valid path, wrong storage → 500 ok)

CROP-01..10: pure-math unit tests (no DB, no HTTP).
FRM-01..16:  FastAPI TestClient + PostgreSQL savepoint fixture.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event as sa_event, select
from sqlalchemy.orm import sessionmaker

from app.core.auth import create_access_token
from app.database import engine, get_db
from app.main import app
from app.models.juggling import (
    BallTrainingAssignment,
    JugglingBallTrajectory,
    JugglingConsent,
    JugglingVideo,
)
from app.models.user import User, UserRole
from app.services.juggling.coordinate_transform import (
    CropBox,
    canonical_crop_box,
    clamp_unit,
    tap_to_full_frame,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_jpeg(width: int = 1920, height: int = 1080) -> bytes:
    """Return minimal valid JPEG bytes via Pillow."""
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=50)
    return buf.getvalue()


_FAKE_JPEG = _make_jpeg()


def _auth(user: User) -> dict:
    token = create_access_token(data={"sub": user.email})
    return {"Authorization": f"Bearer {token}"}


# ── DB + client fixtures (savepoint pattern) ──────────────────────────────────

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


# ── DB helpers ────────────────────────────────────────────────────────────────

def _make_user(db, suffix: str = "", role: UserRole = UserRole.ADMIN) -> User:
    u = User(
        email=f"frm_{suffix}_{uuid.uuid4().hex[:6]}@test.com",
        name="FRM User",
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
        storage_path=f"/tmp/frm_{uuid.uuid4().hex}.mp4",
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
    confidence: float = 0.8,
    tracking_state: str = "detected",
    ball_x: float | None = None,
    ball_y: float | None = None,
    img_w: int = 1920,
    img_h: int = 1080,
) -> JugglingBallTrajectory:
    # ck_ball_traj_coords_state: lost → ball_x/y MUST be NULL; others MUST be set.
    if ball_x is None:
        ball_x = None if tracking_state == "lost" else 0.5
    if ball_y is None:
        ball_y = None if tracking_state == "lost" else 0.4
    t = JugglingBallTrajectory(
        video_id=video.id,
        frame_ms=frame_ms,
        ball_x=ball_x,
        ball_y=ball_y,
        confidence=confidence,
        tracking_state=tracking_state,
        image_width_px=img_w,
        image_height_px=img_h,
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
    display_mode: str | None = None,
) -> BallTrainingAssignment:
    now = datetime.now(timezone.utc)
    a = BallTrainingAssignment(
        user_id=user.id,
        video_id=video.id,
        frame_ms=frame_ms,
        expires_at=now + expires_delta,
        consumed_at=consumed_at,
        display_mode=display_mode,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def _flags_on(monkeypatch):
    """Enable juggling + ball-feedback + frame flags."""
    import app.services.juggling.feature_flag as ff_module
    import app.api.api_v1.endpoints.users.juggling_ball_training as hub_module
    import app.api.api_v1.endpoints.users.juggling_ball_training_frame as frame_module
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: True)
    monkeypatch.setattr(hub_module.settings, "BALL_FEEDBACK_ENABLED", True)
    monkeypatch.setattr(frame_module.settings, "BALL_TRAINING_FRAME_ENABLED", True)


# ── CROP-01..10: pure coordinate-transform math ───────────────────────────────

def test_crop_01_centred_ball_1920x1080():
    """CROP-01: centred ball (0.5, 0.5) → symmetric crop in 1920×1080."""
    box = canonical_crop_box(0.5, 0.5, 1920, 1080, margin_ratio=0.70)
    # half = 0.70 * 1080 / 2 = 378
    assert box.left == pytest.approx(960 - 378)
    assert box.top == pytest.approx(540 - 378)
    assert box.right == pytest.approx(960 + 378)
    assert box.bottom == pytest.approx(540 + 378)


def test_crop_02_ball_near_corner_clamped():
    """CROP-02: ball at (0.01, 0.01) → left/top clamped to 0."""
    box = canonical_crop_box(0.01, 0.01, 1920, 1080, margin_ratio=0.70)
    assert box.left == 0.0
    assert box.top == 0.0
    assert box.right > 0.0
    assert box.bottom > 0.0


def test_crop_03_tap_centre_returns_ball_coords():
    """CROP-03: centre tap in centred crop → back-projects to (0.5, 0.5)."""
    box = canonical_crop_box(0.5, 0.5, 1920, 1080, margin_ratio=0.70)
    full_x, full_y = tap_to_full_frame(0.5, 0.5, box, 1920, 1080)
    assert full_x == pytest.approx(0.5, abs=1e-9)
    assert full_y == pytest.approx(0.5, abs=1e-9)


def test_crop_04_topleft_tap():
    """CROP-04: top-left tap (0.0, 0.0) → box.left/top normalised."""
    box = canonical_crop_box(0.5, 0.5, 1920, 1080, margin_ratio=0.70)
    full_x, full_y = tap_to_full_frame(0.0, 0.0, box, 1920, 1080)
    assert full_x == pytest.approx(box.left / 1920, abs=1e-9)
    assert full_y == pytest.approx(box.top / 1080, abs=1e-9)


def test_crop_05_out_of_range_tap_clamped():
    """CROP-05: tap outside [0, 1] is clamped; output always in [0, 1]."""
    box = canonical_crop_box(0.5, 0.5, 1920, 1080, margin_ratio=0.70)
    for tap_x, tap_y in [(-0.5, 1.5), (2.0, -1.0), (1.0, 1.0)]:
        fx, fy = tap_to_full_frame(tap_x, tap_y, box, 1920, 1080)
        assert 0.0 <= fx <= 1.0
        assert 0.0 <= fy <= 1.0


def test_crop_06_roundtrip_error_less_than_1px():
    """CROP-06: back-projecting the ball position yields < 1 pixel error."""
    ball_x, ball_y = 0.35, 0.60
    img_w, img_h = 1920, 1080
    box = canonical_crop_box(ball_x, ball_y, img_w, img_h, margin_ratio=0.70)
    crop_w = box.right - box.left
    crop_h = box.bottom - box.top
    # Determine ball tap in crop space.
    tap_x = (ball_x * img_w - box.left) / crop_w
    tap_y = (ball_y * img_h - box.top) / crop_h
    full_x, full_y = tap_to_full_frame(tap_x, tap_y, box, img_w, img_h)
    err_px = abs(full_x - ball_x) * img_w
    err_py = abs(full_y - ball_y) * img_h
    assert err_px < 1.0, f"x error {err_px:.4f} px"
    assert err_py < 1.0, f"y error {err_py:.4f} px"


def test_crop_07_clamp_unit():
    """CROP-07: clamp_unit behaviour at boundaries and extremes."""
    assert clamp_unit(-1.0) == 0.0
    assert clamp_unit(0.0) == 0.0
    assert clamp_unit(0.5) == pytest.approx(0.5)
    assert clamp_unit(1.0) == 1.0
    assert clamp_unit(2.0) == 1.0


def test_crop_08_portrait_image():
    """CROP-08: portrait (1080×1920) — half_side derived from shorter dim (1080)."""
    box = canonical_crop_box(0.5, 0.5, 1080, 1920, margin_ratio=0.70)
    # half = 0.70 * 1080 / 2 = 378
    assert box.left == pytest.approx(540 - 378)
    assert box.top == pytest.approx(960 - 378)
    assert box.right == pytest.approx(540 + 378)
    assert box.bottom == pytest.approx(960 + 378)


def test_crop_09_all_edges_clamped_at_corner():
    """CROP-09: ball at image corner (0.0, 0.0) — all four edges clamped ≥ 0."""
    box = canonical_crop_box(0.0, 0.0, 1920, 1080, margin_ratio=0.70)
    assert box.left == 0.0
    assert box.top == 0.0
    assert box.right > 0.0
    assert box.bottom > 0.0
    assert box.right <= 1920
    assert box.bottom <= 1080
    # Similarly at bottom-right corner
    box2 = canonical_crop_box(1.0, 1.0, 1920, 1080, margin_ratio=0.70)
    assert box2.right == 1920.0
    assert box2.bottom == 1080.0
    assert box2.left >= 0.0
    assert box2.top >= 0.0


def test_crop_10_full_frame_passthrough():
    """CROP-10: full_frame mode → tap_x/tap_y ARE the corrected coords (after clamp)."""
    # In full_frame mode the service uses clamp_unit(tap_x/y) directly.
    assert clamp_unit(0.7) == pytest.approx(0.7)
    assert clamp_unit(0.3) == pytest.approx(0.3)
    # Out-of-range clamped.
    assert clamp_unit(1.5) == 1.0
    assert clamp_unit(-0.2) == 0.0


# ── FRM-01..16: HTTP frame-serving tests ─────────────────────────────────────

def test_frm_01_flag_off_returns_503(db, client, monkeypatch):
    """FRM-01: frame endpoint returns 503 when BALL_TRAINING_FRAME_ENABLED is False."""
    import app.services.juggling.feature_flag as ff_module
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: True)
    # Do NOT enable the frame flag.
    reviewer = _make_user(db, "f01")
    resp = client.get(
        f"/api/v1/users/me/ball-training/frame/{uuid.uuid4()}",
        headers=_auth(reviewer),
    )
    assert resp.status_code == 503


def test_frm_02_unknown_uuid_returns_404(db, client, monkeypatch):
    """FRM-02: non-existent assignment UUID → 404."""
    _flags_on(monkeypatch)
    reviewer = _make_user(db, "f02")
    with patch("app.services.juggling.frame_service.extract_video_frame", return_value=_FAKE_JPEG):
        resp = client.get(
            f"/api/v1/users/me/ball-training/frame/{uuid.uuid4()}",
            headers=_auth(reviewer),
        )
    assert resp.status_code == 404


def test_frm_03_cross_user_returns_404(db, client, monkeypatch):
    """FRM-03: assignment belongs to another user → 404 (no info-leak)."""
    _flags_on(monkeypatch)
    reviewer = _make_user(db, "f03a")
    owner = _make_user(db, "f03b")
    other = _make_user(db, "f03c")
    video = _make_video(db, owner)
    _make_consent(db, owner)
    _make_trajectory(db, video, frame_ms=3000)
    assignment = _make_assignment(db, other, video, frame_ms=3000)

    with patch("app.services.juggling.frame_service.extract_video_frame", return_value=_FAKE_JPEG):
        resp = client.get(
            f"/api/v1/users/me/ball-training/frame/{assignment.id}",
            headers=_auth(reviewer),
        )
    assert resp.status_code == 404


def test_frm_04_expired_assignment_returns_410(db, client, monkeypatch):
    """FRM-04: expired assignment (expires_at in the past) → 410."""
    _flags_on(monkeypatch)
    reviewer = _make_user(db, "f04")
    owner = _make_user(db, "f04b")
    video = _make_video(db, owner)
    _make_consent(db, owner)
    _make_trajectory(db, video, frame_ms=4000)
    assignment = _make_assignment(
        db, reviewer, video, frame_ms=4000,
        expires_delta=timedelta(seconds=-1),
    )

    with patch("app.services.juggling.frame_service.extract_video_frame", return_value=_FAKE_JPEG):
        resp = client.get(
            f"/api/v1/users/me/ball-training/frame/{assignment.id}",
            headers=_auth(reviewer),
        )
    assert resp.status_code == 410


def test_frm_05_consumed_assignment_returns_409(db, client, monkeypatch):
    """FRM-05: already-consumed assignment → 409."""
    _flags_on(monkeypatch)
    reviewer = _make_user(db, "f05")
    owner = _make_user(db, "f05b")
    video = _make_video(db, owner)
    _make_consent(db, owner)
    _make_trajectory(db, video, frame_ms=5000)
    assignment = _make_assignment(
        db, reviewer, video, frame_ms=5000,
        consumed_at=datetime.now(timezone.utc),
    )

    with patch("app.services.juggling.frame_service.extract_video_frame", return_value=_FAKE_JPEG):
        resp = client.get(
            f"/api/v1/users/me/ball-training/frame/{assignment.id}",
            headers=_auth(reviewer),
        )
    assert resp.status_code == 409


def test_frm_06_revoked_consent_returns_403(db, client, monkeypatch):
    """FRM-06: owner consent revoked after assignment → 403."""
    _flags_on(monkeypatch)
    reviewer = _make_user(db, "f06")
    owner = _make_user(db, "f06b")
    video = _make_video(db, owner)
    # No consent — revoked.
    _make_trajectory(db, video, frame_ms=6000)
    assignment = _make_assignment(db, reviewer, video, frame_ms=6000)

    with patch("app.services.juggling.frame_service.extract_video_frame", return_value=_FAKE_JPEG):
        resp = client.get(
            f"/api/v1/users/me/ball-training/frame/{assignment.id}",
            headers=_auth(reviewer),
        )
    assert resp.status_code == 403


def test_frm_07_valid_frame_returns_200_jpeg(db, client, monkeypatch):
    """FRM-07: valid assignment → 200 with Content-Type image/jpeg."""
    _flags_on(monkeypatch)
    reviewer = _make_user(db, "f07")
    owner = _make_user(db, "f07b")
    video = _make_video(db, owner)
    _make_consent(db, owner)
    _make_trajectory(db, video, frame_ms=7000, confidence=0.9, tracking_state="detected")
    assignment = _make_assignment(db, reviewer, video, frame_ms=7000)

    with patch("app.services.juggling.frame_service.extract_video_frame", return_value=_FAKE_JPEG):
        resp = client.get(
            f"/api/v1/users/me/ball-training/frame/{assignment.id}",
            headers=_auth(reviewer),
        )
    assert resp.status_code == 200
    assert "image/jpeg" in resp.headers["content-type"]


def test_frm_08_display_mode_context_crop_high_confidence(db, client, monkeypatch):
    """FRM-08: high-confidence detected trajectory → display_mode = 'context_crop'."""
    _flags_on(monkeypatch)
    reviewer = _make_user(db, "f08")
    owner = _make_user(db, "f08b")
    video = _make_video(db, owner)
    _make_consent(db, owner)
    _make_trajectory(db, video, frame_ms=8000, confidence=0.9, tracking_state="detected")
    assignment = _make_assignment(db, reviewer, video, frame_ms=8000)

    with patch("app.services.juggling.frame_service.extract_video_frame", return_value=_FAKE_JPEG):
        resp = client.get(
            f"/api/v1/users/me/ball-training/frame/{assignment.id}",
            headers=_auth(reviewer),
        )
    assert resp.status_code == 200

    # Reload assignment and verify display_mode was persisted.
    db.expire_all()
    updated = db.get(BallTrainingAssignment, assignment.id)
    assert updated.display_mode == "context_crop"


def test_frm_09_display_mode_full_frame_lost(db, client, monkeypatch):
    """FRM-09: tracking_state == 'lost' → display_mode = 'full_frame'."""
    _flags_on(monkeypatch)
    reviewer = _make_user(db, "f09")
    owner = _make_user(db, "f09b")
    video = _make_video(db, owner)
    _make_consent(db, owner)
    _make_trajectory(db, video, frame_ms=9000, confidence=0.8, tracking_state="lost")
    assignment = _make_assignment(db, reviewer, video, frame_ms=9000)

    with patch("app.services.juggling.frame_service.extract_video_frame", return_value=_FAKE_JPEG):
        resp = client.get(
            f"/api/v1/users/me/ball-training/frame/{assignment.id}",
            headers=_auth(reviewer),
        )
    assert resp.status_code == 200

    db.expire_all()
    updated = db.get(BallTrainingAssignment, assignment.id)
    assert updated.display_mode == "full_frame"


def test_frm_10_display_mode_full_frame_low_confidence(db, client, monkeypatch):
    """FRM-10: confidence < 0.30 threshold → display_mode = 'full_frame'."""
    _flags_on(monkeypatch)
    reviewer = _make_user(db, "f10")
    owner = _make_user(db, "f10b")
    video = _make_video(db, owner)
    _make_consent(db, owner)
    _make_trajectory(db, video, frame_ms=10000, confidence=0.15, tracking_state="detected")
    assignment = _make_assignment(db, reviewer, video, frame_ms=10000)

    with patch("app.services.juggling.frame_service.extract_video_frame", return_value=_FAKE_JPEG):
        resp = client.get(
            f"/api/v1/users/me/ball-training/frame/{assignment.id}",
            headers=_auth(reviewer),
        )
    assert resp.status_code == 200

    db.expire_all()
    updated = db.get(BallTrainingAssignment, assignment.id)
    assert updated.display_mode == "full_frame"


def test_frm_11_privacy_no_sensitive_headers(db, client, monkeypatch):
    """FRM-11: response headers must not contain video_id, frame_ms, or storage_path."""
    _flags_on(monkeypatch)
    reviewer = _make_user(db, "f11")
    owner = _make_user(db, "f11b")
    video = _make_video(db, owner)
    _make_consent(db, owner)
    _make_trajectory(db, video, frame_ms=11000, confidence=0.9)
    assignment = _make_assignment(db, reviewer, video, frame_ms=11000)

    with patch("app.services.juggling.frame_service.extract_video_frame", return_value=_FAKE_JPEG):
        resp = client.get(
            f"/api/v1/users/me/ball-training/frame/{assignment.id}",
            headers=_auth(reviewer),
        )
    assert resp.status_code == 200

    header_str = " ".join(f"{k}:{v}" for k, v in resp.headers.items()).lower()
    assert "video_id" not in header_str
    assert "frame_ms" not in header_str
    assert "storage_path" not in header_str
    # Cache must be disabled.
    assert "no-store" in resp.headers.get("cache-control", "")


def test_frm_12_corrected_submit_after_context_crop(db, client, monkeypatch):
    """FRM-12: corrected submit after context_crop → back-calculates corrected_x/y."""
    _flags_on(monkeypatch)
    import app.api.api_v1.endpoints.users.juggling_ball_training as hub_module
    monkeypatch.setattr(hub_module.settings, "BALL_TRAINING_ALLOWED_USER_IDS", "")

    reviewer = _make_user(db, "f12", role=UserRole.ADMIN)
    owner = _make_user(db, "f12b")
    video = _make_video(db, owner)
    _make_consent(db, owner, training=True)
    _make_trajectory(
        db, video, frame_ms=12000, confidence=0.9, tracking_state="detected",
        ball_x=0.5, ball_y=0.5, img_w=1920, img_h=1080,
    )
    assignment = _make_assignment(
        db, reviewer, video, frame_ms=12000,
        display_mode="context_crop",
    )

    # Submit corrected decision with centre tap → should back-project to (0.5, 0.5).
    resp = client.post(
        "/api/v1/users/me/ball-training/feedback",
        json={
            "assignment_id": str(assignment.id),
            "decision": "corrected",
            "tap_x": 0.5,
            "tap_y": 0.5,
        },
        headers=_auth(reviewer),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["decision"] == "corrected"
    assert body["corrected_x"] == pytest.approx(0.5, abs=0.01)
    assert body["corrected_y"] == pytest.approx(0.5, abs=0.01)


def test_frm_13_corrected_submit_after_full_frame(db, client, monkeypatch):
    """FRM-13: corrected after full_frame → corrected_x/y == clamped tap coords."""
    _flags_on(monkeypatch)
    import app.api.api_v1.endpoints.users.juggling_ball_training as hub_module
    monkeypatch.setattr(hub_module.settings, "BALL_TRAINING_ALLOWED_USER_IDS", "")

    reviewer = _make_user(db, "f13", role=UserRole.ADMIN)
    owner = _make_user(db, "f13b")
    video = _make_video(db, owner)
    _make_consent(db, owner, training=True)
    _make_trajectory(db, video, frame_ms=13000, confidence=0.9)
    assignment = _make_assignment(
        db, reviewer, video, frame_ms=13000,
        display_mode="full_frame",
    )

    resp = client.post(
        "/api/v1/users/me/ball-training/feedback",
        json={
            "assignment_id": str(assignment.id),
            "decision": "corrected",
            "tap_x": 0.72,
            "tap_y": 0.35,
        },
        headers=_auth(reviewer),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["corrected_x"] == pytest.approx(0.72, abs=1e-6)
    assert body["corrected_y"] == pytest.approx(0.35, abs=1e-6)


def test_frm_14_corrected_without_frame_fetch_returns_422(db, client, monkeypatch):
    """FRM-14: corrected submit when display_mode is None (frame not fetched) → 422."""
    _flags_on(monkeypatch)
    import app.api.api_v1.endpoints.users.juggling_ball_training as hub_module
    monkeypatch.setattr(hub_module.settings, "BALL_TRAINING_ALLOWED_USER_IDS", "")

    reviewer = _make_user(db, "f14", role=UserRole.ADMIN)
    owner = _make_user(db, "f14b")
    video = _make_video(db, owner)
    _make_consent(db, owner, training=True)
    _make_trajectory(db, video, frame_ms=14000, confidence=0.9)
    # display_mode intentionally left NULL.
    assignment = _make_assignment(db, reviewer, video, frame_ms=14000)

    resp = client.post(
        "/api/v1/users/me/ball-training/feedback",
        json={
            "assignment_id": str(assignment.id),
            "decision": "corrected",
            "tap_x": 0.5,
            "tap_y": 0.5,
        },
        headers=_auth(reviewer),
    )
    assert resp.status_code == 422


def test_frm_15_corrected_roundtrip_error_less_than_1px(db, client, monkeypatch):
    """FRM-15: corrected round-trip: back-projected coords within 1 pixel of ball pos."""
    _flags_on(monkeypatch)
    import app.api.api_v1.endpoints.users.juggling_ball_training as hub_module
    monkeypatch.setattr(hub_module.settings, "BALL_TRAINING_ALLOWED_USER_IDS", "")

    reviewer = _make_user(db, "f15", role=UserRole.ADMIN)
    owner = _make_user(db, "f15b")
    video = _make_video(db, owner)
    _make_consent(db, owner, training=True)
    ball_x, ball_y = 0.35, 0.60
    img_w, img_h = 1920, 1080
    _make_trajectory(
        db, video, frame_ms=15000, confidence=0.9, tracking_state="detected",
        ball_x=ball_x, ball_y=ball_y, img_w=img_w, img_h=img_h,
    )
    assignment = _make_assignment(
        db, reviewer, video, frame_ms=15000,
        display_mode="context_crop",
    )

    # Compute the tap that corresponds to the ball's centre in crop space.
    from app.services.juggling.coordinate_transform import canonical_crop_box
    from app.config import settings
    box = canonical_crop_box(
        ball_x, ball_y, img_w, img_h,
        margin_ratio=settings.BALL_TRAINING_FRAME_MARGIN_RATIO,
    )
    crop_w = box.right - box.left
    crop_h = box.bottom - box.top
    tap_x = (ball_x * img_w - box.left) / crop_w
    tap_y = (ball_y * img_h - box.top) / crop_h

    resp = client.post(
        "/api/v1/users/me/ball-training/feedback",
        json={
            "assignment_id": str(assignment.id),
            "decision": "corrected",
            "tap_x": round(tap_x, 6),
            "tap_y": round(tap_y, 6),
        },
        headers=_auth(reviewer),
    )
    assert resp.status_code == 201
    body = resp.json()
    err_px = abs(body["corrected_x"] - ball_x) * img_w
    err_py = abs(body["corrected_y"] - ball_y) * img_h
    assert err_px < 1.0, f"x error {err_px:.4f} px"
    assert err_py < 1.0, f"y error {err_py:.4f} px"


def test_frm_16_postgres_smoke_reaches_db(db, client, monkeypatch):
    """FRM-16: frame endpoint reaches the DB (valid path but no real video file → 500 ok)."""
    _flags_on(monkeypatch)
    reviewer = _make_user(db, "f16")
    owner = _make_user(db, "f16b")
    video = _make_video(db, owner)
    _make_consent(db, owner)
    _make_trajectory(db, video, frame_ms=16000, confidence=0.9)
    assignment = _make_assignment(db, reviewer, video, frame_ms=16000)

    # Do NOT mock extract_video_frame — let it attempt to open the non-existent file.
    resp = client.get(
        f"/api/v1/users/me/ball-training/frame/{assignment.id}",
        headers=_auth(reviewer),
    )
    # 500 proves the request reached the DB (passed all validation) and failed
    # only at the video extraction step — there is no real MP4 file in tests.
    assert resp.status_code == 500, (
        f"Expected 500 (no real video file); got {resp.status_code}"
    )
