# P5 Phase 2 — Staging Execution Checklist

**Dátum:** 2026-06-12  
**Branch:** main @ e67ee91f  
**Státusz:** BLOCKED_MISSING_STAGING + BLOCKED_MISSING_TEST_DATA  
**Scope:** Backend + iOS staging validation — Juggling media flow (P1–P5)

---

## Baseline Re-check Riport (2026-06-12)

### Hiba az előző verzióban

Az előző checklist `BLOCKED_IOS_NO_VIEW` verdiktet adott, mert a vizsgálat
a `feat/biometric-auto-capture-spike` lokális munkaterületén futott,
nem az `origin/main`-en. Ez téves volt.

### Javított baseline (origin/main alapján)

| Elem | Elvárt | origin/main tényleges | Státusz |
|------|--------|----------------------|---------|
| main HEAD SHA | e67ee91fe973eb532f8d7edc9e08fbc62474c4d1 | e67ee91fe973eb532f8d7edc9e08fbc62474c4d1 | ✅ EGYEZIK |
| main HEAD commit | feat(juggling): add P5 iOS media UI | feat(juggling): add P5 iOS media UI | ✅ |
| JugglingVideoItem.swift | létezik | ✅ ios/LFAEducationCenter/Juggling/ | ✅ |
| JugglingVideoListView.swift | létezik | ✅ ios/LFAEducationCenter/Juggling/ | ✅ |
| JugglingVideoListViewModel.swift | létezik | ✅ ios/LFAEducationCenter/Juggling/ | ✅ |
| JugglingPlayerView.swift | létezik | ✅ ios/LFAEducationCenter/Juggling/ | ✅ |
| Training tab | JugglingVideoListView() | ✅ (LFASpecTabView.swift:21) | ✅ |
| Training tab icon | video.fill | ✅ | ✅ |
| APIClient.fetchData | létezik | ✅ (APIClient.swift:84) | ✅ |
| AuthManager.authenticatedFetchData | létezik | ✅ (AuthManager.swift:250) | ✅ |
| JugglingPlayerView AVKit/AVPlayer | import AVKit + AVPlayer | ✅ (JugglingPlayerView.swift:2,109) | ✅ |
| P5 Phase 1b verdikt | BUILD_VERIFIED_NOT_DEVICE_VALIDATED | megerősítve | ✅ |

**BLOCKED_IOS_NO_VIEW VISSZAVONVA.** Az iOS UI teljes egészében mainen van.

### Aktuális branch helyzet vizsgálatkor

```
Branch:          feat/biometric-auto-capture-spike (7db0b899)
origin/main:     e67ee91f  feat(juggling): add P5 iOS media UI
Különbség:       a feat/biometric spike branch az iOS Juggling UI merge előtti
                 pontból ágazott el → a lokális fájlrendszer NEM tartalmazta
                 a Juggling nézetek fájljait
```

A vizsgálatot ezért `git show origin/main:<path>` és `git ls-tree origin/main` 
parancsokkal végeztük el (branch váltás és clean nélkül), hogy a lokális
munkaterület untracked fájljait (`.claude/`, biometric spike munkák) megőrizzük.

---

## Branch szétválasztási szabály (KÖTELEZŐ)

### Juggling Phase 2 flow

- Kizárólag `origin/main` alapján indul (`e67ee91f` vagy újabb).
- Staging/device validation, test data és Juggling dokumentáció külön Juggling
  branch-en menjen (pl. `feat/juggling-p5-phase2-staging-validation`).
- **Biometric fájlokat tilos érinteni** (`ios/LFAEducationCenter/Biometric/`,
  `app/services/biometric/`, `app/api/*/biometric*`).

### Biometric spike flow

- Külön marad a `feat/biometric-auto-capture-spike` branch-en.
- **Juggling fájlokat tilos érinteni** (`ios/LFAEducationCenter/Juggling/`,
  `app/api/*/juggling*`, `app/tasks/juggling*`).
- Local-only flag módosítás (pl. `kBiometricAutoCaptureSpikeEnabled = true`)
  **nem commitolható**.

### Cross-contamination szabály

A két flow párhuzamos fejlesztés — egymást nem érintik. A `feat/biometric-auto-capture-spike`
branch az iOS Juggling UI merge (`e67ee91f`) előtti pontból ágazott el, ezért
**nem használható Juggling validációhoz** (a Juggling UI fájlok azon a branch-en
nem léteznek).

---

## Előzmény

P5 Phase 1b CLOSED — BUILD_VERIFIED_NOT_DEVICE_VALIDATED.

