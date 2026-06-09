# AN-3B Multi-Camera Session Architecture v2.1 — Contract Consistency

**Date:** 2026-06-21
**Status:** Architecture-only — implementáció kizárólag külön jóváhagyás után.

---

## I. Domain Model — Szétválasztott entitások

### A. Entitás hierarchia

```
MultiCameraSession (1)
  ├── SessionParticipant (1..N)  — személy
  │     └── SessionDevice (1..M)  — fizikai eszköz per résztvevő
  │           └── CaptureStream (0..K)  — felvételi futam
  └── ManagedDevice (0..L)  — tulajdonos nélküli eszköz (GoPro)
        └── CaptureStream (0..K)
```

### B. SessionParticipant — személy

```python
class SessionParticipant(BaseModel):
    participant_id:    uuid.UUID
    user_id:           int
    owner_role:        str          # "student" | "instructor"
    display_name:      str
    joined_at:         str          # ISO-8601
    revision:          int = 0      # Optimistic concurrency
```

Lifecycle: create → join → active → leave / disconnect. Reconnect: `participant_id` marad, `revision` növekszik.

### C. SessionDevice — fizikai eszköz (személyhez kötve)

```python
class SessionDevice(BaseModel):
    device_id:         uuid.UUID
    participant_id:    uuid.UUID
    device_role:       str          # DeviceRole enum
    device_type:       str          # CaptureDeviceType enum
    device_model:      Optional[str]
    os_version:        Optional[str]
    app_version:       Optional[str]
    camera_id:         str          # Session-szintű egyedi
    connection_state:  str          # ConnectionState enum (5 state)
    readiness_state:   str          # ReadinessState enum
    recording_state:   str          # RecordingState enum
    upload_state:      str          # UploadState enum
    last_heartbeat_at: Optional[str]
    revision:          int = 0
```

### D. ManagedDevice — GoPro (nem participant)

```python
class ManagedDevice(BaseModel):
    device_id:              uuid.UUID
    managed_by_device_id:   uuid.UUID       # FK → SessionDevice.device_id
    owner_participant_id:   Optional[uuid.UUID]
    device_type:            str             # "gopro"
    device_role:            str             # "auxiliary_camera"
    camera_id:              str
    firmware_version:       Optional[str]
    ble_state:              str             # GoProBLEState enum
    wifi_state:             str             # GoProWiFiState enum
    http_state:             str             # GoProHTTPState enum
    recording_status:       str             # GoProRecordingStatus enum
    readiness_state:        str             # ReadinessState enum
    upload_state:           str             # UploadState enum
    last_heartbeat_at:      Optional[str]
    revision:               int = 0
```

### E. CaptureStream — felvételi futam

```python
class CaptureStream(BaseModel):
    capture_id:          uuid.UUID
    session_id:          uuid.UUID
    camera_id:           str
    session_device_id:   Optional[uuid.UUID]   # XOR: pontosan 1 kitöltve
    managed_device_id:   Optional[uuid.UUID]   # XOR: pontosan 1 kitöltve
    sequence_number:     int
    start_timestamp_ns:  Optional[int]
    stop_timestamp_ns:   Optional[int]
    media_identifier:    Optional[str]
    media_size_bytes:    Optional[int]
    audio_available:     bool
    capture_preset:      CapturePresetDTO
    state:               str          # CaptureStreamState enum
    revision:            int = 0
```

**XOR constraint:** `(session_device_id IS NOT NULL) XOR (managed_device_id IS NOT NULL)` — egy CaptureStream pontosan egy forráshoz tartozik.

---

## II. Ortogonális állapotdimenziók

### A. ConnectionState (csak hálózati elérhetőség)

```
registered      — Regisztrálva, még nem csatlakozott
connecting      — Aktív csatlakozási kísérlet
connected       — Hálózaton elérhető, heartbeat OK
disconnected    — Heartbeat timeout vagy explicit disconnect
failed          — Helyreállíthatatlan (auth hiba, banned, etc.)
```

### B. ReadinessState (konfigurációs állapot)

```
unconfigured    — Preset nincs ellenőrizve
configured      — Preset betöltve/ellenőrizve
preset_mismatch — Kalibráció és aktuális preset eltér
ready           — Minden előfeltétel teljesül, indítható
```

### C. RecordingState (felvételi állapot)

```
idle            — Nem rögzít
starting        — Start parancs kiküldve, várakozás
recording       — Aktív felvétel
stopping        — Stop parancs kiküldve, várakozás
stopped         — Felvétel befejezve, fájl rendelkezésre áll
```

