# MC1 Tricamera Project — Handoff Report

**Date:** 2026-06-28
**Branch:** `feat/mc1-gopro-tricamera-smoke`
**Base:** `main` @ `4c8d36327d8f86c3c1e808595fadf772e1f39437`
**Status:** ALL CHANGES UNCOMMITTED — 23 modified files, 10 new files, +2152/-486 lines

---

## 1. Végső cél

Instructor egyetlen eszközön (iPhone) élőben látja és vezérli három kamera rendszerét:

- **iPhone kamera** — instructor saját kamerája, lokális preview + rögzítés
- **iPad kamera** — player kamerája, élőkép Multipeer Connectivity-n az instructor dashboard-ra
- **GoPro HERO13** — auxiliary kamera, iPhone által vezérelve HTTP-n

Egy közös session, közös cycle start/stop, három szinkronizált video artifact, utána skeleton (Vision framework pose detection) overlay proof.

---

## 2. Aktuális szerepmodell

| Szerep | Eszköz | Indoklás |
|--------|--------|----------|
| Instructor / coordinator | **iPhone** | Mobilnet biztosítja backend elérést GoPro WiFi mellett |
| Player camera | **iPad** | SIM nélkül, WiFi-n éri a backend-et laborkörnyezetben |
| GoPro controller | **iPhone** | GoPro WiFi AP-re az iPhone csatlakozik |
| `managed_by_device_id` | iPhone instructor device ID | Runtime kapcsolat garancia |
| `begin-cycle` / `end-cycle` target | iPhone | Instructor deep link |
| GoPro deep link-ek | iPhone | `gopro-connect/start/stop/status/media-list` |

**ScenarioContext default:** `iphone_role="instructor"`, `ipad_role="player"` — minden scenario-ban egységes.

---

## 3. Mi működik bizonyítottan

### 3a. 2-eszközös capture (iPad=instructor modell — legacy)

| Milestone | Bizonyíték |
|-----------|-----------|
| smoke: 1-cycle start/stop | PASS — `20260628T093643Z_all` |
| multicycle: 3-cycle start/stop | PASS — `20260628T093643Z_all` |
| Instructor autoPrepare fix | AP-01..04 PASS |
| Polling race condition fix | Applied, PASS után |
| Multi-cycle re-arm | CYC-MC-01..02 + PCO-19..21 PASS |
| Scenario isolation (resetForReuse) | PASS |

**Fontos:** ezek az iPad=instructor modellen futottak. Az iPhone=instructor modellre átállás után ezek a scenario-k NEM lettek újra futtatva.

### 3b. GoPro tricamera capture (iPhone=instructor modell)

| Milestone | Bizonyíték |
|-----------|-----------|
| GoPro device registration | PASS — `20260628T120745Z` |
| GoPro shutter start/stop (HTTP) | PASS — `20260628T120745Z` |
| GoPro confirmDeviceStart/Stop (iOS-driven) | PASS — `20260628T120745Z` |
| All 3 confirmed_start + confirmed_stop | PASS — `20260628T120745Z`, `20260628T123941Z`, `20260628T134349Z` |
| Stop order: GoPro stop BEFORE end-cycle | PASS — `20260628T120745Z` |

**3 sikeres gopro-tricamera-smoke PASS** (12:07, 12:40, 13:44) — de ezek iPad=instructor modellen futottak (a GoPro deep link-ek iPad-re mentek, az iPad a GoPro WiFi-re csatlakozott).

### 3c. iPad → iPhone Multipeer live stream

| Funkció | Státusz |
|---------|--------|
| CameraStreamService Multipeer advertise/browse/connect | Kód kész, build OK |
| CameraFramePublisher JPEG stream ~12 FPS | Kód kész, build OK |
| RemoteCameraView frame display + FPS/latency badge | Kód kész, build OK |
| InstructorDashboardView 3-panel layout | Kód kész, build OK, **fizikailag működik** |
| iPad élőkép megjelenik iPhone dashboardon | **IGEN — screenshot bizonyíték: 9 FPS, live badge, iPad kameraképe látható** |

