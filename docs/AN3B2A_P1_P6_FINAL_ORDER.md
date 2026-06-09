# AN-3B2A — P1–P6 Végleges Implementációs Sorrend

Státusz: **TERV — implementáció nem kezdődött el.**
Branch: `feat/ios-juggling-annotation-an3b2a`.
Előzmény: `docs/AN3B2A_E2E_AUDIT_AND_PLAN.md` (audit + P0–P6 vázlat, elfogadva),
P0 lezárva (commit `4974503e`, `d20b9288`).

Ez a dokumentum a korábbi audit P1–P6 vázlatát commit-szintű,
végrehajtható sorrenddé bontja. **Minden fázis külön jóváhagyás után
indul.** A user 6 fő fázisra kért bontást — ez 1:1 megfelel a korábbi
audit P1–P6 szakaszainak, itt csak commit-bontásra, fájllistára,
CTA-kra, state transitionökre, tesztekre és acceptance criteria-kra
részletezve.

---

## P0 utóellenőrzés — „Last save: —” jelentése (kódalapú megerősítés)

Megerősítve `AnnotationDiagnostics.swift:76-83` és
`JugglingAnnotationViewModel.swift` (`diagnostics` property):

- `AnnotationDiagnosticsSnapshot.lastSaveResult` default értéke `.none`
  (→ `"—"`), és **csak** `onDisappear()` (sikeres/sikertelen `save()`
  után) vagy `markTimestamp(ms:)` (sikeres/sikertelen `save()` után)
  állítja át `.success`/`.failed(...)`-re.
- `onAppear()` **nem** ír a `lastSaveResult`-ba — csak `loadResult` és
  `lastLoadAt`-ot frissíti.
- Minden képernyőmegnyitás új `@StateObject` VM-et hoz létre (lásd audit
  2.3), tehát a `diagnostics` snapshot is friss (`.none`) minden
  megnyitáskor.

**Következtetés**: `Last save: —` egy adott megnyitásnál önmagában
**nem hiba** — azt jelenti, hogy ebben a VM-életciklusban még nem
történt írási kísérlet, miközben a `loaded (4 drafts)` bizonyítja, hogy
a korábbi mentés(ek) sikeresek voltak és a fájl olvasható. Hiba csak
akkor állna fenn, ha `Last save: failed: ...` jelenne meg, vagy ha
`loadResult` `quarantined`/`notFound` lenne ott, ahol korábban már volt
mentett adat.

---

## P1 — Mentés és bezárás + mentési státusz + X megerősítés

**Cél**: a marking mód CTA-zsákutcájának megszüntetése; mentési állapot
mindig látható; X gomb nem zár csendes adatvesztéssel.

### Érintett fájlok
- `ios/LFAEducationCenter/Juggling/Annotation/JugglingAnnotationViewModel.swift`
- `ios/LFAEducationCenter/Juggling/Annotation/Screen/JugglingAnnotationScreen.swift`
- `ios/LFAEducationCenterTests/Juggling/Annotation/JugglingAnnotationViewModelTests.swift`

### CTA-k
- Toolbar badge: "N jelölés" (unlabeled + labelPending összesen).
- Mentési státusz indikátor a badge mellett: "Mentve" / "Mentés…" / "Hiba".
- Új elsődleges CTA a képernyő alján: **"Mentés és bezárás"**.
- X gomb: ha `saveError == nil` → megerősítő dialog → mentés + dismiss.
  Ha mentés hibát dob → alert, screen **nem záródik be**.

### State transitions
- `JugglingAnnotationViewModel`:
  - új `@Published private(set) var isSaving: Bool = false` — minden
    `localStore.save()` hívás köré `isSaving = true` → `false`.
  - `saveStatus: SaveStatus { .saving | .saved | .failed }` computed
    property `isSaving` + `saveError`-ból.
  - `onDisappear()` szignatúra → `@discardableResult func onDisappear() -> Bool`
    (true = sikeres mentés vagy nincs aktív session; false = `saveError != nil`).
  - Új `func clearSaveError()`.
