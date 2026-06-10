# PR-4 Részletes Terv — Biometric Embedding Generálás + Celery Architecture
## feat/biometric-pr4-embedding-celery

> **Engineering/Product státusz:** ✅ ACCEPTED FOR ENGINEERING (Zoltán + ChatGPT szakmai review, 2026-06-10)
> **Legal/DPO státusz:** ⏳ LEGAL/DPO NOT REQUIRED FOR IMPLEMENTATION — pending only before production activation
> **Production aktiválás:** ⛔ NOT PRODUCTION ACTIVE — `BIOMETRIC_FACE_MATCHING_ENABLED=false`
> **Implementációs státusz:** ✅ IMPLEMENTED & MERGED — PR #271, merge commit c8f9d077, main HEAD c8f9d0770a
> **Base:** main (HEAD: 9c03a4e5 — PR-3 merged → implementálva PR-4-ben)
> **Nem KYC. Production aktiváláshoz külön legal/DPO gate szükséges.**

---

## 1. Branch és base

| | |
|--|--|
| **Branch neve** | `feat/biometric-pr4-embedding-celery` |
| **Base branch** | `main` |
| **Base commit** | `9c03a4e5` (PR-3 merge commit) |
| **Alembic migration** | **NEM szükséges** — `user_face_embeddings` tábla megvan PR-1-ből |

---

## 2. Cél összefoglaló

PR-4 az embedding generálási pipeline backend struktúráját alakítja ki:

- AES-256-GCM titkosítási service
- Absztrakt embedding provider (fake/mock — ONNX nincs PR-4-ben)
- Celery task: `biometric_generate_embedding` (fake provider-rel)
- Celery task: `biometric_delete_embedding` (fizikai törlés, ETA-alapú)
- PR-2 `_schedule_embedding_deletion_placeholder` → valódi Celery task
- PR-3 `biometric_embedding_generation_pending` log → valódi Celery dispatch
- Idempotens task működés
- Retry + failure audit trail

Amit PR-4 **NEM** tartalmaz:
- ONNX / InsightFace (PR-5)
- Valódi face matching (PR-6)
- iOS módosítás
- Admin review UI
- Production aktiválás
- `BIOMETRIC_FACE_MATCHING_ENABLED` alapértelmezett értéke marad `false`

---

## 3. Jelenlegi állapot — mit kell felváltani

### PR-3 liveness_service.py placeholder
```python
# Jelenleg:
logger.info("biometric_embedding_generation_pending user_id=%s ... (Celery task in PR-4)")
# PR-4 után:
biometric_generate_embedding_task.apply_async(args=[user.id, photo_filename], countdown=5)
```

### PR-2 consent_service.py placeholder
```python
# Jelenleg:
_schedule_embedding_deletion_placeholder(db, user_id, delete_after, ip_address)
# PR-4 után:
biometric_delete_embedding_task.apply_async(args=[user_id], eta=delete_after)
```

---

## 4. Új fájlok (6)

| Fájl | Tartalom |
|------|----------|
| `app/services/biometric/encryption_service.py` | AES-256-GCM encrypt/decrypt |
| `app/services/biometric/embedding_service.py` | Provider abstraction + store/delete |
| `app/tasks/biometric_tasks.py` | Celery tasks (generate + delete) |
| `tests/biometric/test_encryption_service.py` | BENC-01..10 |
| `tests/biometric/test_embedding_service.py` | BES-01..14 |
| `tests/biometric/test_biometric_tasks.py` | BBT-01..14 |

## 5. Módosított fájlok (5)

| Fájl | Változás |
|------|----------|
| `app/celery_app.py` | `biometric_tasks` include + queue + routing |
| `app/services/biometric/liveness_service.py` | placeholder → Celery dispatch |
| `app/services/biometric/consent_service.py` | placeholder fn → Celery ETA dispatch |
| `tests/snapshots/openapi_snapshot.json` | ha route count változik |
| `tests/unit/api/web_routes/test_*.py` | route count bump ha szükséges |

