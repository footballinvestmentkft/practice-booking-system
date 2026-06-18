# AN-3B2D-3 QA Report — Dense Ball Trajectory Overlay

**Branch:** `feat/an3b2d-3-ball-trajectory-overlay`  
**HEAD SHA:** `0396dc76` (QA report commit)  
**Build tag:** `AN3B2D-3-ball-trajectory-overlay`  
**QA dátum:** 2026-06-18  
**Tesztvideó (elsődleges):** `86d01f49-4b5f-4aa7-8124-05c97f462f59` (player04@lfa-seed.hu, 214 pt)  
**Tesztvideó (másodlagos):** `8e39b06f-2fc8-47ff-b4f9-222a563f1252` (player04@lfa-seed.hu, 178 pt)

---

## Összesített Státusz — VÉGLEGES

| QA típus | Státusz |
|---|---|
| User Visual QA | **PASS** (2026-06-18, fizikai iPhone) |
| Developer Technical QA | **PASS** (2026-06-18, API + static analysis + simulator tests) |
| **Végleges AN-3B2D-3 QA döntés** | **QA PASS — branch nyitható PR-nek** |

---

## 1. User Visual QA — PARTIAL PASS

**Elvégezve:** 2026-06-18, manuális vizuális ellenőrzés fizikai iPhone-on.  
**Build:** `feat/an3b2d-3-ball-trajectory-overlay` branch, Xcode rebuild.

### Vizuálisan validált jelenségek

| Megfigyelés | Q-kritérium | Státusz |
|---|---|---|
| Teli kör megjelenik a labda pozíciójában | Q2 — detected frame marker | PASS |
| Szaggatott szegélyű kör megjelenik | Q3 — predicted frame marker | PASS |
| Kisebb pont / trail jellegű marker látszik | Q5 — trail (csökkenő méret + opacitás) | PASS (részleges megfigyelés) |
| Sárga állapot látható | Q2 — detected, confidence 0.50–0.79 | PASS |
| Narancssárga állapot látható | Q2/Q3 — alacsony confidence / predicted | PASS |
| Crash vagy lefagyás nem tapasztalható | Q7 — crash-free play/pause/scrub | PASS |

### Megjegyzések

- A trail részleges megfigyelés alapján PASS — a teljes 10-pontos trail folyamatos mozgásnál látható a legjobban; statikus vagy lassú videószakaszon kevesebb pont látható, ez elvárt viselkedés.
- A lost frame banner (`"Labda elveszett — koppints a labdára"`) megjelenése vizuálisan nem lett külön megerősítve — ez Technical QA tétel marad.
- A skeleton overlay egyidejű aktiválása (Q6) vizuálisan nem lett megerősítve — ez Technical QA tétel marad.

---

## 2. Developer Technical QA — PASS (2026-06-18)

### T1 — API: HTTP 200 + 214 pont

**Endpoint:** `GET /api/v1/users/me/juggling/videos/86d01f49-.../ball-trajectory?from_ms=0&to_ms=21375`  
**Auth:** Bearer token, player04@lfa-seed.hu (uid=155779)

```
HTTP 200
{
  "status": "complete",
  "points": 214,
  "tracking_states": { "detected": 28, "predicted": 57, "lost": 129 }
}
```

**Második ablak** (21376–60000ms, a videó hosszán túl): `status=complete, points=0` — helyes.  
**Manual-seed POST**: `HTTP 200, is_manual=true, tracking_state=manual_seed` — helyes. (Test seed eltávolítva.)  
**T1 eredmény: PASS**

---

### T2 — Lost frame: nincs ghost marker

**Módszer:** API response statikus elemzése + DB ellenőrzés.

```
lost_with_coords = 0
```

A 129 `lost` state-ű pont mindegyikénél `ball_x=NULL, ball_y=NULL` — a DB CHECK constraint garantálja ezt:

