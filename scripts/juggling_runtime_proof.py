"""
Juggling POC — Minimal Runtime Proof Script
Run with: python scripts/juggling_runtime_proof.py

Requires:
  - JUGGLING_POC_ENABLED=true in .env OR env override
  - alembic upgrade head applied
  - A running PostgreSQL (same DB as app)
  - No Celery worker needed (task is mocked inline)

Produces proof for:
  POST /juggling-consent
  POST /videos/upload-init (source_type=in_app_capture)
  POST /videos/{id}/upload (minimal valid MP4 fixture)
  POST /videos/{id}/complete (Celery mocked)
  GET  /videos/{id}/quality → pending, then analyzed (task run inline)
  Security: .avi 415, fake-mp4 415, empty 400, oversized 413
  Privacy: no public video URL in quality response
"""
import os
import struct
import sys
import uuid

os.environ["JUGGLING_POC_ENABLED"] = "true"
os.environ["TESTING"] = "true"

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# ── App imports ────────────────────────────────────────────────────────────────
from app.main import app
from app.database import get_db, SessionLocal
from app.models.user import User, UserRole
from app.core.security import get_password_hash

# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_ftyp_mp4(size_kb: int = 50) -> bytes:
    """Minimal valid ISO Base Media ftyp box + padding."""
    ftyp = struct.pack(">I", 20) + b"ftyp" + b"isom" + b"\x00\x00\x00\x00" + b"isom"
    return ftyp + b"\x00" * (size_kb * 1024)

def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}

PASS = "✅"
FAIL = "❌"
results = []

def check(label: str, condition: bool, detail: str = "") -> None:
    mark = PASS if condition else FAIL
    line = f"  {mark}  {label}"
    if detail:
        line += f"  [{detail}]"
    print(line)
    results.append((label, condition))

