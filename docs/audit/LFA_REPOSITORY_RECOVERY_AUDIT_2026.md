# LFA Education Center — Repository Recovery Audit 2026

Audit date: **2026-09-08**, Europe/Budapest. Checkout: **a14380c7697817c7c75587007839ba319dc39cc6**, branch **feat/mc2-pr3-status-dashboard-mpc-removal**.

## P0 canonical baseline closure — 2026-09-08

The controlled P0 remediation was merged without conflict into the audited canonical source branch. The validated application baseline is tagged **`LFA_CANONICAL_BASELINE_P0`**.

| Control | Value |
| --- | --- |
| P0 baseline status | **CLOSED** |
| Canonical target branch | `feat/mc2-pr3-status-dashboard-mpc-removal` |
| Audit base | `a14380c7697817c7c75587007839ba319dc39cc6` |
| Remediation source | `codex/p0-controlled-remediation` at `f341bca5f7a26998e2e63d8b0b542a33f95ba935` |
| Canonical baseline / merge commit | `894c43a4a37af4a8a583240a5a2d0b1f31a3fdee` |
| Freeze marker | `LFA_CANONICAL_BASELINE_P0` |

| Finding | Status | Closure evidence |
| --- | --- | --- |
| F01 | **CLOSED** | Password/hash output removed; credential-header values redacted; logging/auth regression PASS |
| F02 | **CLOSED** | Adaptive Learning ownership enforced through the canonical authorization policy |
| F03 | **CLOSED** | Attendance object authorization shared by web and API through the canonical policy |
| F04 | **CLOSED** | Credit mutations are transactional and replay-safe; balance/ledger rollback and concurrency tests PASS |
| F05 | **CLOSED** | Unlock and renewal overspend races prevented; PostgreSQL concurrency tests PASS |
| F06 | **CLOSED** | Cancellation refunds the recorded debit exactly once and retains financial history; ambiguous history fails closed |
| F07 | **CLOSED** | Fabricated progression read/write endpoints fail closed |
| F22 | **CLOSED** | Certificate service returns rendered PDF output rather than placeholder bytes |

Post-merge evidence: P0 critical PostgreSQL gate **32 passed**; security and sensitive-header logging gate **251 passed**; backend/API contract and affected smoke gate **246 passed**; unsigned iOS Debug simulator build PASS; iOS unit tests **962 passed, 0 failed, 0 skipped**. All P1, P2 and P3 audit findings remain **OPEN** with their original evidence and priority. No historical-license backfill, data reinterpretation, production mutation or deployment was performed; uncertain historical unlock evidence continues to fail closed.

The remainder of this document preserves the original audit snapshot. Statements about P0 findings being open or PostgreSQL concurrency being unverified describe the audited `a14380c…` state and are superseded by this closure record only for F01/F02/F03/F04/F05/F06/F07/F22.

**AUDIT ONLY. No application implementation, dependency upgrades, database mutations, migration execution, deployment or deletion was performed.** Repository paths below are relative to the audited checkout. Finding evidence records exact paths/lines. Companion inventories form part of this report.

**FACT** means observed source, runtime metadata or test result; **RISK** means a supported failure possibility not demonstrated live; **INFERENCE** means interpretation; **RECOMMENDATION** means future work. **UNVERIFIED never means PASS.** A tested helper does not certify its parent journey.

## A. Executive Summary

**The repository supports incremental recovery but is not release-ready.** Substantial real FastAPI/PostgreSQL and SwiftUI functionality survives, including meaningful tests, current media/device capabilities and the four-program hub. Mounted legacy contracts, fabricated responses, duplicated authorization/credit rules and incomplete education provenance undermine core journeys.

At the audited SHA, eight P0 findings blocked trusted use: submitted passwords were logged; adaptive-learning and attendance APIs lacked object authorization; repeated credit deduction could debit without a new ledger row; unlock/renewal concurrency was unsafe; cancellation refunded a fixed amount and could erase debit evidence; generic progression endpoints fabricated results; certificate download returned placeholder bytes. Six isolated probes reproduced authorization, scoring, response and credit defects using real handlers/services with fake persistence. Concurrent PostgreSQL behavior and deployed exploitability were UNVERIFIED at audit time; the P0 closure evidence above supersedes this historical status for the eight named findings.

The main domain conflicts concern the four canonical products versus old PLAYER/COACH names, season-only enum values, independent license wallets, parallel progress models and absent-schema curriculum SQL. **Credits are a global user resource**, explicitly settled by the owner. A specialization is spending context, not a separate wallet authority.

**A platform-wide rewrite is not justified.** Preserve native AuthManager/APIClient/Keychain, SwiftUI navigation/capture, SQLAlchemy models, current scheduling/booking/tournament components, pure content validators, adaptive selection math and tested media/cryptographic helpers. Two narrow REWRITE decisions cover the absent-schema curriculum adapter and fake progression handlers. Their contracts encode the wrong hierarchy or no persistence; restructuring internals alone cannot preserve meaningful current behavior. Neither decision extends to all education code or the native client.

There are **61 decision units**, counted once each; subcomponents can share files. Percentages describe units, not lines, cost or verified product coverage.

| Decision | Count | Proportion |
| --- | ---: | ---: |
| KEEP | 6 | 9.8% |
| CLEAN | 13 | 21.3% |
| REFACTOR | 36 | 59.0% |
| REWRITE | 2 | 3.3% |
| DELETE | 0 | 0.0% |
| BUILD | 4 | 6.6% |

There are **40 findings: P0 8, P1 20, P2 10, P3 2**. Zero DELETE decisions is deliberate: absent literal references do not prove non-use of data, migrations or compatibility APIs. KEEP is scoped to named component behavior; database adapter connectivity remains UNVERIFIED, with no evidence warranting its replacement.

Verification: **768 selected existing tests PASS, 17 SKIPPED**, 31 content files PASS the existing pure validator, six isolated probes reproduce defects. Unsigned iOS Debug build PASS; Release FAIL. Dependency scans identify vulnerable packages. A limited redacted secrets scan found no supported patterns, not a clean-secret certification. DB integration/migrations, complete browser/native journeys, devices, backup restore and production settings remain UNVERIFIED.

After owner review, controlled remediation in an isolated checkout with a disposable/restored test database is sensible. Deployment, production-data correction, biometric activation and general feature continuation are not supported by this evidence.

## B. Repository Inventory

### Scope, tools and provenance

The tracked inventory contains **3,827 files**: app 1,033; tests 1,060; docs 692; scripts 315; iOS 280 (260 Swift sources). All 2,237 tracked Python files were syntax-scanned: 2,233 parse, four supporting/archive scripts fail (F39), no active application Python syntax failure. Static extraction found 1,040 decorated route declarations; safe import found **1,057 mounted route objects / 1,061 method-route entries** and **141 ORM tables** (139 static table declarations). Mounting, framework routes and multiple methods explain differing inventory units.

Evidence: [static inventory](LFA_STATIC_INVENTORY_2026.json), [declarations](LFA_API_DECLARATIONS_2026.json), [runtime routes](LFA_RUNTIME_ROUTES_2026.json), [OpenAPI](LFA_OPENAPI_SNAPSHOT_2026.json), [ORM schema](LFA_ORM_SCHEMA_2026.json), [provenance](LFA_AUDIT_PROVENANCE_2026.json). Safe import disabled dotenv/settings-file loading and database/network connections; it did **not** execute application lifespan. It proves route/schema construction in the installed environment, not startup.

HEAD is one commit ahead of local main and cached origin/main (both comparisons 1 0). No remote fetch occurred; remote freshness is UNVERIFIED. The July12 HEAD removes MPC/adds MC2 status dashboard; main contains preceding MC2 work. Local refs include native phase A, domain integrity, license duration, AL content/language, biometric phases, Streamlit decommission, credit acquisition and multicamera work. Ref names are discovery clues, not proof of merge/completion; ref SHAs are preserved in provenance.

Existing untracked .agents/, .claude/, .vercel/, docs/MC2_MANUAL_DUAL_PLAYER_RUNBOOK.md, scripts/mc1_login_check.py, scripts/seed_staging_user2.py and skills-lock.json were preserved. No applicable AGENTS.md was found. Ignored local settings were inspected only for non-secret safety flags/location categories. No production records were queried, containers started, seeds executed or content imported into a DB.

| Layer | Actual implementation | Boundary / uncertainty |
| --- | --- | --- |
| Backend | FastAPI/Pydantic/SQLAlchemy; app/main.py and app/api/api_v1/api.py | Modular monolith; many web routes still own business transactions |
| Web | Jinja2 + static JS/CSS;232 inventoried application templates | Current web is not Streamlit; dynamic references limit dead-template proof |
| Native | SwiftUI/async APIClient/Keychain; AVFoundation/Vision/ARKit | Football scoped tabs partial; three program cards disabled |
| Database | PostgreSQL/Alembic;141 ORM tables | Live schema/rows/applied revision UNVERIFIED |
| Cache/jobs | Redis, Celery, APScheduler/background scheduler | Queue execution and multiworker scheduler ownership UNVERIFIED |
| Storage/media | Local upload/static paths; ffmpeg/ffprobe, image/video processing, Playwright exports | Shared storage, quotas, backups and cross-host visibility UNVERIFIED |
| Optional ML | ONNX/embedding providers, rembg/OpenCV, native vision | Accuracy, artifacts/licenses and production activation UNVERIFIED |
| Deployment | Compose/test Dockerfile, CI, scripts, iOS project/config | Missing Docker inputs; actual deployed topology UNVERIFIED |
| Integrations | Native camera/device network; manual invoice/payment verification | No new payment/cloud provider needed to explain current architecture |

### Source-of-Truth Conflict Register

Authority is scoped: owner/product requirements govern intended behavior; active constraints govern a repository-created DB; implementation/contracts govern current behavior; tests prove exercised assertions; old docs/fixtures describe history or intent. Documentation is not automatically authoritative.

| ID | Conflicting sources | Conclusion / disposition |
| --- | --- | --- |
| C01 | Four base config JSONs/loader/hub versus old LicenseType PLAYER/COACH/INTERNSHIP; specialization/lfa_player.py maps to GanCuju | Four canonical IDs established; explicit historical mapping required, not a fifth product (F15) |
| C02 | Owner global-credit rule/User balance versus license balances/XOR ledger owner | Global ownership settled; preserve contextual history during later reconciliation (F17) |
| C03 | curriculum raw SQL/old seeders versus active baseline/models/track.py | Adapter does not fit active schema; live extra legacy tables UNVERIFIED (F10) |
| C04 | Config/hub PRE5–13/YOUTH14–18 versus reachable service6–11/12–18 and July1 season narrative | Executable age/cutoff conflict; boundary policy needs reconciliation (F16) |
| C05 | Native unlock checks versus web select broad enum/no equivalent age-consent check | Different current routes do not justify separate entitlement rules (F15) |
| C06 | Unlock100/250/450/800 and calendar months versus renewal1000/30-day months and refund100 | Inconsistent contract; retain purchase amount/period evidence (F05/F06) |
| C07 | AL API/mock tests expect xp_earned; real service returns score_delta/score | Real-handler probe proves failure; mock PASS insufficient (F09/F36) |
| C08 | Draft import is_active=false versus content_status default PUBLISHED/selection filter | Draft isolation not assured (F19) |
| C09 | Historical MASTER_PROGRESS completion claims versus missing methods/content/native destinations | Current implementation overrides old completion claims (F11/F14/F24/F38) |
| C10 | Generic progress success versus constants/local dict; native deliberately avoids route | Production MOCK (F07) |
| C11 | Native rotation comments versus stateless refresh/logout | Server single-use rotation/revocation absent (F08) |
| C12 | Biometric checklist unfinished phases versus later implementation; legal/DPO gate unchecked | Separate code completion and approval; activation UNVERIFIED (F25) |
| C13 | Compose/Docker/README versus removed Streamlit/root Dockerfile | Old boot recipe is not deployment evidence (F30) |
| C14 | Internship three-level config versus five-semester narrative | Levels and semesters may differ; exact qualification mapping UNVERIFIED |
| C15 | user_progress, user_licenses, sync/coupling services each carry progress | Need one write authority, preserve history/projections (F15/F20) |

No exhaustive dependency-cycle proof was performed. Safe import succeeded on the inspected path, not every latent import combination. Duplicate dependency facades and service names are consolidation candidates, not automatically dead abstractions.

## C. Canonical Four-Training Matrix

Canonical definitions: config/specializations/lfa_football_player.json, lfa_coach.json, gancuju_player.json, internship.json, corroborated by SpecializationType, SpecializationConfigLoader and web/native hub cards. Four additional academy JSONs describe Football Player season variants. LFA_PLAYER_PRE/YOUTH/AMATEUR/PRO and academy values are age/season representations, not additional licenses. Historical PLAYER maps to GanCuju in old license material; it must not be mapped to Football Player merely by its English name.

