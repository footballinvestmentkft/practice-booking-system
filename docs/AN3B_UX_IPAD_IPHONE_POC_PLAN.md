# AN-3B UX & iPad–iPhone Multi-Camera POC Plan v2

**Date:** 2026-06-21
**Status:** Audit + plan — implementáció kizárólag külön jóváhagyás után.

---

## I. GoProConnectionDebugView elérhetősége — TÉNY

### Jelenlegi állapot

A `GoProConnectionDebugView` **NINCS bekötve semmilyen navigációs útvonalon**:
- Nem referálja `LFASpecTabView`
- Nem referálja `MainHubView`
- Nem referálja `JugglingVideoListView`
- Nem referálja semmilyen production SwiftUI View

**Bizonyíték:** `grep -rn "GoProConnectionDebugView" ios/LFAEducationCenter/ --include="*.swift" | grep -v Tests | grep -v "MultiCamera/GoPro"` → **0 result**.

### Fizikai GoPro smoke teszt végrehajtásának lehetőségei

| Megoldás | Kódmódosítás | Hol | Scope |
|----------|-------------|-----|-------|
| A) Xcode Preview / debug scheme root view swap | Nem commit | Lokális Xcode session | Nincs PR scope |
| B) **`#if DEBUG` test harness gomb a ProfileTab-on** | Igen, minimális | PR #317 kiegészítés | Debug-only |
| C) Külön mini PR: test harness | Igen | Külön branch | Tiszta scope |

### Ajánlás: **B opció — #if DEBUG test harness PR #317-en belül**

```swift
// LFAProfileTab — meglévő view-ban, CSAK debug buildben:
#if DEBUG
Button("🔧 GoPro Debug") {
    isShowingGoProDebug = true
}
.fullScreenCover(isPresented: $isShowingGoProDebug) {
    GoProConnectionDebugView(manager: GoProConnectionManager(
        bleTransport: CoreBluetoothBLETransport(),
        httpTransport: GoProHTTPClientTransport(),
        wifiTransport: SystemWiFiTransport()
    ))
}
#endif
```

**Ez NEM production UI bővítés.** Kizárólag debug buildben elérhető, a Profile tab alján. A release buildben nem jelenik meg. A production transport implementációk (CoreBluetoothBLETransport, etc.) szükségesek ehhez — ezek is PR #317 scope-ban készülhetnek mint internal implementation.

### Pontos fizikai teszt lépések HERO12-vel

| # | Lépés | Eszköz |
|---|-------|--------|
| 1 | Xcode: Build & Run (Debug scheme) → iPhone | iPhone + Xcode |
| 2 | App: Login → MainHubView → LFA Card → LFASpecTabView → Profile tab | iPhone |
| 3 | Profile tab alján: "🔧 GoPro Debug" gomb (csak debug buildben) | iPhone |
| 4 | GoPro: bekapcsolás | HERO12 |
| 5 | Debug screen: "Connect GoPro" tap | iPhone |
| 6 | iOS: Bluetooth permission prompt → Allow | iPhone |
| 7 | GoPro discovery → peripheral megjelenik | iPhone screen |
| 8 | BLE connect → service discovery → notification subscription | Automatikus |
| 9 | AP activation → Wi-Fi SSID megjelenik | iPhone |
| 10 | iOS: Local Network / Wi-Fi join prompt → Allow/Manual | iPhone |
| 11 | HTTP reachability → firmware query → status | iPhone screen |
| 12 | **Ready state** elérve | iPhone screen |
| 13 | Disconnect → idle | iPhone |
| 14 | Reconnect (3×) | iPhone |
| 15 | GoPro kikapcsolása → discovery timeout → failed | iPhone |

---

## II. Lobby polling vs Recording start — szétválasztva

### Lobby: backend polling (5s) — elfogadható

```
iPad polls: GET /multicamera/sessions/{id} every 5s
  → participants list, device states, readiness
iPhone polls: same endpoint, same interval
```

### Recording start: **időzített start (server timestamp)**

A polling **NEM alkalmas** szinkron kamera-starthoz. Megoldás:

```
1. Coordinator (iPad) küld: POST /multicamera/sessions/{id}/start
2. Backend válaszol: { "start_at_unix_ms": 1719000005000 } (5s a jövőben)
3. Minden kliens a következő poll-ban megkapja a start_at-ot
4. Minden eszköz a SAJÁT clock-ja szerint vár a start_at-ig
5. Clock offset: NTP-szinkronizált iPhone/iPad → < 20ms eltérés
6. A tényleges felvétel start = max(start_at, device_ready)
7. Pontos alignment: offline audio sync (PR-4B5)
```

**A server-issued `start_at` nem frame-pontos szinkron — ez a durva szinkronizáció.** A pontos alignment az audio cross-correlation feladata (PR-4B5).

| Szinkronizációs réteg | Pontosság | Mechanizmus | PR |
|----------------------|-----------|-------------|-----|
| Szerver start_at | ±50-200ms | NTP clock + polling delay | PR-4B3 |
| Offline audio sync | < 16ms | Cross-correlation | PR-4B5 |
| Frame-level alignment | ±8-35ms | PTS matching + drift | PR-4B5 |

