# LFA PC3 — Player Canonical Education Hierarchy Specification (2026)

**Status:** specification only; implementation has not started

**Baseline:** `LFA_CANONICAL_BASELINE_PC2` → `382b5698eb0252194ce136992918496c78f864aa`

**Primary acceptance program:** `LFA_FOOTBALL_PLAYER`

**Scope:** Education Center hierarchy, content association, assessment association, and education completion state

**Excluded:** content rewriting, Coach/GānCuju/Internship content creation, historical backfill, deployment, production database mutation, PC4 work

## Executive decision

The canonical learner-visible hierarchy shall be:

```text
Program → Track → Module → Lesson → Component
```

The following terms do not create additional hierarchy levels:

- **Program** is the existing WS1 canonical program (`Specialization`), initially `LFA_FOOTBALL_PLAYER`.
- **Track** is the course-like learning path inside a Program. “Course” is not a second entity or API synonym.
- **Curriculum** means the complete published, versioned Track tree. It is an aggregate/view, not a database entity.
- **Topic** is stable taxonomy metadata attached to Lessons and assessments. It is not a parent between Module and Lesson.
- **Quiz** is a reusable assessment definition attached to a Lesson through an explicit assessment placement.
- **Adaptive Learning** is a delivery mode over explicitly attached quiz questions. It is not a separate curriculum.
- **Exam** is an assessment delivery/purpose mode. It does not require a separate hierarchy or question model.
- **Completion** belongs to the learner and the exact published Track release; it is not content metadata.

An internal `TrackRelease` version boundary is required between Track and Module. It is omitted from learner-visible navigation:

```text
Program → Track → [TrackRelease] → Module → Lesson → Component
                                      └──── LessonAssessment → Quiz → Questions
```

This keeps the product hierarchy small while making published content immutable, localizable, and historically reproducible.

## Audit method and limits

This specification was reconstructed statically from the PC2 baseline. The audit covered current SQLAlchemy models, the active Alembic chain, legacy migrations, active API and web route registration, services, seed/import scripts, OpenAPI snapshot, tests, the adaptive corpus, and the iOS Education Center client. No database was mutated and no remote service was accessed.

“Runtime-reachable” below means the router/service is imported by the running application. It does not mean that the referenced database objects exist in the canonical schema. The raw-SQL curriculum routes are runtime-reachable but structurally incompatible with a database built from the current migration chain.

## CURRENT hierarchy

### 1. Current-schema ORM track system

The current canonical migration chain contains this structure:

```text
Track
  └─ Module
       └─ ModuleComponent

UserTrackProgress
  └─ UserModuleProgress

CertificateTemplate → Track
IssuedCertificate → UserTrackProgress completion
```

Evidence:

| Source | Symbol/table | Current behavior | Reachability | Confidence |
|---|---|---|---|---|
| `app/models/track.py:9-41` | `Track` | UUID track with unique `code`, name, description, semester duration, JSON prerequisites, modules | Current ORM | High |
| `app/models/track.py:43-78` | `Module` | Ordered child of Track; optional semester; objectives and mandatory flag | Current ORM | High |
| `app/models/track.py:80-107` | `ModuleComponent` | Ordered typed child of Module; JSON payload; no Lesson relation | Current ORM | High |
| `app/models/user_progress.py:137-203` | `UserTrackProgress` | Track enrollment/completion percentage/certificate state | Runtime-reachable | High |
| `app/models/user_progress.py:214-292` | `UserModuleProgress` | Module status, grade, attempts and time; hardcoded `grade >= 60` helper | Runtime-reachable | High |
| `app/models/user_progress.py:118-120` | comment above Track progress | Calls the track models “OLD” and “DEPRECATED,” despite active APIs using them | Current source conflict | High |
| `app/models/certificate.py:11-30` | `CertificateTemplate` | Certificate template has a Track FK | Current ORM | High |
| `app/services/track_service.py:30-230` | `TrackService` | Enrollment, start, direct module completion, track aggregation and certificate issue | Runtime-reachable | High |
| `app/api/api_v1/endpoints/tracks.py:21-225` | `/api/v1/tracks/*` | Public API exposes track enrollment/progress and direct module start/complete commands | Registered API | High |
| `alembic/versions/2026_02_21_0859-1ec11c73ea62_squashed_baseline_schema.py:1277-1304,2461-2471,2578-2591,2643-2657` | current tables | `module_components`, `modules`, `tracks`, `user_module_progresses`, `user_track_progresses` are in the current schema | Current migration | High |

The physical Track/Module tables are usable. Their present semantics are not canonical: `Track` is documented as if it were the Program itself, and Player content is absent from the main initializer. `scripts/initialize_track_system.py:19-88` creates only Internship, Coach and GānCuju tracks; lines 91-302 create non-Player modules.

### 2. Runtime-reachable legacy raw-SQL curriculum system

The second hierarchy is:

```text
curriculum_tracks
  └─ lessons
       └─ lesson_modules
            └─ lesson_content

user_lesson_progress
user_module_progress                 # singular table name
lesson_quizzes
```

Evidence:

| Source | Symbol/table | Current behavior | Reachability | Confidence |
|---|---|---|---|---|
| `app/api/api_v1/api.py:154-155` | router registration | Registers `/curriculum` and `/curriculum-adaptive` | Runtime-reachable | High |
| `app/api/api_v1/endpoints/curriculum/tracks.py:15-97` | track/progress routes | Queries `curriculum_tracks`, `lessons`, `user_lesson_progress` by raw SQL | Runtime-reachable, schema missing | High |
| `app/api/api_v1/endpoints/curriculum/lessons.py:18-236` | lesson routes | Queries `lessons`, `lesson_modules`, `lesson_quizzes`, exercises and lesson progress | Runtime-reachable, schema missing | High |
| `app/api/api_v1/endpoints/curriculum/modules.py:14-161` | module commands | Writes singular `user_module_progress`, recalculates lesson progress, directly updates `users.total_xp` | Runtime-reachable, schema/column mismatch | High |
| `app/services/adaptive_learning_service.py:26-547` | legacy curriculum adaptive service | Repeated raw SQL against `user_lesson_progress` and `lessons` | Runtime-reachable through `/curriculum-adaptive` | High |
| `alembic/versions_legacy/2025_10_09_2200-create_curriculum_system.py:34-133` | legacy migration | Defines the raw-SQL hierarchy and progress tables | Legacy only | High |
| `alembic/versions_legacy/2025_10_09_2230-integrate_quizzes_curriculum.py:32-55` | legacy quiz link | Adds quiz curriculum columns and `lesson_quizzes` | Legacy only | High |
| Current squashed migration | absent objects | Contains none of `curriculum_tracks`, `lessons`, `lesson_modules`, `lesson_content`, `user_lesson_progress`, singular `user_module_progress`, or `lesson_quizzes` | Current migration | High |

This is **BROKEN legacy**, not a second system to restore. A fresh canonical PostgreSQL database cannot satisfy these queries. There is also an internal mismatch: the legacy migration defines `user_module_progress.lesson_module_id` (`2025_10_09_2200...py:118-129`), while the active module endpoint selects and writes `module_id` (`curriculum/modules.py:23-42`). The same endpoint updates `users.total_xp` (`curriculum/modules.py:90-92`), but the current User field is `xp_balance` (`app/models/user.py:103`).

The OpenAPI snapshot still advertises all legacy endpoints (`tests/snapshots/openapi_snapshot.json:13966-14947`). Existing smoke tests do not prove behavior: for example, `tests/integration/api_smoke/test_curriculum_smoke.py:200-317` accepts responses including HTTP 500, and uses literal placeholder route segments. Those tests characterize route presence, not a working curriculum.

### 3. Adaptive Learning title-derived hierarchy

The adaptive runtime stores quizzes and questions in current tables, but invents a third “module” from display text:

```text
Quiz title "AL — <module> - <topic> [<language>]"
  └─ split title at first " - " → module_prefix
       └─ AdaptiveLearningSession.module_prefix
```

Evidence:

| Source | Symbol | Current behavior | Reachability | Confidence |
|---|---|---|---|---|
| `app/models/quiz.py:53-125` | `Quiz`, `QuizQuestion`, `QuizAttempt` | Normalized quiz content and learner attempts | Current/runtime | High |
| `app/models/quiz.py:196-240` | `AdaptiveLearningSession` | Stores language, category and nullable `module_prefix`; no Track/Module/Lesson FK | Current/runtime | High |
| `app/api/web_routes/adaptive_learning.py:201-256` | `al_modules` | PostgreSQL `split_part(Quiz.title, ' - ', 1)` creates module identity | Runtime web | High |
| `app/api/web_routes/adaptive_learning.py:283-355` | `al_session_start` | Requires a client-supplied title prefix and validates with `Quiz.title LIKE` | Runtime web | High |
| `app/services/adaptive_learning.py:41-63,284-325` | session start/candidate selection | Persists the prefix and filters question candidates by quiz title; silently falls back from difficulty-filtered to all scoped questions | Runtime service | High |
| `app/api/api_v1/endpoints/adaptive_learning.py:16-96` | API start contract | Starts only by category; omits web-required language and module scope | Runtime API | High |
| `tests/unit/api/web_routes/test_adaptive_learning_web.py:2-19,368-408` | web tests | Explicitly lock in title-prefix module behavior | Current tests | High |

The title prefix is display/localization text, not a stable identity. English and Hungarian titles therefore produce separate pseudo-modules. The API and web contracts also differ: the API starts an unscoped category session, while web requires a language and `module_prefix`.

### 4. Native iOS Education Center

The native app has no Track, Module, Lesson, Component, Quiz, or adaptive-learning DTO and calls no corresponding education endpoint. `ios/LFAEducationCenter/Education/EducationViewModel.swift:3-18,76-118` loads specialization status, specialization progress, license, and skill profile only. It deliberately excludes a known mock progression endpoint. `EducationView.swift:63-103,193-218,343-369` renders identity/license/general progress/skill cards and a placeholder card; it does not render educational content. No education hierarchy tests exist under `ios/LFAEducationCenterTests`.

Native status is therefore **MISSING for curriculum**, while the surrounding account/license shell is reusable.

## Content corpus inventory and import loss

Static inventory of `content/adaptive_learning/**/*.json` at the baseline found:

