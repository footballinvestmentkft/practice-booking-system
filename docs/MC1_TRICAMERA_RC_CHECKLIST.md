# MC1 Tricamera Release Candidate Checklist v1.1

**Cél:** a fizikai `tricamera-capture-skeleton-proof` teszt, illetve bármely későbbi MC1 release előtti teljes rendszer-ellenőrzés. Minden pont objektíven eldönthető PASS/FAIL-re, a megadott bizonyítéktípussal. Kitöltetlen státusz = még nem ellenőrzött.

**Használati szabályok:**
1. Egy pont csak akkor PASS, ha a bizonyíték rögzítve van (parancs-output, CI link, fájl, screenshot). Bizonyíték nélküli PASS érvénytelen.
2. Minden bizonyítéken szerepelnie kell a release commit SHA-nak vagy a futás timestampjének, hogy a bizonyíték a vizsgált állapothoz köthető legyen.
3. FAIL vagy N/A esetén kötelező az indoklás a Megjegyzés mezőben. N/A csak akkor adható, ha a pont az adott release-re bizonyíthatóan nem értelmezhető (indoklással).
4. A M. szekció döntése kizárólag a kitöltött szekciók alapján hozható meg, az ott megadott képlettel.

**Bizonyítéktípus-rövidítések:**
- `CMD` — parancs + teljes output másolat
- `CI` — GitHub Actions run URL + konklúzió
- `FILE` — repo- vagy artifact-fájl útvonala + releváns tartalom-kivonat
- `JSON` — konkrét JSON mező értéke a megnevezett artifactból
- `SCR` — screenshot/fotó, fájlnévben timestamppel
- `GH` — GitHub UI állapot (PR/issue lista) permalinkkel

---

## A. Repository state

| ID | Ellenőrzés | Módszer | Bizonyíték | Státusz | Megjegyzés |
|---|---|---|---|---|---|
| A1 | A release commit SHA rögzítve, és azonos az `origin/main` HEAD-del | `git fetch origin && git rev-parse origin/main` — a kapott SHA kerül a checklist fejlécébe; minden további pont erre a SHA-ra vonatkozik | CMD | | |
| A2 | A lokális working tree tiszta a release SHA-n | `git checkout <SHA> && git status --porcelain` — output üres (a szándékosan ignorált untracked fájlok listája előre deklarálva) | CMD | | |
| A3 | Nincs félbemaradt merge/rebase állapot | `git status` nem tartalmaz "You have unmerged paths" / "rebase in progress" sort; `ls .git/MERGE_HEAD` → nincs ilyen fájl | CMD | | |
| A4 | Nincs nyitott PR, amely MC1 scope-ú fájlt módosít és merge-re vár a fizikai teszt előtt | `gh pr list --state open --json number,title,files` — egyetlen nyitott PR sem érinti a `ios/LFAEducationCenter/MultiCamera/**`, `scripts/mc1_regression/**`, `scripts/run_mc1_regression.sh` útvonalakat; VAGY az érintő PR-ok explicit "nem blokkoló" döntéssel listázva | GH + CMD | | |
| A5 | Nincs nyitott critical/blocker címkéjű issue MC1 scope-ban | `gh issue list --state open --label critical` és `--label blocker` — üres, vagy egyik sem MC1-et érint (tételes indoklással) | GH | | |
| A6 | A fizikai teszten futó build pontosan a release SHA-ból készül | A build előtt: `git rev-parse HEAD` == release SHA; a `MultiCameraLobbyView.buildFingerprint` értéke frissítve az adott release-hez, és a Debug Snapshotban ugyanez jelenik meg | CMD + SCR (Debug Snapshot) | | |
| A7 | Az eszközökre telepített build fingerprint egyezik | Mindkét eszközön `dump-snapshot` deep link → console logban `build: <fingerprint>` sor egyezik az A6-ban rögzítettel | FILE (console log) | | |

## B. GitHub CI

