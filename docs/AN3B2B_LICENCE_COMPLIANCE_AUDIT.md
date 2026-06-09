# AN-3B2B — Licence / Compliance Audit — Ball Detection Model

Státusz: **AUDIT — döntés szükséges a továbbhaladáshoz.**  
Dátum: 2026-06-17.  
Kapcsolódó: `docs/AN3B2B_1_BALL_DETECTION_IMPLEMENTATION_PLAN.md` (v3).

---

## 0. Összefoglaló

Ez a dokumentum auditálja a ball detection pipeline-hoz szükséges modell és
dependency licenceket. A cél: kizárólag permissive licence-ú (MIT / Apache-2.0 /
BSD) komponensek használata, runtime publikus model download nélkül.

**Javasolt modell: ONNX Model Zoo — SSD MobileNet v1 (opset 12)**

---

## 1. Javasolt modell

### 1.1 Elsődleges javaslat: SSD MobileNet v1 (ONNX Model Zoo)

| Tulajdonság | Érték |
|---|---|
| **Modell neve** | `ssd_mobilenet_v1_12` |
| **Formátum** | ONNX (opset 12) |
| **Fájlméret** | ~29.5 MB |
| **Training dataset** | MS COCO 2017 |
| **Osztályok** | 91 COCO kategória (80 érdemi + background + reserved) |
| **Sports ball class** | COCO class ID **37** (`sports_ball`) |
| **Input méret** | 300×300 (SSD architecture) |
| **Output** | Max 10 detection per image (boxes, classes, scores) |

### 1.2 Miért NEM SSD MobileNet v2?

