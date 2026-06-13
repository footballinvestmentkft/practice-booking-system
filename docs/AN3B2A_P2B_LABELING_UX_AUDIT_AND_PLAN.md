# AN-3B2A — P2B: Címkézési UX Audit + Redesign Terv

Státusz: **TERV — implementáció nem kezdődött el.**
Branch: `feat/ios-juggling-annotation-an3b2a`.
Előzmény: P2 elkészült és commitolva (`faa34cf4`, `EventLabelDetailView.swift`,
hosszú taxonómia-lista alapú UX). A user manuális UI-tesztje alapján ez a
lista-alapú UX túl nehézkes — a user nem látja a videórészletet
címkézés közben, és a 18 elemű lista emlékezetből történő választást
igényel.

Ez a dokumentum **csak auditot és tervet** tartalmaz. Semmilyen kódváltozás
nem történt. Implementáció külön jóváhagyás után, commit-bontásban indulhat
(„P2B-1”, „P2B-2”, … munkanevek, hogy a P3 sorszám szabad maradjon).

---

## 1. Audit — jelenlegi állapot

### 1.1 Mi van most (`EventLabelDetailView.swift`, P2)
- `NavigationView` + `List` — szekciók: timestamp+státusz, taxonómia-csoportok
  (gombokkal soronként), bizonyosság szegmens, opcionális custom mezők,
  Vissza/Mentés gombok.
- A videó **nem látható** a sheet-ben — a sheet a marking screen felett
  jelenik meg, a `playback`/`loader` a `JugglingAnnotationScreen`-ben él,
  az `EventLabelDetailView` csak `vm`-et kap.
- A taxonómia forrás: `vm.taxonomy?.groups` (`TaxonomyDocument` ←
  `ContactTaxonomyStore` ← bundle `contact_types_v1.json`). **Ez marad az
  egyetlen adatforrás** az új UX-ben is.

### 1.2 Reuse-lehetőségek

**Playback / preview**
- `PlaybackController` (`ios/.../Playback/PlaybackController.swift`) —
  `@MainActor ObservableObject`, AVPlayer-t csomagol, van `seek(toTimestampMs:)`,
  `play()`/`pause()`, `stepForward()`/`stepBackward()` (frame-step,
  `frameDuration` track-alapú). **Teljesen iOS14-kompatibilis, újrahasználható
  egy második, önálló instance-ként.**
- `AVPlayerLayerView` (`UIViewRepresentable`, `.resizeAspect`, retain-cycle-safe)
  — szintén közvetlenül újrahasználható egy második AVPlayer-rel.
- A letöltött videó lokális fájl-URL-je a `JugglingAnnotationScreen`-ben
  érhető el (`loader.state == .ready(url)` → `AVAsset(url:)`). Ezt az URL-t
  **le kell adni** az `EventLabelDetailView`-nak (init paraméter), hogy a
  preview egy **saját, második `AVPlayer`/`PlaybackController`** instance-on
  keresztül ugyanazt a lokális fájlt nyissa meg. Nem osztjuk meg a fő
  `playback`-et a két screen között (egyszerűbb lifecycle, nincs ütközés a
  marking-screen lejátszási állapotával).

**Still frame**
- `AVAssetImageGenerator` — szinkron `copyCGImage(at:actualTime:)` iOS14-en
  is elérhető (NEM csak `generateCGImagesAsynchronously`, ami szintén
  iOS14-kompatibilis). A still frame-et egy kis helper-struct generálja:
  `EventStillFrameGenerator.makeImage(asset:atMs:) -> UIImage?`. Háttérszálon
  futtatva (`Task.detached` vagy `DispatchQueue.global`), eredmény
  `@MainActor`-ra visszaküldve.

### 1.3 Taxonomy → body-zone leképezés

A `contact_types_v1.json` 5 csoportja + `side_policy` mező közvetlenül
leképezhető zónákra anélkül, hogy új párhuzamos listát kellene
létrehozni:

| Body-zone (UI) | `group_id` | szűrés a csoporton belül |
|---|---|---|
| Jobb lábfej | `foot` | `side == "right"` (4 elem: instep/inside/outside/heel) |
| Bal lábfej | `foot` | `side == "left"` (4 elem) |
| Jobb térd | `knee` | `side == "right"` (1 elem) |
| Bal térd | `knee` | `side == "left"` (1 elem) |
| Jobb csípő | `hip` | `side == "right"` (1 elem) |
| Bal csípő | `hip` | `side == "left"` (1 elem) |
| Mellkas | `upper_body` | `key == "chest"` (1 elem, `side_policy=center` → nincs side választó) |
| Fej | `upper_body` | `key == "head"` (1 elem, center) |
| Jobb váll | `upper_body` | `key == "right_shoulder"` |
| Bal váll | `upper_body` | `key == "left_shoulder"` |
| Hát | `upper_body` | `key == "back"` (center) — **nincs elölnézeti zóna**, lásd 1.4 |
| Egyéb / Lista | `custom` | `custom_other` — mindig elérhető fallback gomb |

A leképezés egy **tiszta, kódolt szűrőfüggvény**
(`BodyZone.contactTypes(in: TaxonomyDocument) -> [TaxonomyContactType]`),
nem egy új hardcode-olt lista — a `key`/`side`/`group_id` mezőkből szűr.
`side_policy == "fixed"` esetén a side **nem kérdés** — már az enum kulcsból
adott (pl. `right_instep.side == "right"`), tehát a zóna-tap egyszerre adja
meg a `contactType`-listát ÉS — ha a listából 1 elem marad (pl. térd, csípő,
mellkas, fej) — akár automatikusan elő is választhatja azt, a user csak
megerősíti.

### 1.4 "Hát" probléma

A `back` (center, side_policy=center) elölnézeti emberábrán nem
jeleníthető meg értelmesen. Két opció:
- **A) (javasolt)**: a "Hát" mindig a "Egyéb / Lista nézet" fallback alatt
  jelenik meg, NEM külön zóna az ábrán (annotation_difficulty=`very_hard`
  egyébként is, ritkán választott).
- B) külön hátsó nézet toggle — túl nagy scope-növelés egy P2B-hez, **nem
  ajánlott** most.

Javaslat: **A) — a Lista nézet (fallback) mindig listázza az ÖSSZES
contact type-ot** (a body-zone picker csak egy gyorsút a leggyakoribbakhoz),
így a `back` és `custom_other` is elérhető marad enélkül, hogy külön zóna
kellene.

---

## 2. Új UX — komponensterv

### 2.1 Képernyő-felépítés (portrait, kis iPhone-on is)

```
┌─────────────────────────────────────┐
│  Címkézés (2/4)            [x]       │ ← navigationTitle/toolbar (mint most)
├─────────────────────────────────────┤
│  ┌─────────────────────────────────┐│
│  │   Preview lejátszó (16:9)        ││ ← fix magasság ~ min(180pt, 28% height)
│  │   timestamp ± ablak, play/pause, ││
│  │   frame-step ◀ ▶, still-frame     ││
│  └─────────────────────────────────┘│
│  Címkézésre vár · 12:34.120          │ ← timestamp + státusz badge (mint most)
├─────────────────────────────────────┤
│                                       │
│         [ emberábra, elölnézet ]     │ ← interaktív zónák, GeometryReader
│         (marad hely: ~40-45% height) │   skálázott, tap target ≥44pt
│                                       │
├─────────────────────────────────────┤
│  (zóna kiválasztva esetén:)          │
│  Részletes kontakt-választás         │ ← a zónához tartozó 1-4 elem,
│  [Jobb rüszt] [Belső] [Külső] [Sarok] │   chip/gomb-sor, NEM teljes lista
│  Bizonyosság: [Biztos|Valószínű|...] │
├─────────────────────────────────────┤
│  Egyéb / Lista nézet                 │ ← mindig látható link/gomb
├─────────────────────────────────────┤
│  [ Vissza ]   [ Mentés és tovább ]   │ ← action bar, mint most
└─────────────────────────────────────┘
```

- Kis (SE-méretű) képernyőn a body-diagram és a részlet-választó **egy
  scrollable `VStack`** legyen (nem `List`, mert a diagram nem sor-alapú),
  az action bar `safeAreaInset(edge: .bottom)`-pal (iOS14: `.background`
  + `VStack` alul, NEM `safeAreaInset` — az iOS15+. Helyette egyszerű
  rögzített `VStack` lent, `Spacer()` a tartalom és az action bar között).

### 2.2 Preview lejátszó

