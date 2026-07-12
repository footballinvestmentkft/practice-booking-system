# MC1 Végleges Fizikai Topológia — Gap Analysis és Frissített Roadmap

**Dátum:** 2026-07-12
**Státusz:** TERVEZÉSI DOKUMENTUM — implementáció külön jóváhagyással.
**Trigger:** Végleges fizikai topológia rögzítése (lásd 1. pont). Ez a dokumentum
felülírja a [MULTICAMERA_3D_IOS_ROADMAP.md](MULTICAMERA_3D_IOS_ROADMAP.md) Phase 2+
fázisbontását és lezárja az [AN3B_PR4B_GOPRO_CAPTURE_POC_PLAN.md](AN3B_PR4B_GOPRO_CAPTURE_POC_PLAN.md)
PR-4B2/4B3/4B4 bontásának rescope-ját. **A PR-4B1 connection scope visszamenőleg NEM bővül.**

> **v1.1 REVÍZIÓ — ARCHITEKTÚRA-DÖNTÉS (user, 2026-07-12):** az MPC/peer-to-peer réteg
> **teljes kivezetése**. Végleges architektúra: backend-orchestrált session, lokális
> rögzítés minden kamerán, post-cycle upload, audio-alapú fine sync, iPad =
> státusz/thumbnail dashboard (backend-polling, élő stream nélkül), GoPro manager =
> SIM-es Player A. Következmények e dokumentumban: G-4 okafogyott (helyette
> dashboard-átállás), MC2-C fázis átírva, TOPO-G3/G4 átfogalmazva, D2 véglegesítve,
> D3 és R1 törölve, R2 egyszerűsödött. A PR-szintű bontást a
> [MC2_MINIMAL_IMPLEMENTATION_PLAN.md](MC2_MINIMAL_IMPLEMENTATION_PLAN.md) v1.1 tartalmazza.

---

## 1. Végleges topológia (követelmény)

| Eszköz | Szerep | Kamera részt vesz? | Backend `device_role` cél |
|---|---|---|---|
| iPhone 12 Pro (A) | Player kamera #1 | ✅ | `player_primary` |
| iPhone 12 Pro Max (B) | Player kamera #2 | ✅ | `player_secondary` |
| GoPro (HERO12*) | Kiegészítő 3. kameranézet | ✅ | `auxiliary_camera` |
| iPad | Instruktori vezérlő + megjelenítő | ❌ **NEM** | `instructor_primary` (nem-felvevő) |

- A három kameraforrás **egy közös MC1 sessionben** és közös cycle-ban fut.
- Az iPaden a két iPhone és a GoPro **státusza + pre-cycle thumbnail** jelenik meg backend-pollinggal (v1.1 — élő videóstream nincs).
- A későbbi 3D skeleton rekonstrukció a három **időben szinkronizált és kalibrált** nézetből készül.

\* *Elnevezési higiénia: a kódbázis vegyesen hivatkozik HERO12-re
([GoProConstants.swift:8](../ios/LFAEducationCenter/MultiCamera/GoProConstants.swift)) és HERO13-ra
(handoff report, `scenarios.py` device_name stringek). A fizikai eszköz típusát a következő
futás jegyzőkönyvében egyértelműen rögzíteni kell; a scenario `device_name` stringeket ehhez
kell igazítani (kozmetikai, nem blokkoló).*

---

## 2. Mit támogat MÁR a jelenlegi rendszer (kód-referenciákkal)

### 2a. Backend — nagyrészt kész