| ID | Ellenőrzés | Módszer | Bizonyíték | Státusz | Megjegyzés |
|---|---|---|---|---|---|
| B1 | A release SHA-n minden required check zöld | `gh api repos/:owner/:repo/commits/<SHA>/check-runs --paginate` vagy `gh pr checks <PR>` — 0 fail, 0 pending a required checkek közt | CI | | |
| B2 | A nem-required, de MC1-releváns workflow-k is zöldek: `ios-ci.yml`, `mc1-regression-harness.yml`, `mc1-e2e-orch2b.yml` | Mindhárom workflow legutóbbi, release SHA-hoz tartozó runja `conclusion: success` | CI (3 run URL) | | |
| B3 | Nincs pending/queued check a release SHA-n | Ugyanaz a lekérdezés, mint B1 — `status: completed` mindenhol | CI | | |
| B4 | Egyetlen zöld check sem rerun-nal lett zöld (flaky-mentesség) | Minden MC1-releváns run `run_attempt == 1`; ha >1, a rerun oka dokumentált és determinisztikusnak bizonyított (a hiba nem MC1 kódban volt) | CI (`gh run view <id> --json attempt`) | | |
| B5 | Branch protection a main-en aktív és a merge azon keresztül történt | `gh api repos/:owner/:repo/branches/main/protection` — required checks lista nem üres; a release commit PR-on keresztül került be (`gh pr view <PR> --json mergedAt,mergeCommit`) | GH + CMD | | |
| B6 | A `mc1-regression-harness` workflow ténylegesen lefuttatta a preflightot ÉS a pytestet (nem skippelt) | A run logban jelen van a `preflight_static_check.py` "X passed, 0 failed" sora és a pytest "N passed" sora | CI (log kivonat) | | |

## C. Build

| ID | Ellenőrzés | Módszer | Bizonyíték | Státusz | Megjegyzés |
|---|---|---|---|---|---|
| C1 | Debug build fordul szimulátorra a release SHA-n | `xcodebuild build-for-testing -project ios/LFAEducationCenter.xcodeproj -scheme LFAEducationCenter -destination 'platform=iOS Simulator,id=<UDID>'` → exit 0 | CMD | | |
| C2 | Debug build települ és elindul a fizikai iPhone-on | `xcodebuild ... -destination 'platform=iOS,id=<IPHONE_UDID>'` build+install exit 0; app elindul, Session Lab megnyílik | CMD + SCR | | |
| C3 | Debug build települ és elindul a fizikai iPadon | Ugyanaz iPad UDID-vel | CMD + SCR | | |
| C4 | Release konfiguráció fordul (regresszió-őr: a DEBUG-only MC1 kód nem töri a Release buildet) | `xcodebuild build -configuration Release -scheme LFAEducationCenter -destination generic/platform=iOS CODE_SIGNING_ALLOWED=NO` → exit 0 | CMD | | |
| C5 | A fizikai teszthez Debug buildet használunk (a teljes MC1 automation `#if DEBUG` mögött van) — ez explicit rögzítve | A telepítési parancsban `-configuration Debug`; a `lfa-mc1://` scheme válaszol (lásd E-szekció preflight) | CMD | | |
| C6 | `lfa-mc1://` URL scheme regisztrált mindkét eszközön | `lib.preflight_url_scheme` fut a runner indulásakor és nem dob (a runner log "[preflight] ... scheme OK" mindkét eszközre) | FILE (runner output) | | |

## D. Tests

