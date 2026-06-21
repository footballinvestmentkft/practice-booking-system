# AN-3B PR-4B — iPhone + GoPro HERO12 Synchronized Capture POC

**Date:** 2026-06-21
**Status:** Audit + terv — implementáció kizárólag külön jóváhagyás után.

---

## I. Jelenlegi iOS állapot

### A. Meglévő kamera infrastruktúra

| Komponens | Fájl | Funkció | Újrahasznosítható |
|-----------|------|---------|-------------------|
| `CameraManager` | `Skeleton/CameraManager.swift` | AVCaptureSession front camera → BodyPoseDetector | Részben (permission pattern) |
| `JugglingVideoExportService` | `Juggling/Upload/JugglingVideoExportService.swift` | Video export + compression | Nem (más célú) |
| `JugglingVideoPHPicker` | `Juggling/Upload/JugglingVideoPHPicker.swift` | PHPicker video import | Nem |

### B. Hiányzó infrastruktúra

| Komponens | Státusz |
|-----------|---------|
| CoreBluetooth (BLE) | **NINCS** — 0 sor a production kódban |
| Wi-Fi AP connection | **NINCS** — 0 sor |
| Video recording (rear camera) | **NINCS** — a CameraManager front kamerás, nem rögzít fájlt |
| Microphone capture / AVAudioSession | **NINCS** explicit |
| Background task management | **NINCS** |
| GoPro-specifikus kód | **NINCS** |

### C. Jelenlegi Info.plist permissionök

| Permission | Jelen? | PR-4B szükséges? |
|-----------|--------|-----------------|
| `NSCameraUsageDescription` | ✅ | ✅ (rear camera recording) |
| `NSBluetoothAlwaysUsageDescription` | ❌ | ✅ **ÚJ** |
| `NSMicrophoneUsageDescription` | ❌ | ✅ **ÚJ** (audio sync referencia) |
| `NSLocalNetworkUsageDescription` | ❌ | ✅ **ÚJ** (GoPro Wi-Fi) |

---

## II. GoPro HERO12 vezérlés — Részletes audit

### A. Open GoPro API (hivatalos)

| Szempont | Leírás |
|----------|--------|
| Forrás | https://github.com/gopro/OpenGoPro |
| Licenc | MIT |
| Commercial use | ✅ |
| iOS SDK | ❌ Nincs hivatalos Swift SDK |
| Implementáció | CoreBluetooth + URLSession (raw BLE/HTTP) |
| Dokumentáció | Teljes BLE + HTTP spec (OpenGoPro v2.0) |

### B. BLE Pairing flow

```
1. CBCentralManager.scanForPeripherals(withServices: [CBUUID("FEA6")])
2. Peripheral discovered → connect
3. Discover services + characteristics
4. Write pairing request → Control characteristic
5. GoPro confirms → encrypted connection established
6. Enable notifications on Command + Query + Settings response chars
```

### C. Wi-Fi connection

```
BLE command: Set AP mode ON
→ GoPro broadcasts SSID (stored in BLE characteristic)
→ iPhone joins Wi-Fi AP via NEHotspotConfiguration (iOS 11+)
   OR: manual user action (Settings → Wi-Fi)
→ HTTP commands available on 10.5.5.9:8080
```

**Megjegyzés:** `NEHotspotConfiguration` programmatic Wi-Fi join iOS 11+-on elérhető. De: a felhasználónak jóvá kell hagynia (iOS 14+ prompt). Az app nem tud transparensen csatlakozni.

### D. Parancsok

