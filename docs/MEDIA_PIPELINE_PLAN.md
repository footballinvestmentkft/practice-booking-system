# Backend Media Pipeline — Tervezési dokumentum

**Dátum:** 2026-06-29
**Státusz:** TERVEZÉSI DOKUMENTUM — implementáció kizárólag külön jóváhagyás után.
**Előzmény:** Capture/storage audit (2026-06-29, lásd session-history) feltárta, hogy az
iPhone/iPad lokálisan rögzít ([SessionCaptureManager.swift](../ios/LFAEducationCenter/MultiCamera/SessionCaptureManager.swift)),
a GoPro SD-kártyára rögzít, de **sehol nincs backend upload, media adatmodell, vagy
finalization médiafájl-meglét alapján** — a `CaptureStream` modell docstring-je explicit
kimondja: *"Contract placeholder — no media lifecycle in PR-4B2."*
([app/models/multicamera_session.py:211-212](../app/models/multicamera_session.py)).

A `gopro-combined-cycle-proof` POC (GoPro preview+recording) **nem helyettesíti** ezt a
munkát — az csak azt bizonyítja, hogy a GoPro SD-kártyára ír, miközben a preview megy.

---

## 1. Media adatmodell

### Döntés: bővítjük a meglévő `CaptureStream` táblát, nem építünk párhuzamos modellt

A `CaptureStream` ([app/models/multicamera_session.py:211-236](../app/models/multicamera_session.py))
már most is per-device, per-cycle, per-stream_type rekord (`session_device_id`,
`capture_cycle_id`, `stream_type`, `capture_result`) — pontosan a megfelelő granularitás
egy media-referenciához. Új mezők (Alembic migráció):

```python
file_url = Column(String(512), nullable=True)        # storage kulcs/URL, NULL amíg nincs upload
file_size_bytes = Column(BigInteger, nullable=True)
checksum_sha256 = Column(String(64), nullable=True)
resolution = Column(String(20), nullable=True)        # "1280x720"
fps = Column(Integer, nullable=True)
codec = Column(String(20), nullable=True)             # "h264" / "hevc"
orientation = Column(String(20), nullable=True)       # "portrait" / "landscape"
upload_status = Column(String(20), nullable=False, server_default="pending")
upload_attempts = Column(Integer, nullable=False, server_default="0")
uploaded_at = Column(DateTime(timezone=True), nullable=True)
```

`upload_status` CHECK constraint: `'pending' | 'uploading' | 'uploaded' | 'failed'`.

**Miért nem új tábla**: a `CaptureStream` már a helyes lifecycle-hoz van kötve
(`created_at`/`started_at`/`stopped_at`/`capture_result`), egy `CaptureMedia` párhuzamos
tábla csak duplikálná a foreign key-eket session_device_id/capture_cycle_id-ra, és extra
join-t igényelne mindenhol, ahol media infót akarunk.

---

## 2. Upload endpoint

### Döntés: multipart/form-data direkt upload, NEM presigned URL — első körben

**Indoklás**: a presigned URL (S3/GCS direct-to-storage) jobban skálázódik nagy fájloknál
és csökkenti a backend terhelést, **de** extra infrastruktúra-függőséget hoz be (S3/GCS
bucket, IAM, CORS konfiguráció), amit most nem tudunk validálni anélkül, hogy tudnánk,
**hova** megy a tárolás (jelenleg nincs ismert object storage ennek a projektnek — ezt
tisztázni kell, mielőtt presigned URL-ezésbe fognánk). Első körben egyszerűbb: a backend
fogadja a multipart upload-ot, és lokálisan/disk-en vagy a már meglévő infrastruktúrán
(amit a `app/services/card_export_service.py` vagy hasonló már használ képekhez/videókhoz
— ezt audit kell, mielőtt eldöntjük) tárolja.

```
POST /api/v1/multicamera/sessions/{uuid}/devices/{device_id}/cycles/{cycle_id}/media
  multipart/form-data:
    file: <video blob>
    checksum_sha256: <string>
    resolution: <string>
    fps: <int>
    codec: <string>
    orientation: <string>
  Idempotency-Key: <client-generated UUID>  (header, lásd 3. pont)

Response 201:
  { "media_id": <capture_stream_id>, "upload_status": "uploaded", "file_url": "..." }

Response 409 (idempotency conflict, ugyanaz a checksum már feltöltve):
  { "media_id": <existing_id>, "upload_status": "uploaded", "file_url": "...", "note": "already uploaded" }
```

**Méretkorlát**: 720p/30fps ~5 perces cycle ≈ 150-300 MB (kodek-függő) — ez **nem fér el**
egy normál HTTP request body-ban kényelmesen. Vagy chunked upload kell (több részlet), vagy
végül mégis presigned URL — **ezt validálni kell egy konkrét fájlmérettel**, mielőtt
elköteleződünk a multipart megoldás mellett az 1-5 perces cycle-ökre.

---

## 3. Checksum / idempotency

- **Idempotency-Key header** (kliens-generált UUID, per upload attempt) — ha a request
  megszakad és a kliens retry-ol, a backend ugyanazt a `media_id`-t adja vissza, nem hoz
  létre duplikátumot.
- **Checksum (SHA-256)** a kliens oldalon számolva feltöltés előtt, a szerver
  összeveti a megérkezett bytes hash-ével — ha eltér, `422` és a kliens újrapróbál.
- A `CaptureStream.checksum_sha256` mező egyben **dedup-kulcs** is: ha ugyanaz a checksum
  már létezik a táblában ugyanarra a `session_device_id`+`capture_cycle_id` páros, az
  upload no-op, visszaadja a meglévő rekordot.