**Nincs migration fájl.**

---

## 6. Részletes service tervek

### 6.1 `encryption_service.py`

```python
# app/services/biometric/encryption_service.py

class BiometricEncryptionService:
    """
    AES-256-GCM encrypt/decrypt for face embeddings.

    Key source: settings.BIOMETRIC_EMBEDDING_KEY (32-byte hex string).
    Each encrypt() call generates a fresh 12-byte random IV (nonce).
    The plaintext is NEVER stored or logged — only ciphertext + IV to DB.
    """

    def __init__(self):
        self._key: bytes = self._load_and_validate_key()

    def _load_and_validate_key(self) -> bytes:
        """
        Load BIOMETRIC_EMBEDDING_KEY from settings.
        Raises ValueError if:
          - key is empty (production guard)
          - key is not valid hex
          - decoded key is not exactly 32 bytes (AES-256)
        In test environments: if key is empty, returns a deterministic 32-byte test key
        (controlled by BIOMETRIC_ENCRYPTION_ALLOW_TEST_KEY=true env flag).
        """
        ...

    def encrypt(self, plaintext: bytes) -> tuple[bytes, bytes]:
        """
        Encrypt plaintext with AES-256-GCM.
        Returns: (ciphertext, iv) — both bytes, suitable for BYTEA storage.
        iv is 12 bytes, randomly generated per call.
        """
        ...

    def decrypt(self, ciphertext: bytes, iv: bytes) -> bytes:
        """
        Decrypt ciphertext with AES-256-GCM.
        Returns: plaintext bytes.
        Raises: ValueError on authentication failure (tamper detection).
        """
        ...

    def embedding_to_bytes(self, embedding: list[float]) -> bytes:
        """Serialize 512-dim float32 list to 2048-byte little-endian binary."""
        import struct
        return struct.pack(f"{len(embedding)}f", *embedding)

    def bytes_to_embedding(self, raw: bytes) -> list[float]:
        """Deserialize 2048-byte binary to 512-dim float32 list."""
        import struct
        return list(struct.unpack(f"{len(raw)//4}f", raw))
```

**Kulcskezelési szabályok:**
- `BIOMETRIC_EMBEDDING_KEY` = 64 hex char = 32 bytes
- Üres string → `ValueError` kivéve ha `BIOMETRIC_ENCRYPTION_ALLOW_TEST_KEY=true`
- Tesztekben: `monkeypatch` vagy `BIOMETRIC_ENCRYPTION_ALLOW_TEST_KEY=true`
- Production: kötelező beállítani, sosem üres
- Key rotation: külön runbook (PR-8 scope)

### 6.2 `embedding_service.py`

```python
# app/services/biometric/embedding_service.py

class AbstractEmbeddingProvider:
    """Base class for embedding generation backends."""
    def generate(self, image_bytes: bytes) -> list[float]:
        raise NotImplementedError

class FakeEmbeddingProvider(AbstractEmbeddingProvider):
    """
    Deterministic fake provider for tests and dev.
    Returns a fixed 512-dim unit vector. NO ONNX, NO InsightFace.
    Selected when BIOMETRIC_EMBEDDING_PROVIDER=fake (default).
    """
    def generate(self, image_bytes: bytes) -> list[float]:
        # Deterministic: hash image_bytes to seed, return unit vector
        ...

# OnnxEmbeddingProvider — planned, NOT in PR-4 (PR-5)

def get_embedding_provider() -> AbstractEmbeddingProvider:
    """
    Returns the active provider based on BIOMETRIC_EMBEDDING_PROVIDER setting.
    PR-4: only 'fake' supported. 'onnx' raises NotImplementedError.
    """
    ...

def store_embedding(
    *,
    db: Session,
    user_id: int,
    embedding: list[float],
    model_version: str,
) -> UserFaceEmbedding:
    """
    AES-256-GCM encrypt and INSERT/UPDATE user_face_embeddings.
    is_active stays False until admin/auto-approval (future PR).
    Idempotent: if row exists, overwrites (re-consent scenario).
    """
    ...

def delete_embedding(
    *,
    db: Session,
    user_id: int,
) -> bool:
    """
    Physical DELETE from user_face_embeddings.
    Returns True if row existed and was deleted, False if not found.
    Audit log written by caller (biometric_tasks.py).
    """
    ...
```

