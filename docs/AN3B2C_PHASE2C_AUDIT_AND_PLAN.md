# AN-3B2C — Felhasználói Analitikai Réteg — Teljes Audit + Terv

Státusz: **AUDIT + TERV — implementáció nem kezdődött el.**  
Alap: PR #301 (AN-3B2B-1 Ball Detection) merge-ready, HEAD `9a621c1f`.  
Audit dátuma: 2026-06-17.

---

## 0. Összefoglaló

A Phase 2B (PR #301) teljesen implementálta a ball detection backend pipeline-t:

| Komponens | Állapot |
|---|---|
| `juggling_ball_detections` DB tábla | ✅ |
| SSD MobileNet v1 ONNX inference (`onnx_ball_detector.py`) | ✅ |
| Frame extraction (`frame_extractor.py`, `cv2`) | ✅ |
| Celery `analysis` queue task (`detect_ball_for_event`) | ✅ |
| Admin trigger endpoint | ✅ |
| User endpoints: POST manual override + GET result | ✅ |
| 61 BDT/TVT teszt (58/58 CI PASS) | ✅ |

**A pipeline kész. A felhasználó még semmit nem lát belőle.**

Ez a Phase 2C audit: hogyan adjunk felhasználói értéket a meglévő pipeline-ra, minimális új backend infrastruktúrával. A 7 produktkérdés megválaszolása, iOS UX design, és jóváhagyásra váró 3-PR terv.

---

## 1. A 7 Produktkérdés

### 1.1 Hogyan jelenjen meg a detektált labda az iOS felületen?

**Jelenlegi iOS állapot**: az `EventLabelDetailView` still frame-t mutat a `EventPreviewSession`-ből, de sem a `JugglingAnnotationAPIClient`, sem a `JugglingAnnotationViewModel` nem ismeri a ball detection endpoint-okat. Nincs iOS oldali labda megjelenítés.

**Javasolt megjelenés — `BallOverlayView`**:

A meglévő `PoseSnapshotOverlayView` mintájára — normált `[0,1]` koordináta-rendszer, `GeometryReader`-rel vetítve a tényleges frame méretre. A labda és a skeleton overlay **egyszerre látható**, `ZStack`-be integrálva:

```
┌──────────────────────────────────────┐
│   [skeleton overlay — zöld vonal]    │
│                                      │
│          ○  ← labda (sárga tömör)    │
│                                      │
│  confidence: 0.87                    │ ← caption a frame alatt
└──────────────────────────────────────┘
```

Vizuális kódolás — a `confidence` alapján:

| Confidence | Megjelenés |
|---|---|
| ≥ 0.80 | Sárga tömör kör (12pt átmérő), `Color.yellow.opacity(0.9)` |
| 0.50–0.79 | Narancs félig átlátszó kör, `Color.orange.opacity(0.6)` |
| < 0.50 | Piros szaggatott kör (alacsony megbízhatóság) |
| `no_ball_detected = true` | Kör nincs; "Labda nem azonosítható" felirat a frame aljára |
| Nincs adat (404) | Szürke szaggatott kör placeholder, "Azonosítás folyamatban..." caption |
| Manual override (user által javított) | Kék tömör kör — jelzi, hogy nem az automatikus detekció |

**iOS 14 kompatibilis**: `ZStack`, `Circle`, `Path`, `strokeBorder` — nincs `Canvas` vagy `Chart`.  
**Helye**: `EventLabelDetailView.swift` still frame területe fölé, a meglévő `PoseSnapshotOverlayView` mellé `ZStack`-be (mindkettő egyszerre aktív).

---

### 1.2 Milyen vizuális visszajelzést kapjon a felhasználó?

Három réteg:

**A) Esemény-szintű badge az `EventTimelineView` sorokban**

A jelenlegi timeline row `timestamp_ms`, `contact_zone`, `body_part` adatot jelenít meg. Kibővítés: kis ikon a sor végén, iOS 14 `Image(systemName:)`-mel:

| Állapot | Ikon | Szín |
|---|---|---|
| Azonosítva (confidence ≥ 0.80) | `circle.fill` | Zöld |
| Azonosítva (confidence < 0.80) | `circle.fill` | Sárga |
| `no_ball_detected = true` | `xmark.circle` | Piros |
| Folyamatban / nincs adat | `circle.dashed` | Szürke |

**B) Feldolgozási állapot banner**

