# AN-3B2D-3 QA Report — Dense Ball Trajectory Overlay

**Branch:** `feat/an3b2d-3-ball-trajectory-overlay`  
**HEAD SHA:** `baae9a31`  
**Build tag:** `AN3B2D-3-ball-trajectory-overlay`  
**QA dátum:** 2026-06-18  
**Tesztvideó (elsődleges):** `86d01f49-4b5f-4aa7-8124-05c97f462f59` (player04@lfa-seed.hu, 214 pt)  
**Tesztvideó (másodlagos):** `8e39b06f-2fc8-47ff-b4f9-222a563f1252` (player04@lfa-seed.hu, 178 pt)

---

## Összesített Státusz

| QA típus | Státusz |
|---|---|
| User Visual QA | **PARTIAL PASS** (2026-06-18) |
| Developer Technical QA | **PENDING** |
| **Végleges AN-3B2D-3 QA döntés** | **FÜGGŐBEN** — Technical QA befejezéséig |

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

## 2. Developer Technical QA — PENDING

A következő tételek fejlesztői / technikai QA-t igényelnek, és **nem helyettesíthetők manuális vizuális ellenőrzéssel.**

### Nyitott tételek

| # | Q-kritérium | Mit kell ellenőrizni | Eszköz |
|---|---|---|---|
| T1 | Q1 — API 200, 214 pont | `GET /ball-trajectory` HTTP 200, `points.count == 214`, `status == "complete"` | Charles / Proxyman / Xcode network debug |
| T2 | Q4 — Lost frame: nincs ghost marker | Lost frame-eken vizuálisan nincs eltévedt pont, banner megjelenik | iPhone + hálózati log kombináció |
| T3 | Q6 — Skeleton overlay koegzisztencia | skeleton toggle bekapcsolva + ball overlay egyszerre látható, ZStack ordering helyes | iPhone vizuális + Xcode console (nincs warningja layout-conflict) |
| T4 | Q8 — Xcode console clean | Nincs `Fatal error`, `EXC_BAD_ACCESS`, `nil force-unwrap`, `Index out of range` a teljes QA session alatt | Xcode Console |
| T5 | Q9 — BTR-01..12 + BTI-01..08 PASS | 20/20 unit test zöld szimulátoron | `xcodebuild test` vagy Xcode Test Navigator |

### T5 — Q9 gyors futtatási parancs

```bash
xcodebuild test \
  -project ios/LFAEducationCenter.xcodeproj \
  -scheme LFAEducationCenterTests \
  -destination 'platform=iOS Simulator,name=iPhone 16' \
  -only-testing:LFAEducationCenterTests/BallTrajectoryOverlayTests \
  -only-testing:LFAEducationCenterTests/BallTrajectoryIntegrationTests \
  2>&1 | grep -E "Test Suite|PASS|FAIL|error:"
```

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