| ID | Ellenőrzés | Módszer | Bizonyíték | Státusz | Megjegyzés |
|---|---|---|---|---|---|
| D1 | iOS unit tesztek: teljes `LFAEducationCenterTests` suite 0 failure a release SHA-n | CI `ios-ci.yml` "Executed N tests, with 0 failures", N rögzítve | CI | | |
| D2 | MC1 célzott unit tesztek lokálisan is zöldek: `MC1AutomationBridgeTests` (benne AB-17* replay guard), `LivePoseOverlayProcessorTests`, `PCOInstructorRoleGateTests`, `PlayerCaptureOrchestratorTests`, `CycleCaptureOrchestratorTests`, `CaptureAuthorityTests`, `OrientationMapperTests`, `CaptureFormatSelectorTests` | `xcodebuild test-without-building -only-testing:...` → "TEST EXECUTE SUCCEEDED", suite-onkénti Executed/failures sorok | CMD | | |
| D3 | Backend integration: MC1 ORCH-2B E2E (16 lépés) zöld a release SHA-n | `mc1-e2e-orch2b.yml` run success | CI | | |
| D4 | Regression harness pytest: `scripts/mc1_regression/tests/` — minden teszt zöld, benne a `test_diag_contract.py` writer↔reader kontraktus tesztek | `python3 -m pytest scripts/mc1_regression/tests/ -q` → "N passed, 0 failed" | CMD | | |
| D5 | Statikus preflight: minden check PASS, exit 0 | `python3 scripts/mc1_regression/preflight_static_check.py` → "X passed, 0 failed", exit 0 | CMD | | |
| D6 | A preflight NEM lett skippelve az utolsó futásnál | `scripts/mc1_regression_runs/static_preflight_skip_audit.log` — nincs a release időszakára eső bejegyzés; a legutóbbi `report.json`-ban `static_preflight_skipped == false` | FILE + JSON | | |
| D7 | Runtime gate-ek definíció szerint aktívak: a tricamera `critical_ok` listában szerepel mind a 14 gate-step (stale-invalidation, backend confirmok, GoPro preview quality+aspect, 3× panel frame traffic, 2× orientation, 2× aspect, timestamp sync) | `grep -A20 "critical_ok = all" scripts/mc1_regression/scenarios.py` a release SHA-n — a lista tételesen egyezik a dokumentált PASS-definícióval | CMD | | |
| D8 | Unattended regresszió (`--scenario all`: smoke + multicycle) fizikai eszközökön PASS a release buildekkel | `./scripts/run_mc1_regression.sh --scenario all` → `report.json` `overall_pass == true` | JSON (`report.json`) + FILE (artifact könyvtár) | | |

## E. Device routing

| ID | Ellenőrzés | Módszer | Bizonyíték | Státusz | Megjegyzés |
|---|---|---|---|---|---|
| E1 | iPhone CoreDevice UDID rögzítve és él | `xcrun devicectl list devices` — az iPhone szerepel, UDID egyezik a futtatási env-vel (`IPHONE_UDID`) | CMD | | |
| E2 | iPad CoreDevice UDID rögzítve és él | Ugyanaz `IPAD_UDID`-re | CMD | | |
| E3 | Console log ↔ eszköz hozzárendelés identitás-alapú | A runner shell log tartalmazza a `_map_legacy_udid` eredményét mindkét eszközre (CoreDevice→legacy mapping), NEM sorrend-alapú fallbackot; a két legacy UDID különbözik | FILE (shell output) | | |
| E4 | iPhone = instructor: a backend session-ben az iPhone-hoz tartozó device `device_role == "instructor_primary"` | `GET /sessions/{uuid}` a futás alatt (a scenario `backend_state/*_session.json` dumpja) — device_name/UDID alapján azonosítva | JSON | | |
| E5 | iPad = player: `device_role == "player_primary"` ugyanígy | Ugyanaz a session dump | JSON | | |
| E6 | GoPro = auxiliary: `device_role == "auxiliary_camera"` ÉS `managed_by_device_id == <instructor device id>` | Session dump + a scenario "gopro registered" step detailje | JSON | | |
| E7 | Instructor eszközön `IsController: yes`, player eszközön `IsController: no` a Debug Snapshotban | Mindkét eszköz `dump-snapshot` kivonata a futás alatt (`debug_snapshots/*`) | FILE | | |
| E8 | Player eszközön PCO attach megtörtént, instructor eszközön NEM (role gate) | Player console: `[PCO]` sorok jelen; instructor console: nincs `[PCO] confirmed` a saját device_id-jére; `PCOInstructorRoleGateTests` zöld (D2) | FILE (console) + CMD | | |
| E9 | Minden device_id egyedi, nincs duplikált session_device ugyanarra a fizikai eszközre | Session dump `devices[]` — eszközönként pontosan 1 nem-removed bejegyzés | JSON | | |
| E10 | Dual-console hard precondition: MINDKÉT eszköz USB-n csatlakozik, trusted, és látható az `idevicesyslog` számára — tricamera scenariónál e nélkül a futás el sem indulhat (a 2026-07-04-i futás a player-oldali console evidencia nélkül futott és bukott) | `idevice_id -l` mindkét legacy UDID-t listázza; a shell wrapper és a runner.py hard-fail ága NEM triggerelt (`check_dual_console_precondition`); `console/iphone_console.log` ÉS `console/ipad_console.log` létrejött | CMD + FILE | | |

