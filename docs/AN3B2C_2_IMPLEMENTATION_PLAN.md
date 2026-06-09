# AN-3B2C-2: Skeleton Renderer Visual Quality Upgrade — Részletes Implementációs Terv

**Dátum:** 2026-06-17  
**Branch kiindulás:** `feat/an3b2b-1-ball-detection` (HEAD dd54dab8)  
**Státusz:** Terv — implementáció jóváhagyás után indulhat  
**Becsült munka:** 2-3 óra implementáció + tesztek  

---

## 1. Érintett fájlok

| Fájl | Változás típusa | Megjegyzés |
|------|----------------|-----------|
| `ios/LFAEducationCenter/Juggling/Annotation/Screen/PoseSnapshotOverlayView.swift` | Módosítás | bone double-stroke, joint méret, `boneSegments` helper |
| `ios/LFAEducationCenter/Juggling/Annotation/Screen/JugglingAnnotationScreen.swift` | Módosítás | skeleton info state, `skeletonStatusBanner`, helper |
| `ios/LFAEducationCenterTests/Juggling/SkeletonOverlayTests.swift` | Módosítás | SK-OV-11..14 új tesztek |

**Nem módosított fájlok:**
- `PoseSnapshotService.swift` — snapshot capture logika változatlan
- `JugglingAnnotationViewModel.swift` — `fetchPoseSnapshots()`, `uploadPendingPoseSnapshot()` változatlan
- `PoseSnapshotDTO.swift` — adatmodellek változatlanok
- Minden backend fájl — nincs backend változás

---

## 2. Kiindulóállapot (jelenlegi kód)

### 2.1 `PoseSnapshotOverlayView.swift` — boneLayer (jelenleg)

```swift
private func boneLayer(byName: [String: BodyLandmarkDTO], w: CGFloat, h: CGFloat) -> some View {
    Path { path in
        for (aName, bName) in Self.bones {
            guard let pa = byName[aName], let pb = byName[bName] else { continue }
            path.move(to:    CGPoint(x: CGFloat(pa.x) * w, y: CGFloat(pa.y) * h))
            path.addLine(to: CGPoint(x: CGFloat(pb.x) * w, y: CGFloat(pb.y) * h))
        }
    }
    .stroke(Color.white.opacity(0.85), lineWidth: 3)
}
```

Problémák: fehér 3px — zöld pályán gyenge kontraszt.

### 2.2 `PoseSnapshotOverlayView.swift` — jointLayer (jelenleg)

```swift
@ViewBuilder
private func jointLayer(w: CGFloat, h: CGFloat) -> some View {
    ForEach(keypoints.body, id: \.name) { lm in
        Circle()
            .fill(Self.jointColor(confidence: lm.confidence))
            .frame(width: 8, height: 8)
            .position(x: lm.x * w, y: lm.y * h)
    }
}
```

Problémák: 8px kicsi, nincs kontúr, elvész zöld/fehér háttéren.

### 2.3 `JugglingAnnotationScreen.swift` — skeleton overlay blokk (jelenleg, sor 342-348)

```swift
if showSkeletonOverlay,
   let snap = closestSnapshot(toMs: playback.currentTimestampMs) {
    PoseSnapshotOverlayView(keypoints: snap.keypoints)
        .frame(width: renderSize.width, height: renderSize.height)
        .allowsHitTesting(false)
}
```

Probléma: ha nincs snapshot ±500ms-ban, a blokk teljesen eltűnik — nincs feedback, a felhasználó nem tudja, miért üres az overlay.

---

## 3. Pontos UI változások

### 3.1 `PoseSnapshotOverlayView.swift` — boneLayer: double-stroke

**A változás lényege:** Minden bone kétszer kerül kirajzolásra — először vastag, sötét "outline" stroke, majd vékonyabb, színes inner stroke. Ez a kombináció bármilyen háttér előtt (zöld fű, fehér fal, sötét szoba) jól látható.

**Intermediary helper (`boneSegments`) kiemelése tesztelhetőségért:**

A bones-ból CGPoint pár kiszámítása kerül egy külön `static` metódusba, amit a tesztek közvetlenül meghívhatnak.

