# AN-3B2D Ball Detection Model Strategy Audit

**Dátum:** 2026-06-18  
**Állapot:** Döntés-előkészítő — implementáció BLOKKOLVA jóváhagyásig  
**Vonatkozik:** `app/services/juggling/onnx_ball_detector.py`, `app/ml_models/ssd_mobilenet_v1_12.onnx`

---

## Összefoglalás

A jelenlegi SSD MobileNet v1 (COCO-pretrained) ball detector **~13%-os detekciós rátát** mutat a juggling videókon (28 detected / 214 frame). Ez product célra alkalmatlan. A gyenge teljesítmény nem kizárólag a modell architektúrájának hibája — az elsődleges ok az, hogy **generikus COCO-modellt alkalmazunk domain-specifikus, kis, gyorsan mozgó labda detektálására**.

A dokumentum két irányt vizsgál: kész permissive-licence modellek, és saját modell tanítása.

---

## 1. Jelenlegi helyzet diagnózisa

### 1.1 A jelenlegi rendszer

| Összetevő | Részlet |
|-----------|---------|
| Model | SSD MobileNet v1 (ONNX Model Zoo) |
| Licence | **Apache-2.0** |
| Modell fájl | `app/ml_models/ssd_mobilenet_v1_12.onnx` (28 MB) |
| Input | 300×300 px, uint8 RGB |
| Detektált osztály | COCO class 37 = `sports_ball` |
| Confidence threshold | 0.30 |
| COCO mAP (sports_ball) | ~20 AP (COCO 2017 val) |
| Megfigyelt detekciós ráta | **~13%** (28/214 frame, test videó) |
| Kalman tracker | constant-velocity 2D, max_miss=5 |

### 1.2 Miért gyenge a teljesítmény?

A probléma **négy egymást erősítő ok kombinációja:**

1. **Generikus training adat.** A COCO `sports_ball` osztály tartalmaz kosárlabdát, teniszlabdát, futball-labdát, röplabdát, golfgolfot. A modell nem szakosodott futball-labdára.

2. **Kis objektum.** Juggling videón a labda tipikusan a frame terület 0.1–0.5%-át foglalja el (30–80px átmérő 720p felbontáson). Az SSD MobileNet v1 300×300 inputja eltörpíti a kis labdákat.

3. **Mozgás-blur.** 10 FPS mintavétel mellett 100 ms ablakokban a gyors juggling mozdulat elmosódott labdát eredményez — a `sports_ball` COCO training adatban ez ritka.

4. **Elavult architektúra.** Az SSD MobileNet v1 2017-es architektúra. Modern anchor-free és transformer-alapú detektorok lényegesen jobbak kis objektumokra.

---

## 2. Rész 1 — Elérhető kész modellek

### 2.1 TILTOTT modellek (licence clearance nélkül)

| Model | Licence | Ok |
|-------|---------|-----|
| YOLOv5 (Ultralytics) | **AGPL-3.0** | Kereskedelmi tiltott |
| YOLOv8 (Ultralytics) | **AGPL-3.0** | Kereskedelmi tiltott |
| YOLOv9 (Ultralytics) | **AGPL-3.0** | Kereskedelmi tiltott |
| YOLOv10 (Ultralytics) | **AGPL-3.0** | Kereskedelmi tiltott |
| YOLOv7 (WongKinYiu) | **GPL-3.0** | Kereskedelmi tiltott |
| YOLOv6 (Meituan) | **GPL-3.0** | Kereskedelmi tiltott |
| YOLO-World | **GPL-3.0** | Kereskedelmi tiltott |
| GOLD-YOLO | **GPL-3.0** | Kereskedelmi tiltott |

**Bármelyik AGPL/GPL modell csak külön, írásos kereskedelmi licence megvásárlásával használható. Ez nem automatikus — implementáció előtt külön jóváhagyás szükséges.**

### 2.2 Permissive licence modellek összehasonlítása