## F. Capture lifecycle

| ID | Ellenőrzés | Módszer | Bizonyíték | Státusz | Megjegyzés |
|---|---|---|---|---|---|
| F1 | Prepare: mindkét iOS eszköz eléri a `ready` capture state-et a cycle előtt | Debug Snapshot `capture: ready ✓` mindkét eszközön; scenario nem timeoutol a join/prepare fázisban | FILE (snapshot) | | |
| F2 | Preview: az instructor dashboardon mindhárom panel él a felvétel előtt | SCR a dashboardról (3 panel, timestamp látszik); pose diag `sourceFramesSeen > 0` mindhárom panelre | SCR + JSON (`pose_overlay_diag.json`) | | |
| F3 | confirmed_start: mindhárom cycle_device `recording_status == "confirmed_start"` `started_at` timestamppel | `proof_cycle_timing.json` / cycles dump | JSON | | |
| F4 | Recording: a felvételi ablak a tervezett hosszú (± 2 s); mindkét iOS fájl `actualDurationSeconds` konzisztens a cycle started/stopped delta-val | `capture_metadata_diag.json` (mindkét eszköz) vs `proof_cycle_timing.json` | JSON | | |
| F5 | confirmed_stop: mindhárom cycle_device `recording_status == "confirmed_stop"` `stopped_at`-tal, cycle `status == "completed"` | Cycles dump / scenario "all 3 confirmed_stop" step | JSON | | |
| F6 | Fájl-validáció: mindkét iOS fájl `fileSizeBytes > 0`, codec `h264`, fps ≈ 30, a `CaptureFormatSelector` profil (1280x720 vagy dokumentált 640x360 fallback) érvényesült | `capture_metadata_diag.json` mezők mindkét eszközről | JSON | | |
| F7 | Cleanup/reset: a scenario utáni `reset-session` után a ViewModel `.idle`, következő join sikeres (multicycle/all futásban bizonyítva) | Következő scenario join stepje PASS ugyanabban a runban | FILE (report.json steps) | | |
| F8 | Timestamp sync: a 3 eszköz `started_at` értékei közti max eltérés rögzítve és ≤ az előre deklarált tűrésnél (tűrést a futás ELŐTT kell számszerűsíteni és ide beírni: ___ ms) | `proof_cycle_timing.json` started_at delták kiszámolva | JSON + CMD | | |

## G. GoPro