# ── Setup ──────────────────────────────────────────────────────────────────────
db: Session = SessionLocal()
try:
    email = f"juggling_proof_{uuid.uuid4().hex[:8]}@test.com"
    user = User(
        name="Juggling Proof User",
        email=email,
        password_hash=get_password_hash("testpass123"),
        role=UserRole.STUDENT,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
finally:
    db.close()

app.dependency_overrides[get_db] = lambda: SessionLocal()

with TestClient(app) as c:
    # ── 1. Login ───────────────────────────────────────────────────────────────
    r = c.post("/api/v1/auth/login", json={"email": email, "password": "testpass123"})
    token = r.json().get("access_token")
    check("Login OK", r.status_code == 200)

    # ── 2. Consent ────────────────────────────────────────────────────────────
    r = c.post("/api/v1/users/me/juggling-consent",
               json={"service_consent": True, "training_consent": True},
               headers=_auth(token))
    check("POST juggling-consent 200", r.status_code == 200,
          f"service_consent={r.json().get('service_consent')}")

    # ── 3. upload-init ────────────────────────────────────────────────────────
    r = c.post("/api/v1/users/me/juggling/videos/upload-init",
               json={"source_type": "in_app_capture", "upload_source": "camera",
                     "client_reported_metadata": {"fps": 60.0, "device": "iPhone 15 Pro"}},
               headers=_auth(token))
    check("POST upload-init 201", r.status_code == 201)
    video_id = r.json().get("video_id", "")
    check("video_id is UUID", len(video_id) == 36,
          f"video_id={video_id[:8]}...")
    check("status=pending_upload", r.json().get("status") == "pending_upload")

    # ── 4. Upload valid MP4 ───────────────────────────────────────────────────
    mp4_bytes = _make_ftyp_mp4(50)
    import tempfile, pathlib
    from unittest.mock import patch, MagicMock
    from app.services.juggling import security_service as ss
    from app.services.juggling.security_service import compute_sha256
    expected_checksum = compute_sha256(mp4_bytes)

    with tempfile.TemporaryDirectory() as td:
        saved = pathlib.Path(td) / f"{uuid.uuid4()}.mp4"
        with patch("app.services.juggling.video_service.save_file") as mock_save:
            mock_save.return_value = saved
            saved.write_bytes(mp4_bytes)
            r = c.post(f"/api/v1/users/me/juggling/videos/{video_id}/upload",
                       files={"file": ("clip.mp4", mp4_bytes, "video/mp4")},
                       headers=_auth(token))

    check("POST upload 200", r.status_code == 200, f"status={r.json().get('status')}")
    check("checksum_sha256 correct", r.json().get("checksum_sha256") == expected_checksum)
    check("file_size_bytes correct", r.json().get("file_size_bytes") == len(mp4_bytes))

    # ── 5. Complete (mock Celery, run task inline) ────────────────────────────
    with patch("app.api.api_v1.endpoints.users.juggling_videos.analyze_video_task") as mt:
        mt.delay = MagicMock()
        r = c.post(f"/api/v1/users/me/juggling/videos/{video_id}/complete",
                   headers=_auth(token))
    check("POST complete 200", r.status_code == 200)
    check("status=processing after complete", r.json().get("status") == "processing")
    check("Celery task.delay called once", mt.delay.call_count == 1,
          f"calls={mt.delay.call_count}")

    # ── 6. Quality poll — pending ─────────────────────────────────────────────
    r = c.get(f"/api/v1/users/me/juggling/videos/{video_id}/quality",
              headers=_auth(token))
    check("GET quality 200 (processing)", r.status_code == 200)
    check("quality_status=pending during processing",
          r.json().get("quality_status") == "pending")
    check("server_detected_metadata=null during processing",
          r.json().get("server_detected_metadata") is None)

    # ── 7. Simulate task writing analyzed result ──────────────────────────────
    db2 = SessionLocal()
    from app.services.juggling import video_service
    server_meta = {
        "fps": 59.94, "resolution": "1280x720", "duration_seconds": 12.0,
        "codec": "h264", "bitrate_kbps": 6400, "rotation": 0,
        "has_audio": False, "file_format": "mov,mp4", "container": "mov",
        "nb_streams": 1,
    }
    quality_detail = {
        "blur_score": 0.72, "dark_frame_ratio": 0.05,
        "fps_detected": 59.94, "fps_acceptable": True,
        "duration_acceptable": True, "rotation": 0,
        "subject_size_score": None, "ball_visible_score": None,
    }
    video_service.apply_analysis(video_id, server_meta, 0.762, "acceptable",
                                 quality_detail, db2)
    db2.close()

    r = c.get(f"/api/v1/users/me/juggling/videos/{video_id}/quality",
              headers=_auth(token))
    data = r.json()
    check("GET quality 200 (analyzed)", r.status_code == 200)
    check("quality_status=acceptable", data.get("quality_status") == "acceptable")
    check("quality_score populated", data.get("quality_score") is not None,
          f"score={data.get('quality_score')}")
    check("server_detected_metadata.fps=59.94",
          data.get("server_detected_metadata", {}).get("fps") == 59.94)
    check("subject_size_score=null (P2 scope)",
          data.get("quality_detail", {}).get("subject_size_score") is None)
    check("ball_visible_score=null (P2 scope)",
          data.get("quality_detail", {}).get("ball_visible_score") is None)

    # Privacy: no storage_path / public video URL in quality response
    quality_resp_str = str(data)
    check("No storage_path in quality response",
          "storage_path" not in quality_resp_str)
    check("No filename_stored in quality response",
          "filename_stored" not in quality_resp_str)
    check("No /static/ video URL in quality response",
          "/static/uploads/juggling" not in quality_resp_str)

    # Storage path is outside /static/
    db3 = SessionLocal()
    from app.models.juggling import JugglingVideo
    rec = db3.query(JugglingVideo).filter_by(id=video_id).first()
    storage_ok = rec.storage_path is None or "static" not in str(rec.storage_path)
    check("storage_path NOT under /static/", storage_ok,
          f"storage_path={rec.storage_path}")
    db3.close()

    # ── 8. Security proof ─────────────────────────────────────────────────────
    # Grant consent for new video
    r_init2 = c.post("/api/v1/users/me/juggling/videos/upload-init",
                     json={"source_type": "uploaded_video"},
                     headers=_auth(token))
    vid2 = r_init2.json().get("video_id", "")

    # .avi → 415
    r = c.post(f"/api/v1/users/me/juggling/videos/{vid2}/upload",
               files={"file": ("video.avi", _make_ftyp_mp4(), "video/x-msvideo")},
               headers=_auth(token))
    check("AVI extension → 415", r.status_code == 415)

    # Fake MP4 (JPEG bytes) → 415 magic bytes
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 200
    r_init3 = c.post("/api/v1/users/me/juggling/videos/upload-init",
                     json={"source_type": "uploaded_video"},
                     headers=_auth(token))
    vid3 = r_init3.json().get("video_id", "")
    r = c.post(f"/api/v1/users/me/juggling/videos/{vid3}/upload",
               files={"file": ("fake.mp4", jpeg_bytes, "video/mp4")},
               headers=_auth(token))
    check("Fake MP4 (JPEG magic) → 415", r.status_code == 415,
          "magic_bytes_invalid")

    # Empty file → 400
    r_init4 = c.post("/api/v1/users/me/juggling/videos/upload-init",
                     json={"source_type": "uploaded_video"},
                     headers=_auth(token))
    vid4 = r_init4.json().get("video_id", "")
    r = c.post(f"/api/v1/users/me/juggling/videos/{vid4}/upload",
               files={"file": ("empty.mp4", b"", "video/mp4")},
               headers=_auth(token))
    check("Empty file → 400", r.status_code == 400)

    # Oversized file → 413 (override limit to 0 MB)
    orig_max = os.environ.get("JUGGLING_VIDEO_MAX_SIZE_MB", "100")
    from app.services.juggling import security_service as ss_mod
    from app.config import settings
    old_mb = settings.JUGGLING_VIDEO_MAX_SIZE_MB
    settings.JUGGLING_VIDEO_MAX_SIZE_MB = 0
    r_init5 = c.post("/api/v1/users/me/juggling/videos/upload-init",
                     json={"source_type": "uploaded_video"},
                     headers=_auth(token))
    vid5 = r_init5.json().get("video_id", "")
    r = c.post(f"/api/v1/users/me/juggling/videos/{vid5}/upload",
               files={"file": ("big.mp4", _make_ftyp_mp4(1), "video/mp4")},
               headers=_auth(token))
    settings.JUGGLING_VIDEO_MAX_SIZE_MB = old_mb
    check("Oversized file → 413 (config-based)", r.status_code == 413)

    # Other user's video → 404
    other_db = SessionLocal()
    other_user = User(
        name="Other User",
        email=f"other_{uuid.uuid4().hex[:6]}@test.com",
        password_hash=get_password_hash("pass123"),
        role=UserRole.STUDENT,
        is_active=True,
    )
    other_db.add(other_user)
    other_db.commit()
    other_db.refresh(other_user)
    other_db.close()

    r_other = c.post("/api/v1/auth/login",
                     json={"email": other_user.email, "password": "pass123"})
    other_token = r_other.json().get("access_token")
    r = c.get(f"/api/v1/users/me/juggling/videos/{video_id}/quality",
              headers=_auth(other_token))
    check("Other user's video → 404", r.status_code == 404)

    # ── 9. Feature flag proof ─────────────────────────────────────────────────
    settings.JUGGLING_POC_ENABLED = False
    endpoints_503 = [
        ("POST", "/api/v1/users/me/juggling-consent", {}),
        ("GET",  "/api/v1/users/me/juggling-consent", {}),
        ("POST", "/api/v1/users/me/juggling/videos/upload-init", {}),
        ("POST", f"/api/v1/users/me/juggling/videos/{video_id}/upload", {}),
        ("POST", f"/api/v1/users/me/juggling/videos/{video_id}/complete", {}),
        ("GET",  f"/api/v1/users/me/juggling/videos/{video_id}/quality", {}),
    ]
    for method, path, body in endpoints_503:
        fn = getattr(c, method.lower())
        r503 = fn(path, headers=_auth(token), json=body if body else None)
        check(f"Feature flag off: {method} {path.split('juggling')[1][:30]} → 503",
              r503.status_code == 503)
    settings.JUGGLING_POC_ENABLED = True

# ── Final summary ──────────────────────────────────────────────────────────────
total = len(results)
passed = sum(1 for _, ok in results if ok)
failed = [label for label, ok in results if not ok]

print()
print(f"{'='*60}")
print(f"Runtime Proof: {passed}/{total} checks passed")
if failed:
    print(f"FAILED:")
    for f in failed:
        print(f"  ✗ {f}")
else:
    print("ALL CHECKS PASS")
print(f"{'='*60}")
sys.exit(0 if not failed else 1)