| Training / specialization | Canonical identifier | Backend | DB | Web | Native | Curriculum | Assessment | Progress | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LFA Football Player | LFA_FOOTBALL_PLAYER | Real skills/sessions; stale credit calls | License/seasons/skills; wallet conflict | Hub/onboarding/training present | Scoped tabs partial |31 AL files; SQL hierarchy conflict | Quiz/AL defects; skill engine exists | Real skills plus duplicate/fake APIs | PARTIAL/CONFLICT |
| LFA Coach | LFA_COACH | Config/service/API; missing methods | Shared license/semesters; old COACH naming | Hub/role views; full course unverified | Disabled card | Legacy seed material; no current AL corpus | Shared quiz; qualification chain unverified | Missing methods/level conflicts | PARTIAL/CONFLICT |
| GānCuju Player | GANCUJU_PLAYER | Belt/config/service/API; stale calls | Shared license/belt/history; old PLAYER name | Hub/role views; full course unverified | Disabled card | Legacy PLAYER seeds; no current AL corpus | Belt components; full assessment unverified | Belt service plus stale adapters | PARTIAL/CONFLICT |
| Internship | INTERNSHIP | Semester/progression/API; stale calls | Shared license/semester/projects | Hub/role views; full course unverified | Disabled card | Legacy seeds; no current AL corpus | Projects/quiz; completion unverified |3 levels/5 semesters unresolved | PARTIAL/CONFLICT |

### Feature coverage

Statuses apply to complete program features. PARTIAL means real components exist without a proven full journey. MISSING indicates demonstrated checkout absence; UNVERIFIED means uncertain applicability/data/evidence. No whole feature receives COMPLETE solely from helper tests. All live DB content and subject-matter completeness are UNVERIFIED.

| Feature | Football Player | Coach | GānCuju | Internship | Evidence / qualification |
| --- | --- | --- | --- | --- | --- |
| Dashboard | PARTIAL | PARTIAL | PARTIAL | PARTIAL | Web hub/dashboard; native Football only |
| Curriculum hierarchy | CONFLICT | CONFLICT | CONFLICT | CONFLICT | F10/F18 current tracks versus old SQL |
| Modules | PARTIAL | LEGACY | LEGACY | LEGACY | Player AL prefixes; other checked-in materials old seeds |
| Lessons | CONFLICT | CONFLICT | CONFLICT | CONFLICT | Adapter targets absent tables |
| Sessions | PARTIAL | PARTIAL | PARTIAL | PARTIAL | Shared Session/Semester/enrollment, policy drift |
| Attendance | CONFLICT | CONFLICT | CONFLICT | CONFLICT | API assignment/history gaps F03/F20 |
| Virtual sessions/training | PARTIAL | UNVERIFIED | UNVERIFIED | UNVERIFIED | Player games exist; applicability elsewhere unspecified |
| Hybrid sessions | PARTIAL | PARTIAL | PARTIAL | PARTIAL | Shared concepts; no four-program E2E |
| Quizzes | CONFLICT | CONFLICT | CONFLICT | CONFLICT | Shared scoring defect F12 |
| Adaptive learning | CONFLICT | MISSING | MISSING | MISSING | Player corpus/API defects; other current corpora/mapping absent |
| Exams | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED | Quiz/project primitives do not prove approved examination process |
| Progression | CONFLICT | CONFLICT | CONFLICT | CONFLICT | Domain services plus fake progress/missing methods |
| XP | PARTIAL | PARTIAL | PARTIAL | PARTIAL | Shared gamification; replay/atomicity incomplete |
| Skills/competencies | PARTIAL | UNVERIFIED | PARTIAL | PARTIAL | Player skills, GanCuju belts, internship/project concepts |
| Achievements | PARTIAL | PARTIAL | PARTIAL | PARTIAL | Badge framework; program criteria unverified |
| Calendar | PARTIAL | PARTIAL | PARTIAL | PARTIAL | Shared events/sessions; filters untested |
| Instructor interaction | PARTIAL | PARTIAL | PARTIAL | PARTIAL | Assignments/messages/evaluation; scope gaps |
| Assessment | CONFLICT | CONFLICT | CONFLICT | CONFLICT | Quiz/attendance/provenance failures |
| Completion | CONFLICT | CONFLICT | CONFLICT | CONFLICT | No verified canonical completion chain |
| Licenses/entitlements | CONFLICT | CONFLICT | CONFLICT | CONFLICT | Unlock/renewal and old model conflicts |
| Certificate download | MOCK | MOCK | MOCK | MOCK | Shared placeholder renderer; per-program applicability unverified |
| Learner history | PARTIAL | PARTIAL | PARTIAL | PARTIAL | Attempts/attendance/license/XP records; incomplete provenance |
| Outcome audit | PARTIAL | PARTIAL | PARTIAL | PARTIAL | Request logs and partial history F20 |
| Native program entry | PARTIAL | MISSING | MISSING | MISSING | MainHubView nil actions |
| Native lesson/assessment runner | MISSING | MISSING | MISSING | MISSING | EducationView is status/catalog/skills, no complete runner |

## D. Global vs Specialization Domain Map

| Boundary | Canonical responsibility | Current evidence / conflict |
| --- | --- | --- |
| Global identity | Invitation/registration, authentication/recovery, profile, consent/settings | User/auth/native AuthManager; logging/revocation defects, recovery missing |
| Global hub | Four-product discovery, entitlements and navigation | Hub/MainHubView; three native destinations missing |
| Global credits | One user balance/ledger; invoice/coupon acquisition; contextual spends/refunds | User is correct owner; license wallet/XOR ledger conflicts F04–F06/F17 |
| Global personal data | DOB/contact/medical/emergency, account photos/preferences | Shared models; exposure and lifecycle need review |
| Global communications | Notifications, messages, account settings | Program/session is a filter; delivery and complete authority unverified |
| Program entitlement | User+canonical program, validity, onboarding/eligibility, renewal/history | UserLicense/config survive; season enum must not create product entitlements |
| Program education | Program→track/curriculum→module→lesson/component→versioned assessment | Old/current hierarchy conflict; AL title prefixes insufficient |
| Program participation | Cohort/season/session, enrollment/booking/attendance, instructor assignment | Shared scheduling survives; central object/program policy needed |
| Program outcomes | Assessment/skills, XP source context, progression/completion/certificate | Domain rules survive; global XP summaries may project scoped source events |
| Program history | Learner+program+content/session+actor+outcome revision | Existing records partial; immutable snapshots missing |
| Shared operations | Location/campus/pitch/team/instructor availability | Resources referenced by programs, not duplicated per program |

A user can hold multiple entitlements. Current navigation specialization alone cannot represent all licensed programs; progress/me currently relies on it (F26). Global profile/credit/communication actions must not require specialization switching. Existing get_student_progress can create/commit progress on a read; initialization needs an explicit idempotent command in future consolidation.

## E. Complete User Journey Matrix

These reconstruct the requested chains. Coverage names relevant suites/probes, not complete live journey passes. All DB mutations below are source observations; none were executed against a database.

| Journey / step | Entry and services | DB mutations / authorization | Failure / missing step | Coverage |
| --- | --- | --- | --- | --- |
| Invitation→registration | API/web auth.py; RegisterView | Student user, invitation consumption, bonus; invitation eligibility | Check-then-use race F21; no proven email-verification chain | Auth units PASS; concurrent DB unverified |
| Verification→onboarding | Age verification, LFAOnboardingView, specialization validation | Own DOB/profile/license onboarding | Age rules diverge; age entry not email ownership proof | Web/config units PASS; native E2E unverified |
| Login→session | API/web login; core auth; AuthManager | Bcrypt, typed signed token, cookie/active-user checks | Password logging F01 | Auth/middleware units PASS; source defect |
| Expiry→refresh→logout | API refresh/logout, APIClient/AuthManager | Token type/expiry; no server revocation state | Replay, password-change invalidation and logout revocation absent | Unit validation PASS; revocation integration MISSING |
| Recovery/error | Web/native auth errors | Local/authenticated state | Password-reset route not found | Recovery MISSING; error units partial |
| Hub→cards→balance | Dashboard/hub, MainHubView/CreditsView | User/licenses/global balance reads |3 disabled cards; enum drift | Config/specialization units PASS |
| Locked→unlock | Native form/Bearer specialization/unlock; web select | User debit, UserLicense and transaction | Lock/funds order; web policy drift; license-owned ledger | F05/F17; concurrency unverified |
| Onboarding→dashboard/cancel | onboarding.py; native onboarding; renewal service | Profile/entitlement; cancellation refund/delete | GET mutation, fixed refund, debit cascade | F06 source; DB reversal not run |
| Program→curriculum→lesson | curriculum tracks/modules/lessons; TrackService; EducationViewModel | Progress records; route-specific dependencies (CSV) | Absent legacy schema; native runner missing | F10 schema comparison; DB journey NOT RUNNABLE |
| Session→booking→attendance | Session/booking APIs; web/API attendance | Enrollment/booking/attendance; role versus assignment guards | API allows other instructor session; relation mismatches | Attendance real-handler probe reproduced |
| Quiz→answer→result | QuizService/API | Owned attempt and option relation; result/answer commit |1/10 correct gives100%; replay/XP atomicity unverified | Quiz units PASS; real scoring probe reproduced |
| AL start→selection→answer→end | Web/API AL; services/adaptive_learning.py | Session score/status; web own/open guard; API ID-only | BOLA, NameError, post-commit KeyError; replay/module binding absent | AL units PASS;3 real-handler probes reproduced |
| Result→XP/skills→achievement | Gamification/skill/track/license/sync services | XP/history/skills/badges, multiple commit owners | Fake generic progress and missing program methods | Partial units; full outcome chain unverified |
| Completion→certificate/history | Track/License/CertificateService | Completion/certificate; own/admin download | Placeholder PDF; immutable basis missing | F22; download E2E not run |
| Instructor login→assigned session | Instructor dashboard; assignment/discovery/availability | Role/qualification/session assignment | Attendance API role gate insufficient; four-program scope unproved | Source + BOLA probe |
| Instructor session→evaluation→completion | Session/attendance/skill/project/competency | Student outcomes/history; admin overrides | Guard/history divergence, no complete outcome revision proof | Selected units; complete course evaluation not run |
| Admin users→programs→permissions | User/admin, specialization, instructor management | Role-gated user/license/assignment changes | Broad DTO/privacy, dual progress authority, CSRF ambiguity | Route graph; live role matrix unverified |
| Admin content→import→validation | AL admin, import/editor/quality/analytics | Validates then per-file quiz/options commit + separate log | Draft conflict, no whole-batch undo, title races/hierarchy loss | Import units +31 files PASS validation; no DB import |
| Admin sessions→outcomes→audit | Session/admin; AuditService/Middleware | Audit/history rows | Request200 not outcome truth; caller-session commit coupling | F20; durability/rollback integration MISSING |

## F. API Contract Matrix

The [full CSV](LFA_API_CONTRACT_MATRIX_2026.csv) contains **1,061 method-route rows** with source/symbol, recursive auth chain, authorization evidence, request declaration, expected-contract uncertainty, declared response/return expressions, consumer candidates, service/DB calls, declared errors, test candidates and readiness. [OpenAPI](LFA_OPENAPI_SNAPSHOT_2026.json) supplies schemas; [runtime routes](LFA_RUNTIME_ROUTES_2026.json) supply mount order. HTML/infrastructure routes are included.

**Limits:** static return expressions are not captured HTTP responses. Consumer/test literal matches are search leads, not confirmed coverage: dynamic URLs can be missed and generic symbols overmatched. Product-level expected payloads and unexercised per-route readiness remain explicitly UNVERIFIED. No route is certified PRODUCTION-READY from mounting or HTTP200. The traced contracts below have stronger evidence.

| Contract | Auth / intended authority | Expected versus observed | Consumer / error / verdict |
| --- | --- | --- | --- |
| API login/refresh/logout | Credentials then typed Bearer | Issues tokens, but no single-use refresh/revocation | Native AuthManager/APIClient; F01/F08; BLOCKED |
| specialization/unlock | Bearer, own entitlement/age | Form specialization/duration boundary coherent; transaction unsafe | UnlockConfirmView; funds checked too early F05 |
| specialization/select | Cookie user | Independent broad-enum policy without same age/consent | Hub HTML redirects legitimate web flow; F15 |
| API AL next/answer/end | User should own session | ID-only; missing QuizAnswerOption; xp_earned mismatch | Old mocked contract; exception after write F02/F09 |
| Web AL answer/end | Own open cookie session | Better guards, no authoritative issued-question/replay binding | Jinja/JS; end lock useful; F23 |
| API attendance | Assigned instructor or admin override | Role-only checks miss object/relation policy | Web separate stronger guard; probe F03 |
| Curriculum endpoints | Per-route Bearer/hybrid chains | Old SQL absent from active schema | Native status failures partly hidden; F10/F26 |
| Four program API families | Own-license/admin checks vary |24 calls to absent service methods including inherited inspection | May short-circuit404 before failing; F11 inventory |
| progress / progress/update | Authenticated | Constants and nonpersistent dict updates | Native avoids route; others unverified; F07 MOCK |
| specializations/progress/me | Current user | Uses current specialization; catches all as empty success | Cannot distinguish no progress from failure F26 |
| Quiz submit | Owned attempt; option relation | Submitted-only denominator/no duplicate-question guard | Persisted100 for1/10 F12 |
| Certificate download | Owner/admin | application/pdf over placeholder bytes | Semantic failure despite200 F22 |
| Debug/metrics/detailed health | Public dependency graph | Unconditional operational/logging surface | Edge restriction UNVERIFIED F35 |

