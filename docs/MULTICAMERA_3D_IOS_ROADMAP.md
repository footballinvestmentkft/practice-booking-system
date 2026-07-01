# Multicamera 3D iOS Roadmap
## iPhone + GoPro HERO12 — Single-player és Two-player 3D Skeleton

**Dátum:** 2026-06-20  
**Státusz:** TERVEZÉSI DOKUMENTUM — implementáció külön fázisokban, jóváhagyással  
**Kötött sorrend:** Phase 1 → 2 → 3 → 4 → 5 (korábbi fázis lezárása előfeltétele a következőnek)

> **2026-06-29 frissítés**: a "Videó és metaadat átvitel" szakasz (lent) kizárólag a
> record-then-download (GoPro lokális rögzítés + utólagos `media/list` HTTP letöltés)
> útvonalat írja le. Ez **a capture/archívum pipeline szempontjából továbbra is érvényes**,
> de **nem elégíti ki** az instructor dashboard live preview követelményét (minden
> connected kamera élőképe egy képernyőn, GoPro HERO13-mal együtt). A live preview külön,
> e mellett futó útvonal — lásd [GOPRO_LIVE_PREVIEW_POC_PLAN.md](GOPRO_LIVE_PREVIEW_POC_PLAN.md).

---

## Phase 1 — Single-player 3D skeleton foundation

### Audit kérdések (implementáció előtt elvégzendő)

- **Jelenlegi skeleton modell 3D képességei**: A Vision framework `VNHumanBodyPoseRequest` 2D
  keypointokat ad vissza (image koordináta). Van-e már depth információ a meglévő pipeline-ban?
  A `PoseSnapshotService` (AN-3B2A) 2D-s — van-e tervezett mélységkiterjesztés?
- **Meglévő joint schema**: Milyen joint neveket és konfidencia-küszöböt használ a jelenlegi annotáció?
  Az 18-joint body pose vs. 21-joint full body (kéz) közül melyik releváns a futball elemzéshez?

### Tervezési döntések

**Joint schema**
- Minimális futball-relevans set: pelvis, hip (L/R), knee (L/R), ankle (L/R), foot (L/R),
  shoulder (L/R), elbow (L/R), wrist (L/R), head, neck
- Kiterjesztett set (két kézzel, futball speciális): +toe (L/R) a rúgási alap meghatározásához
- Egységes schema mindkét kamerára — fázis 3 trianguláció feltétele

**Koordináta rendszerek**
- Image koordináta: `(x_norm, y_norm) ∈ [0,1]` — Vision output
- World koordináta: jobbkezes Descartes koordináta, origó = kalibrációs origó pont
- Camera koordináta → World: extrinsic matrix (rotation + translation), per kamera
- Depth egység: méter (nem pixel), iPhone LiDAR vagy stereo becslés alapján

**Confidence kezelés**
- Per-joint confidence ∈ [0,1]; `< 0.3` → occluded/low-conf, kizárva a 3D rekonstrukcióból
- Frame-szintű skeleton confidence = min-joint vagy weighted average

**iPhone technológiai döntés**

| Opció | Pro | Con |
|---|---|---|
| **RealityKit** | AR marker, modern API, WWDC 2024+ | 3D rendering és physics; nehéz pure skeletal |
| **SceneKit** | Jól dokumentált, skeleton node-ok natívak | Deprecated trend, kevesebb WWDC support |
| **ARKit + Metal** | Maximális kontroll, LiDAR depth | Sok boilerplate, nehéz karbantartani |
| **SwiftUI + RealityView** | Modern integráció, kompozíció | Early-stage API, korlátozott 3D |

**Ajánlás**: RealityKit + ARKit depth (iPhone 12 Pro+), forgatható 3D skeleton ModelEntity-kkel.
SceneKit backup, ha a RealityKit skeletális animáció nem elég rugalmas.

**POC scope (Phase 1)**
- iPhone kamera feed → Vision body pose (2D)
- LiDAR depth map → per-joint depth becslés (raycast + depth texture sample)
- 2D + depth → camera-space 3D joint
- Camera → world transzformáció (ARWorldTrackingConfiguration ground plane)
- Forgatható 3D skeleton overlay SwiftUI/RealityKit nézetben
- AR elhelyezés (virtual figure a valós térben)
- Nincs GoPro, nincs second player; egy kamera, egy játékos

---

## Phase 2 — iPhone + GoPro HERO12 Multi-camera POC

### GoPro csatlakoztatás és vezérlés

**Kapcsolati lehetőségek**
- GoPro Labs firmware: WiFi HTTP + UDP command API
- GoPro Open GoPro SDK (BLE + HTTP/WiFi): `gp-go-open-api`
- USB-C: videó export, nem real-time streaming

**Felvétel vezérlés**
- Indítás/leállítás: HTTP POST `/gopro/camera/shutter/start` (Open GoPro API)
- Állapot polling: GET `/gopro/camera/state` (WiFi)
- Kapcsolatvesztés kezelése: timeout + reconnect logika; iPhone offline buffer

**Videó és metaadat átvitel**
- GoPro WiFi HTTP: GET `/gopro/media/list` → JPEG/MP4 download
- Metaadat: GoPro Metadata Format (GPMF) telemetria stream (gyro, accel, GPS, timestamp)
- GPMF parsing: open-source parser szükséges (Swift port vagy server-side Python)

### Időszinkronizáció

**Módszer: NTP + clapper sync**
- NTP: mind iPhone, mind GoPro system clock NTP-szinkronizálva (precision ≈ 10–50ms)
- Clapper sync (frame-level): egy hangos taps/flash esemény mindkét videóban; kézzel vagy
  automatikusan azonosítható az audio waveform spike-ból
