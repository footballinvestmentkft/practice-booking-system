# P5 Phase 2 — Staging / Test Data Execution Plan

**Dátum:** 2026-06-12  
**Scope:** Juggling media flow — BLOCKED_MISSING_STAGING + BLOCKED_MISSING_TEST_DATA feloldása  
**Branch (validációhoz):** `main` @ `e67ee91f`  
**Kapcsolódó:** [docs/P5_PHASE2_STAGING_EXECUTION_CHECKLIST.md](P5_PHASE2_STAGING_EXECUTION_CHECKLIST.md)

---

## 1. Staging státusz — Lokális környezet valóságos állapota

### Kritikus megállapítás

**Nincs szükség külső staging szerverre.** A lokális dev környezet teljes mértékben
megfelel a P5 Phase 2 validáció céljára:

- Backend preflight (`curl`): `http://localhost:8000`
- Fizikai iPhone tesztelés: `http://192.168.1.129:8000` (az `APIConfig.baseURL` már ezt tartalmazza)

### Lokális infrastruktúra állapota (2026-06-12)

| # | Komponens | Parancs | Eredmény | Státusz |
|---|-----------|---------|----------|---------|
| E-01 | Staging URL | n/a — lokális dev | `localhost:8000` / `192.168.1.129:8000` | ✅ KÉSZ |
| E-02 | HTTPS | Nem szükséges lokálisan (LAN HTTP) | HTTP OK | ✅ KÉSZ |
| E-03 | `JUGGLING_POC_ENABLED=true` | `.env` ellenőrzés | **HIÁNYZIK** — nincs `.env`-ben, default=False → 503 | ❌ SZÜKSÉGES |
| E-04 | `JUGGLING_UPLOAD_DIR` létezik | `ls app/uploads/juggling` | Létezik, csak `.gitkeep` | ⚠️ ÜRES |
| E-05 | FastAPI látja a storage-ot | curl juggling endpoints | 503 (flag off) | ❌ E-03 blokkolja |
| E-06 | Celery worker (juggling_videos) | `ps aux \| grep celery` | **CSAK biometric_embeddings** queue fut | ❌ SZÜKSÉGES |
| E-07 | Redis broker | `redis-cli ping` | PONG | ✅ KÉSZ |
| E-08 | ffmpeg | `ffmpeg -version` | version 7.1.1 | ✅ KÉSZ |
| E-09 | ffprobe | `ffprobe -version` | version 7.1.1 | ✅ KÉSZ |
| E-10 | PostgreSQL + DB | `pg_isready` | localhost:5432 UP, `lfa_intern_system` létezik | ✅ KÉSZ |
| E-11 | Juggling DB táblák | `\dt juggling*` | `juggling_videos`, `juggling_consents`, `juggling_file_deletion_log` | ✅ KÉSZ |

### Szükséges akciók a staging blokker feloldásához

**Akció 1 — JUGGLING_POC_ENABLED bekapcsolása:**

```bash
# Adj hozzá a projekt .env fájlhoz:
echo "JUGGLING_POC_ENABLED=true" >> .env
```

Majd indítsd újra a FastAPI server-t.

**Akció 2 — Celery worker indítása juggling_videos queue-ra:**

```bash
# Új terminálablakban, a projekt gyökerében:
celery -A app.celery_app worker \
  -Q juggling_videos \
  --pool=solo \
  --loglevel=info
```

> `--pool=solo` macOS-en szükséges (forking korlátozás); Linuxon elhagyható.

**Akció 3 — JUGGLING_UPLOAD_DIR írható ellenőrzése:**

```bash
touch app/uploads/juggling/.write_test && rm app/uploads/juggling/.write_test && echo "WRITABLE"
```

**Ellenőrzés az akciók után:**

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/v1/users/me/juggling/videos \
  -H "Authorization: Bearer FAKE"