| Parancs | Protokoll | Endpoint | Latency |
|---------|----------|----------|---------|
| Start recording | HTTP | `GET /gopro/camera/shutter/start` | ~50-150ms HTTP + 200-500ms internal |
| Stop recording | HTTP | `GET /gopro/camera/shutter/stop` | ~50-150ms HTTP + 100-300ms |
| Get status | HTTP | `GET /gopro/camera/state` | ~30-80ms |
| Set preset | HTTP | `GET /gopro/camera/presets/load?id={id}` | ~100-500ms |
| Set setting | HTTP | `GET /gopro/camera/setting?setting={id}&option={val}` | ~50-200ms |
| Media list | HTTP | `GET /gopro/media/list` | ~50-150ms |
| Download file | HTTP | `GET /videos/DCIM/100GOPRO/{filename}` | throughput ~15-25 MB/s |
| Start recording (BLE) | BLE | Write command char | ~100-300ms |
| Get state (BLE) | BLE | Query characteristic | ~50-200ms |

### E. Firmware kompatibilitás

- Open GoPro v2.0: HERO12 Black firmware v2.0+
- Backward compat: API stable a minor verziókon belül
- Risk: firmware update változtathat BLE characteristic UUID-ket

### F. Background/foreground viselkedés

- **BLE:** iOS fenntartja a BLE connection-t background-ban (state restoration-nel)
- **Wi-Fi:** Ha az app background-ba kerül, az iOS Wi-Fi-t visszaadhatja az alap routernek
- **Recording:** GoPro-n a recording NEM függ az iPhone-tól — a BLE csak control channel
- **Ajánlás:** a POC-ban foreground-only. Background support = Phase 2 feature.

---

## III. Capture Session State Machine

```
                 ┌─────────────────────────────────────────────┐
                 │                                             │
    ┌──────┐    ┌──────────┐    ┌─────────┐    ┌───────────┐  │
    │ idle │───▶│discovering│───▶│ pairing │───▶│ connected │  │
    └──────┘    └──────────┘    └─────────┘    └───────────┘  │
                     │               │               │         │
                     ▼               ▼               ▼         │
                 ┌────────┐     ┌────────┐     ┌──────────┐   │
                 │ failed │     │ failed │     │configuring│   │
                 └────────┘     └────────┘     └──────────┘   │
                                                    │         │
                                                    ▼         │
                                               ┌────────┐     │
                                               │ ready  │     │
                                               └────────┘     │
                                                    │         │
                                                    ▼         │
                                               ┌──────────┐   │
                                               │ starting │   │
                                               └──────────┘   │
                                                    │         │
                                                    ▼         │
                                               ┌───────────┐  │
                                               │ recording │  │
                                               └───────────┘  │
                                                    │         │
                                                    ▼         │
                                               ┌──────────┐   │
                                               │ stopping │   │
                                               └──────────┘   │
                                                    │         │
                                                    ▼         │
                                             ┌──────────────┐ │
                                             │ transferring │ │
                                             └──────────────┘ │
                                                    │         │
                                                    ▼         │
                                             ┌───────────┐    │
                                             │ completed │────┘
                                             └───────────┘
```

### Állapotok részletezése

| Állapot | Belépés | Kilépés | Timeout | Retry | User-facing | Recovery |
|---------|---------|---------|---------|-------|-------------|----------|
| `idle` | App launch / reset | User taps "Connect" | — | — | "Csatlakoztasd a GoPro-t" | — |
| `discovering` | scan started | peripheral found OR timeout | 15s | Auto 3× | "GoPro keresése…" | → idle |
| `pairing` | peripheral connect | paired OR denied | 30s | — | "Párosítás…" | → idle |
| `connected` | BLE services discovered | Wi-Fi joined | 20s | Auto 2× | "Wi-Fi csatlakozás…" | → idle |
| `configuring` | HTTP reachable | preset verified | 10s | Auto 2× | "Beállítás ellenőrzése…" | → connected |
| `ready` | preset OK | User taps "Record" | — | — | "Indítható" | — |
| `starting` | Record command sent | Both cameras confirmed | 5s | — | "Indítás…" | → degraded |
| `recording` | Both running | User taps "Stop" | — | — | "Felvétel…" (timer) | — |
| `stopping` | Stop sent | Both stopped | 5s | — | "Leállítás…" | → degraded |
| `transferring` | GoPro file identified | Download complete | 300s | Auto 3× | "Átvitel…" (%) | → degraded |
| `completed` | Both captures verified | User taps "New" | — | — | "Kész ✓" | — |
| `degraded` | Partial failure | User decision | — | — | "Figyelem: részleges…" | → idle |
| `failed` | Unrecoverable | User taps "Retry" | — | — | "Hiba: {message}" | → idle |