A Celery task aszinkron fut; az iOS-nak nincs WebSocket-je. Stratégia:
- `EventLabelDetailView` megnyitásakor: GET `/ball-detection` (`.onAppear + Task{}` — iOS 14 kompatibilis)
- Ha 404: 30 másodperces polling, max 5×
- Ha adat érkezik: "Labda beazonosítva" info banner (2 mp, zöld), majd eltűnik
- Ha 5 polling után sincs adat: statikus "Automatikus azonosítás folyamatban" szöveg — nem hiba, csak tájékoztatás
- Ha `no_ball_detected = true` érkezik: "Labda nem volt azonosítható" warning badge

**C) Videó-szintű összesítő** (Phase 2C-3-ban kerül implementálásra, lásd 2.3)

A `JugglingVideoListView` videó soraiban: `N / M érintésnél azonosítva` szöveg, ahol N = megtalált ball detection count, M = összes approved event count.

---

### 1.3 Hogyan validálhatja vagy javíthatja a detektálást?

Az endpoint már él: `POST /users/me/juggling/videos/{vid}/contacts/{eid}/ball-detection` — idempotent upsert, `detection_source="manual"`.

**Manual override UX** az `EventLabelDetailView` still frame alatt:

```
┌──────────────────────────────────────────┐
│  [frame + skeleton overlay + labda kör]  │
│                                          │
│  [Labda pozíció korrekció]  [Nincs labda]│ ← action row
└──────────────────────────────────────────┘
```

**"Labda pozíció korrekció" gomb → drag mode**:
- Gombra tap → a frame drag-aktív állapotba lép (overlay vizuális jelzéssel)
- A labda marker `DragGesture`-rel (iOS 13+) húzható a frame területén belül
- `onEnded`: `ball_x = dragLocation.x / frameWidth`, `ball_y = dragLocation.y / frameHeight`
- POST `/ball-detection` `{ball_x, ball_y, confidence: 1.0, no_ball_detected: false}`
- **Optimista update**: lokális `@State var localBallPosition` azonnal frissül, background szinkronizáció
- Hiba esetén (network / 422): visszaáll az előző pozícióra + hibaüzenet toast

**"Nincs labda" gomb**:
- POST `/ball-detection` `{no_ball_detected: true}`
- A kör eltűnik, "Labda nem azonosítható" overlay megjelenik
- Visszavonás: "Labda volt" gomb visszaállítja (POST `{no_ball_detected: false}` az előző koordinátával, vagy drag-gel új pozíció)

**Kék kör = manual override**: ha a backend `detection_source="manual"`, az overlay kék tömöt mutat — egyértelműen jelzi a user-módosított állapotot.

**Miért nem long-press**: toggle gomb explicitebb, nem triggerálható véletlenszerűen a skeleton overlay scrollozásakor.

---

### 1.4 Hogyan épül rá a pitch calibration rendszer?

A `juggling_ball_detections` tábla `world_x_m` és `world_y_m` mezői jelenleg **NULL** minden sorban — nincs homográfia, nincs valós koordináta. A pitch calibration (AN-3B2B-2) feloldja ezt.

**User-visible folyamat**:

1. User megnyitja a `JugglingAnnotationScreen`-t
2. Toolbar jobb felső sarkában: "Pálya" ikon (🔲 típusú, `square.dashed`)
3. Tap → `PitchCalibrationView` push navigációval nyílik
4. Háttérben: a videó első frame-je static image-ként (nem lejátszás)
5. 4 draggable marker a pályasarkokon (`DragGesture`, iOS 13+)
6. Két `TextField` párban: "Szélesség (m)" és "Hosszúság (m)" — a valós méretek
7. "Kalibrálás mentése" → POST `/pitch-config` → backend homográfia számítás (`numpy.linalg`)
8. **Azonnali hatás**: minden meglévő ball detection `world_x_m`/`world_y_m` retroaktívan frissül a backend oldalon

**Pitch calibration nélkül a rendszer tovább működik**: screen-koordinátás módban, a heatmap és az analytics megjelenik, de méter helyett normált értékeket mutat. A user figyelmeztetést kap: "Pályakalibrálás nélkül a pozíciók képernyő-arányosak, nem valós méterben."

**iOS 14**: `DragGesture`, `TextField`, `NavigationLink` — nincs `@FocusState` (iOS 15+).

---

### 1.5 Hogyan épül rá a movement analytics?