# PASS ha: 401 (nem 503)
```

---

## 2. Teszt user terv

### Meglévő juggling test userek a lokális DB-ben

| user_id | email | role | service_consent | password |
|---------|-------|------|-----------------|---------|
| 167281 | `juggling_proof_0420edf8@test.com` | STUDENT | ✅ true | ismeretlen |
| 167452 | `smoke_91f8adf8@test.com` | STUDENT | ✅ true | ismeretlen |
| 173242 | `proof+254efa@t.com` | STUDENT | ❌ nincs | ismeretlen |

> A meglévő userekhez a jelszavak ismeretlenek. A legegyszerűbb megközelítés:
> új test user létrehozása ismert jelszóval, vagy meglévő user jelszavának resetelése.

### Ajánlott megközelítés — Meglévő user jelszó reset

```bash
# Bcrypt hash generálása 'TestPass123!' jelszóhoz:
python3 -c "
from passlib.context import CryptContext
ctx = CryptContext(schemes=['bcrypt'], deprecated='auto')
print(ctx.hash('TestPass123!'))
"
# Másold be az alábbi SQL-be <HASH> helyére
```

```sql
-- user_id=167281 jelszavát ismert értékre reseteljük:
UPDATE users
SET password_hash = '<HASH_FROM_ABOVE>'
WHERE id = 167281;
```

**Vagy: új test user létrehozása (ha a reset nem kívánatos)**

```sql
INSERT INTO users (
    email, password_hash, is_active, role, full_name,
    created_at, updated_at
) VALUES (
    'juggling-test-p5@local.lfa.internal',
    '<HASH_OF_TestPass123!>',
    true, 'STUDENT', 'P5 Juggling Test',
    NOW(), NOW()
);
-- Jegyezd fel az új user_id-t:
SELECT id FROM users WHERE email = 'juggling-test-p5@local.lfa.internal';
```

### Access token megszerzése

```bash
BASE="http://localhost:8000"
USER_EMAIL="juggling_proof_0420edf8@test.com"  # vagy az új email
USER_PASS="TestPass123!"

TOKEN=$(curl -s -X POST $BASE/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$USER_EMAIL\",\"password\":\"$USER_PASS\"}" \
  | jq -r '.access_token')

echo "TOKEN=${TOKEN:0:30}..."
# PASS ha: JWT string (nem null, nem üres)
```

### service_consent beállítása (ha szükséges)

```bash
# Ha az új user-nek nincs még consent-je:
curl -s -X POST $BASE/api/v1/users/me/juggling-consent \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"service_consent":true,"training_consent":false,"admin_review_consent":false}' | jq .
# PASS ha: {"service_consent":true, ...}
```

### Refresh token ellenőrzése

```bash
REFRESH=$(curl -s -X POST $BASE/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$USER_EMAIL\",\"password\":\"$USER_PASS\"}" \
  | jq -r '.refresh_token')

curl -s -X POST $BASE/api/v1/auth/refresh \
  -H "Content-Type: application/json" \
  -d "{\"refresh_token\":\"$REFRESH\"}" | jq -r '.access_token' | head -c 30
# PASS ha: új JWT string
```

---

## 3. Tesztadat terv

### Fontos megjegyzés a meglévő rekordokról

A lokális DB-ben van 7 `juggling_videos` rekord, de mindegyik:
- `has_thumbnail = false`
- `has_media = false`
- A `analyzed` státuszú rekordoknál: `transcode_status = pending` (nem `done`)

Ezek **nem megfelelők** a T-01 happy path teszteléshez. Új rekordokat kell felvenni.

### Futtatás előtt

```bash
# Kérd le a teszt user user_id-ját és jegyezd fel:
USER_ID=$(psql -U postgres -h localhost -p 5432 -d lfa_intern_system -t -c \
  "SELECT id FROM users WHERE email = 'juggling_proof_0420edf8@test.com';" | tr -d ' ')
echo "USER_ID=$USER_ID"  # Várható: 167281

# Second user (T-04 cross-user teszthez) — meglévő user:
OTHER_USER_ID=$(psql -U postgres -h localhost -p 5432 -d lfa_intern_system -t -c \
  "SELECT id FROM users WHERE email = 'smoke_91f8adf8@test.com';" | tr -d ' ')
echo "OTHER_USER_ID=$OTHER_USER_ID"  # Várható: 167452
```

### T-01 — Happy path (analyzed, transcode done, fájlok megvannak)

**1. lépés — Tényleges videó fájlok elhelyezése:**

```bash
UPLOAD_DIR="app/uploads/juggling"