- 31 JSON files and 375 questions;
- all declare only `LFA_FOOTBALL_PLAYER`;
- 258 English questions and 117 Hungarian questions;
- categories: 282 `LESSON`, 34 `GENERAL`, 30 `NUTRITION`, 29 `SPORTS_PHYSIOLOGY`;
- difficulties: 284 `EASY`, 41 `MEDIUM`, 50 `HARD`;
- 11 distinct localized module labels and 29 distinct localized topic labels;
- every file uses schema version `1.0`;
- top-level keys are exactly `schema_version`, `specializations`, `category`, `difficulty`, `quiz_title`, `language`, `topic`, `module`, `questions`;
- question keys are `text`, `type`, `points`, `explanation`, `options`, `metadata`;
- current metadata keys are `estimated_difficulty`, `cognitive_load`, `average_time_seconds`, `concept_tags`.

Representative evidence is `content/adaptive_learning/_shared/sports_physiology/conditioning_easy.json:1-11`; Player lesson corpora are under `content/adaptive_learning/lfa_football_player/en/lesson/` and `.../hu/lesson/`.

`app/services/al_import_service.py:108-140` validates schema version, specialization membership, category, difficulty, title and questions. Lines 294-355 persist the Quiz tree. The import currently preserves question text, type, points, explanation, options, quiz language/category/difficulty and selected question metadata. It loses or weakens the following structure:

| Source JSON field/fact | Current persistence | Loss/impact |
|---|---|---|
| `schema_version` | Used during import only | Not traceable after import |
| `specializations` | Validation gate only | Quiz has no Program FK |
| `module` | Flattened into `Quiz.description` | No Module FK or stable identity |
| `topic` | Flattened into `Quiz.description` | No taxonomy key; localized labels become identity-like text |
| source file/path | Import log records submitted filename only in JSON details | Quiz cannot prove which source produced it |
| source checksum | Not stored | Cannot distinguish unchanged replay from same-title changed content |
| content version/revision | Not stored | Published attempt cannot be bound to exact source revision |
| `quiz_title` | Used as identity and skip key | Rename/translation can duplicate; same-title content change is silently skipped |
| structured Program/Track/Module/Lesson placement | Not stored | Corpus cannot participate in curriculum completion |
| locale equivalence | Separate `language` rows only | EN/HU counterparts have no shared semantic key |
| `prerequisite_concepts` | Model supports it, current corpus does not provide it, importer does not set it | Future input would be dropped unless importer changes |

`validate_files` and `apply_import` use title-only skip checks (`al_import_service.py:450-473,521-539`). `ALImportLog` stores operation counts and free-form details (`app/models/al_import_log.py:17-35`), not a durable content-to-source relation.

The professional wording and answers in these files must remain unchanged. PC3 may map and persist their existing metadata; it must not generate, translate, or “improve” the questions.

## Conflict register

| ID | Conflict | Source A | Source B | Runtime impact | Canonical resolution |
|---|---|---|---|---|---|
| PC3-C01 | Program vs Track | `Specialization` is canonical Program (`user_progress.py:15-41`) | `Track` docstring calls Track the highest unit and gives Programs as examples (`track.py:9-13`) | Program ownership is ambiguous | Program owns Tracks; Track is a learning path inside Program |
| PC3-C02 | Two physical hierarchies | Current schema has `tracks/modules/module_components` | Active raw SQL expects legacy `curriculum_tracks/lessons/lesson_modules` | Fresh DB runtime failures; duplicate rules | Reuse current ORM tables; retire raw-SQL hierarchy |
| PC3-C03 | Reversed Module/Lesson meaning | ORM has Module→Component and no Lesson | Legacy has Lesson→“lesson_modules”→content | Contracts cannot align | Canonical Module→Lesson→Component |
| PC3-C04 | Adaptive module identity | AL derives module from localized Quiz title | ORM Module has UUID identity | Rename/locale changes scope and history | Explicit `LessonAssessment`/FK scope; title is display only |
| PC3-C05 | API/web adaptive contract | API starts by category (`adaptive_learning.py:16-96`) | Web requires language + module prefix (`web_routes/adaptive_learning.py:283-355`) | Different question pools by surface | One service/contract using assessment placement ID and locale |
| PC3-C06 | Multiple completion authorities | `TrackService` directly completes Module and aggregates Track | Legacy curriculum updates lesson/module rows; Quiz and AL track separate outcomes | Divergent completion and rewards | One canonical completion command/service; other rows are evidence or projections |
| PC3-C07 | Program progression mixed with education | `SpecializationProgress` stores level/XP/session/project totals | Track/Module progress stores course completion | Education can incorrectly become license/level truth | Program progression remains separate projection; education emits verified outcomes only |
| PC3-C08 | XP write paths | Legacy curriculum writes nonexistent `users.total_xp` | Current XP service writes `users.xp_balance` and ledger | Failure or balance drift | Education uses canonical XP ledger/service with idempotency key |
| PC3-C09 | Player identity collision | `seed_player_curriculum.py:20-27` uses legacy `PLAYER` and names GānCuju/Ganball content | WS1 canonical alias maps `PLAYER → GANCUJU_PLAYER` (`canonical_policy.py:80`) | Unsafe reinterpretation as Football Player | Preserve/manual review; never auto-map to `LFA_FOOTBALL_PLAYER` |
| PC3-C10 | Player content gap | Track initializer creates Internship/Coach/GānCuju only (`initialize_track_system.py:25-49`) | Adaptive corpus is all `LFA_FOOTBALL_PLAYER` | No navigable Player course tree | Create approved Player Track release from an explicit mapping manifest |
| PC3-C11 | Content lifecycle | Quiz has DRAFT/PUBLISHED/ARCHIVED | Tracks/Modules/Components have only `is_active` or no lifecycle/version | In-place edits change historical meaning | Immutable published Track release and versioned Quiz identity |
| PC3-C12 | Native contract | Backend exposes three competing APIs | iOS consumes none of them | Native Player journey cannot learn/complete | Add one typed `/education` contract; web and iOS consume it |
| PC3-C13 | Weak test evidence | Generated smoke tests accept HTTP 500 and literal placeholders | Unit tests mock raw SQL behavior | Broken schema can appear green | PostgreSQL behavior/contract tests with exact statuses and state assertions |
| PC3-C14 | Track authorization gap | Endpoint checks ownership of Track progress | `TrackService.start_module` can create progress for any supplied Module ID without checking it belongs to Track (`track_service.py:173-188`) | Cross-track state contamination | Canonical service validates full Program→release→Module→Lesson ancestry |

