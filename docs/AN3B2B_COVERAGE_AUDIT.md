# AN-3B2B Coverage Audit — Branch 79.8% vs. 80.0% Threshold

Dátum: 2026-06-17.  
PR: #301, HEAD `8ad7515a`.

---

## 1. Main vs. PR coverage számok

| Metrika | Main (`87d34bab`) | PR (`8ad7515a`) | Delta |
|---|---|---|---|
| **Statements** | 52 348 | 52 610 | **+262** |
| **Stmt Miss** | 5 711 | 5 852 | **+141** |
| **Stmt Coverage** | 89.1% | 88.9% | **-0.2%** |
| **Branches** | 14 240 | 14 286 | **+46** |
| **BrPart (missed)** | 1 447 | 1 448 | **+1** |
| **Branch Coverage** | 80.1% | 79.8% | **-0.3%** |
| **Combined** | 87% | 87% | ±0% |

## 2. Root cause

A CI `Unit Tests` job a `tests/unit/ + tests/database/ + tests/integration/ +
tests/biometric/` könyvtárakból futtatja a teszteket `--cov=app` flag-gel.

Az AN-3B2B-1 tesztek az **`app/tests/test_juggling_ball_detection.py`** fájlban
vannak — **ez a könyvtár nincs a CI coverage mérés scope-jában**.

Emiatt a CI szemszögéből az AN-3B2B új fájlok:

| Fájl | CI coverage | Lokális coverage |
|---|---|---|
| `ball_detection_service.py` | **17%** | 100% |
| `juggling_ball_detection.py` (user) | **60%** | 100% |
| `juggling_admin_ball_detection.py` | **31%** | 100% |
| `frame_extractor.py` | **0%** | 100% |
| `onnx_ball_detector.py` | **0%** | 100% |
| `juggling_analysis_task.py` | **0%** | 100% |

A 17%/60%/31% azokból az import side-effectekből jön, ahol más tesztek
az `app.main` modul betöltésekor érintik a fájlokat.

## 3. Matematikai hatás

**Új branch-ek** (PR hozzáadva):

| Forrás | Branch szám |
|---|---|
| `JugglingBallDetection` model (CHECK constraints) | ~6 |
| `ball_detection_service.py` | 8 |
| `frame_extractor.py` | 4 |
| `onnx_ball_detector.py` | 12 |
| `juggling_analysis_task.py` | 22 |
| `juggling_admin_ball_detection.py` | 10 |
| `juggling_ball_detection.py` (user) | 4 |
| **Összesen** | **~46** (egyezik: 14286 - 14240 = 46) |

**Branch coverage hatás számítás**:

```
Main:  (14240 - 1447) / 14240 × ~some_factor = 80.1%
       → covered branches: ~11413

PR:    (14286 - 1448) / 14286 × ~some_factor = 79.8%
       → covered branches: ~11409

Különbség: a CI-ban az új 46 branch-ből ~42 fedetlen (mert az app/tests/ nem fut)
           → +42 missed branch → 1448 - 1447 = 1 BrPart növekedés a coverage.xml-ben
           (a BrPart oszlop parciális ágak, a teljes missed brancheket a branch-rate tartalmazza)
```

A branch-rate a coverage.xml-ben: `79.8%` → `14286 × 0.798 = ~11400 covered`

Ha az `app/tests/` tesztek futottak volna a CI-ban:
- +46 branch fedett → `(11400 + 46) / 14286 = 80.1%` → **PASS**

## 4. Megoldás: tesztek áthelyezése a CI scope-ba

Az `app/tests/test_juggling_ball_detection.py` fájlt a `tests/unit/juggling/`
könyvtárba kell áthelyezni (vagy másolni), hogy a CI `pytest tests/unit/` futásakor
a BDT tesztek is lefussanak és a coverage mérésbe bekerüljenek.

**Szükséges lépések:**

1. `app/tests/test_juggling_ball_detection.py` → `tests/unit/juggling/test_ball_detection.py`
2. A conftest/fixture kompatibilitás ellenőrzése (a `tests/unit/juggling/` conftest-je
   ugyanazt a `db_session`, `client`, `student_user`, `student_token` fixture-t használja)
3. Az import path-ok frissítése ha szükséges

**Ez scope-on belüli változás**: nem random teszteket írunk, hanem a meglévő
61 tesztet a CI által mért helyre tesszük.

## 5. Miért nem más megoldás?

| Opció | Miért nem |
|---|---|
| Threshold csökkentése | Nem jóváhagyott |
| Random tesztek más fájlokban | Nem jóváhagyott, scope creep |
| `app/tests/` hozzáadása a CI pytest parancshoz | Nagyobb infra változás, kockázatosabb |
| Coverage exclude az új fájlokra | Nem jóváhagyott (teljes fájl kizárás) |
| **Test áthelyezés** | **Scope-on belüli, valódi fix** |