### D. UploadState (médiafájl átvitel)

```
none            — Nincs átviteli feladat
pending         — Átvitel várakozik
transferring    — Aktív átvitel
completed       — Sikeresen átadva
failed          — Átvitel sikertelen
```

### E. GoPro-specifikus állapotok (ManagedDevice)

```python
class GoProBLEState(str, Enum):
    disconnected = "disconnected"
    connecting = "connecting"
    connected = "connected"
    paired = "paired"
    failed = "failed"

class GoProWiFiState(str, Enum):
    off = "off"
    ap_activating = "ap_activating"
    ap_active = "ap_active"
    iphone_joining = "iphone_joining"
    connected = "connected"
    failed = "failed"

class GoProHTTPState(str, Enum):
    unreachable = "unreachable"
    verifying = "verifying"
    reachable = "reachable"
    failed = "failed"

class GoProRecordingStatus(str, Enum):
    unknown = "unknown"
    idle = "idle"
    recording = "recording"
    stopping = "stopping"
```

**A GoPro heartbeat = HTTP status poll.** Nem egyetlen "connected" állapot, hanem BLE + Wi-Fi + HTTP + recording state külön-külön.

### F. Érvényes és tiltott cross-state kombinációk

| connection | readiness | recording | Érvényes? |
|-----------|-----------|-----------|-----------|
| connected | ready | recording | ✅ normál felvétel |
| connected | unconfigured | idle | ✅ frissen csatlakozott |
| connected | ready | idle | ✅ indításra vár |
| disconnected | * | recording | ⚠️ lokális felvétel folytatódhat |
| disconnected | * | starting | ❌ TILTOTT — transition → recording vagy idle |
| failed | * | recording | ❌ TILTOTT — failed = nincs aktív felvétel |
| registered | ready | * | ❌ TILTOTT — ready csak connected-ből |
| * | preset_mismatch | recording | ❌ TILTOTT — felvétel indítás blocked |
| connected | unconfigured | recording | ❌ TILTOTT — felvétel konfigurálás nélkül |

---

## III. Session Coordinator — Authority Model

### A. MultiCameraSession

```python
class MultiCameraSession(BaseModel):
    session_id:              uuid.UUID
    topology:                str                     # SessionTopology enum
    authority_type:          str                     # "client" | "server"
    coordinator_device_id:   Optional[uuid.UUID]    # Kötelező HA authority_type == "client"
    world_origin_camera_id:  str
    calibration_set_id:      Optional[uuid.UUID]
    state:                   str                     # SessionState enum
    created_at:              str
    revision:                int = 0
    schema_version:          str = "1"
```

**Szabály:** `coordinator_device_id` NOT NULL IFF `authority_type == "client"`. Server-authority esetén NULL (a backend a koordinátor).

### B. Authority assignment

| Topology | authority_type | coordinator_device_id | Indoklás |
|----------|---------------|----------------------|----------|
| `single_camera` | client | player_primary device | Egyetlen eszköz |
| `dual_player_remote` | **server** | NULL | Nincs közös hálózat |
| `dual_player_onsite` | client | player_primary device | Lokális hálózaton |
| `instructor_solo` | client | instructor_primary device | iPad vezérel |
| `instructor_dual` | client | instructor_primary device | iPad vezérel |

### C. Remote session szinkronizáció

`dual_player_remote` topológiánál a backend az autoritatív forrás:

| Megközelítés | Előny | Hátrány | Verdict |
|---|---|---|---|
| **HTTP polling** (5s) | Egyszerű, meglévő infra | Latency, nem valós idejű | **MVP** |
| WebSocket | Valós idejű, alacsony latency | Új infra, connection management | Phase 2 |
| Server-Sent Events | Egyirányú real-time | Nem kétirányú | Phase 2 alternatíva |

**MVP:** HTTP polling 5s intervallummal. A `session.revision` mező garantálja, hogy a kliens mindig a legfrissebb állapotot kapja (optimistic concurrency: `If-Match: revision` header).

### D. Coordinator failure policy

| Helyzet | authority | Reakció | Eredmény |
|---------|----------|---------|----------|
| Coordinator disconnect | client | Felvétel helyben folytatódik, stop nem lehetséges távolról | `degraded_recording` |
| Coordinator disconnect | server | Backend auto-stop: 60s timeout | `auto_stopped` |
| Coordinator crash | client | Persist state → recovery on relaunch | `degraded_recording` + recovery |
| Coordinator battery | client | Ugyanaz mint disconnect | `degraded_recording` |

