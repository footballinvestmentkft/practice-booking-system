# MC2 Minimális Implementációs Terv — 2 iPhone + GoPro + iPad POC

**Dátum:** 2026-07-12
**Státusz:** ELFOGADVA (2026-07-12, feltételekkel — lásd 6. pont) — implementáció PR-onként külön jóváhagyással, merge kizárólag külön jóváhagyás után.
**Alap:** [MC1_FINAL_TOPOLOGY_GAP_ANALYSIS.md](MC1_FINAL_TOPOLOGY_GAP_ANALYSIS.md) (elfogadva 2026-07-12).
**Cél:** a leggyorsabb út egy működő 2 iPhone + GoPro + iPad POC-hoz — minimális új kód, minimális PR-szám, nulla architekturális átépítés.

> **ARCHITEKTÚRA-DÖNTÉS (user, 2026-07-12 — v1.1 revízió):** az MPC/peer-to-peer réteg
> **teljes kivezetése**. Végleges architektúra: (1) backend-orchestrált session-koordináció,
> (2) minden kamera lokálisan rögzít, (3) post-cycle upload pipeline, (4) audio-alapú fine
> synchronization (backend-óra csak koarse igazításra), (5) iPad = státusz/thumbnail
> dashboard backend-pollinggal — élő videóstream nélkül, (6) GoPro manager = SIM-es
> Player A iPhone. E revízió törölte a multi-peer MPC PR-t (volt PR3), az AWDL/GoPro
> koegzisztencia-probe-ot és a kapcsolódó K1/K2 kockázatokat; az L2/L3 MPC-találatok
> okafogyottá váltak. A 3D rekonstrukciós adatút SOHA nem függött az MPC-től — az csak
> élő monitoring volt.

---

## 0. Rögzített döntések (user, 2026-07-12)

| Eszköz | Szerep | Hálózat |
|---|---|---|
| iPhone #1 (SIM) | Player A (`player_primary`) + **GoPro manager** | backend: cellular; GoPro AP: WiFi (a ma bizonyított minta) |
| iPhone #2 (nincs SIM) | Player B (`player_secondary`) | backend: lab WiFi (mint ma az iPad) |
| GoPro | 3. kamera (`auxiliary_camera`), `managed_by_device_id` = Player A | GoPro AP ← iPhone #1 |
| iPad (WiFi) | Instructor dashboard + session coordinator (`instructor_primary`), **NEM felvevő**; kamerája ebben a fázisban nem használható | backend: lab WiFi (nincs P2P) |

- iPad **minden** panelje: **státusz/thumbnail** (backend polling) — nincs élő videóstream, se MPC, se relay (2026-07-12 architektúra-döntés).
- PR-4B1 scope változatlan; `GoProConnectionManager` / BLE / HTTP réteghez nem nyúlunk.
- Prioritás: (1) non-recording instructor → (2) dual-player → (3) státusz/thumbnail dashboard + MPC-kivezetés → (4) GoPro→Player A + proof → (5) post-cycle upload → MC3/MC4-re halasztva a többi.

---

## 1. Vezérelvek — amit tudatosan NEM csinálunk

| Elkerült munka | Indoklás |
|---|---|
| Új `DeviceRole` / `recording_capable` oszlop / Alembic migration | A required-szabály szerep-alapú átírása 1 sor, a meglévő enum elég |
| **Bármilyen MPC/peer-to-peer munka** (multi-peer refactor, frame-attribúció, session-validate invite, MPC titkosítás) | 2026-07-12 architektúra-döntés: az MPC teljes kivezetésre kerül — a 3D adatút soha nem függött tőle, a monitoring-igényt a státusz/thumbnail dashboard fedi |
| Élő videóstream bármely útvonalon (MPC, UDP relay, backend streaming) | A dashboard státusz/thumbnail-alapú; élőkép egyik panelen sincs |
| Tri-view contract, kalibráció, audio fine-sync implementáció, trianguláció | MC3-A/B/C és MC4 — e terv scope-ján kívül |
| Backend API/séma változás a session/cycle koordinációban | A `player_secondary` auto-kiosztás és a `managed_by_device_id` már működik (a thumbnail- és upload-endpoint az egyetlen új backend-elem, PR3/PR5) |

---

## 2. PR-bontás — 5 PR összesen (v1.1: a volt multi-peer MPC PR3 törölve)