- Új `EventPreviewPlayerView` (SwiftUI `View`):
  - kap: `videoURL: URL`, `timestampMs: Int`.
  - belül saját `@StateObject PlaybackController` + `AVPlayerLayerView`.
  - `onAppear`/`onChange(of: timestampMs)`: `loadAsset` (ha még nem
    töltött) + `seek(toTimestampMs: max(0, timestampMs - 500))`.
  - Alatta egy kompakt control-row: ◀ frame-step, play/pause, ▶ frame-step
    (a meglévő `PlaybackController.stepBackward()/stepForward()/togglePlayPause()`
    metódusokkal — nincs új logika, csak UI).
  - **Still frame**: amíg a user nem nyom play-t, a nézet egy
    `AVAssetImageGenerator`-ral generált still képet mutat a
    `timestampMs` pillanatában (placeholder, amíg a kép generálódik:
    `ProgressView` vagy szürke háttér). Play után átvált live previewre.
  - Loop: ha a lejátszás eléri `timestampMs + 1000`-et, automatikusan
    `pause()` + `seek` vissza `timestampMs - 500`-ra (egyszerű "preview loop"
    — nem kell külön observer, a meglévő periodikus time-observer
    `currentTimestampMs`-ét figyeljük `.onReceive`/`.onChange`-csel).

- **Honnan jön a `videoURL`?** A `JugglingAnnotationScreen` már ismeri
  (`loader.state == .ready(url)`). Az `EventLabelDetailView` init-je kap
  egy `videoURL: URL?` paramétert (nil = nincs preview, pl. teszt esetén —
  ekkor a preview szekció elrejtve / placeholder "Videó nem elérhető").

### 2.3 Body-zone picker

- Új `BodyZonePickerView`:
  - SwiftUI `Shape`/`Path` alapú, elölnézeti emberalak (fej, törzs,
    karok/váll, csípő, comb, lábfej kontúrok) — **nem image asset**, hanem
    kódolt `Path`-ok, hogy ne kelljen aszteket gondozni és skálázás
    natívan menjen `GeometryReader`-rel.
  - Minden zóna egy `Shape` + egy **invisible, nagyobb tap-target overlay**
    (`Rectangle().opacity(0.001).frame(minWidth: 44, minHeight: 44)`
    a vizuális zóna fölött, `.onTapGesture`) — iOS14-compatible mintázat,
    nincs `.hoverEffect`/`.contentShape` trükk szükséges hozzá.
  - Zónák: jobb/bal lábfej, jobb/bal térd, jobb/bal csípő, mellkas, fej,
    jobb/bal váll. (Hát kimarad — ld. 1.4.)
  - Kiválasztott zóna vizuálisan kiemelve (`fill(Color.accentColor.opacity(0.35))`).
  - `onZoneSelected: (BodyZone) -> Void` callback — a szülő (`EventLabelDetailView`)
    számolja ki a `BodyZone.contactTypes(in: taxonomy)`-t és tölti a
    "Részletes kontakt-választás" chip-sort.
  - Accessibility: minden zóna `accessibilityElement` + `accessibilityLabel`
    (pl. "Jobb lábfej"), `accessibilityAddTraits(.isButton)`.

### 2.4 Részletes kontakt-választás (zóna után)

- Chip/gomb-sor (`LazyVGrid` vagy `HStack` + `.fixedSize` wrap — iOS14:
  `LazyVGrid` elérhető iOS14-től, OK), max 4 elem (lábfej zóna esetén:
  rüszt/belső/külső/sarok).
- 1 elemű zónák (térd, csípő, mellkas, fej, váll) esetén a chip-sor 1
  elemből áll, és **automatikusan preselect**-elve — a user csak a
  "Mentés és tovább"-ot nyomja meg (de a chipet látja, megerősítésként).
- A side **nem külön kérdés** — a zóna+chip kombináció már egyértelműen
  meghatározza (`side_policy=fixed` → `side` a JSON-ból).
- Bizonyosság szegmens — marad, mint a P2-ben.

### 2.5 "Egyéb / Lista nézet" fallback

- Mindig látható gomb/link a body-diagram alatt: **"Egyéb / Lista nézet"**.
- Megnyitja a **P2-ben már megírt, taxonómia-csoportos listát**
  (a jelenlegi `EventLabelDetailView.formView` taxonómia-szekciói) —
  ez NEM törlődik, hanem **kiszervezve** egy `TaxonomyListPickerView`
  al-komponensbe, amit a body-zone UX másodlagos módként (`@State private var
  showListFallback = false`, sheet vagy push) hív meg.
