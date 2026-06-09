# AN-3B PR-4B2 — Multi-Device Session Contract — Final Implementation Plan v1.2

**Date:** 2026-06-21
**Status:** Plan-only — implementáció kizárólag külön jóváhagyás után.
**Előfeltétel:** PR-4B1 (#317) MERGED + külön implementációs jóváhagyás.
**Replaces:** v1.0 + v1.1 (ez a dokumentum önmagában teljes).

---

## I. Fájlstruktúra

### Backend — Új

| Fájl | Tartalom |
|------|----------|
| `app/models/multicamera_session.py` | ORM modellek (5 tábla) |
| `app/schemas/multicamera_session.py` | Pydantic DTO-k + enumok + validators |
| `app/services/multicamera/__init__.py` | Package |
| `app/services/multicamera/session_state_machine.py` | Session state transitions + guards |
| `app/services/multicamera/device_state_validator.py` | Cross-state validation |
| `app/services/multicamera/session_repository.py` | Repository (join, register, start, stop, heartbeat, update) |
| `app/services/multicamera/calibration_validator.py` | CalibrationSet JSON validator |
| `alembic/versions/2026_xx_xx_add_multicamera_sessions.py` | Migration |
| `tests/unit/multicamera/__init__.py` | — |
| `tests/unit/multicamera/test_session_state_machine.py` | SSM-01..SSM-22 |
| `tests/unit/multicamera/test_device_state_validator.py` | DSV-01..DSV-15 |
| `tests/unit/multicamera/test_session_contract.py` | SC-01..SC-14 |
| `tests/unit/multicamera/test_repository.py` | REP-01..REP-18 |
| `tests/unit/multicamera/test_calibration_validator.py` | CAL-01..CAL-06 |
| `tests/fixtures/multicamera/*.json` | 8 fixture |

### Backend — Módosítandó

| Fájl | Változás |
|------|---------|
| `app/models/__init__.py` | Import multicamera_session |

### iOS — Új

| Fájl | Tartalom |
|------|----------|
| `ios/.../MultiCamera/SessionModels.swift` | Codable structs |
| `ios/.../MultiCamera/SessionEnums.swift` | Enums (topology, states) |
| `ios/.../MultiCamera/DeviceStateValidator.swift` | Advisory validation |
| `ios/.../MultiCamera/CalibrationFingerprint.swift` | Fingerprint model |
| `ios/...Tests/MultiCamera/SessionContractTests.swift` | SC-S-01..SC-S-12 |
| `ios/...Tests/MultiCamera/DeviceStateValidatorTests.swift` | DSV-S-01..DSV-S-08 |

### Közös fixture-ek: `tests/fixtures/multicamera/`

8 JSON fixture (Python + Swift bundle resource).

---

## II. Végleges DDL

```sql
-- ═══════════════════════════════════════════════════════════════════════
-- TABLE 1: multicamera_sessions
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE multicamera_sessions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topology                VARCHAR(30) NOT NULL,
    authority_type          VARCHAR(10) NOT NULL,
    coordinator_device_id   UUID,
    world_origin_camera_id  VARCHAR(60) NOT NULL,
    calibration_set_json    JSONB,
    state                   VARCHAR(30) NOT NULL DEFAULT 'created',
    revision                INTEGER NOT NULL DEFAULT 0,
    schema_version          VARCHAR(5) NOT NULL DEFAULT '1',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at              TIMESTAMPTZ,

    CONSTRAINT ck_mcs_topology CHECK (topology IN (
        'single_camera','dual_player_remote','dual_player_onsite',
        'instructor_solo','instructor_dual'
    )),
    CONSTRAINT ck_mcs_authority CHECK (authority_type IN ('client','server')),
    CONSTRAINT ck_mcs_state CHECK (state IN (
        'created','configuring','calibrating','ready','recording',
        'degraded_recording','stopping','post_processing',
        'completed','completed_degraded','failed'
    )),
    CONSTRAINT ck_mcs_coordinator_authority CHECK (
        (authority_type = 'client' AND coordinator_device_id IS NOT NULL) OR
        (authority_type = 'server' AND coordinator_device_id IS NULL) OR
        (state IN ('degraded_recording','stopping','post_processing',
                   'completed','completed_degraded','failed'))
    )
);

-- ═══════════════════════════════════════════════════════════════════════
-- TABLE 2: session_participants
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE session_participants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES multicamera_sessions(id) ON DELETE CASCADE,
    user_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
    user_id_snapshot INTEGER NOT NULL,
    owner_role      VARCHAR(20) NOT NULL,
    display_name    VARCHAR(100) NOT NULL,
    anonymized      BOOLEAN NOT NULL DEFAULT FALSE,
    joined_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    left_at         TIMESTAMPTZ,
    revision        INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT uq_sp_session_user UNIQUE (session_id, user_id_snapshot),
    CONSTRAINT uq_sp_id_session UNIQUE (id, session_id)
);
CREATE INDEX ix_sp_session ON session_participants(session_id);
CREATE UNIQUE INDEX uix_sp_active_user ON session_participants(session_id, user_id)
    WHERE left_at IS NULL AND user_id IS NOT NULL;

-- ═══════════════════════════════════════════════════════════════════════
-- TABLE 3: session_devices
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE session_devices (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          UUID NOT NULL,
    participant_id      UUID NOT NULL,
    device_role         VARCHAR(30) NOT NULL,
    device_type         VARCHAR(20) NOT NULL,
    device_model        VARCHAR(60),
    os_version          VARCHAR(20),
    app_version         VARCHAR(20),
    camera_id           VARCHAR(60) NOT NULL,
    connection_state    VARCHAR(20) NOT NULL DEFAULT 'registered',
    readiness_state     VARCHAR(20) NOT NULL DEFAULT 'unconfigured',
    recording_state     VARCHAR(20) NOT NULL DEFAULT 'idle',
    upload_state        VARCHAR(20) NOT NULL DEFAULT 'none',
    last_heartbeat_at   TIMESTAMPTZ,
    removed_at          TIMESTAMPTZ,
    revision            INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fk_sd_participant_session
        FOREIGN KEY (session_id, participant_id)
        REFERENCES session_participants(session_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_sd_id_session UNIQUE (id, session_id),
    CONSTRAINT ck_sd_connection CHECK (connection_state IN (
        'registered','connecting','connected','disconnected','failed'
    )),
    CONSTRAINT ck_sd_readiness CHECK (readiness_state IN (
        'unconfigured','configured','preset_mismatch','ready'
    )),
    CONSTRAINT ck_sd_recording CHECK (recording_state IN (
        'idle','starting','recording','stopping','stopped'
    )),
    CONSTRAINT ck_sd_upload CHECK (upload_state IN (
        'none','pending','transferring','completed','failed'
    ))
);
CREATE INDEX ix_sd_session ON session_devices(session_id);
CREATE UNIQUE INDEX uix_sd_active_camera ON session_devices(session_id, camera_id)
    WHERE removed_at IS NULL;

-- Coordinator FK (deferred: references session_devices created above)
ALTER TABLE multicamera_sessions ADD CONSTRAINT fk_mcs_coordinator_session
    FOREIGN KEY (id, coordinator_device_id)
    REFERENCES session_devices(session_id, id) ON DELETE SET NULL;
-- Note: composite FK ensures coordinator belongs to same session

-- ═══════════════════════════════════════════════════════════════════════
-- TABLE 4: managed_devices
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE managed_devices (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id              UUID NOT NULL,
    managed_by_device_id    UUID NOT NULL,
    owner_participant_id    UUID,
    device_type             VARCHAR(20) NOT NULL DEFAULT 'gopro',
    device_role             VARCHAR(30) NOT NULL DEFAULT 'auxiliary_camera',
    camera_id               VARCHAR(60) NOT NULL,
    firmware_version        VARCHAR(20),
    ble_state               VARCHAR(20) NOT NULL DEFAULT 'disconnected',
    wifi_state              VARCHAR(20) NOT NULL DEFAULT 'off',
    http_state              VARCHAR(20) NOT NULL DEFAULT 'unreachable',
    recording_status        VARCHAR(20) NOT NULL DEFAULT 'unknown',
    readiness_state         VARCHAR(20) NOT NULL DEFAULT 'unconfigured',
    upload_state            VARCHAR(20) NOT NULL DEFAULT 'none',
    last_heartbeat_at       TIMESTAMPTZ,
    removed_at              TIMESTAMPTZ,
    revision                INTEGER NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fk_md_managing_device_session
        FOREIGN KEY (session_id, managed_by_device_id)
        REFERENCES session_devices(session_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_md_owner
        FOREIGN KEY (session_id, owner_participant_id)
        REFERENCES session_participants(session_id, id) ON DELETE SET NULL,
    CONSTRAINT uq_md_id_session UNIQUE (id, session_id)
);
CREATE UNIQUE INDEX uix_md_active_camera ON managed_devices(session_id, camera_id)
    WHERE removed_at IS NULL;

-- ═══════════════════════════════════════════════════════════════════════
-- TABLE 5: capture_streams
-- ═══════════════════════════════════════════════════════════════════════
CREATE TABLE capture_streams (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id                  UUID NOT NULL REFERENCES multicamera_sessions(id) ON DELETE CASCADE,
    camera_id                   VARCHAR(60) NOT NULL,
    session_device_id           UUID,
    managed_device_id           UUID,
    source_camera_id_snapshot   VARCHAR(60) NOT NULL,
    source_device_type_snapshot VARCHAR(20) NOT NULL,
    sequence_number             INTEGER NOT NULL DEFAULT 0,
    start_timestamp_ns          BIGINT,
    stop_timestamp_ns           BIGINT,
    media_identifier            VARCHAR(255),
    media_size_bytes            BIGINT,
    audio_available             BOOLEAN NOT NULL DEFAULT FALSE,
    capture_preset_json         JSONB NOT NULL,
    state                       VARCHAR(20) NOT NULL DEFAULT 'pending',
    revision                    INTEGER NOT NULL DEFAULT 0,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT fk_cs_session_device
        FOREIGN KEY (session_id, session_device_id)
        REFERENCES session_devices(session_id, id) ON DELETE RESTRICT,
    CONSTRAINT fk_cs_managed_device
        FOREIGN KEY (session_id, managed_device_id)
        REFERENCES managed_devices(session_id, id) ON DELETE RESTRICT,
    CONSTRAINT ck_cs_device_xor CHECK (
        (session_device_id IS NOT NULL AND managed_device_id IS NULL) OR
        (session_device_id IS NULL AND managed_device_id IS NOT NULL)
    ),
    CONSTRAINT ck_cs_state CHECK (state IN (
        'pending','recording','stopped','transferred','failed'
    )),
    CONSTRAINT uq_cs_device_seq UNIQUE (session_id, camera_id, sequence_number)
);
CREATE INDEX ix_cs_session ON capture_streams(session_id);
```

---

## III. Coordinator integritás

### Composite FK

```sql
FOREIGN KEY (id, coordinator_device_id)
    REFERENCES session_devices(session_id, id) ON DELETE SET NULL
```

Ez garantálja: coordinator_device_id **csak a saját session device-ára** mutathat.

### Coordinator eltávolítás → service logic

Az FK `ON DELETE SET NULL` **csak** a mezőt nullázza. A session state változást **kizárólag a service** végzi:

```python
# session_repository.py
def remove_coordinator_device(db, session_id, device_id, reason):
    """Called when coordinator device fails/removed. Service is authoritative."""
    session = db.get(MultiCameraSession, session_id)
    if session.coordinator_device_id == device_id:
        session.coordinator_device_id = None
        if session.state == "recording":
            session.state = "degraded_recording"
        session.revision += 1
        db.flush()
```

**Szabály:** fizikai device DELETE NEM megengedett active session-ben. A service `remove_device()` metódusa:
1. `removed_at = now()` (soft delete)
2. Ha coordinator → `remove_coordinator_device()` hívás
3. Ha ez az utolsó kalibrált kamera-pár → session `degraded_recording`
4. Session `revision += 1`

Nincs trigger. Kizárólag service-en keresztül engedélyezett.

---

## IV. Heartbeat concurrency

### PostgreSQL row-level lock viselkedés

A `UPDATE session_devices SET last_heartbeat_at = now() WHERE id = :did` PostgreSQL-ben:
- **Row-level exclusive lock**-ot szerez az adott sorra
- Ha egyidejűleg `update_device_state()` is fut ugyanarra a sorra → a második UPDATE **várakozik** a lock feloldásáig (nem timeout, nem error — blocking wait)
- Lock duration: a tranzakció commit/rollback-ig

### Tranzakciós viselkedés

| Művelet | Érintett mezők | Lock | Revision |
|---------|---------------|------|----------|
| heartbeat | `last_heartbeat_at` | row lock, ~1ms | NEM növekszik |
| state_update | `connection_state`, `readiness_state`, etc. + `revision` | row lock, ~2-5ms | +1 |

### Garantáltak

- **Nincs lost update:** PostgreSQL MVCC + row lock garantálja, hogy mindkét UPDATE érvényesül szekvenciálisan
- **Rövid blocking:** heartbeat < 1ms lock duration → state_update max ~1ms várakozás
- **Retry policy:** nincs szükség — a blocking automatikusan feloldódik

### Teszt: valódi két-kapcsolatos PostgreSQL

```python
def test_REP_15_heartbeat_vs_state_no_lost_update(db_engine):
    """Two real PostgreSQL connections — heartbeat and state update on same row."""
    # Connection 1: BEGIN; UPDATE heartbeat; HOLD (don't commit yet)
    # Connection 2: BEGIN; UPDATE state; BLOCKED until conn1 commits
    # conn1: COMMIT → conn2 proceeds
    # Verify: both heartbeat AND state change persisted
```

---

## V. Soft delete + Unique constraint

### Partial unique indexes

```sql
-- Participant: same user can rejoin (new row) after leaving
CREATE UNIQUE INDEX uix_sp_active_user ON session_participants(session_id, user_id)
    WHERE left_at IS NULL AND user_id IS NOT NULL;

-- Device: same camera_id reregisterable after removal
CREATE UNIQUE INDEX uix_sd_active_camera ON session_devices(session_id, camera_id)
    WHERE removed_at IS NULL;

-- Managed device: same
CREATE UNIQUE INDEX uix_md_active_camera ON managed_devices(session_id, camera_id)
    WHERE removed_at IS NULL;
```

### Reaktiváció vs új rekord

| Eset | Viselkedés |
|------|-----------|
| Participant leaves (`left_at` set), same user rejoins | **Új rekord** (új `participant_id`, új `joined_at`) |
| Device removed (`removed_at` set), same camera_id reregistered | **Új rekord** (új `device_id`) |
| GoPro removed, same GoPro reconnected | **Új ManagedDevice rekord** |

Indoklás: a történeti rekord (left_at/removed_at) **immutable**. Új csatlakozás = új életciklus.

---

## VI. Calibration JSON validator

```python
# app/services/multicamera/calibration_validator.py

def validate_calibration_set(
    calibration_json: dict,
    session_camera_ids: Set[str],
    schema_version: str = "1",
) -> List[str]:
    """Validates CalibrationSet JSON before persistence. Returns violations."""
    errors = []

    # 1. Schema version supported
    if calibration_json.get("schema_version") != schema_version:
        errors.append("unsupported_schema_version")

    # 2. world_origin_camera_id must be a registered camera
    origin = calibration_json.get("world_origin_camera_id")
    if origin not in session_camera_ids:
        errors.append(f"world_origin '{origin}' not in session cameras")

    # 3. All intrinsic camera_ids must be session cameras
    for intr in calibration_json.get("intrinsics", []):
        if intr.get("camera_id") not in session_camera_ids:
            errors.append(f"intrinsic camera '{intr.get('camera_id')}' not in session")

    # 4. All extrinsic camera pairs must be session cameras
    pairs_seen = set()
    for ext in calibration_json.get("extrinsics", []):
        a, b = ext.get("camera_a_id"), ext.get("camera_b_id")
        if a not in session_camera_ids:
            errors.append(f"extrinsic camera_a '{a}' not in session")
        if b not in session_camera_ids:
            errors.append(f"extrinsic camera_b '{b}' not in session")
        pair_key = tuple(sorted([a, b]))
        if pair_key in pairs_seen:
            errors.append(f"duplicate pair {pair_key}")
        pairs_seen.add(pair_key)

    return errors
```

---

## VII. Snapshot immutability enforcement

### Mechanism: ORM event listener

```python
# In multicamera_session.py model definition

@event.listens_for(SessionParticipant, "before_update")
def _prevent_snapshot_mutation(mapper, connection, target):
    state = inspect(target)
    for attr in ("user_id_snapshot", "owner_role", "display_name"):
        hist = state.attrs[attr].history
        if hist.has_changes() and not target.anonymized:
            raise IntegrityError(
                f"Immutable field '{attr}' cannot be modified (use anonymize())"
            )

@event.listens_for(CaptureStream, "before_update")
def _prevent_capture_snapshot_mutation(mapper, connection, target):
    state = inspect(target)
    for attr in ("source_camera_id_snapshot", "source_device_type_snapshot"):
        hist = state.attrs[attr].history
        if hist.has_changes():
            raise IntegrityError(f"Immutable field '{attr}' cannot be modified")
```

**Kivétel:** `anonymized = True` set → `display_name` módosítható "[Anonymized]" értékre.

---

## VIII. GDPR / Anonimizáció

### Policy

| Mező | Adat típus | Retention | Anonimizáció |
|------|-----------|-----------|-------------|
| `user_id` | FK, nullable | SET NULL on user delete | Automatikus |
| `user_id_snapshot` | Integer | Megmarad (nem PII önmagában) | Megmarad (nem azonosító önmagában) |
| `display_name` | String, PII | Aktív session: eredeti; törölt user: anonimizált | `"[Anonymized]"` |
| `owner_role` | String, nem PII | Megmarad | Megmarad |

### Service

```python
def anonymize_participant(db, participant_id):
    """Called when user exercises GDPR erasure right."""
    p = db.get(SessionParticipant, participant_id)
    p.display_name = "[Anonymized]"
    p.anonymized = True
    p.user_id = None
    p.revision += 1
```

---

## IX. Migration downgrade safety

### Éles környezet

`alembic downgrade -1` **NEM biztonságos éles adattal** — 5 tábla DROP. Kizárólag:
- Dev/staging üres DB
- Test cleanup
- CI migration roundtrip test

### Éles rollback stratégia

| Helyzet | Akció |
|---------|-------|
| Bug a service-ben | Forward-fix: new commit, hotfix PR |
| Schema hiba (constraint hibás) | ALTER TABLE korrekció, forward migration |
| Teljes feature revert szükséges | Application-level disable (feature flag), táblák maradnak üresek |

### Migration downgrade guard

```python
def downgrade() -> None:
    # GUARD: refuse if any session has captures
    conn = op.get_bind()
    count = conn.execute(text("SELECT COUNT(*) FROM capture_streams")).scalar()
    if count > 0:
        raise RuntimeError(
            "REFUSING downgrade: capture_streams contains data. "
            "Use forward-fix or manual data migration."
        )
    op.drop_table("capture_streams")
    op.drop_table("managed_devices")
    op.drop_table("session_devices")
    op.drop_table("session_participants")
    op.drop_table("multicamera_sessions")
```

---

## X. Service Contract

```python
# app/services/multicamera/session_repository.py

class RevisionConflictError(Exception):
    def __init__(self, entity_id, expected_rev, actual_rev):
        self.).entity_id = entity_id
        self.expected = expected_rev
        self.actual = actual_rev

def create_session(db, topology, authority_type, world_origin_camera_id) -> MultiCameraSession
def join_session(db, session_id, user_id, owner_role, display_name) -> SessionParticipant
def register_device(db, session_id, participant_id, device_role, device_type, camera_id, ...) -> SessionDevice
def register_managed_device(db, session_id, managed_by_device_id, camera_id, ...) -> ManagedDevice
def start_capture(db, session_id, camera_id, sequence_number, preset, ...) -> CaptureStream
def stop_capture(db, capture_id) -> CaptureStream
def update_device_state(db, device_id, revision, **new_states) -> SessionDevice  # raises RevisionConflictError
def heartbeat(db, device_id) -> None  # atomic, no revision
def remove_device(db, device_id) -> None  # soft delete + coordinator check
def set_calibration(db, session_id, calibration_json) -> None  # validates + persists
def anonymize_participant(db, participant_id) -> None
```

**Minden state-módosítás ezen a service-en keresztül megy.** Nincs közvetlen ORM attribute assignment a callerek-ben.

---

## XI. Tesztmátrix — Végleges (91 teszt)

### SSM: Session State Machine (22)

| ID | Teszt |
|----|-------|
| SSM-01 | created → configuring on all_devices_registered |
| SSM-02 | configuring → ready (no calibration topologies) |
| SSM-03 | configuring → calibrating (triangulation topologies) |
| SSM-04 | calibrating → ready |
| SSM-05 | ready → recording |
| SSM-06 | recording → degraded_recording |
| SSM-07 | recording → stopping |
| SSM-08 | degraded_recording → stopping |
| SSM-09 | stopping → post_processing |
| SSM-10 | post_processing → completed |
| SSM-11 | post_processing → completed_degraded |
| SSM-12 | * → failed on timeout |
| SSM-13 | TILTOTT: skip configuring |
| SSM-14 | TILTOTT: degraded → completed (bypass stopping) |
| SSM-15 | TILTOTT: ready → completed |
| SSM-16 | authority_type=server → coordinator NULL |
| SSM-17 | authority_type=client → coordinator required |
| SSM-18 | Revision ++ on state change |
| SSM-19 | No revision ++ on heartbeat |
| SSM-20 | Timeout → failed |
| SSM-21 | Coordinator removed → degraded_recording + revision ++ |
| SSM-22 | Coordinator from wrong session → FK violation |

### DSV: Device State Validator (15)

| ID | Teszt |
|----|-------|
| DSV-01..DSV-15 | (unchanged from v1.0 — all cross-state combinations) |

### SC: Session Contract (14)

| ID | Teszt |
|----|-------|
| SC-01 | instructor_solo fixture roundtrip |
| SC-02 | dual_player_onsite fixture decode |
| SC-03 | single_camera fixture decode |
| SC-04 | XOR both NULL → error |
| SC-05 | XOR both filled → error |
| SC-06 | XOR one filled → valid |
| SC-07 | Forward compat (unknown fields) |
| SC-08 | Backward compat (missing optional = default) |
| SC-09 | Fingerprint deterministic hash |
| SC-10 | Fingerprint mismatch |
| SC-11 | Coordinator XOR valid |
| SC-12 | Coordinator XOR invalid |
| SC-13 | Snapshot immutability (ORM hook blocks) |
| SC-14 | Anonymize updates display_name only |

### REP: Repository + Idempotency + Concurrency (18)

| ID | Teszt |
|----|-------|
| REP-01 | join_session idempotent (same user) |
| REP-02 | register_device idempotent (same camera_id) |
| REP-03 | start_capture idempotent (same seq) |
| REP-04 | stop_capture on stopped → no-op |
| REP-05 | start_capture new seq → new id |
| REP-06 | Revision conflict → RevisionConflictError |
| REP-07 | Revision match → success + revision+1 |
| REP-08 | Concurrent update → exactly one conflict |
| REP-09 | Source device RESTRICT: delete blocked |
| REP-10 | Cross-session device → FK violation |
| REP-11 | Cross-session managed_device → FK violation |
| REP-12 | Coordinator removed → session state + revision |
| REP-13 | Soft-deleted camera reregister → new device_id |
| REP-14 | User DELETE → user_id NULL, snapshot preserved |
| REP-15 | Heartbeat vs state: two PG connections, no lost update |
| REP-16 | Duplicate seq concurrent → exactly one IntegrityError |
| REP-17 | Migration roundtrip (upgrade + downgrade empty DB) |
| REP-18 | ORM model metadata matches migration schema (reflection) |

### CAL: Calibration Validator (6)

| ID | Teszt |
|----|-------|
| CAL-01 | Valid calibration accepted |
| CAL-02 | Unknown camera in intrinsics → error |
| CAL-03 | World origin not in cameras → error |
| CAL-04 | Duplicate pair → error |
| CAL-05 | Unknown schema version → error |
| CAL-06 | Extrinsic camera not in session → error |

### SC-S: Swift Contract (12)

| ID | Teszt |
|----|-------|
| SC-S-01 | instructor_solo fixture decode |
| SC-S-02 | All SessionTopology rawValues |
| SC-S-03 | All ConnectionState rawValues |
| SC-S-04 | All RecordingState rawValues |
| SC-S-05 | CaptureStream XOR null pattern |
| SC-S-06 | Fingerprint hash matches Python |
| SC-S-07 | Int64 timestamp lossless |
| SC-S-08 | UUID roundtrip |
| SC-S-09 | Null optional fields |
| SC-S-10 | revision field |
| SC-S-11 | Snapshot fields present |
| SC-S-12 | Anonymized display_name decode |

### DSV-S: Swift Validator (8)

| ID | Teszt |
|----|-------|
| DSV-S-01..DSV-S-08 | (unchanged — advisory validation) |

**Total: 22 + 15 + 14 + 18 + 6 + 12 + 8 = 95 teszt** (75 Python + 20 Swift)

---

## XII. Commit bontás

| # | Commit | Validálható |
|---|--------|-------------|
| 1 | `feat(multicamera): ORM models + alembic migration` | migration roundtrip |
| 2 | `feat(multicamera): Pydantic schemas + enums + validators` | schema unit test |
| 3 | `feat(multicamera): state machine + device validator` | SSM + DSV |
| 4 | `feat(multicamera): repository + heartbeat + revision` | REP tests |
| 5 | `feat(multicamera): calibration validator` | CAL tests |
| 6 | `feat(multicamera): Swift models + enums + validator` | iOS build |
| 7 | `test(multicamera): Python tests (75)` | full backend |
| 8 | `test(multicamera): Swift tests (20) + fixtures` | iOS tests |
| 9 | `chore(multicamera): model init registration` | app startup |

---

## XIII. CI gate-ek

| Gate | Típus |
|------|-------|
| 95/95 new tests PASS | RELEASE |
| Migration roundtrip (empty DB) | RELEASE |
| iOS BUILD SUCCEEDED (iPhone + iPad) | RELEASE |
| Existing 57 Skeleton3D tests PASS | RELEASE |
| Existing backend suite 0 new FAIL | RELEASE |
| Cross-platform fixture parity | RELEASE |
| Downgrade guard (non-empty DB blocked) | RELEASE |

---

## XIV. Scope-on kívül

- ❌ HTTP API endpoint
- ❌ Valódi capture recording
- ❌ GoPro orchestration
- ❌ Audio sync
- ❌ iPad UI
- ❌ Local network discovery
- ❌ Trianguláció
- ❌ Skeleton3DFrame módosítás
- ❌ PoseKeypointsDTO módosítás

---

## XV. Merge-readiness checklist

- [ ] 95/95 tests PASS
- [ ] Migration roundtrip (upgrade + downgrade empty)
- [ ] Downgrade guard active (non-empty → RuntimeError)
- [ ] iOS BUILD SUCCEEDED (iPhone + iPad simulator)
- [ ] 57/57 Skeleton3D tests PASS
- [ ] XOR: DB CHECK + ORM validator + Pydantic + Swift
- [ ] Composite FK: cross-session blocked at DB level
- [ ] Coordinator composite FK: same-session guaranteed
- [ ] Snapshot immutability: ORM listener blocks mutation
- [ ] Revision conflict: RevisionConflictError raised
- [ ] Heartbeat: atomic, no revision, no lost update (2-conn PG test)
- [ ] Soft delete: partial unique index allows reregister
- [ ] Calibration validator: all 6 checks pass
- [ ] Anonymize: display_name updated, snapshot preserved
- [ ] Full GitHub CI: 0 FAIL, 0 PENDING
- [ ] Scope-clean

---

## XVI. GO / NO-GO Verdikt

### **GO** — az alábbi feltételekkel:

1. PR-4B1 (#317) MERGED to main
2. Ez a v1.2 plan jóváhagyva
3. Külön implementációs jóváhagyás megadva
4. Meglévő modellek NEM módosulnak
5. Skeleton3DFrame (PR-4A) változatlan
6. Rollback: downgrade guard + forward-fix stratégia

### Nincs blocker:

- A 5 tábla + composite FK-k + partial indexek PostgreSQL standard feature-ök
- Az ORM event listener meglévő pattern a projektben (`juggling.py` már használ)
- A JSONB calibration rugalmas és migration-free
- A service repository pattern követi a meglévő `ball_training_service.py` mintát
- A soft delete (`deleted_at`) meglévő pattern (`JugglingContactEvent.deleted_at`)

---

**Implementációt, branchet vagy PR-t külön jóváhagyás nélkül nem kezdünk. A PR #317 scope-ja változatlan.**