- Frame-level alignment: `offset_ms = t_clapper_gopro - t_clapper_iphone`
- Drift mérés: ha a felvétel >5 perc, becsülje a temporal drift-et; GoPro GPMF gyro timestamp
  pontosabb referencia az audio-nál

**Pontossági célok**
- Szinkronizáció hibacél: < 33ms (1 frame @ 30fps)
- Drift kezelés: lineáris interpoláció a clapper és a felvétel vége közt

### Kalibráció

- **Intrinsic**: egyenként, checkboard calibration (OpenCV Python); GoPro fisheye undistortion
- **Extrinsic**: relatív R|t mátrix a két kamera közt (sztereó kalibráció, vagy PnP a közös
  látható ponton)
- Közös origó: kalibráló board vagy ismert 3D pont (pl. pitch sarok)
- Mentési formátum: JSON per session (focal length, principal point, distortion coeffs, R, t)

### Közös eseményazonosító és offline fallback

- Session UUID: a felvétel kezdetekor iPhone generálja; GoPro fájlhoz rendelésnél ez az összerendelő kulcs
- Offline fallback: GoPro nélküli iPhone-only felvétel mindig működjön (degraded mode: 2D skeleton only)

---

## Phase 3 — Two-camera 3D reconstruction

### Triangulációs pipeline

```
iPhone 2D joint (u1, v1) + GoPro 2D joint (u2, v2)
    → undistort → normalize → triangulate(P1, P2)
    → (X, Y, Z) world koordináta
```

- Trianguláció: DLT (Direct Linear Transform) vagy `cv2.triangulatePoints`
- Camera matrices: `P = K @ [R | t]` per kamera
- Reprojection error: `||pi - project(Pi, Xi)||²` megőrzés per joint per frame

**Occlusion kezelés**
- Ha egy kamerában `joint.confidence < 0.3`: csak a másik kamera projekciójából becsül (single-view depth)
- Ha mindkét kamerában occluded: joint = `None` (nem rekonstruált)
- Temporal smoothing: Kalman filter per 3D joint trajektória (már van `kalman_ball_tracker.py` — mintaként)

**3D skeleton export**
- JSON per frame: `{ "frame_ms": ..., "joints": { "left_knee": { "x":..., "y":..., "z":..., "conf":... } } }`
- Kompatibilis a meglévő annotation upload pipeline-nal

**Megjelenítés**
- iPhone: forgatható 3D skeleton (Phase 1 viewer kiterjesztve)
- AR: rekonstruált figura a valós térben elhelyezve

---

## Phase 4 — Two-player skeleton

### Két játékos azonosítása és tracking

**Személyazonosítás (player identity)**
- Jelenetes azonosítás: az első frame-ben a két legközelebbi két skeleton →Player A (iPhone oldalán lévő) és Player B
- Tracking: `centroid tracking` — ha a centriod pozíció kontinuus, marad az ID
- Felcserélődés elleni védelem: ha az euklideszi távolság ugrása >1.5m/frame → ID reassignment flag

**Két kamera × két játékos asszociáció**
- Minden kamerában vision detektál ≤2 skeletonet
- `Hungarian algorithm` (scipy) a két kamera skeleton-pairjainak párosítására
- `Reprojection cost` = a párosítás reprojection errora — minimum párosítást választ

**Egymással szembeni hibrid játékok**
- 1v1 juggling challenge: labda és mindkét játékos contact événye közös idővonalon
- Timeline key: `{ "frame_ms", "event_type": "contact|no_contact|ball_switch", "player_id": "A|B" }`

**Jelenlegi limitáció**
- Crowd-sourced videóknál a two-player asszociáció instabil; Phase 4 csak kontrollált, rögzített kameraállással célzott

---

## Phase 5 — Scalable data collection

### Consent és session metadata

- Kiegészített `JugglingConsent` schema: `multicamera_consent`, `3d_reconstruction_consent`
- Session metadata: `camera_count`, `sync_offset_ms`, `calibration_id`
- Privacy: GoPro videók nem tárolódnak a saját szerverünkön; csak a derivált 3D skeleton JSON
- GDPR: a 3D skeleton biometrikailag érzékeny adat → DPIA szükséges a training adatgyűjtés előtt

### Automatikus upload pipeline

- iPhone: sikeres 3D rekonstrukció → `AnnotationUploadService` kiterjesztve `skeleton_3d` payload-dal
- GoPro: nyers videó lokálisan marad; csak GPMF telemetria és joint payload megy szerverre
- Upload retries: meglévő deferred upload pipeline (AN-3B2A minta)

### Annotation queue és minőségellenőrzés

- `JugglingBallFeedback` mintájára: `JugglingSkeletonFeedback` tábla (tervezett)
- Reviewerek validálhatják a 3D skeleton pontosságát
- Control sample mechanizmus (szintetikus ground truth) kiterjesztve

### XP/credit ösztönzés

- AN-3B2E reward engine bővíthető: `decision = "skeleton_corrected"` új reward típussal
- Részletezés: külön AN-3B2F vagy AN-3B3X fázisban

---

## Függőségi térkép

```
Phase 1 (Single-cam 3D skeleton + viewer)
    └─► Phase 2 (GoPro integration + sync)
            └─► Phase 3 (Two-cam triangulation)
                    └─► Phase 4 (Two-player)
                            └─► Phase 5 (Data collection)
```

Minden fázis le kell záruljon és külön jóváhagyást kapjon a következő előtt.

---

**CONSTRAINT**: Ez a dokumentum tervezési roadmap. Implementáció fázisonként, külön jóváhagyással.  
*Előző fázis: AN-3B2E (XP/credit reward) — PR-3A backend implementálás alatt.*