- Ez fedi a `custom_other`-t és a `back`-et (1.4), és bármilyen jövőbeli új
  taxonómia-elemet automatikusan, hardcode nélkül.

### 2.6 Custom/other kezelése

- A Lista nézetben `custom_other` kiválasztásakor ugyanaz a validáció,
  mint a P2-ben (`requiresCustomLabel`/`requiresCustomDescription`,
  `customLabelSection`/`customDescSection`) — **ez a kód 1:1 átemelhető**
  a `TaxonomyListPickerView`-ba.

### 2.7 Navigáció / action bar

- "Vissza" / "Mentés és tovább" / "Mentés és befejezés" — **logika
  változatlan** (`vm.labelEvent(...)`, `currentIndex` léptetés,
  `loadFormState()` resetelt body-zone+chip state-tel).
- `canSave` feltétel kibővül: body-zone út esetén `selectedKey != nil`
  (a zóna+chip kiválasztásból), lista út esetén a meglévő logika.
- Progress (`Címkézés (i/n)`) — marad a navigationTitle-ben, változatlan.
- Mentési hiba — marad az `.alert`, változatlan.

---

## 3. Fájllista (tervezett)

| Fájl | Típus | Tartalom |
|---|---|---|
| `Juggling/Annotation/Screen/EventLabelDetailView.swift` | MODOSÍTOTT | body-zone UX-re átépített `formView`; preview szekció; "Egyéb / Lista nézet" link; `videoURL` init paraméter |
| `Juggling/Annotation/Screen/BodyZonePickerView.swift` | ÚJ | interaktív emberábra, `Shape`-alapú zónák, tap callback |
| `Juggling/Annotation/Screen/BodyZone.swift` | ÚJ | `enum BodyZone` + `contactTypes(in:)` szűrő a taxonomy-ból |
| `Juggling/Annotation/Screen/EventPreviewPlayerView.swift` | ÚJ | preview player (saját `PlaybackController` + `AVPlayerLayerView` + loop) |
| `Juggling/Annotation/Screen/TaxonomyListPickerView.swift` | ÚJ (kiszervezve P2-ből) | a jelenlegi taxonómia-lista + custom mezők, fallback módként |
| `Juggling/Annotation/EventStillFrameGenerator.swift` | ÚJ | `AVAssetImageGenerator` wrapper, still frame |
| `Screen/JugglingAnnotationScreen.swift` | MODOSÍTOTT | `videoURL` átadása az `EventLabelDetailView`-nak (`.sheet`) |
| `LFAEducationCenterTests/Juggling/BodyZoneTests.swift` | ÚJ | `BodyZone.contactTypes(in:)` leképezés unit tesztek (mind a 12 zóna + fallback) |
| `LFAEducationCenterTests/Juggling/EventLabelingVMTests.swift` | VÁLTOZATLAN | a `labelEvent`/`screenMode` logika nem változik, a meglévő 9 teszt továbbra is releváns |
| `project.pbxproj` | MODOSÍTOTT | 4 új fájl regisztrálása |

**ViewModel**: nincs új VM, nincs VM-módosítás — `labelEvent(...)`,
`enterLabelingMode()`, `exitLabelingMode()`, `screenMode` mind változatlanok.
A body-zone state (`selectedZone`, `selectedKey`, `selectedSide`,
`confidence`, custom mezők) **lokális `@State`** az `EventLabelDetailView`-ban,
ahogy a P2-ben is volt.

---

## 4. iOS 14 kompatibilitás — kockázati pontok

- `LazyVGrid` — iOS14 OK.
- `Shape`/`Path`/`GeometryReader`/`.onTapGesture` — iOS14 OK.
- `AVAssetImageGenerator.copyCGImage(at:actualTime:)` — iOS14 OK (deprecated
  csak iOS16+-ban, de **nem tiltott**, és az async variáns
  `generateCGImagesAsynchronously` is iOS14-től elérhető — ezt használjuk).
- `safeAreaInset(edge:)` — **iOS15+, NEM használható.** Helyette: rögzített
  `VStack` lent + `Spacer()` fent, ahogy a `JugglingAnnotationScreen` már
  most is csinálja a "Mentés és bezárás" gombbal.