```swift
// MARK: — boneSegments (extracted for testability)
static func boneSegments(
    byName: [String: BodyLandmarkDTO],
    w: CGFloat,
    h: CGFloat
) -> [(CGPoint, CGPoint)] {
    Self.bones.compactMap { (a, b) in
        guard let pa = byName[a], let pb = byName[b] else { return nil }
        return (
            CGPoint(x: CGFloat(pa.x) * w, y: CGFloat(pa.y) * h),
            CGPoint(x: CGFloat(pb.x) * w, y: CGFloat(pb.y) * h)
        )
    }
}
```

**Új `boneLayer`:**

```swift
// MARK: — Bone lines: double-stroke for contrast on any background
//
// Outer dark halo (5 pt) makes lines visible on bright/green backgrounds.
// Inner cyan stroke (2.5 pt) provides the visible skeleton colour.
// Segments pre-computed once via boneSegments() and reused for both passes.

private func boneLayer(byName: [String: BodyLandmarkDTO], w: CGFloat, h: CGFloat) -> some View {
    let segs = Self.boneSegments(byName: byName, w: w, h: h)
    return ZStack {
        Path { path in
            for (s, e) in segs { path.move(to: s); path.addLine(to: e) }
        }
        .stroke(Color.black.opacity(0.55), lineWidth: 5)

        Path { path in
            for (s, e) in segs { path.move(to: s); path.addLine(to: e) }
        }
        .stroke(Color.cyan.opacity(0.92), lineWidth: 2.5)
    }
}
```

**Vizuális eredmény:**
- Zöld pályán: fekete árnyék + kék/cyan vonal — egyértelműen látható
- Fehér falon: szintén kontrasztos
- Sötét háttéren: a sötét outline nem ront, a cyan vonal kiemelkedik

### 3.2 `PoseSnapshotOverlayView.swift` — jointLayer: nagyobb, kontúros pontok

```swift
// MARK: — Joint dots: dark-outlined circle with confidence-based fill
//
// Outer dark ring (14 pt) provides contrast on any background.
// Inner coloured fill (10 pt) keeps the confidence colour coding.

@ViewBuilder
private func jointLayer(w: CGFloat, h: CGFloat) -> some View {
    ForEach(keypoints.body, id: \.name) { lm in
        ZStack {
            Circle()
                .fill(Color.black.opacity(0.55))
                .frame(width: 14, height: 14)
            Circle()
                .fill(Self.jointColor(confidence: lm.confidence))
                .frame(width: 10, height: 10)
        }
        .position(x: lm.x * w, y: lm.y * h)
    }
}
```

**`jointColor` változatlan** — yellow/orange/red confidence-alapú logika megmarad. Az SK-OV-01..05 tesztek változatlanul érvényesek.

### 3.3 `PoseSnapshotOverlayView.swift` — header comment frissítése

A fájl tetején lévő `// Visual encoding:` komment-blokk frissítése a valós értékekre:

```swift
// Visual encoding:
//   Bones: double-stroke — 5 pt dark halo + 2.5 pt cyan inner line.
//          Visible on green pitch, white backgrounds, and dark surfaces.
//   Joints: 14 pt dark outline ring + 10 pt confidence-coloured fill.
//     ≥ 0.70 → yellow  (high confidence)
//     ≥ 0.50 → orange  (medium confidence)
//     <  0.50 → red    (low confidence)
```

---

## 4. Skeleton status banner

### 4.1 Logika

Ha `showSkeletonOverlay == true` de nincs snapshot ±500ms ablakban, **eddig**: néma eltűnés. **Ezután**: informatív banner jelenik meg.

Két eset:
- `poseSnapshots.isEmpty` → a videóhoz egyáltalán nincs skeleton adat (pose capture nem futott)
- `!poseSnapshots.isEmpty`, de nincs ±500ms-ban → ennél az időpontnál nincs snapshot

### 4.2 Testable helper a JugglingAnnotationScreen-ben

```swift
// MARK: — Skeleton status helpers (internal for testing)

static func skeletonStatusText(snapshotsEmpty: Bool, hasNearby: Bool) -> String {
    if snapshotsEmpty { return "Nincs skeleton adat ehhez a videóhoz" }
    if !hasNearby     { return "Nincs skeleton adat ehhez az időponthoz" }
    return ""
}
```

`static` → unit tesztelhető ViewInspector nélkül.

A `skeletonStatusBanner` computed property felhasználja:

```swift
private var skeletonStatusBanner: some View {
    let text = JugglingAnnotationScreen.skeletonStatusText(
        snapshotsEmpty: poseSnapshots.isEmpty,
        hasNearby:      closestSnapshot(toMs: playback.currentTimestampMs) != nil
    )
    return VStack {
        Spacer()
        Text(text)
            .font(.system(size: 11, weight: .medium))
            .foregroundColor(.white.opacity(0.85))
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(Color.black.opacity(0.60))
            .cornerRadius(6)
            .padding(.bottom, 60)
    }
    .allowsHitTesting(false)
}
```

**`.padding(.bottom, 60)`** — az idővonal/timeline sáv felett helyezkedik el, nem takarja a UI-t.

### 4.3 JugglingAnnotationScreen — skeleton overlay blokk módosítása (sor 342-348)

**Jelenlegi:**

```swift
// Skeleton overlay — event-level granularity, ±500ms window
if showSkeletonOverlay,
   let snap = closestSnapshot(toMs: playback.currentTimestampMs) {
    PoseSnapshotOverlayView(keypoints: snap.keypoints)
        .frame(width: renderSize.width, height: renderSize.height)
        .allowsHitTesting(false)
}
```

**Új:**

```swift
// Skeleton overlay — event-level granularity, ±500ms window.
// Shows status banner when toggle is ON but no snapshot is available.
if showSkeletonOverlay {
    if let snap = closestSnapshot(toMs: playback.currentTimestampMs) {
        PoseSnapshotOverlayView(keypoints: snap.keypoints)
            .frame(width: renderSize.width, height: renderSize.height)
            .allowsHitTesting(false)
    } else {
        skeletonStatusBanner
            .frame(width: renderSize.width, height: renderSize.height)
    }
}
```

---

## 5. iOS tesztek

### 5.1 Meglévő tesztek (változatlanul érvényesek)

`SkeletonOverlayTests.swift` SK-OV-01..10 — mind passing marad:
- SK-OV-01..05: `PoseSnapshotOverlayView.jointColor` határértékek
- SK-OV-06..10: `BallVideoOverlayColorHelper.ballColor`

`jointColor` logika NEM változik → ezek a tesztek érintetlenek.

### 5.2 Új tesztek: SK-OV-11..14

Mind a négy teszt `SkeletonOverlayTests.swift`-ben, külön `// MARK: — AN-3B2C-2` szekcióban.

---

**SK-OV-11: `boneSegments` — teljes joint készlettel 17 szegmenst ad vissza**

```swift
func test_SK_OV_11_boneSegments_fullJointsYield17Segments() {
    // All 19 joints present with known positions
    let joints: [BodyLandmarkDTO] = [
        .init(name: "neck",           x: 0.5,  y: 0.1,  confidence: 0.9),
        .init(name: "root",           x: 0.5,  y: 0.6,  confidence: 0.9),
        .init(name: "left_shoulder",  x: 0.4,  y: 0.2,  confidence: 0.9),
        .init(name: "right_shoulder", x: 0.6,  y: 0.2,  confidence: 0.9),
        .init(name: "left_elbow",     x: 0.35, y: 0.35, confidence: 0.8),
        .init(name: "right_elbow",    x: 0.65, y: 0.35, confidence: 0.8),
        .init(name: "left_wrist",     x: 0.3,  y: 0.5,  confidence: 0.7),
        .init(name: "right_wrist",    x: 0.7,  y: 0.5,  confidence: 0.7),
        .init(name: "left_hip",       x: 0.45, y: 0.65, confidence: 0.85),
        .init(name: "right_hip",      x: 0.55, y: 0.65, confidence: 0.85),
        .init(name: "left_knee",      x: 0.44, y: 0.8,  confidence: 0.8),
        .init(name: "right_knee",     x: 0.56, y: 0.8,  confidence: 0.8),
        .init(name: "left_ankle",     x: 0.43, y: 0.95, confidence: 0.75),
        .init(name: "right_ankle",    x: 0.57, y: 0.95, confidence: 0.75),
        .init(name: "nose",           x: 0.5,  y: 0.04, confidence: 0.9),
        .init(name: "left_eye",       x: 0.47, y: 0.03, confidence: 0.85),
        .init(name: "right_eye",      x: 0.53, y: 0.03, confidence: 0.85),
        .init(name: "left_ear",       x: 0.44, y: 0.05, confidence: 0.7),
        .init(name: "right_ear",      x: 0.56, y: 0.05, confidence: 0.7),
    ]
    let byName = Dictionary(uniqueKeysWithValues: joints.map { ($0.name, $0) })
    let segs = PoseSnapshotOverlayView.boneSegments(byName: byName, w: 100, h: 200)
    XCTAssertEqual(segs.count, 17, "All 17 bones should produce segments when all joints present")
}
```