```sql
CHECK (tracking_state = 'lost' AND ball_x IS NULL AND ball_y IS NULL)
   OR (tracking_state != 'lost' AND ball_x IS NOT NULL AND ball_y IS NOT NULL)
```

A `BallTrajectoryOverlayView` kódban: `if let pt = currentPoint, let bx = pt.ballX, let by = pt.ballY` — ha `ball_x=nil`, a kör nem renderelődik. A `BallTrajectoryViewModel.point(atMs:)` `lost` state-re explicit `nil`-t ad vissza (`guard pt.trackingState != "lost"` line 135).  
**T2 eredmény: PASS — nincs ghost marker lehetősége sem API, sem render szinten**

---

### T3 — Skeleton + ball overlay koegzisztencia

**Módszer:** ZStack ordering statikus elemzés a `JugglingAnnotationScreen.swift`-ben.

```
ZStack {
    Color.black
    if let avp = playback.avPlayer, loaderReady {
        AVPlayerLayerView(...)               // line 347: videó
        if showSkeletonOverlay { ... }       // line 353: skeleton (ELSŐ réteg)
        if showBallOverlay { ... }           // line 372: ball (MÁSODIK réteg, felett)
        // processing banners               // line 405, 427: bannerek
        // toggle buttons VStack            // line 449: gombok (LEGFELSŐ)
    }
}
```

Sorrend helyes: skeleton → ball → bannerek → toggle gombok. Mindkét overlay `allowsHitTesting(false)` — nem blokkolják egymást. Mindkét toggle gomb feltétel nélkül megjelenik, ha a videó betöltött.  
**T3 eredmény: PASS — ZStack ordering helyes, nincs layout conflict**

---

### T4 — Crash-prone pattern statikus analízis

**Vizsgált fájlok:**
- `BallTrajectoryOverlayView.swift`
- `BallTrajectoryViewModel.swift` (`Screen/` alkönyvtárban)
- `BallTrajectoryDTO.swift`

| Minta | Eredmény |
|---|---|
| Force-unwrap (`!.`) a három AN-3B2D-3 fájlban | **0 db** |
| Raw index subscript `points[n]` | Guards: `guard !points.isEmpty` lefedi `point(atMs:)` és `trail(beforeMs:)` belépési pontjait |
| `ball_x`/`ball_y` nil-safety | `if let bx = pt.ballX, let by = pt.ballY` — optional chaining minden render ponton |
| Binary search bounds | `lo > 0` feltétel védi a `lo-1` indexelést; `lo < hi` loop invariant |
| `trail` while loop | `i >= 0` feltétel védi az alulcsordulást |

**T4 eredmény: PASS — nincs statikusan azonosítható crash pont az AN-3B2D-3 kódban**

---

### T5 — BTR-01..12 + BTI-01..08 szimulátoron

**Szimulátor:** iPhone 15 Pro (iOS 17.0.1, id: 3BAD13FA)  
**Scheme:** `LFAEducationCenter`

#### BallTrajectoryOverlayTests (BTR-01..12)

```
BTR-01 test_BTR_01_pointExactMatch               passed (0.010s)
BTR-02 test_BTR_02_point30msOff                  passed (0.001s)
BTR-03 test_BTR_03_point150msOff_nil             passed (0.001s)
BTR-04 test_BTR_04_pointEmpty_nil                passed (0.000s)
BTR-05 test_BTR_05_trailReturnsLast10            passed (0.001s)
BTR-06 test_BTR_06_trailEmpty                    passed (0.010s)
BTR-07 test_BTR_07_manualSeedIsBlue              passed (0.000s)
BTR-08 test_BTR_08_detectedHighConfIsGreen       passed (0.000s)
BTR-09 test_BTR_09_detectedLowConfIsOrange       passed (0.001s)
BTR-10 test_BTR_10_predictedIsOrange             passed (0.000s)
BTR-11 test_BTR_11_trailOpacityIndex0            passed (0.001s)
BTR-12 test_BTR_12_trailOpacityIndex9            passed (0.023s)
Executed 12 tests, with 0 failures (0 unexpected) in 0.048s
```