| Model | Licence | COCO mAP | Modell méret | Input | CPU inf. (server) | ONNX export | iPhone fut. | Integrációs nehézség |
|-------|---------|-----------|--------------|-------|-------------------|-------------|-------------|---------------------|
| **SSD MobileNet v1** *(jelenlegi)* | Apache-2.0 | ~23 AP | 28 MB | 300×300 | ~30ms | ✓ kész | CoreML via coremltools | — |
| **SSD MobileNet v2** | Apache-2.0 | ~25 AP | ~34 MB | 300×300 | ~40ms | ✓ kész | CoreML via coremltools | Alacsony |
| **EfficientDet-D0** | Apache-2.0 | 33.8 AP | ~16 MB | 512×512 | ~90ms | ✓ (tf2onnx) | CoreML via coremltools | Közepes |
| **EfficientDet-D1** | Apache-2.0 | 39.6 AP | ~21 MB | 640×640 | ~170ms | ✓ (tf2onnx) | CoreML via coremltools | Közepes |
| **YOLO-NAS-S** | **Apache-2.0** | 47.5 AP | ~26 MB | 640×640 | ~80ms | ✓ (super-gradients) | CoreML via coremltools | Közepes |
| **YOLO-NAS-M** | **Apache-2.0** | 51.0 AP | ~62 MB | 640×640 | ~140ms | ✓ (super-gradients) | Nehézkes (nagy) | Közepes |
| **RT-DETR-R18** | Apache-2.0 | 46.5 AP | ~76 MB | 640×640 | ~150ms | ✓ (PaddlePaddle) | Nehézkes | Magas |
| **NanoDet-Plus-m** | Apache-2.0 | 34.5 AP | ~5 MB | 416×416 | ~20ms | ✓ | CoreML natívan | Alacsony |
| **PP-YOLOE-S** | Apache-2.0 | 43.1 AP | ~23 MB | 640×640 | ~90ms | ✓ (PaddlePaddle) | CoreML via coremltools | Közepes |
| **Roboflow Universe futball modellek** | CC-BY 4.0 | domain-specifikus | változó | változó | változó | opcionális | opcionális | Alacsony (API) |

*COCO mAP értékek: COCO 2017 val, teljes osztálykészletre. `sports_ball` specifikus mAP ezek töredéke.*

### 2.3 Modell részletes értékelés

#### SSD MobileNet v2 (Apache-2.0)

- **Előny:** Minimális integrációs változás — azonos ONNX Model Zoo forrás, közel azonos output interface. Már elérhető a Model Zoo-ban.
- **Hátrány:** Csak ~10% javulás v1 felett. Generikus COCO training marad.
- **Várható detekciós ráta juggling videón:** ~15–20%.
- **Döntés:** Nem érdemes váltani, ha domain-specifikus finomhangolás nélkül alkalmazzuk.

#### EfficientDet-D0 (Apache-2.0)

- **Előny:** Lényegesen jobb kis objektum detekció a BiFPN neck architektúra miatt. 512×512 input jobban megtartja a kis labda pixeleit.
- **Hátrány:** TensorFlow-natív, ONNX exporthoz `tf2onnx` konverzió szükséges. ~3× lassabb, mint az SSD v1 (CPU).
- **Várható detekciós ráta juggling videón:** ~25–35% (generikus), ~55–70% fine-tune után.
- **Integrációs változás:** Új input szét kell nézni (float32 vs uint8), output schema eltér.

#### YOLO-NAS-S (Apache-2.0) ← erős jelölt

- **Licence:** Apache-2.0, **beleértve a pretrained weights-et** — ez kritikusan fontos. A SuperGradients library (Deci.ai/NVIDIA) és a súlyok is Apache-2.0 alatt publikusak.
- **Előny:** State-of-the-art pontosság permissive licence-en. 47.5 COCO AP. Anchor-free, erős kis objektum teljesítmény. Natív ONNX export `super-gradients` library-vel.
- **Hátrány:** SuperGradients Python dependency hozzáadása szükséges (csak exporthoz, inferenciához nem — az ONNX Runtime marad).
- **Várható detekciós ráta juggling videón:** ~35–45% (generikus COCO), ~70–85% domain-specifikus fine-tune után.
- **Modell méret:** ~26 MB (hasonló az SSD v1-hez).
- **Integrációs változás:** ONNX export után az `OnnxBallDetector` osztály minimálisan módosítandó (input/output schema).
- **Licence kockázat:** Alacsony, de javasolt jogi megerősítés a SuperGradients pretrained weights licence státuszáról (Apache-2.0 mint ONNX Model Zoo-nál).

