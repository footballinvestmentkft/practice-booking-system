# GoPro HERO13 Live Preview — Architektúra-frissítés + POC terv

**Dátum:** 2026-06-29
**Státusz:** TERVEZÉSI DOKUMENTUM — POC jóváhagyás után, implementáció külön jóváhagyással.
**Trigger:** Termékkövetelmény-pontosítás — az instructor dashboardnak minden connected
kamera (instructor iPhone, player iPad/iPhone, GoPro HERO13) élőképét egyszerre kell
megjelenítenie. A `GoProConnectionManager.state == .ready` connection-state, nem live
feed — ez jelenleg nem teljesül a GoPro oldalán.

---

## 1. Mely meglévő dokumentumok elavultak ebben a pontban

| Dokumentum | Mi van benne most | Mi hiányzik / elavult |
|---|---|---|
| [docs/MULTICAMERA_3D_IOS_ROADMAP.md](MULTICAMERA_3D_IOS_ROADMAP.md) §"Videó és metaadat átvitel" (65-77. sor) | Kizárólag record-then-download modell: `shutter/start` → lokális rögzítés → `GET /gopro/media/list` utólagos letöltés | Nem tartalmaz semmilyen élő preview útvonalat; ez volt a hallgatólagos feltevés, hogy a GoPro csak utófeldolgozásra kell |
| [docs/AN3B_PR4B_GOPRO_CAPTURE_POC_PLAN.md](AN3B_PR4B_GOPRO_CAPTURE_POC_PLAN.md) §II "GoPro vezérlés" | Ugyanaz a record-then-download minta, BLE+WiFi+HTTP audit | Ugyanaz a hiányosság |

**Javasolt módosítás mindkét dokumentumban**: egy rövid figyelmeztető blokk a dokumentum
elejére, ami jelzi, hogy a record-then-download modell **a videó archívum/utófeldolgozás
útja**, és **nem helyettesíti** az instructor dashboard live preview követelményét — az
utóbbira ez a dokumentum (`GOPRO_LIVE_PREVIEW_POC_PLAN.md`) az irányadó. Nem törlöm vagy
írom át a record-then-download leírást, mert az a capture/upload pipeline szempontjából
továbbra is érvényes és helyes — a két útvonal **egymás mellett él**, nem egymást
helyettesíti (lásd 3. pont).

---

## 2. Új architektúra-döntés (ADR)

**Döntés**: A GoPro HERO13 **két, egymástól független adatútvonalat** szolgáltat egyidejűleg:

1. **Capture/archívum útvonal** (meglévő, dokumentált, érintetlen marad):
   `shutter/start` → on-camera H.264 rögzítés SD-kártyára → session végén `media/list` +
   HTTP fájl-letöltés → szerver feldolgozás (skeleton, GPMF telemetria).
2. **Live preview útvonal** (új, ez a dokumentum):
   `stream/start` → GoPro UDP unicast H.264/MPEG-TS elemi stream a portra → iPhone-on
   dekódolás → instructor dashboard render. **Csak megjelenítésre**, nem mentésre — a
   preview frame-ek nem kerülnek perzisztálásra, nem helyettesítik a kártyára rögzített
   master felvételt.

Ez két különálló GoPro HTTP API hívás (`stream/start` és `shutter/start` egymástól
függetlenül indíthatók/állíthatók le), tehát **nem kioltják egymást** — a GoPro
egyszerre tud helyi kártyára rögzíteni és élő preview-t küldeni (ez az Open GoPro API
dokumentált, támogatott kombinációja, "preview + record" együttes mód).

---

## 3. Hogyan illeszkedik a meglévő recording/download modell mellé

```
GoProConnectionManager.state == .ready
        │
        ├─► (meglévő, érintetlen) capturePreparable.autoPrepare → shutter/start → SD-kártya
        │
        └─► (ÚJ) GoProStreamReceiver.start() → stream/start → UDP:8554 → dekódolás → @Published frame
                        │
                        └─► InstructorDashboardView.goProPreviewPanel — Image/AVSampleBufferDisplayLayer
```