**Idempotencia szabályok:**
```
generate task input  : user_id + photo_filename
check before generate: user_face_embeddings WHERE user_id=X AND is_active=True → SKIP (already done)
check re-consent     : is_active=False → overwrite (user revoked and re-consented)
```

### 6.3 `biometric_tasks.py`

```python
# app/tasks/biometric_tasks.py

@celery_app.task(
    bind=True,
    name="app.tasks.biometric_tasks.biometric_generate_embedding_task",
    queue="biometric_embeddings",
    max_retries=3,
    default_retry_delay=60,       # first retry after 60s
    # exponential: 60s → 300s → 900s handled via self.retry(countdown=...)
    acks_late=True,
    reject_on_worker_lost=True,
)
def biometric_generate_embedding_task(self, user_id: int, photo_filename: str | None):
    """
    Generate and store AES-256-GCM encrypted embedding for a user.

    Steps:
      1. Feature flag guard (503 if off — abort task, no retry)
      2. Load user — abort if not found
      3. Verify active consent — abort (no retry) if revoked
      4. Idempotency check — skip if is_active embedding exists
      5. Get image bytes (from photo_filename path) — retry on IOError
      6. Generate embedding via provider (FakeEmbeddingProvider in PR-4)
      7. store_embedding() — encrypt + INSERT
      8. Audit log: EVT_REFERENCE_AUTO_APPROVED_LIVENESS (event_result=auto_approved_liveness)
      9. On failure: audit log EVT_REFERENCE_REJECTED + error_message
         Retry with exponential backoff (max 3)
      10. On max retries: EVT_REFERENCE_REJECTED + error_message="max_retries_exceeded"
    """
    ...


@celery_app.task(
    bind=True,
    name="app.tasks.biometric_tasks.biometric_delete_embedding_task",
    queue="biometric_embeddings",
    max_retries=5,
    default_retry_delay=300,
    acks_late=True,
    reject_on_worker_lost=True,
)
def biometric_delete_embedding_task(self, user_id: int):
    """
    Physical DELETE of face embedding after consent revocation.

    ETA-scheduled from consent_service.revoke_consent():
        biometric_delete_embedding_task.apply_async(args=[user_id], eta=delete_after)

    Steps:
      1. Load user — abort if not found
      2. delete_embedding(db, user_id) — physical DELETE
      3. Audit log: EVT_EMBEDDING_DELETED (event_result=completed)
         Replaces the "pending" audit row written at revocation time
      4. On not found (already deleted): log WARNING, mark as idempotent success
      5. On failure: retry with exponential backoff (max 5)
      6. On max retries: alert log + EVT_EMBEDDING_DELETED(event_result=failed)
    """
    ...
```

### 6.4 `celery_app.py` — módosítás

```python
# Jelenlegi include lista:
include=["app.tasks.tournament_tasks", "app.tasks.mood_photo_tasks"]

# PR-4 után:
include=["app.tasks.tournament_tasks", "app.tasks.mood_photo_tasks", "app.tasks.biometric_tasks"]

# Új queue:
task_queues={
    "default":              {},
    "tournaments":          {},
    "mood_photos":          {},
    "biometric_embeddings": {},   # PR-4 ÚJ
}

# Új routing:
task_routes={
    ...,
    "app.tasks.biometric_tasks.biometric_generate_embedding_task": {"queue": "biometric_embeddings"},
    "app.tasks.biometric_tasks.biometric_delete_embedding_task":   {"queue": "biometric_embeddings"},
}

# Rate limiting:
task_annotations={
    ...,
    "app.tasks.biometric_tasks.biometric_generate_embedding_task": {"rate_limit": "30/m"},
    "app.tasks.biometric_tasks.biometric_delete_embedding_task":   {"rate_limit": "60/m"},
}
```