#### NanoDet-Plus-m (Apache-2.0) ← mobil-első alternatíva

- **Előny:** Extrém kis modell (5 MB). Kiváló CPU-sebesség (~20ms/frame). Alkalmas iPhone on-device futtatásra is CoreML-en keresztül.
- **Hátrány:** Kisebb COCO mAP mint az EfficientDet vagy YOLO-NAS. Kisebb community, kevesebb futball-specifikus fine-tuned változat.
- **Alkalmazás:** Ha iPhoneön is futtatni akarjuk (nem csak backenden) — ez a reálisabb méret.

#### Roboflow Universe futball-specifikus modellek (CC-BY 4.0)

- A Roboflow Universe-n számos közösség által annotált futball-labda detektáló modell elérhető.
- **Licence:** Jellemzően CC-BY 4.0 — kereskedelmi célra felhasználható, de attribúció szükséges.
- **Előny:** Már domain-specifikus training adattal készültek. 60–80%-os detekciós rátát mutatnak futball-videókon.
- **Hátrány:** A modellek minősége változó. Nem mindig ismert a training adat forrása és minősége. Némelyik Ultralytics-alapú (AGPL) — alapos licence ellenőrzés szükséges az egyes modellekre.
- **Ajánlott megközelítés:** Auditálni az adott modell backbone architektúráját és training kódját — ne csak a "CC-BY" label alapján dönteni.

---

## 3. Rész 2 — Saját modell tanítása

### 3.1 Reális elvárás

Saját, juggling-specifikus adaton tanított modell várhatóan **70–90%-os detekciós rátát** érhet el, mivel:
- Az annotált adatok pontosan a mi kameraállásainkból, megvilágítási körülményeink között, a mi labdánkkal készülnek.
- A modell nem "zavarja meg" a kosárlabda, teniszlabda és golfgolf képekkel.

### 3.2 Minimálisan szükséges annotált adat

| Célminőség | Szükséges annotált frame | Megjegyzés |
|-----------|--------------------------|-----------|
| Baseline (proof-of-concept) | 300–500 | Elfogadható, ha homogén a videóanyag |
| Production-ready | 1 500–3 000 | Különböző fényviszony, kameraállás, labdatípus |
| Robosztus általánosítás | 5 000+ | Több felhasználó, különböző helyszín |

Az 500 frame elég egy gyors validációs kísérlethez, amely megmutatja, hogy domain fine-tune tényleg javít-e.

### 3.3 Annotációs formátum

**Javasolt:** COCO JSON, mert az EfficientDet és YOLO-NAS egyaránt elfogadja, és konvertálható más formátumba.

```json
{
  "images": [{"id": 1, "file_name": "frame_000100.jpg", "width": 1280, "height": 720}],
  "annotations": [{
    "id": 1,
    "image_id": 1,
    "category_id": 1,
    "bbox": [620, 340, 45, 45],
    "area": 2025,
    "iscrowd": 0
  }],
  "categories": [{"id": 1, "name": "football"}]
}
```

A `bbox` formátum: `[x_topleft, y_topleft, width, height]` pixelben.

### 3.4 Hogyan használhatók a meglévő manual seed adatok

A `juggling_ball_trajectories` tábla tartalmazza:
- `frame_ms`: frame időbélyeg → konvertálható frame indexre
- `ball_x`, `ball_y`: normalizált középpontkoordináta [0, 1]
- `image_width_px`, `image_height_px`: frame mérete (ha kitöltött)
- `is_manual = true`: felhasználó által megerősített pozíció
- `tracking_state = 'detected'`: magas confidence auto-detekció