| Képesség | Kód | Státusz |
|---|---|---|
| 4 device-role, köztük `player_secondary` | `app/models/multicamera_session.py:33-37` (`DeviceRole` enum) | ✅ KÉSZ |
| Második player automatikus szerep-kiosztás (player → `player_primary`, ha már van → `player_secondary`) | `app/services/multicamera/session_service.py:284-311` | ✅ KÉSZ — de **soha nem futott** sem scenarióban, sem fizikai teszten |
| N-eszközös cycle lifecycle (minden nem-removed device snapshotolva a cycle-ba) | `app/services/multicamera/cycle_service.py` `create_cycle` | ✅ KÉSZ — 3 device-szal fizikailag bizonyított (2026-07-04) |
| GoPro registration + `managed_by_device_id` ownership + confirm start/stop | `device_service.py` + scenariók | ✅ KÉSZ, fizikailag bizonyított |
| **Required-szabály**: `required = True` minden szerepre, kivéve `auxiliary_camera` | `cycle_service.py:82` | ❌ **BLOKKOLÓ GAP**: az `instructor_primary` iPad kötelező felvevő lenne — az új topológiában az iPad NEM vesz fel |

### 2b. iOS — a váz megvan, a topológia-specifikus részek hiányoznak

| Képesség | Kód | Státusz |
|---|---|---|
| `playerSecondary` device role + PCO attach allow-list engedi | `MultiCameraSessionModels.swift:29`, `MultiCameraSessionViewModel.swift:368-377` | ✅ enum-szinten kész, fizikailag nem tesztelt |
| Controller-feloldás participant-role alapú (nem device-type) → iPad instruktorként elvben működik | `MultiCameraSessionViewModel.swift` `resolveIsController` + `resolvedParticipantRole` | ✅ elvben; ❌ iPad=instructor irányban fizikailag nem validált (a jelenlegi futások iPhone=instructor modellen mennek) |
| `shouldAutoPrepare`: instructor is capture-t készít | `MultiCameraSessionViewModel.swift:356-363` | ❌ GAP: nincs "megjelenítő-koordinátor kamera nélkül" mód |
| MPC live stream player → controller | `CameraStreamService.swift` | ⚠️ v1.1: **KIVEZETENDŐ** (MC2-PR3) — az MPC-réteg a 2026-07-12 döntés szerint teljes egészében megszűnik; a korábbi single-peer gap okafogyott |
| Instructor dashboard | `InstructorDashboardView.swift` | ❌ GAP (v1.1 átfogalmazva): a cél 3 **státusz/thumbnail panel** (player A, player B, GoPro) backend-pollinggal, lokális panel és élő stream NÉLKÜL |
| Közös óra-alap: szerveridő-offset sync (NTP-szerű RTT-mintavétel) | `ClockSyncService.swift` | ✅ software-szintű közös timestamp (started_at delta gate: RC checklist F8) |
| Per-kamera 2D skeleton (Vision) | `SkeletonProcessor.swift` | ✅ kód kész, fizikai proof folyamatban |

### 2c. Contract / séma

| Képesség | Kód | Státusz |
|---|---|---|
| Kalibrációs DTO-k: `IntrinsicCalibrationDTO`, `StereoCalibrationDTO` | `app/schemas/skeleton_3d.py:150,174` | ✅ séma létezik; ❌ **0 sor** capture/kalibráló kód (se iOS, se OpenCV pipeline) |
| `calibration_json` oszlop a sessionön | `app/models/multicamera_session.py:131` | ✅ tárolóhely van, író nincs |
| `Skeleton3DFrame` | `app/schemas/skeleton_3d.py:126-148` | ❌ GAP: 1 `camera_id`/frame, `coordinate_system` fixen `camera_a_origin_rh_meters` — **2-kamerás párra** tervezve, tri-view bundle nincs |

### 2d. Regression harness

| Képesség | Kód | Státusz |
|---|---|---|
| `ScenarioContext` | `scripts/mc1_regression/lib.py:501-511` | ❌ GAP: pontosan **2 iOS UDID** (`ipad_udid`, `iphone_udid`) + **2 token** (instructor, player). Harmadik iOS eszköz és második player token nem létezik |
| Scenariók szerepmodellje | `scenarios.py` (`iphone_role="instructor"`, `ipad_role="player"` default) | ❌ a végleges topológia ennek **inverze** (iPad=instructor) + 1 extra player |

