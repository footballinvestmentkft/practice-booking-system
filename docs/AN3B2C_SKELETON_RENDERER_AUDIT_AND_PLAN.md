# AN-3B2C: Skeleton Renderer — Kódszintű Audit és Implementációs Terv

**Dátum:** 2026-06-17  
**Branch:** `feat/an3b2b-1-ball-detection`  
**Státusz:** Audit kész — implementáció jóváhagyás után indulhat  

---

## 1. Kritikus megállapítás: A kék vonalas skeleton forrása

**A Screenshot 1 ("Phase 2 — Body Pose", FPS: 29, Joints: 19, kék vonalak) NEM szerepel ebben a kódbázisban.**

Az összes `Biometric/Spike/` fájl (`ARFaceTrackingView.swift`, `SpikeLivenessView.swift`, `BiometricAutoCapture.swift`, `BiometricPhotoCapture.swift`, `SpikeLivenessViewModel.swift`, `GestureStabilizer.swift`, `FaceGestureType.swift`, `FacePoseThresholds.swift`) ARKit face tracking-et végez — `ARFaceAnchor`, TrueDepth kamera, arc gestures. Body pose detection nincs bennük.

A teljes iOS kódbázisban egyetlen body pose skeleton renderer létezik:

```
ios/LFAEducationCenter/Juggling/Annotation/Screen/PoseSnapshotOverlayView.swift
```

A Screenshot 1 egy **külső referencia-képernyőről** érkezik (valószínűleg egy standalone `AVCaptureSession + VNDetectHumanBodyPoseRequest` tesztalkalmazásból). Ez a tesztkód NEM része ennek a projektnek.

**Következmény:** Nincs mit "átvenni" vagy "egységesíteni" — a `PoseSnapshotOverlayView` az egyetlen renderer, amelyet javítani és fejleszteni kell.

---

## 2. Jelenlegi renderer audit — `PoseSnapshotOverlayView.swift`

### 2.1 Bones (csontvázrajzolás)

**VOLT (hibás — bones nem jelennek meg):**

```swift
@ViewBuilder
private func boneLayer(byName: [String: BodyLandmarkDTO], w: CGFloat, h: CGFloat) -> some View {
    ForEach(Self.bones.indices, id: \.self) { i in
        let (aName, bName) = Self.bones[i]
        if let pa = byName[aName], let pb = byName[bName] {  // ← SILENT EmptyView
            Path { ... }.stroke(Color.white.opacity(0.85), lineWidth: 3)
        }
    }
}
```

**Root cause:** Swift `@ViewBuilder` context-ben a `if let a = ..., let b = ...` multi-binding csendesen `EmptyView`-t ad vissza minden iterációban.

**VAN (javított — single Path):**

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

**Státusz:** Javítva, 520/520 test PASS.