**Korlát:** A manual seed és detektált pontok csak középpontot tárolnak, bounding box méretet nem. Az annotációhoz szükséges lenne a labda sugarát (kb. 20–40px 720p-n) hozzábecsülni, vagy retroaktívan visszanézni a frame-eket.

**Javasolt pipeline:**
1. Exportálni a `is_manual = true` és `tracking_state = 'detected'` pontokat (legalább 200 pont szükséges kezdőnek).
2. Frame extractionnel kimenteni a megfelelő képkockákat.
3. Annotáló eszközben (CVAT) ellenőrizni és korrigálni a bbox-okat.
4. A manuálisan korrektált pontokat training data-ként használni.

**Ez a meglévő adat csak kiindulópontként megfelelő** — az automatikusan detektált pontok 46%-os átlagos confidence-sel rendelkeznek, és sok false positive lehet köztük. Minden frame-et emberi ellenőrzéssel kell validálni.

### 3.5 Teljes training pipeline

```
1. Frame extraction
   - script: scripts/extract_training_frames.py (elkészítendő)
   - tool: cv2.VideoCapture, minden 5-10 frame-et kimenteni
   - output: JPEG frame-ek, 720p, max 30 fps

2. Annotáció
   - eszköz: CVAT (Apache-2.0, self-hosted vagy cloud)
     alternatíva: Label Studio (Apache-2.0)
   - feladat: bounding box rajzolása minden labdára
   - export: COCO JSON formátum
   - prioritás: manual seed által jelölt frame-ek ellenőrzése

3. Train/val/test split
   - 70% / 15% / 15%
   - stratifikáció: felhasználónként, ne ugyanazon videóból kerüljön egyszerre train és test-be

4. Modell training
   - architektúra: EfficientDet-D0 (Apache-2.0) vagy YOLO-NAS-S (Apache-2.0)
   - framework: TensorFlow2 (EfficientDet) vagy SuperGradients (YOLO-NAS)
   - kiindulópont: COCO pretrained weights + domain fine-tune (transfer learning)
   - batch size: 16–32
   - epochs: 50–100 (early stopping patience=10)
   - augmentáció: horizontal flip, random crop, brightness/contrast jitter, motion blur (fontos!)

5. ONNX export
   - EfficientDet: tf2onnx konverzió
   - YOLO-NAS: super-gradients natív export
   - validáció: ONNX Runtime inferencia vs. framework output különbség < 0.01

6. Validáció metrikai
   - detection_rate = detected_frames / total_frames (fő metrika)
   - mAP@0.5 (IoU threshold)
   - false_positive_rate = FP / (FP + TN frames)
   - avg_confidence (detected pontokra)
   - small_ball_recall (< 40px átmérő)
   - tracking_stability = predicted_frames / (detected + predicted)
```

### 3.6 Szükséges infrastruktúra

| Fázis | Szükséges | Megjegyzés |
|-------|-----------|-----------|
| Frame extraction | Jelenlegi szerver (cv2) | Már megvan |
| Annotáció | CVAT (web UI) vagy Roboflow | CVAT önhosztolható, ingyenes |
| Training | GPU-s szerver vagy cloud | **Ez a hiányzó elem** |
| GPU ajánlás | NVIDIA A100/A10 (~$3/h cloud) | 500 frame fine-tune: ~2–4 óra |
| ONNX export | CPU-n is mehet | Már megvan az ONNX Runtime |
| Validáció | Jelenlegi szerver | scripts/ball_detection_audit.py kiterjeszthető |

**Google Colab Pro** elegendő a proof-of-concept training-hez (500 frame, EfficientDet-D0).

### 3.7 Ajánlott modellarchitektúra saját tanításhoz

**Elsődleges: EfficientDet-D0 (Apache-2.0)**
- Indok: jó dokumentáció, aktív közösség, COCO pretrained weights szabadon elérhetők, 512×512 input kedvező kis labdák esetén, viszonylag gyors CPU-n.
- TFLite export lehetséges → iPhone on-device futtatás opcionális.

