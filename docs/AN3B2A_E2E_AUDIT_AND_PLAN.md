# AN-3B2A — Teljes E2E Audit és Implementációs Terv

Státusz: **AUDIT + TERV — implementáció nem kezdődött el.**
Branch: `feat/ios-juggling-annotation-an3b2a`, HEAD: `e9259022`.
Dátum: 2026-06-13.

---

## 0. Vezetői összefoglaló

A manuális teszt visszajelzése két problémát jelzett:

1. **Perzisztencia**: a korábban felvett `.unlabeled` jelölések újranyitáskor eltűntek.
2. **UX zsákutca**: nincs Mentés / Mentés és bezárás / Tovább / Befejezés CTA.

A kód-audit alapján a (2) pont **100%-ban megerősített**: a `JugglingAnnotationScreen`
jelenleg **kizárólag** a FAB-ot és az X (bezárás) gombot rendereli. A
`JugglingAnnotationViewModel` már tartalmazza a `saveError`, `loadWarning`,
`finishError`, `finishResult`, `enterLabelingMode()`, `flushPending()`, `finish()`
logikát — de **ezek közül egyiket sem hívja és egyiket sem jeleníti meg a Screen**.
Ez azt jelenti, hogy bármilyen mentési hiba, karantén-figyelmeztetés vagy
befejezési hiba **csendben elnyelődik** — pontosan az a hiba, amit a Commit 5A
hardening volna hivatva megszüntetni a UI rétegben, de a UI réteg nincs bekötve.

Az (1) pontra (eseményvesztés) a kód maga **nem tartalmaz** olyan logikai hibát,
amely a leírt forgatókönyvben (mark → bezárás → újranyitás, ugyanaz a userId,
ugyanaz a videoId) adatvesztést okozna — a `markTimestamp()` minden hívás után
azonnal `localStore.save()`-t hív, és az `onAppear()` `.loaded` esetben
visszatölti a session-t. A legvalószínűbb root cause-ok ezért **a tesztkörnyezet
és a hiányzó UI-visszajelzés** oldalán vannak, nem a perzisztencia-rétegben
magában. Lásd 2. szakasz a rangsorolt hipotézisekért és a runtime-bizonyíték
hiányának dokumentálásáért.

**Erre a sessionre nem állt rendelkezésre a teszteléshez használt
szimulátor/eszköz konténere** — a gépen elérhető CoreSimulator konténerek
között egyetlen `Documents/juggling_annotations/**` fájl sem található (csak
`juggling_taxonomy` cache és XCTest tmp könyvtárak régi futásokból, 06-09-i
időbélyeggel). Emiatt a runtime-bizonyíték jelentős része **kód-alapú
következtetés**, nem közvetlen log. A P0 fázis első commitja ezért egy
**diagnosztikai overlay**-t ad hozzá, amely a következő körben azonnali,
on-device bizonyítékot ad.

---

## 1. Build/commit-azonosítás

- `git log --oneline -1` a jelenlegi branch-en: `e9259022 fix(juggling): AN-3B2A
  Commit 5A — persistence + currentUserId hardening` — ez a HEAD, tehát a
  forráskód tartalmazza az 5A módosításokat.
- **Nem ellenőrizhető innen**: hogy a telefonon/szimulátoron futó binárist
  **ebből** a commitból fordították-e. Az Xcode gyakran inkrementálisan fordít,
  és ha a teszt során nem történt Clean Build / töröld-és-telepítsd-újra
  ciklus, könnyen előfordulhat, hogy egy **korábbi binárist** teszteltek
  (pl. a `9f15dcad`/`81c7d24e` előtti, ahol a Phase 1 `.unlabeled` mentés vagy
  az `AnnotationLoadResult` split még nem létezett).
- **P0 javaslat**: egy láthatatlan (csak DEBUG build) build-jelző hozzáadása a
  Screen navigációs címéhez vagy egy hosszú nyomásra megjelenő diagnosztikai
  panelhez, amely kiírja a `#define`-ban vagy `Bundle` infóban tárolt git SHA-t.
  Ez egyszer és mindenkorra megszünteti a "melyik build fut" kérdést.

---

## 2. Perzisztencia-audit: kódnyomvonal és hipotézisek

### 2.1 A kódban dokumentált, helyes útvonal

```
JugglingVideoListView
  .fullScreenCover(item: $viewModel.showPlayerFor) { video in
      guard let userId = authManager.currentUserId, userId > 0 else { AnnotationUserUnavailableView() }
      JugglingAnnotationScreen(video: video, authManager: authManager, userId: userId)
  }

JugglingAnnotationScreen.init
  → @StateObject vm = JugglingAnnotationViewModel(userId: userId, videoId: video.videoId, authManager: ...)
     precondition(userId > 0)

.onAppear → vm.onAppear()
  → localStore.load(userId:, videoId:)
     .notFound      → createAndPersistFreshSession()  (ÍR egy üres session-t + saveError ha hiba)
     .loaded(s)      → session = s                      (NINCS írás)
     .quarantined    → loadWarning beállítva, fresh session (ÍR)

FAB tap → vm.markTimestamp(ms:)
  → session.drafts.append(.unlabeled draft)
  → localStore.save(session: &current)  — AZONNALI, szinkron írás
     hiba esetén: saveError beállítva, session VÁLTOZATLAN marad (rollback)

X gomb / onDisappear → vm.onDisappear()
  → localStore.save(session: &current)  — ismételt írás (idempotens)
```