Six duplicate method/path registrations occur below /api/v1/tournaments/{tournament_id}: GET rankings, GET preview-sessions, POST generate-sessions, GET generation-status/{task_id}, GET sessions, DELETE sessions. Registration order/OpenAPI may diverge; deduplicate mounting later, retaining correct handlers. Duplicates alone do not prove auth bypass.

Swift APIClient, Models, EducationViewModel, Auth, Unlock and Credits were compared with server declarations. Core form/Bearer transport is worth preserving. Proven issues concern missing flows, empty-on-error decoding, server refresh semantics and Release compilation, not grounds to regenerate all DTOs. Live payload decoding remains UNVERIFIED. No Education cookie/Bearer WebView bridge was found; identified WKWebView usage is bundled Skeleton POC content.

## G. DB/Data Integrity Assessment

The [ORM inventory](LFA_ORM_SCHEMA_2026.json) records columns/nullability, FKs, constraints/indexes. [Migration graph](LFA_MIGRATION_GRAPH_2026.json): **121 active revisions**, root1ec11c73ea62, head2026_06_24_1000, no missing parent links; **143 archived legacy files** outside active version directory. Static graph linkage PASS does not prove migration execution. Actual DB revision/indexes/orphans/duplicates, downgrade safety, lock behavior and drift are UNVERIFIED.

| Operation | Existing protection to preserve | Defect / uncertainty |
| --- | --- | --- |
| Credit debit | Conditional nonnegative User UPDATE, unique key, nested transaction | Debit precedes duplicate-key return F04; unique ledger alone not idempotence |
| Unlock | UserLicense uniqueness, row lock, native package checks | Pre-lock funds check, no forced refresh; divergent web rules/ledger owner |
| Renewal | Period/history service | No atomic balance guard; pricing/duration divergence; DB contention untested |
| Cancellation | Incomplete-onboarding guard, refund record | GET, fixed100 for100/250/450/800, random key, license/debit cascade F06 |
| Registration | Unique email, invitation checks | Non-atomic invitation consumption across different emails F21 |
| Enrollment | User/semester/license relations, uniqueness/eligibility | Enum/age drift; debit/enrollment concurrency unverified |
| Booking | Active-booking uniqueness/lifecycle guards | Capacity/waitlist/credit rollback needs disposable PostgreSQL tests |
| Attendance | Relations; web assignment/time/history | API missing equivalent object/relation/history policy F03/F20 |
| Quiz | Ownership, option relation, attempts/answers | Partial denominator, duplicate submissions, outcome/XP commits, no version F12/F20 |
| AL session | Score/status, web guard, completion lock | API BOLA, post-write response failure, no issued-question/program FK |
| Content import | Validator, log, per-file rollback | Partial batch commits, separate log, no title uniqueness, draft divergence |
| XP/skills/achievements | Event/history and deterministic skill components | Multiple write/commit paths; exactly-once propagation unverified |
| Progress/license | Domain histories and sync | Multiple authorities/old types; generic progress fake |
| Certificates | Identity/ownership/verification/revocation | Renderer fake; historical completion basis unversioned |
| Audit | Request log/domain history | Caller-session commit; second AL log best-effort; no full outcome snapshot |

Active schema uses tracks/modules/module_components/user_track_progresses/user_module_progresses. Old adapter uses curriculum_tracks/lessons/lesson_modules/user_module_progress/user_lesson_progress/user_learning_profiles, absent from active migrations. Replace the narrow adapter later; do not drop unknown live tables. First map rows/consumers on a sanitized restore and preserve financial/educational identifiers and history.

### Education proof

For general qualification/learner-record obligations: **BUSINESS/REGULATORY REQUIREMENT NOT DOCUMENTED**. This is separate from technical false results. A specific biometric legal/DPO checklist exists.

| Historical question | Available evidence | Conclusion |
| --- | --- | --- |
| Who/when/source? | Audit actor/request/time/status, domain timestamps | PARTIAL; request success not committed outcome proof |
| Whose record/program/session? | User/attempt/session/license FKs | PARTIAL; AL title organization and parallel models impair scope |
| Previous/current attendance/result/XP/entitlement? | Web AttendanceHistory, license/XP histories | PARTIAL; API/independent update paths diverge |
| Exact question/options/content/scoring version? | Mutable quiz/options, client-presented option log | MISSING immutable issued assessment provenance M61 |
| Authorized admin correction and reason? | Role guards/some logs | UNVERIFIED across all outcome mutations |
| Financial history after cancellation? | XOR owner ledger | CONFLICT; debit cascade prevents durable reconstruction |

### Adaptive/content chain

ALImportService._validate_file supports schemas1.0/2.0, specialization/category allowlists, difficulty, nonempty questions/explanations, correct/distractor rules. V2 requires at least2 correct variants and6 distractors. **31 current JSON files /375 questions** PASS this pure validator:23 English,8 Hungarian, all Football Player, no duplicate titles or normalized question texts in this corpus. [Validation](LFA_CONTENT_VALIDATION_2026.json), [inventory](LFA_CONTENT_INVENTORY_2026.json). Pedagogy, localization equivalence, curriculum completeness and DB import remain UNVERIFIED.

Validation does not persist a real program/topic/module hierarchy: topic/module flatten into description, modules use title prefixes. Selection/randomization/distractor helpers have passing tests, but historical presentation lacks immutable server-issued question/option/version evidence. Client-presented options are not authoritative. Preview limits20 files/256KB each, validators and editor/quality/analytics should survive. Per-file commits + import log support partial-success reporting, not whole-batch rollback. Draft status F19 must be unified before publication controls are trusted.

## H. Legacy Register

[Candidate inventory](LFA_LEGACY_CANDIDATES_2026.json) and [232-template reference inventory](LFA_TEMPLATE_REFERENCES_2026.json) support this curated reachability assessment. Of232 templates,214 have literal reference candidates and18 remain UNVERIFIED; none is proven removal-safe. Keywords/absent literal references alone do not prove non-use; dynamic includes, external clients and historical DB rows remain possible.

| Path / symbol | Purpose / referenced by | Reachable? / data dependency | Replacement / removal risk / decision |
| --- | --- | --- | --- |
| models/license.py::LicenseType | Old PLAYER/COACH names; license/progress code | Yes; historical rows possible | Four configs exist; explicit mapping/high data risk; REFACTOR |
| services/specialization/lfa_player.py | Name suggests Football, constant GanCuju | Specialization service family | Renaming alone cannot fix saved semantics; REFACTOR |
| services/specs and services/specialization | Parallel qualification/progress abstractions | Route factories/sync use both | Preserve true rules, one implementation per rule; REFACTOR |
| endpoints/curriculum | Old raw SQL hierarchy | Mounted; absent-schema dependency | Track/Module not drop-in aliases; narrow REWRITE |
| services/adaptive_learning_service.py | Old recommendation/profile SQL; same class name as current engine | Curriculum-adaptive family; old tables | Current ORM engine partial replacement; mapping needed; narrow REWRITE |
| endpoints/progression.py generic handlers | Hardcoded historical levels | Mounted; no DB writes; native avoids | Preserve real skill-profile in same file; narrow REWRITE |
| coach/gancuju/internship/lfa_player APIs | Old methods including wallets | Mounted;24 absent calls | Current services partial replacement; consumer mapping; REFACTOR |
| License wallet/XOR credit owner | Separate resource representation | Live unlock path records license owner | User wallet canonical; history migration high risk; REFACTOR |
| progress_license_sync/coupling, user_progress | Duplicate progress/license concepts | Referenced; some writes on reads | Authority not settled; REFACTOR, no deletion |
| alembic/versions_legacy |143 archived revisions | Not active path; historical DB meaning | Squashed active DAG exists; retain provenance; CLEAN |
| scripts/seed_*_curriculum.py | Old program content | Manual only; absent-schema assumptions | Player corpus partial replacement; other content valuable; CLEAN |
| Compose/test Dockerfile Streamlit | Old deployment/frontend | Documented boot recipes reachable manually | Jinja exists, recipes broken; REFACTOR |
| Four invalid setup/archive scripts F39 | Test/demo setup | No active app import established | Non-use not fully proven; CLEAN P3 |
| Native WebBridge | Bundled Skeleton POC visualization | Referenced local WKWebView | Native capture survives; device regression first; CLEAN |
| Native MultiCamera diagnostics | GoPro probes/status | Debug reachable; Release references fail | Preserve no-MPC design; narrow build CLEAN |
| tests/.archive, old audit/coverage docs | Historical validation | Not current release proof | Preserve history, label active evidence; CLEAN |
| Unreferenced-literal templates | Potential obsolete presentation | UNVERIFIED dynamic includes/names | No removal proven safe; investigate; zero DELETE |

## I. Mock/Fake/Placeholder Register

| Artifact | Reachability / effect | Classification / future action |
| --- | --- | --- |
| Generic progress GET/POST | Mounted; constants and local dict update | Production MOCK F07 P0; narrow replacement |
| CertificateService.generate_certificate_pdf | Mounted authorized PDF download returns placeholder bytes | Production MOCK F22 P0; implement renderer inside retained service |
| FakeEmbeddingProvider | Default provider; feature defaultoff, local development enabled | Test/R&D fake needs production guard F25; actual production use UNVERIFIED |
| Biometric test key flag | Explicit option can enable test key | Environment guard needed; no actual production key exposure asserted |
| MainHubView3 coming-soon cards | Visible but nil action | Honest placeholder, required native functionality MISSING F14 |
| Education/progress empty fallbacks | Failure maps to empty success | Reliability defect F26, not legitimate demo data |
| Release PRODUCTION_URL_NOT_SET | Committed release config | Endpoint configuration missing; release usability unverified |
| Seed/demo scripts and dumps | Manual/test infrastructure | Data provenance and accidental invocation risk; not executed |
| GoPro/biometric spikes/debug views | Some DEBUG guards; Release mismatch | Isolate deliberate POCs; preserve proven device integration |


## J. Security Findings

### Current official baseline

Versions were checked against official sources at audit time, **2026-09-08**. This is a scoped repository review against control families, not an ASVS/MASVS certification or complete penetration test.