| ID | Ellenőrzés | Módszer | Bizonyíték | Státusz | Megjegyzés |
|---|---|---|---|---|---|
| G1 | BLE: a connect flow eljut `awaitingManualWiFiJoin(ssid:)`-ig (SSID kinyerve BLE-n) | Console log `gopro_connection: Csatlakozz: <SSID>` vagy snapshot ugyanezzel | FILE | | |
| G2 | Wi-Fi: manuális join után HTTP verify sikeres, GoPro state `ready` | `gopro_diag.json` `outcome == "signalReady_ok"` (vagy `_after_409_retry`) | JSON | | |
| G3 | Backend ready: a GoPro session_device `status == "ready"` a backend oldalon | Scenario "gopro ready" step PASS + session dump | JSON | | |
| G4 | Preview: friss `gopro_stream_diag.json` — `udpPacketsReceived > 0` ÉS `videoPIDFound == true` ÉS `decodeSuccesses > 0` | JSON mezők; a fájl frissessége az I-szekció szerint bizonyított | JSON | | |
| G5 | Preview aspect: `previewAspectRatio` a mért SPS-ből 16:9 (± a `_aspect_ratio_matches` tűrés) | Ugyanazon diag `previewWidth/previewHeight/previewAspectRatio` | JSON | | |
| G6 | Recording: shutter start/stop OK ÉS a media/list diff új fájlt mutat az SD kártyán | Scenario "gopro confirmed_start"/"confirmed_stop" + `[GOPRO-MEDIA-BEGIN]` blokk az iPhone console logban | JSON + FILE | | |
| G7 | Stop után a GoPro ténylegesen nem vesz fel | Fizikai ellenőrzés: a kamera kijelzőjén nincs REC a scenario vége után (K-szekció manuális pont hivatkozása) + `recordingStateAfterStop == "stopped"` ha combined-proof diag készült | SCR + JSON | | |
| G8 | Diag artifactok hiánytalanok: a scenario minden GoPro diag fájlja bekerült az artifact könyvtárba | `ls <run_dir>/video_artifacts/` + report.json artifact stepek PASS | FILE | | |
| G9 | Ismert korlát explicit elfogadva: auto Wi-Fi join entitlement hiányában manuális (SystemWiFiTransport mindig `.unavailable`) — operátor jelen van | L-szekcióban dokumentált, futtatási tervben operátor nevesítve | FILE (checklist) | | |

## H. Skeleton pipeline

| ID | Ellenőrzés | Módszer | Bizonyíték | Státusz | Megjegyzés |
|---|---|---|---|---|---|
| H1 | Frame-forgalom instructor panel: friss `pose_overlay_diag.json` `instructor.framesReceived ≥ 1` | JSON (frissesség I-szekció szerint) | JSON | | |
| H2 | Frame-forgalom player panel: `player.framesReceived ≥ 1` ÉS `player.sourceFramesSeen > 0` (MPC ténylegesen szállított) | JSON | JSON | | |
| H3 | Frame-forgalom GoPro panel: `gopro.framesReceived ≥ 1` ÉS `gopro.sourceFramesSeen > 0` (decode ténylegesen történt) | JSON | JSON | | |
| H4 | Vision fut: legalább egy panelen `framesProcessed > 0` és `visionDetectionSuccesses` rögzítve (érték lehet 0, ha nem volt ember a képben — de a mező jelen van) | JSON | JSON | | |
| H5 | Overlay vizuálisan látszik: cyan skeleton mindhárom panelen, miközben ember áll a képben | Operátor screenshot: `visual_skeleton_overlay.png` az artifact könyvtárban (K-szekció) | SCR | | |
| H6 | Export: `skeleton_output.json` friss, `sampled_frames > 0`, `total_joints_detected` rögzítve, `generated_at` a futás utáni | JSON | JSON | | |
| H7 | A pose diag kulcs-kontraktus a release SHA-n garantált (writer `framesReceived` ↔ reader `framesReceived`) | D4 kontraktus tesztek zöldek + D5 preflight CHECK 11 PASS | CMD | | |

## I. Artifact integrity