---

## 7. Config kiegészítés

Új settings mező (ha még nincs):
```python
# app/config.py — már megvan:
BIOMETRIC_EMBEDDING_KEY: str = ""

# PR-4-ben hozzáadandó:
BIOMETRIC_EMBEDDING_PROVIDER: str = "fake"   # "fake" | "onnx" (PR-5)
BIOMETRIC_ENCRYPTION_ALLOW_TEST_KEY: bool = False  # True csak dev/test env-ben
```

---

## 8. Endpoint változások

**Nincs új endpoint PR-4-ben.**

Meglévő endpointok viselkedése változik:
- `DELETE /me/biometric-consent` — a belső `_schedule_embedding_deletion_placeholder` helyett valódi Celery `apply_async` hívódik (külső viselkedés azonos)
- `POST /me/biometric-liveness` — a belső `logger.info` placeholder helyett valódi Celery `apply_async` hívódik (külső viselkedés azonos: 201 + státuszok)

**Route count nem változik.**

---

## 9. Audit eventek (PR-4-ben aktív)

| Event constant | Trigger | event_result |
|----------------|---------|--------------|
| `EVT_REFERENCE_AUTO_APPROVED_LIVENESS` | Embedding sikeresen store-olva | `"completed"` |
| `EVT_REFERENCE_REJECTED` | Embedding generálás meghiúsult (retry után) | `"failed"` |
| `EVT_EMBEDDING_DELETED` | Fizikai törlés sikeres | `"completed"` |
| `EVT_EMBEDDING_DELETED` | Max retries elérve törléskor | `"failed"` |

Megjegyzés: `EVT_REFERENCE_AUTO_APPROVED_LIVENESS` PR-3-ban `"auto_approved_liveness"` event_result-tel jelenik meg (liveness fogadáskor). PR-4 a `"completed"` értéket hozzáadja az embedding store után. A két audit sor külön: PR-3 = szándék rögzítése, PR-4 = tény rögzítése.

---

## 10. Titkosítási kulcskezelés

| Szempont | Szabály |
|----------|---------|
| **Algoritmus** | AES-256-GCM (pycryptodome vagy cryptography lib) |
| **Key forrás** | `settings.BIOMETRIC_EMBEDDING_KEY` (hex string, 64 char = 32 byte) |
| **IV** | 12 byte, `os.urandom(12)`, per-row egyedi |
| **Tárolás** | `user_face_embeddings.embedding_ciphertext` + `.embedding_iv` (BYTEA) |
| **Plaintext** | SOHA nem perzisztált, SOHA nem logolt |
| **Üres key** | `ValueError` raise, kivéve `BIOMETRIC_ENCRYPTION_ALLOW_TEST_KEY=true` |
| **Test key** | `"00" * 32` (deterministic, nem secure, csak tesztben) |
| **Key rotation** | Nem PR-4 scope — külön runbook (PR-8) |

**Dependency:** `cryptography>=42.0` (már megvan a projekt requirements-ben?) — ellenőrizendő. Ha nem: `pycryptodome` alternatíva.

---

## 11. Idempotencia szabályok

### biometric_generate_embedding_task
```
Input       : user_id, photo_filename
Guard 1     : user_face_embeddings WHERE user_id=X AND is_active=True → SKIP, log WARNING, success
Guard 2     : user biometric consent is_active=False → ABORT, no retry (user revoked)
Guard 3     : feature flag off → ABORT, no retry
Re-consent  : is_active=False row EXISTS → overwrite (INSERT or UPDATE)
```

### biometric_delete_embedding_task
```
Input       : user_id
Guard 1     : nincs sor user_face_embeddings-ben → idempotent success, log WARNING
Guard 2     : már törölt (soft + fizikailag) → idempotent success
```

---

## 12. Failure / Retry logika

### biometric_generate_embedding_task