**Nincs automatikus kliens-failover MVP-ben.**

---

## IV. Kalibráció — Javított modell

### A. CalibrationSet

```python
class CalibrationSet(BaseModel):
    calibration_set_id:     uuid.UUID
    session_id:             Optional[uuid.UUID]
    version:                int
    created_at:             str
    intrinsics:             List[IntrinsicCalibrationDTO]
    extrinsics:             List[PairwiseExtrinsicDTO]
    world_origin_camera_id: str
    schema_version:         str = "1"
```

### B. Lejárati szabályok — kizárólag geometriai események

A kalibráció KIZÁRÓLAG az alábbi események miatt járhat le:

| Esemény | Hatás | Detekció |
|---------|-------|----------|
| Fizikai elmozdítás | Extrinsic invalid | Session-start control pont check |
| Orientáció változás | Extrinsic invalid | Device orientation sensor |
| Felbontás/preset változás | Intrinsic invalid | Preset mismatch detection |
| Zoom/fókusz módosulás | Intrinsic invalid | Metadata check (ha elérhető) |
| Kamera/lencse csere | Teljes kalibráció invalid | camera_id változás |
| Explicit újrakalibrálás | Felváltja a régit | User-initiated |

**NEM jár le heartbeat timeout, connection loss, vagy időalapú lejárat miatt.** A kalibráció matematikai adat — nem avul el pusztán az idő múlásával.

### C. Láncolt extrinsic (chain triangulation)

Szabályok:

| Szabály | Limit | Indoklás |
|---------|-------|----------|
| Max chain depth | **2** (A↔B↔C, max 1 köztes) | Összetett hiba exponenciálisan nő |
| Max chain reprojection error | < 3px összesített | Gate: `sum(pair_errors) < 3px` |
| Chain confidence | `min(pair_confidences) * 0.8` | Degradáció jelzése |
| Chain depth > 2 | **TILTOTT** | Nem triangulálható így |

```python
class TriangulationPairInfo(BaseModel):
    camera_a_id:             str
    camera_b_id:             str
    chain_depth:             int          # 1 = direkt, 2 = 1 köztes
    chain_path:              List[str]    # ["cam_a", "cam_b"] or ["cam_a", "cam_c", "cam_b"]
    combined_reproj_error:   float
    chain_confidence:        float        # [0,1]
    is_direct:               bool
```

---

## V. Session State Machine — Degraded lezárás

```
created → configuring → calibrating → ready → recording → stopping → post_processing → completed
                                                 │                                         │
                                                 ▼                                         │
                                        degraded_recording → stopping → post_processing    │
                                                                            │              │
                                                                            ▼              │
                                                                    completed_degraded ────┘
         ↓              ↓
       failed         failed
```

| Állapot | Belépés | Kilépés | Timeout |
|---------|---------|---------|---------|
| `created` | Session allocated | All devices registered | 120s → failed |
| `configuring` | Devices registered | Presets verified | 30s → failed |
| `calibrating` | Presets OK | CalibrationSet valid | 300s (manual, no auto-fail) |
| `ready` | Calibration OK (or skipped) | Coordinator starts | — |
| `recording` | All confirmed | Stop OR device failure | — |
| `degraded_recording` | Device failed during recording | Stop command | — |
| `stopping` | Stop sent | All confirmed stopped | 10s → force stop |
| `post_processing` | All stopped | Sync + transfer complete | 600s → completed_degraded |
| `completed` | All processed successfully | — | — |
| `completed_degraded` | Processed with missing/incomplete data | — | — |
| `failed` | Unrecoverable (timeout, all disconnect) | — | — |

**Szabály:** `degraded_recording` NEM kerülheti meg `stopping` → `post_processing` → `completed_degraded` szekvenciát. Nincs direct jump `degraded → completed`.

---

## VI. Graceful Degradation — Feltételes

| Helyzet | Feltétel | Eredmény |
|---------|---------|----------|
| N → N-1 kamera, maradó ≥ 2 közösen kalibrált+szinkronizált | ✅ | Multi-view VAGY 2-view fallback |
| N → N-1, maradó 2 NEM kalibrált egymással | ❌ | 2D-only, `degraded_recording` |
| N → 1 kamera | — | 2D-only, `degraded_recording` |
| 1 → 0 | — | `failed` |
| GoPro BLE disconnect, recording continues | BLE ≠ recording | Warning, GoPro data available post-session |

---

## VII. iPad Readiness — Kódbizonyítékkal