### 2.2 Joints (pontok)

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
// Confidence: ≥0.70=yellow, ≥0.50=orange, <0.50=red
```

**Hibák:** 8px kicsi; nincs kontúr → zöld háttéren elvész; `ForEach` itt működik (unconditional Circle()).

### 2.3 Csontváz connectivity

```swift
private static let bones: [(String, String)] = [
    ("neck", "root"),          // törzs
    ("neck", "left_shoulder"), ("neck", "right_shoulder"),
    ("left_shoulder", "left_elbow"), ("left_elbow", "left_wrist"),
    ("right_shoulder", "right_elbow"), ("right_elbow", "right_wrist"),
    ("root", "left_hip"),  ("left_hip", "left_knee"),  ("left_knee", "left_ankle"),
    ("root", "right_hip"), ("right_hip", "right_knee"), ("right_knee", "right_ankle"),
    ("nose", "left_eye"),  ("nose", "right_eye"),
    ("left_eye", "left_ear"), ("right_eye", "right_ear"),
]
```

**17 csont definiálva**, minden fő testrész lefedve. Helyes.

### 2.4 Joint name mapping (PoseSnapshotService → PoseSnapshotOverlayView)

```
Vision raw value   → snake_case (tárolva DB-ben) → bones tömbben
"neck1"            → "neck"            ✓
"leftShoulder1"    → "left_shoulder"   ✓
"rightShoulder1"   → "right_shoulder"  ✓
"leftElbow1"       → "left_elbow"      ✓
"rightElbow1"      → "right_elbow"     ✓
"leftWrist1"       → "left_wrist"      ✓
"rightWrist1"      → "right_wrist"     ✓
"root"             → "root"            ✓
"leftHip1"         → "left_hip"        ✓
"rightHip1"        → "right_hip"       ✓
"leftKnee1"        → "left_knee"       ✓
"rightKnee1"       → "right_knee"      ✓
"leftAnkle1"       → "left_ankle"      ✓
"rightAnkle1"      → "right_ankle"     ✓
"nose"             → "nose"            ✓
"leftEye"          → "left_eye"        ✓
"rightEye"         → "right_eye"       ✓
"leftEar"          → "left_ear"        ✓
"rightEar"         → "right_ear"       ✓
```

**Mapping helyes.** Minden joint névkonvenció konzisztens tárolástól renderelésig.

### 2.5 Confidence threshold

```swift
// PoseSnapshotService.swift
static let confidenceThreshold: Float = 0.3
```

Joints amelyek confidence-je < 0.3 NEM kerülnek be az upload-ba. Ez magyarázza a "hiányos" skeletonokat: ha pl. a bokák vagy csuklók confidence-je gyenge (mozgás, takarás, nagy kameratávolság), ezek a joint-ok hiányoznak a `byName` dict-ből → az érintett bones sem renderelnek.

---

## 3. Mi a különbség a Reference Screenshot és a jelenlegi renderer között

| | Reference (Screenshot 1) | Jelenlegi PoseSnapshotOverlayView |
|---|---|---|
| **Forrás** | Külső live-camera test app | Tárolt event snapshot |
| **Bones renderelés** | Kék, vastag, kontrasztos | Fehér 3px — fix után MŰKÖDIK, de láthatóság gyenge zöld háttéren |
| **Joint méret** | ~12-14px | 8px |
| **Joint kontúr** | Igen (sötét outline) | Nincs |
| **Szín** | Egységes kék | Confidence-alapú yellow/orange/red |
| **Kontraszt** | Magas (kék + fehér háttér) | Alacsony fehér háttér előtt OK, zöld előtt gyenge |
| **FPS** | 29 (live) | N/A (event snapshot) |
| **Kontinuitás** | Folyamatos | ±500ms event ablak |

---

## 4. Miért volt csak pontfelhő

1. `@ViewBuilder` multi-binding `if let` bug → bones nem rendereltek → **JAVÍTVA**
2. Fehér szín fehér/világos háttéren → láthatatlan bones (ha van adat, de kontraszt gyenge)
3. Kis jointméret (8px) → könnyen elvész

**A build előtt készített screenshoton** (Screenshot 2) a régi kód futott. Az aktuális kódon a bone lines JAVÍTVA vannak.

---

## 5. Remaining visual quality gap — mit kell még javítani

### 5.1 Probléma: fehér vonalak kültéri zöld pályán

Fehér 3px vonal zöld háttéren (fű, turf) nem elég kontrasztos. A referencia kék-fehér kombináció sokkal erősebb vizuális jel.

### 5.2 Probléma: kis joint méret

8px circle méretű pont kicsi és könnyen elvész.

### 5.3 Probléma: nincs skeleton info state

Ha nincs snapshot ±500ms-ban, a skeleton toggle-t bekapcsolva SEMMI sem jelenik meg — nincs üzenet.

---

## 6. Implementációs terv

### 6.1 PR: AN-3B2C-2 — Skeleton renderer visual quality upgrade

**Scope:** `PoseSnapshotOverlayView.swift` + `JugglingAnnotationScreen.swift`  
**Backend változás:** Nincs  
**DB változás:** Nincs  

#### Változás 1: Double-stroke bone rendering (kontraszt bármilyen háttérre)

```swift
// Dark outline + colored inner line → látható MINDEN háttéren
private func boneLayer(byName: [String: BodyLandmarkDTO], w: CGFloat, h: CGFloat) -> some View {
    let segments: [(CGPoint, CGPoint)] = Self.bones.compactMap { (a, b) in
        guard let pa = byName[a], let pb = byName[b] else { return nil }
        return (
            CGPoint(x: CGFloat(pa.x) * w, y: CGFloat(pa.y) * h),
            CGPoint(x: CGFloat(pb.x) * w, y: CGFloat(pb.y) * h)
        )
    }
    return ZStack {
        // Outer dark stroke — creates halo/outline effect
        Path { path in
            for (s, e) in segments { path.move(to: s); path.addLine(to: e) }
        }
        .stroke(Color.black.opacity(0.55), lineWidth: 5)
        // Inner colored stroke
        Path { path in
            for (s, e) in segments { path.move(to: s); path.addLine(to: e) }
        }
        .stroke(Color.cyan.opacity(0.92), lineWidth: 2.5)
    }
}
```

#### Változás 2: Nagyobb, kontúros jointok

```swift
@ViewBuilder
private func jointLayer(w: CGFloat, h: CGFloat) -> some View {
    ForEach(keypoints.body, id: \.name) { lm in
        ZStack {
            // Dark outline for contrast
            Circle()
                .fill(Color.black.opacity(0.55))
                .frame(width: 14, height: 14)
            // Confidence-colored fill
            Circle()
                .fill(Self.jointColor(confidence: lm.confidence))
                .frame(width: 10, height: 10)
        }
        .position(x: lm.x * w, y: lm.y * h)
    }
}
```

#### Változás 3: Skeleton info state hiány esetén

A `JugglingAnnotationScreen`-ben, ha `showSkeletonOverlay == true` de nincs snapshot ±500ms-ban:

```swift
// Meglévő:
if showSkeletonOverlay, let snap = closestSnapshot(toMs: playback.currentTimestampMs) {
    PoseSnapshotOverlayView(...)
}