---

## 3. Mit validál a futó fizikai HERO12 smoke teszt a végleges topológiából

A jelenlegi branch (`fix/mc1-p0-player-camera-session`) fizikai futásai a
`gopro-tricamera-smoke` / `tricamera-capture-skeleton-proof` scenariót futtatják
**iPhone=instructor(+kamera) + iPad=player(+kamera) + GoPro** topológián. A legutóbbi
futás (`20260704T115717Z`) minden capture-lifecycle stepet PASS-olt (gopro ready, 3×
confirmed_start/stop, timestamp sync report); a FAIL a player-oldali evidencia-pull USB
hibáján (CoreDeviceError 4016) történt — erre irányulnak a branch P0 fixei.

| Végleges-topológia elem | A futó smoke validálja? | Megjegyzés |
|---|---|---|
| Közös session + cycle 3 kameraforrással (backend lifecycle) | ✅ IGEN | Strukturálisan azonos: 3 device, 1 cycle, 3× confirmed_start/stop |
| GoPro vezérlés: registration, ownership, ready, shutter, media evidence, preview probe | ✅ IGEN | Az `auxiliary_camera` láb 1:1 átvihető |
| Player capture-session ownership P0 fix (2×AVCaptureSession kontenció) | ✅ IGEN | A player-láb viselkedése mindkét leendő player iPhone-ra átvihető |
| 1 MPC live stream → controller dashboard | ✅ IGEN (1 forrásra) | 2 párhuzamos streamet NEM bizonyít |
| Közös timestamp-alap (ClockSync + started_at delta) | ✅ IGEN | Software-szintű; frame-szintűt nem |
| Per-kamera 2D skeleton + artifact/evidencia-lánc | ✅ IGEN | |
| **iPad mint master coordinator** | ❌ NEM | A controller iPhone |
| **Nem-felvevő koordinátor** | ❌ NEM | Az instructor eszköz kamerával, kötelező felvevőként vesz részt |
| **Két külön player iPhone** | ❌ NEM | 1 player van; `player_secondary` útvonal még soha nem futott |
| ~~2 párhuzamos MPC stream az iPaden~~ *(v1.1: okafogyott — MPC kivezetve)* | — | A dashboard státusz/thumbnail-alapú lesz |
| **Frame-szintű sync, kalibráció, tri-view contract** | ❌ NEM | Nem is scope-ja a smoke-nak |

**Következtetés:** a futó smoke a végleges topológia **közös építőelemeit** validálja
(session/cycle lifecycle, GoPro-láb, player-láb, evidencia-lánc), a
**topológia-specifikus** elemeket (szerep-inverzió, 2. player, státusz/thumbnail
dashboard, nem-felvevő koordinátor) nem. Ezért a smoke PASS **szükséges, de nem elégséges** előfeltétele az
átállásnak — értéke változatlan, futtatása nem függ ettől a gap analysistől.

---

## 4. Gap-lista tételesen