A `/analysis` composite endpoint (AN-3B2B-3 terv) az érintés pillanatában kombinált képet ad:
- **Pose** (joint szögek): trunk_lean_deg, left/right_knee_flexion_deg, ankle_dorsiflexion_deg
- **Ball position**: normált vagy valós koordináta (ha van pitch config)
- **pitch_config_applied**: jelzi, melyik módban számoltak

**User-visible értéke az `EventLabelDetailView`-ban**:

```
┌─────────────────────────────────────────┐
│  [Frame: skeleton + labda kör]          │
├─────────────────────────────────────────┤
│  Törzsdőlés           12.4°             │
│  Bal térd hajlítás    34.1°             │
│  Jobb térd hajlítás   31.8°             │
│  Bal boka             —                 │  ← null ha confidence < 0.3
│  Labda helyzete       3.2 m / 8.1 m    │  ← ha van pitch config
│                       (0.31 / 0.67 n.) │  ← ha nincs pitch config
└─────────────────────────────────────────┘
```

- **Cél**: önreflektív szám — a user látja, hogyan állt érintéskor. Nincs "jó" / "rossz" minősítés Phase 2C-ben.
- `null` metrikák (`—` jelzéssel) jelzik az alacsony confidence jointokat (< 0.3) — nem hibát, hanem az adat bizonytalanságát
- iOS 14: `VStack` + `HStack` label-érték párok, `.font(.caption)`, `.foregroundColor(.secondary)`

---

### 1.6 Hogyan lesz ebből heatmap?

A `/movement-summary` endpoint (AN-3B2B-4 terv) egy videó összes ball detection pozícióját aggregálja.

**Számítás** (backend, `scipy.stats.gaussian_kde`):
- Input: az összes `juggling_ball_detections` sor a videohoz (`world_x_m` IS NOT NULL, ha van pitch config; fallback: `ball_x/ball_y`)
- Kimenet: 20×14 float rács (standard 105m×68m pálya arány) + scatter pontok listája

**`MovementHeatmapView` iOS UX**:

```
┌────────────────────────────────────────────────┐
│  Érintési pozíciók  (47 db)   [Pályakalibrálás]│  ← ha nincs pitch config
│  ┌──────────────────────────────────────────┐   │
│  │  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │   │
│  │  ░░▒▒▒▒░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │   │
│  │  ░░▒▒████▒▒░░░░░░░░░░░░░░░░░░░░░░░░░░░  │   │  ← pitch kontúr
│  │  ░░▒▒████████▒▒░░░░░░░░░░░░░░░░░░░░░░░  │   │
│  │  ░░▒▒████▒▒░░░░░░░░░░░░░░░░░░░░░░░░░░░  │   │
│  │  ░░▒▒▒▒░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │   │
│  │  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │   │
│  └──────────────────────────────────────────┘   │
│  ● ● ●  ← scatter pontok                        │
└────────────────────────────────────────────────┘
```

**iOS 14 kompatibilis implementáció**:
- Pálya kontúr: `Path`-alapú négyszög outline + középső vonal — nincs `Canvas` (iOS 15+)
- Hőtérkép rács: nested `ForEach` + `Rectangle`, szín: `Color(hue: 0.6, saturation: 0.7, brightness: lerp(0.4, 0.9, density))`
- Scatter pontok: kis `Circle` overlay az egyes érintési pozíciókon
- `point_count < 5`: "Nincs elegendő adat" placeholder (min adatküszöb a KDE stabilitáshoz)
- Pitch config nélküli mód: disclaimer badge + "képernyő-relatív" módfelirat

**Elérhetőség**:
- `JugglingVideoListView` → videó sor → "Elemzés" gomb → `VideoAnalysisView` push navigáció
- `VideoAnalysisView` tartalmazza: `MovementHeatmapView` + összesítő stats (`N/M azonosítva`) + event lista linkkel

---

### 1.7 Hogyan kapcsolódik a jövőbeli skill/GameResult rendszerhez?

> **Hatóköri megkötés (nem módosítandó)**: `football_skill_service.py`, `segment_reward_service.py`, `virtual_training_metrics.py`, `tournament_participation_service.py` — ezek érintetlenek maradnak Phase 2C-ben és azon túl is, amíg külön jóváhagyás nem születik.

**Lehetséges összeköttetési pontok — tájékoztató, nem Phase 2C scope**:

A ball detection pipeline három olyan jelet termel, amelyek a skill pipeline bemeneteként szolgálhatnának anélkül, hogy a skill pipeline-t módosítani kellene — a jövőbeli integráció **csak olvasná** ezeket az adatokat:

