# PR-3 Részletes Terv — Biometric Liveness Reference Flow
## feat/biometric-pr3-liveness-reference

> **Státusz:** TERV — implementáció PR-2 merge után indulhat.
> **Base:** main (HEAD: d1d5d49a — PR-2 merged)
> **Előfeltétel:** PR #267 merged ✓

---

## 1. Branch és base

| | |
|--|--|
| **Branch neve** | `feat/biometric-pr3-liveness-reference` |
| **Base branch** | `main` |
| **Base commit** | `d1d5d49a` (PR-2 merge commit) |
| **Alembic migration** | **NEM szükséges** — minden oszlop megvan PR-1-ből |

---

## 2. Scope összefoglaló

PR-3 egyetlen endpoint-ot vezet be: a liveness challenge eredményének backend fogadása.

- Az iOS kamera challenge **nem** PR-3 scope (önálló PR, ha backend készen áll)
- Embedding generálás **nem** PR-3 scope (PR-4)
- ONNX / InsightFace **nem** PR-3 scope (PR-5)
- Admin review UI **nem** PR-3 scope (PR-7)
- Celery task valódi implementáció **nem** PR-3 scope (PR-4)

---

## 3. Endpointok

### 3.1 Új endpoint

```
POST /api/v1/users/me/biometric-liveness
```

| Attribútum | Érték |
|------------|-------|
| Status code | 201 |
| Feature flag | `require_biometric_enabled` Depends guard |
| Auth | `get_current_user` |
| Request body | `BiometricLivenessSubmitRequest` |
| Response | `BiometricVerificationStatusOut` |
| Idempotencia | Dupla submission → 409 ha `face_reference_photo_status == "onboarding_liveness_capture"` már aktív |

### 3.2 Request body: `BiometricLivenessSubmitRequest`

```python
class BiometricLivenessSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["onboarding_liveness"]
    liveness_metadata: LivenessMetadata       # már létező schema (PR-1)
    photo_filename: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Basename only — no path, no URL"
    )
```

**Validáció:**
- `source` csak `"onboarding_liveness"` elfogadott (Literal)
- `liveness_metadata` a meglévő `LivenessMetadata` schema — extra="forbid"
- `photo_filename`: basename csak (path separator tiltva), max 255 char

### 3.3 Meglévő endpointok változatlanok

- `POST /me/biometric-consent` — változatlan
- `GET /me/biometric-consent` — változatlan
- `DELETE /me/biometric-consent` — változatlan

---

## 4. Service réteg: `liveness_service.py`

**Új fájl:** `app/services/biometric/liveness_service.py`

### Fő függvény

```python
def submit_liveness_result(
    *,
    db: Session,
    user: User,
    liveness_metadata: dict,          # már sanitizálva Pydantic-on átment
    source: str,                      # "onboarding_liveness"
    photo_filename: Optional[str],
    ip_address: Optional[str],
) -> BiometricVerificationStatusOut:
```

### Üzleti logika (lépésről lépésre)

```
1. Consent check
   └── Ha user-nek nincs aktív consent → 403 "biometric_consent_required"

2. Duplicate guard
   └── Ha user.face_reference_photo_status == "onboarding_liveness_capture"
       ÉS felülírás nem engedélyezett → 409

3. liveness_metadata sanitize (3. réteg)
   └── sanitize_liveness_metadata(liveness_metadata) — kötelező, még ha Pydantic már szűrt

4. photo_filename basename guard
   └── os.path.basename(photo_filename) == photo_filename → 400 ha nem

5. DB update — users tábla
   └── user.face_reference_photo_status = "onboarding_liveness_capture"
   └── user.face_match_status = "reference_pending"

6. Audit log — liveness_completed
   └── BiometricAuditLogger.log(
         event_type=EVT_LIVENESS_COMPLETED,
         event_result="accepted",
         liveness_metadata=sanitized_metadata,
         actor_ip_address=ip_address,
       )

7. Audit log — reference_submitted
   └── BiometricAuditLogger.log(
         event_type=EVT_REFERENCE_SUBMITTED,
         event_result="pending",
         photo_filename=photo_filename,
       )

8. Auto-approve liveness path (source == "onboarding_liveness")
   └── BiometricAuditLogger.log(
         event_type=EVT_REFERENCE_AUTO_APPROVED_LIVENESS,
         event_result="auto_approved_liveness",
       )

9. Embedding generation placeholder
   └── logger.info("biometric_embedding_generation_pending user_id=%s (Celery task in PR-4)", user.id)
   └── NEM indít Celery taskot

10. db.flush() — nem commit (endpoint commitol)

11. Return BiometricVerificationStatusOut
    └── face_match_status=user.face_match_status
    └── face_reference_photo_status=user.face_reference_photo_status
    └── has_biometric_consent=True
    └── manual_review_required=user.manual_review_required
```

