# Physical E2E Orientation Validation Report
**Date:** 2026-06-23  
**Branch:** feat/an3b-pr4b3b1b-session-orchestration  
**HEAD SHA:** 9111fd3b  
**PR:** #332 (NOT YET MERGED — 10-cycle drift measurement pending)

---

## 1. Orientation Fix — Változtatások összefoglalója

| Fájl | Root cause / fix |
|------|-----------------|
| `Info.plist` | Portrait-only lock eltávolítva; iPhone: portrait+landscapeLeft+landscapeRight; iPad: mind a 4 |
| `CaptureOrientationHelper.swift` | Új shared helper: `UIInterfaceOrientation → AVCaptureVideoOrientation`; `UIWindowScene.interfaceOrientation` elsődleges forrás; `.unknown`/faceUp/faceDown → `.portrait` fallback |
| `CapturePreviewView.swift` | `interfaceOrientation` param hozzáadva; `makeUIView` + `updateUIView` mindkettő alkalmazza a connection orientation-t |
| `SessionCaptureManager.swift` | Hardcoded `.portrait` eltávolítva `prepare()`-ből; `startCapture(captureOrientation:)` snapshot capture-start időben |
| `SessionCaptureOrchestrator.swift` | `fireScheduledCapture()` snapshotol, átadja a managernek |
| `MultiCameraLobbyView.swift` | `@State interfaceOrientation` + `UIDevice.orientationDidChangeNotification` + `CapturePreviewView` paraméter |

**Build:** `9111fd3b` — BUILD SUCCEEDED (0 error)  
**Unit tests:** 787/787 PASS (+7 ORI-01..07)

---

## 2. Orientation-mátrix — Fizikai teszteredmények

### 2.1 Device × Orientation alapesetek

| # | Device | Interface orientation | Preview | REC overlay | .mov playback | Verdict |
|---|--------|-----------------------|---------|-------------|---------------|---------|
| 1 | iPad | landscapeLeft/Right | ✅ helyes landscape | ✅ helyes pozíció | ✅ landscape | **PASS** |
| 2 | iPad | portrait | ✅ helyes portrait | ✅ helyes pozíció | ✅ portrait | **PASS** |
| 3 | iPhone | portrait | ✅ helyes portrait | ✅ helyes pozíció | ✅ portrait | **PASS** |
| 4 | iPhone | landscapeLeft | ✅ helyes landscape | ✅ helyes pozíció | ✅ landscapeLeft | **PASS** |
| 5 | iPhone | landscapeRight | ✅ helyes landscape | ✅ helyes pozíció | ✅ landscapeRight | **PASS** |

### 2.2 Lifecycle-specifikus esetek

| # | Teszteset | Eredmény | Megjegyzés |
|---|-----------|----------|------------|
| 6 | Forgatás recording **előtt** — portrait→landscape→capture | ✅ .mov landscape | PASS |
| 6b | Forgatás recording **előtt** — landscape→portrait→capture | ✅ .mov portrait | PASS |
| 7 | Forgatás aktív recording **közben** — recording orientation változatlan | ✅ PASS | Átmeneti hálózati hiba a Start gombnál (self-recovered, nem orientation-related) |
| 8 | Stop után **új felvétel** az új tájolással | ✅ .mov az új orientációt tükrözi | PASS |

---

## 3. iPhone vs iPad rotation — Szándékos viselkedéskülönbség

| | iPad | iPhone |
|-|------|--------|
| Támogatott tájolások | portrait, portraitUpsideDown, landscapeLeft, landscapeRight | portrait, landscapeLeft, landscapeRight |
| Teljes 360° forgás | ✅ igen | ❌ nem — landscapeLeft ↔ portrait ↔ landscapeRight |
| PortraitUpsideDown | ✅ engedélyezve | ❌ szándékosan kihagyva (Apple konvenció, Face ID compat) |

Ez **helyes és szándékos** viselkedés, nem hiba.

---

## 4. Megfigyelt incidensek

| Incidens | Típus | Orientation-related? | Státusz |
|----------|-------|----------------------|---------|
| Start gomb átmeneti hálózati hiba test 7 közben | Hálózati | ❌ Nem | Self-recovered, külön nyomozást nem igényel |

---

## 5. Összesített verdikt

**ORIENTATION-MÁTRIX: TELJES PASS**

- 5 device × orientation alapeset: mind PASS
- 3 lifecycle eset (rotation before/during/after): mind PASS
- Nincs 90°-os elfordulás, tükrözés, torzítás egyetlen esetben sem
- Preview és .mov orientation mindig egyezett
- `CaptureOrientationHelper` egységes mapping — preview layer és movie output azonos értéket kap

---

## 6. Következő lépés

**Capture-start timestamp drift instrumentáció + 10-ciklusos fizikai mérés**

- PR #332 merge: TILOS mindaddig, amíg a drift mérés nincs kész
- A drift mérést a `9111fd3b` HEAD-en kell elvégezni (ez a végleges capture pipeline)

---

*Report generated 2026-06-23. Physical test: iPad (instructor_primary) + iPhone (player_primary).*