| Jel | Forrás adat | Lehetséges felhasználás |
|---|---|---|
| Contact zone distribution | `ball_x`, `ball_y` → zónabinning (bal / közép / jobb × közel / közép / messze) | Pozíció-specifikus ball control értékelés; lateralitás megerősítés |
| Stance quality index | `keypoints` → `trunk_lean_deg`, `knee_flexion_deg` átlag és szórás | Biomechanikai konzisztencia jelzője; skill assessment kontextusa |
| Approved contact count | `annotation_review_status = approved` count per video | Mennyiségi aktivitásjelző — már elérhető az AN-1 óta |

**Phase 2C döntés**: a ball detection és pose analytics adatai **tárolódnak és megjelennek a felhasználónak**, de **nem injektálódnak a skill scoring pipeline-ba**. A skill integráció külön Phase 3 döntés, önálló jóváhagyással, miután a Phase 2C analytics adatok valós forgalomban validálódtak.

---

## 2. Phase 2C Scope — 3 PR

### AN-3B2C-1: iOS Ball Detection Visualization + Manual Correction

**Cél**: a backend által már tárolt ball detection adatot iOS-on megjeleníteni és a user számára korrigálhatóvá tenni. **Nincs új backend endpoint** — az AN-3B2B-1 endpoint-ok (POST manual override + GET result) már live-ok.

**iOS fájlok**:

| Fájl | Típus | Változtatás |
|---|---|---|
| `JugglingAnnotationAPIClient.swift` | MÓDOSÍTOTT | +`fetchBallDetection(videoId:eventId:)`, +`postManualBallDetection(videoId:eventId:x:y:noBall:)` |
| `JugglingAnnotationAPIClient.swift` | MÓDOSÍTOTT | +`BallDetectionOut` Swift struct, +`BallDetectionManualRequest` struct |
| `JugglingAnnotationViewModel.swift` | MÓDOSÍTOTT | +`ballDetections: [UUID: BallDetectionOut]`, +`fetchBallDetection(for:)`, +`postManualBallDetection(for:x:y:)`, +`markNoBall(for:)` |
| `BallOverlayView.swift` | ÚJ | `GeometryReader`-alapú labda marker overlay, confidence-alapú stílus |
| `EventLabelDetailView.swift` | MÓDOSÍTOTT | +`BallOverlayView` integrálás a still frame `ZStack`-be, +drag correction gesture, +"Labda pozíció korrekció"/"Nincs labda" action row |
| `EventTimelineView.swift` | MÓDOSÍTOTT | +ball detection badge a timeline row végén |
| `JugglingAnnotationAPIClientProtocol` | MÓDOSÍTOTT | +`fetchBallDetection`, +`postManualBallDetection` protocol metódusok |
| `ios/.../Tests/Juggling/BallOverlayViewTests.swift` | ÚJ | |
| `ios/.../Tests/Juggling/BallDetectionManualCorrectionTests.swift` | ÚJ | |
| `ios/LFAEducationCenter.xcodeproj/project.pbxproj` | MÓDOSÍTOTT | |

**Polling stratégia**: `.onAppear + Task{}` (iOS 14 kompatibilis). GET /ball-detection az `EventLabelDetailView` megnyitásakor. 404 esetén 30 mp-ként újra, max 5×. Siker esetén `@Published` property frissítése → UI automatikusan frissül.

**Tesztek (BD-IOS-01..BD-IOS-08)**:

| ID | Mit tesztel |
|---|---|
| BD-IOS-01 | `BallOverlayView` confidence ≥ 0.80 → sárga tömör kör |
| BD-IOS-02 | `BallOverlayView` confidence < 0.50 → piros szaggatott kör |
| BD-IOS-03 | `BallOverlayView` `no_ball_detected=true` → kör nincs, szöveg van |
| BD-IOS-04 | `BallOverlayView` `detection_source="manual"` → kék tömör kör |
| BD-IOS-05 | Drag correction → `postManualBallDetection` meghívódik, lokális optimista update |
| BD-IOS-06 | "Nincs labda" tap → `markNoBall` meghívódik |
| BD-IOS-07 | `fetchBallDetection` 404 → placeholder állapot + polling indul |
| BD-IOS-08 | Timeline badge: high confidence → zöld ikon; no data → szürke; no_ball → piros |

---

### AN-3B2C-2: Pitch Calibration UI + Backend

**Cél**: user megadhatja a pályakalibrálást → ball detections `world_x_m/world_y_m` feltöltésre kerül → a heatmap és analytics valós méterekben mutat.