---

## 5. Fájllista

### Új fájlok (5)

| Fájl | Tartalom |
|------|----------|
| `app/api/api_v1/endpoints/users/biometric_liveness.py` | POST endpoint, router |
| `app/services/biometric/liveness_service.py` | submit_liveness_result() service |
| `tests/biometric/test_liveness_api.py` | BCL-01..15 API tesztek |
| `tests/biometric/test_liveness_service.py` | BCLS-01..12 service unit tesztek |
| `docs/biometric/PR3_PLAN.md` | Ez a dokumentum |

### Módosított fájlok (3)

| Fájl | Változás |
|------|----------|
| `app/api/api_v1/endpoints/users/__init__.py` | `biometric_liveness` router regisztrálása |
| `app/schemas/biometric.py` | `BiometricLivenessSubmitRequest` hozzáadása |
| `tests/snapshots/openapi_snapshot.json` | Új route + route count update |

### Módosuló route count tesztek (automatikus)

Minden `tests/unit/api/web_routes/test_*.py` ahol a route szám hardcode-olva van — automatikusan frissítendő.

**Teljes fájlszám becslés: ~24–26 fájl** (23 teszt route snapshot + 3 core + 5 új)

---

## 6. Adatmodell-módosítás

**Nincs szükség Alembic migrációra.**

Az összes érintett oszlop már létezik (PR-1, migration `2026_06_10_1000`):
- `users.face_match_status` — String(30), nullable
- `users.face_reference_photo_status` — String(30), nullable
- `users.manual_review_required` — Boolean, default=False
- `biometric_verification_logs.*` — teljes tábla létezik

---

## 7. Audit eventek (PR-3-ban logolt)

| Event constant | Esemény | event_result |
|----------------|---------|--------------|
| `EVT_LIVENESS_COMPLETED` | Liveness challenge sikeres | `"accepted"` |
| `EVT_REFERENCE_SUBMITTED` | Referencia fotó backend-en fogadva | `"pending"` |
| `EVT_REFERENCE_AUTO_APPROVED_LIVENESS` | Onboarding liveness → auto-jóváhagyás | `"auto_approved_liveness"` |

Mindhárom konstans már definiált az `audit_log.py`-ban (PR-1).

---

## 8. Acceptance Criteria

### AC-1: Főfolyamat (happy path)
- POST `/me/biometric-liveness` visszaad 201-et
- `face_reference_photo_status = "onboarding_liveness_capture"` beállítva a user-en
- `face_match_status = "reference_pending"` beállítva
- 3 audit log sor létrejön: `liveness_completed`, `reference_submitted`, `reference_auto_approved_liveness`
- Embedding placeholder log megjelenik (Celery task NEM indul)

### AC-2: Feature flag OFF → 503
- `BIOMETRIC_FACE_MATCHING_ENABLED=false` esetén 503 visszaadva

### AC-3: Nincs aktív consent → 403
- Ha user nem adott hozzájárulást, 403 "biometric_consent_required"

### AC-4: Dupla submission → 409
- Ha már `onboarding_liveness_capture` állapotban van, 409

### AC-5: Forbidden fields elutasítva → 422
- `yaw`, `roll`, `device_model`, `landmarks` a `liveness_metadata`-ban → 422

### AC-6: photo_filename path separator → 400
- `photo_filename` tartalmaz `/` vagy `..` → 400

### AC-7: Sanitizer fut minden esetben
- liveness_metadata a sanitizer rétegen átmegy még ha Pydantic már szűrt

### AC-8: face_match_score nem jelenik meg response-ban
- `face_match_score` nem szerepel semmilyen response-ban