| # | Elem | Státusz | Hiányzó munka |
|---|---|---|---|
| G-1 | iPad mint master coordinator | RÉSZBEN | Szerep-feloldás eszközfüggetlen (✅), de: (a) instructor kötelező felvevő (`cycle_service.py:82` + `shouldAutoPrepare`), (b) dashboard lokális-kamera panelre épít, (c) scenariók `iphone_role="instructor"` defaultúak, (d) **GoPro ownership döntés** — lásd D1 |
| G-2 | Két player iPhone regisztrációja | RÉSZBEN | Backend auto-kiosztás + iOS enum kész; hiányzik: 2. player user/token, harness 3. UDID, `player_secondary` első valós futása, dedikált unit/E2E tesztek |
| G-3 | Három kamera közös sessionje | NAGYRÉSZT KÉSZ | 3 felvevő device egy cycle-ban bizonyított; a hiány a **4. (nem-felvevő) device** viselkedése — G-1(a) |
| G-4 | ~~Több stream megjelenítése iPaden~~ → **Státusz/thumbnail dashboard (v1.1)** | ÁTFOGALMAZVA | Az MPC-kivezetés után: dashboard-panelek backend-pollingos státusszal + pre-cycle thumbnail-lel; `CameraStreamService` és fogyasztóinak kivezetése; thumbnail endpoint (MC2-PR3 v1.1) |
| G-5 | Közös timestamp és frame-szinkron | RÉSZBEN | ClockSync + F8 gate megvan (~100 ms nagyságrend). Frame-szintű: audio-clap cross-correlation (az eredeti PR-4B3 `AudioSyncEngine`/`FrameTimeMatcher` — **0 sor implementálva**), GoPro GPMF telemetria parsing szintén nincs |
| G-6 | Per-camera calibration | CSAK SÉMA | `IntrinsicCalibrationDTO`/`StereoCalibrationDTO` létezik; nincs kalibrációs capture flow (checkerboard), nincs OpenCV pipeline, nincs `calibration_json` író |
| G-7 | Háromnézetes 3D skeleton input contract | HIÁNYZIK | `Skeleton3DFrame` 2-kamerás pár feltevésű (`camera_a_origin_rh_meters`); kell: synchronized multi-view frame-bundle (3 camera_id + közös timestamp + sync_quality), kalibrációs gráf (3 kamera → 2-3 pairwise `StereoCalibrationDTO`), `coordinate_system` általánosítás |

---

## 5. PR-4B2/4B3 roadmap módosítás — IGEN, szükséges

| Eredeti PR | Eredeti scope | Döntés |
|---|---|---|
| **PR-4B1** | GoPro BLE/WiFi connection + state machine | **VÁLTOZATLAN, visszamenőleg nem bővül.** A GoPro-lábat a jelenlegi smoke validálja; a 40 SM teszt + fizikai bizonyíték él |
| **PR-4B2** | Dual capture session + iPhone recording | **SUPERSEDED** az MC1 ORCH-sorozat által (`SessionCaptureManager`, `CycleCaptureOrchestrator`, `PlayerCaptureOrchestrator`, backend cycle lifecycle) — lezárandó "superseded by MC1 ORCH-2B..5" megjegyzéssel, külön implementáció nem kell |
| **PR-4B3** | GoPro media retrieval + offline audio sync | **RESCOPE**: a media retrieval részben kész (media/list + download a scenariókban); az audio sync (AudioSyncEngine, FrameTimeMatcher, SYNC/DROP tesztsuite) él, de **2 helyett 3 forrásra** kell tervezni → átkerül **MC3-B**-be |
| **PR-4B4** | Physical benchmark + gate report | **KIVÁLTVA** az RC checklisttel (`MC1_TRICAMERA_RC_CHECKLIST.md`) + regression harness-szel — külön PR nem kell |

### Frissített fázissor (javaslat — mindegyik külön jóváhagyással indul)

