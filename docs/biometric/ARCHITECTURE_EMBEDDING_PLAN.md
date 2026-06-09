# Embedding Generálás — Architekturális Terv (PR-4+ scope)

> **Státusz:** TERV — implementáció PR-4-ban kezdődik.
> Jelen dokumentum tervező jellegű; semmiféle kód nincs még implementálva.

---

## Adatfolyam

```
iOS kamera (PR-3+)
    │
    ▼
POST /me/biometric-liveness  ←── PR-3 (liveness metadata + photo_filename)
    │
    ├─► BiometricAuditLogger (liveness_completed, reference_submitted)
    │
    ▼
Celery task: biometric_generate_embedding     ← PR-4
    │
    ├─► Load ONNX model (InsightFace buffalo_sc_v1)  ← PR-5
    ├─► Run inference → 512-dim float32 embedding
    ├─► AES-256-GCM encrypt (per-row IV)
    ├─► INSERT user_face_embeddings (ciphertext + iv + model_version)
    └─► BiometricAuditLogger (reference_auto_approved_liveness / pending_review)
```

---

## Celery Task Architektúra (PR-4)

### Task definíció
```
Queue    : biometric_embeddings  (dedikált, nem a mood_photos queue)
Task ID  : biometric.generate_embedding
Retry    : max 3, exponential backoff (60s, 300s, 900s)
Timeout  : 30s (ONNX inference)
```

### Idempotencia
- Task body: `user_id` + `photo_filename` + `consent_version`
- Duplicate guard: ha `user_face_embeddings.user_id` már létezik és `is_active=True` → skip + audit log
- Failure: audit log `EVT_REFERENCE_REJECTED` + `error_message`

### Failure audit
```
SUCCESS path : embedding_ciphertext + iv → DB, is_active=False, pending admin/auto-approve
FAILURE path : biometric_verification_logs INSERT EVT_REFERENCE_REJECTED
DLQ path     : after max retries → EVT_REFERENCE_REJECTED + alert
```

### Delayed delete task
```
Task     : biometric.delete_embedding
Trigger  : consent revocation (PR-2 placeholder már logol)
Delay    : EMBEDDING_DELETION_DELAY_DAYS = 30
Action   : DELETE user_face_embeddings WHERE user_id=X
Audit    : EVT_EMBEDDING_DELETED
```

---

## AES-256-GCM Titkosítás

| Paraméter | Érték |
|-----------|-------|
| Algoritmus | AES-256-GCM |
| Key source | `BIOMETRIC_EMBEDDING_KEY` env var (32 byte hex) |
| IV (nonce) | 12 byte, `os.urandom(12)`, per-row egyedi |
| Tárolás | `embedding_ciphertext` BYTEA, `embedding_iv` BYTEA |
| Plaintext | SOHA nem tárolva, SOHA nem logolva |
| Key rotation | Külön eljárás (runbook szükséges) |

---

## ONNX / InsightFace Terv (PR-5+)

### Dependency terv
```
onnxruntime==1.18.x    (CPU-only build — nincs GPU dependencia)
insightface==0.7.x     (buffalo_sc model)
```

- **NEM** add hozzá `requirements.txt`-hez PR-4 előtt
- CI: mock-olt ONNX inference (valódi modell nem töltődik le CI-ban)
- Modell artifact: checksum-ellenőrzéssel (SHA-256) töltődik be production-on

### Modellverzió rögzítés
```
MODEL_NAME    = "insightface_buffalo_sc_v1"
MODEL_SHA256  = "[rögzítendő production deploy előtt]"
MODEL_URL     = "[belső artifact registry — nem CDN]"
```

### CI mock stratégia
```python
# tests/biometric/conftest.py
@pytest.fixture
def mock_onnx_inference():
    with patch("app.services.biometric.embedding_service.run_inference") as m:
        m.return_value = [0.1] * 512  # fake 512-dim vector
        yield m
```

---

## Admin Review Fallback (PR-7, UI nélküli backend terv)

- Ha `face_match_score` a review threshold bandben van (pl. 0.55–0.75):
  - `user.manual_review_required = True`
  - `user.face_match_status = "manual_review_required"`
  - Audit log: `EVT_MATCH_REVIEW_REQUIRED`
- Admin recovery endpoint (PR-7): `POST /admin/biometric/{user_id}/override`
  - `event_result`: "approved" / "rejected"
  - Audit log: `EVT_ADMIN_OVERRIDE`
  - RBAC: admin role guard
- UI: PR-7+ scope — jelen dokumentum csak backend útvonalat tervezi

---

*Implementáció kezdete: PR-4 (PR-3 merge után)*