### AC-9: Unauthenticated → 401
- Auth nélküli kérés → 401

### AC-10: Nincs iOS / Swift / ONNX / embedding
- PR diff nem tartalmaz ezeket

---

## 9. Tesztterv

### `tests/biometric/test_liveness_api.py` — BCL-* tesztek

| Teszt | Leírás |
|-------|--------|
| BCL-01 | POST 201 happy path — helyes request, check response fields |
| BCL-02 | face_reference_photo_status = onboarding_liveness_capture beállítva |
| BCL-03 | face_match_status = reference_pending beállítva |
| BCL-04 | 3 audit log sor létrejön (liveness_completed, reference_submitted, reference_auto_approved) |
| BCL-05 | Feature flag OFF → 503 |
| BCL-06 | No consent → 403 |
| BCL-07 | Dupla submission → 409 |
| BCL-08 | liveness_metadata forbidden field (yaw) → 422 |
| BCL-09 | liveness_metadata forbidden field (device_model) → 422 |
| BCL-10 | photo_filename path traversal → 400 |
| BCL-11 | source != "onboarding_liveness" → 422 |
| BCL-12 | Unauthenticated → 401 |
| BCL-13 | Response nem tartalmaz face_match_score mezőt |
| BCL-14 | Response nem tartalmaz embedding mezőt |
| BCL-15 | liveness_metadata sanitizer fut (ismert forbidden key warning) |

### `tests/biometric/test_liveness_service.py` — BCLS-* tesztek

| Teszt | Leírás |
|-------|--------|
| BCLS-01 | submit_liveness_result happy path — return value helyes |
| BCLS-02 | Consent check: nincs consent → HTTPException 403 |
| BCLS-03 | user.face_reference_photo_status beállítva |
| BCLS-04 | user.face_match_status beállítva |
| BCLS-05 | 3 audit log sor INSERT-elve |
| BCLS-06 | Sanitizer hívódik liveness_metadata-ra |
| BCLS-07 | photo_filename basename guard |
| BCLS-08 | Dupla submission guard |
| BCLS-09 | Embedding placeholder log — Celery task NEM indul |
| BCLS-10 | db.flush() hívódik |
| BCLS-11 | face_match_score SOHA nem kerül return value-ba |
| BCLS-12 | ip_address audit log-ba kerül |

---

## 10. Out-of-scope lista (PR-3)

| Mi | Miért nem PR-3 |
|----|----------------|
| iOS kamera liveness challenge | Önálló iOS PR, backend után |
| Embedding generálás (ONNX) | PR-4 |
| Celery biometric task valódi impl. | PR-4 |
| AES-256-GCM titkosítás | PR-4 |
| InsightFace / ONNX dependency | PR-5 |
| Face matching logic | PR-6 |
| Admin review UI | PR-7 |
| Admin override endpoint UI | PR-7 |
| Monitoring / alerting | PR-8 |
| Adathordozhatóság export | TBD |
| Kiskorú szülői hozzájárulás | Jogi döntés után |

---

## 11. Production Readiness Workstream (PR-3 perspektívájából)

PR-3 önmagában **production-ra nem deploy-olható** (feature flag false marad).

Amit PR-3 előkészít:
- Backend liveness fogadó endpoint → iOS client implementálhat ellene
- Audit log trail liveness_completed eseményre → DPIA kielégítve erre a PR-re
- Reference photo státusz gép → PR-4 embedding task ebből indul ki

Amit PR-3 NEM old meg (production gate-ek):
- DPIA jóváhagyás — PENDING
- Embedding törlés — PR-4
- Bias audit — PENDING
- Kiskorú consent — PENDING

---

## 12. Jogi / adatvédelmi blokkolók (PR-3-ra specifikus)

| Blokkoló | PR-3 érintettsége | Státusz |
|----------|-------------------|---------|
| DPIA jóváhagyás | Liveness metadata kezelés bővül | PENDING |
| liveness_metadata adatminimalizálás | ✅ Sanitizer + schema enforced | Technikai OK |
| face_reference_photo_status | Csak állapotjelző string, nem biometrikus adat | Alacsony kockázat |
| Kiskorú hozzájárulás | Nem kezel életkorspecifikus logikát PR-3 | DPO-val egyeztetendő |