```
[FUT]  HERO12 smoke PASS a P0 branch-en  (előfeltétele mindennek)
   │
MC2 — Topológia-átállás (2 iPhone player + GoPro + iPad coordinator)
   ├─ MC2-A  Nem-felvevő koordinátor mód
   │         backend: required-szabály szereptől/recording_capable flagtől függjön
   │         (cycle_service.py:82); iOS: shouldAutoPrepare display-only ág,
   │         dashboard lokális panel nélkül; RC checklist E4 átírás (iPad=instructor)
   ├─ MC2-B  Dual-player regisztráció
   │         2. player user/token; harness: ScenarioContext 3 iOS UDID + 2 player token;
   │         player_secondary első fizikai futása; új scenario: dual-player-smoke
   ├─ MC2-C  Státusz/thumbnail dashboard + MPC-kivezetés  (v1.1 — a volt N-peer
   │         MPC fázist váltja): panelenként backend-pollingos státusz + pre-cycle
   │         thumbnail; CameraStreamService és fogyasztóinak kivezetése a diag/
   │         preflight kulcsokkal együtt; thumbnail endpoint (egyetlen új backend-elem)
   ├─ MC2-D  GoPro ownership az iPad-instructor modellben  (D1/D2 döntések;
   │         D3 v1.1-ben törölve)
   ├─ MC2-E  Teljes végleges-topológia fizikai smoke  (TOPO-G gate-ek, lásd 6.)
   └─ MC2-F  Post-cycle upload pipeline  (v1.1 új: videó+metadata upload a backendre
   │         cycle után — az MC3/MC4 bemenete; a proof USB-pull-lal is teljes értékű)
   │
MC3 — Szinkron + kalibráció
   ├─ MC3-A  Kalibrációs flow: per-camera intrinsic + pairwise extrinsic
   │         (checkerboard felvétel iOS-en, offline OpenCV, calibration_json upload)
   ├─ MC3-B  Frame-szintű sync 3 forrásra (audio clap cross-correlation —
   │         az eredeti PR-4B3 öröksége; SY-G1..G5 gate-ek megtartva)
   └─ MC3-C  Tri-view skeleton input contract (schema PR: multi-view bundle,
   │         kalibrációs gráf, coordinate_system általánosítás)
   │
MC4 — Trianguláció + 3D viewer  (a régi roadmap Phase 3 megfelelője, változatlan)
```

---

## 6. Új acceptance gate-ek a 2 iPhone + GoPro + iPad fizikai futáshoz (MC2-E)

A meglévő RC checklist (A–M szekciók) érvényben marad; az alábbi TOPO-gate-ek
**kiegészítik**, nem helyettesítik.

| Gate | Metrika | Target |
|---|---|---|
| TOPO-G1 | Session device-összetétel: pontosan 4 nem-removed device, szerepek = `instructor_primary` (iPad) + `player_primary` + `player_secondary` + `auxiliary_camera`; mindegyik a helyes fizikai eszközhöz rendelve (session dump + UDID mapping) | 4/4 egyezik |
| TOPO-G2 | Cycle required-halmaz: az instructor device NINCS a required felvevők közt; a cycle 3 felvevő confirmed_start + confirmed_stop-pal zárul | 10/10 cycle |
| TOPO-G3 *(v1.1)* | iPad dashboard: 3 **státusz/thumbnail panel** (player A, player B, GoPro), panelenként friss backend-státusz (nem stale) ÉS megérkezett pre-cycle thumbnail, session_device_id-hez kötött megjelenítéssel | 3/3 panel |
| TOPO-G4 *(v1.1)* | Panel-attribúció: minden panel a HELYES session_device státuszát/thumbnail-jét mutatja (nincs kereszt-hozzárendelés); player kiesésekor a panel explicit disconnected állapotot jelez | 0 kereszt-hozzárendelés |
| TOPO-G5 | Timestamp sync: a 3 felvevő `started_at` max deltája ≤ előre deklarált tűrés (software-szint, javaslat: ≤ 150 ms; frame-szintű < 33 ms cél MC3-B után) | delta ≤ tűrés |
| TOPO-G6 | Artifact-teljesség: 3 videófájl (2 iOS + 1 GoPro letöltés) + 3 per-kamera skeleton JSON fizikailag begyűjtve | 3+3 fájl |
| TOPO-G7 | iPad nem vesz fel: az iPad-en NEM jön létre capture fájl, és a cycle alatt nincs aktív capture-célú AVCaptureSession (a P0 kamera-kontenció tanulsága — a dashboard nem nyithat kamerát) | 0 fájl, 0 session |
| TOPO-G8 | Silent partial: nincs olyan cycle, amely csendben 3-nál kevesebb felvevővel zárul PASS-ként | 0 |
| TOPO-G9 *(MC3 után)* | Kalibrációs artifact: per-camera intrinsic + ≥ 2 pairwise extrinsic, preset-egyezés validálva (`validate_preset_match`) | 3 intrinsic + 2 extrinsic |