---

## 4. Local-first storage + perzisztens retry queue (iOS oldal)

### Döntés: a fájl MINDIG előbb lokálisan készül el (ez már így van), upload csak utólag, külön service-ből

A `SessionCaptureManager` jelenlegi viselkedése (rögzít, lokálisan ment, **nem töröl**)
helyes alap — ezt nem kell megváltoztatni. Új komponens:

```swift
// Új fájl: ios/LFAEducationCenter/MultiCamera/MediaUploadQueue.swift
final class MediaUploadQueue: ObservableObject {
    // Perzisztens állapot: UserDefaults vagy egy kis SQLite/plist a pending uploadokról
    // (sessionUUID, deviceId, cycleId, fileURL, checksum, attempts, lastError)
    func enqueue(fileURL: URL, sessionUUID: String, deviceId: Int, cycleId: Int)
    func processQueue() async  // URLSession upload task-ok, exponenciális backoff
}
```

- **Perzisztencia**: a queue állapotot **fájlba** kell írni (nem csak memóriába), hogy app
  force-quit/crash után is megmaradjon, melyik fájl van még feltöltésre várva.
- **Background upload**: `URLSession` background configuration (`.background(withIdentifier:)`),
  hogy app-backgroundban is folytatódjon az upload, ne csak foreground-ban.
- **Retry policy**: exponenciális backoff (pl. 5s, 15s, 60s, 5min, majd óránként), max attempt
  szám után `upload_status=failed`, de **a lokális fájl megmarad törlés nélkül** — soha nem
  veszik el felvétel csak azért, hogy az upload sikertelen volt.
- **Törlési policy**: lokális fájl **csak akkor törlődik**, ha a backend visszaigazolta
  `upload_status=uploaded`-t, **és** eltelt egy retention-időszak (pl. 24-48 óra biztonsági
  pufferként, hogy egy backend-oldali hiba esetén még legyen mit újraküldeni).

---

## 5. Cycle finalization — médiafájl-meglét alapján

Jelenleg a `CaptureCycle.status`/`result` **kizárólag a recording state-re** vonatkozik
(started/stopped/completed/failed), a médiafájl meglététől függetlenül. Új logika:

- Egy cycle csak akkor kapjon `finalized` státuszt, ha **minden elvárt device-hoz** van
  `upload_status='uploaded'` `CaptureStream` rekord.
- **Partial state**: ha egy device upload-ja sikertelen marad (pl. GoPro letöltés
  meghiúsul), a cycle `partial`-ként jelölhető — explicit állapot, nem hallgatólagos hiány.
- Ez egy **új CHECK constraint vagy service-szintű validáció** a `cycle_service.py`-ban,
  nem migráció — a `CaptureCycle.status` enum bővül `'partial'` értékkel.

---

## 6. GoPro SD-fájl letöltési és upload folyamata

A meglévő `gopro-download-latest` deep link ([MultiCameraLobbyView.swift:274-300](../ios/LFAEducationCenter/MultiCamera/MultiCameraLobbyView.swift))
**`temporaryDirectory`**-ba tölt le — ez nem biztonságos végállapot (iOS bármikor purge-elheti).

Javasolt módosítás (implementáció **nem most**, csak a terv):
1. Letöltés után a fájl azonnal átmozgatásra kerül a **`Application Support`**-ba (ugyanaz
   a tartós könyvtár, amit az iPhone/iPad lokális capture is használ), nem marad `tmp/`-ben.
2. Csak ezután kerül a `MediaUploadQueue`-ba, ugyanazzal a mechanizmussal, mint az
   iPhone/iPad saját felvétele — a GoPro media **a feltöltés szempontjából
   megkülönböztethetetlen** az iPhone/iPad médiától, csak a forrása más.
3. A GoPro-specifikus checksum/resolution/fps/codec metaadatot a `camera/state` válaszból
   kell kiolvasni (lásd a Capture Quality blokk 4. pontja — "először olvasd ki").

---

## 7. Retention és lokális törlési policy (összefoglalva)

| Állapot | Lokális fájl törlés |
|---|---|
| `pending` (még nem próbált upload-olni) | nem törlődik |
| `uploading` (folyamatban) | nem törlődik |
| `failed` (minden retry kimerült) | **nem törlődik soha automatikusan** — manuális beavatkozást igényel |
| `uploaded` + retention időszak (24-48h) letelt | törlődhet — `removeZeroByteFiles`-hez hasonló, de most már a sikeresen feltöltött, NEM 0-byte fájlokra vonatkozó takarítási job kell |

---

## 8. Nyitott kérdések — implementáció előtt tisztázni kell

1. **Hova megy a tárolt média?** Van-e már S3/GCS/Azure Blob konfiguráció a projektben
   bármilyen más célra (pl. card export videók)? Ha igen, azt kellene újrahasznosítani.
2. **Mekkora egy tipikus 1-5 perces cycle fájl mérete** 720p/30fps-nél (kodek-függő) — ez
   dönti el, hogy a multipart upload elfér-e egy request-ben, vagy chunked/presigned kell.
3. **Ki fér hozzá a feltöltött médiához** — van-e már auth/permission modell, ami
   kiterjeszthető media-letöltésre (instruktor, admin, érintett player)?

---

**Implementációt ezen a dokumentumon nem kezdünk, amíg nincs külön jóváhagyás minden egyes
szekcióhoz (adatmodell migráció, upload endpoint, iOS upload service, finalization logika
egyenként eldönthető, hogy melyik mikor indul).**