## Canonical target hierarchy

### Learner-visible model

```text
Program (existing Specialization)
└── Track
    └── Module
        └── Lesson
            ├── Component [ordered content blocks]
            └── LessonAssessment [ordered placement]
                └── Quiz [question set]
                    ├── QuizQuestion
                    └── QuizAnswerOption
```

### Internal version boundary

`TrackRelease` sits between Track and Module. A draft release is editable. A published release is immutable. A new version creates a new release and child structure. `Track.current_release_id` selects the current published release. Existing learner progress always retains its original `track_release_id`.

This avoids version fields and copy rules scattered independently across every API surface.

### Why Track, not Track plus Course

The current schema, certificate FK, service and API already use Track. Introducing Course as a second model would add migration and mapping cost without a distinct business meaning. In PC3, **Track is the course-like container**. UI copy may display “course” in a localized sentence, but API/schema/domain names use `track` consistently.

### Why Curriculum is not an entity

No independent Curriculum lifecycle survives the audit. The legacy `curriculum_tracks` table duplicates Track. Canonically, “curriculum” is the published content graph for a Track release. It may appear as a response name such as `TrackCurriculumResponse`; it must not own business rules or persistence.

### Why Topic is metadata

Current topics are localized labels in JSON, while lessons and quizzes need stable identity. Making Topic a required tree node would force every lesson into exactly one taxonomy branch and add no needed completion boundary. PC3 shall store:

- a stable, language-neutral `topic_key` on Lesson and/or LessonAssessment;
- optional additional `topic_tags` for search;
- localized topic display labels in localization metadata.

Topic does not control order, unlock, eligibility, or completion. If later editorial governance needs a Topic catalog, a lookup table can be added without changing the hierarchy.

## Entity definitions and ownership

| Entity | Meaning | Source of truth | Key invariants |
|---|---|---|---|
| `Specialization` / Program | WS1 canonical program identity | Existing `specializations.id` and canonical policy | PC3 first accepts only `LFA_FOOTBALL_PLAYER`; legacy aliases resolve before lookup |
| `Track` | Stable course-like learning path in one Program | `tracks` | Has one Program; stable code; no content version fields used as identity |
| `TrackRelease` | Immutable version of a Track curriculum | New table | Unique `(track_id, version)`; one current published release; publication is atomic |
| `Module` | Ordered thematic unit inside one release | Refactored `modules` | Stable key within release; cannot span releases |
| `Lesson` | Ordered learner outcome/completion boundary inside one Module | New table | Stable key; contains components and assessment placements |
| `ModuleComponent` | Ordered content block within one Lesson | Refactored existing table | Typed payload; no quiz embedded as opaque JSON; canonical rows have `lesson_id` |
| `Quiz` | Reusable, localized, versioned question set | Existing quiz tables, extended identity/version metadata | Published revision immutable; title is never identity |
| `LessonAssessment` | Placement of a Quiz in a Lesson | New table | Owns delivery mode, order, required flag and attempt policy; Quiz owns scoring content/pass threshold |
| `QuizAttempt` | Evidence of a learner’s scored attempt | Existing table | Bound indirectly/directly to the placement used; append-only after completion |
| `AdaptiveLearningSession` | Adaptive delivery instance and analytics | Existing table, add explicit assessment placement FK | Question candidates only from the linked placement; no title-prefix scope for new sessions |
| `UserComponentProgress` | Learner completion of required content blocks | New table | Unique per user Track progress/component; idempotent transition |
| `UserLessonProgress` | Canonical Lesson state and completion | New table | Bound to exact release; completes only from required component/assessment evidence |
| `UserModuleProgress` | Derived persisted aggregate for a Module | Existing table, refactored | Updated only by canonical completion service; no public “complete regardless of children” command |
| `UserTrackProgress` | Education enrollment and derived Track completion | Existing table, refactored | Bound to Program entitlement and exact release; separate from Program entitlement itself |
| `EducationProgressEvent` | Append-only transition/audit record | New table | actor, source, idempotency key, timestamp, before/after, release/content IDs |
| `XPTransaction` | Reward ledger | Existing canonical XP service/ledger | Education never directly edits User or Specialization XP balances |