# Minimális MP4 stub: töltsd le vagy másolj egy valódi kis videót
# Pl. ffmpeg-gel generálj egy 5 másodperces teszt videót:
ffmpeg -f lavfi -i testsrc=size=1280x720:rate=30 -t 5 \
  -vf scale=1280:720 -c:v libx264 -crf 28 \
  $UPLOAD_DIR/t01_processed.mp4 -y

cp $UPLOAD_DIR/t01_processed.mp4 $UPLOAD_DIR/t01_original.mp4

# Thumbnail (1 frame kinyerése a videóból):
ffmpeg -i $UPLOAD_DIR/t01_processed.mp4 -vframes 1 -q:v 2 \
  $UPLOAD_DIR/t01_thumb.jpg -y

# Ellenőrzés:
ls -la $UPLOAD_DIR/t01_*.{mp4,jpg}
```

**2. lépés — DB rekord:**

```sql
INSERT INTO juggling_videos (
    id, user_id,
    source_type, upload_source,
    status, transcode_status, quality_status,
    quality_score,
    quality_detail,
    thumbnail_path,
    processed_path,
    storage_path,
    original_path,
    filename_stored,
    file_size_bytes,
    processed_resolution, processed_fps, processed_file_size_bytes,
    audio_stripped,
    checksum_sha256,
    created_at, updated_at
) VALUES (
    gen_random_uuid(), 167281,
    'uploaded_video', 'file',
    'analyzed', 'done', 'acceptable',
    '0.82',
    '{"blur_score":0.85,"dark_frame_ratio":0.02,"fps_detected":30.0,"fps_acceptable":true,
      "duration_seconds":5.0,"duration_acceptable":true,"audio_present":false}',
    'app/uploads/juggling/t01_thumb.jpg',
    'app/uploads/juggling/t01_processed.mp4',
    'app/uploads/juggling/t01_original.mp4',
    'app/uploads/juggling/t01_original.mp4',
    't01_original.mp4',
    1048576,
    '1280x720', 30.0, 900000,
    true,
    'aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899',
    NOW(), NOW()
);

-- T-01 video_id mentése:
SELECT id FROM juggling_videos
WHERE user_id = 167281 AND status = 'analyzed' AND transcode_status = 'done'
ORDER BY created_at DESC LIMIT 1;
```

**Elvárt a list endpointban:** `has_thumbnail=true`, `has_media=true`

### T-02 — Processing (pipeline fut, media nem elérhető)

```sql
INSERT INTO juggling_videos (
    id, user_id,
    source_type, upload_source,
    status, transcode_status, quality_status,
    thumbnail_path, processed_path,
    storage_path, original_path, filename_stored,
    file_size_bytes, created_at, updated_at
) VALUES (
    gen_random_uuid(), 167281,
    'in_app_capture', 'camera',
    'processing', 'processing', 'pending',
    NULL, NULL,
    'app/uploads/juggling/t02_original.mp4',
    'app/uploads/juggling/t02_original.mp4',
    't02_original.mp4',
    524288,
    NOW() - INTERVAL '30 seconds', NOW()
);

SELECT id FROM juggling_videos
WHERE user_id = 167281 AND status = 'processing'
ORDER BY created_at DESC LIMIT 1;
```

**Elvárt:** `has_thumbnail=false`, `has_media=false`, media hívás → 409

### T-03 — Rejected (quality gate elbukott)

```sql
INSERT INTO juggling_videos (
    id, user_id,
    source_type, upload_source,
    status, transcode_status, quality_status,
    quality_score, quality_detail, rejection_reason,
    thumbnail_path, processed_path,
    storage_path, original_path, filename_stored,
    file_size_bytes, created_at, updated_at
) VALUES (
    gen_random_uuid(), 167281,
    'uploaded_video', 'gallery',
    'rejected', 'done', 'rejected',
    '0.21',
    '{"blur_score":0.15,"dark_frame_ratio":0.85,"fps_detected":15.0,"fps_acceptable":false,
      "duration_seconds":5.0,"duration_acceptable":true,"audio_present":false}',
    'dark_frame_ratio_too_high',
    NULL, NULL,
    'app/uploads/juggling/t03_original.mp4',
    'app/uploads/juggling/t03_original.mp4',
    't03_original.mp4',
    256000,
    NOW() - INTERVAL '10 minutes', NOW()
);

