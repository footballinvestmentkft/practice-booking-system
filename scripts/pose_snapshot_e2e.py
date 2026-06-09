"""
Phase 2A E2E Validation Script — Pose Snapshot
Uses FastAPI TestClient (in-process) so route registration is always current.
All DB operations hit the real dev database (lfa_intern_system).

Evidence collected:
  [1]  POSE_SNAPSHOT_ENABLED runtime value (config.py + .env)
  [2]  Login → Bearer token
  [3]  JugglingVideo + JugglingContactEvent created via ORM
  [4]  POST /pose-snapshot → HTTP 201 + response JSON
  [5]  keypoints JSON excerpt (15 joints, sample values)
  [6]  Raw DB record → direct SELECT on juggling_pose_snapshots
  [7]  GET /pose-snapshots → 200, list with 1 entry
  [8]  Upsert → second POST returns 200 (not 201)
  [9]  Feature-flag-off guard → 503 on require_pose_snapshot_enabled()
  [10] Cleanup

Usage:
    cd practice_booking_system
    python scripts/pose_snapshot_e2e.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/lfa_intern_system")

# ── Helpers ───────────────────────────────────────────────────────────────────

SEP = "─" * 72

def section(n: int, title: str) -> None:
    print(f"\n{SEP}")
    print(f"  [{n}] {title}")
    print(SEP)

def ok(msg: str) -> None:
    print(f"  ✓  {msg}")

def info(label: str, value) -> None:
    print(f"  {label:<34} {value}")

# ── 1. POSE_SNAPSHOT_ENABLED ─────────────────────────────────────────────────

section(1, "POSE_SNAPSHOT_ENABLED — runtime value")

from app.config import settings  # noqa: E402

info("Default in config.py:",        "False  (POSE_SNAPSHOT_ENABLED: bool = False)")
info("Value loaded from .env:",       settings.POSE_SNAPSHOT_ENABLED)

if not settings.POSE_SNAPSHOT_ENABLED:
    print("  ⚠  Flag is OFF — set POSE_SNAPSHOT_ENABLED=true in .env and retry")
    sys.exit(1)

ok("Flag is ON → endpoints active")

# ── Bootstrap TestClient ──────────────────────────────────────────────────────

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app                  # noqa: E402

client = TestClient(app, raise_server_exceptions=True)
engine = create_engine(DATABASE_URL)

# ── 2. Login ──────────────────────────────────────────────────────────────────

section(2, "Login — obtain Bearer token via TestClient")

from app.core.security import get_password_hash  # noqa: E402
from app.models.user import User, UserRole        # noqa: E402

E2E_EMAIL    = f"pose_e2e_{uuid.uuid4().hex[:8]}@test.com"
E2E_PASSWORD = "pose_e2e_pw_2026"

with Session(engine) as db:
    user = User(
        name          = "Pose E2E User",
        email         = E2E_EMAIL,
        password_hash = get_password_hash(E2E_PASSWORD),
        role          = UserRole.STUDENT,
        is_active     = True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    USER_ID = user.id

info("Created ephemeral test user:", E2E_EMAIL)
info("User ID:",                     USER_ID)

r_login = client.post("/api/v1/auth/login", json={"email": E2E_EMAIL, "password": E2E_PASSWORD})
assert r_login.status_code == 200, f"Login failed: {r_login.status_code} {r_login.text}"
TOKEN = r_login.json()["access_token"]
AUTH  = {"Authorization": f"Bearer {TOKEN}"}
ok(f"Token obtained (first 40 chars): {TOKEN[:40]}…")

# ── 3. Create JugglingVideo + JugglingContactEvent ────────────────────────────

section(3, "Create JugglingVideo + JugglingContactEvent (ORM)")

from app.models.juggling import JugglingContactEvent, JugglingVideo  # noqa: E402

VIDEO_ID      = uuid.uuid4()
EVENT_ID      = uuid.uuid4()
DEVICE_EVT_ID = uuid.uuid4()
TIMESTAMP_MS  = 4_250   # 4.25 s

with Session(engine) as db:
    video = JugglingVideo(
        id                = VIDEO_ID,
        user_id           = USER_ID,
        source_type       = "uploaded_video",
        upload_source     = "gallery",
        status            = "analyzed",
        annotation_status = "in_progress",
        storage_path      = "/tmp/e2e_pose_test.mp4",
        filename_stored   = "e2e_pose_test.mp4",
        file_size_bytes   = 2_048,
        checksum_sha256   = "e" * 64,
    )
    db.add(video)
    db.flush()

    event = JugglingContactEvent(
        id                       = EVENT_ID,
        video_id                 = VIDEO_ID,
        created_by_user_id       = USER_ID,
        device_event_id          = DEVICE_EVT_ID,
        timestamp_ms             = TIMESTAMP_MS,
        contact_type             = "instep_kick",
        side                     = "right",
        annotation_confidence    = "certain",
        annotation_source        = "manual_user",
        annotation_review_status = "pending",
        taxonomy_review_status   = "not_applicable",
        excluded_from_training   = True,
        excluded_from_count      = False,
        taxonomy_version         = "v1",
        consent_snapshot         = {},
    )
    db.add(event)
    db.commit()

info("Video ID:",   str(VIDEO_ID))
info("Event ID:",   str(EVENT_ID))
info("Timestamp:", f"{TIMESTAMP_MS} ms  (= 4.25 s)")
ok("Committed to DB")

# ── 4+5. POST /pose-snapshot ──────────────────────────────────────────────────

section(4, "POST /me/juggling/videos/{vid}/contacts/{eid}/pose-snapshot")

KEYPOINTS_PAYLOAD = {
    "schema_version": "1",
    "body": [
        {"name": "root",           "x": 0.511, "y": 0.532, "confidence": 0.994},
        {"name": "neck",           "x": 0.509, "y": 0.285, "confidence": 0.991},
        {"name": "left_shoulder",  "x": 0.462, "y": 0.301, "confidence": 0.987},
        {"name": "right_shoulder", "x": 0.558, "y": 0.299, "confidence": 0.985},
        {"name": "left_elbow",     "x": 0.431, "y": 0.388, "confidence": 0.973},
        {"name": "right_elbow",    "x": 0.591, "y": 0.387, "confidence": 0.971},
        {"name": "left_wrist",     "x": 0.402, "y": 0.468, "confidence": 0.962},
        {"name": "right_wrist",    "x": 0.621, "y": 0.466, "confidence": 0.959},
        {"name": "left_hip",       "x": 0.474, "y": 0.557, "confidence": 0.981},
        {"name": "right_hip",      "x": 0.548, "y": 0.556, "confidence": 0.979},
        {"name": "left_knee",      "x": 0.451, "y": 0.694, "confidence": 0.968},
        {"name": "right_knee",     "x": 0.572, "y": 0.692, "confidence": 0.965},
        {"name": "left_ankle",     "x": 0.412, "y": 0.834, "confidence": 0.971},
        {"name": "right_ankle",    "x": 0.588, "y": 0.831, "confidence": 0.968},
        {"name": "nose",           "x": 0.510, "y": 0.241, "confidence": 0.989},
    ],
    "left_hand":  [],
    "right_hand": [],
}

POST_BODY = {
    "keypoints":            KEYPOINTS_PAYLOAD,
    "model_version":        "apple_vision_v1",
    "capture_source":       "ios_realtime",
    "captured_at_ms":       TIMESTAMP_MS,
    "image_width_px":       640,
    "image_height_px":      360,
    "inference_confidence": 0.987,
}

URL_POST = f"/api/v1/users/me/juggling/videos/{VIDEO_ID}/contacts/{EVENT_ID}/pose-snapshot"
URL_GET  = f"/api/v1/users/me/juggling/videos/{VIDEO_ID}/pose-snapshots"

r1 = client.post(URL_POST, json=POST_BODY, headers=AUTH)

info("POST URL (parameterised):", "/…/videos/{vid}/contacts/{eid}/pose-snapshot")
info("Full URL:",                  URL_POST)
info("HTTP status:",               r1.status_code)

if r1.status_code != 201:
    print(f"  ✗  Unexpected status: {r1.text}")
    sys.exit(1)

ok("POST → 201 Created")

d1 = r1.json()
SNAPSHOT_ID = d1["id"]
info("Snapshot ID:",      SNAPSHOT_ID)
info("contact_event_id:", d1["contact_event_id"])
info("video_id:",         d1["video_id"])
info("timestamp_ms:",     d1["timestamp_ms"])
info("model_version:",    d1["model_version"])
info("capture_source:",   d1["capture_source"])
info("inference_conf:",   d1["inference_confidence"])
info("image_width_px:",   d1["image_width_px"])
info("image_height_px:",  d1["image_height_px"])
info("created_at:",       d1["created_at"])

section(5, "keypoints JSON excerpt from POST response")
body_joints = d1["keypoints"]["body"]
info("schema_version:", d1["keypoints"]["schema_version"])
info("body count:",     len(body_joints))
print()
print("  Joint               x       y       confidence")
print("  " + "-" * 52)
for j in body_joints:
    print(f"  {j['name']:<20}  {j['x']:.3f}   {j['y']:.3f}   {j['confidence']:.3f}")

# ── 6. Raw DB record ──────────────────────────────────────────────────────────

section(6, "Raw DB record — SELECT FROM juggling_pose_snapshots")

with Session(engine) as db:
    row = db.execute(
        text("""
            SELECT
                id,
                contact_event_id,
                video_id,
                timestamp_ms,
                model_version,
                capture_source,
                inference_confidence,
                image_width_px,
                image_height_px,
                created_at,
                jsonb_array_length(keypoints->'body') AS body_joint_count,
                keypoints->'body'->0                  AS first_joint_json
            FROM juggling_pose_snapshots
            WHERE id = :sid
        """),
        {"sid": SNAPSHOT_ID},
    ).mappings().one()

ok("Row found in juggling_pose_snapshots")
print()
for k, v in row.items():
    if k in ("first_joint_json",):
        print(f"\n  keypoints.body[0]:\n    {v}")
    else:
        info(k + ":", v)

# ── 7. GET /pose-snapshots ────────────────────────────────────────────────────

section(7, "GET /me/juggling/videos/{vid}/pose-snapshots")

r2 = client.get(URL_GET, headers=AUTH)
info("GET URL:",     URL_GET)
info("HTTP status:", r2.status_code)
assert r2.status_code == 200, f"Expected 200: {r2.text}"
snaps = r2.json()
info("List length:", len(snaps))
assert len(snaps) == 1
ok("GET → 200, list contains exactly 1 snapshot")
info("Returned snapshot ID:", snaps[0]["id"])
info("timestamp_ms:",         snaps[0]["timestamp_ms"])
info("model_version:",        snaps[0]["model_version"])
assert snaps[0]["id"] == SNAPSHOT_ID

# ── 8. Upsert: second POST → 200 ─────────────────────────────────────────────

section(8, "Upsert — second POST for same event_id → 200 (keypoints updated)")

UPDATED_KP = {**KEYPOINTS_PAYLOAD, "body": [KEYPOINTS_PAYLOAD["body"][0]]}
r3 = client.post(URL_POST, json={**POST_BODY, "keypoints": UPDATED_KP, "inference_confidence": 0.501}, headers=AUTH)
info("HTTP status:", r3.status_code)
assert r3.status_code == 200, f"Expected 200: {r3.text}"
d3 = r3.json()
info("body joint count (updated):", len(d3["keypoints"]["body"]))
info("inference_confidence (upd):", d3["inference_confidence"])
assert len(d3["keypoints"]["body"]) == 1
ok("Upsert → 200 OK, body reduced to 1 joint")

# Restore with full keypoints for the GET check below
client.post(URL_POST, json=POST_BODY, headers=AUTH)

# ── 9. Flag-off → 503 ────────────────────────────────────────────────────────

section(9, "Feature flag OFF → 503 on require_pose_snapshot_enabled()")

from app.api.api_v1.endpoints.users.juggling_pose_snapshots import require_pose_snapshot_enabled  # noqa: E402
from app.api.api_v1.endpoints.users import juggling_pose_snapshots as ps_module               # noqa: E402
from fastapi import HTTPException                                                               # noqa: E402

original_flag = ps_module.settings.POSE_SNAPSHOT_ENABLED
ps_module.settings.POSE_SNAPSHOT_ENABLED = False

try:
    asyncio.run(require_pose_snapshot_enabled())
    print("  ✗  No exception — unexpected!")
    sys.exit(1)
except HTTPException as exc:
    info("HTTPException.status_code:", exc.status_code)
    info("HTTPException.detail:",      exc.detail)
    assert exc.status_code == 503
    ok("Raises HTTP 503 when POSE_SNAPSHOT_ENABLED=False")

ps_module.settings.POSE_SNAPSHOT_ENABLED = original_flag
ok(f"Flag restored to {original_flag}")

# ── 10. Cleanup ────────────────────────────────────────────────────────────────

section(10, "Cleanup — delete test user (cascade)")

with Session(engine) as db:
    # Delete in FK-safe order: events → videos → user
    db.execute(text("DELETE FROM juggling_contact_events WHERE video_id = :vid"), {"vid": str(VIDEO_ID)})
    db.execute(text("DELETE FROM juggling_videos         WHERE id       = :vid"), {"vid": str(VIDEO_ID)})
    db.execute(text("DELETE FROM users                   WHERE id       = :uid"), {"uid": USER_ID})
    db.commit()
ok(f"Deleted events → videos → user {USER_ID}")

# ── Summary ───────────────────────────────────────────────────────────────────

print(f"\n{'═' * 72}")
print("  Phase 2A E2E Validation — ALL 10 CHECKS PASSED ✓")
print(f"{'═' * 72}")
print()
print("  EVIDENCE SUMMARY")
print("  ─────────────────")
print(f"  [1]  POSE_SNAPSHOT_ENABLED = True  (loaded from .env)")
print(f"  [2]  Login → Bearer token for {E2E_EMAIL}")
print(f"  [3]  Video {VIDEO_ID}")
print(f"       Event {EVENT_ID}  @ {TIMESTAMP_MS} ms")
print(f"  [4]  POST → 201 Created  |  Snapshot {SNAPSHOT_ID}")
print(f"  [5]  keypoints.body: {len(body_joints)} joints  |  root=(0.511, 0.532, conf=0.994)")
print(f"  [6]  DB row confirmed  |  created_at={row['created_at']}")
print(f"  [7]  GET → 200  |  list length 1  |  id matches POST response")
print(f"  [8]  Second POST → 200 (upsert)  |  body count updated 15→1")
print(f"  [9]  Flag=False → require_pose_snapshot_enabled() raises HTTP 503")
print(f"  [10] Ephemeral test data cleaned up")
print()