`SpecializationProgress`, `LicenseProgression`, `UserQuestionPerformance`, AL session metrics, achievements, and skill progression are not education hierarchy/completion authorities. They may consume canonical completion outcomes or provide analytics.

## Assessment mapping

`LessonAssessment` has a deliberately small fixed contract:

- `lesson_id`;
- `quiz_id` pointing to the exact published Quiz revision;
- `delivery_mode`: `STANDARD`, `ADAPTIVE`, or `EXAM`;
- `purpose`: `FORMATIVE` or `SUMMATIVE`;
- `is_required`;
- `order_in_lesson`;
- `max_attempts` (nullable means policy-defined unlimited);
- optional unlock prerequisite placement IDs represented by explicit relations only if the approved Player map requires them.

`Quiz.passing_score` remains the score threshold authority. An Exam uses the same Quiz/attempt model with `delivery_mode=EXAM`; PC3 does not introduce an `Exam` content tree. An adaptive question set is the questions from one or more explicitly placed Quiz revisions. The adaptive engine can weight/select questions, but it cannot widen the pool by category or display-title prefix.

`SessionQuiz` and `ProjectQuiz` remain valid contextual placements for physical/virtual sessions and projects (`app/models/quiz.py:144-160`, `app/models/project.py:213-234`). They do not become education hierarchy links and do not authorize Lesson completion unless a canonical LessonAssessment explicitly references the same attempt under an approved rule.

## Localization model

The base/canonical language is English. Stable keys, IDs, ordering, types, prerequisite relations and completion rules are language-neutral. Existing English columns (`name`, `title`, `description`, objectives, component payload) remain the base content during the additive transition. Add a structured `translations` JSONB object to Track, Module, Lesson and Component content-bearing rows, keyed by BCP-47 locale, for non-English fields.

Quiz already has `language`; it additionally needs a stable language-neutral `content_key` so translated Quiz revisions can be grouped without title matching. API responses return:

- stable IDs and keys;
- resolved localized fields;
- `resolved_locale`;
- `available_locales`;
- explicit `translation_missing` where appropriate.

Fallback is deterministic: requested locale → English. Missing English canonical content makes the item unavailable/fail-closed; it must not silently borrow an arbitrary language or title-derived match.

The existing Hungarian corpus is legitimate localization/educational content. Mapping EN/HU pairs is editorial evidence work; spelling or professional content must not be changed by migration.

## Content versioning model

1. Draft `TrackRelease` trees may be edited.
2. Publish validates full ancestry, English base content, stable-key uniqueness, assessment references, and the absence of placeholder/unmapped required nodes.
3. Publishing computes and stores a deterministic content hash, freezes the release, and atomically changes `Track.current_release_id`.
4. Changes after publication require a new release version. Published Quiz revisions are similarly immutable.
5. New enrollments use the current published release. Existing learners remain on their bound release unless an explicit, separately approved transfer is recorded.
6. Completion records and certificates retain release/version identity and enough display metadata to remain historically intelligible.

## Proposed additive DB mapping

### Reuse and extend

- `tracks`: add `specialization_id` FK, language-neutral stable key/code rules, `default_locale='en'`, nullable `current_release_id`, and localization metadata. Retain existing IDs/data.
- `modules`: add nullable `track_release_id`, stable key, localization metadata, publication-safe timestamps. Retain `track_id` during transition as legacy compatibility; canonical writes derive ancestry through release. Remove redundant `track_id` only in a later cleanup after evidence, not in initial PC3.
- `module_components`: add nullable `lesson_id`, stable key, constrained component type, localization metadata and payload schema version. Existing `module_id` remains legacy/read-only until proven mapping; canonical writes require Lesson ancestry.
- `quizzes`: add stable `content_key`, integer revision/version, optional supersedes relation/source hash/source schema version. Keep existing language/content-status fields and all question/attempt rows.
- `adaptive_learning_sessions`: add nullable `lesson_assessment_id`; retain `module_prefix` read-only for historical rows. New canonical sessions require placement ID.
- `user_track_progresses`: add nullable `track_release_id` and uniqueness/idempotency constraints suitable for one active enrollment per user/release.
- `user_module_progresses`: add uniqueness on `(user_track_progress_id, module_id)` and audit/update metadata; remove the hardcoded pass-grade helper from authority use.
- certificate metadata/template path: retain Track association; issued metadata adds release/version identity.

### New tables

- `track_releases`;
- `lessons`;
- `lesson_assessments`;
- `user_lesson_progresses`;
- `user_component_progresses`;
- `education_progress_events`.

No new generic workflow engine, polymorphic content framework, independent Curriculum table, Course table, Topic tree, Exam table, or adaptive curriculum tree is required.

### Required constraints

- Program/Track and all parent-child FKs must be real database FKs.
- Unique stable keys within their parent/release.
- Unique Track version per Track and at most one current published release.
- Published release and published Quiz revisions are application-immutable, backed by tests and restricted commands.
- Unique learner progress rows per parent/content item.
- Unique `EducationProgressEvent.idempotency_key` per logical command.
- Completion FKs bind to the same Track release; cross-tree component/module/assessment IDs fail before mutation.
- Deletion of published content/history is restricted; archive/retire instead.