- `JugglingAnnotationScreen`:
  - `@State private var showCloseConfirm = false`
  - `@State private var showSaveErrorAlert = false`
  - X tap → `showCloseConfirm = true` → confirm → `vm.onDisappear()` →
    siker: dismiss; hiba: `showSaveErrorAlert = true`, marad a screen.
  - "Mentés és bezárás" gomb → ugyanaz a logika, explicit hívással.

### Tesztek (unit)
- `JugglingAnnotationViewModelTests`:
  - `test_AN3B2A_P1_onDisappear_returnsTrueOnSuccessfulSave`
  - `test_AN3B2A_P1_onDisappear_returnsFalseAndSetsSaveErrorOnFailure`
  - `test_AN3B2A_P1_saveStatus_reflectsIsSavingAndSaveError`
  - `test_AN3B2A_P1_clearSaveError_resetsSaveError`

### Manuális acceptance criteria
1. Jelölj 3 eseményt → badge "3" → státusz "Mentve" (rövid "Mentés…"
   átmenet látható, ha érzékelhető).
2. "Mentés és bezárás" → visszajutsz a `JugglingVideoListView`-ra.
3. Nyisd meg ugyanazt a videót → 3 esemény + badge "3" (debug overlay:
   `loaded (3 drafts)`).
4. Mentési hiba szimulálása (pl. `chmod 444` a session fájlra, vagy
   write-protected dir) → X gomb → confirm → alert jelenik meg, screen
   NEM záródik be; debug overlay: `Last save: failed: ...`.

### Commit-bontás
- **P1-1**: `unlabeledCount`/`labelPendingCount` badge a toolbar-ban.
- **P1-2**: `isSaving`/`saveStatus` VM-ben + UI indikátor.
- **P1-3**: `onDisappear() -> Bool`, X megerősítés + alert, "Mentés és
  bezárás" CTA, `clearSaveError()`.

---

## P2 — EventLabelingScreen + egyedi event-szintű contact type hozzárendelés

**Cél**: Phase 1 (marking) → Phase 2 (labeling) átmenet; minden
`.unlabeled`/`.labelPending` esemény egyedi kontakt-típus/oldal/
megbízhatóság hozzárendelése.

### Érintett fájlok
- `ios/LFAEducationCenter/Juggling/Annotation/Screen/JugglingAnnotationScreen.swift`
- `ios/LFAEducationCenter/Juggling/Annotation/Screen/EventLabelDetailView.swift` (ÚJ)
- `ios/LFAEducationCenter/Juggling/Annotation/JugglingAnnotationViewModel.swift`
- `ios/LFAEducationCenter.xcodeproj/project.pbxproj` (új fájl regisztrálása)
- `ios/LFAEducationCenterTests/Juggling/Annotation/JugglingAnnotationViewModelTests.swift`

### CTA-k
- **"Tovább a címkézéshez"** — megjelenik, ha `unlabeledCount > 0`,
  P1 "Mentés és bezárás" mellett másodlagos CTA-ként. Tap →
  `enterLabelingMode()` → mód váltás `labeling`-re.
- Esemény-sor tap (labeling módban, `.labelPending`/`.localOnly`
  állapotú draft-on) → push/sheet `EventLabelDetailView`.
- `EventLabelDetailView`: kontakt-típus választó (taxonómia csoportok),
  oldal (bal/jobb/center, taxonómia-függő), `annotationConfidence`
  segmented control (`certain`/`probable`/`uncertain`), opcionális
  custom label/description, "Mentés" gomb.

### State transitions
- `AnnotationScreenMode` enum (`marking`/`labeling`) — `@Published` a
  VM-ben, levezetve: `labeling`, ha `labelPendingCount > 0` VAGY explicit
  `enterLabelingMode()` hívás történt és van legalább egy aktív esemény.
- Új VM metódus:
  ```swift
  func labelEvent(deviceEventId: UUID, contactType: String, side: String?,
                   annotationConfidence: String, customLabel: String?,
                   customDescription: String?) -> Bool
  ```
  `.unlabeled`/`.labelPending` → mezők beállítása → `.localOnly` →
  `localStore.save()`. Hiba esetén `saveError`, draft visszaáll előző
  állapotba (rollback, az `editEvent` mintájára).