A `LocalAnnotationStore.fileURL`:
```
{Documents}/juggling_annotations/{userId}/{videoId}.json
```
`save()` atomikus: temp fájlba ír, majd `replaceItemAt` + `.bak.json` backup.
`load()` checksum-ot ellenőriz (`SHA256(encoder.encode(drafts))`,
`.sortedKeys`), eltérés esetén karanténba mozgatja az eredetit (bájt-pontosan
megőrzi), és `.quarantined` eredményt ad vissza.

**Ez az útvonal — pontos kód szerint — nem veszíthet el `.unlabeled` eseményt
a leírt forgatókönyvben**, feltéve hogy:
- a build tartalmazza `e9259022`-t (lásd 1. szakasz),
- ugyanaz a `userId` és `videoId` érvényes mindkét megnyitáskor,
- a `save()` ténylegesen sikeresen lefut (nincs lemezhiba),
- a fájlrendszer-konténer nem változott (nincs reinstall) a két megnyitás
  között.

### 2.2 Rangsorolt hipotézisek a jelentett adatvesztésre

| # | Hipotézis | Kód-bizonyíték | Hogyan ellenőrizhető |
|---|---|---|---|
| H1 | **Stale build** — a tesztelt binárist egy `e9259022` előtti commitból fordították, ahol a Phase 1 marking / `.notFound` szétválasztás még nem létezett (pl. `.empty` mindig friss session-t hozott létre `.loaded` helyett is). | `git log`: `9f15dcad` (Phase1 marking), `e9259022` (5A) — több commit különbség lehetséges a build és a HEAD között. | P0-1 diagnosztikai overlay: git SHA + `localStore.load()` eredmény kiírása a képernyőn. |
| H2 | **`videoId` nem stabil a két megnyitás között** — a "ugyanaz a videó" azonosítása csak `displayDate` + thumbnail alapján történik a UI-n, nincs látható videó-azonosító. Ha új videó lett feltöltve a két teszt között, a lista sorrendje (`created_at desc`) eltolódhat, és a felhasználó *másik* sort nyithatott meg ugyanazon vizuális pozícióban. | `JugglingVideoListView` sorai csak `video.displayDate` + `statusBadgeLabel` + thumbnail-t mutatnak; `videoId` (UUID) sehol nem jelenik meg. | P0-1 overlay: `videoId` (első 8 karakter) kiírása a navigációs címben vagy a diagnosztikai panelen. |
| H3 | **`saveError`/`loadWarning` csendben elnyelődik** — ha bármelyik `localStore.save()` hívás hibát dob (pl. lemez tele, sandbox jogosultság), vagy a fájl korábbi (AN-2/AN-3A korai) séma miatt checksum-mismatch-csel karanténba kerül, a felhasználó **semmilyen visszajelzést nem lát** — az esemény "eltűnik", miközben a VM `saveError`/`loadWarning` mezője be van állítva. | `grep` a Screen fájlban: `loadWarning`, `saveError`, `finishError`, `finishResult` **0 találat** — ezek a `@Published` mezők nincsenek a View-ban felhasználva. **Megerősített, kódban bizonyított hiba.** | P0-2: kösd be `saveError`/`loadWarning` megjelenítését (alert/banner) — ezután minden néma adatvesztés látható lesz. |
| H4 | **Reinstall / sandbox-konténer csere** a két megnyitás között — ha az app törölve és újratelepítve lett (nem csak újraindítva), a `Documents` könyvtár (és a `UserDefaults` cache, benne `lfa_current_user_id`) is törlődik; ez **valódi, várt** adatvesztés, nem hiba. | `LocalAnnotationStore.init`: `FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]` — ez sandbox-konténerhez kötött, reinstall = új konténer UUID. | A teszt jegyzőkönyvben rögzítendő: relaunch (Stop/Run Xcode-ból) vagy törlés+telepítés történt-e. |
| H5 | **Checksum-mismatch korábbi (AN-2/AN-3A) session-fájlon** — ha a tesztkészüléken/szimulátoron **korábbi** sessionből származó `juggling_annotations/{userId}/{videoId}.json` fájl van egy régebbi `ContactEventDraft` sémával, és az új encoder (`.sortedKeys`, bővített mezőkkel) más bájtsorozatot ad, a checksum nem egyezik → karantén → friss üres session. A `loadWarning` ilyenkor beállítva van, de (H3 miatt) nem látható. | `ContactEventDraft.Codable` — `pendingServerSnapshot`/`conflictRetryCount` AN-3B2-ben került be (`decodeIfPresent`/`?? 0`), de a **checksum** a teljes (új mezőkkel bővített) `drafts` tömb bájtjaira számol — egy régi fájl checksuma sosem fog egyezni az új encoder kimenetével, ha a régi fájl a régi (kevesebb mezős) JSON-t tartalmazza. | Ellenőrzés: a tesztkészüléken keresendő `{Documents}/juggling_annotations/{userId}/{videoId}.json` és `.../quarantine/*` — ha a quarantine könyvtár nem üres, ez a root cause. |
| H6 | **Más `userId` a két session között** — `currentUserId` az első indításnál a UserDefaults cache-ből (`Self.cachedUserId()`), a `validateSession()` után pedig a `/users/me`-ből frissül. Ha a két érték eltér (pl. a cache egy korábbi teszt-userhez tartozik), a storage path (`{userId}/{videoId}.json`) megváltozik. | `AuthManager.init()`: `currentUserId = Self.cachedUserId()` optimista beállítás; `validateSession()` felülírja, ha sikeres. Race csak akkor, ha a Screen `onAppear` **a `validateSession()` lefutása előtt** történik — ezt az app gyökerének (SplashView) kellene kizárnia, de ez nincs ezen az auditon belül ellenőrizve. | P0-1 overlay: `authManager.currentUserId` kiírása minden `onAppear`-en. |