**Backend** (AN-3B2B-2 terv szerinti):
- 2 új DB tábla: `juggling_pitch_configs`, `juggling_reference_objects`
- 5 endpoint: POST/GET/DELETE `/pitch-config`, POST/DELETE `/reference-objects/{id}`
- Homográfia számítás: `numpy.linalg.lstsq` DLT (ha `opencv-python-headless` már requirement az AN-3B2B-1-ből: `cv2.getPerspectiveTransform` is megfelel)

**iOS**:

| Fájl | Típus |
|---|---|
| `PitchCalibrationView.swift` | ÚJ — video first frame + 4 draggable marker + world metrics input |
| `JugglingAnnotationScreen.swift` | MÓDOSÍTOTT — toolbar "Pálya" gomb → `PitchCalibrationView` push |
| `JugglingAnnotationAPIClient.swift` | MÓDOSÍTOTT — +`fetchPitchConfig`, +`savePitchConfig`, +`deletePitchConfig` |
| `ios/.../Tests/Juggling/PitchCalibrationViewTests.swift` | ÚJ |

**Backend tesztek**: AN-3B2B-2 terv szerinti PCT-01..PCT-10.

---

### AN-3B2C-3: Movement Analytics + Heatmap

**Cél**: aggregált nézet iOS-on — joint szögek, ball pozíció, hőtérkép. Ez a Phase 2C legfontosabb user-visible deliverable-je.

**Backend** (AN-3B2B-3 + AN-3B2B-4 kombinálva):
- `/analysis` composite endpoint: event + pose + ball + metrics
- `/movement-summary` endpoint: KDE heatmap 20×14 grid + scatter
- Movement metrics service: trunk_lean, knee_flexion, ankle_dorsiflexion (pure Python, numpy)
- `scipy.stats.gaussian_kde` (>=1.11.0)

**iOS**:

| Fájl | Típus |
|---|---|
| `MovementHeatmapView.swift` | ÚJ — pitch kontúr + hőtérkép rács + scatter (iOS 14 kompatibilis) |
| `VideoAnalysisView.swift` | ÚJ — videó-szintű analytics hub: stats summary + heatmap + event lista |
| `EventLabelDetailView.swift` | MÓDOSÍTOTT — +metrika szekció (trunk lean, knee flex) |
| `JugglingAnnotationAPIClient.swift` | MÓDOSÍTOTT — +`fetchAnalysis`, +`fetchMovementSummary` |
| `JugglingVideoListView.swift` | MÓDOSÍTOTT — +"Elemzés" gomb, N/M azonosítva badge |
| `ios/.../Tests/Juggling/MovementHeatmapViewTests.swift` | ÚJ |
| `ios/.../Tests/Juggling/VideoAnalysisViewTests.swift` | ÚJ |

**Backend tesztek**: AN-3B2B-3 + AN-3B2B-4 terv szerinti MMT-01..08 + HMP-01..06.

---

## 3. Implementációs sorrend

```
AN-3B2C-1 (iOS Ball Viz)  ────────────────────────────────────── PR #302
                                                                    │
AN-3B2C-2 (Pitch Calib)   ── backend + iOS ──────────────────── PR #303
                                                                    │
                                   (mindkettő merged)
                                           │
                                    AN-3B2C-3 (Analytics) ──── PR #304
```

- **AN-3B2C-1 és AN-3B2C-2 párhuzamosan** futtatható — nincs köztük DB függőség
- **AN-3B2C-3** csak AN-3B2C-1+2 merge után indul: a composite `/analysis` endpoint a pitch config + ball detection adatot kombinálja, és az iOS analytics screen mindkét előző PR iOS komponensét feltételezi

---

## 4. Összesítő: meglévő vs. tervezett

| Réteg | Backend | iOS |
|---|---|---|
| Ball detection adattárolás | ✅ PR #301 COMPLETE | ❌ |
| Ball detection overlay (EventLabelDetailView) | ✅ (endpoint él) | ❌ AN-3B2C-1 |
| Manual position correction | ✅ (POST endpoint él) | ❌ AN-3B2C-1 |
| No-ball marking | ✅ (`no_ball_detected` mező él) | ❌ AN-3B2C-1 |
| Timeline badge | — | ❌ AN-3B2C-1 |
| Pitch calibration | ❌ AN-3B2C-2 | ❌ AN-3B2C-2 |
| World coordinates (m) | ❌ (NULL, pitch config hiányzik) | ❌ AN-3B2C-2 |
| Joint angle metrics (trunk lean, knee flex) | ❌ AN-3B2C-3 | ❌ AN-3B2C-3 |
| Video-level heatmap | ❌ AN-3B2C-3 | ❌ AN-3B2C-3 |
| Skill/GameResult signal | Adat elérhető — integráció **DEFERRED** | — |