A backend teljes Juggling pipeline (P1 upload intake, P2 transcode, P3 retention,
P4 media endpoints, P5 list endpoint) mainen van, 31 JVL + 34 PM + 20 MS unit teszt PASS.

Az iOS UI (JugglingVideoListView, JugglingPlayerView, JugglingVideoListViewModel,
JugglingVideoItem) szintén mainen van, `e67ee91f` commit.

Az iPhone/staging validáció jelenleg két okból blokkolt:
- **BLOCKED_MISSING_STAGING** — staging URL ismeretlen
- **BLOCKED_MISSING_TEST_DATA** — T-01..T-05 tesztadatok hiányoznak

---

## 1. Staging környezet ellenőrzése

### 1.1 Staging URL

| Változó | Elvárt érték | Jelenlegi ismert érték | Státusz |
|---------|-------------|----------------------|---------|
| Staging base URL | `https://staging.your-domain.com` | **ISMERETLEN** | ❌ BLOCKER |
| HTTPS | működik | ellenőrizendő | ⬜ |

> **BLOCKED_MISSING_STAGING** — Nincs ismert staging URL.
> Ha csak local dev server érhető el, az elfogadható a backend preflight validációhoz
> (curl tesztek), de fizikai iPhone tesztre nem elegendő (CORS + HTTPS).

### 1.2 Backend environment checklist

Minden elemet ellenőrizz és töltsd ki a staging szerveren:

```bash
# 1. FastAPI process fut
curl -s https://STAGING_URL/api/v1/health | jq .
# PASS ha: {"status":"ok"} vagy 200-as válasz

# 2. JUGGLING_POC_ENABLED=true
curl -s https://STAGING_URL/api/v1/users/me/juggling/videos \
  -H "Authorization: Bearer INVALID" | jq .
# PASS ha: 401 (nem 503)
# FAIL ha: {"detail":"Juggling POC is not enabled on this server."} → flag off

# 3. DATABASE_URL staging DB-re mutat
# Ellenőrzés: sikeres login + user_id visszaadás

# 4. JUGGLING_UPLOAD_DIR létezik és írható
ls -la /var/juggling/uploads/  # (vagy a konfig szerinti path)
# PASS ha: könyvtár létezik + write jogosultság

# 5. Redis / Celery broker
redis-cli ping
# PASS ha: PONG

# 6. Celery worker fut a juggling_videos queue-n
celery -A app.celery_app inspect active_queues | grep juggling_videos
# PASS ha: juggling_videos szerepel

# 7. ffmpeg elérhető
ffmpeg -version | head -1
# PASS ha: "ffmpeg version X.X.X"

# 8. ffprobe elérhető
ffprobe -version | head -1
# PASS ha: "ffprobe version X.X.X"
```

### 1.3 Checklist táblázat

| # | Elem | Parancs / ellenőrzés | PASS/FAIL | Megjegyzés |
|---|------|---------------------|-----------|------------|
| E-01 | Staging URL ismert | URL dokumentálva | ⬜ | |
| E-02 | HTTPS működik | curl -I https://STAGING_URL | ⬜ | |
| E-03 | JUGGLING_POC_ENABLED=true | 401 (nem 503) tesztre | ⬜ | |
| E-04 | JUGGLING_UPLOAD_DIR létezik+írható | ls -la | ⬜ | |
| E-05 | FastAPI process látja a storage-ot | upload-init → upload teszt | ⬜ | |
| E-06 | Celery worker fut (juggling_videos) | inspect | ⬜ | |
| E-07 | Redis broker működik | redis-cli ping | ⬜ | |
| E-08 | ffmpeg elérhető | ffmpeg -version | ⬜ | |
| E-09 | ffprobe elérhető | ffprobe -version | ⬜ | |
| E-10 | DATABASE_URL staging DB-re mutat | login + user_id check | ⬜ | |
| E-11 | SECRET_KEY / auth config működik | login → access_token kap | ⬜ | |

---

## 2. Teszt user létrehozása

### 2.1 User setup

```bash
# Staging DB-n (psql vagy admin script):

INSERT INTO users (
    email, password_hash, is_active, role, full_name,
    phone_number, created_at, updated_at
) VALUES (
    'juggling-test@staging.lfa.internal',
    -- bcrypt hash of 'TestPass123!'
    '$2b$12$<GENERATE_BCRYPT_HASH>',
    true,
    'student',
    'Juggling Test User',
    NULL,
    NOW(), NOW()
);

-- Generálás Pythonból:
-- python -c "from passlib.context import CryptContext; c=CryptContext(schemes=['bcrypt']); print(c.hash('TestPass123!'))"
```