**Alternatíva: YOLO-NAS-S (Apache-2.0)**
- Indok: magasabb pontossági plafon, könnyebb ONNX export, ActiveInference licence tiszta.
- SuperGradients fine-tune API egyszerűbb, mint a TF2 automl pipeline.

---

## 4. Összehasonlító döntési mátrix

| Kritérium | SSD v1 (jelenlegi) | SSD v2 | EfficientDet-D0 | YOLO-NAS-S | Saját fine-tune |
|-----------|-------------------|--------|-----------------|------------|-----------------|
| **Detekciós ráta (juggling)** | ~13% | ~18% | ~30% | ~40% | ~70-85% |
| **Integrációs nehézség** | — | Alacsony | Közepes | Közepes | Magas |
| **Licence kockázat** | Nincs | Nincs | Nincs | Alacsony* | Nincs (saját adat) |
| **Modell méret (ONNX)** | 28 MB | 34 MB | 16 MB | 26 MB | ~16-26 MB |
| **CPU inferencia (server)** | ~30ms | ~40ms | ~90ms | ~80ms | ~90ms |
| **Kalman stabilitás** | Gyenge (kevés seed) | Gyenge | Közepes | Jó | Kiváló |
| **iPhone futtatás** | Nem tervezett | Nem tervezett | CoreML lehetséges | CoreML lehetséges | CoreML lehetséges |
| **Implementáció idő** | 0 | 1 nap | 2–3 nap | 2–3 nap | 2–4 hét |

*YOLO-NAS Apache-2.0 licence ellenőrzése javasolt jogi megerősítéssel.

---

## 5. Konkrét javaslatok

### 5.1 Rövid táv — melyik kész modellre érdemes váltani

**Javaslat: YOLO-NAS-S (Apache-2.0) + domain fine-tune nélkül, kísérletként.**

Indoklás:
- A generikus COCO mAP 47.5 AP vs SSD v1 23 AP — közel kétszeres pontossági plafon.
- Ugyanolyan licence tisztaság (Apache-2.0).
- ONNX export minimális változtatással integrálható a meglévő `OnnxBallDetector` osztályba.
- Ha a generikus YOLO-NAS-S is csak ~40%-ot hoz juggling-on, ez megerősíti, hogy **domain fine-tune nélkül nem megoldható a probléma** — és ez maga is értékes döntési adat.

**Alternatíva rövid távra:** EfficientDet-D0, ha YOLO-NAS licence-megerősítés késik.

**Amit nem érdemes rövid távon tenni:** SSD v2-re váltani — nem hoz szignifikáns javulást.

### 5.2 Középtáv — saját modell tanítása

**Javaslat: EfficientDet-D0 fine-tune, 500 manuálisan annotált frame-mel, Google Colab Pro-n.**

Ütemezés:
1. **1. hét:** Frame extraction script + CVAT setup + 200–300 frame annotáció (manual seed-ek ellenőrzése prioritás).
2. **2. hét:** 200–300 additional frame annotáció, train/val split, EfficientDet-D0 fine-tune indítása.
3. **3. hét:** ONNX export, integráció, A/B teszt az SSD v1 vs. fine-tuned modellel a meglévő trajectory task-on.
4. **4. hét:** Ha detection_rate > 50% → production deployment. Ha nem → YOLO-NAS-S fine-tune kísérlet.

### 5.3 Leggyorsabb validációs kísérlet

**48 órán belül elvégezhető:**

1. Letölteni a YOLO-NAS-S ONNX modelt (SuperGradients `model.export()`).
2. Módosítani az `OnnxBallDetector.detect()` metódust az új input/output schemához.
3. Futtatni a `run_dense_ball_trajectory()` task-ot a meglévő test videón (`86d01f49`).
4. Összehasonlítani: detected/predicted/lost arány a jelenlegi 28/57/129-hez képest.

Ha a YOLO-NAS-S générique COCO-n is 40%+ detekciót hoz → megerősíti az architektúra-váltás értékét.  
Ha nem → megerősíti, hogy csak domain fine-tune segít.