**Erre a session-re a legvalószínűbb kombináció: H3 (néma hibajelzés) + (H1
vagy H5)** — azaz vagy egy korábbi/eltérő build fut, vagy egy korábbi sémájú
session-fájl miatt karantén történt, és mindkét esetben a felhasználó
**nem kapott semmilyen vizuális jelzést**, csak azt látta, hogy "eltűntek az
események". A P0 fázis ezért egyszerre old meg diagnosztikát (P0-1) és a néma
hiba-elnyelést (P0-2).

### 2.3 Lifecycle hívásszám-audit

- `onAppear`: a Screen `.onAppear { Task { await onAppear() } }` — SwiftUI
  garantáltan egyszer hívja meg egy `fullScreenCover` megjelenésekor (item
  alapú cover → új instance minden megjelenítéskor). **Nincs duplikáció-védelem
  szükséges**, mert minden megjelenítés új `@StateObject`-et kap.
- `onDisappear`: `didCleanUp` flag védi a duplikált hívás ellen (X gomb
  explicit hívja, majd a SwiftUI `.onDisappear` is hívná — a flag ezt
  kiszűri). **Helyes.**
- **Probléma**: az X gomb `onDisappear()`-t hív **és azonnal dismiss-el** —
  nincs várakozás a `localStore.save()` eredményére. Ha a save hibát dob
  (`saveError` beáll), a képernyő **már bezárult**, a felhasználó nem látja a
  hibát. Ez a P1 (Mentés és bezárás + X confirmation) explicit scope-ja.

---

## 3. UI-felszín audit — meglévő vs. stub vs. hiányzó