Szükséges mezők:
- `email`: `juggling-test@staging.lfa.internal`
- `role`: `student`
- `is_active`: `true`
- Jelszó: `TestPass123!`

### 2.2 Consent beállítása

```bash
# Login → access_token megszerzése
TOKEN=$(curl -s -X POST https://STAGING_URL/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"juggling-test@staging.lfa.internal","password":"TestPass123!"}' \
  | jq -r '.access_token')

echo "TOKEN=$TOKEN"

# Consent beállítása (service_consent=true kötelező az upload-init előtt)
curl -s -X POST https://STAGING_URL/api/v1/users/me/juggling-consent \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"service_consent":true,"training_consent":false,"admin_review_consent":false}' | jq .
# PASS ha: {"service_consent":true, ...}
```

### 2.3 Refresh token

```bash
REFRESH=$(curl -s -X POST https://STAGING_URL/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"juggling-test@staging.lfa.internal","password":"TestPass123!"}' \
  | jq -r '.refresh_token')

curl -s -X POST https://STAGING_URL/api/v1/auth/refresh \
  -H "Content-Type: application/json" \
  -d "{\"refresh_token\":\"$REFRESH\"}" | jq .access_token
# PASS ha: új access_token string kapunk
```

### 2.4 Admin user (T-04 cross-user teszthez)

```sql
INSERT INTO users (email, password_hash, is_active, role, full_name, created_at, updated_at)
VALUES (
    'juggling-other@staging.lfa.internal',
    '$2b$12$<BCRYPT_HASH_OF_OtherPass456!>',
    true, 'student', 'Other User', NOW(), NOW()
);
```

---

## 3. Tesztadat létrehozása

A tesztadatokat közvetlen DB INSERT-tel érdemes létrehozni (megkerüli a Celery pipeline-t),
majd a fizikai fájlokat is le kell helyezni a JUGGLING_UPLOAD_DIR-be.

### 3.1 T-01 — Happy path (analyzed + transcode done)

```sql
-- User ID lekérdezése:
SELECT id FROM users WHERE email = 'juggling-test@staging.lfa.internal';
-- Tegyük fel: user_id = 99

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
    gen_random_uuid(), 99,
    'uploaded_video', 'file',
    'analyzed', 'done', 'acceptable',
    '0.82',
    '{"blur_score":0.85,"dark_frame_ratio":0.02,"fps_detected":30.0,"fps_acceptable":true,"duration_seconds":15.3,"duration_acceptable":true,"audio_present":false}',
    '/var/juggling/uploads/t01_thumb.jpg',
    '/var/juggling/uploads/t01_processed.mp4',
    '/var/juggling/uploads/t01_original.mp4',
    '/var/juggling/uploads/t01_original.mp4',
    't01_original.mp4',
    1048576,
    '1280x720', 30.0, 900000,
    true,
    'abc123def456abc123def456abc123def456abc123def456abc123def456abc1',
    NOW(), NOW()
);
```

**Fizikai fájlok elhelyezése:**
```bash
cp /path/to/test_clip.mp4 /var/juggling/uploads/t01_processed.mp4
cp /path/to/test_clip.mp4 /var/juggling/uploads/t01_original.mp4
# Thumbnail (JPEG, bármely méret):
cp /path/to/any.jpg /var/juggling/uploads/t01_thumb.jpg
# vagy convert paranccsal: convert -size 1280x720 xc:black /var/juggling/uploads/t01_thumb.jpg
```

### 3.2 T-02 — Processing (pipeline fut)

```sql
INSERT INTO juggling_videos (
    id, user_id,
    source_type, upload_source,
    status, transcode_status, quality_status,
    thumbnail_path, processed_path,
    storage_path, original_path, filename_stored,
    file_size_bytes, created_at, updated_at
) VALUES (
    gen_random_uuid(), 99,
    'in_app_capture', 'camera',
    'processing', 'processing', 'pending',
    NULL, NULL,
    '/var/juggling/uploads/t02_original.mp4',
    '/var/juggling/uploads/t02_original.mp4',
    't02_original.mp4',
    524288,
    NOW() - INTERVAL '30 seconds', NOW()
);
```

Elvárt: `has_thumbnail=false`, `has_media=false`, Play gomb disabled iOS-en.

