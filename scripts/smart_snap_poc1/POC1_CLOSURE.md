# Smart Snap POC-1 — Kutatás Lezárása

**Dátum:** 2026-06-20  
**Branch:** `feat/smart-snap-poc1` (PR #312, draft/research)  
**Verdict:** REJECT CURRENT SNAP METHODS

---

## Mi működött

- **Annotációs infrastruktúra** — Az eseményvezérelt v2 annotátor (GT_R1 → GT_R2 → RAW_TAP×3 → LOUPE_TAP×3, modell rejtve GT fázisban, zoom/pan, incrementális mentés) stabilan működik; 60/60 frame annotálva, 40 valódi humán GT koordinátával.
- **Benchmark pipeline** — Az offline Python/OpenCV benchmark (00→01→02b→05→03→04) reprodukálható, GT-független, adatbázisba nem ír.
- **Manual loupe UX validálva** — A jelenlegi Train AI loupe + marker + Megerősítés flow átlagos hibája 7.4 px (holdout, 1920px referencia). Ez az a pontossági szint, amellyel az automatikus módszereknek kellene versenyezniük.
- **Adatminőség auditált** — A 60 frame vizuálisan felülvizsgált és szub-kategorizált; 26 helytelen provenencia javítva (köztük 6 DB lost-state frame amelyekben ténylegesen labda volt).

---

## Mi bukott meg

### Algoritmusok (holdout eredmények)

| Módszer | Mean error | Wrong-snap vs M2_raw | No-ball FP |
|---|---|---|---|
| M3 Stored SSD | 211 px | 75.0% | 80.0% |
| M4 Contour (Canny) | 333 px | 66.7% | 40.0% |
| M5 Hough Circle | 217 px | 62.5% | 100% |
| M6 Template Match | 338 px | 100% | 100% |
| **M2 Loupe (emberi)** | **7.4 px** | 0% | 0% |

**Minden automatikus módszer elutasítva:**
- Wrong-snap gate (< 5%): 62–100% — FAIL
- No-ball FP gate (< 10%): 40–100% — FAIL
- Az algoritmusok az esetek 62–100%-ában **rontanak** a felhasználó eredeti pozícióján.

### Adatminőség problémák

- A `tracking_state=lost` DB mező nem egyenlő azzal, hogy a frame-ben nincs labda; a Type C frameok 80%-ában ténylegesen labda volt.
- A `5416b28f` videó (egyensúly-deszka edzés) és a `66ee1c8a` képernyőrögzítések (videolejátszó UI overlay) nem juggling tartalom — ezek adatgyűjtési gap-et jeleznek.

---

## Miért biztonságosabb a jelenlegi manual loupe

1. **Pontosság**: 7.4 px átlagos hiba vs 211–338 px automatikus módszerekkel. Nincs olyan algoritmus, amely megközelíti.
2. **Wrong-snap veszély nulla**: A loupe flow-ban a felhasználó mindig manuálisan erősíti meg a pozíciót. Automatikus snap esetén a 75%+ wrong-snap rate aktív rontást jelent.
3. **No-ball biztonság**: A loupe soha nem jelöl labdát no-ball frame-eken. Az automatikus módszerek 40–100%-os FP rátával tévesen jelölnek labdát.
4. **Adatminőség**: A loupe által gyűjtött pozíciók közvetlen humán GT-ként használhatók jövőbeli modell tanításhoz.

---

## Mikor érdemes újranyitni a Smart Snap kutatást

A következő feltételek **mind** szükségesek:

1. **Célzott ball-detection modell** — A jelenlegi SSD/Hough/contour módszerek általános célú CV technikák; egy kifejezetten juggling labda detektálásra tanított modell (pl. YOLOv8 fine-tuned a saját adatokon) szükséges.
2. **Elegendő tanítási adat** — Legalább 500–1000 frame valódi humán loupe annotációval, eloszolva a hiányzó kategóriákra is (motion_blur, partial_occlusion, small_ball).
3. **Hiányzó kategóriák lefedve** — A jelenlegi datasetből hiányoznak: `motion_blur`, `partial_occlusion`, `small_ball`. Ezek nélkül a robusztusság nem értékelhető.
4. **iOS natív validáció** — A Python/OpenCV eredmények nem prediktívek az iOS Vision framework teljesítményére; POC-2-nek fizikai iPhone benchmarkot kell tartalmaznia.
5. **Wrong-snap guard** — Bármely jövőbeli implementációban a model confidence-nek > 0.85 kell legyen az automatikus snap alkalmazásához; különben a kézi loupe flow az alapértelmezett.

---

## Következő lépések (termékfejlesztési roadmap)

- **Train AI flow változatlan marad**: loupe + marker + Megerősítés
- **Adatgyűjtés folytatódik** a jelenlegi flow-val; minden loupe pozíció potenciális tanítási adat
- **Smart Snap kutatás zárva** — nem nyílik újra legalább 500 valódi humán annotáció és célzott modell nélkül
- **PR #312 research/draft** — nem merge-elhető mainre jelenlegi formában

---

*Dokumentum: scripts/smart_snap_poc1/POC1_CLOSURE.md*  
*Teljes riport: scripts/smart_snap_poc1/report.md*  
*Constraint: No automatic snap production integration. No main merge. No backend snap endpoint.*