### MC2-PR1 — Instructor non-recording mode (= MC2-A)

**A legkisebb változtatás, ami után az iPad koordinálhat felvétel nélkül.**

| Réteg | Változás | Méret |
|---|---|---|
| Backend | [cycle_service.py:126](../app/services/multicamera/cycle_service.py): `required = DeviceRole(sd.device_role) != AUXILIARY_CAMERA` → `required = role in (PLAYER_PRIMARY, PLAYER_SECONDARY)`. A completion-logika (`get_required_cycle_devices`) változatlanul csak a required halmazt nézi — más sor nem változik | **1 sor** + tesztek |
| iOS | `shouldAutoPrepare(.instructorPrimary)` → `false` ([MultiCameraSessionViewModel.swift:356](../ios/LFAEducationCenter/MultiCamera/MultiCameraSessionViewModel.swift)) | 1 sor + AP tesztek frissítése |
| iOS | `CycleCaptureOrchestrator`: `recordsLocally: Bool` konstruktor-paraméter (default `true`). `false` esetén kihagyja: `startCapture()` (335. sor), `stopCapture()` (188/467/490. sor), és a saját confirm_start/stop hívást. Így a backend-en NEM keletkezik confirm-evidencia fájl nélkül | ~10 sor guard |
| iOS | Begin-cycle UI gate ([MultiCameraLobbyView.swift:533](../ios/LFAEducationCenter/MultiCamera/MultiCameraLobbyView.swift)): a `captureManager.state == .ready` feltétel csak `recordsLocally` esetén kötelező | ~2 sor |
| iOS | `InstructorDashboardView`: a lokális kamera panel `recordsLocally == false` esetén "coordinator" placeholder (nem nyit AVCaptureSession-t — a P0 kamera-kontenció tanulsága). A 3-panelesítés az MC2-PR3-ban jön | ~10 sor guard |
| Harness | `smoke`/`multicycle`/tricamera scenariók assert-frissítése: az instructor confirm_start/stop elvárás törlése; helyette assert, hogy az instructor cycle_device `required == false` és `pending` maradhat | scenario-módosítás |

**Tesztek:** meglévő CYC-O suite zöld marad + új CYC-NR-01..04 (non-recording: nincs startCapture hívás — `FakeCaptureController`-rel ellenőrizve —, cycle completes players-only confirmokkal); backend: cycle required-halmaz unit tesztek.
**Fizikai validáció:** 2-eszközös smoke **iPad=instructor(non-recording) + iPhone#1=player** — ez egyben az első iPad-instructor irányú fizikai futás.

### MC2-PR2 — Dual-player support (= MC2-B)

**Cél: a `player_secondary` útvonal első valós futása. App-kód elvárt változás: 0 sor.**

| Réteg | Változás |
|---|---|
| Harness | `ScenarioContext` ([lib.py:501](../scripts/mc1_regression/lib.py)): + `iphone2_udid: str \| None = None`, + `player2_token: str \| None = None`; runner env: `IPHONE2_UDID`; console capture + preflight (URL scheme, dual→**tri**-console precondition) kiterjesztése a 3. iOS eszközre |
| Harness | Új scenario: `dual-player-smoke` — iPad instructor (non-recording) + 2 player iPhone, **GoPro nélkül**, 1 cycle; assert: a 2. player device `device_role == "player_secondary"` (a backend auto-kiosztás első fizikai bizonyítéka), mindkét player confirmed_start/stop |
| Backend/iOS | **0 sor** — ha mégis elakad, az hibajegy, nem scope-bővítés |

**Előfeltétel (nem kód):** második player user + token a staging backendben; iPhone #2 provisioning (Debug build, `lfa-mc1://` scheme, USB trust).

### MC2-PR3 — iPad státusz/thumbnail dashboard + MPC-kivezetés (v1.1, a volt multi-peer MPC PR-t váltja)

**A dashboard élő videópanelek helyett backend-pollingos státusz- és thumbnail-panelekre áll át; az MPC réteg kódja kivezetésre kerül.**