| Hiba típus | Akció |
|-----------|-------|
| Consent revoked | ABORT (no retry), EVT_REFERENCE_REJECTED |
| Feature flag off | ABORT (no retry) |
| IOError (file not found) | retry (max 3, expo backoff 60s→300s→900s) |
| Encryption error | retry (max 3) |
| DB error | retry (max 3) |
| Max retries exceeded | EVT_REFERENCE_REJECTED(error_message="max_retries_exceeded") + alert log |

### biometric_delete_embedding_task

| Hiba típus | Akció |
|-----------|-------|
| User not found | ABORT, log WARNING |
| DB error | retry (max 5, 300s delay) |
| Max retries exceeded | EVT_EMBEDDING_DELETED(event_result="failed") + CRITICAL log |
| Already deleted (idempotent) | SUCCESS, log WARNING |

---

## 13. Worker indítás (dokumentáció célú)

```bash
# biometric_embeddings queue worker (production után, ha flag true)
celery -A app.celery_app worker -Q biometric_embeddings --pool=solo --loglevel=info
```

**Megjegyzés:** Worker csak `BIOMETRIC_FACE_MATCHING_ENABLED=true` production esetén szükséges. Development + CI: mock task dispatch (`task.apply_async` mock-olva).

---

## 14. Tesztterv

### `tests/biometric/test_encryption_service.py` — BENC-* tesztek

| Teszt | Leírás |
|-------|--------|
| BENC-01 | encrypt() visszaad (ciphertext, iv) tuple-t |
| BENC-02 | iv mindig 12 byte |
| BENC-03 | ciphertext és plaintext különböző |
| BENC-04 | decrypt(encrypt(x)) == x round-trip |
| BENC-05 | különböző plaintext → különböző ciphertext |
| BENC-06 | különböző iv → különböző ciphertext (ugyanarra a plaintextre) |
| BENC-07 | tampered ciphertext → ValueError (GCM auth fail) |
| BENC-08 | tampered iv → ValueError |
| BENC-09 | üres key → ValueError (ha ALLOW_TEST_KEY=false) |
| BENC-10 | embedding_to_bytes / bytes_to_embedding round-trip (512-dim) |

### `tests/biometric/test_embedding_service.py` — BES-* tesztek

| Teszt | Leírás |
|-------|--------|
| BES-01 | FakeEmbeddingProvider.generate() → 512-dim float list |
| BES-02 | FakeEmbeddingProvider deterministic (same input → same output) |
| BES-03 | FakeEmbeddingProvider nem tartalmaz ONNX importot |
| BES-04 | store_embedding() INSERT-el user_face_embeddings-be |
| BES-05 | store_embedding() ciphertext != None, iv != None |
| BES-06 | store_embedding() is_active=False (nem auto-approved még) |
| BES-07 | store_embedding() idempotens: második hívás felülírja, nem duplikál |
| BES-08 | store_embedding() plaintext sosem kerül DB-be (ciphertext ≠ plaintext) |
| BES-09 | delete_embedding() törli a sort |
| BES-10 | delete_embedding() visszaad True ha volt sor |
| BES-11 | delete_embedding() visszaad False ha nem volt sor (idempotens) |
| BES-12 | get_embedding_provider() "fake" → FakeEmbeddingProvider |
| BES-13 | get_embedding_provider() "onnx" → NotImplementedError (PR-5) |
| BES-14 | face_match_score sosem kerül store_embedding return value-ba |

### `tests/biometric/test_biometric_tasks.py` — BBT-* tesztek

| Teszt | Leírás |
|-------|--------|
| BBT-01 | generate task happy path: embedding store-olva + audit log |
| BBT-02 | generate task: is_active embedding exists → SKIP (idempotens) |
| BBT-03 | generate task: consent revoked → ABORT, EVT_REFERENCE_REJECTED |
| BBT-04 | generate task: feature flag off → ABORT, no retry |
| BBT-05 | generate task: IOError → retry (mock max_retries) |
| BBT-06 | generate task: max retries → EVT_REFERENCE_REJECTED(max_retries_exceeded) |
| BBT-07 | generate task: plaintext sosem logolt |
| BBT-08 | delete task happy path: fizikai törlés + audit EVT_EMBEDDING_DELETED(completed) |
| BBT-09 | delete task: no row → idempotens success, WARNING log |
| BBT-10 | delete task: DB error → retry |
| BBT-11 | delete task: max retries → EVT_EMBEDDING_DELETED(failed) + CRITICAL log |
| BBT-12 | biometric_tasks modul nem importálja az onnxruntime-t |
| BBT-13 | liveness_service dispatch: Celery apply_async hívódik (nem placeholder log) |
| BBT-14 | consent_service revoke: Celery apply_async ETA-val hívódik |