A `goProPreviewPanel` ([InstructorDashboardView.swift:140](../ios/LFAEducationCenter/MultiCamera/InstructorDashboardView.swift))
jelenlegi ikon+szöveg render marad **fallback státuszként** (pl. amíg a stream nem indult
el, vagy ha a dekódolás megszakad) — nem törlődik, csak kiegészül egy live-frame ággal,
ugyanúgy, ahogy a `RemoteCameraView` is fallback szöveget mutat `lastReceivedFrame == nil`
esetén.

**Frame-source absztrakció**: érdemes a meglévő `RemoteCameraView` mintáját követni — egy
közös `protocol LiveFrameSource { var lastFrame: ... { get } }`-szerű absztrakció mögé
tenni mind a Multipeer-alapú (iPad/iPhone), mind a GoPro UDP-alapú forrást, hogy a render
réteg (a panel) ne tudja/nem kelljen tudnia, honnan jött a kép. Ez nem kötelező a POC-hoz,
de a "később több player + több GoPro" skálázási igényhez (a user által jelzett jövőbeli
követelmény) ez a helyes irány — minden forrás egy egységes `[DeviceID: LiveFrameSource]`
dictionary-ben, panel-rács dinamikusan generálva belőle (ez már ma is így működik a
`orderedPanels` ForEach-csel, csak a forrás-típusokat kell egységesíteni).

---

## 4. Kockázatok

| Kockázat | Súlyosság | Részletezés | Mitigáció / döntési hatás |
|---|---|---|---|
| **GoPro Wi-Fi AP stabilitás** | Magas | A HERO13 AP-ja már egyetlen HTTP hívásnál (Block 1, ma) is 409/connectivity gondot adott; folytonos UDP stream alatt a GoPro AP terhelése jelentősen nagyobb | POC-ban mérni kell, hogy a stream aktív léte alatt a `shutter/start`/`updateDeviceStatus` HTTP hívások továbbra is mennek-e — ha nem, ez blokkoló |
| **iPhone cellular/backend coexistence** | Magas | A Block 1 fix pont arra épült, hogy a backend HTTP hívás a cellularon megy, míg a GoPro AP-n nincs internet; UDP socketet **explicit a GoPro AP interfészhez kell kötni** (`NWParameters.requiredInterfaceType`/`prohibitedInterfaceTypes`), különben a rendszer rossz interfészen próbálja fogadni | Ha a UDP socket "leszivárog" a cellularra vagy nem köthető determinisztikusan az AP-hoz, a stream nem jön be — ezt a POC Lépés 2 direkt teszteli |
| **UDP packet loss** | Közepes-Magas | UDP nem garantált kézbesítés; Wi-Fi AP + 2.4/5GHz interferencia + GoPro saját hőkorlátozása mind csomagvesztést okozhat | H.264-nél egy I-frame elvesztése vizuális artefaktot/freeze-t okoz amíg jön a következő keyframe — mérni kell az I-frame intervallumot és a tényleges loss rate-et helyszínen |
| **Latencia** | Közepes | Instruktori "digitális bíró" workflow valós idejű döntéshez kell — másodperces késés már zavaró | POC-ban mérendő: capture→render end-to-end latency; cél < 500ms, de ez validálandó, nem előre garantálható |
| **Dekódolás iPhone-on** | Közepes-Magas | Nincs jelenleg semmilyen UDP/MPEG-TS/H.264 dekóder kód a projektben (0 sor); VideoToolbox saját kezű demux+decode jelentős munka, hibalehetőség | POC-ban a legkockázatosabb pont — ha a saját demux+VideoToolbox út túl instabil/időigényes, a Döntési pont (6.) szerint kész könyvtár (pl. HaishinKit) bevezetése a B terv |
| **Dashboard render** | Alacsony | A render réteg triviális a meglévő `RemoteCameraView` minta alapján, ha már van dekódolt `CVPixelBuffer`/`UIImage` | Alacsony kockázat, csak a 4 fenti pont után releváns |

---

## 5. POC terv — mit bizonyít vagy cáfol gyorsan

**Cél**: minimális, eldobható debug-only kód (hasonlóan a meglévő `[GOPRO-DIAG]` deep
linkhez), ami **igen/nem választ ad** a live preview megvalósíthatóságára, mielőtt
production-minőségű implementáció készülne.