---

**SK-OV-12: `boneSegments` — üres byName dict → 0 szegmens**

```swift
func test_SK_OV_12_boneSegments_emptyDictYieldsZeroSegments() {
    let segs = PoseSnapshotOverlayView.boneSegments(byName: [:], w: 100, h: 200)
    XCTAssertTrue(segs.isEmpty, "Empty byName dict must yield zero bone segments")
}
```

---

**SK-OV-13: `skeletonStatusText` — üres snapshots → videóhoz nincs adat**

```swift
func test_SK_OV_13_skeletonStatusText_emptySnapshots() {
    let text = JugglingAnnotationScreen.skeletonStatusText(
        snapshotsEmpty: true,
        hasNearby: false
    )
    XCTAssertEqual(text, "Nincs skeleton adat ehhez a videóhoz")
}
```

---

**SK-OV-14: `skeletonStatusText` — van snapshot, de nem közeli → időponthoz nincs adat**

```swift
func test_SK_OV_14_skeletonStatusText_noNearbySnapshot() {
    let text = JugglingAnnotationScreen.skeletonStatusText(
        snapshotsEmpty: false,
        hasNearby: false
    )
    XCTAssertEqual(text, "Nincs skeleton adat ehhez az időponthoz")
}
```

---

**Összesítés:**

| Test ID | Fájl | Mit tesztel | Meglévő? |
|---------|------|-------------|----------|
| SK-OV-01..05 | SkeletonOverlayTests | `jointColor` határértékek | ✓ változatlan |
| SK-OV-06..10 | SkeletonOverlayTests | `BallVideoOverlayColorHelper.ballColor` | ✓ változatlan |
| SK-OV-11 | SkeletonOverlayTests | `boneSegments` 17 szegmens teljes joint listával | ÚJ |
| SK-OV-12 | SkeletonOverlayTests | `boneSegments` üres dict → 0 szegmens | ÚJ |
| SK-OV-13 | SkeletonOverlayTests | `skeletonStatusText` snapshotsEmpty=true | ÚJ |
| SK-OV-14 | SkeletonOverlayTests | `skeletonStatusText` hasNearby=false | ÚJ |

**Elvárt test count:** 520 → 524 (+4)

---

## 6. Device QA lépések

### Előfeltétel

- iPhone-ra telepítve az AN-3B2C-2 build
- Legalább egy juggling videó elérhető, amelyhez van feltöltött PoseSnapshot (pl. a tesztelt 319eb833 videó)

### QA-1: Skeleton vonalak zöld pályán

1. Nyisd meg a juggling annotáció képernyőt a tesztelt videóval
2. Koppints a **skeleton toggle** gombra (figura ikon, jobb felső sarok)
3. Navigálj az idővonalán egy esemény közelébe (±500ms)
4. **Elvárt:** 
   - Cyan vonalak jelennek meg a testrészek között
   - Minden vonalnak van sötét "halo" árnyéka
   - Zöld pályán egyértelműen látható, nem olvad bele
5. **Nem elvárt:** Csak pontok, vonalak nélkül (ez az előző hiba volt)

### QA-2: Joint pontok mérete és kontrasztja

1. Előző képernyőn, skeleton toggle ON
2. Nézd meg a jointokat közelebbről
3. **Elvárt:**
   - Minden joint egyszerre tartalmaz sötét külső kört és színes belső kört
   - A confidence-alapú szín látható (yellow/orange/red)
   - Az egész "csontváz" az egész test jellemzőit átfogja (nyaktól bokáig)
4. **Méret ellenőrzés:** A pontok lényegesen nagyobbak a korábbi 8px-eknél

### QA-3: Skeleton status banner — videó skeleton adattal, de nem megfelelő időpontban

1. Skeleton toggle ON
2. Navigálj az idővonalán egy olyan pontra, ahol **nincs** esemény / snapshot (pl. videó eleje/vége)
3. **Elvárt:** 
   - Alul megjelenik egy kis szöveg: `"Nincs skeleton adat ehhez az időponthoz"`
   - Fekete background, fehér szöveg
   - A szöveg NEM takarja a timeline-t (60px clearance)