| Réteg | Változás |
|---|---|
| iOS | `InstructorDashboardView`: panelenként (Player A, Player B, GoPro) backend-polling alapú státusz — device_status, cycle recording_status, utolsó heartbeat kora; a `RemoteCameraView` élő-frame útvonala helyett thumbnail-megjelenítés. A meglévő GoPro státusz-panel mintája terjed ki mindenre |
| iOS | Pre-cycle framing-ellenőrzés: a player Begin Cycle előtt EGY thumbnail-t (kisfelbontású JPEG) tölt fel a backendre; az iPad ezt pollozza és mutatja. Nem stream — cycle-onként legfeljebb néhány kép |
| iOS | **MPC-kivezetés**: `CameraStreamService` és fogyasztói (`RemoteCameraView` élő útvonal, `LivePoseOverlayProcessor` MPC-forrás, `CameraFramePublisher` P2P-ág) eltávolítása/deaktiválása. A `pose_overlay_diag.json` élő-panel kulcsai és a rájuk épülő preflight CHECK kivezetése a writerrel EGY PR-ban (P0 kulcs-kontraktus tanulság) |
| Backend | Kis thumbnail-endpoint: feltöltés a player tokennel + lekérés az instructor tokennel (per session_device, felülíró — nem galéria). Az egyetlen új backend-elem ebben a PR-ban |
| Harness | Dashboard-evidencia átállítása: élő frame-forgalom gate-ek helyett státusz-frissesség + thumbnail-megérkezés assert |

**Tesztek:** thumbnail endpoint unit tesztek (ownership: player tölt, instructor olvas); dashboard státusz-mapping unit tesztek; a törölt MPC-tesztek kivezetése ugyanebben a PR-ban.
**Fizikai validáció:** dashboard vizuális futás — 2 player + GoPro panel státusszal és thumbnail-lel, élő stream nélkül.

### MC2-PR4 — GoPro ownership átirányítás + végleges topológia proof (= MC2-D + MC2-E, összevonva)

**Mindkettő dominánsan harness/doksi — egy PR-ban a legolcsóbb.** (v1.1: az AWDL/MPC koegzisztencia-probe TÖRÖLVE — MPC híján okafogyott; a Player A cellular↔GoPro-AP kettős elérése a meglévő gopro-scenariókkal már bizonyított.)