---

## III. PR Gate-ek — javított hozzárendelés

### PR-4B3A gates (instructor navigation + session create/join)

| Gate | Target | Típus |
|------|--------|-------|
| Instructor tab: INSTRUCTOR role-only | 100% | RELEASE |
| Session create: session_id + invite code generated | Functional | RELEASE |
| iPhone join via code: participant + device registered | Functional | RELEASE |
| Lobby polling: both devices visible | Functional | RELEASE |
| iPad + iPhone build: SUCCEEDED | — | RELEASE |

### PR-4B3B gates (dual local capture)

| Gate | Target | Típus |
|------|--------|-------|
| Coordinator start_at issued | Functional | RELEASE |
| Both cameras begin recording within 1s of start_at | POC | POC |
| Two local video files created | Functional | RELEASE |
| Start/stop timestamps in capture_stream records | Functional | RELEASE |
| Session state: recording → stopping → completed | Functional | RELEASE |

### PR-4B5 gates (audio sync) — NEM PR-4B3

| Gate | Target | Típus |
|------|--------|-------|
| Audio offset < 16ms | — | RELEASE (PR-4B5 only) |
| Frame alignment p95 < 35ms | — | RELEASE (PR-4B5 only) |

---

## IV. Juggling Challenge terméklogika

### A. Jelenlegi Juggling flow (videó-alapú, egyéni)

```
Player uploads video → AI annotates contacts → Player labels events
→ Skeleton + ball tracking → Performance score (contacts/min, technique)
```

Jelenleg **nincs** challenge/versus/opponent fogalom a Juggling modulban. A backend `juggling.py` nem tartalmaz challenge modellt.

### B. Kétkamerás challenge — terméktervezés

| Elem | Leírás |
|------|--------|
| **Challenger** | Az a player, aki a challenge-et indítja (iPad coordinator választja ki) |
| **Opponent** | Az a player, aki elfogadja (iPhone-nal csatlakozik) |
| **Challenge type** | Juggling endurance (max contacts in X seconds) |
| **Kör** | Egyidejű recording: mindkét player ugyanazt a feladatot végzi, saját kameránézet rögzíti |
| **Teljesítmény mérés** | Contacts/min (automatikus, AI-annotated), contact quality, technique score |
| **Eredmény** | Challenger vs Opponent score → Winner / Tie / Forfeit |
| **Kapcsolódás meglévő flow-hoz** | A recording → annotation pipeline ugyanaz marad; a session metadata köti össze a két videót |

### C. Challenge lifecycle

```
1. Instructor (iPad): Create Challenge Session
   → topology=dual_player_onsite, challenge_type=juggling_endurance
   → duration=60s
2. Invite player(s) via code
3. Both players join, devices ready
4. Coordinator starts → both record simultaneously
5. Timer expires OR coordinator stops
6. Both videos → AI annotation pipeline
7. Scores calculated independently
8. Results: side-by-side comparison, winner declared
```

### D. Külön PR scope?

A challenge scoring és eredménylogika **NEM fér biztonságosan a dual capture scope-ba**. Ajánlás:
- **PR-4B3A/3B:** session create/join/lobby + dual capture (infrastruktúra)
- **PR-4B-CHALLENGE:** scoring, timer, results, winner declaration (terméklogika)

---

## V. iPhone user flow — részletes

### JugglingVideoListView módosítás (PR-4B3A)

```
┌──────────────────────────���───────────┐
│ My Videos                        + ↻ │
├────────────────────���─────────────────┤
│ [🏆 Active Challenge] (ha van)       │  ← Új banner (PR-4B3A)
│   "Challenge from Coach — tap to join"│
├────────────────────────────���─────────┤
│ [Solo Training]        [Challenge ▸] │  ← Új action row
├──────────────────────────────────────┤
│ Video 1...                           │
│ Video 2...                           │
└──────────────────────────────────────┘
```

| Elem | Navigáció | PR |
|------|-----------|-----|
| "Active Challenge" banner | → SessionJoinView (lobby) | PR-4B3A |
| "Solo Training" | → JugglingVideoListView (unchanged) | — |
| "Challenge" | → ChallengeListView (join/create) | PR-4B-CHALLENGE |

### SessionJoinView (iPhone)

```
┌──────────────────────────────────────┐
│ Challenge Lobby                      │
├───────────────────────────���──────────┤
│ Session: abc123                      │
│ Instructor: Coach Name               │
│ Opponent: [waiting...]               │
│                                      │
│ Your device: Ready ✓                 │
│ Camera: Rear 1080p/30 ✓             │
│                                      │
│ [Waiting for coordinator to start]   │
└──────────────────────────────────────┘
```

### SessionRecordingView (iPhone)

```
┌──────────────────────────────────────┐
│ ● REC  0:23 / 1:00         Juggling │
│                                      │
│ [Camera feed — full screen]          │
│                                      │
│ Contacts: 14                         │
└──────────────────────────────────────┘
```

---

## VI. iPad instructor flow — szétválasztva