The initial migration is additive and nullable where existing rows cannot be proven. Canonical application writes must enforce the new fields immediately; nullable columns exist only to preserve historical rows, not to allow new ambiguous data.

## Canonical progress and completion policy

The only command authority is a canonical `EducationProgressService`, used by API and web. Native calls the same API. Direct model mutations and raw-SQL updates are not supported commands.

State flow:

```text
NOT_STARTED → IN_PROGRESS → COMPLETED
```

Rules:

1. PC1 canonical Player eligibility and `LFA_FOOTBALL_PLAYER` entitlement authorize Track access. Education enrollment cannot create or repair entitlement.
2. A learner is bound to one published Track release when education progress starts.
3. Starting or completing a child validates the entire Program→Track→release→Module→Lesson ancestry.
4. A Component completes from its type-specific evidence. Repeated completion with the same idempotency key returns the same result.
5. A Lesson completes only when all required Components are complete and all required LessonAssessments have a passing completed attempt under their placement policy.
6. Module and Track states/percentages are derived from required child completion. They are persisted projections for efficient reads, updated in the same transaction.
7. QuizAttempt and AdaptiveLearningSession record attempt/delivery truth. Neither independently marks a Lesson complete; the canonical service evaluates the linked placement.
8. Completion, progress projections, audit event and any reward ledger entry commit atomically. Failure rolls back all of them.
9. XP is awarded through the existing ledger-backed XP service with an education-event idempotency key. No direct `users.*xp*` update is allowed.
10. Completion against an archived, unknown or unmapped historical node fails closed and remains available for manual reconciliation.

`SpecializationProgress` may receive a projection after commit, but cannot determine Lesson/Module/Track completion. License/category/skill progression remains outside PC3.

## API mapping

The canonical API surface should be cohesive and typed under `/api/v1/education`:

| Method/path | Purpose |
|---|---|
| `GET /programs/{program_id}/tracks` | Eligible published Track catalog for current learner |
| `GET /tracks/{track_id}` | Current localized Track release summary |
| `GET /tracks/{track_id}/curriculum` | Versioned Module→Lesson→Component/assessment tree plus learner state |
| `GET /lessons/{lesson_id}` | Localized Lesson content and assessment placements |
| `POST /components/{component_id}/complete` | Idempotent component completion command |
| `POST /assessments/{placement_id}/attempts` | Start standard/adaptive/exam attempt in exact Lesson context |
| `POST /assessments/{placement_id}/attempts/{attempt_id}/submit` | Submit and transactionally project completion |
| `GET /progress` | Learner education progress bound to release/version |

All mutation requests require an idempotency key. Responses include `program_id`, `track_id`, `track_release_id`, `content_version`, stable child IDs, locale metadata, and explicit state.

Compatibility plan:

- `/api/v1/tracks` routes become thin adapters to the canonical service where an exact mapping exists; they do not retain business rules.
- `/api/v1/curriculum` and `/api/v1/curriculum-adaptive` raw-SQL implementations are retired. During a bounded compatibility window, mapped reads may adapt canonical DTOs. Ambiguous/unmapped requests fail closed with an explicit deprecation error rather than querying missing tables.
- `/api/v1/adaptive-learning` start/answer paths converge on LessonAssessment placement IDs. Category-only or title-prefix starts remain read-only historical compatibility and cannot award canonical Lesson completion.
- OpenAPI changes are additive first. Deprecated contracts are marked before removal; exact contract tests replace “any status including 500” smoke assertions.

## Web mapping

Web controllers render canonical DTOs and call the same `EducationProgressService` command layer as API. They do not derive modules with SQL string functions, parse Quiz titles, calculate completion, or award XP. The current adaptive pages can be retained visually, but module discovery and session start must use stable Track/Module/Lesson/assessment IDs.

No silent fallback is permitted. Missing mapping or unavailable locale is shown as an explicit unavailable state; English fallback is reported in the DTO.

## Native mapping

iOS adds typed models mirroring the canonical education DTOs:

- `EducationTrackSummary`;
- `EducationCurriculum` with `contentVersion`/release ID;
- `EducationModule`, `EducationLesson`, `EducationComponent`;
- `LessonAssessmentSummary`;
- typed progress/status and locale metadata.

`EducationViewModel` loads the Player Track catalog and selected curriculum after PC1 entitlement succeeds. It must not use `SpecializationProgressData` as lesson completion or locally synthesize progress. Native actions call canonical component/assessment commands and refresh returned server state. Cached content is keyed by Track release + locale; a release mismatch invalidates the cache safely.

The existing native identity/license/skill shell is retained. No Coach, GānCuju, or Internship education screens/content are included in PC3 implementation acceptance.

## Adaptive Learning mapping

1. Introduce an owner-reviewed mapping manifest containing stable Program/Track/Module/Lesson/topic/quiz keys, source path, locale, and source checksum.
2. Import the existing 31 files byte/field faithfully into versioned Quiz revisions. Do not edit question text, answers, explanation or educational meaning.
3. Link each approved Quiz revision to a Lesson through `LessonAssessment`.
4. New AL sessions receive `lesson_assessment_id`; the service resolves allowed Quiz IDs from that placement.
5. Candidate selection continues using QuestionMetadata and UserQuestionPerformance. The current difficulty fallback may broaden only within the explicitly resolved placement, never to another Lesson/Track/Program.
6. Topic/module strings become localized display/taxonomy metadata. They never identify DB scope.
7. Completed adaptive sessions remain analytics/audit evidence. A required adaptive placement completes only under its explicit passing/completion policy.