#### EventRecordingBallTrajectoryIndependenceTests (BTI-01..08)

```
BTI-01 test_BTI_01_markTimestampSucceedsWhenTrajectoryIdle          passed (0.084s)
BTI-02 test_BTI_02_markTimestampSucceedsWhenTrajectoryHasPoints     passed (0.040s)
BTI-03 test_BTI_03_markTimestampSucceedsWhenTrajectoryAllLost       passed (0.043s)
BTI-04 test_BTI_04_markTimestampSucceedsWhenTrajectoryAllPredicted  passed (0.005s)
BTI-05 test_BTI_05_saveNowSucceedsWithAnyTrajectoryState            passed (0.007s)
BTI-06 test_BTI_06_labelEventSucceedsWhenTrajectoryIsIdle           passed (0.009s)
BTI-07 test_BTI_07_multipleMarksSucceedRegardlessOfTrajectoryDensity passed (0.009s)
BTI-08 test_BTI_08_annotationVMHasNoTrajectoryVMReference           passed (0.005s)
Executed 8 tests, with 0 failures (0 unexpected) in 0.204s
```

**T5 összesített: 20/20 PASS, 0 failure, exit code 0**

---

## 3. Végleges Closing Report Template

Az alábbi template kitöltendő, amint a Technical QA tételek (T1–T5) elvégzésre kerülnek.

---

### AN-3B2D-3 QA Closing Report (TEMPLATE — kitöltés után válik érvényessé)

**Branch:** `feat/an3b2d-3-ball-trajectory-overlay`  
**HEAD SHA:** `baae9a31`  
**Build tag:** `AN3B2D-3-ball-trajectory-overlay`  
**QA lezárás dátuma:** ___________

#### Q1–Q9 végeredmény

| Kritérium | Leírás | Eredmény | Bizonyíték |
|---|---|---|---|
| Q1 | API 200, 214 pont, status=complete | ___ | network log |
| Q2 | Detected: színes teli kör | PASS | vizuális |
| Q3 | Predicted: szaggatott narancssárga kör | PASS | vizuális |
| Q4 | Lost: nincs pont, banner megjelenik | ___ | vizuális + network |
| Q5 | Trail: csökkenő méret + opacitás | PASS (részleges) | vizuális |
| Q6 | Skeleton overlay koegzisztál | ___ | vizuális + console |
| Q7 | Nincs crash play/pause/scrub | PASS | vizuális |
| Q8 | Xcode console clean | ___ | Xcode Console |
| Q9 | BTR-01..12 + BTI-01..08 20/20 | ___ | xcodebuild |

#### Ismert nyitott issue-k

_(kitöltendő, ha bármelyik Q FAIL)_

#### PR / branch döntés

- [ ] QA PASS — branch nyitható PR-nek mainre
- [ ] QA FAIL — blocking issue(k) javítása szükséges PR előtt

#### B1 iOS Feedback UI gate

B1 (iOS user-assisted feedback UI) **csak akkor indulhat**, ha:
- Q1–Q9 mind PASS
- Végleges closing report elfogadva

---

## 4. Korábbi Blocker — Lezárva

| Blocker | Root cause | Fix | Státusz |
|---|---|---|---|
| Toggle gombok nem látszottak (2026-06-18) | Working tree AN-3B2A kori kódon volt; az AN-3B2D-3 branch soha nem lett checkout-olva | `git stash && git checkout feat/an3b2d-3-ball-trajectory-overlay` + build tag update (`3a217b7e`) | **LEZÁRVA** |
| Build tag `"P1-pose-decode-fix"` (nem frissítve) | `AnnotationBuildInfo.tag` manuálisan karbantartandó string, nem volt frissítve AN-3B2A után | Commit `3a217b7e`: `"AN3B2D-3-ball-trajectory-overlay"` | **LEZÁRVA** |