### A. Instructor Training Session (1 player + instruktor figyelés)

```
Session tab → Create Training → Select player → Start
→ iPad records (kamera nézet) + Player iPhone records
→ Instruktor megfigyeli, jegyzetel
→ Post-session: review, annotation, feedback
```

### B. Two-player Challenge (2 player egymás ellen)

```
Session tab → Create Challenge → Invite 2 players
→ Lobby (2 player + instructor)
→ Coordinator start → Both players record
→ AI scoring → Results → Winner
```

### C. GoPro Connection Test (debug/research)

```
Profile tab → #if DEBUG → GoPro Debug screen
→ BLE discovery → connect → status → disconnect
(Nincs session, nincs recording, nincs capture)
```

**Ezek 3 különálló state machine és UI flow**, nem egyetlen összemosott képernyő.

---

## VII. GoPro Production Transport — egyetlen definíció

| Komponens | Leírás | PR |
|-----------|--------|-----|
| `GoProBLETransport` protocol | PR-4B1 ✅ | Kész |
| `GoProHTTPTransport` protocol | PR-4B1 ✅ | Kész |
| `GoProWiFiTransport` protocol | PR-4B1 ✅ | Kész |
| `CoreBluetoothBLETransport` (production impl) | **PR-4B1** #317 kiegészítés (debug harness-hez kell) | PR-4B1 |
| `GoProHTTPClientTransport` (production impl) | **PR-4B1** #317 | PR-4B1 |
| `SystemWiFiTransport` (production impl) | **PR-4B1** #317 | PR-4B1 |
| Recording orchestration (start/stop GoPro recording) | **PR-4B4** | PR-4B4 |
| Media transfer (file download) | **PR-4B4** | PR-4B4 |

**Egyértelmű határ:** PR-4B1 = connection (BLE+WiFi+HTTP). PR-4B4 = recording + transfer.

---

## VIII. Módosított PR-bontás

| PR | Scope | Előfeltétel |
|----|-------|-------------|
| **PR-4B1** (#317) | GoPro connection SM + production transports + #if DEBUG test harness | PR-4A ✅ |
| **PR-4B2** | Multi-device session contract (models, schemas, repository, migration) | PR-4B1 |
| **PR-4B3A** | Instructor role navigation + session create/join/lobby + API endpoints | PR-4B2 |
| **PR-4B3B** | iPad + iPhone dual local capture + server-issued start_at | PR-4B3A |
| **PR-4B4** | GoPro recording orchestration + 3-camera session + media transfer | PR-4B3B |
| **PR-4B5** | Audio sync (per-pair cross-correlation + drift + frame matching) | PR-4B3B |
| **PR-4B-CHALLENGE** | Challenge scoring, timer, results, winner logic | PR-4B3B |
| **PR-4B6** | Physical per-topology benchmark | PR-4B4 + PR-4B5 |

---

## IX. Végső válaszok

### 1. Tudod-e MOST a PR #317 buildjében fizikailag megnyitni a GoPro debug képernyőt?

**NEM.** A `GoProConnectionDebugView` sehol nincs bekötve a navigációba. A production transport implementációk (`CoreBluetoothBLETransport` etc.) sem léteznek — a PR-4B1 csak protocol + mock tartalmaz.

### 2. Pontosan milyen lépéseket kell végrehajtani?

1. **Implementálni kell** a production transport-okat (CoreBluetooth, URLSession, NEHotspot wrapper)
2. **Hozzáadni** `#if DEBUG` gomb a Profile tab-hoz
3. **Build & Run** debug scheme-mel iPhone-ra
4. Navigate: Login → Hub → LFA Card → Profile → "GoPro Debug"
5. A HERO12-vel végrehajtani a 15 lépéses smoke tesztet (Section I.)

### 3. Kell-e ehhez kódmódosítás?

**IGEN.** A PR #317-hez 2 kiegészítő commit szükséges:
- Production transport implementations (CoreBluetooth + URLSession + WiFi wrappers)
- `#if DEBUG` test harness bekötés a Profile tab-ba

Ezek **debug-only** scope, **NEM** production UI bővítés.

### 4. Mi a legkisebb fejlesztési lépés, amely után az iPad és iPhone két userként játszhat?

| Szükséges | PR | Leírás |
|-----------|-----|--------|
| Session contract (DB + schemas) | PR-4B2 | 5 tábla, session model |
| Backend API (create/join/start/stop) | PR-4B3A | 6 endpoint |
| Instructor tab + lobby UI | PR-4B3A | Role-based nav + session UI |
| iPhone join + camera recording | PR-4B3B | Player kliens |
| iPad camera recording | PR-4B3B | Instructor capture |

**Minimum 4 PR** (PR-4B1 merge + PR-4B2 + PR-4B3A + PR-4B3B) kell ahhoz, hogy két eszköz valóban egymás ellen játsszon.

---

**Implementációt, branchet vagy PR-t külön jóváhagyás nélkül nem kezdünk. A PR #317 scope-ja nem bővül production UI-val; a debug harness és production transports PR-4B1 #317 scope.**