- `EventTimelineView`/`eventRow` pin-szín: `.labelPending` (szürke) →
  `.localOnly` (narancs) `labelEvent` után.

### Tesztek (unit)
- `JugglingAnnotationViewModelTests`:
  - `test_AN3B2A_P2_labelEvent_transitionsLabelPendingToLocalOnly`
  - `test_AN3B2A_P2_labelEvent_setsContactTypeSideConfidence`
  - `test_AN3B2A_P2_labelEvent_rollsBackOnSaveFailure`
  - `test_AN3B2A_P2_enterLabelingMode_setsModeWhenUnlabeledExists`
- Új `EventLabelDetailViewTests` (ha snapshot/logic tesztelhető iOS
  14-en): picker → `labelEvent` hívás paraméter-ellenőrzés.

### Manuális acceptance criteria
1. Marking módban 2 esemény → "Tovább a címkézéshez" → labeling mód,
   mindkét pin szürke (`.labelPending`).
2. Tap az első sorra → `EventLabelDetailView` → válassz típust
   (pl. "Belső csap"), oldalt ("Jobb"), confidence "certain" → Mentés
   → pin narancs (`.localOnly`), debug overlay `Last save: success`.
3. Zárd be ("Mentés és bezárás") + nyisd meg újra → labeling mód,
   az első esemény továbbra is `.localOnly` és a beállított típust
   mutatja a sorban.
4. A második esemény továbbra is `.labelPending` (szürke).

### Commit-bontás
- **P2-1**: `AnnotationScreenMode` + "Tovább a címkézéshez" CTA + mód
  state.
- **P2-2**: `EventLabelDetailView` (ÚJ fájl, pbxproj regisztráció) +
  `labelEvent()` VM metódus.
- **P2-3**: navigáció a sor tap-re labeling módban + pin-szín frissítés.

---

## P3 — Labeling completeness + Finish readiness UI

**Cél**: a "Címkézés befejezése és feldolgozás" CTA előfeltételeinek
explicit megjelenítése; konfliktusok feloldhatók.

### Érintett fájlok
- `ios/LFAEducationCenter/Juggling/Annotation/Screen/JugglingAnnotationScreen.swift`
- `ios/LFAEducationCenter/Juggling/Annotation/Screen/ConflictResolutionView.swift` (ÚJ)
- `ios/LFAEducationCenter.xcodeproj/project.pbxproj`
- `ios/LFAEducationCenterTests/Juggling/Annotation/JugglingAnnotationViewModelTests.swift`