| # | Lépés | Mit bizonyít | Eszköz |
|---|---|---|---|
| 1 | `POST /gopro/camera/stream/start` hívás, válasz 200 ellenőrzése | A HERO13 elfogadja a stream-start parancsot a jelenlegi firmware-rel, a GoPro AP-n, amíg a `shutter`/`updateDeviceStatus` hívások is mennek | Bővítve a meglévő `[GOPRO-DIAG]` deep linket (már részben létezik: [MultiCameraLobbyView.swift:229](../ios/LFAEducationCenter/MultiCamera/MultiCameraLobbyView.swift)) |
| 2 | UDP listener `Network.framework`-kel, explicit GoPro AP interfészhez kötve, port 8554 — első csomag vétele | Bejön-e egyáltalán adat a porton, a dual-network routing nem blokkolja-e | Új debug-only `GoProStreamProbe` osztály, csak naplóz byte-számot/csomagszámot, nem dekódol |
| 3 | Nyers UDP payload mentése fájlba (pár másodperc), majd `ffmpeg`/VLC-vel offline ellenőrzés Mac-en: MPEG-TS vagy RTP, milyen H.264 profil/bitrate | A tényleges formátum — ez validálja vagy dönti meg a "requires HERO13 validation" kódkommentet | Mac terminál, nem iOS kód |
| 4 | VideoToolbox `VTDecompressionSession` proof-of-concept: a 3. lépésben mentett nyers adatból dekódol-e legalább 1 framet | A dekódolási út egyáltalán működik-e natívan, vagy kell 3rd-party lib | Külön Swift Playground vagy debug screen, nem a production view |
| 5 | A dekódolt frame megjelenik a `goProPreviewPanel`-ben, élő, **legalább 15-30 másodpercig stabilan** (nincs freeze, nincs crash) | A teljes lánc end-to-end működik, elég stabil-e egy rövid demo-hoz | Debug build, fizikai eszköz, screen recording bizonyítékként |

**Mérési bizonyíték minden lépésnél**: konzol log + (ahol releváns) a már bevált
`gopro_diag.json`-szerű strukturált fájl-write mintát kell követni (ne `print()`-re
hagyatkozzunk, mert az idevicesyslog ma is megbízhatatlannak bizonyult) — pl.
`gopro_stream_diag.json` mezőkkel: `packetsReceived`, `bytesReceived`, `firstPacketAt`,
`decodeAttempts`, `decodeSuccesses`, `lastError`.

---

## 6. Döntési pont

- **Ha a POC (1-5. lépés) mind teljesül**, és az 5. lépés ≥15-30s stabil preview-t ad
  packet loss/crash nélkül → **beépítjük** a `GoProStreamReceiver`-t a production flow-ba,
  a 3. pontban leírt frame-source absztrakcióval, külön implementációs PR-ben.
- **Ha a POC bármelyik lépésnél elakad** (pl. UDP nem jön be a dual-network routing miatt,
  vagy a dekódolás natívan nem megy stabilan VideoToolbox-szal) → **dokumentáltan más live
  preview útvonalat** kell keresni, jelöltek:
  - GoPro webcam mode (USB-C, ha a HERO13 támogatja UVC webcam módot — ez teljesen
    elkerülné a Wi-Fi/UDP réteget, de USB-kábelt igényelne az instruktor eszközhöz, ami
    used-case szempontból kérdéses egy mobil instruktori setupnál);
  - alacsonyabb frissítési rátájú "snapshot polling" (pl. GoPro thumbnail/snapshot HTTP
    endpoint 1-2 fps-sel, nem valódi videó, de stabilabb mint folytonos UDP) mint
    átmeneti/degraded megoldás;
  - 3rd-party médiakönyvtár (HaishinKit vagy hasonló) bevezetése külön license-jóváhagyással.
  Ez a döntés **nem ezen a dokumentumon belül** történik — ha ide jutunk, vissza kell
  jönni jóváhagyásért, melyik jelölt útvonalat választjuk.

---

**Implementációt (a POC debug-kódon túl) külön jóváhagyás nélkül nem kezdünk.**