The current corpus is sufficient to build question sets, but not sufficient by itself to decide the approved Module/Lesson editorial structure. That mapping is an owner/content-owner gate.

## Legacy compatibility and historical data

The following data remains untouched unless exact provenance is proven:

- existing `tracks`, `modules`, `module_components`, `user_track_progresses`, and `user_module_progresses` rows lacking Program/release/Lesson identity;
- any deployed legacy raw-SQL curriculum tables even though they are absent from the canonical migration chain;
- existing Quiz/attempt/AL rows without stable content key, checksum or LessonAssessment link;
- title-prefix `AdaptiveLearningSession.module_prefix` history;
- legacy `PLAYER` GānCuju/Ganball curriculum from `scripts/seed_player_curriculum.py`;
- existing certificates and their stored metadata.

Compatibility classifications:

- **Provable mapping:** may be linked after a dry-run report and explicit mapping review; content bytes/meaning remain unchanged.
- **Ambiguous mapping:** read-only/manual review; excluded from canonical completion and current Player catalog.
- **Legacy-only route:** no new writes; compatibility adapter only when exact canonical identity is available.
- **Historical attempt:** remains valid evidence of what happened, but does not retroactively grant canonical Lesson completion without an approved rule.

There is no automatic backfill, title-based guessing, Program reinterpretation, or historical completion recalculation.

## Migration strategy

### Phase PC3-1 — characterize before change

- PostgreSQL tests reproduce current raw-SQL failure against the canonical migration head.
- Contract tests lock current Track/Quiz/AL response shapes and native behavior.
- Inventory/mapping tooling produces read-only reports for every existing content and progress row.

### Phase PC3-2 — additive schema and models

- Add release, Lesson, assessment placement, progress and audit tables/columns.
- Add constraints and indexes.
- Do not alter or delete existing rows.
- Add model/service tests before routes change.

### Phase PC3-3 — canonical service and Player draft content map

- Implement one read/command service with PC1 authorization.
- Build a draft `LFA_FOOTBALL_PLAYER` Track release only from the reviewed mapping manifest.
- Import existing source content unchanged with checksums and stable keys.
- Keep the release unpublished until coverage and editorial mapping gates pass.

### Phase PC3-4 — surface convergence

- Add canonical API and OpenAPI contracts.
- Point web at the canonical service.
- Add native typed DTOs and Player screens.
- Convert exact legacy reads to adapters and disable legacy writes.
- Convert AL from title-prefix/category scope to assessment placement scope.

### Phase PC3-5 — controlled publish and verification

- Publish the validated Player release atomically in disposable PostgreSQL first.
- Run full Player journey, replay/concurrency, API/web/native and P0/WS1/PC1/PC2 regression gates.
- Production publication/deployment remains a separate owner-approved operation.

## Rollback strategy

- Initial schema changes are additive. Application rollback leaves new tables dormant and preserves all history.
- Track publication is a transactional current-release pointer change. Rollback selects the prior already-published release; it never edits completed progress.
- New progress commands use idempotency keys and atomic transactions, so a failed cutover cannot leave partial completion/reward state.
- During the compatibility window, old read routes can be re-enabled as adapters. Raw-SQL writes are not a rollback mechanism.
- Do not drop new columns/tables in an operational rollback. Destructive schema cleanup requires a later evidence-backed migration gate.
- Content import rollback archives the unpublished/new revision and restores the prior current release. It never deletes questions, attempts, learner history or certificates.

## Required tests

### Characterization/regression

- Prove current ORM Track tables exist at migration head and legacy curriculum tables do not.
- Reproduce raw-SQL route failures on a disposable UTF-8 PostgreSQL database.
- Preserve Quiz, QuizQuestion, options, attempts, AL performance/session history, Track certificate behavior and existing unrelated use contexts.
- Assert no content source file is modified by implementation.

### Schema and versioning

- Fresh upgrade and upgrade-from-PC2 on disposable UTF-8 PostgreSQL.
- FK, stable-key, version uniqueness, current-release and cross-release ancestry constraints.
- Published release/Quiz immutability and new-version flow.
- Rollback leaves historical rows readable and no partial writes.

### Import/content fidelity

- All 31 files/375 questions retain every current field and UTF-8 text exactly.
- Stable source path/checksum/version persisted.
- Same checksum replay is idempotent; same title with different checksum is a new reviewed revision, not a silent skip.
- EN/HU association uses reviewed stable keys, never normalized display-title guesses.
- Unknown schema field fails visibly or is retained according to explicit schema policy; it is never silently discarded.

### Authorization and isolation

- Eligible `LFA_FOOTBALL_PLAYER` learner; missing/expired/wrong-program entitlement.
- Learner can read/mutate only own progress.
- Cross-Program, cross-Track, cross-release and foreign Lesson/Component/assessment IDs fail before mutation.
- Admin/content-publisher roles can draft/publish only through explicit policy; instructor access follows canonical role assignment where needed.