A TensorFlow Model Zoo-ban elérhető `ssd_mobilenet_v2_coco_2018_03_29` modell
frozen graph formátumú (.pb). Az ONNX-ba konvertáláshoz `tf2onnx` szükséges,
ami dokumentáltan problémás a v2 depthwise convolution-ökkel
([GitHub issue #1100](https://github.com/onnx/tensorflow-onnx/issues/1100),
[issue #1031](https://github.com/onnx/tensorflow-onnx/issues/1031)).

Az **ONNX Model Zoo natívan kínálja** a v1-et ONNX formátumban, tesztelten
és konverziós kockázat nélkül. A v1 és v2 közötti mAP különbség (~2-3%) nem
releváns a sports_ball detekció use case-ben.

### 1.3 Alternatívák (dokumentálás, nem javaslat)

| Modell | Licence | Probléma |
|---|---|---|
| YOLOv8n (Ultralytics) | **AGPL-3.0** | ❌ Nem permissive. Modellsúlyok is AGPL. |
| YOLO NAS-S (Deci AI) | Apache-2.0 (runtime) | ⚠️ A modellsúlyok licence nem egyértelmű. |
| EfficientDet-D0 (TF) | Apache-2.0 | ⚠️ Nincs natív ONNX; konverziós kockázat. |
| DETR (Facebook) | Apache-2.0 | ⚠️ ~160MB, túl nagy; PyTorch dependency. |

---

## 2. Pontos forrás URL / repository

### 2.1 ONNX modell fájl

| Elem | Érték |
|---|---|
| **Repository** | [onnx/models](https://github.com/onnx/models) (GitHub) |
| **Hugging Face mirror** | [onnxmodelzoo/ssd_mobilenet_v1_12](https://huggingface.co/onnxmodelzoo/ssd_mobilenet_v1_12) |
| **Közvetlen ONNX fájl** | `ssd_mobilenet_v1_12.onnx` (29.5 MB) |
| **Repo licence** | Apache-2.0 (a teljes ONNX Model Zoo repo) |
| **Modell licence** | Apache-2.0 (explicit a modell README-ben) |

### 2.2 Eredeti TensorFlow modell (a konverzió alapja)

| Elem | Érték |
|---|---|
| **Repository** | [tensorflow/models — detection model zoo](https://github.com/tensorflow/models/blob/master/research/object_detection/g3doc/tf1_detection_zoo.md) |
| **Eredeti forrás** | `http://download.tensorflow.org/models/object_detection/ssd_mobilenet_v1_coco_2018_01_28.tar.gz` |
| **Licence** | Apache-2.0 |

### 2.3 Kontrollált model source terv

A runtime **NEM tölt le modellt publikus URL-ről**. A modell letöltése egyszeri,
offline, fejlesztői/ops művelet:

```
scripts/download_ml_models.py --model ssd-mobilenet-v1-coco
  → Letölti: app/ml_models/ssd_mobilenet_v1_12.onnx
  → SHA256 ellenőrzés (hardcoded hash a scriptben)
  → Forrás: konfig-alapú (env BALL_DETECTION_MODEL_URL, default: Hugging Face HF Hub)
```

A production deployment-ben a modell fájl:
- Docker image-ba bake-elve (build-time download) VAGY
- Privát artifact storage-ból másolva (S3/GCS) VAGY
- Ops által manuálisan elhelyezve a szerverre

**Nincs runtime publikus URL hívás.**

---

## 3. Modell licence — részletes elemzés

### 3.1 ONNX Model Zoo repo licence

| Kérdés | Válasz |
|---|---|
| Repo licence | **Apache-2.0** |
| Modell-specifikus licence | **Apache-2.0** (README: "The original model is distributed under the Apache License, Version 2.0") |
| Módosítható? | Igen (Apache-2.0 megengedi) |
| Kereskedelmi felhasználás? | **Igen** |
| Attribution szükséges? | Igen — "Apache License" notice a licence fájlban / dokumentációban |

### 3.2 Training data (MS COCO) licence

| Kérdés | Válasz |
|---|---|
| COCO annotációk licence | **CC-BY 4.0** (kereskedelmi használat engedélyezett, attribution szükséges) |
| COCO képek licence | **Flickr ToS** (az eredeti fotósok licencei érvényesek) |
| Hatás a modellsúlyokra | A modellsúlyok **nem tartalmazzák** a képeket — a súlyok a tanult paraméterek. A TF Model Zoo Apache-2.0 alatt adja ki a modellsúlyokat, ami implicit lefedi a training data felhasználást. |
| Kockázat | **Alacsony.** A modellsúlyok Apache-2.0 alatti kiadása (Google/TensorFlow által) lefedi a kereskedelmi felhasználást. A COCO képek maguk nem kerülnek a rendszerbe. |

### 3.3 Összehasonlítás: YOLOv8n vs. SSD MobileNet v1

| Szempont | YOLOv8n (Ultralytics) | SSD MobileNet v1 (ONNX Zoo) |
|---|---|---|
| **Modellsúly licence** | AGPL-3.0 | Apache-2.0 |
| **Runtime dependency** | `ultralytics` (AGPL-3.0) | `onnxruntime` (MIT) |
| **Kereskedelmi használat** | ❌ Nem engedélyezett jogi clearance nélkül | ✅ Engedélyezett |
| **Training data** | COCO (CC-BY 4.0 + Flickr ToS) | COCO (CC-BY 4.0 + Flickr ToS) |
| **mAP (COCO val2017)** | ~37.3 | ~23.0 |
| **Fájlméret** | ~6.2 MB | ~29.5 MB |

---

## 4. Dependency licence — teljes stack

### 4.1 Jelenlegi dependencies (már a repo-ban)

| Package | Verzió | Licence | Státusz |
|---|---|---|---|
| `onnxruntime` | 1.26.0 | **MIT** | ✅ Permissive |
| `numpy` | (via onnxruntime) | **BSD-3-Clause** | ✅ Permissive |
| `Pillow` | ≥10.0.0 | **MIT-CMU** (HPND variant) | ✅ Permissive |

### 4.2 Új dependency (AN-3B2B-1-hez szükséges)

| Package | Verzió | Licence | Cél | Alternatíva |
|---|---|---|---|---|
| `opencv-python-headless` | ≥4.8.0 | **Apache-2.0** (OpenCV) + **MIT** (wrapper) | Frame extraction videóból | Pillow (csak képekhez, nem videóhoz) |

### 4.3 Konverziós tool (NEM runtime dependency, NEM requirements.txt)

| Package | Verzió | Licence | Cél |
|---|---|---|---|
| `tf2onnx` | ≥1.8.2 | **Apache-2.0** | Egyszerű offline konverzió (csak ha saját v2 export kellene) |

**Megjegyzés**: a `tf2onnx` NEM szükséges, mert az ONNX Model Zoo már kész ONNX
fájlt ad. Csak abban az esetben kellene, ha a TF frozen graph-ból saját konverziót
csinálnánk.

### 4.4 Licence összesítés

| Komponens | Licence | Permissive? |
|---|---|---|
| ONNX modell (ssd_mobilenet_v1_12) | Apache-2.0 | ✅ |
| onnxruntime | MIT | ✅ |
| numpy | BSD-3-Clause | ✅ |
| Pillow | MIT-CMU | ✅ |
| opencv-python-headless | Apache-2.0 | ✅ |
| COCO annotációk (training data) | CC-BY 4.0 | ✅ (attribution) |

**Nincs AGPL, GPL, LGPL, SSPL, vagy egyéb copyleft dependency a teljes stackben.**

---

## 5. Production használati kockázat

| # | Kockázat | Súlyosság | Mitigáció |
|---|---|---|---|
| 1 | COCO Flickr image licence kérdés | **Alacsony** | A modellsúlyok nem tartalmazzák a képeket. Google/TF Model Zoo Apache-2.0 alatt adta ki — implicit lefedi. |
| 2 | SSD MobileNet v1 mAP alacsonyabb mint YOLOv8n | **Közepes** | `no_ball_detected` flag + manuális override. Production-ben a confidence threshold hangolható per type. |
| 3 | Model fájl integritás (supply chain) | **Közepes** | SHA256 hash ellenőrzés a `download_ml_models.py` scriptben. Nem random publikus URL — kontrollált forrás. |
| 4 | ONNX Model Zoo repo maintenance | **Alacsony** | A modell fájl egy snapshot — nem függ a repo jövőbeli állapotától. Letöltés után lokálisan tárolt. |
| 5 | `opencv-python-headless` FFmpeg LGPL | **Alacsony** | A headless variant FFmpeg-et LGPL alatt szállítja, de az LGPL **nem copyleft a dinamikus linkelés szintjén** — a Python wheel binary-ként szállítja, a mi kódunk nem módosítja. |

---

## 6. Elfogadható-e zárt/commercial környezetben?

### Válasz: **IGEN**, az alábbi feltételekkel:

1. **Attribution**: a `LICENSE-THIRD-PARTY.md` (vagy hasonló) fájlban szerepelnie kell:
   - "SSD MobileNet v1 model: Apache-2.0, Copyright Google LLC"
   - "ONNX Model Zoo: Apache-2.0"
   - "OpenCV: Apache-2.0"
   - "COCO dataset annotations: CC-BY 4.0, attribution to Microsoft COCO"

2. **Apache-2.0 NOTICE fájl**: ha az Apache-2.0 licencű komponensekhez NOTICE fájl
   tartozik, annak tartalmát meg kell őrizni a disztribúcióban.

3. **Nincs forráskód-megosztási kötelezettség**: sem az Apache-2.0, sem az MIT nem
   igényli a saját kód nyílt forráskódú megosztását.

---

## 7. Szükséges-e jogi jóváhagyás?

### 7.1 A javasolt stack (SSD MobileNet v1 + onnxruntime + opencv) esetén

| Kérdés | Válasz |
|---|---|
| Szükséges-e formális jogi review? | **Ajánlott, de nem blokkoló.** |
| Miért ajánlott? | A COCO training data Flickr ToS rétege elméleti kockázat. A practice-ban a TF Model Zoo Apache-2.0 kiadása ezt lefedi, és az iparági konszenzus (Google, Microsoft, Meta, NVIDIA mind használja production-ben) erős precedens. |
| Mi kellene a jogi review-hoz? | Ez a dokumentum + a LICENSE-THIRD-PARTY.md draft |

### 7.2 A YOLOv8n / Ultralytics esetén (NEM javasolt)

| Kérdés | Válasz |
|---|---|
| Szükséges-e formális jogi review? | **IGEN, kötelező.** |
| Miért kötelező? | AGPL-3.0 modellsúlyok + AGPL-3.0 runtime. Az AGPL megköveteli a teljes forráskód megosztását, ha a szoftver hálózaton keresztül szolgáltat. |
| Elfogadható zárt környezetben? | **NEM**, kivéve Ultralytics Enterprise licence vásárlásával. |

---

## 8. Összefoglaló döntési mátrix

| Opció | Modell | Licence | Commercial OK? | Jogi review? | Javaslat |
|---|---|---|---|---|---|
| **A) (JAVASOLT)** | SSD MobileNet v1 (ONNX Zoo) | Apache-2.0 | ✅ Igen | Ajánlott | ✅ |
| B) | SSD MobileNet v2 (TF → tf2onnx) | Apache-2.0 | ✅ Igen | Ajánlott | ⚠️ Konverziós kockázat |
| C) | YOLOv8n (Ultralytics) | AGPL-3.0 | ❌ Nem | **Kötelező** | ❌ |
| D) | YOLO NAS-S (Deci AI) | Nem egyértelmű | ⚠️ Kérdéses | **Kötelező** | ❌ |

---

## 9. Szükséges lépések a jóváhagyás után

Ha az A) opció elfogadva:

1. `LICENSE-THIRD-PARTY.md` fájl létrehozása az attribution notice-okkal
2. `scripts/download_ml_models.py` implementálása SHA256 hash-sel
3. `BALL_DETECTION_MODEL_PATH` config frissítése: `ssd_mobilenet_v1_12.onnx`
4. `detection_source` CHECK constraint frissítése: `'mobilenet_ssd_v1'` (v1, nem v2)
5. `AnalysisModelConfig` registry frissítése az ONNX Zoo modell paramétereivel

---

## 10. Források

- [ONNX Model Zoo — SSD MobileNet v1 (Hugging Face)](https://huggingface.co/onnxmodelzoo/ssd_mobilenet_v1_12)
- [ONNX Model Zoo — GitHub repo](https://github.com/onnx/models) (Apache-2.0)
- [TensorFlow Model Zoo — SSD MobileNet v1 COCO](https://github.com/tensorflow/models/blob/master/research/object_detection/g3doc/tf1_detection_zoo.md) (Apache-2.0)
- [OpenVINO — SSD MobileNet v1 COCO model card](https://docs.openvino.ai/2023.3/omz_models_model_ssd_mobilenet_v1_coco.html)
- [COCO Dataset](https://cocodataset.org/) — annotációk: CC-BY 4.0
- [opencv-python-headless (PyPI)](https://pypi.org/project/opencv-python-headless/) — Apache-2.0
- [onnxruntime (PyPI)](https://pypi.org/project/onnxruntime/) — MIT
- [tf2onnx (PyPI)](https://pypi.org/project/tf2onnx/) — Apache-2.0
- [COCO 91 class labels](https://gist.github.com/9d00c4683d52d94cf348acae29e8db1a) — sports_ball = class 37
- [COCO dataset licence analysis (arXiv)](https://arxiv.org/pdf/2303.13735)

---

**Licence audit elfogadásra vár. Implementáció (Functional Ball Detection Pipeline)
csak a jóváhagyás után indulhat.**