### 3d. iOS unit tesztek

| Test suite | Count | Status |
|-----------|-------|--------|
| CaptureAuthorityTests (CTL + AP) | 12 | PASS |
| MC1AutomationBridgeTests (AB) | 21 | PASS |
| GoProConnectionStateMachineTests (SM) | 40 | PASS |
| CycleCaptureOrchestratorTests (CYC) | 34 | PASS |
| PlayerCaptureOrchestratorTests (PSO/PCO) | 14 | PASS |
| Python test_device_control | 9 | PASS |
| **Total** | **130** | **PASS** |

---

## 4. Mi NEM működik vagy NEM bizonyított

### 4a. KRITIKUS — blokkoló a proof-hoz

| Probléma | Részletek |
|----------|-----------|
| **GoPro device registration 403** | Az iPhone=instructor modellváltás után a `managed_by_device_id` rossz device-re mutatott (player device ID-vel, instructor token-nel). Legutóbbi fix (variable naming) NEM lett fizikailag tesztelve. |
| **GoPro ready state soha nem ért el a backendre** | Az iPhone `gopro-connect` action elindul, de a `waitAndSignalGoProReady` timeout-ol (45s). A GoPro BLE+WiFi connect flow nem jut el `.ready`-ig, VAGY az `updateDeviceStatus(.ready)` hívás nem sikerül. Console logok nem vizsgáltak. |
| **Artifact collection nincs implementálva** | `xcrun devicectl device copy from` kód megvan a `lib.py`-ban, de a `devicectl device copy from` parancs szintaxisa nem validált (nem biztos, hogy így hívható). Videó és skeleton JSON fizikailag NEM lett begyűjtve egyszer sem. |
| **Skeleton processing nem bizonyított** | `SkeletonProcessor.swift` megvan (Vision framework VNDetectHumanBodyPoseRequest), de fizikailag soha nem futott. |
| **GoPro live preview NEM létezik** | Endpoint konstansok definiálva (`/gopro/camera/stream/start`, UDP:8554), de HERO13 validáció nem történt meg. Nincs `GoProPreviewView`. A dashboard GoPro panelje placeholder ("offline" / "ready" szöveg). |
| **PASS/FAIL félrevezető** | A `gopro-iphone-diagnostics` PASS-t ad csak deep link küldés alapján (nem ellenőrzi a választ). A `gopro-tricamera-smoke` korábbi PASS-ok iPad=instructor modellen futottak (nem az aktuális iPhone=instructor modellen). |

### 4b. KÖZEPES — nem blokkoló de hiányzik

| Probléma | Részletek |
|----------|-----------|
| Dashboard auto-open | `.onChange(of: vm.sessionDeviceId)` kód megvan, de fizikailag nem tesztelve az iPhone=instructor modellen |
| Multipeer stream quality | 9 FPS a screenshot-on, cél 20-30 FPS. JPEG quality 0.3, `.medium` preset. Tuningolható. |
| Console log capture | `log stream --device <udid>` javítás megvan a shell script-ben, de NEM bizonyított, hogy tényleg működik (korábbi futásokban üresek voltak a logok) |
| GoPro WiFi auto-join | `NEHotspotConfiguration` kód megvan, de a WiFi join prompt-ot a felhasználónak kell tap-pelni. Terepi automatizáláshoz ez probléma. |

---

## 5. Konkrét hibák időrendben