---

## IV. Közös Session Contract

Épít a PR-4A `Skeleton3DFrame` contractra:

```python
class CaptureSessionMetadata(BaseModel):
    session_id:                  uuid.UUID
    iphone_capture_id:           uuid.UUID
    gopro_capture_id:            Optional[uuid.UUID]   # null if GoPro failed

    # Camera identification
    iphone_camera_id:            str = "iphone_primary"
    gopro_camera_id:             str = "gopro_hero12"
    iphone_device_model:         str          # "iPhone 15 Pro"
    gopro_firmware_version:      Optional[str]

    # Preset
    gopro_preset:                CapturePresetDTO
    iphone_preset:               CapturePresetDTO

    # Timestamps (nanoseconds, device-local monotonic)
    iphone_start_command_ns:     int
    iphone_recording_start_ns:   int
    gopro_start_command_sent_ns: int
    gopro_start_ack_ns:          Optional[int]
    gopro_recording_confirmed_ns: Optional[int]

    iphone_stop_command_ns:      int
    iphone_recording_stop_ns:    int
    gopro_stop_command_sent_ns:  int
    gopro_stop_ack_ns:           Optional[int]

    # Media
    gopro_media_filename:        Optional[str]    # "GX010042.MP4"
    gopro_media_size_bytes:      Optional[int]
    iphone_media_url:            Optional[str]    # local file URL
    audio_available_iphone:      bool
    audio_available_gopro:       bool

    # Transfer
    transfer_status:             Literal["pending", "in_progress", "completed", "failed", "skipped"]
    transfer_checksum_md5:       Optional[str]

    # Synchronization
    sync_status:                 Literal["not_started", "audio_sync_pending", "synced", "failed"]
    initial_offset_ms:           Optional[float]
    drift_rate_ms_per_s:         Optional[float]
    sync_quality:                Optional[str]    # "high" | "acceptable" | "degraded" | "failed"

    created_at:                  str  # ISO-8601
```

---

## V. Szinkronizációs POC terv

### A. Offline audio sync workflow

```
1. Session recording befejezése
2. iPhone video → audio track extraction (AVAssetReader, kAudioFormatLinearPCM)
3. GoPro video letöltése → audio track extraction (ugyanaz a módszer)
4. Audio cross-correlation (vDSP / Accelerate framework):
   a. 48kHz PCM → normalized float buffer
   b. Opening clap region detection (amplitude spike > threshold)
   c. Cross-correlate clap regions → peak = offset_samples
   d. offset_ms = offset_samples / 48000 * 1000
5. Optional: záró clap → drift calculation
   drift_rate = (end_offset - start_offset) / duration_seconds
6. Frame matching: per-frame PTS + corrected offset → nearest-neighbor pairing
7. Sync quality assessment:
   - offset < 16ms → "high"
   - offset < 35ms → "acceptable"
   - offset < 50ms → "degraded"
   - offset >= 50ms → "failed"
```

### B. Software start — metadata only

A software start command timestamp `gopro_start_command_sent_ns` kizárólag **metadata és fallback**:
- Nem tekindjük frame-pontos szinkronnak
- Tipikus pontatlansága: ±200-500ms
- Célja: durva orientáció az audio sync előtt
- Ha audio sync sikertelen → a software start a legjobb elérhető offset

---

## VI. POC UI terv

A PR-4B egy **külön debug/research screen** — nem integrálódik a Train AI flow-ba.