4. Navigálj vissza egy esemény közelébe → a szöveg eltűnik, a skeleton megjelenik

### QA-4: Skeleton status banner — videóhoz nincs skeleton adat

1. Nyiss meg egy juggling videót, amelyhez **nincs** feltöltött pose snapshot
2. Skeleton toggle ON
3. **Elvárt:** `"Nincs skeleton adat ehhez a videóhoz"` banner (bárhol az idővonalán)

### QA-5: Skeleton toggle OFF — semmi sem látható

1. Skeleton toggle OFF állapotban
2. **Elvárt:** Sem skeleton, sem banner — semmi sem jelenik meg az overlay területen

### QA-6: Ball + skeleton egyszerre aktív

1. Skeleton toggle ON, ball toggle ON
2. Navigálj esemény közelébe, ahol mindkét adatnak kellene lennie
3. **Elvárt:** Skeleton (cyan vonalak + pontok) + ball marker egyszerre látható, nem fedik el egymást
4. ZStack sorrend: skeleton alul, ball felül

### QA-7: Rotation (forgatás)

1. Skeleton toggle ON, videó forgatva (ha alkalmazható a tesztelt videóra)
2. **Elvárt:** Skeleton overlay követi a `rotationEffect` forgatását — a `PoseSnapshotOverlayView` a `renderSize` frame-re van szorítva, és az `AVPlayerLayerView`-val együtt forog

---

## 7. Commit bontás

### Commit 1: `refactor(skeleton): extract boneSegments helper for testability`

**Érintett fájl:** `PoseSnapshotOverlayView.swift`

Tartalom:
- `static func boneSegments(byName:w:h:)` metódus hozzáadása
- `boneLayer` átírva: `let segs = Self.boneSegments(...)` majd két Path a ZStack-ben
- `jointLayer`: ZStack (14px dark + 10px colored)
- Header comment frissítése

Ez **kizárólag a renderelési kódot** érinti, logikai változás nincs.

### Commit 2: `feat(skeleton): add info banner when no snapshot in ±500ms window`

**Érintett fájlok:** `JugglingAnnotationScreen.swift`

Tartalom:
- `static func skeletonStatusText(snapshotsEmpty:hasNearby:) -> String` hozzáadása
- `skeletonStatusBanner` computed property hozzáadása
- Skeleton overlay blokk módosítása: `if showSkeletonOverlay { if let snap ... else { skeletonStatusBanner } }`

### Commit 3: `test(skeleton): SK-OV-11..14 — boneSegments and status text coverage`

**Érintett fájl:** `SkeletonOverlayTests.swift`

Tartalom: 4 új test case a meglévő fájlba, `// MARK: — AN-3B2C-2` szekció alatt.

---

## 8. Build / test elvárások

### Build

- **No new warnings** — a double-stroke ZStack és a `static` helper nem vezet be egyetlen new Swift warning-ot sem
- **iOS deployment target 15.0** — minden API (`Color.cyan`, `ZStack`, `Path.stroke`) iOS 14-kompatibilis, az emelés (15.0) után is érvényes
- `Color.cyan` availability: iOS 14+ ✓

### Unit tests

```
524/524 tests PASS (520 meglévő + 4 új)
0 failures
```

**Akceptanciafeltétel:** A Xcode tesztek helyi futtatása (`xcodebuild test -scheme LFAEducationCenter ...`) 524/524 PASS eredménnyel zárul.

### Nincs backend változás

Nincs alembic migration, nincs Python fájl módosítás, nincs API változás → a backend tesztek (65 db) érintetlenek.

---

## 9. Kizárt scope (NEM kerül be ebbe a PR-ba)

| Téma | Miért halasztott |
|------|-----------------|
| AN-3B2D ball trajectory | Külön PR sorozat, backend infra kell |
| YOLOv8n model swap | Licenc audit hiányzik |
| Continuous (live-camera) skeleton | Vision real-time API redesign, nem event-snapshot |
| Skeleton on `EventLabelDetailView` | Ott már működik, nem változik |
| `closestSnapshot` ablak módosítása (±500ms → egyéb) | Nincs javaslat, eredeti ablak megmarad |

---

*Implementáció csak jóváhagyás után indul.*