---

## 7. Döntési pontok és kockázatok

| # | Döntés/kockázat | Részletek | Ajánlás |
|---|---|---|---|
| D1 | **Ki vezérli a GoPro-t?** Az iPad (SIM nélkül) nem tud egyszerre a GoPro AP-n ÉS a backenden lenni | A jelenlegi modellben a GoPro manager = instructor iPhone (cellular + GoPro WiFi). iPad-instructor mellett ez nem vihető át 1:1 | GoPro manager = **player iPhone A** (cellular backend + GoPro AP WiFi — a ma bizonyított minta); a backend `managed_by_device_id` ezt már támogatja |
| D2 *(v1.1: VÉGLEGES)* | GoPro megjelenítése az iPaden | A UDP preview csak a GoPro AP-ra csatlakozott eszközön (player A) érhető el | **Státusz/thumbnail** GoPro panel az iPaden (backend-polling) + felvétel-evidencia — a 2026-07-12 döntéssel véglegesítve, élőkép-relay nem készül |
| ~~D3~~ *(v1.1: TÖRÖLVE)* | ~~MPC (AWDL) koexisztencia~~ | MPC híján okafogyott; a Player A cellular↔GoPro-AP kettős elérése a meglévő gopro-scenariókkal bizonyított | — |
| D4 | GoPro legyen-e required a cycle-ban? | Ma `auxiliary_camera` nem required — 3D-hez viszont mindhárom nézet kell | Tri-view proof futásnál gate-szinten kényszerítjük (TOPO-G2/G8), a backend required-szabályt nem változtatjuk meg emiatt |
| ~~R1~~ *(v1.1: TÖRÖLVE)* | ~~L2/L3 (MPC session-validáció + titkosítatlanság)~~ | MPC-kivezetéssel okafogyott — a volt L3 production blocker megszűnik | — |
| R2 *(v1.1: egyszerűsödött)* | iPhone 12 Pro/Pro Max teljesítmény | 2 player eszközön egyszerre: rögzítés + (player A-n) GoPro vezérlés — MPC JPEG stream már nincs | FPS/hőmérséklet mérés a MC2-E futásban |
| R3 *(v1.1: új)* | Instructor a cycle alatt élő kép nélkül | Beállítási hiba (framing, takarás) csak cycle után derül ki | Pre-cycle thumbnail framing-ellenőrzés (MC2-C) — tudatosan vállalt trade-off |

---

## 8. Összefoglaló döntési tábla

| Kérdés | Válasz |
|---|---|
| A jelenlegi rendszer támogatja a topológiát? | Backend: ~80% (required-szabály a fő gap). iOS: váz kész, topológia-specifikus rétegek (státusz/thumbnail dashboard, nem-felvevő mód) hiányoznak. Harness: 2-eszköz limit |
| v1.1 architektúra-delta? | MPC/P2P teljes kivezetés; dashboard = státusz/thumbnail; + post-cycle upload fázis (MC2-F); D3/R1 törölve; TOPO-G3/G4 átfogalmazva |
| A PR-4B1 scope elég? | A GoPro-lábra igen; nem bővítjük visszamenőleg |
| A futó HERO12 smoke mit validál? | A közös építőelemeket (lásd 3. pont) — futtatása változatlanul indokolt |
| PR-4B2/4B3 módosítás kell? | IGEN: 4B2 superseded, 4B3 rescope → MC3-B, 4B4 kiváltva; új MC2/MC3 fázissor (5. pont) |
| Új gate-ek? | TOPO-G1..G9 (6. pont) |

---

**Implementációt, branchet vagy PR-t külön jóváhagyás nélkül nem kezdünk.**