```
┌─────────────────────────────┐
│ 🔬 Multi-Camera POC         │
├─────────────────────────────┤
│ GoPro: [Connected ✓]       │
│ Preset: 1080p/30/Linear ✓  │
│ iPhone: Ready               │
│ Session: abc123...          │
├─────────────────────────────┤
│ [🔴 START RECORDING]       │
│                             │
│ Duration: 00:00             │
│ Start latency: --           │
├─────────────────────────────┤
│ Sync: [Not started]        │
│ GoPro file: --              │
│ Transfer: [--]              │
│ Audio offset: --            │
│                             │
│ [Export Session JSON]       │
└─────────────────────────────┘
```

---

## VII. Hibakezelés — Failure Matrix

| Hiba | Detekció | Reakció | Státusz |
|------|----------|---------|---------|
| GoPro nem található (BLE) | 15s timeout | Retry 3×, majd failed | `failed` |
| Pairing rejected | BLE delegate callback | User info | `failed` |
| Wi-Fi nem csatlakozik | HTTP timeout 20s | Retry 2× | `failed` |
| BLE megszakad recording közben | Peripheral disconnect | GoPro recording folytatódik! Stop manual | `degraded` |
| Start command timeout (5s) | Timer | iPhone starts anyway, GoPro uncertain | `degraded` |
| GoPro nem indul | Status poll "not recording" | iPhone stop, session incomplete | `degraded` |
| iPhone nem indul | AVCaptureSession error | GoPro stop, session incomplete | `failed` |
| Stop only one device | Partial stop ack | Both status checked, mark partial | `degraded` |
| GoPro battery low | Status query threshold | Warning before start | `ready` (with warning) |
| Storage full | Status query / HTTP error | Block start | `configuring` → warning |
| Audio missing | Post-recording check | Sync fallback to software start | `completed` (degraded sync) |
| Download failure | HTTP error / timeout | Retry 3×, then skip | `completed` (no transfer) |
| App backgrounded | UIApplication lifecycle | Pause UI, GoPro continues recording | `recording` (warning) |
| App crash | — | Session recovery from persisted metadata | `degraded` on relaunch |

---

## VIII. Acceptance Gate-ek

| Gate | Metrika | Target | Indoklás |
|------|---------|--------|----------|
| POC-G1 | Session success rate (10-ből) | ≥ 9/10 | Alapvető működési stabilitás |
| POC-G2 | Recording start confirmed (mindkét eszköz) | 10/10 | Critical: mindkét camera-nak indulnia kell |
| POC-G3 | Audio sync success | ≥ 9/10 | Clap detekció megbízhatósága |
| POC-G4 | Initial offset (audio sync után) | < 16ms | SY-G1 from Architecture doc |
| POC-G5 | p95 frame alignment | < 35ms | SY-G4 |
| POC-G6 | Drift | < 3ms/perc | SY-G2 |
| POC-G7 | Frame mismatch rate | < 2% | SY-G5 |
| POC-G8 | Silent partial session | 0 | Nincs session ami csendben incomplete |
| POC-G9 | BLE command latency p95 | < 500ms | Felhasználói élmény |
| POC-G10 | GoPro file download success | ≥ 9/10 | Transfer megbízhatóság |

**Módosítás az eredeti javaslathoz képest:** POC-G2-t 10/10-re emeltem (9/10 helyett), mert a recording confirmation a rendszer legkritikusabb invariánsa — ha egy camera nem indul, az egész session értéktelen.

---

## IX. Licence és Dependency Audit

| Dependency | Source | Licenc | Commercial | iOS | Maintenance |
|------------|--------|--------|-----------|-----|-------------|
| **Open GoPro spec** | github.com/gopro/OpenGoPro | MIT | ✅ | Spec only | Active (GoPro official) |
| **CoreBluetooth** | Apple SDK | Proprietary | ✅ | Native | Apple maintained |
| **Network.framework** | Apple SDK | Proprietary | ✅ | Native (iOS 12+) | Apple maintained |
| **NEHotspotConfiguration** | Apple SDK | Proprietary | ✅ | Native (iOS 11+) | Apple maintained |
| **AVFoundation** | Apple SDK | Proprietary | ✅ | Native | Apple maintained |
| **Accelerate (vDSP)** | Apple SDK | Proprietary | ✅ | Native | Apple maintained |

