# PR-6 — Face Matching (threshold, review band, match/fail flow)
## feat/biometric-pr6-face-matching

> **Engineering/Product státusz:** ACCEPTED FOR ENGINEERING (2026-06-10)
> **Production use:** NOT PRODUCTION ACTIVE — `BIOMETRIC_FACE_MATCHING_ENABLED=false`
> **Nem KYC. Nem production-ready.**

---

## Scope

| Komponens | Fájl | Leírás |
|-----------|------|--------|
| Matching service | `app/services/biometric/matching_service.py` | cosine similarity, classify, run_face_match |
| Embedding load | `app/services/biometric/embedding_service.py` | `load_reference_embedding()` — decrypt + deserialize |
| Celery task fix | `app/tasks/biometric_tasks.py` | `is_active=True` + `approved_at` az auto-approval után |
| Verify endpoint | `app/api/api_v1/endpoints/users/biometric_verify.py` | `POST /me/biometric-verify` |
| Schemas | `app/schemas/biometric.py` | `BiometricVerifyRequest`, `BiometricVerifyResponse` |
| Router | `app/api/api_v1/endpoints/users/__init__.py` | `biometric_verify` router regisztrálva |

## Threshold konstansok (nem expozáltak API-ban)

```
MATCH_THRESHOLD = 0.75   # score >= 0.75 → verified
REVIEW_LOWER    = 0.55   # 0.55 <= score < 0.75 → manual_review_required
                          # score < 0.55 → rejected
```

## POST /me/biometric-verify

**Request:**
```json
{ "photo_filename": "live_capture_20260610.jpg" }
```

**Response:**
```json
{ "result": "verified" }
```
result: `verified` | `manual_review_required` | `rejected`

**Nem tartalmaz:** `face_match_score`, `embedding`, raw sensor data

**Guardok:**
- `BIOMETRIC_FACE_MATCHING_ENABLED=false` → 503
- no active consent → 403
- no active embedding → 404
- path traversal in `photo_filename` → 400

## Celery task (PR-6 fix)

A `biometric_generate_embedding_task` eddig `store_embedding()` után `is_active=False`-on hagyta a sort.
PR-6-ban az onboarding liveness auto-approval után `is_active=True` + `approved_at=now()` kerül beállításra.

Ez szükséges, mert `load_reference_embedding()` csak `is_active=True` sort olvas.

## User state update

| outcome | user.face_match_status | user.manual_review_required |
|---------|----------------------|---------------------------|
| verified | "verified" | False |
| manual_review_required | "manual_review_required" | True |
| rejected | "rejected" | (unchanged) |

## Audit log

| outcome | event_type | face_match_score |
|---------|-----------|-----------------|
| verified | EVT_MATCH_SUCCESS | stored internally |
| manual_review_required | EVT_MATCH_REVIEW_REQUIRED | stored internally |
| rejected | EVT_MATCH_FAILED | stored internally |

`face_match_score` soha nem kerül API válaszba — csak `biometric_verification_logs`-ban tárolódik.

## Out-of-scope (PR-6-ban tilos)

iOS kliens · admin review UI · `POST /admin/biometric/{user_id}/override` ·
key rotation · production activation · bias/fairness audit · AuraFace production döntés

## Tesztek

| ID | Fájl | Leírás |
|----|------|--------|
| BCM-01..09 | test_matching_service.py | cosine similarity + classify pure unit tesztek |
| BCM-10 | test_matching_service.py | run_face_match happy path integration |
| BCM-11..20 | test_biometric_verify_api.py | POST /me/biometric-verify API tesztek |
| BBT-15 | test_biometric_tasks.py | is_active=True a task után |