### 3.3 T-03 — Rejected (quality gate elbukott)

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
    gen_random_uuid(), 99,
    'uploaded_video', 'gallery',
    'rejected', 'done', 'rejected',
    '0.21',
    '{"blur_score":0.15,"dark_frame_ratio":0.85,"fps_detected":15.0,"fps_acceptable":false,"duration_seconds":5.0,"duration_acceptable":true,"audio_present":false}',
    'dark_frame_ratio_too_high',
    NULL, NULL,
    '/var/juggling/uploads/t03_original.mp4',
    '/var/juggling/uploads/t03_original.mp4',
    't03_original.mp4',
    256000,
    NOW() - INTERVAL '10 minutes', NOW()
);
```

Elvárt: lista megjeleníti, media/thumbnail 409 (not ready), play nem elérhető.

### 3.4 T-04 — Másik user videója (cross-user 404)

```sql
-- Futtatandó a másik user (juggling-other) user_id-jával:
INSERT INTO juggling_videos (
    id, user_id,
    source_type, upload_source,
    status, transcode_status, quality_status,
    quality_score, thumbnail_path, processed_path,
    storage_path, original_path, filename_stored,
    file_size_bytes, created_at, updated_at
) VALUES (
    gen_random_uuid(), <other_user_id>,
    'uploaded_video', 'file',
    'analyzed', 'done', 'acceptable',
    '0.90',
    '/var/juggling/uploads/t04_thumb.jpg',
    '/var/juggling/uploads/t04_processed.mp4',
    '/var/juggling/uploads/t04_original.mp4',
    '/var/juggling/uploads/t04_original.mp4',
    't04_original.mp4',
    1048576,
    NOW(), NOW()
);

-- Jegyezd fel a video_id-t a curl teszthez:
SELECT id FROM juggling_videos WHERE user_id = <other_user_id>;
```

### 3.5 T-05 — GDPR deleted (410 teszt)

```sql
INSERT INTO juggling_videos (
    id, user_id,
    source_type, upload_source,
    status, transcode_status,
    storage_path, original_path, filename_stored,
    file_size_bytes, created_at, updated_at
) VALUES (
    gen_random_uuid(), 99,
    'uploaded_video', 'file',
    'gdpr_deleted', 'done',
    NULL, NULL, NULL, NULL,
    NOW() - INTERVAL '1 hour', NOW()
);

SELECT id FROM juggling_videos WHERE status = 'gdpr_deleted' AND user_id = 99;
```

Elvárt: lista NEM mutatja, direkt hívás 410.

---

## 4. REST/API preflight validáció (curl)

Futtasd le ezeket az iPhone tesztelése előtt.

```bash
BASE="https://STAGING_URL"
TOKEN="<access_token_from_login>"
T01_VIDEO_ID="<T-01 video UUID>"
T02_VIDEO_ID="<T-02 video UUID>"
T03_VIDEO_ID="<T-03 video UUID>"
OTHER_VIDEO_ID="<T-04 video UUID>"
GDPR_VIDEO_ID="<T-05 video UUID>"
```

### 4.1 API-01 — Login

```bash
curl -s -X POST $BASE/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"juggling-test@staging.lfa.internal","password":"TestPass123!"}' \
  | jq '{access_token: .access_token[0:20], token_type: .token_type}'