| Idő (UTC) | Hiba | Root cause | Fix státusz |
|-----------|------|------------|-------------|
| 07:13 | `target_status` vs `target` API body mismatch | Script küld `target`, backend `target_status`-t vár | **FIXED** |
| 07:43 | Instructor capture `pending` (iPad) | `autoPrepare` kizárta `instructorPrimary`-t | **FIXED** |
| 08:28 | Multicycle join timeout | Polling race: in-flight `getSession` felülírja `.idle` state-et | **FIXED** |
| 08:41 | Multicycle cycle 1 `recording_pending` | `SessionCaptureManager` `.completed` state, `startCapture` guard no-op; PCO `.confirmedStop` nem fogad új cycle-t | **FIXED** |
| 10:09 | `gopro-tricamera-smoke` join timeout | `gopro-status` deep link a join előtt → LobbyView nincs nyitva | **FIXED** |
| 10:22 | GoPro `confirmed_stop` 422 | Stop sorrend: end-cycle ELŐTT GoPro stop kell | **FIXED** |
| 14:22 | iPhone backend preflight 403 | `get_session` player token-nel join előtt → `Not a session participant` | **FIXED** |
| 14:28 | GoPro `confirmed_start` timeout | iOS-driven confirm refactor: iPhone GoPro WiFi-n, de a GoPro connect nem jut `.ready`-ig | **NEM MEGOLDOTT** |
| 14:38 | CoreDeviceError 4016 | iPhone USB instabil | Átmeneti, nem kód hiba |
| 14:41 | GoPro ready timeout (backend poll) | `waitAndSignalGoProReady` 45s timeout: GoPro connect flow nem teljes, VAGY `updateDeviceStatus` 403 | **NEM MEGOLDOTT** |
| 14:44 | GoPro ready timeout (ismétlés) | Ugyanaz | **NEM MEGOLDOTT** |
| 15:59 | GoPro register 403 `Not authorized to manage` | `managed_by_device_id` a player device ID-re mutatott (variable naming hiba a role swap miatt) | **FIX MEGVAN, NEM TESZTELVE** |

---

## 6. Jelenlegi branch és fájlállapot

- **Branch:** `feat/mc1-gopro-tricamera-smoke`
- **HEAD:** `4c8d3632` (main HEAD — nincs commit a branchen)
- **Minden változás UNCOMMITTED**
- **Build:** OK (simulator, utolsó sikeres: 2026-06-28 ~16:30)
- **CI:** NEM futott (uncommitted changes, nincs push)

### Módosított fájlok (23)

| Fájl | Mi változott |
|------|-------------|
| `MultiCameraSessionViewModel.swift` | `authManager` exposed; `resolvedParticipantRole`; `shouldAutoPrepare`; device role from participant role (nem device type) |
| `SessionCaptureManager.swift` | `rearmForNextCycle()`; `resetForReuse()`; `previewSession` expose |
| `CaptureController.swift` | `rearmForNextCycle()` protocol method |
| `CycleCaptureOrchestrator.swift` | `rearmForNextCycle()` before startCapture |
| `PlayerCaptureOrchestrator.swift` | `.confirmedStop` elfogad új cycle-t; `rearmForNextCycle()` |
| `GoProConnectionManager.swift` | `shared` singleton; `GoProRecordingState`; `startRecording()`/`stopRecording()`; `fetchMediaList()`; auto WiFi join (`NEHotspotConfiguration`) |
| `GoProConstants.swift` | Preview stream endpoint konstansok |
| `GoProConnectionDebugView.swift` | Dismiss gomb |
| `MC1AutomationBridge.swift` | GoPro actions (connect/start/stop/status/media-list/http-diag/download); skeleton-process; capture-info |
| `MultiCameraLobbyView.swift` | GoPro automation handlers (iOS-driven confirm); dashboard auto-open; Multipeer stream setup; skeleton/capture-info/download handlers; 3-panel dashboard fullScreenCover |
| `MainHubView.swift` | GoProConnectionManager.shared singleton; fullScreenCover dismiss on session lab open |
| `LFASpecTabView.swift` | GoProConnectionManager.shared |
| `Info.plist` | NSBonjourServices for Multipeer |
| `scenarios.py` | 7 scenario-k; role-agnostic join; iPhone=instructor default; artifact collection code |
| `lib.py` | `register_device`; `confirm_device_start/stop`; `get_server_time_iso`; `copy_from_device`; `extract_*_from_log`; `ScenarioContext` role fields |
| `runner.py` | gopro- prefix exclusion from `all`; `log stream --device` fix |
| `run_mc1_regression.sh` | `log stream --device <udid>` per-device console capture |