| ID | Ellenőrzés | Módszer | Bizonyíték | Státusz | Megjegyzés |
|---|---|---|---|---|---|
| I1 | Sentinel-invalidálás lefutott: a report `"stale diag artifacts invalidated"` step PASS, minden targetre `true` | `report.json` step detailek | JSON | | |
| I2 | Egyetlen gate-elt artifact sem sentinel: a begyűjtött diag fájlokban nincs `mc1_invalidated: true` | `grep -l mc1_invalidated <run_dir>/video_artifacts/*.json` → üres | CMD | | |
| I3 | Frissesség: minden gate-elt diag `timestamp`/`generated_at` mezője a scenario indulása UTÁNI (a gate ezt kikényszeríti — bizonyíték: nincs "stale"/"predates" error a report stepekben) | `report.json` stepek + a diag fájlok timestamp mezői kézzel szúrópróbázva (min. 2 fájl) | JSON | | |
| I4 | run_id lánc: a report `run_id`-ja egyezik a sentinel-fájlokban rögzítettel (a `diag_sentinels/` könyvtárban) | `report.json` "stale diag artifacts invalidated" step `run_id` == `diag_sentinels/sentinel_*.json` `run_id` | FILE + JSON | | |
| I5 | JSON kontraktus: minden gate által olvasott mező létezik a writer oldalon (pose: kontraktus-teszt; capture metadata: `orientationConsistent`, `effectiveAspectRatio`, `fileSizeBytes` jelen; gopro stream: `udpPacketsReceived`, `videoPIDFound`, `decodeSuccesses` jelen) | D4/D5 + a begyűjtött fájlok mezőlistája | CMD + JSON | | |
| I6 | A console log a helyes eszközhöz tartozik: az `iphone_console.log`-ban instructor-oldali markerek ([CCO], GOPRO-AUTO), az `ipad_console.log`-ban player-oldaliak ([PCO]) | `grep` mindkét logban; E3 mapping-bizonyíték | CMD + FILE | | |
| I7 | Az artifact könyvtár teljes és archiválva: `report.json`, `report.txt`, `backend_state/*`, `console/*`, `debug_snapshots/*`, `video_artifacts/*` mind jelen, a run könyvtár neve (timestamp) rögzítve a checklistben | `ls -R <run_dir>` | FILE | | |

## J. Orientation