Ez a kísérlet **nem igényel training infrastruktúrát** — csak a pretrained ONNX weights letöltését és az inference kód minimális módosítását.

### 5.4 Mi marad tiltott (licence clearance nélkül)

| Tiltott | Ok |
|---------|----|
| YOLOv5, YOLOv8, YOLOv9, YOLOv10 (Ultralytics) | AGPL-3.0 |
| YOLOv7 (WongKinYiu) | GPL-3.0 |
| YOLOv6 (Meituan) | GPL-3.0 |
| YOLO-World | GPL-3.0 |
| Bármely Roboflow Universe modell, amelynek backbone-ja Ultralytics | AGPL-3.0 (rejtett függőség) |
| TrackNet-v2/v3 (shuttle/ball tracking) | MIT, de nem kereskedelmi adatokra tanítva — ellenőrzendő |

**Fontos:** Egy "CC-BY" Roboflow-modell is lehet AGPL-tiltott, ha az alapjául szolgáló training kód Ultralytics-alapú. A backbone és a training framework licence-ét is ellenőrizni kell, nem csak a weights licence-t.

### 5.5 Szükséges döntések implementáció előtt

Az alábbi döntések **jóváhagyás nélkül nem indulhat implementáció:**

| # | Döntés | Opciók |
|---|--------|--------|
| D1 | **Kísérlet engedélyezése** | YOLO-NAS-S ONNX letöltése + inference teszt (48h, nincs training) |
| D2 | **YOLO-NAS-S licence megerősítés** | Jogi ellenőrzés: SuperGradients pretrained weights Apache-2.0 státusza |
| D3 | **Annotációs infrastruktúra** | CVAT self-hosted vs. Roboflow (proprietary annotáció tool, de export szabad) |
| D4 | **Training GPU** | Google Colab Pro ($9.99/hó) vs. saját szerver vs. cloud GPU |
| D5 | **Fine-tune prioritás** | EfficientDet-D0 vs. YOLO-NAS-S (architekturális döntés) |
| D6 | **iPhone on-device futtatás** | Backenden fut-e marad (ONNX), vagy phone-ra is akarjuk (CoreML) |

---

## 6. Kockázatok és nyitott kérdések

| Kockázat | Valószínűség | Hatás | Mitigáció |
|----------|-------------|-------|-----------|
| YOLO-NAS-S sem hoz 40%+ juggling-on | Közepes | Közepes | Fine-tune kötelező lesz |
| 500 frame annotáció nem elegendő | Alacsony-Közepes | Magas | Iteratív adat-bővítés tervezett |
| EfficientDet-D0 CPU inferencia túl lassú | Alacsony | Közepes | NanoDet-Plus-m fallback |
| Annotáció minősége gyenge (manual seed alapú bbox) | Közepes | Magas | CVAT emberi ellenőrzés kötelező |
| Labda mérete videónként nagyon változik | Alacsony | Közepes | Multi-scale augmentáció training-ben |

---

## 7. Nem vizsgált, de releváns alternatívák (jövőbeli audit)

- **Apple Vision sportlabda-detekció:** iOS 17+ `VNRecognizeAnimalsRequest` mintájára, ha Apple kiad sport-specifikus Vision modellt (nem létezik 2026-ban).
- **TrackNetV2 (MIT):** Tollaslabda/teniszlabda pályakövetés — kisebb gyors objektumra optimalizált, de nem generic detector; football-ra való adaptálása nem triviális.
- **ByteTrack + EfficientDet:** Multi-object tracking keretrendszer (MIT) + EfficientDet detektorral — jobb hosszú-ablak tracking stabilitást adhat a Kalman filter mellett.
- **Foundation models (Grounding DINO, Apache-2.0):** Zero-shot "football" szöveges promptra — lassú CPU-n, de fine-tune nélkül kipróbálható.

---

*Implementáció kizárólag a D1–D6 döntések jóváhagyása után indulhat.*