### CTA-k
- Readiness banner (labeling mód, a lista felett):
  - `.readyZero` / `.readyWithCount(n)` → zöld banner, "Címkézés
    befejezése és feldolgozás" CTA **aktív**.
  - `.blocked(statuses)` → sárga/piros banner, felsorolja az okokat
    (pl. "1 esemény még nincs címkézve", "1 esemény konfliktus —
    koppints a feloldáshoz"); CTA **inaktív**.
- Konfliktus-sor tap (`.conflicted` pin) → `ConflictResolutionView` sheet:
  szerver vs. lokális verzió összehasonlítás, "Szerver verzió
  megtartása" / "Saját verzió megtartása" gombok.
- `.readyZero` esetén Finish CTA tap → megerősítő dialog: "Nincs
  rögzített esemény. Biztosan befejezed annotáció nélkül?" →
  `confirm_zero_contacts=true`.

### State transitions
- `finishReadiness` (már létező VM computed property) kiértékelése a
  Screen-ben minden `session` változásnál.
- `pendingConflictId` (már létező) → `ConflictResolutionView`
  megjelenítés trigger (`.sheet(item:)`).
- `acceptServerVersion()` / `keepLocalVersion()` (már létező VM
  metódusok) → `pendingConflictId = nil`, readiness újraszámol.

### Tesztek (unit)
- `JugglingAnnotationViewModelTests`:
  - `test_AN3B2A_P3_finishReadiness_blockedWhenLabelPendingExists`
  - `test_AN3B2A_P3_finishReadiness_blockedWhenConflictedExists`
  - `test_AN3B2A_P3_finishReadiness_readyZeroWhenNoEvents`
  - `test_AN3B2A_P3_acceptServerVersion_clearsConflictAndUpdatesReadiness`
  - `test_AN3B2A_P3_keepLocalVersion_clearsConflictAndUpdatesReadiness`

### Manuális acceptance criteria
1. 1 esemény `.labelPending` állapotban → Finish CTA inaktív, banner:
   "1 esemény még nincs címkézve".
2. Címkézd le (P2) → banner zöldre vált, CTA aktív.
3. Konfliktus szimulálása (backend PATCH másik kliensről / manuális DB
   verzió-bump) → `.conflicted` pin → tap → `ConflictResolutionView` →
   "Szerver verzió megtartása" → readiness frissül.
4. 0 eseményes videón → Finish CTA tap → megerősítő dialog →
   `confirm_zero_contacts=true` továbbadva P4-nek.

### Commit-bontás
- **P3-1**: readiness banner + Finish CTA enable/disable.
- **P3-2**: `ConflictResolutionView` (ÚJ) + sheet bekötés.
- **P3-3**: 0-esemény finish megerősítő dialog.

---

## P4 — Backend sync + retry/offline kezelés

**Cél**: a már implementált `flushPending()`/`finish()` logika
bekötése a UI-ba; offline kezelés látható és helyes.

### Érintett fájlok
- `ios/LFAEducationCenter/Juggling/Annotation/Screen/JugglingAnnotationScreen.swift`
- `ios/LFAEducationCenter/Juggling/Annotation/JugglingAnnotationViewModel.swift`
- `ios/LFAEducationCenter/Juggling/Annotation/JugglingAnnotationAPIClient.swift`
  (csak P4-3, opcionális)
- `ios/LFAEducationCenter/Juggling/Annotation/AnnotationSyncEngine.swift`
  (csak P4-3, opcionális)
- `ios/LFAEducationCenterTests/Juggling/Annotation/AnnotationSyncEngineTests.swift`

### CTA-k
- Nincs új explicit CTA `flushPending()`-hez (fire-and-forget,
  háttérben fut).
- "Címkézés befejezése és feldolgozás" CTA (P3-ból aktiválva) → tap →
  `finish()`.
- `finishError` esetén "Újra" gomb az alert-ben.

### State transitions
- `onAppear()` után + minden `markTimestamp`/`labelEvent`/`editEvent`
  után (amikor `.localOnly`-ra vált egy draft) → `Task { await
  vm.flushPending() }` fire-and-forget.
- Opcionális: `scenePhase == .active` → `flushPending()` (foreground
  visszatérés).
- Finish CTA tap → `isFinishing = true` → `await vm.finish()`:
  - siker → `finishResult != nil` → `isFinishing = false` → P5 navigáció.
  - hiba → `finishError != nil` → `isFinishing = false` → alert,
    "Újra" → újra `finish()`.

### Tesztek (unit)
- `AnnotationSyncEngineTests`:
  - meglévő `flushPending`/`pushCreate`/`pushPatch`/`pushDelete` tesztek
    regressziója (nincs új logika, csak hívási pont).
- `JugglingAnnotationViewModelTests`:
  - `test_AN3B2A_P4_onAppear_triggersFlushPendingWhenLocalOnlyEventsExist`
  - `test_AN3B2A_P4_finish_setsFinishResultOnSuccess`
  - `test_AN3B2A_P4_finish_setsFinishErrorOnFailure_andRetryWorks`
- **P4-3 (opcionális)** — ha implementálva:
  - `batchSubmit` protokoll-bővítés tesztje + `AnnotationSyncEngine`
    batch hívás teszt.

### Manuális acceptance criteria
1. Online módban 3 eseményt címkézz (P2) → pár másodperc múlva mindhárom
   pin zöld (`.synced`), explicit "Sync" gomb nélkül.
2. Finish CTA → loading overlay → siker → `GET
   /users/me/juggling/videos` listában a videó `annotation_status ==
   human_review_pending`.
3. Repülő-mód bekapcsolása `finish()` közben → `finishError` alert →
   "Újra" → repülő-mód kikapcsolása → siker.
4. Offline címkézés: repülő-mód, jelölj+címkézz 1 eseményt → `.localOnly`
   marad, readiness `.blocked` (P3 banner: "1 esemény szinkronizálás
   alatt/offline") → repülő-mód ki → automatikus `.synced`.

### Commit-bontás
- **P4-1**: `flushPending()` bekötés (`onAppear` + state-change
  trigger-ek).
- **P4-2**: Finish CTA → `finish()`, loading/hiba/retry UI.
- **P4-3 (opcionális, csak ha P2 után mérhető teljesítményprobléma)**:
  `batchSubmit` integrálása.

---

## P5 — Finish/feldolgozás indítása + completed állapot

**Cél**: a lezárt annotáció láthatóvá tétele; biztonságos újranyitás
lezárt videóhoz.

### Érintett fájlok
- `ios/LFAEducationCenter/Juggling/Annotation/Screen/AnnotationFinishedView.swift` (ÚJ)
- `ios/LFAEducationCenter/Juggling/Annotation/Screen/JugglingAnnotationScreen.swift`
- `ios/LFAEducationCenter/Juggling/JugglingVideoListView.swift`
- `ios/LFAEducationCenter/Juggling/Annotation/JugglingAnnotationViewModel.swift`
- `ios/LFAEducationCenter.xcodeproj/project.pbxproj`
- `ios/LFAEducationCenterTests/Juggling/**`

### CTA-k
- `AnnotationFinishedView`: összefoglaló (`total_juggling_count`,
  `annotation_finished_at`), **"Kész"** gomb → dismiss vissza
  `JugglingVideoListView`-ra.
- `JugglingVideoListView`: lezárt videóknál (`annotationStatus` ∈
  `human_review_pending`/`annotated`/`reviewed`) a play+annotate gomb
  helyett **"Megtekintés"** ikon → read-only `AnnotationFinishedView`
  (`mode: .reviewExisting`).

### State transitions
- `finishResult != nil` (P4-ből) → `AnnotationFinishedView(mode:
  .justFinished)` sheet/fullScreenCover → "Kész" → dismiss.
- `JugglingAnnotationViewModel.onAppear()`: ha `localStore.load()`
  `.notFound` ÉS a `video.annotationStatus` már lezárt állapotban van →
  **nem** hoz létre friss `in_progress` session-t, helyette jelez a
  Screen-nek (`@Published var shouldShowReadOnlyResult: Bool`), amely
  `AnnotationFinishedView(mode: .reviewExisting)`-et nyit (GET
  `/contacts` a végleges lista lekéréséhez).
- `AnnotationFinishedView` egy komponens, `mode: .justFinished |
  .reviewExisting` paraméterrel (a 7.3 nyitott kérdés válasza: egy
  komponens, kevesebb duplikáció).

### Tesztek (unit)
- `JugglingAnnotationViewModelTests`:
  - `test_AN3B2A_P5_onAppear_setsShouldShowReadOnlyResult_whenAnnotationStatusClosed`
  - `test_AN3B2A_P5_onAppear_doesNotCreateFreshSession_whenAnnotationStatusClosed`
- Új `AnnotationFinishedViewTests` (ha tesztelhető): `mode` →
  megfelelő CTA/szöveg renderelés.
- `JugglingVideoListViewModelTests`: lezárt videó → "Megtekintés" ikon
  megjelenése.

### Manuális acceptance criteria
1. Fejezz be egy annotációt (P4) → `AnnotationFinishedView
   (.justFinished)` megjelenik az összefoglalóval → "Kész" → lista.
2. A lista adott sora most "Megtekintés" ikont mutat.
3. Tap → read-only összefoglaló, a korábban rögzített események
   listája látszik, **nincs FAB, nincs swipe-delete, nincs Mentés
   CTA**.
4. Próbáld a régi módon (marking screen) megnyitni egy lezárt videót
   (ha van ilyen útvonal) → ne engedje, vagy kizárólag olvasásra
   nyisson.

### Commit-bontás
- **P5-1**: `AnnotationFinishedView` (ÚJ, `mode` paraméterrel) +
  `.justFinished` bekötés a Finish sikerből.
- **P5-2**: `JugglingVideoListView` "Megtekintés" ikon + `onAppear()`
  read-only redirect lezárt videóhoz.

---

## P6 — Teljes manuális E2E + regressziós teszt

**Cél**: a teljes flow (P1–P5) egyetlen végpontig-végpontig
forgatókönyvben validálva, plusz a teljes meglévő unit suite zöld.

### Érintett fájlok
- `ios/LFAEducationCenterTests/Juggling/**` (minden P1–P5 unit teszt
  végső regressziós futtatása)
- `docs/AN3B2A_E2E_MANUAL_TEST.md` (ÚJ)

### CTA-k / state transitions
Nincs új kód — ez a fázis kizárólag tesztelés és dokumentáció.

### Tesztek
- **P6-1**: minden P1–P5 unit teszt megírva és zöld (ha valamelyik
  fázisnál elmaradt).
- **P6-3**: `xcodebuild test` — teljes
  `LFAEducationCenterTests` + `LFAEducationCenterUITests` (ha van) suite,
  AN-1..AN-3B2A összes meglévő tesztcsoportja zöld, 0 reggresszió.

### Manuális acceptance criteria (= teljes projekt acceptance criteria)
1. Videó megnyitása → marking mód, korábbi események visszatöltődnek
   (debug overlay igazolja: `loaded (N drafts)`).
2. FAB jelölés → azonnali "Mentve" státusz (P1).
3. "Mentés és bezárás" → lista, állapot megmarad, folytatható (P1).
4. "Tovább a címkézéshez" → labeling mód, minden esemény egyedileg
   címkézhető (P2).
5. Readiness banner pontosan jelzi a blokkoló okokat; konfliktus
   feloldható (P3).
6. "Címkézés befejezése és feldolgozás" → `flushPending` + `finish()`
   → siker esetén `human_review_pending` (P4).
7. `AnnotationFinishedView` megjelenik, lista "Megtekintés" módba vált
   (P5).
8. Hibakezelés: mentési/finish hiba mindig látható alert/banner, sosem
   csendes adatvesztés (P1, P4).
9. Offline: jelölés/címkézés offline is megmarad lokálisan
   (`.localOnly`/`.unlabeled`), online visszatéréskor automatikusan
   szinkronizálódik (P4).
10. iOS 14 deployment target nem sérül — minden új View iOS
    14-kompatibilis API-kat használ.

### Commit-bontás
- **P6-1**: hiányzó unit tesztek pótlása (ha van).
- **P6-2**: `docs/AN3B2A_E2E_MANUAL_TEST.md` — lépésről lépésre
  forgatókönyv + elvárt UI/backend állapot minden lépésnél.
- **P6-3**: teljes regressziós futtatás + eredmény dokumentálása.

---

## Összefoglaló commit-sorrend

```
P1-1  esemény-számláló badge
P1-2  mentési státusz indikátor (isSaving/saveStatus)
P1-3  Mentés és bezárás CTA + X megerősítés + onDisappear()->Bool
P2-1  Tovább a címkézéshez CTA + AnnotationScreenMode
P2-2  EventLabelDetailView (ÚJ) + labelEvent()
P2-3  labeling navigáció + pin-szín frissítés
P3-1  finish readiness banner + CTA enable/disable
P3-2  ConflictResolutionView (ÚJ) + sheet bekötés
P3-3  0-esemény finish megerősítő dialog
P4-1  flushPending() bekötés (onAppear + state-change triggerek)
P4-2  finish() bekötés + loading/hiba/retry UI
P4-3  batchSubmit (opcionális, csak teljesítményprobléma esetén)
P5-1  AnnotationFinishedView (ÚJ, mode: justFinished|reviewExisting)
P5-2  JugglingVideoListView "Megtekintés" + read-only redirect
P6-1  hiányzó unit tesztek pótlása
P6-2  E2E manuális teszt doksi
P6-3  teljes regresszió
```

Minden commit után: build + unit teszt zöld + a megadott manuális
acceptance criteria. **Implementáció fázisonként, külön jóváhagyás
után indul — ez a dokumentum kizárólag tervezés, kód nem készült.**