| Baseline | Version / publication | Use in this audit |
| --- | --- | --- |
| [OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/) |5.0.0, May30 2025 | Authentication/session/access control, validation, data protection, logging/configuration |
| [OWASP API Security Top10](https://owasp.org/API-Security/) |2023 edition | API1 BOLA, API2 authentication, API3 property authorization, API4 resource consumption, API5 function authorization, API6 sensitive business flows, API8 configuration, API9 inventory |
| [OWASP MASVS](https://github.com/OWASP/masvs/releases/tag/v2.1.0) |2.1.0, January18 2024 | AUTH, STORAGE, NETWORK, PLATFORM, PRIVACY families |
| [OWASP MASTG](https://github.com/OWASP/mastg/releases/tag/v2.0.0) |2.0.0, June30 2026 | Mobile verification methods; device tests not performed |
| [NIST SSDF final](https://csrc.nist.gov/pubs/sp/800/218/final) |SP800-218 v1.1, February3 2022 | Secure development, review/testing, dependency vulnerability response |
| [NIST SSDF publications](https://csrc.nist.gov/projects/ssdf/publications) |v1.2 draft, December17 2025 | Not substituted for final1.1 |
| [Apple Keychain](https://support.apple.com/guide/security/secb0694df1a/web), [secure connections](https://developer.apple.com/documentation/security/preventing-insecure-network-connections) |Living platform guidance, retrieved September8 2026 | Keychain accessibility, transport/ATS and release configuration |

### Coverage and control mapping

| Security area | Observed controls / defects | Baseline mapping / remaining verification |
| --- | --- | --- |
| Identity/passwords | Bcrypt and signed typed JWT exist; password logged F01; recovery absent; invitation race F21 | ASVS auth/data protection; API2/API6; credential enumeration and full brute-force attack test UNVERIFIED |
| Session lifecycle | Expiry/type verification and native single-flight refresh useful; old refresh reusable/logout symbolic F08 | ASVS session; MASVS-AUTH/API2; live replay/revocation tests not run |
| Object/function authorization | Central role dependencies exist; real probes bypass AL ownership and instructor assignment F02/F03 | API1/API5; exhaustive all-role/all-object matrix UNVERIFIED |
| Program isolation | Four IDs exist but legacy values/active-navigation scope/AL title hierarchy drift F15/F18 | ASVS access control/API1/API3; all-program entitlement tests required |
| Unsafe credit/business flows | Existing atomic debit guard and uniqueness; replay/lock/refund gaps F04–F06/F21 | API6, ASVS business logic/concurrency; PostgreSQL contention untested |
| CSRF/cookies/redirects | HttpOnly/Secure/SameSite configurable, safe_next rejects external targets; API blanket exemption conflicts with hybrid cookie auth F34 | ASVS web/session; SameSite strict mitigates, not proof; browser exploit chain UNVERIFIED |
| XSS/template rendering | Jinja autoescape architecture; JS/HTML/markup surfaces and broad legacy templates remain | No stored/reflected/DOM XSS proof claimed; complete template/sanitizer/CSP browser testing UNVERIFIED |
| Injection | SQLAlchemy and parameter binding exist; old SQL is schema-incompatible F10 | No blanket SQL/shell injection PASS. Every dynamic query/subprocess/content boundary not executed |
| CORS/headers | Configured middleware/security headers exist | Effective deployed origin/credential/CSP policy UNVERIFIED; do not equate local defaults with production |
| Mass assignment/property access | Typed schemas/explicit assignments on traced paths; broad student/profile DTO exposure concern | API3; all response field matrices and update schemas UNVERIFIED |
| Resource exhaustion | Import size/count limits; upload/path controls; rate middleware | API4; forwarded-IP trust/process-local counters F33; edge upload quotas/timeouts UNVERIFIED |
| API inventory/configuration | All mounted methods inventoried; duplicate routes, missing methods, fake handlers, debug surface F07/F11/F35/F37 | API8/API9; runtime inventory is not readiness certification |
| Third-party consumption | Camera/model/media/worker boundaries exist; no new external provider assumed | API10 review incomplete; model artifact authenticity, remote-response limits and webhook/payment deployment UNVERIFIED |
| Mobile storage/network/platform | Keychain/APIClient native foundation; release config/ATS/debug issues | MASVS AUTH/STORAGE/NETWORK/PLATFORM/PRIVACY; device backups/logs/snapshots/deep-link adversarial tests unrun |
| Logging/audit | Request/domain logs exist; credential leak and incomplete outcome evidence F01/F20 | ASVS logging/data protection; failed-audit transaction semantics need tests |
| Supply chain | Scan matches and unreproducible env F27/F28; CI gaps F29 | SSDF review/test/vulnerability-response practices; no upgrades performed |

### Findings register

Each finding below is counted once. Related module rows/scan alerts are not additional finding counts. “Reproduced” refers to isolated real code with fake persistence; no live exploitation or real-user mutation occurred. Technical remedies below are recommendations only.

### F01 — P0 — CLOSED — Submitted passwords printed by API login

FACT: login prints the supplied password and a stored hash prefix. Mounted POST /api/v1/auth/login; no DEBUG condition. Actual historical log contents were not opened. Remove logging in a separately approved change and assess log access/retention and credential rotation exposure.

Evidence: app/api/api_v1/endpoints/auth.py:37.

### F02 — P0 — CLOSED — Adaptive session BOLA

FACT: API answer/next/end pass arbitrary session IDs; service filters by ID only. Isolated real-handler probe completed owner 999 session as actor 42. Fill-in answer also mutates foreign session. Live exploitation NOT ATTEMPTED.

Evidence: app/api/api_v1/endpoints/adaptive_learning.py:143; app/services/adaptive_learning.py:102.

### F03 — P0 — CLOSED — Instructor attendance object authorization bypass

FACT: API role gate permits every instructor; create/update do not require assignment to the session. Probe changed instructor 999 session as instructor 42. Web mark handler correctly checks session.instructor_id; reuse that policy, preserving legitimate admin override.

Evidence: app/api/api_v1/endpoints/attendance.py:27; app/api/api_v1/endpoints/attendance.py:264; app/api/web_routes/attendance.py:47.

### F04 — P0 — CLOSED — Credit deduction is not idempotent

FACT: deduct executes UPDATE before create_transaction checks existing key; ignores created=False. Probe with existing ledger key performed one debit and inserted zero ledger rows. Atomic nonnegative UPDATE is worth preserving, but ledger-plus-balance operation needs one replay-safe transaction.

Evidence: app/services/credit_service.py:142.

### F05 — P0 — CLOSED — Unlock and renewal balance concurrency is unsafe

FACT: native unlock checks funds before acquiring user lock and does not recheck funds after it; ORM identity-map refresh is not forced. Renewal reads/modifies balance without a row lock/conditional UPDATE. RISK: concurrent distinct operations can lose debits or overdraw; no PostgreSQL concurrency test was run. Existing unique license constraint protects duplicate identity only.

Evidence: app/api/web_routes/specialization.py:39; app/services/license_renewal_service.py:70.

### F06 — P0 — CLOSED — Cancellation refunds wrong amount and erases debit evidence

FACT: GET onboarding-cancel refunds fixed 100, creates random-key ledger record, deletes incomplete license, and commits without a row lock. Unlock packages cost 100/250/450/800; deletion cascades license-linked unlock debit. Financial history loss and refund mismatch are deterministic from code; concurrent double-refund risk UNVERIFIED.

Evidence: app/api/web_routes/onboarding.py:389; app/models/credit_transaction.py:49.

### F07 — P0 — CLOSED — Production progression API returns invented success

FACT: GET /progress returns constant junior/pre_assistant/bamboo for any account; POST /progress/update mutates only a local dict and reports success. Mounted with no environment gate. Native explicitly avoids it; external consumer use UNVERIFIED. Narrow endpoint replacement justified; preserve real skill-profile endpoint in same file.

Evidence: app/api/api_v1/endpoints/progression.py:165.

### F08 — P1 — Refresh replay and logout/password-change revocation absent

FACT: signed token type/expiry checks exist, but no jti/family/version/revocation lookup. Refresh issues another refresh token without invalidating old one; logout is symbolic. Password change does not invalidate issued tokens. Native comments claim rotation but server does not enforce single use.

Evidence: app/core/auth.py:21; app/api/api_v1/endpoints/auth.py:160; app/api/api_v1/endpoints/auth.py:205.

### F09 — P1 — Adaptive API contract fails independently of DB availability

FACT: QuizAnswerOption is not imported, causing NameError on selected multiple-choice answer. Fill-in branch commits real service then reads nonexistent result[xp_earned], causing KeyError. Both reproduced with fake persistence; latter had one commit. Service returns score_delta/score. API last changed e7b421c8 (2026-03-03), service 80fd38a9 (2026-05-21).

Evidence: app/api/api_v1/endpoints/adaptive_learning.py:169; app/api/api_v1/endpoints/adaptive_learning.py:203; app/services/adaptive_learning.py:134.

### F10 — P1 — Curriculum API targets schema absent from active migration chain

FACT: raw SQL uses curriculum_tracks, lessons, lesson_modules, user_lesson_progress, user_module_progress; active baseline/ORM instead contain tracks/modules/module_components and plural progress tables. Mounted routes are broken against the repository-migrated schema. Legacy DB may contain extra tables: UNVERIFIED. Merely renaming tables would change hierarchy semantics; rewrite this narrow adapter after mapping retained content.

Evidence: app/api/api_v1/endpoints/curriculum/tracks.py:14; app/api/api_v1/endpoints/curriculum/modules.py:56; app/models/track.py:9.

### F11 — P1 — Specialization APIs call missing service methods

FACT: runtime class inspection found 24 call sites across mounted player/coach/GanCuju/internship handlers, including purchase_credits, create_license, promote_level, add_xp. Earlier guards may short-circuit some requests. An endpoint with a service import is not a working feature. Fix contracts incrementally; do not revive separate-wallet tables merely to satisfy stale routes.

Evidence: docs/audit/LFA_MISSING_SERVICE_METHODS_2026.json.

### F12 — P1 — Quiz can pass with only one submitted correct answer

FACT: denominator sums submitted valid questions rather than all quiz questions; omitted answers are not counted. Probe submitted 1 of 10, persisted score=100 and passed=True. Duplicate submitted question IDs are not rejected in service; races/XP repetition also need DB verification. This is outcome-integrity failure, not an established legal violation.

Evidence: app/services/quiz_service.py:165; app/schemas/quiz.py.

### F13 — P1 — iOS Release build fails while Debug builds

FACT: unsigned Release failed on Xcode 27.0/27A5194q: unguarded debug view references DEBUG-only GoProStreamProbe and diagnostic types. Debug build passed on same toolchain. First sandbox macro failure discarded as environment artifact; reported failure is from approved unrestricted rerun.

Evidence: ios/LFAEducationCenter/MultiCamera/GoProConnectionDebugView.swift:6; ios/LFAEducationCenter/MultiCamera/GoProStreamProbe.swift:22.

### F14 — P1 — Three programs unavailable in native navigation

FACT: GanCuju, Coach, Internship cards hardcode comingSoon with nil action; only Football Player enters scoped tabs. Native EducationView is status/skills/catalog, not a lesson or assessment runner. BUILD remaining flows gradually, preserving working native foundation.

Evidence: ios/LFAEducationCenter/App/MainHubView.swift:62; ios/LFAEducationCenter/App/LFASpecTabView.swift:15.

### F15 — P1 — Conflicting license and specialization representations

FACT: canonical four IDs coexist with PLAYER/COACH metadata, service named LFA_PLAYER maps to GANCUJU_PLAYER, progress/license sync models, and a shared enum containing season-only types. Web selection accepts any SpecializationType enum, does not restrict to four licenses or enforce age/consent; unlike native unlock. Canonicalize boundaries after data mapping; preserve historical rows.

Evidence: app/models/specialization.py:8; app/models/license.py:14; app/services/specialization/lfa_player.py:19; app/api/web_routes/onboarding.py:76.

### F16 — P1 — Age and season rules disagree in executable code

FACT: hub/config describe PRE 5-13/YOUTH 14-18; reachable spec factory uses PRE 6-11/YOUTH 12-18 and current age. Model documentation requires July-1 season lock. Do not choose one from comments alone; reconcile requirements, boundary tests and existing enrollments.

Evidence: app/utils/age_requirements.py:43; app/services/specs/session_based/lfa_player_service.py:54; app/services/specialization_validation.py.

### F17 — P1 — Global credit resource still represented as license wallets

FACT: User holds hub balance; license also has credit_balance/purchased/expiry and ledger XOR owner model. Unlock deducts User but records user_license_id. Global transaction-history paths and cascade retention cannot reliably represent one user wallet. User instruction settles global ownership; retain specialization as context only.

Evidence: app/models/user.py; app/models/license.py:244; app/models/credit_transaction.py:39; app/api/api_v1/endpoints/lfa_player/credits.py.

### F18 — P1 — Adaptive content hierarchy and program assignment not persisted

FACT: import validates specialization/category but accepts optional topic/module only as description metadata; Quiz/ALSession lack canonical program FK and module hierarchy. Web module selection relies on title/module_prefix conventions. Category/language filters cannot prove program entitlement. Schema/content versions are not immutable assessment provenance.

Evidence: app/services/al_import_service.py:294; app/models/quiz.py:53; app/api/web_routes/adaptive_learning.py:200.

### F19 — P1 — Import-as-draft can be served as published

FACT: import_as_draft only changes is_active=False; content_status defaults PUBLISHED and candidate query filters content_status, not is_active. No model hook synchronizes them. Database insert execution UNVERIFIED; constructor/default/query inconsistency proven. Draft content isolation needs a single lifecycle field/policy.

Evidence: app/services/al_import_service.py:503; app/services/al_import_service.py:305; app/models/quiz.py:67; app/services/adaptive_learning.py:288.

### F20 — P1 — Learner outcome audit evidence is incomplete

FACT: request audit records method/path/status, not prior/new outcome values or immutable question text/version. AL log is a second commit with swallowed failure, and presented options are client-provided. API attendance updates omit AttendanceHistory although web has it. AuditService commits caller session. Technical proof gap; education regulatory standard is not documented.

Evidence: app/services/audit_service.py:28; app/middleware/audit_middleware.py:139; app/api/web_routes/adaptive_learning.py:586; app/models/quiz.py:270.

### F21 — P1 — Invitation consumption is check-then-write

FACT: both registration paths check unused invitation then create user/bonus and mark used without locking invitation or conditional consumption. Unique user email does not protect concurrent different emails using unrestricted invitation. RISK: multiple bonuses/accounts for one-use invitation; concurrent DB behavior not tested.

Evidence: app/api/api_v1/endpoints/auth.py:270; app/api/web_routes/auth.py:346; app/models/invitation_code.py.

### F22 — P0 — CLOSED — Certificate download advertises PDF but returns placeholder bytes

FACT: authenticated authorized download returns b'PDF certificate content placeholder' as application/pdf. Mounted and not feature-gated. Existing ownership/verification/revocation domain should remain; real rendering is missing. Narrow renderer replacement, not certificate-system rewrite.

Evidence: app/services/certificate_service.py:242; app/api/api_v1/endpoints/certificates.py:62.

### F23 — P1 — Adaptive web scoring trusts unbound question submissions

FACT: web verifies own open session and option-to-question relation, but does not persist/validate a server-issued question attempt, membership in selected module, or one answer per presentation. Client supplies presented_option_ids; service increments each call. RISK: repeated known answers can inflate session score/XP. Separate completed-session lock is useful but insufficient.

Evidence: app/api/web_routes/adaptive_learning.py:504; app/services/adaptive_learning.py:102.

### F24 — P1 — Verified curriculum/assessment coverage missing for three domains

FACT: 31 content JSON files/375 questions all target LFA_FOOTBALL_PLAYER; other programs only have config/legacy seed material, including COACH/PLAYER IDs and absent schema. Product completion for those programs cannot be claimed. Curriculum subject-matter correctness remains UNVERIFIED for all four.

Evidence: content/adaptive_learning; scripts/seed_coach_curriculum.py; scripts/seed_internship_curriculum.py; scripts/seed_player_curriculum.py.

### F25 — P1 — Biometric mock provider and production guard mismatch

FACT: default provider=fake; provider choice/test-key flag are not rejected specifically in production Settings validation. Face matching default is off, but local .env enables it in development. Production activation is UNVERIFIED, not alleged. Prevent fake identity outcomes on deployment; existing legal/DPO gate remains unresolved.

Evidence: app/config.py:311; app/services/biometric/embedding_service.py:104; app/services/biometric/encryption_service.py:57; docs/biometric/PRODUCTION_ACTIVATION_CHECKLIST.md.

### F26 — P2 — Native and API failure states hide lost education data

FACT: native maps HTTP/decoding failure to empty loaded state; backend catches all progress exceptions and returns success with empty data. Learner cannot distinguish no progress from broken service/schema. Preserve native views, expose typed empty/error states.

Evidence: ios/LFAEducationCenter/Education/EducationViewModel.swift:79; app/api/api_v1/endpoints/specializations/progress.py:34.

### F27 — P1 — Known vulnerable declared/runtime dependencies

FACT: pip-audit matched 19 unique package/advisory IDs in 5 of 24 exact direct pins; installed environment has additional matches. npm lock has 18 vulnerable packages (10 high). Exploitability/production versions UNVERIFIED; multipart/Jinja/auth libraries are mounted attack surfaces. No package upgraded.

Evidence: docs/audit/LFA_SECURITY_SCANS_2026.json; requirements.txt.

### F28 — P2 — Python environment is not reproducible from declared dependencies

FACT: no hash-locked Python transitive manifest; Pillow/OpenCV lower bounds; local 199 distributions differ from pins; pip check fails cryptography/cffi requirement. Separate Python test libraries live in runtime requirements; browser/ffmpeg/model provisioning not one reproducible release manifest.

Evidence: requirements.txt; requirements-test.txt; docs/audit/LFA_INSTALLED_PACKAGES_2026.json.

### F29 — P2 — CI evidence can conceal build errors and misses Release

FACT: iOS build pipeline ends || true; later test rerun may still fail, so whole job is not automatically green. Only Debug tested, missing this proven Release failure. Actions use mutable major tags; many workflows lack explicit least-privilege permissions. No pull_request_target trigger found; effective repository permissions UNVERIFIED.

Evidence: .github/workflows/ios-ci.yml:149; .github/workflows.

### F30 — P1 — Documented container boot recipes reference removed files

FACT: main compose build context expects root Dockerfile/target dev but no root Dockerfile exists; test Dockerfile copies absent streamlit_requirements.txt. Decommissioned Streamlit references survive. Do not run these recipes or assume deployed image matches; deployment is not reproducible from this checkout.

Evidence: docker-compose.yml:60; docker/Dockerfile.test:39; docker-compose.test.yml.

### F31 — P2 — Startup and worker readiness have incomplete guarantees

FACT: lifespan creates initial admin then starts in-process scheduler; failures log and continue. Multi-process scheduler duplication and durable job dispatch/restore need verification. Readiness checks DB, worker health can be degraded; feature availability should not be inferred from /health=200. No running environment tested.

Evidence: app/main.py:42; app/background/scheduler.py; app/core/health.py.

### F32 — P2 — Privacy lifecycle beyond biometrics is undocumented/unverified

FACT: profile/medical/emergency/DOB/photos/device and motion data exist; generic account-wide export/deletion/retention workflow not found in route inventory. Biometric/video partial deletion controls exist; backup erasure and derived-data retention UNVERIFIED. Purpose/retention owner decisions needed; no invented regulatory finding.

Evidence: app/models/user.py; app/models/juggling.py; app/models/user_mood_photos.py; docs/biometric/DPIA_TEMPLATE.md.

### F33 — P2 — Rate-limit identity trusts arbitrary forwarding header

FACT: X-Forwarded-For first value accepted without trusted-proxy boundary, counters in process dictionaries. RISK: bypass if edge does not strip spoofed header; multiworker limits multiply. Local .env disables limiting; production edge configuration UNVERIFIED. Preserve rate-limit policy while verify/enforce deployment boundary.

Evidence: app/middleware/security.py:110.

### F34 — P1 — CSRF exemption contradicts hybrid cookie APIs

FACT: all /api/v1/* writes exempt; some admin endpoints accept cookie via hybrid dependencies, and any Bearer-looking header bypasses middleware before validation. SameSite strict helps but does not establish route-level protection, including same-site origins. Browser exploit chain UNVERIFIED; blanket API exemption is false architectural assumption.

Evidence: app/middleware/csrf_middleware.py:67; app/dependencies.py:194.

### F35 — P2 — Public debug/operational surface and exception leakage

FACT: debug router mounted unconditionally; frontend log endpoint unauthenticated prints arbitrary body; /health/detailed and /metrics public. Several handlers return str(exception) in 500 detail. Requirement for public metrics absent; exposure/rate limits at edge UNVERIFIED.

Evidence: app/api/api_v1/endpoints/debug.py; app/main.py:199; app/api/api_v1/endpoints/lfa_player/credits.py:117.

### F36 — P2 — Tests can pass obsolete mock contracts

FACT: mocked AL service supplies xp_earned that real service omits, and successful multiple-choice test omits selected option. 630 selected tests passed despite isolated defects. Many tests under unit require live DB, and pytest excludes archived/security-XSS directories. Test count/old coverage.xml cannot establish release quality.

Evidence: tests/unit/services/test_adaptive_learning_endpoint.py:227; pytest.ini; tests/unit/conftest.py:39.

### F37 — P2 — Large route modules and repeated business rules

FACT: 1990/2452/1963-line web files; 24 missing service call sites; 6 duplicate mounted method/path registrations. Unlock/onboarding/assessment logic is duplicated across web/API. Consolidate only evidenced business rules; avoid speculative service layers.

Evidence: app/api/web_routes/dashboard.py; app/api/web_routes/virtual_training.py; app/api/web_routes/vt_challenges.py; app/api/api_v1/api.py.

### F38 — P3 — Historical documents overstate current completion

FACT: historical all-four-complete claims disagree with present missing methods/content; biometric checklist still leaves implemented code phases unticked. Mark dated evidence, keep provenance, replace stale navigation/status claims after owner review.

Evidence: README.md; docs/implementation/implementation/MASTER_PROGRESS.md; docs/SECURITY_AUDIT_REPORT.md; docs/biometric/PRODUCTION_ACTIVATION_CHECKLIST.md.

### F39 — P3 — Archived/test setup scripts have syntax defects

FACT: AST parse failed on four tracked Python files; all active app Python parsed. No runtime reachability demonstrated; cleanup priority only. Do not fix/delete during audit.

Evidence: scripts/create_test_data.py:17; scripts/setup_tournament_enrollment_test.py:22; scripts/test_instructor_tournament_ui.py:19; tests/.archive/test_admin_create_tournament.py:18.

### F40 — P2 — Stored fixtures and local secrets lack demonstrated lifecycle evidence

FACT: three tracked binary DB dumps, ignored local .env/private TLS key, and logs exist. Pattern scan of 3754 text files found zero supported secret patterns; two historical key/env blobs scanned. Dump data provenance, password-literal classification, all-history entropy scan, secret validity/rotation UNVERIFIED. No actual secret values inspected in report or disclosed; no confirmed committed live credential claim.

Evidence: tests/e2e/snapshots; docs/audit/LFA_SECRETS_SCAN_REDACTED_2026.json.


### Secrets review boundary

[Redacted scan](LFA_SECRETS_SCAN_REDACTED_2026.json):3,754 tracked text files checked for supported high-confidence patterns (private PEM, GitHub/AWS/Stripe-live tokens and JWT literals), zero matches. Binary/large-file exclusions are recorded. All local history object names were enumerated; only two suspicious key/env filename blobs were content-scanned, zero matches. This is **not a full-history entropy scan**, not exhaustive password-literal classification, and not a validity/rotation check.

Ignored local .env and a private TLS key exist; no secret values are in this report. Three tracked binary DB dumps remain unclassified for personal data provenance. Local development flags and private files do not establish production exposure. Conversely, zero regex hits does not negate the proven password-logging source defect. No credentials were tested, rotated or disclosed.

## K. Privacy/Data Map

| Data class | Collection/storage evidence | Access/purpose evidence | Retention/deletion/minimization assessment |
| --- | --- | --- | --- |
| Account identity/contact | app/models/user.py; email/name/phone/address/nationality/DOB | Auth/profile/admin/instructor user APIs | Global account-wide export/deletion workflow not found; role DTO minimization incomplete |
| Medical/emergency information | User fields and profile/onboarding | Training safety/contact rationale plausible, not a complete documented purpose policy | Retention, field-level access and lawful basis UNVERIFIED; avoid copying into broad student lists |
| Profile/academy/mood/card photos | User/profile, user_mood_photos, card and upload services | Own profile/card creation and some public presentation | Multiple derivatives/duplicates; consent/publication/backup deletion chain UNVERIFIED |
| Measurements/self-rating/skills | Football skill assessments, onboarding and progression models | Education/training feedback | Historical correction/audit and per-program instructor access partial |
| Video/audio | Juggling model/tasks/media service, native capture/upload | Training/annotation/playback; app permission descriptions | Retention task exists, default cleanup off/dry-run on; actual execution, backup/derivative erasure unverified |
| Skeleton/Vision/annotations | Native Skeleton/Juggling/MultiCamera; backend motion/video records | Device capture, coordinates/trajectory/assessment | Local annotation files and derived-data purpose/retention need explicit policy |
| Biometric embedding/liveness | app/models/biometric.py, biometric services/tasks | Disclosure/consent/adult checks, restricted admin review, encrypted embeddings | AES-GCM/metadata sanitizer good; fake provider/activation gate; delayed deletion controls partial |
| Device/session/location telemetry | Device identifiers, multicamera session/cycle/network metadata and device logs | Camera orchestration/synchronization | GPS/device log content and backup/snapshot exposure UNVERIFIED; minimize retained diagnostics |
| Behavioral/education history | Quiz/AL attempts, XP, attendance, licenses, projects | Learner history/admin analysis | Immutable assessment version missing; outcome retention requirement not documented |
| Financial-like credits/invoices | User/license/transaction/invoice/coupon | Global credit accounting/acquisition | Cancellation can destroy debit evidence; durable retention/reconciliation needed |
| Operational/audit logs | Structured/request logs, public frontend log route | Debugging/audit | Password leak F01; arbitrary log body and retention/access policy unverified |
| Fixtures/backups |3 tracked tests/e2e/snapshots/*.dump | Test restoration intent | Whether synthetic/anonymized and safe for repository history UNVERIFIED; not restored or inspected as user data |

General account/data retention requirements: **BUSINESS/REGULATORY REQUIREMENT NOT DOCUMENTED**. No claim of legal compliance or violation is made without a defined requirement. The biometric DPIA/checklist contains a real unresolved approval process; this audit does not replace it.

Biometrics have meaningful controls: encrypted embeddings, minimized metadata, consent/disclosure, adult checks, restricted review/self-review protections and deletion mechanisms. These do not prove accuracy or permission to activate. Feature matching defaults off, provider defaults fake, and local development enables matching; production use is UNVERIFIED. User/global data deletion, backups and all derivatives need a coordinated lifecycle; video-specific deletion is not an account-wide policy.

## L. Native/Web Architecture Assessment

**Preserve native-first gradual migration.** No evidence supports replacing AuthManager/APIClient/Keychain or native capture with WebViews. Classification below describes intended screen ownership, not production PASS. Shared subviews inherit their feature’s ownership. Actual entry/screen files are recorded in the [native view inventory](LFA_NATIVE_SCREEN_INVENTORY_2026.json); [build evidence](LFA_NATIVE_BUILD_EVIDENCE_2026.json) preserves exact commands/compiler errors.

| Screen family / paths under ios/LFAEducationCenter unless stated | Classification | Functional evidence / action |
| --- | --- | --- |
| Auth/LoginView, RegisterView, AcademyID views; App/RootView/SplashView | NATIVE-CANONICAL | Real SwiftUI auth/navigation; server revocation and Keychain failure handling need repair |
| App/MainHubView, LFASpecTabView, WelcomeSuccessView | NATIVE-CANONICAL | Global hub/scoped Football tabs survive;3 program destinations missing |
| Credits/CreditsView, InvoiceRequestView, RedeemCodeView | NATIVE-CANONICAL | Global wallet acquisition UI; fix backend integrity without scoping wallet |
| Unlock/UnlockConfirmView, Onboarding/LFAOnboardingView/PitchSelectorView | NATIVE-CANONICAL | Real native flow; server age/funds/policy divergence remains |
| Profile/ProfileView/photo/mood/baseline/celebration | NATIVE-CANONICAL | Preserve capture/profile functionality; data lifecycle and failures unverified |
| Dashboard/DashboardView, Education/EducationView | NATIVE-CANONICAL | Status/skills/catalog partial; data errors hidden; no complete lesson/assessment runner |
| Training/BallTrainingHubView and training/progress views | NATIVE-CANONICAL | Native training/device integration; real-device regressions unrun |
| Juggling video list/player/upload/annotation/overlays | NATIVE-CANONICAL | Native media and annotation substantial; local data/backup/deletion tests needed |
| MultiCamera lobby/instructor/player capture, QR/status | NATIVE-CANONICAL | Current polling/cycle/session no-MPC design preserved; devices untested |
| MultiCamera/GoProConnectionDebugView | BROKEN | Unguarded Release references DEBUG-only types F13 |
| Biometric disclosure/liveness/verification and spike views | NATIVE-CANONICAL | Native implementation, R&D/approval boundary; classification not production activation |
| WebBridge/SkeletonBridge.swift and bundled skeleton_receiver.html | TEMPORARY-WEBVIEW | Local POC visualization compatibility only; no remote Education fallback discovered |
| Web admin/import/editor/permissions and complex instructor/course tools | WEB-CANONICAL | Existing Jinja routes retained until contracts and native equivalents proven |
| Web/native auth/profile/credits presentation | DUPLICATED | Two legitimate clients; share server business rules, not necessarily presentation |
| Coach/GanCuju/Internship native destinations | NOT-IMPLEMENTED | Disabled comingSoon cards/nil action |
| Full native lessons/quizzes/exams/completion runner | NOT-IMPLEMENTED | Catalog/status views do not implement full education execution |

AuthManager uses a main-actor/single-flight refresh mechanism; APIClient centralizes requests/forms/uploads. Tokens are stored in Keychain with AfterFirstUnlock accessibility; save success is not consistently acted on. Cached IDs/preferences use UserDefaults. Accessibility class should be chosen against actual background/device needs, not changed speculatively. Backup, clipboard, background snapshot, screenshot and local log protections were not dynamically tested; absence of a found pasteboard path is not PASS.

APIConfig accepts HTTP schemes; Release config contains PRODUCTION_URL_NOT_SET. Info.plist includes NSAllowsArbitraryLoads and NSAllowsLocalNetworking. Apple’s OS-specific key precedence means this combination does **not** justify claiming universally disabled ATS; effective Release transport on supported OS versions remains UNVERIFIED. Local camera networking may require deliberately limited exceptions; public API traffic should have an explicit release HTTPS contract.

Debug MC1 deep-link handler is guarded by DEBUG, although URL scheme declaration remains. Camera/microphone/local-network usage and ARKit capabilities are present. No general token-to-web-cookie bridge or remote Education WKWebView was established.

Unsigned build evidence on **Xcode27.0, build27A5194q (beta)**: Debug PASS, Release FAIL because GoProConnectionDebugView references DEBUG-only GoProStreamProbe/GoProStreamDiagWriter/GoProCameraStateProbe/GoProCameraStateDiagWriter. An earlier sandbox macro error was discarded and rerun with approved build access. No signing, simulator interaction, native test suite, App Store validation or device run occurred. Toolchain portability to the intended stable release Xcode is UNVERIFIED.

## M. Test Evidence Matrix

[Per-case evidence](LFA_TEST_EVIDENCE_2026.json), [isolated probes](LFA_ISOLATED_PROBES_2026.json), [content validation](LFA_CONTENT_VALIDATION_2026.json), [missing-method inspection](LFA_MISSING_SERVICE_METHODS_2026.json). Audit harnesses deny real connections, disable dotenv/settings files and skip selected DB fixtures; pytest plugin autoload disabled, asyncio/mock explicitly enabled. Existing source tests were not edited.

| Type / behavior | Status | Evidence / limits |
| --- | --- | --- |
| Core service/auth/middleware unit selection,15 files | PASS630; SKIPPED17 |647 collected;0 failures/errors; credits/spec configs/license/AL/quiz/auth/middleware; DB fixtures deliberately skipped |
| Additional biometric/media/Kalman/web unit selection,8 files | PASS138 |0 failures/errors/skips; encryption/sanitizer/flags/path/math/web auth/spec/AL |
| Actual content schema validation | PASS31 files | Existing pure validator,375 questions; not DB import/pedagogy proof |
| Isolated real-handler/service regression probes |6 defects REPRODUCED | Foreign AL completion, AL NameError, foreign AL write then KeyError, credit replay, attendance assignment bypass,1/10 quiz100% |
| Python static syntax | PASS2233; FAIL4 | All active app sources parse;4 support/archive syntax errors F39 |
| Runtime route/OpenAPI/ORM construction | PASS under isolation | Lifespan and DB startup not run; duplicate registration warnings remain |
| DB/integration/concurrency | NOT RUNNABLE in audit isolation | No authorized disposable DB; refused connections cannot be reported as product assertion failures |
| API HTTP contract/security | PARTIAL / NOT RUN | Dependency graph + real function probes; no live HTTP all-role suite or penetration test |
| Migration/restore | NOT RUNNABLE | DAG checked only; no upgrade/downgrade/restore executed |
| Web E2E/Cypress | NOT RUNNABLE | No isolated server/database; old screenshots/coverage not current proof |
| iOS unsigned Debug | PASS | Same checkout/toolchain as Release |
| iOS unsigned Release | FAIL | Source configuration defect F13 |
| Native XCTest/UI/device/Skeleton/GoPro | NOT RUN | Tests exist but no native/device execution; reproducible build not functional proof |
| Complete four-program regression | MISSING current passing evidence | Missing journeys/content/contracts prevent certification |
| Flaky tests | UNVERIFIED | No repetition sufficient to classify FLAKY; do not relabel failures as flakes |
| Account recovery/revocation, assessment versioning | MISSING behavior/coverage | Build requirements M60/M61; tests alone cannot replace implementation |

Initial core harness execution had4 missing-mocker errors plus17 deliberately blocked DB tests; explicit plugin setup/fixture skipping yielded final630/17 above. An exploratory additional selection included DB-dependent ball-annotation/multicamera tests (despite unit directory):126 setup errors and12 call failures all encountered the connection-denial guard, with138 other passes. Removing those DB files yielded final138 PASS. Those blocked cases are **NOT RUNNABLE**, not126+12 confirmed application defects. No connection was allowed to complete.

Test quality defect F36: tests/unit/services/test_adaptive_learning_endpoint.py mocks xp_earned, which the real service omits, and a multiple-choice “success” path omits the selected option that triggers NameError. Thus high test counts coexist with mounted broken behavior. pytest collection excludes archive/security-XSS areas; old coverage.xml and historical green CI are not release evidence. DB fixtures in unit directories undermine safety/reproducibility. Future meaningful tests must exercise real service-to-handler contracts, object denial, fixed transaction keys, complete quiz denominators and actual Release configuration.

## N. Dependency/Supply-Chain Findings

[Sanitized scan results](LFA_SECURITY_SCANS_2026.json) preserve package/advisory IDs, aliases and scanner-reported fixed versions; [installed environment inventory](LFA_INSTALLED_PACKAGES_2026.json) records local distributions. Matches require exploitability triage; fixed-version metadata is not an upgrade plan. No repository requirements, lockfile or installed application dependency was changed. Scan-only pip-audit2.10.1/Bandit1.9.4 were installed separately under /tmp.

| Ecosystem | Manifest / direct inventory | Findings |
| --- | --- | --- |
| Python runtime/test | requirements.txt, requirements-test.txt; exact24 direct pins captured in scans |5 pinned packages matched19 unique package/advisory IDs; aliases can overlap, so not19 proven distinct exploits |
| Local Python environment |199 installed distributions |24 packages matched108 package/advisory IDs; includes local/test extras, not proof all deployed |
| JavaScript/Cypress | cypress/package.json + package-lock.json | npm audit:18 vulnerable packages,10 high/6 moderate/2 low/0 critical;348 total dependency metadata count |
| Swift/Apple | Xcode project, Swift source and Apple frameworks | No independently established third-party SPM/CocoaPods release lock; toolchain and Release build issues remain |
| System/media/ML/browser tools | ffmpeg/ffprobe, model runtimes/artifacts, Playwright browser provisioning | Not fully represented by Python pins; versions/artifact provenance/reproduction UNVERIFIED |

| Vulnerable exact direct pin | Unique scanner IDs | Exposure qualification |
| --- | ---: | --- |
| python-jose3.3.0 |3 | JWT/auth boundary; exploit applicability depends algorithms/usage |
| python-multipart0.0.6 |9 | Mounted form/upload parsing; some advisory aliases duplicate underlying issue |
| python-dotenv1.0.0 |1 | Environment-loading dependency; production execution path/config matters |
| pytest7.4.3 |1 | Test/tooling, not automatically production-facing |
| Jinja2 3.1.2 |5 | Template rendering boundary; specific exploit preconditions require triage |

Bandit reported **119 alerts:112 low,5 medium,2 high**. These are not119 confirmed findings. The two high B324 MD5 uses in users/credits.py and semester_enrollment.py concern deterministic identifiers/idempotency-style hashing, not observed password hashing; triage the security role and collision implications before prioritizing. Exact sanitized scanner locations are retained; source snippets/secret values are omitted.

pip check **FAIL**: installed cryptography46.0.5 requires cffi>=2, installed cffi1.17.1. No hash-locked Python transitive manifest; Pillow/OpenCV lower bounds and local versions diverge from declared pins. Runtime and test tools are mixed in requirements. Clean-install resolution was not executed. A package being old does not prove abandonment; abandonment and unnecessary-dependency removal are UNVERIFIED without usage/upstream evidence. Keep needed media/crypto libraries until behavior/usage is traced.

CI observations: .github/workflows/ios-ci.yml masks an intermediate build with “|| true”; later test invocation can still fail, so this is not proof the whole job always passes. Debug-only CI misses the Release defect. Actions commonly use mutable version tags; many workflows omit explicit least-privilege permissions; actual repository default permissions are UNVERIFIED. No pull_request_target trigger was found. No malicious script or leaked live CI secret was established. Future release evidence needs explicit failure propagation, pinned/provenanced inputs and separated test/runtime environments.

## O. Operational/Deployment Findings

| Area | Observation | Operational implication |
| --- | --- | --- |
| Boot recipe | Root docker-compose expects missing root Dockerfile/dev target; test Dockerfile copies missing streamlit_requirements.txt | Clean container startup not reproducible F30; no containers started |
| Startup | app/main.py lifespan creates initial admin, checks references, starts scheduler; some failures warn/continue | Import success is not startup proof; bootstrap writes deliberately not executed |
| Jobs | In-process scheduler plus Redis/Celery tasks | Multi-process ownership, durable dispatch/retries/idempotence and queue backlog unverified |
| Health/readiness | DB-aware health plus metrics/detailed endpoints | health200 not every feature ready; public operational surface needs deployment policy |
| Logs/errors | Structured/request logging plus prints and broad catches | Password disclosure, raw exception detail and empty successes F01/F26/F35 |
| Environment identity | Ignored local .env: development/local DB, cookie securefalse, rate limitingfalse, biometric matchingtrue | Local facts only; production config not inferred; fake-provider guard still code issue |
| Media/storage | Local paths, workers and derivatives | Multi-host durability/retention/quotas and reconciliation not established |
| Backups/restore | Snapshot/runbook artifacts exist | No restore drill or production backup verification; destructive scripts not run |
| Migrations | Static DAG coherent; old archive retained | Applied state and backwards/forwards data compatibility UNVERIFIED |
| Native release | Placeholder URL and failed Release compile | No signed distributable proven; Debug build not release approval |
| Observability | Broad exceptions can return successful empty progress | A learner/admin cannot distinguish no data from failed service; explicit error states needed |

No production secret/settings, worker scheduling, hosting or deployment configuration changed. Recovery should begin by making an isolated environment reproducible and obtaining a sanitized backup/restore validation before data repairs.

## P. Module Decision Matrix

The machine-readable control list is [LFA_MODULE_DECISIONS_2026.json](LFA_MODULE_DECISIONS_2026.json). Every row has one primary decision. Status WORKING applies only to named demonstrated behavior; confidence describes evidence/recommendation, not production certification. Paths intentionally overlap for narrow helpers versus parent modules. Tests below refer to selected evidence; untested integrations remain UNVERIFIED. Findings linked in the evidence column explain functional/security caveats.

| Area | Paths | Current purpose | Evidence | Functional status | Security status | Tests | Decision | Priority | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M01 Configuration and environment | app/config.py; .env.example | Configuration and environment | F25, F28, F31 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Enforce actual environment/test/mock boundaries, preserve settings architecture. |
| M02 Database session and connection adapter | app/database.py | Database session and connection adapter | Source inventory; selected test evidence | UNVERIFIED | No defect established within named component; integration/production UNVERIFIED | Database connection/startup/restore NOT RUNNABLE under audit isolation | KEEP | P2 | Keep pooled SQLAlchemy boundary; runtime connectivity/restore unverified, no replacement justified. |
| M03 Active schema migrations | alembic/versions; alembic/env.py | Active schema migrations | F10, F17 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | CLEAN | P2 | One connected 121-revision DAG; preserve every applied migration, verify DB drift on isolated restore. |
| M04 Historical migration archive | alembic/versions_legacy | Historical migration archive | F38 | LEGACY | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | CLEAN | P3 | 143 files outside active version path; valuable reconstruction evidence, not deletion-safe data migrations. |
| M05 API identity and password/session lifecycle | app/api/api_v1/endpoints/auth.py; app/core/auth.py; app/core/security.py | API identity and password/session lifecycle | F01, F08, F21 | BROKEN | FINDINGS | Auth unit suites PASS; token revocation integration MISSING | REFACTOR | P0 | Preserve bcrypt and signed typed JWT; remove disclosure and implement replay/revocation boundaries. |
| M06 Web registration/login/logout | app/api/web_routes/auth.py; app/templates/login.html; app/templates/register.html | Web registration/login/logout | F08, F21 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Shared account use case needed; preserve safe-next, cookie flags and student role assignment. |
| M07 Authentication and role dependencies | app/dependencies.py; app/api/deps.py | Authentication and role dependencies | F03, F34 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Keep central role gates; consolidate explicit bearer/cookie context and object policy. |
| M08 Web middleware/security/logging | app/middleware; app/core/csrf.py | Web middleware/security/logging | F01, F33, F34, F35 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Retain CSRF/cookie/header concepts; enforce actual auth mode, trusted proxy and safe logs. |
| M09 Global user/profile/admin CRUD | app/api/api_v1/endpoints/users; app/models/user.py; app/api/web_routes/profile.py | Global user/profile/admin CRUD | F17, F32 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P2 | Global profile remains global; instructor-wide student fields/purpose require privacy review. |
| M10 Global hub and web navigation | app/api/web_routes/dashboard.py; app/templates/hub_specializations.html | Global hub and web navigation | F15, F16, F37 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Keep four-card hub; split presentation from onboarding/card business policies only after contracts agree. |
| M11 Canonical program configuration loader | app/services/specialization_config_loader.py | Canonical program configuration loader | Source inventory; selected test evidence | WORKING | No defect established within named component; integration/production UNVERIFIED | tests/unit/services/test_specialization_config_loader.py — selected suite PASS | KEEP | P2 | Existing loader validation/cache passed selected unit suite; keep mechanism, reconcile config semantics separately. |
| M12 Program definitions and age policy | config/specializations; app/models/specialization.py; app/utils/age_requirements.py; app/services/specialization_validation.py | Program definitions and age policy | F15, F16 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Four IDs established; season-only IDs and age boundaries need single policy. |
| M13 Unlock/onboarding/cancellation | app/api/web_routes/onboarding.py; app/api/web_routes/specialization.py | Unlock/onboarding/cancellation | F05, F06, F15 | BROKEN | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P0 | Preserve native/web flows and package logic; one transactional entitlement command. |
| M14 Global credit ledger and balance | app/services/credit_service.py; app/models/credit_transaction.py | Global credit ledger and balance | F04, F05, F06, F17 | BROKEN | FINDINGS | Credit unit suite PASS; real deduct replay probe REPRODUCED defect | REFACTOR | P0 | One user ledger, atomic idempotent balance mutations, durable contextual references. |
| M15 Invoice/coupon/invitation/payment operations | app/api/api_v1/endpoints/invoices; app/api/api_v1/endpoints/coupons.py; app/api/api_v1/endpoints/invitation_codes.py; app/api/api_v1/endpoints/payment_verification.py | Invoice/coupon/invitation/payment operations | F21, F28 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Retain existing manual verification/payment approach; verify credit and registration transactions, do not add payment provider. |
| M16 License models/renewal/authorization | app/models/license.py; app/services/license_service.py; app/services/license_renewal_service.py; app/services/license_authorization_service.py | License models/renewal/authorization | F05, F15, F17 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P0 | Keep licenses/progression history; stop independent wallet/level drift. |
| M17 Specialization progress and sync layers | app/services/specialization; app/services/specialization_service.py; app/services/progress_license_sync_service.py; app/services/progress_license_coupling.py; app/models/user_progress.py | Specialization progress and sync layers | F15, F26 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Compatibility facades can survive temporarily; choose level authority and explicit projections, no dual writes without transaction. |
| M18 Football Player service/API | app/services/specs/session_based/lfa_player_service.py; app/api/api_v1/endpoints/lfa_player | Football Player service/API | F11, F16, F17 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Real skill/license APIs survive; retire broken license-wallet contract only after consumer inventory. |
| M19 Coach service/API | app/services/specs/semester_based/lfa_coach_service.py; app/api/api_v1/endpoints/coach | Coach service/API | F11, F15 | BROKEN | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Config/qualification rules survive; missing hours/promotion methods and outcome chain require repair, not whole rewrite. |
| M20 GanCuju service/API | app/services/specs/semester_based/gancuju_player_service.py; app/api/api_v1/endpoints/gancuju; app/services/gancuju_belt_service.py | GanCuju service/API | F11, F15 | BROKEN | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Preserve belt domain and history; repair service adapters and canonical IDs. |
| M21 Internship service/API | app/services/specs/semester_based/lfa_internship_service.py; app/api/api_v1/endpoints/internship; app/services/intern_progression_service.py | Internship service/API | F11, F15 | BROKEN | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Three-level config and five-semester narrative need product mapping; repair known adapters. |
| M22 Old raw-SQL curriculum adapter | app/api/api_v1/endpoints/curriculum; app/services/adaptive_learning_service.py | Old raw-SQL curriculum adapter | F10, F18 | BROKEN | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REWRITE | P1 | Narrow rewrite justified: queries and hierarchy based on absent tables, no working current-schema behavior to preserve. Retain content for migration review. |
| M23 Tracks/modules/certificates domain | app/models/track.py; app/services/track_service.py; app/services/certificate_service.py; app/api/api_v1/endpoints/tracks.py; app/api/api_v1/endpoints/certificates.py | Tracks/modules/certificates domain | F10, F22 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P0 | Existing ORM, ownership/verification/revocation survive; replace placeholder renderer and prove completion flow. |
| M24 Mock cross-program progression handlers | app/api/api_v1/endpoints/progression.py | Mock cross-program progression handlers | F07 | MOCK | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REWRITE | P0 | Narrow replacement justified: fabricated reads and nonpersistent writes cannot be refactored into truthful behavior. Preserve skill-profile portions. |
| M25 Quiz selection/scoring/submission | app/models/quiz.py; app/services/quiz_service.py; app/api/api_v1/endpoints/quiz | Quiz selection/scoring/submission | F12, F20 | PARTIAL | FINDINGS | Quiz unit suite PASS; real partial-answer probe REPRODUCED defect | REFACTOR | P1 | Keep question/option/attempt records and server scoring; repair denominator, replay constraints, versioned evidence. |
| M26 Adaptive session engine and routes | app/services/adaptive_learning.py; app/api/api_v1/endpoints/adaptive_learning.py; app/api/web_routes/adaptive_learning.py | Adaptive session engine and routes | F02, F09, F18, F23 | BROKEN | FINDINGS | AL suites PASS; real handler/service probes REPRODUCED BOLA and response defects | REFACTOR | P0 | Keep selection/randomization math; one authorized attempt/scoring/session lifecycle used by both clients. |
| M27 Adaptive content import/editor/quality/admin | app/services/al_import_service.py; app/services/al_editor_service.py; app/services/al_quality_service.py; app/services/al_analytics_service.py; app/api/web_routes/admin/adaptive_learning.py | Adaptive content import/editor/quality/admin | F18, F19, F20 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Keep schema validators, typed distractor pool and import log; persist topology/version/lifecycle, reconcile partial imports. |
| M28 Current adaptive content corpus | content/adaptive_learning | Current adaptive content corpus | F18, F24 | PARTIAL | FINDINGS | 31 files PASS pure _validate_file; pedagogy and DB import UNVERIFIED | CLEAN | P2 | 375 questions, 31 files; do not rewrite educational content without subject-matter review. |
| M29 Semesters/seasons/program enrollment | app/models/semester.py; app/models/semester_enrollment.py; app/api/api_v1/endpoints/semesters; app/api/api_v1/endpoints/semester_enrollments; app/api/web_routes/programs.py | Semesters/seasons/program enrollment | F15, F16, F17 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Preserve mini/academy/tournament distinctions, uniqueness and scheduling guards; verify exact all-four eligibility. |
| M30 Session/booking/attendance lifecycle | app/api/api_v1/endpoints/sessions; app/api/api_v1/endpoints/bookings; app/api/api_v1/endpoints/attendance.py; app/api/web_routes/attendance.py; app/models/booking.py; app/models/attendance.py | Session/booking/attendance lifecycle | F03, F20 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P0 | Keep booking partial uniqueness and attendance history; unify API/web assignment and confirmation policy. |
| M31 Instructor qualification/assignment/admin | app/api/api_v1/endpoints/instructor_management; app/api/api_v1/endpoints/instructor_assignments; app/api/web_routes/instructor.py; app/api/web_routes/instructor_dashboard.py | Instructor qualification/assignment/admin | F03, F15, F20 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Preserve qualifications/availability; prove scoped assessment and history across API/web. |
| M32 Tournament engine/results/reward orchestration | app/services/tournament; app/api/api_v1/endpoints/tournaments; app/api/web_routes/tournaments; app/models/event_reward_log.py | Tournament engine/results/reward orchestration | F37 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | CLEAN | P2 | Retain state machines, ranking/reward services and guards; deduplicate router mounting. Full DB concurrency/regression unverified. |
| M33 XP/gamification/achievements | app/services/gamification; app/models/gamification.py; app/api/api_v1/endpoints/gamification.py | XP/gamification/achievements | F12, F20, F23 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Keep XP ledger and badge rules; one immutable event key per outcome, reconcile bypass writes. |
| M34 Football skill progression and assessment | app/services/skill_progression; app/services/skill_progression_service.py; app/services/football_skill_service.py; app/models/football_skill_assessment.py; app/skills_config.py | Football skill progression and assessment | F16, F20 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | CLEAN | P2 | Keep deterministic EMA/laterality decomposition and central skill keys; provenance and domain-specific qualification checks remain. |
| M35 Project/exercise/competency learning | app/models/project.py; app/api/api_v1/endpoints/projects; app/api/api_v1/endpoints/competency.py | Project/exercise/competency learning | F10, F20, F24 | UNVERIFIED | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Routes/services exist, but cannot prove end-to-end mapped curriculum; preserve project model pending mapping. |
| M36 Global communications/calendar/events | app/api/api_v1/endpoints/notifications.py; app/api/api_v1/endpoints/messages.py; app/api/web_routes/communications.py; app/api/web_routes/events.py | Global communications/calendar/events | Source inventory; selected test evidence | UNVERIFIED | UNVERIFIED | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | CLEAN | P2 | Retain notification/message/calendar boundaries; full delivery/read-authority and per-program journeys unverified. |
| M37 Organization/locations/campuses/teams/pitches | app/models/location.py; app/models/campus.py; app/models/team.py; app/models/pitch.py; app/api/api_v1/endpoints/teams.py; app/api/api_v1/endpoints/pitches.py | Organization/locations/campuses/teams/pitches | Source inventory; selected test evidence | PARTIAL | UNVERIFIED | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | CLEAN | P2 | Retain operational domain and FKs; do not collapse academy location capabilities or team cost sharing. |
| M38 Sponsors/campaigns/public profiles/cards | app/services/card_system; app/api/web_routes/card_studio.py; app/api/web_routes/public_player.py; app/api/web_routes/admin/sponsors.py; app/models/sponsor.py | Sponsors/campaigns/public profiles/cards | F17, F32, F37 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P2 | Retain existing product behavior; extract repeated entitlement/publish rules and review public-data purpose. |
| M39 Virtual training/challenges/friendship | app/services/virtual_training_service.py; app/api/web_routes/virtual_training.py; app/api/web_routes/vt_challenges.py; app/api/web_routes/friends.py; app/models/virtual_training.py | Virtual training/challenges/friendship | F37 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P2 | Preserve game/challenge contracts; large adapters need bounded consolidation, native migration later. |
| M40 Outcome audit and system events | app/models/audit_log.py; app/services/audit_service.py; app/middleware/audit_middleware.py; app/api/api_v1/endpoints/audit.py; app/core/structured_log.py | Outcome audit and system events | F20, F35 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Keep audit tables/request context; transactionally persist actor/before/after/context for outcome changes. |
| M41 Juggling/media pipeline and retention | app/services/juggling; app/api/api_v1/endpoints/users; app/models/juggling.py; app/tasks/juggling_tasks.py; app/tasks/juggling_retention_task.py | Juggling/media pipeline and retention | F31, F32 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P2 | Preserve safe media serving/transcode; test delete/derivative retention and worker reliability without resetting POC state. |
| M42 Media path safety | app/services/juggling/media_service.py | Media path safety | Source inventory; selected test evidence | WORKING | No defect established within named component; integration/production UNVERIFIED | tests/unit/juggling/test_media_service.py — selected suite PASS | KEEP | P2 | Containment and state checks verified by selected pure unit suite; keep service. |
| M43 Ball trajectory computation | app/services/juggling/kalman_ball_tracker.py | Ball trajectory computation | Source inventory; selected test evidence | WORKING | No defect established within named component; integration/production UNVERIFIED | tests/unit/juggling/test_kalman_tracker.py — selected suite PASS | KEEP | P2 | Selected Kalman tests passed; keep algorithm, no speculative model replacement. |
| M44 Multicamera backend and device session domain | app/services/multicamera; app/api/api_v1/endpoints/multicamera; app/models/juggling.py | Multicamera backend and device session domain | Source inventory; selected test evidence | PARTIAL | UNVERIFIED | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | CLEAN | P2 | Current HEAD preserves backend polling/session/cycle contracts; DB/device regression not runnable without dedicated environment. |
| M45 Biometric encryption primitive | app/services/biometric/encryption_service.py | Biometric encryption primitive | F25 | WORKING | No defect established within named component; integration/production UNVERIFIED | tests/biometric/test_encryption_service.py — selected suite PASS | KEEP | P2 | AES-GCM tamper/nonce/key validation tests passed; production test-key gating belongs to configuration work. |
| M46 Liveness metadata sanitizer | app/services/biometric/liveness_metadata_sanitizer.py | Liveness metadata sanitizer | Source inventory; selected test evidence | WORKING | No defect established within named component; integration/production UNVERIFIED | tests/biometric/test_liveness_metadata_sanitizer.py — selected suite PASS | KEEP | P2 | Allowlist/minimization tests passed; preserve sanitizer and bounded DTOs. |
| M47 Biometric consent/liveness/matching/review | app/services/biometric; app/models/biometric.py; app/tasks/biometric_tasks.py; docs/biometric | Biometric consent/liveness/matching/review | F25, F32 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Preserve consent/review controls and feature flags; no production activation, fake provider allowed only deliberate test use. |
| M48 Native auth/networking/keychain | ios/LFAEducationCenter/Auth; ios/LFAEducationCenter/Networking | Native auth/networking/keychain | F08, F13, F26 | PARTIAL | FINDINGS | Unsigned Debug build PASS; Release FAIL; native tests NOT RUN | REFACTOR | P1 | Keep native AuthManager single-flight refresh/APIClient/Keychain; align lifecycle and error contracts, restrict release transport. |
| M49 Native hub/profile/credits/onboarding | ios/LFAEducationCenter/App; ios/LFAEducationCenter/Profile; ios/LFAEducationCenter/Credits; ios/LFAEducationCenter/Onboarding; ios/LFAEducationCenter/Unlock | Native hub/profile/credits/onboarding | F14 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | CLEAN | P2 | Native-canonical foundation survives; reuse for other three programs rather than WebView redesign. |
| M50 Native Education/skill dashboard | ios/LFAEducationCenter/Education; ios/LFAEducationCenter/Dashboard; ios/LFAEducationCenter/Models | Native Education/skill dashboard | F14, F26 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Keep native status UI; typed failures and actual curriculum navigation needed. |
| M51 Native Vision/Skeleton/annotation/video | ios/LFAEducationCenter/Skeleton; ios/LFAEducationCenter/Skeleton3D; ios/LFAEducationCenter/Juggling; ios/LFAEducationCenter/Training; ios/LFAEducationCenter/WebBridge | Native Vision/Skeleton/annotation/video | F32 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | CLEAN | P2 | Preserve native capture/mapping/upload/sync; bundled JS skeleton bridge is POC compatibility, not Education fallback browser. |
| M52 Native multicamera/GoPro diagnostics | ios/LFAEducationCenter/MultiCamera | Native multicamera/GoPro diagnostics | F13 | BROKEN | FINDINGS | Unsigned Debug build PASS; Release FAIL (F13) | CLEAN | P1 | Debug builds; Release-only symbol guards fail. Keep current no-MPC design and validated POC evidence, narrow build cleanup. |
| M53 Native biometric screens | ios/LFAEducationCenter/Biometric | Native biometric screens | F25, F32 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | CLEAN | P2 | Native screens present; R&D/consent legal gate remains. Do not claim production verification. |
| M54 CI/test harness and baseline evidence | .github/workflows; tests; app/tests; cypress; pytest.ini | CI/test harness and baseline evidence | F29, F36 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P2 | Retain meaningful suites; isolate database fixtures, run actual contract/security/Release gates, stop mocked historical contracts. |
| M55 Dependency/build manifests | requirements.txt; requirements-test.txt; cypress/package.json; cypress/package-lock.json; ios/LFAEducationCenter.xcodeproj | Dependency/build manifests | F13, F27, F28 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Inventory/update in approved phase; no upgrades during audit. |
| M56 Deployment/health/scheduler/worker ops | docker-compose.yml; docker-compose.test.yml; docker/Dockerfile.test; app/main.py; app/background; app/tasks; app/celery_app.py | Deployment/health/scheduler/worker ops | F30, F31 | BROKEN | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | REFACTOR | P1 | Retain FastAPI/PostgreSQL/Redis/Celery; restore reproducible boot and environment-specific readiness. |
| M57 Docs/scripts/old seed/demo fixtures | docs; scripts; datasets; tests/e2e/snapshots | Docs/scripts/old seed/demo fixtures | F38, F39, F40 | PARTIAL | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | CLEAN | P3 | Preserve history and prove references/data origin before deleting anything. Four syntax failures are outside application code. |
| M58 Native remaining three program journeys | ios/LFAEducationCenter/App/MainHubView.swift | Native remaining three program journeys | F14 | MISSING | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | BUILD | P1 | Canonical programs have disabled cards, need staged native access/curriculum/assessment after backend truth established. |
| M59 Verified complete content and assessment coverage | content/adaptive_learning; config/specializations | Verified complete content and assessment coverage | F24 | MISSING | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | BUILD | P1 | Build missing approved program materials/mapping; Football Player also needs completeness review, not automatic duplication. |
| M60 Secure account recovery lifecycle | app/api/api_v1/endpoints/auth.py; app/api/web_routes/auth.py | Secure account recovery lifecycle | F08 | MISSING | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | BUILD | P1 | No password-reset/recovery route in mounted inventory; implement verified single-use recovery and invalidation when approved. |
| M61 Immutable assessment version/provenance | app/models/quiz.py; app/models/al_import_log.py | Immutable assessment version/provenance | F20 | MISSING | FINDINGS | See LFA_TEST_EVIDENCE_2026.json; whole subsystem integration UNVERIFIED | BUILD | P1 | Store issued question/option/content/scoring version and outcome revision evidence; minimal addition to existing DB, no event platform. |


REWRITE justification is deliberately narrow. M22’s old SQL hierarchy cannot become the current Track/Module model through refactoring without changing its fundamental contract; preserve source content/history and rebuild that adapter against confirmed canonical mapping. M24 has no truthful persistence to preserve; replace only fabricated get/update handlers, retaining the real skill-profile implementation. All other recoverable business components receive incremental decisions. No DELETE is authorized or proven.

## Q. Missing Product Functionality

| Missing capability | Programs / priority | Evidence and boundary |
| --- | --- | --- |
| Reachable native program journeys | Coach/GānCuju/Internship;P1 |3 disabled hub cards (M58). Build after backend entitlement/content truth |
| Complete approved content/assessment mapping | All; especially3 non-Football domains;P1 |31 files all Football; legacy seeds not current complete curricula (M59). Football completeness also requires owner/subject review |
| Complete native lesson/assessment execution | All; staged native phase | EducationView status/catalog is not runner. Keep useful existing UI |
| Secure account recovery and revocation lifecycle | Global;P1 | No reset/recovery endpoint; refresh/logout semantics incomplete (M60/F08) |
| Immutable assessment presentation/version/provenance | All;P1 | No durable exact question/options/scoring-version snapshot (M61) |
| Real PDF certificate output | Where certificate product applies;P0 blocker | Placeholder bytes (F22); build renderer within retained service, not another certificate platform |
| Verified coherent progression/completion chain | All;P1 | Fake generic endpoints, absent methods and parallel authorities. Primarily repair current components |
| Account-wide privacy lifecycle | Global;requirement clarification | General retention/export/deletion flow not found; video/biometric partial controls do not cover whole account |
| Current all-four acceptance/security/release evidence | All;release gate | DB/native/browser/device/restore evidence missing; no inferred PASS |

Exams, qualification criteria, localized course equivalence and applicability of virtual/hybrid experiences to each program remain UNVERIFIED rather than invented mandatory implementations. Owner decisions below define those product boundaries.

## R. Target Architecture

**Minimum necessary architecture: retain the modular monolith, PostgreSQL, current worker tools and native SwiftUI client.** No microservices, new generic workflow framework, new cloud provider, new payment system, universal repository layer or WebView shell is justified.

| Existing component that survives | Minimum change in a later approved phase |
| --- | --- |
| FastAPI API + Jinja web adapters | Both call the same domain command/policy for registration, entitlement/credit, attendance and assessment |
| SQLAlchemy/PostgreSQL/Alembic | Keep tables/history; map legacy identities on sanitized restore before additive/corrective migration |
| User/global hub/credit interface | One user ledger and balance; specialization/session is nullable context, never wallet owner |
| UserLicense/program configs and eligibility | Exactly four product IDs; separate age/season types; one authority for entitlement and level |
| Current Track/Module and quiz/AL components | Establish confirmed hierarchy; replace old SQL adapter, persist versioned issued assessment and scope |
| Booking/session/tournament/skill/gamification | Preserve real domain algorithms; shared authorization and transactional outcome dispatch |
| Existing audit/history tables | Record actor/subject/context/before-after/version in outcome transaction; avoid accidental caller commits |
| SwiftUI AuthManager/APIClient/Keychain/hub/capture | Align token/error contracts; complete native education/program screens gradually |
| Complex web admin/instructor tools | Retain as web-canonical until replacement has behavior/acceptance evidence |
| Redis/Celery/media workers | Keep where needed; make ownership/retries/storage/readiness explicit |
| Existing tests/CI | Preserve meaningful suites; exercise real adapters, transaction races and both native configurations |

Each write command should own a clearly bounded transaction. Global credit replay must return the original result without another debit. Outcome writes should bind learner, program, session/content version and authorization context and produce durable history together. Notifications/media side effects can follow committed work through existing job infrastructure; use a minimal durable handoff only where failure evidence requires it, not a new event platform.

Read projections may serve hub and program dashboards without becoming another mutable authority. Web/native can retain distinct UI navigation while sharing business contracts. Temporary bundled Skeleton WebBridge can remain while device behavior is preserved. Target architecture intentionally retains proven code instead of optimizing for fewer lines.

## S. Remediation Sequence

These are **future phases, not actions taken**. Phase labels P0–P6 describe ordering; finding severity remains P0–P3.

| Phase | Work after approval | Exit evidence |
| --- | --- | --- |
| P0 — Security/authorization/integrity blockers | Remove credential logging; assess real log exposure; enforce AL/attendance ownership; atomic replay-safe debit/unlock/renew/refund; truthful progress/certificate responses | Real denial/replay/rollback/concurrency tests on disposable PostgreSQL; reconciled ledger design; no placeholder success |
| P1 — Canonical versus legacy model | Four IDs, age/season rules, global wallet, progress authority; inspect sanitized live-schema/data drift | Owner-approved qualification mapping; preserved historical row mapping and migration rehearsal |
| P2 — API/contracts/consolidation | Replace narrow absent-schema/fake adapters; repair24 missing-method sites and AL responses; shared policies/errors/audit transaction boundaries | Real service-handler/Swift contract tests; no silent success on error; exact route registration |
| P3 — Evidence-based cleanup | Broken Docker/Release guards, stale runbooks/scripts, obsolete wrappers/templates after reference proof | Reproducible environment; both native builds; deletion review with consumer/data proof |
| P4 — Four-program functionality | Approved curriculum/assessments/progression/completion and localization gaps | Product/subject-matter acceptance for each program; versioned content and outcome evidence |
| P5 — Native completion |3 program destinations and lesson/assessment journeys; preserve working auth/profile/credits/Vision | Device/native UI and accessibility/error-state acceptance; no WebView-first redesign |
| P6 — Regression/security/release | Full role/object/program matrix, DB migrations/restore, dependency triage, web/native/device/worker tests | Reviewed release configuration, no unresolved blockers, actual backup/restore and rollback evidence |

Dependency remediation for exposed auth/template/upload packages belongs early with the relevant security repair, not deferred until the final phase. Release compilation/Docker fixes can support isolated verification earlier than P3; the sequence is dependency-aware, not rigid. Biometric production activation requires its existing separate legal/DPO gate and real-provider validation regardless of engineering progress.

## T. Decision Requests

Only product-owner decisions remain here. Canonical IDs, global wallet ownership, missing service methods, broken contracts and Release failure are technical conclusions already answered by evidence.

1. **Program release scope and acceptance:** must all four complete journeys ship together, or can Football Player ship first with clearly unavailable remaining products? Specify required curriculum, assessments, completion/certificate criteria and localization for each program. Old seed content is not sufficient approval.
2. **Qualification/age policy:** confirm exact PRE/YOUTH boundaries and season cutoff, and how Internship’s3 levels relate to5 semesters. Decide treatment of existing learners affected by a corrected policy; technical migration mechanics will be designed from data evidence.
3. **Commercial entitlement policy:** confirm duration/renewal/refund rules and treatment of existing purchases whose recorded amount/period conflicts. Global wallet ownership is already settled; this asks about product terms, not database design.
4. **Data purpose and retention:** approve purposes/retention/access expectations for medical/emergency fields, photos/videos, motion/biometric data, outcome evidence and backups; identify any applicable qualification/audit obligation absent from current specifications.
5. **Biometric scope/approval:** confirm whether this remains disabled R&D or is intended for future production, and who owns the unresolved legal/DPO/accuracy acceptance in the existing activation checklist.

No owner answer is needed to recognize password disclosure, BOLA, non-idempotent credit writes, fake responses or build failures. No implementation began while these decisions were assembled.

Audit harness documentation and reproduction limits: [evidence README](LFA_RECOVERY_EVIDENCE_2026/README.md). Artifact hashes: [manifest](LFA_AUDIT_ARTIFACT_MANIFEST_2026.json).

**IMPLEMENTATION NOT STARTED — AWAITING OWNER REVIEW**