| Réteg | Változás |
|---|---|
| Harness | GoPro deep link-ek célja: instructor → **`iphone_udid` (Player A)**; `register_device`: `managed_by_device_id` = Player A device id (a backend ownership-check ezt már támogatja — a 2026-06-28-i "iPad=instructor modellen GoPro iPad-ről vezérelve" PASS-ok pont ezt a mintát bizonyították, csak fordított szereposztásban) |
| Harness | Új scenario: `final-topology-proof` — 4 device (TOPO-G1), cycle 3 felvevő confirmmal, instructor nincs a required-ben (TOPO-G2), dashboard státusz/thumbnail evidencia (TOPO-G3/G4 v1.1 — lásd gap analysis), started_at delta (TOPO-G5), 3 videó + 3 skeleton JSON begyűjtve (TOPO-G6), iPaden nincs capture fájl (TOPO-G7), silent-partial tiltás (TOPO-G8). A meglévő `tricamera-capture-skeleton-proof` váz újrafelhasználásával — nem nulláról írjuk |
| iOS | Elvárt: **0 sor.** Verifikálandó (a PR első commitja előtt): a `[GOPRO-AUTO]` automation handlerek nem `isController`-gateltek ([MultiCameraLobbyView.swift:133](../ios/LFAEducationCenter/MultiCamera/MultiCameraLobbyView.swift) — a dispatch nem a 402. sori `isController` blokkban van); ha mégis gate mögött vannak, a gate feloldása player-re ~2 sor |
| Doksi | RC checklist v1.2 delta: E4/E5 szerep-átírás (iPad=instructor, iPhone#1=player_primary+GoPro manager, iPhone#2=player_secondary), E10 dual→tri-console, TOPO-G gate-ek felvétele a D7 critical_ok listába |

**Fizikai validáció:** ez maga az MC2-E záró futás — a 2 iPhone + GoPro + iPad POC proof.

### MC2-PR5 — Post-cycle upload pipeline (új, 2026-07-12 architektúra-döntés)

**A rögzített videók és metaadatok cycle utáni feltöltése a backendre — a 3D pipeline (MC3/MC4) bemenete. A final-topology-proof (PR4) ettől még nem függ: ott az evidencia USB-n is lehúzható; az upload a produkciós/MC3 út.**

| Réteg | Változás |
|---|---|
| Backend | Capture-artifact upload endpoint: per cycle_device videó + capture-metadata JSON fogadása (player token, ownership-check), tárolás session/cycle/device szerint címezve |
| iOS | Confirm_stop után háttér-upload a lokális fájlból (retry-val); GoPro fájl: Player A húzza le a GoPro HTTP media API-ról és továbbítja, VAGY USB-s utólagos import — a POC-ban a kisebb kockázatú út választandó, döntés a PR elején |
| Harness | Assert: cycle completion után az upload megérkezik, mérete > 0, metadata konzisztens a lokális diaggal |

**Tesztek:** upload endpoint unit tesztek (ownership, duplikátum, méret-limit); iOS upload-retry unit teszt.
**Fizikai validáció:** 1 cycle → mindhárom felvevő artifactja a backenden.

---

## 3. Sorrend és függőségek

```
[előfeltétel]  Futó HERO12 smoke PASS a fix/mc1-p0-player-camera-session branchen
      │
MC2-PR1 (non-recording instructor)          ← fizikai: iPad-instructor 2-device smoke
      │
MC2-PR2 (dual-player, harness-only)         ← fizikai: dual-player-smoke, player_secondary első futás
      │
MC2-PR3 (státusz/thumbnail dashboard,       ← fizikai: dashboard vizuális futás, élő stream nélkül
         MPC-kivezetés)
      │
MC2-PR4 (GoPro→Player A + proof)            ← fizikai: final-topology-proof = a POC cél
      │
MC2-PR5 (post-cycle upload)                 ← fizikai: 3 felvevő artifactja a backenden
```

A sorrend szándékosan kockázat-lépcső: minden PR pontosan egy új ismeretlent visz fizikai tesztre (szerep-inverzió → 2. eszköz → dashboard-átállás → GoPro-átirányítás → upload), így egy FAIL egyértelműen lokalizálható. A PR5 a PR4 után is indulhat párhuzamosan az MC3-előkészítéssel — a final-topology-proof USB-s evidencia-pull-lal is teljes értékű.

## 4. Kockázatok (maradék; v1.1: K1/K2 törölve — MPC híján okafogyottak)

| # | Kockázat | Kezelés |
|---|---|---|
| K3 | Player B (SIM nélkül) backend-elérése lab WiFi-n | Azonos a mai iPad-player mintával — bizonyított útvonal |
| K4 | A scenario assert-frissítések (PR1) a régi 2-device smoke-ot érintik | A smoke/multicycle a PR1 után is fut CI-ben és fizikailag — a non-recording instructor az ÚJ elvárás, nem opció |
| K5 | Thumbnail-feltöltés terhelése/ütemezése (PR3) | Cycle-onként legfeljebb néhány kisfelbontású kép, felülíró tárolás — nem stream, nem galéria; méret-limit az endpointon |
| K6 | Upload-pipeline tárolás/méret (PR5) | POC-ban méret-limit + lokális megőrzés feltöltés után is; storage-stratégia (retention, formátum) MC3 előtt döntendő |
| K7 | Instructor a cycle alatt élő kép nélkül dolgozik | Pre-cycle thumbnail framing-ellenőrzés (PR3) + cycle utáni azonnali evidencia; tudatosan vállalt trade-off a 2026-07-12 döntésben |

## 5. Explicit módon elhalasztva (MC3/MC4) vagy okafogyott

Elhalasztva: tri-view `Skeleton3DFrame` contract (MC3-C), audio-alapú fine sync 3 forrásra (MC3-B, a volt PR-4B3; cél ≤5–10 ms, a backend-óra csak koarse igazítás), per-camera kalibráció (MC3-A), trianguláció (MC4), 3D skeleton viewer. **Ezek csak a `final-topology-proof` PASS után indulhatnak.**

Okafogyott (2026-07-12 MPC-kivezetés): MPC titkosítás (volt L3 production blocker), MPC session-validate invite (volt L2), GoPro élőkép az iPaden, multi-peer stream-attribúció.

---

## 6. Jóváhagyási feltételek és kötelező gate-ek (user, 2026-07-12)

### 6.1 Előfeltétel (minden MC2 munka előtt)

Az MC2-PR1 CSAK akkor indulhat, ha a futó HERO12 P0 smoke: (a) fizikai PASS, (b) teljes CI zöld, (c) nincs unresolved hardware/USB blocker, (d) a PR-4B1 scope lezárható. Indulás külön user-jóváhagyással, külön branchen.

### 6.2 MC2-PR1 kötelező gate-ek

- Instructor NEM required recorder; az iPad nem készít lokális capture fájlt és nem küld saját capture confirmationt (a `recordsLocally=false` ág a confirmot is kihagyja — **nincs silent fake confirmation**).
- Bizonyítandó: az iPaden nincs aktív capture-célú AVCaptureSession; nincs iPad media file.
- Cycle completion kizárólag a felvevő eszközök evidenciája alapján. *Fázis-pontosítás:* a "3 tényleges felvevővel lezáruló cycle" teljes gate-je fizikailag csak PR4-ben bizonyítható (a 2. player PR2-ben, a GoPro-átirányítás PR4-ben érkezik); PR1 fizikai proofja: 2-eszközös futás, ahol a required-halmaz = players-only és az instructor evidencia nélkül marad. A GoPro backend-szinten nem required (D4 döntés) — a 3-felvevős teljesség gate-szinten kényszerített (TOPO-G2/G8, PR4).

### 6.3 MC2-PR2 kötelező gate-ek

- Először bizonyítani, hogy a dual-player útvonal MEGLÉVŐ app-kóddal működik. **Ha app-kód-módosítás kellene: STOP + riport — nem automatikus scope-bővítés.**
- Külön staging user + token Player B-hez; külön `camera_id` és `capture_id` per player; `player_primary`/`player_secondary` helyes kiosztás; két iPhone egy sessionben; nincs role collision; nincs token/device ownership keveredés.

### 6.4 MC2-PR3 kötelező scope (v1.1 — státusz/thumbnail dashboard, nem csak happy path)

Panelenkénti determinisztikus device-azonosítás (session_device_id alapján, nem név alapján); státusz-frissesség jelzése (stale/disconnected állapot explicit megjelenítése, ha a polling elakad); thumbnail ownership-check (player tölt fel, instructor olvas, más session-ből nem látható); az iPad továbbra sem indít lokális kamerát; a teljes MPC-kód és a rá épülő diag/preflight kulcsok EGY PR-ban kerülnek ki a writerrel (kulcs-kontraktus szabály). Disconnect-viselkedés: player kiesésekor a panel explicit "disconnected"-et mutat, a session/cycle nem omlik össze (TOPO-G10 előkép).

### 6.5 MC2-PR4 kötelező előfeltétel (v1.1 — a koegzisztencia-probe törölve)

Az AWDL/MPC koegzisztencia-probe okafogyott (nincs MPC). Megmaradó hálózati előfeltétel-ellenőrzés a proof futás elején: Player A mobiladat aktív + GoPro AP-n + backend session coordination működik (a meglévő gopro-scenariókkal már bizonyított minta), Player B (SIM nélkül) stabil a lab WiFi-n. **Ha a GoPro AP megtöri a backend-kommunikációt: STOP, BLOCKED riport + alternatív hálózati terv.**

### 6.6 Final-topology-proof — bizonyítandók

Fizikai eszközpark: iPhone 12 Pro Max + iPhone 12 Pro + iPad (Wi-Fi) + GoPro HERO12.

TOPO-G1..G8 (gap analysis 6. pont) + kiegészítés:

| Gate | Metrika |
|---|---|
| TOPO-G10 | **Graceful degradation** (v1.1, státusz-alapú): egy player státusz/heartbeat kiesésekor a dashboard explicit disconnected állapotot mutat, a session/cycle nem omlik össze, nincs crash; a polling helyreállása után a panel visszaáll; a kiesés SOHA nem eredményez silent partial completion-t (kapcsolódik: TOPO-G8) |

Továbbá: közös `session_id`, per-camera `capture_id` + `camera_id`, teljes started/stopped evidence chain mindhárom felvevőre.

### 6.7 Riportálási kötelezettség

Minden PR után: külön fizikai proof + teljes CI + **MERGE-READY / BLOCKED** riport. Merge kizárólag külön user-jóváhagyás után.

---

**Összesen: 5 PR (v1.1). PR1 = 1 backend-sor + iOS guardok (folyamatban); PR2 = harness-only; PR3 = dashboard-átállás + MPC-kivezetés + thumbnail endpoint; PR4 = dominánsan harness; PR5 = upload pipeline (backend + iOS háttér-upload). Implementációt PR-onként külön jóváhagyás után kezdünk; merge kizárólag külön jóváhagyással.**