// Javított:
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

```swift
private var skeletonStatusBanner: some View {
    VStack {
        Spacer()
        Text(skeletonStatusText)
            .font(.system(size: 11, weight: .medium))
            .foregroundColor(.white.opacity(0.85))
            .padding(.horizontal, 10).padding(.vertical, 5)
            .background(Color.black.opacity(0.60))
            .cornerRadius(6)
            .padding(.bottom, 60) // above timeline
    }
    .allowsHitTesting(false)
}

private var skeletonStatusText: String {
    if poseSnapshots.isEmpty { return "Nincs skeleton adat ehhez a videóhoz" }
    return "Nincs skeleton adat ehhez az időponthoz"
}
```

### 6.2 Csontváz coverage összefoglaló (dokumentált limitek)

| Testrész | Elérhető | Megjegyzés |
|----------|----------|-----------|
| Fej/arc | ✓ | nose, left/right_eye, left/right_ear |
| Nyak | ✓ | neck |
| Vállak | ✓ | left/right_shoulder |
| Könyökök | ✓ | left/right_elbow |
| Csuklók | ✓ | left/right_wrist |
| Kezek/ujjak | ✗ | Vision 2D nem adja; iOS 17+ Hand Pose külön |
| Törzs (spine) | ✓ (részben) | neck–root, de nincs spine_mid |
| Csípő | ✓ | root, left/right_hip |
| Térdek | ✓ | left/right_knee |
| Bokák | ✓ | left/right_ankle |
| Lábfej/lábujj | ✗ | Vision 2D korlát, iOS 17+ Body Pose 3D kellene |

### 6.3 Tesztek

**Meglévő tesztek (maradnak):**
- `SkeletonOverlayTests.swift`: SK-OV-01..10 — `PoseSnapshotOverlayView.jointColor`, `BallVideoOverlayColorHelper.ballColor`

**Új tesztek (AN-3B2C-2):**
- SK-OV-11: `boneLayer` kettős stroke — `segments` lista nem üres ha byName tartalmaz bejegyzéseket
- SK-OV-12: joint méret 14px outer, 10px inner
- SK-OV-13: skeletonStatusText — üres poseSnapshots → "Nincs skeleton adat ehhez a videóhoz"
- SK-OV-14: skeletonStatusText — van snapshot de nem ±500ms → "Nincs skeleton adat ehhez az időponthoz"

### 6.4 Device QA terv

1. **Build telepítése** — elsőként a bone-fix buildet kell tesztelni (ez már kész)
2. **Skeleton toggle ON, 319eb833 videó, 0:00.549** → skeleton megjelenik vonalakkal ✓
3. **Skeleton toggle ON, 0:00.200** (nincs snapshot ±500ms) → banner szöveg megjelenik
4. **Zöld pályás videón**: kék/cyan vonalak + fekete kontúr → egyértelműen látható
5. **Fehér háttéren**: szintén látható (double stroke)

### 6.5 A fő videóképernyőn lesz látható

Igen. A `JugglingAnnotationScreen` ZStack-jébe van integrálva — nem event detail. A toggle a jobb felső sarokban mindig elérhető (`overlayToggleButton`). A `closestSnapshot(toMs:)` ±500ms ablakban keresi a legközelebbi snapshot-ot.

---

## 7. Mi az, amit jelen pillanatban a felhasználó lát (ha újrabuildelnek)

A **jelenlegi commit** (feat/an3b2b-1-ball-detection HEAD) tartalmazza:
- ✓ Single Path bone rendering (fix)
- ✓ Fehér 3px vonalak (látható, de kontrasztja javítható)
- ✓ 8px confidence-alapú pontok
- ✗ Skeleton info state hiánykor (néma eltűnés)
- ✗ Double-stroke kontraszt

A **AN-3B2C-2** PR után:
- ✓ Kék/cyan double-stroke vonalak (bármilyen háttéren látható)
- ✓ 10px colored + 14px dark-outline pontok
- ✓ "Nincs skeleton adat" banner hiány esetén

---

*Implementáció jóváhagyás után indulhat. A bone-fix (single Path) már a branchben van.*