| Elem | Állapot | Bizonyíték |
|---|---|---|
| FAB (jelölés rögzítése) | **Megvan, működik** | `JugglingAnnotationScreen.fabButton` → `vm.markTimestamp` |
| Esemény lista + swipe delete | **Megvan** | `eventList`, `.onDelete { vm.markDeleted }` |
| Timeline pins + seek | **Megvan** | `EventTimelineView` |
| X / bezárás | **Megvan, de nincs megerősítés és nincs mentés-várakozás** | `toolbar` X gomb → `onDisappear()` + azonnali `dismiss()` |
| Mentési státusz kijelző | **HIÁNYZIK** | nincs UI-elem `saveError`/sikeres-mentés jelzésére |
| `saveError` alert | **HIÁNYZIK (kódban van, UI-ban nincs bekötve)** | grep: 0 találat a Screen-ben |
| `loadWarning` (karantén figyelmeztetés) | **HIÁNYZIK (kódban van, UI-ban nincs bekötve)** | grep: 0 találat |
| Esemény-számláló badge (unlabeled/labelPending) | **HIÁNYZIK** | `unlabeledCount`/`labelPendingCount` léteznek a VM-ben, sehol nem jelennek meg |
| „Mentés és bezárás” CTA | **HIÁNYZIK** | nincs ilyen gomb |
| „Tovább a címkézéshez” CTA | **HIÁNYZIK** | `enterLabelingMode()` létezik, sosem hívódik |
| Egyedi esemény-címkézés UI (Phase 2 picker/detail) | **TELJESEN HIÁNYZIK ezen a branch-en** | `find ios/.../Annotation` → nincs `ContactPickerView`/`EventDetailView`/`ConflictResolutionView`; ezek a `42112310` commit ("remove Phase 2 screen files from AN-3B2A scope") óta nem részei ennek a branch-nek. (Megjegyzés: a memóriában szereplő PR #299 egy **másik** branch-en/PR-ben létezik ezeket — ide nincsenek átemelve.) |
| Készültség-ellenőrzés (Finish readiness) | **VM-logika megvan, UI nincs** | `finishReadiness` computed property, sosem olvasott a Screen-ben |
| „Címkézés befejezése és feldolgozás” CTA | **VM-logika megvan (`finish()`), UI nincs** | `finish()` → `callFinish()` → `POST /finish`, sosem hívódik |
| Konfliktus-feloldó UI | **VM-logika megvan (`resolveConflict`, `acceptServerVersion`, `keepLocalVersion`), UI nincs** | nincs hívás/megjelenítés a Screen-ben |
| Eredmény / completed állapot UI | **TELJESEN HIÁNYZIK** | `finishResult: FinishAnnotationOut?` sosem olvasott |
| Újranyitás befejezett videóhoz | **TELJESEN HIÁNYZIK** | nincs logika `annotation_status == human_review_pending/annotated` esetére a listában vagy a Screen-ben |

---

## 4. Backend integráció audit

A backend (AN-1, PR #295, már `main`-en) a teljes CRUD + finish API-t biztosítja:

| Endpoint | Megléte | iOS hívja-e jelenleg |
|---|---|---|
| `GET /users/me/juggling/videos` | megvan | igen (`JugglingVideoListViewModel.fetchList`) |
| `GET /users/me/juggling/videos/{id}/contacts` | megvan | igen, csak `resolveConflict`/`reconcile` útján (sosem hívódik, mert ezek a flow-k nincsenek bekötve) |
| `POST /users/me/juggling/videos/{id}/contacts` | megvan | igen, `flushPending` → `pushCreate` — **de `flushPending()` sosem hívódik a Screen-ből** |
| `POST /users/me/juggling/videos/{id}/contacts/batch` | megvan | `batchSubmit` definiálva a protokollban, de **nem** szerepel a `JugglingAnnotationAPIClientProtocol`-ban (24-28. sor) — jelenleg nem elérhető a sync engine-ből |
| `PATCH /contacts/{eventId}` | megvan | `pushPatch` — `flushPending` része, ugyanaz a hívási hiány |
| `DELETE /contacts/{eventId}` | megvan | `pushDelete` — ugyanaz |
| `POST /videos/{id}/finish` | megvan, állapotgép: `in_progress → human_review_pending` | `finish()` definiálva, **sosem hívódik** |

**Összefoglalva: a teljes sync/finish vezérlés (P2-P4 funkcionalitás) már
implementálva van a `JugglingAnnotationViewModel` + `AnnotationSyncEngine` +
`JugglingAnnotationAPIClient` szintjén — a hiányzó réteg kizárólag a
SwiftUI Screen/CTA-rárakás és a hozzá tartozó képernyők (Phase 2 labeling,
Finish, Result).** Ez jelentősen csökkenti a P2-P5 kockázatát: nincs új
sync-logika, csak UI + navigáció.

---

## 5. Cél végállapot — user flow és állapotgép

### 5.1 Képernyők

```
[VideoList] ──tap playable video──▶ [AnnotationScreen: Marking Mode]
                                          │
                                          │ FAB → markTimestamp (azonnali lokális mentés)
                                          │ "Mentés és bezárás" → save + dismiss (visszatér VideoList-re,
                                          │                        állapot megmarad, később folytatható)
                                          │ "Tovább a címkézéshez" (csak ha unlabeledCount > 0)
                                          ▼
                                    [AnnotationScreen: Labeling Mode]
                                          │ esemény-lista, mindegyik .labelPending
                                          │ tap → [EventLabelDetail] (ContactPicker + side + confidence)
                                          │ minden esemény labelezve → readiness OK
                                          │ "Címkézés befejezése és feldolgozás" → flush + finish()
                                          ▼
                                    [AnnotationScreen: Processing / Completed]
                                          │ POST /finish 200 → annotation_status=human_review_pending
                                          │ újranyitás → read-only összefoglaló (Result view)
```

### 5.2 Állapotgép (képernyő-szintű, a meglévő `ContactEventSyncStatus` 12
állapota fölött)

```
AnnotationScreenMode:
  marking            — Phase 1, FAB aktív, esemény-lista timestamp-only
  labeling           — Phase 2, minden .unlabeled → .labelPending, egyedi
                        címkézés UI
  readinessBlocked   — labeling kész, de finishReadiness == .blocked
  finishing          — finish() in-flight (isFinishing == true)
  completed          — finishResult != nil (vagy annotation_status már
                        human_review_pending/annotated/reviewed a backendről)
  error              — finishError != nil, retry lehetőséggel
```

Mód-átmenetek:
- `marking → labeling`: `enterLabelingMode()` + `unlabeledCount == 0` után
  minden esemény `.labelPending`.
- `labeling → readinessBlocked`: ha `finishReadiness == .blocked(...)`
  (pl. még van `.labelPending` esemény, vagy `.syncing`/`.conflicted`).
- `labeling/readinessBlocked → finishing`: `finish()` hívás.
- `finishing → completed`: `finishResult != nil`.
- `finishing → error`: `finishError != nil`, "Újra" gomb → vissza
  `labeling`/`readinessBlocked`-ba.
- Újranyitás (`onAppear`) `annotation_status`-tól függően közvetlenül
  `completed`-be ugorhat (lásd P5).

### 5.3 Lokális vs. backend állapot összefoglaló táblázat

| Lokális (`AnnotationSessionFile` / `ContactEventDraft.syncStatus`) | Backend (`JugglingVideo.annotation_status`) | UI mód |
|---|---|---|
| nincs fájl, `annotation_status == metadata_ready` | `metadata_ready` | marking (üres) |
| van `.unlabeled` draft | `in_progress` (implicit, első esemény után) | marking |
| minden draft `.labelPending` / labelezés alatt | `in_progress` | labeling |
| minden aktív draft `.synced`, readiness OK | `in_progress` | labeling (readiness OK, Finish CTA aktív) |
| `finish()` folyamatban | `in_progress` | finishing |
| `finish()` 200 OK, session törölve | `human_review_pending` | completed |
| (újranyitás, nincs lokális fájl) | `human_review_pending`/`annotated`/`reviewed` | completed (read-only, GET /contacts) |

---

## 6. Implementációs terv — fázisok

Minden fázis külön commit-sorozat, **minden commit után kötelező manuális
teszt** (lásd az egyes commitoknál). Implementáció csak fázisonkénti
jóváhagyás után.

### P0 — Perzisztencia root cause + diagnosztika + néma adatvesztés megszüntetése

**Cél**: bizonyítani/kizárni a 2.2 hipotéziseket, és garantálni, hogy **semmilyen**
mentési/karantén/finish-hiba ne maradjon néma.

- **Commit P0-1 — Diagnosztikai overlay (DEBUG-only)**
  - Új fájl: `ios/LFAEducationCenter/Juggling/Annotation/Screen/AnnotationDebugOverlay.swift`
  - Megjelenítés: hosszú nyomás a navigációs címen (vagy `#if DEBUG` toolbar
    ikon).
  - Tartalom: `authManager.currentUserId`, `video.videoId` (teljes UUID),
    `vm.userId`, `localStore.load()` eredmény típusa (`notFound` /
    `loaded(draftsCount)` / `quarantined`), a session fájl teljes elérési
    útja, build git SHA (Info.plist-be injektálva egy build script-tel, vagy
    statikus konstans, amit minden commit-on frissítünk).
  - Érintett fájlok: `JugglingAnnotationScreen.swift` (overlay bekötés),
    `Info.plist` + build settings (git SHA injektálás — opcionális, ha túl
    nagy a scope, statikus stringgel is induló).

- **Commit P0-2 — `saveError` / `loadWarning` UI bekötés**
  - `JugglingAnnotationScreen`: `.alert` modifier `vm.saveError`-ra
    (non-nil → alert, "OK" → `vm.clearSaveError()`).
  - `loadWarning` → dismissible banner a képernyő tetején (nem alert, mert
    nem blokkoló, de látható kell legyen).
  - Új VM metódus: `clearLoadWarning()` (analóg `clearSaveError()`-hoz).
  - Érintett fájlok: `JugglingAnnotationViewModel.swift` (clearLoadWarning),
    `JugglingAnnotationScreen.swift`.

- **Commit P0-3 — Checksum-migráció / karantén-helyreállítás (ha H5
  igazolódik)**
  - Csak ha a manuális teszt (P0-1 overlay-jel végzett újrateszt) megerősíti,
    hogy a karantén tényleg lefut egy meglévő fájlon.
  - Megoldás: `LocalAnnotationStore` checksum-ellenőrzést tegyük
    sémaverzió-tudatossá — `schemaVersion` mezőt nézzük meg dekódolás előtt,
    és régi `schemaVersion < currentVersion` esetén **migráljuk** a draftokat
    (töltsük fel az új mezőket defaulttal), majd számítsuk újra a checksumot
    és írjuk vissza — **mielőtt** a checksum-összehasonlítás megtörténne.
  - Érintett fájlok: `LocalAnnotationStore.swift`,
    `LocalAnnotationStoreTests.swift` (új teszt: régi-séma fájl betöltése nem
    okoz karantént).

**Manuális teszt P0 után**:
1. Telepítsd a build-et, nyiss meg egy videót, jelölj 2 eseményt, nézd meg az
   overlay-t — jegyezd fel `userId`, `videoId`, fájl elérési út, git SHA.
2. Zárd be (X), nyisd meg újra **ugyanazt a sort** — overlay: `loaded(2)`
   várt.
3. Ha bármikor `saveError` vagy `loadWarning` jelenik meg, a screenshot +
   overlay adatok alapján pontosan lokalizálható a root cause.

**Acceptance criteria P0**: a 2.2 táblázat összes hipotézise vagy
megerősített, vagy kizárt, dokumentálva; nincs néma `saveError`/`loadWarning`/
`finishError`.

---

### P1 — Mentés és bezárás + mentési státusz

**Cél**: a marking mód CTA-zsákutcájának megszüntetése, biztonságos kilépés.

- **Commit P1-1 — Esemény-számláló badge**
  - `unlabeledCount`/`labelPendingCount` megjelenítése a navigációs sávban
    (pl. "3 jelölés").
  - Érintett fájlok: `JugglingAnnotationScreen.swift`.

- **Commit P1-2 — Mentési státusz indikátor**
  - Új VM computed property: `saveStatus: SaveStatus` enum
    (`.saved`/`.saving`/`.failed`) — levezethető `saveError`-ból + egy
    `isSaving` flag-ből, amit minden `localStore.save()` hívás körül
    állítunk.
  - UI: kis ikon/szöveg ("Mentve" / "Mentés…" / "Hiba") a badge mellett.
  - Érintett fájlok: `JugglingAnnotationViewModel.swift`,
    `JugglingAnnotationScreen.swift`.

- **Commit P1-3 — „Mentés és bezárás” CTA + X megerősítés**
  - Toolbar: X gomb → ha `saveError == nil`, megerősítő dialog
    ("Bezárod? A jelölések elmentve maradnak.") → `onDisappear()` + dismiss.
  - Ha `onDisappear()` mentése hibát dob (`saveError != nil` lesz), **a
    dismiss nem történik meg** — alert jelenik meg "Újra" / "Mégse mentés
    nélkül bezár (data-loss warning)" opciókkal.
  - Új explicit "Mentés és bezárás" gomb (nem csak X) — ugyanaz a logika,
    de elsődleges CTA-ként a képernyő alján.
  - Érintett fájlok: `JugglingAnnotationScreen.swift`,
    `JugglingAnnotationViewModel.swift` (`onDisappear()` → `Bool` visszatérési
    érték a sikerről, hogy a View el tudja dönteni a dismisst).

**Manuális teszt P1 után**:
1. Jelölj 3 eseményt → badge "3" → "Mentve" státusz.
2. "Mentés és bezárás" → visszajutsz a listára.
3. Nyisd meg ugyanazt a videót → 3 esemény + badge "3" látható.
4. Szimulálj mentési hibát (P0 manuális checklist `chmod -w` trükkje) →
   X gomb → alert jelenik meg, a screen NEM záródik be automatikusan.

**Acceptance criteria P1**: a marking módból biztonságosan ki- és
visszaléphető, mentési állapot mindig látható, sikertelen mentés nem vezet
csendes adatvesztéshez vagy véletlen bezáráshoz.

---

### P2 — Valódi labeling screen és egyedi event labeling

**Cél**: Phase 1 → Phase 2 átmenet + minden `.unlabeled`/`.labelPending`
esemény egyedi címkézése.

- **Commit P2-1 — „Tovább a címkézéshez” CTA + módváltás**
  - Megjelenik, ha `unlabeledCount > 0` és nincs `.unlabeled`/`.labelPending`
    aktív szerkesztés. Tap → `enterLabelingMode()` → `AnnotationScreenMode
    = .labeling`.
  - A meglévő `EventTimelineView`/lista marad, de a sorok most
    `.labelPending` állapotú eseményekre tap → navigál a Label Detail
    képernyőre.
  - Érintett fájlok: `JugglingAnnotationScreen.swift` (mód state),
    `JugglingAnnotationViewModel.swift` (mód-számítás a `session`-ből, ha nincs
    explicit tárolt mód: `mode = labelPendingCount > 0 || (unlabeledCount==0 &&
    activeEvents nem mind synced) ? .labeling : .marking`).

- **Commit P2-2 — `EventLabelDetailView` (Contact Picker + side + confidence)**
  - Új fájl: `ios/LFAEducationCenter/Juggling/Annotation/Screen/EventLabelDetailView.swift`.
  - Taxonómia-csoportok (`vm.taxonomy`) alapján kontakt-típus választó,
    oldal (bal/jobb/center, ha releváns a taxonómia group-hoz),
    `annotationConfidence` (certain/probable/uncertain) segmented control,
    opcionális custom label/description.
  - "Mentés" → `vm.editEvent(...)` ha a draft már `.localOnly`/`.synced`
    (szerkesztés), vagy egy új `vm.labelEvent(deviceEventId:, contactType:,
    ...)` metódus, amely `.labelPending → .localOnly` átmenetet végez
    (jelenleg `editEvent` a `.unlabeled`/`.labelPending` ágon `return false`-t
    ad — ezt egy új, dedikált metódusra kell bontani, hogy a `.localOnly`
    átmenet explicit legyen).
  - Új VM metódus: `labelEvent(deviceEventId: UUID, contactType: String, side:
    String?, annotationConfidence: String, customLabel: String?,
    customDescription: String?) -> Bool` — `.labelPending → .localOnly`,
    `contactType` beállítása, `localStore.save`.
  - Érintett fájlok: `JugglingAnnotationViewModel.swift` (új metódus),
    `EventLabelDetailView.swift` (új), `JugglingAnnotationScreen.swift`
    (navigáció a sor tap-re).

- **Commit P2-3 — Readiness ellenőrzés a labeling módban**
  - `finishReadiness` kiértékelése; ha `.blocked([...])`, banner: "X esemény
    még nincs címkézve / szinkronizálva" + lista a blokkoló eseményekről,
    tap → ugrás a Label Detail-re.
  - Ha minden esemény `.synced` vagy `.readyZero`/`.readyWithCount` → "Tovább"
    aktív (P3-ba navigál, vagy közvetlenül megjelenik a Finish CTA).
  - Érintett fájlok: `JugglingAnnotationScreen.swift`.

**Manuális teszt P2 után**:
1. Marking módban 2 esemény → "Tovább a címkézéshez" → labeling mód.
2. Mindkét esemény `.labelPending` (szürke pin) → tap → Label Detail →
   válassz típust/oldalt/confidence → Mentés → pin színe `.localOnly`
   (narancs) → (ha van net) → `.synced` (zöld) `flushPending()` után.
3. Zárd be + nyisd meg újra → labeling mód, mindkét esemény továbbra is
   címkézett (`.synced`/`.localOnly`).
4. Töröld a hálózatot → címkézz egy új eseményt → `.localOnly` marad,
   readiness `.blocked` amíg nincs net.

**Acceptance criteria P2**: minden `.unlabeled` esemény egyedileg
címkézhető, a readiness-ellenőrzés helyesen blokkol/enged tovább, offline
címkézés nem vezet adatvesztéshez (`.localOnly` megmarad).

---

### P3 — Validáció és Finish readiness UI

**Cél**: a "Címkézés befejezése és feldolgozás" CTA előfeltételeinek explicit
megjelenítése.

- **Commit P3-1 — Finish readiness banner + CTA engedélyezés**
  - `finishReadiness` → `.readyZero`/`.readyWithCount(n)` esetén a "Címkézés
    befejezése és feldolgozás" CTA aktív; `.blocked(statuses)` esetén
    inaktív + a blokkoló okok felsorolása (pl. "2 esemény szinkronizálás
    alatt", "1 esemény konfliktus — oldd fel").
  - Érintett fájlok: `JugglingAnnotationScreen.swift`.

- **Commit P3-2 — Konfliktus-feloldó UI**
  - Ha `pendingConflictId != nil`, modal/sheet: szerver vs. lokális verzió
    összehasonlítása (`pendingServerSnapshot` vs. draft mezők), két gomb:
    "Szerver verzió megtartása" (`acceptServerVersion`) / "Saját verzió
    megtartása" (`keepLocalVersion`).
  - Új fájl: `ios/LFAEducationCenter/Juggling/Annotation/Screen/ConflictResolutionView.swift`.
  - Érintett fájlok: `JugglingAnnotationScreen.swift`,
    `ConflictResolutionView.swift` (új).

- **Commit P3-3 — "0 esemény" finish megerősítés**
  - `.readyZero` esetén a Finish CTA megerősítő dialógust mutat: "Nincs
    rögzített esemény erről a videóról. Biztosan befejezed annotáció
    nélkül?" → `confirm_zero_contacts=true`.
  - Érintett fájlok: `JugglingAnnotationScreen.swift`.

**Manuális teszt P3 után**:
1. Hagyj egy eseményt `.labelPending` állapotban → Finish CTA inaktív,
   banner mutatja az okot.
2. Címkézd le → Finish CTA aktív.
3. Hozz létre szándékosan verzió-konfliktust (két eszközről/szerkesztésből,
   vagy manuálisan a backend felé PATCH-csel a verziószám módosítása) →
   `.conflicted` → ConflictResolutionView megjelenik → válassz egy oldalt →
   readiness frissül.
4. 0 eseményes videón → Finish CTA → megerősítő dialog → `confirm_zero_contacts=true`.

**Acceptance criteria P3**: a Finish CTA soha nem engedélyezett blokkoló
állapotban; minden blokkoló ok felhasználóbarát szöveggel megjelenik;
konfliktus felhasználó által feloldható.

---

### P4 — Backend sync és feldolgozás indítása

**Cél**: `flushPending()` és `finish()` bekötése a UI-ba (a logika már létezik).

- **Commit P4-1 — Periodikus/explicit `flushPending()` hívás**
  - `onAppear()` után, és minden `markTimestamp`/`labelEvent`/`editEvent`
    után (amikor a draft `.localOnly`-ra vált), indíts egy `Task { await
    vm.flushPending() }`-et — fire-and-forget, UI-blokkolás nélkül.
  - Opcionális: `Foreground` visszatéréskor (`scenePhase` figyelés) is
    `flushPending()`.
  - Érintett fájlok: `JugglingAnnotationScreen.swift`.

- **Commit P4-2 — "Címkézés befejezése és feldolgozás" → `finish()`**
  - CTA tap → `await vm.finish()`; `isFinishing` alatt loading overlay.
  - Siker → `finishResult != nil` → P5 Result View.
  - Hiba → `finishError` alert, "Újra" gomb → újra `finish()`.
  - Érintett fájlok: `JugglingAnnotationScreen.swift`.

- **Commit P4-3 — `batchSubmit` integrálása (opcionális optimalizáció)**
  - Ha a P2 sok `.localOnly` eseményt hagy felgyűlni, a `batchSubmit`
    (`POST /contacts/batch`) hozzáadása a
    `JugglingAnnotationAPIClientProtocol`-hoz és az `AnnotationSyncEngine`-hez
    csökkenti a hívásszámot. **Csak ha P4-1 teljesítményproblémát mutat.**
  - Érintett fájlok: `JugglingAnnotationAPIClient.swift`,
    `AnnotationSyncEngine.swift`.

**Manuális teszt P4 után**:
1. Címkézz 3 eseményt online módban → pár másodpercen belül mindhárom
   `.synced` (zöld pin) `flushPending()` hatására, anélkül hogy explicit
   "Sync" gombot nyomnál.
2. Finish CTA → "Címkézés befejezése és feldolgozás" → loading → siker →
   backend `GET /users/me/juggling/videos` listában az adott videó
   `annotation_status == human_review_pending`.
3. Network hiba szimulálása `finish()` közben → `finishError` alert →
   "Újra" → siker, ha helyreáll a net.

**Acceptance criteria P4**: a már implementált sync/finish logika éles UI
útvonalon keresztül elérhető; sikeres finish a backend állapotát
`human_review_pending`-re állítja, ellenőrizve DB lekérdezéssel vagy a lista
endpoint válaszával.

---

### P5 — Completed/result UI és újranyitás

**Cél**: a lezárt annotáció láthatóvá tétele, biztonságos újranyitás.

- **Commit P5-1 — Result View**
  - `finishResult != nil` → új sheet/screen:
    `AnnotationFinishedView` — összefoglaló (`total_juggling_count`,
    `annotation_finished_at`), "Kész" gomb → dismiss vissza a VideoList-re.
  - Új fájl: `ios/LFAEducationCenter/Juggling/Annotation/Screen/AnnotationFinishedView.swift`.

- **Commit P5-2 — Újranyitás `human_review_pending`/`annotated`/`reviewed`
  videóhoz**
  - `JugglingVideoItem` már tartalmaz `annotationStatus: String?` (AN-1).
  - `JugglingVideoListView`: ha `annotationStatus` ∈
    {`human_review_pending`, `annotated`, `reviewed`}, a play gomb helyett/
    mellett egy "Megtekintés" ikon jelenik meg, amely egy **read-only**
    `AnnotationFinishedView`/summary-t nyit (GET `/contacts` a végleges lista
    megjelenítéséhez), NEM a marking/labeling Screen-t.
  - `JugglingAnnotationViewModel.onAppear()`: ha `localStore.load()`
    `.notFound` ÉS a backend `annotation_status` már lezárt állapotban van
    (extra GET hívás vagy a `video` paraméterből átadott `annotationStatus`),
    ne hozzon létre friss `in_progress` session-t — navigáljon a read-only
    nézetbe.
  - Érintett fájlok: `JugglingVideoListView.swift`,
    `JugglingAnnotationViewModel.swift`, `AnnotationFinishedView.swift`.

**Manuális teszt P5 után**:
1. Fejezz be egy annotációt (P4) → Result View megjelenik, "Kész" → lista.
2. A lista adott sora most "Megtekintés" ikont mutat (nem play+annotate).
3. Tap → read-only összefoglaló, a korábban rögzített események listája
   látszik, nincs FAB/szerkesztés.
4. Próbálj egy `human_review_pending` videót a régi módon (URL-séma
   trükkel / debug) megnyitni marking módban → nem szabad engedni, vagy
   ha megnyílik, kizárólag olvasásra.

**Acceptance criteria P5**: lezárt videó nem nyitható újra szerkesztésre;
a felhasználó látja a végeredményt; a flow vizuálisan "lezárt" állapotban
végződik.

---

### P6 — Manuális E2E és regressziós tesztelés

**Cél**: a teljes flow (1-11. lépés a bevezetőből) egyetlen, végponttól
végpontig tartó forgatókönyvben validálva, plusz a meglévő unit/regressziós
suite zöld.

- **Commit P6-1 — Unit tesztek minden új VM/sync metódusra**
  - `labelEvent`, mód-számítás, readiness-banner logika, result-view state.
  - Érintett fájlok: `ios/LFAEducationCenterTests/Juggling/**`.

- **Commit P6-2 — E2E manuális forgatókönyv-dokumentum**
  - `docs/AN3B2A_E2E_MANUAL_TEST.md`: lépésről lépésre (1-11), minden
    lépésnél elvárt UI-állapot + backend-ellenőrzés (DB query vagy API
    válasz).

- **Commit P6-3 — Regresszió**
  - Teljes iOS unit suite futtatása (`xcodebuild test`), AN-1..AN-3B2A
    összes meglévő tesztcsoportja zöld.

**Acceptance criteria P6 (= a teljes projekt acceptance criteria)**:

1. Videó megnyitása → marking mód, korábbi `.unlabeled`/`.labelPending`/
   `.synced` események visszatöltődnek (overlay/badge igazolja).
2. FAB jelölés → azonnali, látható "Mentve" státusz.
3. "Mentés és bezárás" → visszatérés a listára, állapot megmarad,
   ugyanonnan folytatható.
4. "Tovább a címkézéshez" → labeling mód, minden esemény egyedileg
   címkézhető.
5. Readiness banner pontosan jelzi a blokkoló okokat; konfliktus
   feloldható.
6. "Címkézés befejezése és feldolgozás" → `flushPending` + `finish()` →
   siker esetén `human_review_pending`.
7. Result View megjelenik, lista "Megtekintés" módba vált.
8. Hibakezelés: mentési/finish hiba mindig látható alert/banner,
   sosem csendes adatvesztés.
9. Offline: jelölés/címkézés offline is megmarad lokálisan
   (`.localOnly`/`.unlabeled`), online visszatéréskor automatikusan
   szinkronizálódik.
10. iOS 14 deployment target nem sérül (minden új View iOS 14-kompatibilis
    API-kat használ — lásd a meglévő backport cheatsheet).

---

## 7. Nyitott kérdések / döntési pontok (jóváhagyás előtt tisztázandó)

1. **P0-1 git SHA injektálás**: build-script alapú (Info.plist `GIT_SHA`)
   vagy elég egy manuálisan karbantartott konstans minden commit-on? (build
   script módosítás nagyobb scope, de tartósan megoldja a "melyik build fut"
   kérdést jövőbeli hardening körökhöz is.)
2. **P2 Label Detail navigáció**: sheet (modal) vagy push (NavigationLink)?
   iOS 14-en a `NavigationView`/`.stack` style mellett push egyszerűbb, de a
   jelenlegi `JugglingAnnotationScreen` maga is egy `fullScreenCover` —
   dupla-modal UX-et érdemes elkerülni.
3. **P5 read-only nézet**: új képernyő (`AnnotationFinishedView` általános,
   "már lezárt videóhoz megnyitva" módban is használható) vagy külön
   `AnnotationReviewView`? Javaslat: egy komponens, `mode: .justFinished |
   .reviewExisting` paraméterrel — kevesebb duplikáció.
4. **P4-3 batch submit**: csak akkor szükséges, ha P2 után mérhető
   teljesítményprobléma van — alapesetben kihagyható az MVP-ből.

---

## 8. Összefoglaló commit-sorrend

```
P0-1  diag overlay (DEBUG)
P0-2  saveError/loadWarning UI bekötés
P0-3  checksum/schema migráció (csak ha H5 igazolódik)
P1-1  esemény-számláló badge
P1-2  mentési státusz indikátor
P1-3  Mentés és bezárás + X megerősítés
P2-1  Tovább a címkézéshez CTA + mód state
P2-2  EventLabelDetailView + labelEvent()
P2-3  readiness ellenőrzés labeling módban
P3-1  finish readiness banner + CTA
P3-2  ConflictResolutionView
P3-3  0-esemény finish megerősítés
P4-1  flushPending bekötés
P4-2  finish() bekötés + Result navigáció
P4-3  batchSubmit (opcionális)
P5-1  AnnotationFinishedView
P5-2  újranyitás lezárt videóhoz (read-only)
P6-1  unit tesztek
P6-2  E2E manuális teszt doksi
P6-3  teljes regresszió
```

Minden commit után: build + unit teszt zöld + a fent megadott manuális
lépések. Implementáció fázisonként, külön jóváhagyás után indul — most
**semmilyen kód nem készült**, ez kizárólag audit + terv.