### Új fájlok (10)

| Fájl | Funkció |
|------|---------|
| `CameraStreamService.swift` | Multipeer Connectivity: advertise/browse/connect/send/receive frames |
| `CameraFramePublisher.swift` | AVCaptureVideoDataOutput → JPEG → Multipeer send (~12 FPS) |
| `RemoteCameraView.swift` | Fogadott JPEG frame-ek megjelenítése + live/FPS/latency badge |
| `CapturePreviewLayer.swift` | AVCaptureVideoPreviewLayer SwiftUI UIViewRepresentable wrapper |
| `RecordingOverlay.swift` | REC piros pont + elapsed timer |
| `PlayerCaptureView.swift` | Full-screen player preview + REC overlay |
| `InstructorCaptureView.swift` | Korábbi 2-panel instructor view (deprecated by InstructorDashboardView) |
| `InstructorDashboardView.swift` | 3-panel dashboard: local cam + iPad stream + GoPro status |
| `SkeletonProcessor.swift` | Vision VNDetectHumanBodyPoseRequest: video → skeleton JSON |
| `SkeletonFrameMetadata.swift` | Frame metadata struct |

---

## 7. Létező script scenario-k

### `smoke` / `multicycle`
- **Mit csinál:** 2-eszközös (iPhone instructor + iPad player) capture, 1 vagy 3 cycle
- **Mit validál ténylegesen:** Backend confirmed_start/stop mindkét eszközön
- **Utoljára PASS:** `20260628T093643Z` — de iPad=instructor modellen. iPhone=instructor modellre átállva NEM tesztelve.

### `gopro-tricamera-smoke`
- **Mit csinál:** 3-eszközös capture (iPhone+iPad+GoPro), 1 cycle, GoPro start/stop, all 3 confirmed
- **Mit validál:** Backend lifecycle (confirmed_start/stop 3 device-ra)
- **Utoljára PASS:** `20260628T134349Z` — de iPad=instructor modellen (GoPro iPad-ről volt vezérelve)
- **iPhone=instructor modellen:** FAIL — GoPro ready timeout, majd 403 authorization hiba

### `gopro-iphone-diagnostics`
- **Mit csinál:** GoPro HTTP diagnostics deep link-ek küldése iPhone-ra
- **FÉLREVEZETŐ:** PASS-t ad, de csak azt ellenőrzi, hogy a deep link-ek el lettek küldve. A GoPro HTTP válaszok csak console logba kerülnek, amiket senki nem olvas.

### `tricamera-capture-skeleton-proof`
- **Mit csinál:** 3-eszközös capture + skeleton processing + artifact collection
- **Utoljára futott:** `20260628T155902Z` — FAIL 403 (GoPro register authorization)
- **Artifact collection:** Kód megvan, de `devicectl device copy from` szintaxis nem validált

---

## 8. Hálózati modell

```
┌─────────────────────┐
│      iPhone          │
│  (mobilnet: LTE/5G) │──── backend (Vercel staging)
│  (WiFi: GoPro AP)   │──── GoPro HERO13 (10.5.5.9:8080)
│  Multipeer: browser  │──── iPad (Multipeer frame receive)
└─────────────────────┘

┌─────────────────────┐
│       iPad           │
│  (WiFi: router/lab)  │──── backend (Vercel staging)
│  Multipeer: advertise│──── iPhone (Multipeer frame send)
│  NO SIM / NO cellular│
└─────────────────────┘

┌─────────────────────┐
│    GoPro HERO13      │
│  WiFi AP mode        │──── iPhone HTTP control
│  BLE: paired         │──── iPhone BLE commands
│  SD card recording   │
│  Preview stream: ?   │     NOT VALIDATED on HERO13
└─────────────────────┘
```

**Ismert probléma:** iPhone GoPro WiFi-re csatlakozva a mobilnet routing nem garantált. iOS-ben a WiFi elsőbbséget kap a cellular felett, és a GoPro AP-nak nincs internet route-ja. Az iOS *általában* fallback-el cellular-ra, de ez nem 100% megbízható.