**Nincs third-party dependency.** A teljes implementáció Apple natív frameworkökkel + Open GoPro HTTP/BLE spec alapján.

---

## X. PR-bontás — Senior Ajánlás

### Ajánlott: 4 PR bontás

| PR | Scope | Indoklás |
|----|-------|----------|
| **PR-4B1** | GoPro BLE connection + command state machine | Legmagasabb kockázat (hardware-függő BLE). Külön validálható. |
| **PR-4B2** | Dual capture session + iPhone recording | A session contract + iPhone rear-camera recording. GoPro nélkül is tesztelhető. |
| **PR-4B3** | GoPro media retrieval + offline audio sync | Transfer + cross-correlation. Szintetikus audio-val is tesztelhető. |
| **PR-4B4** | Physical device benchmark + gate report | Nem code PR — mérési eredmények. Hardware E2E validáció. |

**Indoklás a 4-es bontásra:**
1. A BLE/Wi-Fi kód kockázatos (firmware-függő, nem mockolt CI-ben) — külön validálható
2. A session contract + iPhone recording hardware nélkül tesztelhető mock BLE adapterrel
3. Az audio sync algoritmikus — szintetikus jelekkel unit-tesztelhető
4. A fizikai benchmark nem code-change, hanem mérési riport

---

## XI. Tesztstratégia

### Mockolt tesztek (CI-ben futnak)

| Suite | Scope | Teszt típus |
|-------|-------|-------------|
| SM (state machine) | State transitions, timeouts, retries | Pure logic |
| BLE-M (mock BLE) | Connection, discovery, command handling | Mock CBCentralManager |
| CMD (command) | HTTP command encoding, response parsing | Mock URLSession |
| META (metadata) | Session metadata serialization | Pure model |
| PRESET (preset) | Preset validation, compatibility | Pure logic |
| SYNC (audio sync) | Cross-correlation, offset, drift | Synthetic PCM buffers |
| DROP (dropped frame) | PTS gap detection, frame matching | Synthetic timestamps |

### Hardware E2E (manuális, fizikai eszközökkel)

| Teszt | Leírás |
|-------|--------|
| HW-E2E-01 | Teljes BLE discovery → pairing → Wi-Fi → record → stop |
| HW-E2E-02 | 5 perc felvétel, audio sync < 16ms |
| HW-E2E-03 | GoPro battery warning kezelés |
| HW-E2E-04 | BLE disconnect recovery |
| HW-E2E-05 | App background → foreground |
| HW-E2E-06 | 10× ismételt session (gate compliance) |
| HW-E2E-07 | File transfer teljes 1080p/30 fájl |
| HW-E2E-08 | Audio clap detection precision |

---

## XII. Fájlérintettség (becsült)

### PR-4B1 — GoPro Connection

| Fájl | Tartalom |
|------|----------|
| `ios/LFAEducationCenter/MultiCamera/GoProBLEManager.swift` | BLE discovery, pairing, connection |
| `ios/LFAEducationCenter/MultiCamera/GoProHTTPClient.swift` | HTTP command execution |
| `ios/LFAEducationCenter/MultiCamera/GoProConnectionStateMachine.swift` | Connection state machine |
| `ios/LFAEducationCenter/MultiCamera/GoProModels.swift` | Status, Preset, MediaList DTOs |
| `ios/LFAEducationCenter/App/Info.plist` | +3 permission (BLE, Microphone, LocalNetwork) |
| `ios/LFAEducationCenterTests/MultiCamera/GoProStateMachineTests.swift` | SM-01..SM-20 |
| `ios/LFAEducationCenterTests/MultiCamera/GoProCommandTests.swift` | CMD-01..CMD-10 |