> **P1 DÖNTÉS (2026-07-04, issue #357):** az app portrait-only (`Info.plist`), ezért az
> `OrientationMapper` mindig portrait-ot ad — landscape-re szerelt eszköznél a player
> preview ÉS a rögzített fájl orientációja hibás (az első fizikai futás bizonyította).
> Amíg a #357 nincs javítva: **capture-start chain validációs futás CSAK portrait
> szereléssel** végezhető (J1 ennek megfelelően értelmezendő); **landscape tricamera
> futás a #357 javításáig BLOKKOLT.** A #357 javítása külön PR — nem keverendő a
> capture-session P0 fixszel.

| ID | Ellenőrzés | Módszer | Bizonyíték | Státusz | Megjegyzés |
|---|---|---|---|---|---|
| J1 | Előfeltétel deklarálva: mindkét iOS eszköz állványon, elforgatás-mentesen rögzítve a futás alatt — a #357 javításáig PORTRAIT állásban (a fenti P1 döntés szerint); a #357 lezárása után landscape-ben (a 16:9 gate akkor válik teljes értékűvé) | Futtatási terv + setup fotó | SCR | | |
| J2 | Rotation lock állapot rögzítve a futás előtt (KI/BE), és a futás alatt nem változik | Setup fotó a Vezérlőközpontról vagy explicit operátor-nyilatkozat a futási jegyzőkönyvben | SCR/FILE | | |
| J3 | iPhone: `orientationConsistent == true` ÉS `deviceOrientationAtRecordingStart == fileOrientationCoarse` | `capture_metadata_diag.json` (iPhone) | JSON | | |
| J4 | iPad: ugyanaz | `capture_metadata_diag.json` (iPad) | JSON | | |
| J5 | iPhone/iPad effektív aspect 16:9: `effectiveAspectRatio` a tűrésen belül | Ugyanazon diagok | JSON | | |
| J6 | GoPro preview aspect 16:9 a mért SPS-ből (== G5) | `gopro_stream_diag.json` | JSON | | |
| J7 | Vizuális ellenőrzés: a felvett fájlok lejátszva helyes állásúak (nem elfordítottak) — mivel az automata gate csak interface↔fájl konzisztenciát mér, gravitáció-helyességet nem | Operátor visszanézi mindkét iOS videót + rögzíti (K-szekció) | SCR + FILE (jegyzőkönyv) | | |
| J8 | Metadata: a fájlok `preferredTransform`-ból derivált orientation mező jelen van és nem "unknown" | `capture_metadata_diag.json` `actualOrientation` | JSON | | |

## K. Physical validation — manuális, nem automatizálható pontok

Ezeket ember végzi; mindegyikhez timestampelt bizonyíték kötelező.

| ID | Manuális ellenőrzés | Miért nem automatizálható | Bizonyíték | Státusz | Megjegyzés |
|---|---|---|---|---|---|
| K1 | Mindhárom panelen látható cyan skeleton overlay, miközben ember mozog a képtérben | A Vision "talált-e embert" nem wiring kérdés; képtartalom-helyesség pixel-szinten nem gate-elt | `visual_skeleton_overlay.png` az artifact könyvtárban | | |
| K2 | GoPro Wi-Fi manuális join elvégzése a script promptjára | HotspotConfiguration entitlement hiánya (personal team) | Futási jegyzőkönyv bejegyzés (ki, mikor) | | |
| K3 | A felvett videók visszanézése: kép éles, hang van, orientáció helyes, nincs korrupció | Fájl-validáció csak konténer-szintű; percepciós minőség nem automatizált | Rövid képernyővideó vagy jegyzőkönyv mindhárom felvételről | | |
| K4 | GoPro fizikai állapot a futás után: nem vesz fel, nem forró, akku > 20%, SD-n ott az új fájl | Kamera kijelző/hardver állapot kívül esik az API-diagnosztikán | Fotó a kamera kijelzőjéről + media/list kivonat | | |
| K5 | Dashboard UX folyamatosság: a futás alatt nem volt fagyás/fekete panel/app crash | UI-folytonosság futás közben nem gate-elt (nincs XCUITest fizikai eszközön) | Operátor jegyzőkönyv; crash esetén ips log csatolva | | |
| K6 | Preset-write jellegű scenariók után: a GoPro beállításai az eredetiek (ha ilyen scenario futott) | Rollback-verify automata, de a kamera menüjének végső állapotát ember erősíti meg | Fotó a kamera beállítás-képernyőjéről | | |
| K7 | Környezet rögzítése: helyszín, világítás, kamera-távolságok, Wi-Fi környezet (más GoPro/MPC eszközök jelenléte a térben) | Reprodukálhatósághoz kell; automatikusan nem gyűjtött | Setup fotó + jegyzőkönyv | | |

## L. Known limitations — explicit kockázat-elfogadás

Minden tétel PASS-a azt jelenti: "a korlát dokumentált, a döntéshozó név szerint elfogadta erre a futásra/release-re". Nem azt, hogy a hiba javítva van.

| ID | Korlát | Kategória | Elfogadás módja | Státusz | Elfogadó + dátum |
|---|---|---|---|---|---|
| L1 | GoPro stop-recovery hiánya: shutter/start HTTP timeout + ténylegesen elindult felvétel esetén az app nem tudja leállítani a kamerát (recordingState=.failed → stopRecording blokkolt); mitigáció: K4 fizikai ellenőrzés | P1 | Jegyzőkönyvi elfogadás | | |
| L2 | MPC session-validáció hiánya: a player bármely invite-ot elfogad, az instructor bármely peert meghív (session UUID nincs egyeztetve); mitigáció: futás alatt nincs másik MC1 session a térben (K7) | P1 | Jegyzőkönyvi elfogadás | | |
| L3 | MPC titkosítatlan (`encryptionPreference: .none`) — élő videó a lokális hálón; production előtt kötelező javítás | P1 / production blocker | Jegyzőkönyvi elfogadás fizikai tesztre; production: NEM elfogadható | | |
| L4 | Instructor role-flash: session-létrehozás után ~3 s-ig PlayerCaptureView jelenhet meg az instructoron (isController az első pollig false) | P1 | Jegyzőkönyvi elfogadás | | |
| L5 | SkeletonProcessor a MainActort blokkolja a feldolgozás alatt (UI fagy, heartbeat kiesés lehetséges a skeleton-process lépés alatt) — a scenario ezt a confirmed_stop UTÁN futtatja | P1 | Jegyzőkönyvi elfogadás | | |
| L6 | PCO stop/start átfedési race + timeout nélküli stop-várakozás (beragadhat .stoppingCapture-ben) | P1 | Jegyzőkönyvi elfogadás | | |
| L7 | GoProStreamProbe: nincs reentrancia-guard, NWConnection-ök nem záródnak, minden a main queue-n — egy futáson belül csak EGY stream-probe action engedélyezett (futtatási terv betartja) | P1 | Jegyzőkönyvi elfogadás | | |
| L8 | Orientation gate csak interface↔fájl konzisztenciát mér; rotation lock + elfordított eszköz mellett vizuálisan rossz videó is PASS-olhat — mitigáció: J1/J2/J7 | P2 | Jegyzőkönyvi elfogadás | | |
| L9 | Per-frame CIContext, objectWillChange 1-frame-lag/duplikált feed, dashboard nem observeli a GoPro managert, InstructorCaptureView halott kód, MPEGTSDemuxer teszteletlen, firmware-gate halott kód, capture-quality-proof nincs regisztrálva | P2 / tech debt | Backlog-hivatkozás elég | | |
| L10 | Production blockerek (fizikai tesztet NEM blokkolják): nincs upload pipeline (`uploadStatus: not_implemented`), manuális GoPro Wi-Fi join (entitlement), MPC titkosítás (L3), TD-01 device dedup, teljes MC1 UI `#if DEBUG` mögött | Production blocker | Listázva; M2 döntésnél kötelezően NO-t okoz, amíg nyitott | | |
| L12 | Portrait-only app: OrientationMapper mindig portrait; landscape szerelésnél elforgatott preview + elforgatott rögzített fájl (issue #357) — mitigáció: portrait szerelés a J-szekció döntése szerint | P1 (landscape tricamera célra BLOCKER) | Jegyzőkönyvi elfogadás portrait futásra; landscape futás: NEM engedélyezett a #357-ig | | |
| L11 | A fenti lista teljessége: a 2026-07-04-i review P1/P2 találatai közül egy sem hiányzik erről a listáról | — | Tételes összevetés a review dokumentummal | | |

## M. Release decision

A döntés kizárólag a fenti szekciók kitöltött státuszai alapján hozható meg.

**M1 — READY FOR PHYSICAL VALIDATION feltétele:**
A1–A7, B1–B6, C1–C3+C5–C6, D1–D8, E1–E9 mind PASS; F/G/H/I/J szekciókból a futás ELŐTT eldönthető pontok (D7, G9, H7, I5-statikus fele, J1, J2) PASS; K-szekció előkészítve (operátor + jegyzőkönyv-sablon megvan); L1–L9 + L11 elfogadva. (F/G/H/I/J futás-függő pontjai magát a fizikai futást minősítik, nem előfeltételei.)

```
READY FOR PHYSICAL VALIDATION

YES / NO
```

**M2 — READY FOR PRODUCTION feltétele:**
M1 = YES ÉS a fizikai validáció minden futás-függő pontja (F, G, H, I, J, K) PASS bizonyítékkal ÉS L10 minden production blockere lezárva (nem "elfogadva" — lezárva) ÉS L1–L7 P1 tételekre javítás vagy formális, határidős kivétel van.

```
READY FOR PRODUCTION

YES / NO
```

---
*Checklist verzió: v1.1 (2026-07-04 — E10 dual-console precondition, J-szekció orientation P1 döntés #357, L12). A checklist maga is release-artifact: kitöltött példánya a futás artifact-könyvtárába kerül (`<run_dir>/rc_checklist_filled.md`), a release SHA-val a fejlécében.*