- Második `AVPlayer`/`PlaybackController` instance egyidejűleg a fő
  screen player-jével — nincs API-szintű iOS14 korlát, de figyelni kell
  az audio session-re (két player audio route-ja ütközhet). Mivel a
  marking screen player-je a sheet megnyitásakor jellemzően szünetel
  (`vm.enterLabelingMode()` előtt a user nem feltétlenül pause-olja —
  **ezt a P2B implementáció elején explicit `playback.pause()`-szal
  kell kezelni** a `JugglingAnnotationScreen`-ben, a `.sheet`
  megnyitásakor).

---

## 5. Tesztterv

### Unit
- `BodyZoneTests` — minden zóna helyes `contactTypes(in:)` szűrést ad
  (pl. jobb lábfej → 4 elem, mind `side=="right"`; mellkas → 1 elem,
  `side==nil`/`center`).
- `EventStillFrameGeneratorTests` — adott `AVAsset` + ms → nem-nil `UIImage`,
  hibás asset → nil, nem dob.
- `EventLabelingVMTests` — változatlan, 9/9 továbbra is releváns
  (a `labelEvent` szerződés nem változik).

### UI / manuális
1. 2 esemény jelölve → "Tovább a címkézéshez" → sheet nyílik, preview
   lejátszó látszik (still frame az event timestampjén).
2. Play a preview-n → kis ablakban (~timestamp±0.5-1.0s) lejátszik, majd
   loop vagy pause.
3. Body-zóna tap (pl. jobb lábfej) → kiemelődik, alatta 4 chip
   (rüszt/belső/külső/sarok).
4. Chip kiválasztás → bizonyosság választás → "Mentés és tovább" aktív.
5. 1 elemű zóna (pl. jobb térd) → automatikus preselect, chip látszik,
   közvetlenül menthető.
6. "Egyéb / Lista nézet" → megnyílik a teljes taxonómia-lista (P2-ből
   ismert), `custom_other` választható, kötelező mezők validálnak.
7. "Vissza" → előző esemény, korábbi zóna/chip-választás visszatöltve.
8. Bezárás+újranyitás → progress megmarad (mint P2-ben, `labelEvent`
   perzisztencia változatlan).
9. Kis képernyő (SE) → action bar mindig látható, body-diagram nem csúszik
   a gombok alá.
10. VoiceOver: minden zóna és chip felolvasható, tap target ≥44pt.

---

## 6. Implementációs sorrend (javaslat, külön jóváhagyásra)

- **P2B-1**: `BodyZone.swift` + `BodyZoneTests.swift` (tiszta leképezés,
  nincs UI).
- **P2B-2**: `EventStillFrameGenerator.swift` + teszt.
- **P2B-3**: `EventPreviewPlayerView.swift` (preview player + still frame
  + loop), `videoURL` átadása a sheet-nek, `playback.pause()` a sheet
  megnyitásakor.
- **P2B-4**: `BodyZonePickerView.swift` (emberábra + zónák + tap).
- **P2B-5**: `TaxonomyListPickerView.swift` kiszervezés P2-ből (fallback
  mód), `EventLabelDetailView.swift` átépítése az új layoutra
  (preview + body-zone + chip-sor + fallback link + action bar).
- **P2B-6**: manuális UI-teszt jegyzőkönyv + screenshot-ok, build+test
  futás, commit.

Minden lépés külön build+test futtatással, a meglévő 47 teszt + az új
tesztek zöld állapotával.

---

## 7. Nyitott kérdések a jóváhagyáshoz

1. **Hát (`back`) és `custom_other` csak a Lista fallback-ben legyen** —
   ahogy 1.4-ben javasolva (A opció)? Vagy szükséges külön hátsó nézet?
2. **Preview ablak hossza**: `timestamp - 500ms` … `timestamp + 1000ms`
   (1.5s loop) megfelelő, vagy más arány kell?
3. **Still frame generálás**: minden esemény-váltáskor újra generáljuk,
   vagy cache-eljük a sheet életciklusára (memóriában, `[UUID: UIImage]`)?
   Javaslat: egyszerű in-memory cache a sheet `@State`-jében — elég 1-4
   eseményhez, nincs perzisztencia-igény.
4. Emberábra **stílusa**: egyszerű vonalas/sematikus (SF Symbols-szerű,
   monokróm) vagy színes/illusztrált? Javaslat: egyszerű, monokróm
   vonalrajz `Path`-okkal — gyors, karbantartható, dark mode-barát.

---

**P2B implementáció nincs elindítva** — várom a külön jóváhagyást a fenti
tervre és a 7. szakasz nyitott kérdéseire.