### Completion, replay and concurrency

- Required/optional Component boundaries.
- Standard, adaptive and exam assessment pass/fail/retake rules.
- Lesson cannot complete without every required evidence item.
- Module/Track projection matches required child truth.
- Duplicate/replayed Component and assessment completion returns one logical outcome.
- Concurrent final-child completions produce one Lesson/Module/Track completion event, one reward, and at most one certificate.
- Injected failure rolls back progress, projections, event and XP ledger together.
- Historical/unmapped content fails closed.

### Contract and surfaces

- OpenAPI snapshot for canonical education DTOs and explicit deprecation metadata.
- Web and API call the same canonical service/policy; no raw-SQL or title parser remains in route business logic.
- API/web return the same hierarchy, locale, release and completion state.
- iOS DTO decoding fixtures for complete, optional, unknown-enum and translation-fallback responses.
- Local iOS build and Player Education navigation/action tests.
- Adaptive pool contains only questions linked to the requested LessonAssessment.

### Regression scope

- P0 authorization/security/credit replay and rollback.
- WS1 Program/season/category authority.
- PC1 Player identity/DOB/guardian/entitlement.
- PC2 participation/booking/attendance.
- Quiz session/project placements, achievements, XP ledger and certificates.
- Test database fail-closed protections.

## Model disposition

| Model/system | Disposition | Required action |
|---|---|---|
| `Specialization` (Program) | **KEEP** | Use as Program FK and PC1/WS1 identity |
| `Track` | **REFACTOR** | Keep table/IDs; make course-like child of Program; add current release/localization |
| `Module` | **REFACTOR** | Keep table/IDs; bind canonical rows to TrackRelease; add stable key/localization |
| `ModuleComponent` | **REFACTOR** | Keep table/IDs; bind canonical rows to Lesson; constrain payload/type |
| `Lesson` | **ADD** | Create canonical completion/content boundary |
| `TrackRelease` | **ADD** | Create internal immutable version boundary |
| `Quiz`, questions, options, attempts | **KEEP + REFACTOR METADATA** | Preserve content/history; add stable version/source identity and placement linkage |
| `AdaptiveLearningSession`, question performance, answer log | **KEEP + REFACTOR SCOPE** | Preserve algorithm/history; replace title scope for new sessions with placement FK |
| `UserTrackProgress`, `UserModuleProgress` | **REFACTOR** | Bind to release; make canonical-service projections; add constraints |
| `UserLessonProgress`, `UserComponentProgress`, progress events | **ADD** | Canonical granular completion and audit |
| `SpecializationProgress` | **KEEP OUTSIDE EDUCATION AUTHORITY** | Program-level projection only |
| `SessionQuiz`, `ProjectQuiz` | **KEEP** | Preserve their contextual use; do not use as curriculum hierarchy |
| raw-SQL `curriculum_*`, legacy lesson/module progress runtime | **REWRITE/RETIRE ADAPTERS** | Remove raw-SQL business logic; exact reads may adapt canonical service temporarily |
| iOS Education identity/license shell | **KEEP** | Add typed curriculum models and screens |
| iOS education hierarchy/progress implementation | **ADD** | Consume canonical API; no local authority |

No platform-wide model rewrite is required. “REWRITE” applies only to the broken raw-SQL route/service adapters, whose referenced schema is not part of the canonical migration chain.

## Owner/content-owner decisions

The domain hierarchy itself has no unresolved owner decision: this specification selects Track as the single course-like concept, Curriculum as an aggregate, and Topic as metadata.

One content decision is required before a Player release may be published:

1. **PLAYER CONTENT MAPPING APPROVAL — REQUIRED FOR PUBLICATION.** Approve the explicit mapping manifest that assigns each existing `LFA_FOOTBALL_PLAYER` source file/Quiz revision to canonical Track→Module→Lesson placements and pairs verified EN/HU translations. The repository supplies labels and questions but not a reliable approved Lesson structure. PC3 implementation may build the additive schema, importer and a draft manifest/report before this approval; it may not guess mappings, rewrite content, or publish the release.

The legacy `PLAYER` Ganball curriculum is not part of this decision: WS1 already resolves `PLAYER` to `GANCUJU_PLAYER`, so it remains read-only/manual review and cannot populate the Football Player Track.

## Acceptance gate for implementation

PC3 implementation is complete only when:

- one Player hierarchy is available through canonical API, web and native contracts;
- no active route owns parallel hierarchy/completion business logic;
- adaptive selection uses explicit assessment placement IDs;
- the approved existing content is preserved exactly and linked by stable IDs/checksums;
- progress and rewards are atomic, idempotent, auditable and release-bound;
- historical ambiguous data is unchanged and fail-closed;
- all required PostgreSQL, contract, native and prior-baseline regressions pass locally;
- no production database or deployment is touched.

## Cost guard and execution statement

This specification work performs no push and creates no PR. Therefore expected GitHub Actions runs are `0`; GitHub macOS/paid runners are not used. Any later implementation push requires a fresh open-PR, `push`, `pull_request`, `workflow_run` and runner preflight with expected Actions runs exactly `0`.

**IMPLEMENTATION NOT STARTED — WAITING FOR OWNER REVIEW**