**Kiemelt tanulság**: az AN-3B2B-1 egy teljes backend pipeline-t deliver-ált, de az iOS megjelenítése teljesen hiányzik. Az AN-3B2C-1 az egyetlen olyan PR a sorozatban, amely **kizárólag iOS munkát** tartalmaz — ezt érdemes elsőként kiszállítani, hogy a ball detection értéke az user számára láthatóvá váljon.

---

## 5. iOS 14 kompatibilitás — kockázati pontok

| Elem | Kockázat | Megoldás |
|---|---|---|
| `BallOverlayView` drag correction | `DragGesture` iOS 13+ | OK |
| `MovementHeatmapView` hőtérkép | `Canvas` (iOS 15+) nem elérhető | nested `ForEach` + `Rectangle` — iOS 14 OK |
| `PitchCalibrationView` `TextField` focus | `@FocusState` iOS 15+ | `UITextField` UIViewRepresentable wrapper, vagy simple `TextField` fókusz nélkül |
| `VideoAnalysisView` navigation | `NavigationStack` iOS 16+ | `NavigationLink` + `NavigationView` — mint a többi screen |
| `/analysis` endpoint poll | `.task` modifier iOS 15+ | `.onAppear + Task{}` — mint a Phase 2A minta |

---

## 6. Nem módosítandó fájlok (explicit hatóköri kizárás)

| Fájl | Ok |
|---|---|
| `football_skill_service.py` | Skill pipeline — külön jóváhagyás nélkül nem érinthető |
| `segment_reward_service.py` | Reward pipeline — nem érinthető |
| `virtual_training_metrics.py` | VT metrikák — nem érinthető |
| `tournament_participation_service.py` | Tournament — nem érinthető |
| `juggling_analysis_task.py` | pragma: no cover, Celery task — Phase 2C-ben nem módosítandó |
| `onnx_ball_detector.py` | Inference réteg — Phase 2C-ben nem módosítandó |
| `frame_extractor.py` | Frame extraction — Phase 2C-ben nem módosítandó |

---

## 7. Nyitott kérdések jóváhagyáshoz

1. **AN-3B2C-1 polling stratégia**: 30 mp-es GET /ball-detection polling (max 5×) elfogadható-e, vagy egy explicit "Frissítés" pull-to-refresh gomb preferált? Javaslat: polling + pull-to-refresh kombinálva (a pull-to-refresh azonnal újra lekéri).

2. **Drag correction UX**: a "Labda pozíció korrekció" toggle gomb (tap → drag mode bekapcsolódik) vagy közvetlen long-press a frame-en? Javaslat: toggle gomb — explicitebb, nem triggerálható véletlenszerűen scrollozás közben.

3. **PitchCalibrationView entry point**: a `JugglingAnnotationScreen` toolbar gombja az AN-3B2C-1 részeként (csak a gomb placeholder, PitchCalibrationView még nincs), vagy az AN-3B2C-2 részeként? Javaslat: AN-3B2C-2-ben, egységes PR-ban a backend backend pitch-config endpoint-okkal.

4. **VideoAnalysisView navigáció**: a heatmap + analytics view a `JugglingVideoListView`-ból "Elemzés" gombbal érhető el, VAGY az annotation lezárása (`finishAnnotation`) után automatikusan megjelenik? Javaslat: explicit "Elemzés" gomb — az automatikus navigáció az annotation flow-t szakítaná meg.

5. **Metrikák "normál tartomány" megjelenítése**: Phase 2C-3 csak nyers számokat mutat (trunk_lean: 12.4°), vagy hasznos lenne egy vizuális tartomány-indikátor (pl. sávok, mini progress bar)? A normál tartományok meghatározásához domain expert input kell — ez külön döntés, nem Phase 2C scope.

6. **`scipy` vs. `onnxruntime` numpy kompatibilitás**: `scipy>=1.11.0` + `onnxruntime==1.26.0` numpy verzión keresztüli inkompatibilitást okozhat-e? Ellenőrizendő a requirements.txt teljes dependency grafjával.

---

**AN-3B2C implementáció nem indult el — a fenti terv és a 7. szakasz kérdései jóváhagyást igényelnek.**