### PR-4B2 — Dual Capture Session

| Fájl | Tartalom |
|------|----------|
| `ios/LFAEducationCenter/MultiCamera/CaptureSessionManager.swift` | Dual-camera orchestration |
| `ios/LFAEducationCenter/MultiCamera/IPhoneVideoRecorder.swift` | AVCaptureSession rear camera recording |
| `ios/LFAEducationCenter/MultiCamera/CaptureSessionMetadata.swift` | Session contract (Swift DTO) |
| `ios/LFAEducationCenter/MultiCamera/MultiCameraPOCView.swift` | Debug UI |
| `app/schemas/skeleton_3d.py` | `CaptureSessionMetadata` Pydantic (extend) |
| `ios/LFAEducationCenterTests/MultiCamera/CaptureSessionTests.swift` | META-01..META-08 |
| `ios/LFAEducationCenterTests/MultiCamera/PresetValidationTests.swift` | PRESET-01..PRESET-06 |

### PR-4B3 — Audio Sync

| Fájl | Tartalom |
|------|----------|
| `ios/LFAEducationCenter/MultiCamera/AudioSyncEngine.swift` | PCM extraction + cross-correlation |
| `ios/LFAEducationCenter/MultiCamera/GoProMediaTransfer.swift` | File download + progress |
| `ios/LFAEducationCenter/MultiCamera/FrameTimeMatcher.swift` | PTS alignment + drift correction |
| `ios/LFAEducationCenterTests/MultiCamera/AudioSyncTests.swift` | SYNC-01..SYNC-12 |
| `ios/LFAEducationCenterTests/MultiCamera/FrameMatcherTests.swift` | DROP-01..DROP-06 |

---

## XIII. Tiltott scope

- ❌ Train AI integration
- ❌ Production user session
- ❌ Automatikus upload
- ❌ Trianguláció
- ❌ 3D viewer
- ❌ Multi-person
- ❌ Backend API endpoint (metadata-t lokálisan exportáljuk JSON-ként)
- ❌ Alembic migration
- ❌ Meglévő kamera pipeline módosítás
- ❌ 3rd party GoPro SDK (csak natív Apple + Open GoPro spec)

---

## XIV. Szükséges-e DB/Migration?

**NEM.** A PR-4B kizárólag:
- iOS-oldali code (BLE + capture + sync)
- Python schema bővítés (`CaptureSessionMetadata` DTO — no ORM)
- JSON export a POC UI-ból

A session metadata lokálisan JSON fájlként exportálódik. Backend persistence = Phase 2 (API endpoint PR).

---

## XV. GO / NO-GO Ajánlás

### GO — az alábbi feltételekkel:

1. ✅ A PR-4A contract foundation mainen van (merge commit `f7a2af8a`)
2. ✅ Az Open GoPro API MIT licencű, hivatalos, aktívan karbantartott
3. ✅ Minden dependency Apple natív framework (0 third-party)
4. ✅ A POC debug-only screen, nem érint production flow-t
5. ✅ A 4 PR-es bontás izolált kockázatkezelést biztosít

### Kockázatok

| Kockázat | Súlyosság | Mitigáció |
|----------|-----------|-----------|
| GoPro BLE instabilitás firmware-frissítésnél | Közepes | Pin firmware version, defensive coding |
| NEHotspotConfiguration iOS prompt | Alacsony | Felhasználói útmutató + fallback manual join |
| Wi-Fi disconnect background-ban | Közepes | Foreground-only POC, warning overlay |
| Audio clap detection false positive | Alacsony | Threshold calibration, manual override |
| BLE latency variance | Közepes | Metadata logging, nem a szinkronizáció alapja |

---

**Implementációt, branchet vagy PR-t külön jóváhagyás nélkül nem kezdünk.**