SELECT id FROM juggling_videos
WHERE user_id = 167281 AND status = 'rejected'
ORDER BY created_at DESC LIMIT 1;
```

**Elvárt:** listában megjelenik, thumbnail hívás → 409, media hívás → 409

### T-04 — Másik user videója (cross-user 404)

```bash
# Ehhez a second usernek is kell fájl:
cp app/uploads/juggling/t01_processed.mp4 app/uploads/juggling/t04_processed.mp4
cp app/uploads/juggling/t01_thumb.jpg     app/uploads/juggling/t04_thumb.jpg
```

```sql
INSERT INTO juggling_videos (
    id, user_id,
    source_type, upload_source,
    status, transcode_status, quality_status,
    quality_score, thumbnail_path, processed_path,
    storage_path, original_path, filename_stored,
    file_size_bytes, created_at, updated_at
) VALUES (
    gen_random_uuid(), 167452,      -- OTHER user!
    'uploaded_video', 'file',
    'analyzed', 'done', 'acceptable',
    '0.90',
    'app/uploads/juggling/t04_thumb.jpg',
    'app/uploads/juggling/t04_processed.mp4',
    'app/uploads/juggling/t04_original.mp4',
    'app/uploads/juggling/t04_original.mp4',
    't04_original.mp4',
    1048576,
    NOW(), NOW()
);

-- T-04 video_id mentése (ezt a fő test user SOHA nem láthatja):
SELECT id FROM juggling_videos WHERE user_id = 167452
ORDER BY created_at DESC LIMIT 1;
```

**Elvárt:** saját `/videos` listában NEM jelenik meg, direkt API hívás → 404

### T-05 — GDPR deleted (410)

```sql
INSERT INTO juggling_videos (
    id, user_id,
    source_type, upload_source,
    status, transcode_status,
    storage_path, original_path, filename_stored,
    file_size_bytes, created_at, updated_at
) VALUES (
    gen_random_uuid(), 167281,
    'uploaded_video', 'file',
    'gdpr_deleted', 'done',
    NULL, NULL, NULL, NULL,
    NOW() - INTERVAL '1 hour', NOW()
);

SELECT id FROM juggling_videos
WHERE user_id = 167281 AND status = 'gdpr_deleted'
ORDER BY created_at DESC LIMIT 1;
```

**Elvárt:** listában NEM jelenik meg, direkt API hívás → 410

---

## 4. REST preflight terv — Konkrét parancsok

```bash
# Változók beállítása (töltsd ki a Section 3 SQL kimenetek alapján):
BASE="http://localhost:8000"
USER_EMAIL="juggling_proof_0420edf8@test.com"
USER_PASS="TestPass123!"

TOKEN=$(curl -s -X POST $BASE/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$USER_EMAIL\",\"password\":\"$USER_PASS\"}" \
  | jq -r '.access_token')

T01_VIDEO_ID="<T-01 UUID>"   # Section 3.1 SQL SELECT kimenete
T02_VIDEO_ID="<T-02 UUID>"   # Section 3.2 SQL SELECT kimenete
T03_VIDEO_ID="<T-03 UUID>"   # Section 3.3 SQL SELECT kimenete
OTHER_VIDEO_ID="<T-04 UUID>" # Section 3.4 SQL SELECT kimenete
GDPR_VIDEO_ID="<T-05 UUID>"  # Section 3.5 SQL SELECT kimenete
```

### P-01 — Login

```bash
curl -s -X POST $BASE/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$USER_EMAIL\",\"password\":\"$USER_PASS\"}" \
  | jq '{status:"ok", token_start: (.access_token[0:20]), token_type: .token_type}'
```
**Elvárt:** `{"status":"ok","token_start":"eyJ...","token_type":"bearer"}`

### P-02 — Consent set

```bash
curl -s -X POST $BASE/api/v1/users/me/juggling-consent \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"service_consent":true,"training_consent":false,"admin_review_consent":false}' \
  | jq '{service_consent:.service_consent}'