### A. Build

| Szempont | Sor/file | Eredmény |
|----------|---------|----------|
| `TARGETED_DEVICE_FAMILY = "1,2"` | `project.pbxproj:1430,1459,1476,1494` | ✅ Universal |
| `SUPPORTED_PLATFORMS = "iphoneos iphonesimulator"` | `project.pbxproj:1425,1454` | ✅ |
| `IPHONEOS_DEPLOYMENT_TARGET = 15.0` | `project.pbxproj:1417,1446,1471` | ✅ |
| iPad Simulator build | `xcodebuild -destination 'iPad Pro 13-inch (M4),OS=18.1'` → **BUILD SUCCEEDED** | ✅ |

### B. Layout kockázatok

| Probléma | Hely | Súlyosság | PR scope |
|----------|------|-----------|---------|
| Portrait-only (iPad is) | Info.plist:51 | ⚠️ közepes | PR-4B4 (landscape unlock) |
| `UIScreen.main.bounds` | EventLabelDetailView:328 | ⚠️ alacsony | Nem PR-4B scope |
| Sheet → iPad popover | 43 helyen | ⚠️ vizuális | Nem PR-4B scope |
| Multitasking nem tesztelt | — | ⚠️ ismeretlen | Nem PR-4B scope |

---

## VIII. Contract Schema és Compatibility

### A. Schema version

| Contract | schema_version | Compatibility |
|----------|---------------|--------------|
| `MultiCameraSession` | `"1"` | Forward: ismeretlen mezők ignorálva |
| `SessionParticipant` | nincs (session version öröklődik) | — |
| `CaptureStream` | nincs (session version öröklődik) | — |
| `CalibrationSet` | `"1"` | Forward: ismeretlen mezők ignorálva |
| `Skeleton3DFrame` (PR-4A) | `"2"` | **Nem módosul** |

### B. Backward/forward compatibility szabály

- **Backward:** régebbi kliens képes olvasni újabb schemát (ismeretlen mezők ignorálva)
- **Forward:** újabb kliens képes olvasni régebbi schemát (hiányzó mezők default értékkel)
- **Breaking change:** schema_version növelés + migration path dokumentálva

### C. Idempotency szabályok

| Művelet | Idempotency key | Viselkedés ismételt híváskor |
|---------|----------------|------------------------------|
| `join_session` | `(session_id, participant_id)` | Visszaadja a meglévő participant-et |
| `register_device` | `(session_id, device_id)` | Visszaadja a meglévő device-t |
| `start_capture` | `(session_id, device_id, sequence_number)` | Visszaadja a meglévő capture-t |
| `stop_capture` | `(capture_id)` | No-op ha már stopped |
| `update_state` | `(entity_id, revision)` | 409 Conflict ha revision mismatch |

### D. Optimistic concurrency

Minden mutable entitás tartalmaz `revision: int` mezőt:
- Olvasáskor a kliens megkapja az aktuális revision-t
- Íráskor a kliens küld `If-Match: {revision}` header-t
- Ha a szerveren a revision magasabb → **409 Conflict**, kliens újraolvas
- Sikeres írás: `revision += 1`

---

## IX. Acceptance Gate-ek — PR-hez rendelve

### PR-4B2 gates (contract + lifecycle)

| Gate | Target | Típus | Mérés |
|------|--------|-------|-------|
| Topology validation unit test | 100% coverage | RELEASE | pytest |
| State transition unit test | Minden érvényes + tiltott kombináció | RELEASE | pytest + Swift XCTest |
| Cross-platform fixture parity | Python ↔ Swift | RELEASE | Fixture decode test |
| Idempotency unit test | join/register/start/stop | RELEASE | pytest |
| Optimistic concurrency unit test | revision conflict → 409 | RELEASE | pytest |

### PR-4B3 gates (iPhone dual capture)

| Gate | Target | Típus | Mérés |
|------|--------|-------|-------|
| Session join latency (local) | < 2s | POC | 2 iPhone, timestamp |
| Software start delta | < 500ms | POC | monotonic diff |
| CaptureStream lifecycle | pending→recording→stopped | RELEASE | Unit test |
| Coordinator disconnect handling | degraded_recording state | RELEASE | Mock disconnect test |

### PR-4B4 gates (GoPro + iPad)

| Gate | Target | Típus | Mérés |
|------|--------|-------|-------|
| 3 cameras online at start | 100% (10/10) | RELEASE | Physical test |
| GoPro start confirmed | < 1s | POC | BLE→status poll |
| iPad build + capture | BUILD SUCCEEDED + file output | RELEASE | CI |
| Graceful degradation | 1-camera dropout → degraded_recording | RELEASE | Controlled test |