---

## 15. Acceptance Criteria

### AC-1: AES-256-GCM round-trip
- `decrypt(encrypt(plaintext)) == plaintext`
- `iv` egyedi minden encrypt() hívásnál
- Tampered ciphertext → exception

### AC-2: FakeEmbeddingProvider
- 512-dim float list visszaadva
- Nincs onnxruntime import a modulban
- Determinisztikus (azonos input → azonos output)

### AC-3: store_embedding idempotencia
- Második store ugyanarra a user_id-re nem hoz létre duplikált sort
- Meglévő is_active=True sor esetén skip (generate task)

### AC-4: delete_embedding idempotencia
- Nem létező sort törlés → False visszaadva, nem exception
- Fizikai törlés után audit log EVT_EMBEDDING_DELETED(completed)

### AC-5: Celery dispatch valódi
- liveness_service: `apply_async` hívódik (nem logger.info)
- consent_service: `apply_async` ETA-val hívódik (nem logger.info)
- CI-ban mock: `@patch("app.tasks.biometric_tasks.biometric_generate_embedding_task.apply_async")`

### AC-6: Plaintext protection
- Plaintext embedding sosem logolt (caplog-ban nincs)
- Plaintext embedding sosem kerül DB-be
- face_match_score sosem kerül visszatérési értékbe

### AC-7: Feature flag guard
- `BIOMETRIC_FACE_MATCHING_ENABLED=false` → task ABORT, nincs retry

### AC-8: Route count változatlan
- Nincs új endpoint → route count marad 878

### AC-9: Nincs ONNX/InsightFace
- `onnxruntime` nincs importálva sehol PR-4 kódjában
- `insightface` nincs importálva

---

## 16. Out-of-scope lista (PR-4)

| Mi | Miért nem PR-4 |
|----|----------------|
| ONNX / InsightFace (OnnxEmbeddingProvider) | PR-5 |
| Valódi face matching (cosine similarity) | PR-6 |
| iOS kamera | Önálló iOS PR |
| Admin review UI | PR-7 |
| face_match_score threshold logika | PR-6 |
| Key rotation eljárás | PR-8 runbook |
| Monitoring / alerting | PR-8 |
| Production aktiválás | Jogi gate-ek után |

---

## 17. Production / jogi blokkolók (PR-4-re specifikus)

| Blokkoló | PR-4 érintettsége | Státusz |
|----------|-------------------|---------|
| DPIA jóváhagyás | AES-256-GCM tárolás bővül | ⛔ PENDING |
| `BIOMETRIC_EMBEDDING_KEY` production beállítása | PR-4 implementálja az encryption service-t | ⚠️ Szükséges production előtt |
| Key rotation eljárás | PR-4 nem implementálja | ⛔ PR-8 scope |
| `BIOMETRIC_FACE_MATCHING_ENABLED=false` | Változatlan | ⛔ locked |
| Bias / fairness audit | Nem érinti (fake provider) | ⛔ PENDING (ONNX előtt) |
| Worker deployment | Celery worker szükséges production-on | ⚠️ Ops gate |

---

## 18. Fájlszám becslés

| | Darab |
|--|--|
| Új fájlok | 6 |
| Módosított fájlok | 5 (celery_app + 2 service + snapshot + route tests) |
| **Összesen** | **~11 fájl** |
| Új tesztek | ~38 (BENC-01..10 + BES-01..14 + BBT-01..14) |