```

**Elvárt:** `{"access_token":"eyJ...","token_type":"bearer"}`

### 4.2 API-02 — Consent set

```bash
curl -s -X POST $BASE/api/v1/users/me/juggling-consent \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"service_consent":true,"training_consent":false,"admin_review_consent":false}' | jq .
```

**Elvárt:** `{"service_consent":true,...}`

### 4.3 API-03 — Upload init

```bash
INIT_VIDEO_ID=$(curl -s -X POST $BASE/api/v1/users/me/juggling/videos/upload-init \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_type":"uploaded_video","upload_source":"file"}' | jq -r .video_id)
echo "INIT_VIDEO_ID=$INIT_VIDEO_ID"
```

**Elvárt:** UUID string

### 4.4 API-04 — Upload

```bash
curl -s -X POST $BASE/api/v1/users/me/juggling/videos/$INIT_VIDEO_ID/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@/path/to/test_clip.mp4;type=video/mp4" | jq .
```

**Elvárt:** `{"status":"uploaded","file_size_bytes":...}`

### 4.5 API-05 — Complete (Celery trigger)

```bash
curl -s -X POST $BASE/api/v1/users/me/juggling/videos/$INIT_VIDEO_ID/complete \
  -H "Authorization: Bearer $TOKEN" | jq .
```

**Elvárt:** `{"status":"processing"}`

### 4.6 API-06 — Quality poll

```bash
# 10-30 másodperccel az API-05 után
curl -s $BASE/api/v1/users/me/juggling/videos/$INIT_VIDEO_ID/quality \
  -H "Authorization: Bearer $TOKEN" \
  | jq '{status:.status, quality_status:.quality_status, quality_score:.quality_score}'
```

**Elvárt:** `{"status":"analyzed","quality_status":"acceptable",...}`  
**Timeout:** ha 60s után is `processing` → BLOCKED_MISSING_CELERY_OR_FFMPEG

### 4.7 API-07 — List endpoint

```bash
curl -s $BASE/api/v1/users/me/juggling/videos \
  -H "Authorization: Bearer $TOKEN" \
  | jq '{total:.total, count:(.videos|length), first_status:(.videos[0].status), has_thumbnail:(.videos[0].has_thumbnail), has_media:(.videos[0].has_media)}'
```

**Elvárt:** `total >= 3`, T-04 és T-05 NEM szerepel

### 4.8 API-08 — Thumbnail GET (T-01)

```bash
curl -v -o /dev/null $BASE/api/v1/users/me/juggling/videos/$T01_VIDEO_ID/thumbnail \
  -H "Authorization: Bearer $TOKEN" 2>&1 | grep -E "< HTTP|Cache-Control|Content-Type"
```

**Elvárt:** `200`, `Content-Type: image/jpeg`, `Cache-Control: private, no-store`

### 4.9 API-09 — Media GET (T-01)

```bash
curl -v -o /dev/null $BASE/api/v1/users/me/juggling/videos/$T01_VIDEO_ID/media \
  -H "Authorization: Bearer $TOKEN" 2>&1 | grep -E "< HTTP|Cache-Control|Content-Type"
```

**Elvárt:** `200` vagy `206`, `Content-Type: video/mp4`, `Cache-Control: private, no-store`

### 4.10 API-10 — Media Range GET

```bash
curl -v -o /dev/null $BASE/api/v1/users/me/juggling/videos/$T01_VIDEO_ID/media \
  -H "Authorization: Bearer $TOKEN" \
  -H "Range: bytes=0-65535" 2>&1 | grep -E "< HTTP|Content-Range"
```

**Elvárt:** `206`, `Content-Range: bytes 0-65535/...`

### 4.11 API-11 — Processing media 409 (T-02)

```bash
curl -s -o /dev/null -w "%{http_code}" \
  $BASE/api/v1/users/me/juggling/videos/$T02_VIDEO_ID/media \
  -H "Authorization: Bearer $TOKEN"
```

**Elvárt:** `409`

### 4.12 API-12 — Other user 404 (T-04)

```bash
curl -s -o /dev/null -w "%{http_code}" \
  $BASE/api/v1/users/me/juggling/videos/$OTHER_VIDEO_ID/media \
  -H "Authorization: Bearer $TOKEN"
```

**Elvárt:** `404`

### 4.13 API-13 — GDPR deleted 410 (T-05)

```bash
curl -s -o /dev/null -w "%{http_code}" \
  $BASE/api/v1/users/me/juggling/videos/$GDPR_VIDEO_ID/media \
  -H "Authorization: Bearer $TOKEN"
```

**Elvárt:** `410`

### 4.14 API-14 — Feature flag off 503

```bash
# Átmenetileg JUGGLING_POC_ENABLED=false + restart, majd:
curl -s -o /dev/null -w "%{http_code}" \
  $BASE/api/v1/users/me/juggling/videos \
  -H "Authorization: Bearer $TOKEN"
# Elvárt: 503
# Visszakapcsolni: JUGGLING_POC_ENABLED=true + restart
```

### 4.15 API-15 — No raw path in list response

```bash
curl -s $BASE/api/v1/users/me/juggling/videos \
  -H "Authorization: Bearer $TOKEN" | \
  python3 -c "
import json, sys
data = json.load(sys.stdin)
forbidden = ['storage_path','processed_path','thumbnail_path','original_path','quality_detail']
for video in data.get('videos', []):
    for field in forbidden:
        if field in video:
            print(f'FAIL: {field} present in response')
            sys.exit(1)
print('PASS: no raw path/detail fields in list response')
"
```

**Elvárt:** `PASS: no raw path/detail fields in list response`

### 4.16 Preflight eredménytáblázat

| API ID | Endpoint | Elvárt HTTP | Tényleges | PASS/FAIL |
|--------|----------|-------------|-----------|-----------|
| API-01 | POST /auth/login | 200 + access_token | ⬜ | ⬜ |
| API-02 | POST /juggling-consent | 200 | ⬜ | ⬜ |
| API-03 | POST upload-init | 201 | ⬜ | ⬜ |
| API-04 | POST upload | 200 | ⬜ | ⬜ |
| API-05 | POST complete | 200, status=processing | ⬜ | ⬜ |
| API-06 | GET quality (poll) | 200, status=analyzed | ⬜ | ⬜ |
| API-07 | GET /videos (list) | 200, total>=3 | ⬜ | ⬜ |
| API-08 | GET thumbnail T-01 | 200, image/jpeg, private no-store | ⬜ | ⬜ |
| API-09 | GET media T-01 | 200/206, video/mp4 | ⬜ | ⬜ |
| API-10 | GET media Range | 206, Content-Range | ⬜ | ⬜ |
| API-11 | GET media T-02 (proc) | 409 | ⬜ | ⬜ |
| API-12 | GET media T-04 (other) | 404 | ⬜ | ⬜ |
| API-13 | GET media T-05 (gdpr) | 410 | ⬜ | ⬜ |
| API-14 | GET list (flag off) | 503 | ⬜ | ⬜ |
| API-15 | No raw path in list | PASS | ⬜ | ⬜ |

---

## 5. iOS build konfiguráció

### 5.1 main-ről kell buildelni

Az iOS Juggling UI (`JugglingVideoListView`, `JugglingPlayerView`) az `origin/main`
`e67ee91f` commiton van. A `feat/biometric-auto-capture-spike` branch **nem tartalmazza**
ezeket a fájlokat, mert a spike branch az iOS UI merge előtti pontból ágazott el.

**iPhone validációhoz:** `main` branch-ről kell buildelni.

### 5.2 APIConfig.baseURL staging beállítása

**Jelenlegi érték** ([ios/LFAEducationCenter/Networking/APIConfig.swift:13](ios/LFAEducationCenter/Networking/APIConfig.swift#L13)):

```swift
static let baseURL = "http://192.168.1.129:8000"
```

**Staging-re állításhoz** (LOCAL-ONLY módosítás, NEM commitolható):

```swift
static let baseURL = "https://STAGING_URL"
```

> **FIGYELEM:** Ez a módosítás ne kerüljön commitba.
> Ha tartós staging config kell, külön Xcode Scheme + xcconfig kell.
> Addig: local edit → build → test → `git restore ios/LFAEducationCenter/Networking/APIConfig.swift`

### 5.3 Build checklist

| # | Elem | Érték | Státusz |
|---|------|-------|---------|
| B-01 | Xcode verzió | ≥ 15.x (dokumentáld) | ⬜ |
| B-02 | Branch | main (nem feat/biometric-spike) | ⬜ |
| B-03 | Scheme | LFAEducationCenter (nem test) | ⬜ |
| B-04 | Destination | Physical iPhone (nem Simulator) | ⬜ |
| B-05 | baseURL staging-re állítva (local only) | https://STAGING_URL | ⬜ |
| B-06 | Build result | BUILD SUCCEEDED, 0 error | ⬜ |
| B-07 | App telepítve az iPhone-ra | ✓ | ⬜ |
| B-08 | staging baseURL NEM commitolva | git diff clean | ⬜ |

---

## 6. iPhone manual checklist

> **ELŐFELTÉTEL:** E-01..E-11 PASS + T-01..T-05 léteznek + B-01..B-08 PASS

| M-ID | Lépés | Elvárt eredmény | Tényleges | PASS/FAIL |
|------|-------|-----------------|-----------|-----------|
| M-01 | App indítás | Splash → LoginView | ⬜ | ⬜ |
| M-02 | Login (test user) | Dashboard megjelenik | ⬜ | ⬜ |
| M-03 | LFA Football Player kártya → Unlock | LFASpecTabView megjelenik | ⬜ | ⬜ |
| M-04 | Training tab tap | JugglingVideoListView betölt | ⬜ | ⬜ |
| M-05 | Lista betölt (API hívás) | T-01, T-02, T-03 sor látszik | ⬜ | ⬜ |
| M-06 | T-01 thumbnail megjelenik | JPEG thumbnail a listában | ⬜ | ⬜ |
| M-07 | T-02 processing badge | "Processing…" vagy spinner | ⬜ | ⬜ |
| M-08 | T-02 Play gomb disabled | Play nem tapintható | ⬜ | ⬜ |
| M-09 | T-01 Play gomb tap | JugglingPlayerView megjelenik | ⬜ | ⬜ |
| M-10 | Videó betölt (AVPlayer) | Videó frame megjelenik, nem fekete | ⬜ | ⬜ |
| M-11 | Lejátszás indul | Videó megy, progress bar mozog | ⬜ | ⬜ |
| M-12 | Seeking (scrubbing) | Seek reagál, playback folytatódik | ⬜ | ⬜ |
| M-13 | JugglingPlayerView close | Visszatér a listába | ⬜ | ⬜ |
| M-14 | Logout | LoginView megjelenik | ⬜ | ⬜ |
| M-15 | Offline: repülő módba kapcsol | Lista error/empty state (nem crash) | ⬜ | ⬜ |
| M-16 | Offline: videó lejátszás | Playback error state (nem crash) | ⬜ | ⬜ |
| M-17 | Online visszaállítás | Lista újratölt | ⬜ | ⬜ |

---

## 7. Network/security validation

Proxy (Charles / Proxyman / mitmproxy) szükséges az iPhone forgalom vizsgálatához.

| S-ID | Ellenőrzés | Elvárt | Tényleges | PASS/FAIL |
|------|-----------|--------|-----------|-----------|
| S-01 | GET /videos kérés fejléce | `Authorization: Bearer eyJ...` jelen van | ⬜ | ⬜ |
| S-02 | GET /thumbnail kérés fejléce | `Authorization: Bearer eyJ...` jelen van | ⬜ | ⬜ |
| S-03 | GET /media kérés fejléce | `Authorization: Bearer eyJ...` jelen van | ⬜ | ⬜ |
| S-04 | /videos response body | `storage_path` NEM szerepel | ⬜ | ⬜ |
| S-05 | /videos response body | `processed_path` NEM szerepel | ⬜ | ⬜ |
| S-06 | /videos response body | `thumbnail_path` NEM szerepel | ⬜ | ⬜ |
| S-07 | /thumbnail response | `Cache-Control: private, no-store` | ⬜ | ⬜ |
| S-08 | /media response | `Cache-Control: private, no-store` | ⬜ | ⬜ |
| S-09 | Nincs public URL | Semmi `http://` raw URL a response bodyban | ⬜ | ⬜ |
| S-10 | Nincs signed URL | Semmi `?X-Amz-Signature` vagy `?token=` | ⬜ | ⬜ |
| S-11 | Nincs /original endpoint | GET /original → 404/422 | ⬜ | ⬜ |
| S-12 | T-02 media 409 | Processing status → 409 (nem 500) | ⬜ | ⬜ |
| S-13 | T-04 media 404 | Other user → 404 (nem 200) | ⬜ | ⬜ |
| S-14 | T-05 media 410 | GDPR deleted → 410 | ⬜ | ⬜ |

---

## 8. Eredmény riport

> Töltsd ki a validáció elvégzése után.

| Test ID | Platform | Lépés | Elvárt eredmény | Tényleges eredmény | PASS/FAIL | Hiba kategória | Megjegyzés |
|---------|----------|-------|-----------------|-------------------|-----------|---------------|------------|
| E-01..E-11 | Backend | Env check | ld. Szekció 1 | ⬜ | ⬜ | | |
| API-01..API-15 | curl/Backend | Preflight | ld. Szekció 4 | ⬜ | ⬜ | | |
| B-01..B-08 | iOS Build | Build config | ld. Szekció 5 | ⬜ | ⬜ | | |
| M-01..M-17 | iPhone | Manual | ld. Szekció 6 | ⬜ | ⬜ | | |
| S-01..S-14 | iPhone Proxy | Security | ld. Szekció 7 | ⬜ | ⬜ | | |

---

## 9. Failure classification

| Kategória | Jel | Megoldás |
|-----------|-----|---------|
| `staging_config_issue` | 503 minden juggling endpointon | JUGGLING_POC_ENABLED=true staging .env-ben |
| `missing_feature_flag` | ld. fent | ld. fent |
| `missing_celery_or_ffmpeg` | API-06 `processing` marad 60s+ után | Celery worker / ffmpeg installálás |
| `missing_test_data` | Lista üres, T-01..T-05 nincs | SQL INSERT + fájl elhelyezés |
| `ios_auth_issue` | M-02 FAIL, 401 az API hívásokon | Token inject hiba |
| `ios_wrong_branch` | Training tab PlaceholderScreen | main branch-ről kell buildelni (nem biometric spike) |
| `ios_playback_issue` | M-10/M-11 FAIL | AVPlayer config hiba |
| `backend_media_endpoint_issue` | API-08/API-09 FAIL | FileResponse / path resolution |
| `storage_file_issue` | API-08 200 de üres body | Fizikai fájl hiányzik |
| `ux_issue` | M funkció megvan de rossz UX | iOS layout/state hiba |

---

## 10. Verdikt

### Aktuális státusz (2026-06-12, javított)

```
┌─────────────────────────────────────────────────────────────────┐
│ OVERALL: BLOCKED                                                  │
│                                                                   │
│ ✅ BLOCKED_IOS_NO_VIEW — VISSZAVONVA                              │
│    iOS UI (JugglingVideoListView + JugglingPlayerView +           │
│    JugglingVideoListViewModel + JugglingVideoItem) mainen van.    │
│    Training tab JugglingVideoListView()-t használ (nem            │
│    PlaceholderScreen).                                            │
│    P5 Phase 1b CLOSED — BUILD_VERIFIED_NOT_DEVICE_VALIDATED.      │
│                                                                   │
│ ❌ BLOCKED_MISSING_STAGING          (Szekció 1.1)                 │
│    Staging URL ismeretlen. Backend preflight local dev szerveren  │
│    elvégezhető; fizikai iPhone stagingre nem.                     │
│                                                                   │
│ ❌ BLOCKED_MISSING_TEST_DATA        (Szekció 3)                   │
│    T-01..T-05 test recordok és fizikai fájlok nincsenek          │
│    elhelyezve a staging DB-ben / disk-en.                        │
│                                                                   │
│ ⚠️  iOS BUILD NOTE (Szekció 5.1)                                  │
│    Validációhoz main branch-ről kell buildelni.                   │
│    A feat/biometric-auto-capture-spike branch nem tartalmazza     │
│    a Juggling UI fájljait.                                        │
└─────────────────────────────────────────────────────────────────┘
```

### Verdikt opciók

| Kód | Feltétel |
|-----|---------|
| `READY_FOR_STAGING_DEVICE_VALIDATION` | E-01..E-11 PASS + T-01..T-05 léteznek + B-01..B-08 PASS |
| `BLOCKED_MISSING_STAGING` | **JELENLEGI ÁLLAPOT** — staging URL ismeretlen |
| `BLOCKED_MISSING_TEST_DATA` | **JELENLEGI ÁLLAPOT** — T-01..T-05 DB/fájlok hiányoznak |
| `BLOCKED_MISSING_CELERY_OR_FFMPEG` | API-06 timeout |
| `BLOCKED_IOS_BUILD_CONFIG` | B-06 FAIL (Xcode build error) |
| `BLOCKED_AUTH_CONFIG` | E-11 FAIL vagy API-01 FAIL |
| `DEVICE_VALIDATION_PASS` | M-01..M-17 + S-01..S-14 mind PASS |
| `DEVICE_VALIDATION_FAIL` | Legalább egy M/S teszt FAIL |

### Következő végrehajtási fókusz — Juggling Phase 2

Az alábbi lépések sorrendben oldják fel az aktív blokkert:

**Staging előkészítés (BLOCKED_MISSING_STAGING feloldása):**
1. Staging URL beszerzése vagy konfigurálása (ops feladat)
2. `JUGGLING_POC_ENABLED=true` staging `.env`-ben
3. Redis / Celery worker / ffmpeg / ffprobe ellenőrzés (E-06..E-09)
4. Közös storage path (`JUGGLING_UPLOAD_DIR`) ellenőrzése (FastAPI + Celery látja) (E-04..E-05)

**Tesztadat létrehozás (BLOCKED_MISSING_TEST_DATA feloldása):**
5. Teszt user létrehozása (`juggling-test@staging.lfa.internal`) + `service_consent=true`
6. T-01 happy path videó (status=analyzed, transcode_status=done, fájlok disk-en)
7. T-02 processing videó (thumbnail=null, processed_path=null)
8. T-03 rejected/failed videó
9. T-04 other-user auth boundary rekord
10. T-05 gdpr_deleted rekord (ha biztonságosan tesztelhető)

**REST/API preflight (Szekció 4):**
11. API-01..API-15 curl tesztek — elvégezhető local dev szerveren is

**iOS device validáció (mindkét blocker feloldása után):**
12. iOS build `main` branch-ről, staging baseURL-lel (local-only, nem commitolható)
13. M-01..M-17 iPhone manual checklist végrehajtása
14. S-01..S-14 network/security validation (proxy)

**Dokumentáció:**
15. Szekció 8 eredmény riport kitöltése
16. Verdikt frissítése: `DEVICE_VALIDATION_PASS` vagy `DEVICE_VALIDATION_FAIL`

---

*Utolsó frissítés: 2026-06-12 | Állapot: BLOCKED_MISSING_STAGING + BLOCKED_MISSING_TEST_DATA*