### PR-4B5 gates (audio sync)

| Gate | Target | Típus | Mérés |
|------|--------|-------|-------|
| Audio sync offset (best pair) | < 16ms | RELEASE | Cross-correlation |
| Frame alignment p95 | < 35ms | RELEASE | PTS statistika |
| Drift / perc | < 3ms | TBD | Záró clap mérés |
| Multi-pair consistency | max pair delta < 5ms | TBD | N-pair comparison |

### PR-4B6 gates (benchmark)

| Gate | Target | Típus | Mérés |
|------|--------|-------|-------|
| Per-topology end-to-end | Összes RELEASE gate teljesül | RELEASE | Physical |
| 3-camera valid joint ratio | > 60% | TBD | Triangulation output |
| Coordinator recovery | State restored | POC | Crash test |

---

## X. PR Roadmap — Végleges

| PR | Scope | Előfeltétel | Merge gate-ek |
|----|-------|-------------|--------------|
| **PR-4B1** | GoPro connection SM (NYITVA #317) | PR-4A ✅ | SM-20/20, iOS build, GoPro smoke |
| **PR-4B2** | Multi-device session contract | PR-4B1 merge | Lifecycle tests, fixture parity, idempotency |
| **PR-4B3** | iPhone dual capture + local coord | PR-4B2 merge | 2 iPhone session join, capture lifecycle |
| **PR-4B4** | GoPro + iPad integration | PR-4B3 merge | 3-camera online, iPad build, degradation |
| **PR-4B5** | Audio sync + multi-pair | PR-4B3 merge | Audio offset < 16ms, p95 < 35ms |
| **PR-4B6** | Physical benchmark | PR-4B4 + PR-4B5 | All RELEASE gates pass |

---

## XI. Verdikt

### Végleges pontok

- Domain model: SessionParticipant / SessionDevice / ManagedDevice / CaptureStream szétválasztás
- Ortogonális state dimenziók: connection (5) × readiness (4) × recording (5) × upload (5)
- GoPro 4-rétegű state: BLE + WiFi + HTTP + recording status
- Coordinator authority: client | server, nincs failover MVP-ben
- Kalibráció lejárat: kizárólag geometriai események, NEM időalapú
- Chain trianguláció: max depth 2, max combined error < 3px
- CaptureStream FK: XOR constraint (session_device_id | managed_device_id)
- Session state machine: degraded_recording → stopping → post_processing → completed_degraded
- Idempotency + optimistic concurrency (revision mező)
- Schema versioning + backward/forward compat szabály

### TBD — benchmark után véglegesítendő

- Drift / perc target (< 3ms jelenleg ideiglenes)
- Multi-pair sync consistency target
- 3-camera triangulation valid joint ratio (> 60% ideiglenes)
- Chain confidence degradáció mértéke

### Fizikai benchmark szükséges

- Session join latency (2 iPhone)
- GoPro start confirmation latency
- Audio sync precision per topology
- Coordinator failure recovery time
- Degradation fallback latency

### PR-4B2 contract scope — végleges

1. `MultiCameraSession`, `SessionParticipant`, `SessionDevice`, `ManagedDevice`, `CaptureStream` Pydantic + Swift Codable
2. `SessionTopology`, `ConnectionState`, `ReadinessState`, `RecordingState`, `UploadState`, `GoProBLEState`, `GoProWiFiState`, `GoProHTTPState`, `GoProRecordingStatus` enumok
3. `CalibrationSet` + `PairwiseExtrinsicDTO` + `TriangulationPairInfo`
4. Cross-state validation (érvényes + tiltott kombinációk)
5. Session state machine (10 állapot + transitions)
6. Coordinator authority contract
7. Idempotency + optimistic concurrency (revision)
8. Schema version + compatibility szabályok
9. Közös Python + Swift fixture-ek
10. Unit tesztek: state transitions, topology validation, XOR constraint, idempotency

### Implementáció feltételei

A PR-4B2 implementáció **megkezdhető** ha:
1. ✅ PR-4B1 (#317) MERGED to main
2. ✅ Ez a v2.1 dokumentum jóváhagyva
3. ✅ Külön implementációs jóváhagyás megadva

---

**Implementációt, branchet vagy PR-t külön jóváhagyás nélkül nem kezdünk. A PR #317 scope-ja változatlan.**