**GoPro preview stream:** `/gopro/camera/stream/start` → UDP:8554 — endpoint konstansok definiálva, de HERO13-on soha nem tesztelve. A GoPro dashboard panel jelenleg placeholder.

---

## 9. Kívánt következő implementáció

A proof PASS feltételei:

1. Dashboard auto-open join után ✓ (kód megvan, tesztelni kell)
2. iPhone local preview ✓ (működik)
3. iPad live preview → iPhone dashboard ✓ (működik, 9 FPS, tuning kell)
4. GoPro: legalább bizonyított recording + media evidence
5. Három video artifact fizikailag begyűjtve az artifact mappába
6. Skeleton JSON fizikailag létrejön és begyűjtve
7. PASS/FAIL a tényleges fájlok meglétéből dönt

---

## 10. Őszinte ajánlás

### Mi kell kidobni
- `InstructorCaptureView.swift` — deprecated, `InstructorDashboardView` váltotta le
- `gopro-iphone-diagnostics` scenario jelenlegi formájában — félrevezető PASS, nem validál semmit

### Mi kell megtartani
- **Multipeer live stream** — működik, screenshot bizonyítja, tuningolni kell (FPS, quality)
- **CycleCaptureOrchestrator + PlayerCaptureOrchestrator** — robosztus, 130 unit teszt
- **GoPro HTTP shutter control** — működik (3× PASS bizonyíték iPad=instructor modellen)
- **SkeletonProcessor** — kód kész, logika helyes, csak futtatni kell
- **Backend session/cycle lifecycle** — stabil

### Architektúra-problémák
1. **Variable naming confusion** — `ipad_id`/`iphone_id` vs `instructor_id`/`player_id`. A role swap után a nevek nem feleltek meg a valóságnak. RÉSZBEN javítva, de a legacy scenario-kban még `ipad_id` nevek vannak.
2. **Device role assignment** — A `autoRegisterDevice` korábban device type-ból döntötte a role-t (`iPad → instructorPrimary`). Javítva: participant role alapján dönt. DE: az iOS `autoRegisterDevice` NEM lett tesztelve iPhone=instructor modellen fizikailag.
3. **GoPro ownership model** — A `managed_by_device_id` check a backenden strict: a manager device participant user-jének egyeznie kell a hívó user-rel. Ha a script instructor token-nel regisztrálja a GoPro-t és a `managed_by_device_id` a player device-re mutat, 403-at kap.

### Script bugok
1. **`managed_by_device_id` rossz device-re mutatott** — a legutóbbi fix (instructor_id vs player_id) NEM tesztelve
2. **`devicectl device copy from` szintaxis** — nem validált, lehet, hogy más a parancsformátum
3. **Console log parsing** — `extract_capture_path_from_log` és `extract_skeleton_path_from_log` nem tesztelt

### Backend jogosultsági hiba
- A `register_device` endpoint `managed_by_device_id` ellenőrzése strict: a manager device participant user-jének egyeznie kell a hívóval. Ez helyes viselkedés, a script hibája volt, hogy rossz device ID-t küldött.

### Leggyorsabb út a működő proof-ig
1. **Commit current state** — stabil checkpoint (build OK, 130 teszt PASS)
2. **Futtatni `smoke --scenario all`** iPhone=instructor modellen — validálni, hogy a 2-device flow működik
3. **Fix és futtatni `tricamera-capture-skeleton-proof`** — a `managed_by_device_id` fix tesztelése
4. **`devicectl device copy from` szintaxis validáció** — lehet, hogy `--source`/`--destination` nem jó
5. **Skeleton processing fizikai teszt** — `skeleton-process` deep link küldése, JSON ellenőrzés
6. **GoPro HERO13 preview endpoint tesztelése** — `gopro-http-diag` futtatása és console log olvasása
7. **Multipeer FPS tuning** — JPEG quality / resolution / frame skip

**Becsült munka a működő proof-ig:** 2-4 óra célzott debug + teszt (nem új feature, hanem a meglévő kód fizikai validációja).