```
**Elvárt:** `{"service_consent":true}`

### P-03 — Upload init

```bash
INIT_ID=$(curl -s -X POST $BASE/api/v1/users/me/juggling/videos/upload-init \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_type":"uploaded_video","upload_source":"file"}' \
  | jq -r '.video_id')
echo "INIT_ID=$INIT_ID"
```
**Elvárt:** UUID string (nem null)

### P-04 — Upload

```bash
curl -s -X POST $BASE/api/v1/users/me/juggling/videos/$INIT_ID/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@app/uploads/juggling/t01_processed.mp4;type=video/mp4" \
  | jq '{status:.status, bytes:.file_size_bytes}'
```
**Elvárt:** `{"status":"uploaded","bytes":...}`

### P-05 — Complete (Celery trigger)

```bash
curl -s -X POST $BASE/api/v1/users/me/juggling/videos/$INIT_ID/complete \
  -H "Authorization: Bearer $TOKEN" \
  | jq '{status:.status}'
```
**Elvárt:** `{"status":"processing"}`

### P-06 — Quality poll (Celery fut → 30-60s után)

```bash
sleep 30
curl -s $BASE/api/v1/users/me/juggling/videos/$INIT_ID/quality \
  -H "Authorization: Bearer $TOKEN" \
  | jq '{status:.status, quality_status:.quality_status, quality_score:.quality_score}'
```
**Elvárt:** `{"status":"analyzed","quality_status":"acceptable",...}`  
**FAIL:** ha 60s után is `processing` → Celery worker nem fut juggling_videos queue-n

### P-07 — List endpoint

```bash
curl -s $BASE/api/v1/users/me/juggling/videos \
  -H "Authorization: Bearer $TOKEN" \
  | jq '{total:.total, first_status:(.videos[0].status), first_has_thumb:(.videos[0].has_thumbnail), first_has_media:(.videos[0].has_media)}'
```
**Elvárt:** `total >= 4` (T-01..T-03 + INIT), T-01 `has_thumbnail=true`, `has_media=true`

### P-08 — No raw path in list response

```bash
curl -s $BASE/api/v1/users/me/juggling/videos \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
forbidden = ['storage_path','processed_path','thumbnail_path','original_path']
found = [f for v in data.get('videos', []) for f in forbidden if f in v]
print('FAIL: ' + ', '.join(found)) if found else print('PASS: no raw paths')
"
```
**Elvárt:** `PASS: no raw paths`

### P-09 — Thumbnail GET (T-01)

```bash
curl -v -o /tmp/thumb_test.jpg \
  $BASE/api/v1/users/me/juggling/videos/$T01_VIDEO_ID/thumbnail \
  -H "Authorization: Bearer $TOKEN" 2>&1 \
  | grep -E "< HTTP|< Cache-Control|< Content-Type"
```
**Elvárt:** `HTTP/1.1 200`, `Content-Type: image/jpeg`, `Cache-Control: private, no-store`

### P-10 — Media GET (T-01)

```bash
curl -v -o /tmp/media_test.mp4 \
  $BASE/api/v1/users/me/juggling/videos/$T01_VIDEO_ID/media \
  -H "Authorization: Bearer $TOKEN" 2>&1 \
  | grep -E "< HTTP|< Cache-Control|< Content-Type|< Content-Length"
```
**Elvárt:** `HTTP/1.1 200` (vagy 206), `Content-Type: video/mp4`, `Cache-Control: private, no-store`

### P-11 — Media Range GET

```bash
curl -v -o /dev/null \
  $BASE/api/v1/users/me/juggling/videos/$T01_VIDEO_ID/media \
  -H "Authorization: Bearer $TOKEN" \
  -H "Range: bytes=0-65535" 2>&1 \
  | grep -E "< HTTP|< Content-Range"
```
**Elvárt:** `HTTP/1.1 206`, `Content-Range: bytes 0-65535/...`

### P-12 — Processing media 409 (T-02)

```bash
curl -s -o /dev/null -w "HTTP %{http_code}\n" \
  $BASE/api/v1/users/me/juggling/videos/$T02_VIDEO_ID/media \
  -H "Authorization: Bearer $TOKEN"
```
**Elvárt:** `HTTP 409`

### P-13 — Other user 404 (T-04)

```bash
curl -s -o /dev/null -w "HTTP %{http_code}\n" \
  $BASE/api/v1/users/me/juggling/videos/$OTHER_VIDEO_ID/media \
  -H "Authorization: Bearer $TOKEN"
```
**Elvárt:** `HTTP 404`

### P-14 — GDPR deleted 410 (T-05)

```bash
curl -s -o /dev/null -w "HTTP %{http_code}\n" \
  $BASE/api/v1/users/me/juggling/videos/$GDPR_VIDEO_ID/media \
  -H "Authorization: Bearer $TOKEN"
```
**Elvárt:** `HTTP 410`

### P-15 — Original endpoint nem létezik

```bash
curl -s -o /dev/null -w "HTTP %{http_code}\n" \
  "$BASE/api/v1/users/me/juggling/videos/$T01_VIDEO_ID/original" \
  -H "Authorization: Bearer $TOKEN"
```
**Elvárt:** `HTTP 404` vagy `422`

---

## 5. iOS build terv

### Branch szabály

**Kizárólag `main` branch-ről szabad buildelni a Juggling validációhoz.**

```bash
# Xcode-ból vagy terminálból:
git checkout main
git pull origin main   # HEAD-et ellenőrizd: e67ee91f

# Juggling fájlok ellenőrzése:
ls ios/LFAEducationCenter/Juggling/
# Elvár: JugglingVideoItem.swift, JugglingVideoListView.swift,
#        JugglingVideoListViewModel.swift, JugglingPlayerView.swift
```

### APIConfig.baseURL beállítása (LOCAL-ONLY)

**Jelenlegi érték** ([ios/LFAEducationCenter/Networking/APIConfig.swift:13](ios/LFAEducationCenter/Networking/APIConfig.swift#L13)):

```swift
static let baseURL = "http://192.168.1.129:8000"
```

Ez **már helyes** a fizikai iPhone teszteléshez — a Mac LAN IP `192.168.1.129`.

> ⚠️ Ha a LAN IP megváltozott: `ifconfig | grep "inet " | grep -v 127` → frissítsd az értéket.  
> A módosítás LOCAL-ONLY, ne commitold: `git restore ios/LFAEducationCenter/Networking/APIConfig.swift`

### Build lépések

| # | Lépés | Részlet |
|---|-------|---------|
| B-01 | Xcode megnyitása | `open ios/LFAEducationCenter.xcodeproj` |
| B-02 | Branch ellenőrzés | Xcode source control vagy terminál: `git branch --show-current` → `main` |
| B-03 | Scheme | `LFAEducationCenter` (nem test target) |
| B-04 | Destination | Csatlakoztatott fizikai iPhone |
| B-05 | baseURL ellenőrzés | APIConfig.swift:13 → `http://192.168.1.129:8000` |
| B-06 | Build (⌘B vagy Product → Build) | **BUILD SUCCEEDED, 0 error, 0 warning** |
| B-07 | Run (⌘R vagy Product → Run) | App telepítve és elindult iPhone-on |
| B-08 | git diff ellenőrzés | `git diff ios/LFAEducationCenter/Networking/APIConfig.swift` → üres (nincs commitolnivaló) |

### Biometric spike branch szabály

A `feat/biometric-auto-capture-spike` branch NEM tartalmazza a Juggling UI fájljait.
Ha véletlenül arra van checkoutolva:

```bash
git checkout main  # NEM: git checkout feat/biometric-auto-capture-spike
```

---

## 6. Readiness Gate — READY_FOR_STAGING_DEVICE_VALIDATION feltételei

### Infrastruktúra (E-sorok)

- [ ] E-03: `JUGGLING_POC_ENABLED=true` `.env`-ben, server újraindítva
- [ ] E-03 ellenőrzés: `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/v1/users/me/juggling/videos -H "Authorization: Bearer FAKE"` → `401` (nem `503`)
- [ ] E-06: Celery worker fut `juggling_videos` queue-n (`celery -A app.celery_app worker -Q juggling_videos --pool=solo`)
- [ ] E-04: `JUGGLING_UPLOAD_DIR` írható
- [ ] E-07: Redis UP (már OK)
- [ ] E-08: ffmpeg elérhető (már OK)
- [ ] E-09: ffprobe elérhető (már OK)
- [ ] E-10: PostgreSQL + DB elérhető (már OK)

### Teszt user (U-sorok)

- [ ] U-01: Test user email + jelszó ismert és működik (login → access_token)
- [ ] U-02: `service_consent=true` beállítva
- [ ] U-03: Refresh token működik

### Tesztadatok (T-sorok)

- [ ] T-01: `analyzed` + `transcode_status=done` rekord, fizikai `.mp4` és `.jpg` fájlok disk-en, `has_thumbnail=true`, `has_media=true`
- [ ] T-02: `processing` státusz rekord
- [ ] T-03: `rejected` státusz rekord
- [ ] T-04: Másik user `analyzed` rekordja, video_id ismert
- [ ] T-05: `gdpr_deleted` státusz rekord, video_id ismert

### REST preflight (P-sorok)

- [ ] P-01..P-15: Mind PASS

### iOS build (B-sorok)

- [ ] B-01..B-08: BUILD SUCCEEDED, `main` branch, `192.168.1.129:8000` baseURL, nem commitolva

### Branch tisztaság

- [ ] `git branch --show-current` → `main` Xcode buildhez
- [ ] `git diff ios/LFAEducationCenter/Networking/APIConfig.swift` → üres
- [ ] Biometric fájlok nem módosítva a Juggling validáció során

---

## 7. Verdikt

### Aktuális státusz (2026-06-12)

```
┌──────────────────────────────────────────────────────────────────┐
│ OVERALL: BLOCKED                                                   │
│                                                                    │
│ ❌ BLOCKED_MISSING_STAGING — RÉSZBEN FELOLDHATÓ                    │
│    Nincs külső staging URL, de a lokális dev env elegendő:         │
│    • curl preflight: http://localhost:8000                         │
│    • iPhone tesztelés: http://192.168.1.129:8000 (már konfigurálva)│
│    Blokkoló akciók:                                                │
│    1. JUGGLING_POC_ENABLED=true hozzáadása .env-hez               │
│    2. Celery worker indítása juggling_videos queue-ra              │
│                                                                    │
│ ❌ BLOCKED_MISSING_TEST_DATA                                        │
│    Meglévő DB rekordok: has_thumbnail=false, has_media=false       │
│    T-01..T-05 rekordok és fizikai fájlok hiányoznak               │
│    → Section 3 lépéseivel feloldható                               │
│                                                                    │
│ ✅ BLOCKED_IOS_NO_VIEW — VISSZAVONVA (origin/main baseline)        │
│ ✅ ffmpeg / ffprobe / Redis / PostgreSQL — KÉSZ                     │
│ ✅ APIConfig.baseURL — MÁR HELYES (192.168.1.129:8000)             │
└──────────────────────────────────────────────────────────────────┘
```

### Szükséges akciók a READY_FOR_STAGING_DEVICE_VALIDATION kinyilvánításához

| Prioritás | Akció | Szekció |
|-----------|-------|---------|
| 1 (AZONNALI) | `JUGGLING_POC_ENABLED=true` → `.env` + server restart | §1 Akció 1 |
| 2 (AZONNALI) | Celery worker indítása `juggling_videos` queue-n | §1 Akció 2 |
| 3 | Test user jelszó reset vagy új user létrehozás | §2 |
| 4 | T-01 fizikai fájlok (ffmpeg-gel generálható) | §3.1 |
| 5 | T-01..T-05 SQL INSERT-ek futtatása | §3.1–3.5 |
| 6 | P-01..P-15 REST preflight futtatása | §4 |
| 7 | iOS build `main` branch-ről (⌘B) | §5 |
| 8 | M-01..M-17 iPhone manual checklist | [Checklist §6](P5_PHASE2_STAGING_EXECUTION_CHECKLIST.md#6-iphone-manual-checklist) |

---

*Utolsó frissítés: 2026-06-12 | Állapot: BLOCKED_MISSING_STAGING (feloldható) + BLOCKED_MISSING_TEST_DATA*
