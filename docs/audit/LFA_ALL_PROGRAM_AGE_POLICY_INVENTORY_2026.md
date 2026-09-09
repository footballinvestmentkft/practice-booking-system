# LFA AGE / SEASON / PROGRESSION POLICY INVENTORY

**Audit date:** 2026-09-09  
**Repository baseline:** `894c43a4a37af4a8a583240a5a2d0b1f31a3fdee`  
**Mode:** static repository reconstruction; no policy normalization, implementation, migration, or data mutation  
**Canonical programs:** `LFA_FOOTBALL_PLAYER`, `LFA_COACH`, `GANCUJU_PLAYER`, `INTERNSHIP`

## Reading guide

- **Runtime-reachable** means the source is imported by a mounted API/web route or a service used from one. It does not prove that every branch completes successfully.
- **Declarative/broken route** means the route is mounted, but the inspected call path invokes methods absent from the current service or otherwise cannot complete as declared.
- **Legacy/supporting** means the source still exists but is not the canonical config loader input or is a historical compatibility path.
- **Executable** means Python/config interpreted by runtime. **Contract/UI**, **test**, and **documentation** sources are printed separately and do not override executable behavior.
- Line numbers refer to the audited baseline. Confidence is **HIGH** where the exact branch and caller are visible, **MEDIUM** where reachability depends on data/route selection, and **LOW** for narrative-only evidence.

## 1. LFA Football Player

### 1.1 Program entry, license, training, and competition

| ID | Rule surface | Current repository rule | Age calculation / cutoff | Evidence | Reachability / type | Confidence |
|---|---|---|---|---|---|---|
| FP-01 | Program minimum | 5+ in canonical config, shared age helper, native card, and central specialization validator. The session service instead rejects age 5 and requires 6+. | Config/helper use caller-provided current age; central validator uses `User.age`; session service uses calendar age today. | `config/specializations/lfa_football_player.json:5` `min_age`; `app/utils/age_requirements.py:49-67,83-109`; `app/services/specs/session_based/lfa_player_service.py:119-157`; `ios/LFAEducationCenter/App/MainHubView.swift:51-57`; `tests/unit/test_age_requirements.py` | Executable config/service + native + test; runtime-reachable | HIGH |
| FP-02 | Program maximum | No program maximum. PRE/YOUTH have upper bounds in some sources; AMATEUR/PRO do not. | N/A | `config/specializations/lfa_football_player.json:5-31`; `app/utils/age_requirements.py:49-67` | Executable config/helper | HIGH |
| FP-03 | License unlock | Bearer `/specialization/unlock` requires DOB-derived current age and 5+. Web onboarding `/specialization/select` performs no age check. Central `/specializations/me` uses JSON validation but missing DOB is warning-only. | Calendar age today; no season cutoff. | `app/api/web_routes/specialization.py:42-194`; `app/api/web_routes/onboarding.py:37-156`; `app/api/api_v1/endpoints/specializations/user.py:58-140`; `app/services/specialization_validation.py:96-127,153-190` | Runtime-reachable API/web | HIGH |
| FP-04 | Semester enrollment | Generic admin enrollment assigns PRE/YOUTH from age at current season's July 1; under 5 and over 18 receive `None`. It does this without first restricting the rule to football specialization. | Season age at July 1; current season picked from today's month. | `app/api/api_v1/endpoints/semester_enrollments/crud.py:104-121`; `app/services/age_category_service.py:15-99` | Runtime-reachable API | HIGH |
| FP-05 | Training/session booking | Player service requires active license/enrollment/payment, then applies its 6–11/12–18/14+ category rules. It reads a season-type value such as `LFA_PLAYER_PRE` from `UserLicense.specialization_type`, despite the enum declaring only unified `LFA_FOOTBALL_PLAYER` legal for user licenses. | Calendar age today when category calculated; booking primarily trusts license category. | `app/services/specs/session_based/lfa_player_service.py:54-82,119-157,235-338`; `app/models/specialization.py:34-62`; route mount `app/api/api_v1/api.py:183` | Runtime-reachable service, internally conflicting representation | HIGH |
| FP-06 | Tournament competition, API | Availability derives season-age category; over-18 may use latest enrollment category, otherwise defaults AMATEUR. Enrollment requires DOB, derives season-age, allows an adult category only under its own tournament-data rules, and then requires that derived category occur literally in tournament allowed groups. | Season age at tournament season year / July 1. | `app/api/api_v1/endpoints/tournaments/available.py:77-137`; `app/api/api_v1/endpoints/tournaments/enroll.py:140-203,277-289`; `tests/unit/services/test_tournament_enroll.py:257-270` | Runtime-reachable API + test | HIGH |
| FP-07 | Tournament competition, web | Browse does not age-filter tournaments. Enrollment verifies football license/onboarding but does not compare the player's category with tournament age groups. Missing DOB, under-5, and over-18 category resolution falls back to AMATEUR. | Season age when DOB exists; default AMATEUR otherwise. | `app/api/web_routes/tournaments/__init__.py:74-80`; `app/api/web_routes/tournaments/browse.py:26-90,93-230` | Runtime-reachable web | HIGH |
| FP-08 | Tournament policy helper | Visibility/validation helper declares self-service upward access: PRE→PRE/YOUTH/AMATEUR/PRO; YOUTH→YOUTH/AMATEUR/PRO; AMATEUR→AMATEUR/PRO; PRO→PRO. Enrollment endpoint imports but does not call the validator. | Category hierarchy, not direct age. | `app/services/tournament/validation.py:17-104`; `app/api/api_v1/endpoints/tournaments/enroll.py:20,140-310`; `tests/unit/services/test_tournament_enroll.py` | Executable helper, partially stale at enrollment | HIGH |

### 1.2 Categories and numeric levels

The same program contains two incompatible executable category tables. Neither is treated as canonical here.

| Category | Canonical JSON/config rule | Session-service rule | Assignment / movement currently encoded | Evidence | Confidence |
|---|---|---|---|---|---|
| PRE | 5–13; canonical levels 1–2. Academy config also 5–13. | 6–11, self-enrollable. | Season service says under-14 cannot leave PRE, but admin may bypass; session booking permits PRE user into YOUTH; public card uses `<7`. | `config/specializations/lfa_football_player.json:8-15`; `config/specializations/lfa_player_pre_academy.json:4-18`; `app/services/specs/session_based/lfa_player_service.py:54-61,287-307`; `app/services/age_category_service.py:102-167`; `app/api/web_routes/public_player.py:336-345` | HIGH |
| YOUTH | 14–18; canonical levels 3–4. However both level records state `min_age: 9`. Academy config 14–18. | 12–18, self-enrollable. | Override docs say YOUTH can move to AMATEUR/PRO; implementation accepts PRE too for any 14+ user. Session booking permits YOUTH→PRE and YOUTH→AMATEUR. | `config/specializations/lfa_football_player.json:16-21,70-103`; `config/specializations/lfa_player_youth_academy.json:4-18`; `app/services/specs/session_based/lfa_player_service.py:62-68,287-317`; `app/services/age_category_service.py:130-167` | HIGH |
| AMATEUR | 14+, no maximum; canonical levels 5–6. Academy config 14+. | 14+, no maximum, self-enrollable; auto-category for 19+ is AMATEUR. | Season-age service requires instructor assignment for >18; session booking permits AMATEUR→YOUTH, a downward participation rule. Tournament helper permits AMATEUR→PRO. | `config/specializations/lfa_football_player.json:22-27,104-137`; `config/specializations/lfa_player_amateur_academy.json:4-18`; `app/services/specs/session_based/lfa_player_service.py:69-75,119-157,287-326`; `app/services/tournament/validation.py:17-104` | HIGH |
| PRO | 14+, no maximum; canonical levels 7–8. Academy config 14+. | 14+, no maximum; cannot self-enroll; comment says Master Instructor promotion. Auto-category never selects PRO. | Session booking restricts PRO to PRO. `promote_to_higher_age_group` accepts any different category, performs no hierarchy, age, role, or assignment-scope check, then overwrites license specialization type. | `config/specializations/lfa_football_player.json:28-31,138-171`; `config/specializations/lfa_player_pro_academy.json:4-18`; `app/services/specs/session_based/lfa_player_service.py:76-82,119-157,524-567` | HIGH |

| Numeric level | Repository name | Category | Per-level minimum in canonical JSON | Other age evidence | Current age policy status |
|---|---|---|---:|---|---|
| 1 | LFA Pre Football Player | PRE | 5 | PRE category 5–13 vs session PRE 6–11 | Conflict |
| 2 | LFA Pre Football Advanced Player | PRE | 5 | PRE category 5–13 vs session PRE 6–11 | Conflict |
| 3 | LFA Youth Football Player | YOUTH | 9 | Parent category says 14–18; session YOUTH says 12–18 | Conflict |
| 4 | LFA Youth Football Advanced Player | YOUTH | 9 | Parent category says 14–18; session YOUTH says 12–18 | Conflict |
| 5 | LFA Amateur Football Player | AMATEUR | 14 | No maximum | Present, consistent at minimum |
| 6 | LFA Amateur Football Advanced Player | AMATEUR | 14 | No maximum | Present, consistent at minimum |
| 7 | LFA Pro Football Player | PRO | 14 | No maximum; service requires promotion rather than self-enrollment | Present, assignment rule conflicts |
| 8 | LFA Pro Football Elite Player | PRO | 14 | No maximum; service requires promotion rather than self-enrollment | Present, assignment rule conflicts |

**Evidence for levels:** `config/specializations/lfa_football_player.json:33-171` (`levels[*].requirements.min_age`). These records are loaded by `SpecializationConfigLoader.CONFIG_FILE_MAP` at `app/services/specialization_config_loader.py:32-38`. Level-age enforcement during progression was not found; the values are declarative config. Confidence: **HIGH** for values, **MEDIUM** for enforcement effect.

### 1.3 Season model and movement

| ID | Question | Current repository evidence | Runtime consequence | Evidence / classification | Confidence |
|---|---|---|---|---|---|
| FP-S01 | Season start/end | Model comments, age service, academy configs and academy generator state July 1→June 30. | Used by season-age assignment and academy season generation. | `app/models/specialization.py:47-50`; `app/services/age_category_service.py:15-99`; `config/specializations/lfa_player_*_academy.json:7-10`; `app/api/api_v1/endpoints/semesters/academy_generator.py:171-187`; executable/config | HIGH |
| FP-S02 | Cutoff | Age on July 1 of season start year. Jan–Jun dates belong to season that started previous calendar year. | Birthday after July 1 is ignored in that season-age calculation. | `app/services/age_category_service.py:15-43,81-99`; executable | HIGH |
| FP-S03 | Recalculation | Generic enrollment calculates once at enrollment using the *current* season. No scheduled boundary recalculation or preserved season-base record was found. | A record is not demonstrably recalculated at July 1; new enrollment after boundary gets new value. | `app/api/api_v1/endpoints/semester_enrollments/crud.py:104-121`; `app/models/semester_enrollment.py:92-100`; executable/model | HIGH |
| FP-S04 | Birthday change | Season-age functions say no midseason change. Other live helpers and player service use `date.today()`, so birthday can change displayed/derived category during a season. | Behavior differs by route. | `app/services/age_category_service.py:15-43`; `app/services/specs/base_spec.py` `calculate_age`; `app/services/specs/session_based/lfa_player_service.py:119-157`; `app/api/web_routes/helpers.py:44-70`; executable | HIGH |
| FP-S05 | Base vs effective representation | One mutable `SemesterEnrollment.age_category` plus latest override flags/actor/time. No distinct base/effective fields, reason/source, or append-only movement history. `to_dict()` omits category and override metadata. | Override destroys previous effective value; base and movement chain cannot be reconstructed from this row. | `app/models/semester_enrollment.py:92-115,129-144`; executable model | HIGH |
| FP-S06 | Upward movement | Endpoint says instructors may move 14+ among YOUTH/AMATEUR/PRO anytime; admin may override every category. It authorizes any instructor globally, has no object assignment scope, no reason, and overwrites the one field. Tournament helper separately permits player self-service upward competition. | Multiple authorization/business policies. | `app/api/api_v1/endpoints/semester_enrollments/category_override.py:29-104`; `app/services/tournament/validation.py:17-104`; executable API/service | HIGH |
| FP-S07 | Downward movement | Validator implementation accepts all valid categories for anyone 14+, so YOUTH→PRE and adult→PRE/YOUTH are valid. Session booking explicitly permits YOUTH→PRE and AMATEUR→YOUTH. Admin can bypass even under-14 restriction. | Downward movement/participation exists despite upward-only narrative. | `app/services/age_category_service.py:130-167`; `app/services/specs/session_based/lfa_player_service.py:287-326`; `app/api/api_v1/endpoints/semester_enrollments/category_override.py:80-92`; executable | HIGH |
| FP-S08 | Parallel base/higher participation | No base/effective dual model. Tournament helper permits a base category to see higher categories, but records the player's derived category, not a separate assigned tournament category. Web bypasses category matching. | Some higher participation is possible, but not represented as audited dual participation. | `app/services/tournament/validation.py:17-104`; `app/api/api_v1/endpoints/tournaments/enroll.py:277-289`; web browse/enroll above | HIGH |
| FP-S09 | Other period calendars | PRE mini-periods are monthly; YOUTH are calendar quarters. Amateur/Pro templates describe Jul–Sep etc., while generator creates roughly July of year N through September of N+1 by combining Q1 template with next-year Q3 end. | Operational periods do not consistently equal academy season. | `app/services/semester_templates.py:12-106`; `app/api/api_v1/endpoints/periods/lfa_player_generators.py:134-157,234-259,310-353,404-447`; executable admin generators | HIGH |
| FP-S10 | Owner-approved principle | July 1→June 30; season/base category fixed by season; birthday does not trigger midseason base change; upward movement is explicit and audited; base remains historically preserved. | This is an owner decision, not a statement that repository already complies. | Owner decision supplied before this inventory | OWNER EVIDENCE |

## 2. LFA Coach

### 2.1 Program-wide rules

| ID | Rule | Current repository rule | Evidence | Reachability / type | Confidence |
|---|---|---|---|---|---|
| CO-01 | Minimum program-entry age | 14+ in config, shared helper, native card, parallel service, and coach service. | `config/specializations/lfa_coach.json:4-8`; `app/utils/age_requirements.py:69-78,97-103`; `app/services/parallel_specialization_service.py:20-42`; `app/services/specs/semester_based/lfa_coach_service.py:192,210-239`; `ios/LFAEducationCenter/App/MainHubView.swift:71-78` | Executable + native; runtime-reachable | HIGH |
| CO-02 | Maximum age | None found at program or level. | `config/specializations/lfa_coach.json:5,9-37`; coach service level table | Executable config/service | HIGH |
| CO-03 | Minor participation | Code permits age 14–17 on the central specialization-validation path only with `User.parental_consent=True`. | `app/services/specialization_validation.py:102-116`; `app/models/user.py:519-541` | Runtime-reachable central API | HIGH |
| CO-04 | Consent evidence | User stores boolean, timestamp, and free-text `parental_consent_by`; no guardian FK/identity or program-scoped consent record was found. | `app/models/user.py:209-225`; `app/schemas/user.py:30-42,57-73` | Executable model/schema | HIGH |
| CO-05 | Missing DOB | Generic specialization validator treats missing DOB as valid and warning-only; coach service's direct validator rejects missing DOB. | `app/services/specialization_validation.py:125-127,153-190`; `app/services/specs/semester_based/lfa_coach_service.py:210-239`; base service DOB validation | Executable service conflict | HIGH |
| CO-06 | Unlock/activation | Central `/specializations/me` enforces age/consent; bearer unlock enforces 14+ but not coach consent; web onboarding enforces neither. Coach-specific license route has no age/consent guard and calls service methods absent from current `LFACoachService`, so is mounted but not operational as declared. | `app/api/api_v1/endpoints/specializations/user.py:58-140`; `app/api/web_routes/specialization.py:42-194`; `app/api/web_routes/onboarding.py:66-156`; `app/api/api_v1/endpoints/coach/licenses.py:292-377`; `app/api/api_v1/api.py:173` | Runtime paths + declarative/broken route | HIGH |
| CO-07 | Renewal | Shared renewal service checks license, user, credits, period, and payment but no age or minor consent. Coach-specific progression renewal route calls service methods absent from current coach service. | `app/services/license_renewal_service.py:61-224`; `app/api/api_v1/endpoints/coach/progression.py`; route mount `app/api/api_v1/api.py:173,181` | Runtime shared service + declarative/broken coach route | HIGH |

### 2.2 All coach levels and target participant ages

The canonical JSON assigns **14** to every level. The coach service assigns progressive minimum coach ages. These are direct conflicts.

| Level | Name | Target group | JSON min coach age | Coach-service min coach age | Target participant age evidence | Teaching mode |
|---:|---|---|---:|---:|---|---|
| 1 | LFA Pre Football Asszisztens Edző / `PRE_ASSISTANT` | PRE | 14 | 14 | Config/service: 5–13; coach-level service label: U6–U8 | With Master supervision |
| 2 | LFA Pre Football Vezetőedző / `PRE_HEAD` | PRE | 14 | 16 | Config/service: 5–13; coach-level service label: U6–U8 | Independent |
| 3 | LFA Youth Football Asszisztens Edző / `YOUTH_ASSISTANT` | YOUTH | 14 | 16 | Config/service: 14–18; coach-level service label: U9–U12 | With Master supervision |
| 4 | LFA Youth Football Vezetőedző / `YOUTH_HEAD` | YOUTH | 14 | 18 | Config/service: 14–18; coach-level service label: U9–U12 | Independent |
| 5 | LFA Amateur Football Asszisztens Edző / `AMATEUR_ASSISTANT` | AMATEUR | 14 | 18 | Config/service: 14+; coach-level service label: U13–U16 | With Master supervision |
| 6 | LFA Amateur Football Vezetőedző / `AMATEUR_HEAD` | AMATEUR | 14 | 20 | Config/service: 14+; coach-level service label: U13–U16 | Independent |
| 7 | LFA PRO Football Asszisztens Edző / `PRO_ASSISTANT` | PRO | 14 | 21 | Config says target 14+; coach service says PRO target 16+; coach-level service says U17+ | With Master supervision |
| 8 | LFA PRO Football Vezetőedző / `PRO_HEAD` | PRO | 14 | 23 | Config says target 14+; coach service says PRO target 16+; coach-level service says U17+ | Independent |

**Level evidence:** JSON names/ages/exam thresholds at `config/specializations/lfa_coach.json:39-199`; progressive service ages and requirements at `app/services/specs/semester_based/lfa_coach_service.py:50-190`; target labels and minimum coach levels at `app/services/coach_level_service.py:18-23,158-180`; teaching mode at `app/services/teaching_permission_service.py:19-23,88-123`. Tests assert progressive service ages at `tests/integration/test_lfa_coach_service_simple.py:156-171` and `tests/unit/services/semester_based/test_lfa_coach_service.py:185-194`. Confidence: **HIGH**.

### 2.3 Promotion, qualification, instruction, and license conditions

| ID | Surface | Current repository rule | Evidence | Confidence |
|---|---|---|---|---|
| CO-P01 | Age check for target level | `validate_age_eligibility(user, target_group)` enforces progressive service minimum when explicitly called. | `app/services/specs/semester_based/lfa_coach_service.py:210-239` | HIGH |
| CO-P02 | Actual promotion | `certify_next_level` does not call age validation. It increments sequentially and accepts `exam_score=None`. | `app/services/specs/semester_based/lfa_coach_service.py:497-551`; tests `tests/unit/services/semester_based/test_lfa_coach_service.py:221-237` | HIGH |
| CO-P03 | Exam/qualification | JSON per-level pass thresholds are 60/70/65/75/70/75/80/85. Service hardcodes 80 only when a score is supplied. No separate exam age beyond level minimum was found. | `config/specializations/lfa_coach.json:50-195`; `app/services/specs/semester_based/lfa_coach_service.py:527-532` | HIGH |
| CO-P04 | Instructor activity | Odd levels teach with supervision; even levels independently. Permission service does not re-check age or parental consent. | `app/services/teaching_permission_service.py:19-23,88-123` | HIGH |
| CO-P05 | Who may promote | Service accepts `certified_by` as an integer and records it only in returned data; it does not load/authorize the actor or create history. Mounted coach API has its own role declarations but currently calls absent service adapters. | `app/services/specs/semester_based/lfa_coach_service.py:497-551`; `app/api/api_v1/endpoints/coach/progression.py` | HIGH |
| CO-P06 | License activation/renewal age | No age/consent revalidation found in shared renewal, session booking, or teaching permission. | `app/services/license_renewal_service.py`; `app/services/specs/semester_based/lfa_coach_service.py:245-285`; `app/services/teaching_permission_service.py` | HIGH |

## 3. GānCuju Player

### 3.1 Entry and participation

| ID | Rule | Current repository rule | Evidence | Reachability / type | Confidence |
|---|---|---|---|---|---|
| GC-01 | Program minimum | 5+ in config, GānCuju service, shared helper, native card, and legacy `PLAYER` parallel service. | `config/specializations/gancuju_player.json:5`; `app/services/specs/semester_based/gancuju_player_service.py:74,92-114`; `app/utils/age_requirements.py:38-47,97-103`; `app/services/parallel_specialization_service.py:20-42`; `ios/LFAEducationCenter/App/MainHubView.swift:62-68` | Executable + native; runtime-reachable | HIGH |
| GC-02 | Maximum age | None found. | `config/specializations/gancuju_player.json:5-8`; service | Executable | HIGH |
| GC-03 | Enrollment | Service requires DOB and calendar age 5+ when `get_enrollment_requirements` is used. Central generic validator skips missing DOB. | `app/services/specs/semester_based/gancuju_player_service.py:92-114,197-200`; `app/services/specialization_validation.py:153-190` | Runtime service conflict | HIGH |
| GC-04 | Session | Service checks active license, semester enrollment/payment, and specialization; it does not re-check age in `can_book_session`. | `app/services/specs/semester_based/gancuju_player_service.py:120-158` | Runtime-reachable service | HIGH |
| GC-05 | Competition | No age-specific GānCuju competition/session category or youth/adult split was found. Competition recording is license/progression based. | `app/api/api_v1/endpoints/gancuju/activities.py`; `app/services/specs/semester_based/gancuju_player_service.py` | Mounted API/service | HIGH |
| GC-06 | Unlock | Same split as other programs: central route/config and bearer helper require 5+; web onboarding has no age check. GānCuju-specific license route has no age guard and calls methods absent from the current service. | `app/api/api_v1/endpoints/specializations/user.py:58-140`; `app/api/web_routes/specialization.py:42-194`; `app/api/web_routes/onboarding.py:66-156`; `app/api/api_v1/endpoints/gancuju/licenses.py:220-390`; route mount `app/api/api_v1/api.py:171` | Runtime paths + declarative/broken route | HIGH |
| GC-07 | Legacy `PLAYER` | `PLAYER` maps to `GANCUJU_PLAYER` in two functions, but `handle_legacy_specialization` rejects it after 2026-05-18 while `specialization_id_to_enum` continues to map it. Parallel service still names `PLAYER` with min age 5. | `app/services/specialization/validation.py:16-21,30-92`; `app/services/parallel_specialization_service.py:20-42`; `app/services/specialization/lfa_player.py:1-33` (misleading legacy file maps to GānCuju) | Legacy/supporting, runtime-dependent | HIGH |

### 3.2 All belts/levels

There is no per-belt age key in the canonical JSON or service table. After program entry, every belt is **NO AGE LIMIT — PERFORMANCE/PROGRESSION BASED**.

| Level | Canonical belt | Color | Current age rule | Non-age progression evidence |
|---:|---|---|---|---|
| 1 | Bambusz Tanítvány / Bamboo Disciple | White | NO AGE LIMIT — PERFORMANCE/PROGRESSION BASED | Entry belt; theory/practice/skill requirements only |
| 2 | Hajnali Harmat / Morning Dew | Yellow | NO AGE LIMIT — PERFORMANCE/PROGRESSION BASED | Sequential next belt; requirements only |
| 3 | Rugalmas Nád / Flexible Reed | Green | NO AGE LIMIT — PERFORMANCE/PROGRESSION BASED | Sequential next belt; requirements only |
| 4 | Égi Folyó / Celestial River | Blue | NO AGE LIMIT — PERFORMANCE/PROGRESSION BASED | Sequential next belt; requirements only |
| 5 | Erős Gyökér / Strong Root | Brown | NO AGE LIMIT — PERFORMANCE/PROGRESSION BASED | Sequential next belt; requirements only |
| 6 | Téli Hold / Winter Moon | Grey | NO AGE LIMIT — PERFORMANCE/PROGRESSION BASED | Sequential next belt; requirements only |
| 7 | Éjfél Őrzője / Midnight Guardian | Black | NO AGE LIMIT — PERFORMANCE/PROGRESSION BASED | Sequential next belt; requirements only |
| 8 | Sárkány Bölcsesség / Dragon Wisdom | Red | NO AGE LIMIT — PERFORMANCE/PROGRESSION BASED | Highest/Grand Master; requirements only |

**Evidence:** full belt definitions in `config/specializations/gancuju_player.json:9-183`; service belt order/info at `app/services/specs/semester_based/gancuju_player_service.py:48-74`; sequential promotion at `app/services/specs/semester_based/gancuju_player_service.py:337-391`. The promotion service validates sequence and optional score range, but has no age requirement and no internal actor authorization; it writes progression history. Tests: `tests/unit/services/semester_based/test_gancuju_player_service.py`. Confidence: **HIGH**.

No separate age rule was found for promotion, belt exam, teaching activity, license renewal, competition, or completion. This means **absence of a repository rule**, not an owner-approved no-limit policy.

## 4. Internship

### 4.1 Entry and all 16+/18+ occurrences that affect the product

| ID | Surface | Current repository rule | Evidence | Reachability / type | Confidence |
|---|---|---|---|---|---|
| IN-01 | Canonical config entry | 16+; no maximum. All three config levels repeat `min_age: 16`. | `config/specializations/internship.json:5,9-102` | Loaded canonical config | HIGH |
| IN-02 | Internship service entry | 18+; DOB required; calendar age today. | `app/services/specs/semester_based/lfa_internship_service.py:45-48,200-227` | Runtime-reachable service | HIGH |
| IN-03 | Shared helper / native | 18+ in web helper and native main hub. | `app/utils/age_requirements.py:27-36,97-103`; `ios/LFAEducationCenter/App/MainHubView.swift:80-86` | Runtime helper + UI contract | HIGH |
| IN-04 | Parallel legacy service | `INTERNSHIP: 18` and display text “min. 18 év”. | `app/services/parallel_specialization_service.py:20-42` | Legacy/supporting runtime service | HIGH |
| IN-05 | Central generic enrollment | Uses loaded config, therefore 16+; missing DOB passes with warning. | `app/api/api_v1/endpoints/specializations/user.py:58-140`; `app/services/specialization_validation.py:89-127,153-190`; loader `app/services/specialization_config_loader.py:32-38` | Runtime-reachable API | HIGH |
| IN-06 | Bearer unlock | Uses shared helper, therefore 18+ and missing DOB rejects. | `app/api/web_routes/specialization.py:42-194`; `app/utils/age_requirements.py:83-109` | Runtime-reachable API | HIGH |
| IN-07 | Web onboarding unlock | No age or DOB check; can create Internship license if other conditions pass. | `app/api/web_routes/onboarding.py:66-156` | Runtime-reachable web | HIGH |
| IN-08 | Internship-specific license route | No age check; accepts requested initial credits/duration and invokes a `create_license` method absent from current `LFAInternshipService`, so route is mounted but not operational as declared. | `app/api/api_v1/endpoints/internship/licenses.py:311-390`; service class; route mount `app/api/api_v1/api.py:172` | Declarative/broken route | HIGH |
| IN-09 | Owner decision | Internship minimum is 18+. | Prior explicit owner decision (“3A elfogadva: 18+”) | Owner-approved, implementation not started | OWNER EVIDENCE |

No other executable 16+ Internship entry rule was found outside `internship.json`; other `16+` occurrences found in football/coach target labels and unrelated platform/runtime compatibility notes are not Internship policy.

### 4.2 Internship config levels versus service semesters

These are different structures today and there is no executable mapping between the three config levels and five service stages beyond generic numeric level fields.

#### Three canonical-config levels

| Config level | Name | Age rule | Other progression data | Evidence |
|---:|---|---|---|---|
| 1 | Startup Explorer | min 16; no separate max | XP/session/project/skill requirements | `config/specializations/internship.json:9-39` |
| 2 | Startup Contributor | min 16; no separate max | XP/session/project/skill requirements | `config/specializations/internship.json:40-71` |
| 3 | Startup Leader | min 16; no separate max | XP/session/project/skill requirements | `config/specializations/internship.json:72-102` |

#### Five service stages/semesters over eight numeric levels

| Semester | Service stage | Numeric levels | Age rule | Progression basis | Evidence |
|---:|---|---|---|---|---|
| 1 | INTERN JUNIOR | 1–2 | No stage-specific age; program service minimum 18 | XP, attendance, quizzes, thresholds | `app/services/specs/semester_based/lfa_internship_service.py:54-73` |
| 2 | INTERN MID-LEVEL | 3–4 | No stage-specific age; program service minimum 18 | XP, attendance, quizzes, thresholds | `app/services/specs/semester_based/lfa_internship_service.py:74-84` |
| 3 | INTERN SENIOR | 5–6 | No stage-specific age; program service minimum 18 | XP, attendance, quizzes, thresholds | `app/services/specs/semester_based/lfa_internship_service.py:85-95` |
| 4 | INTERN LEAD | 7 | No stage-specific age; program service minimum 18 | XP, attendance, quizzes, thresholds | `app/services/specs/semester_based/lfa_internship_service.py:96-106` |
| 5 | INTERN PRINCIPAL | 8 | No stage-specific age; program service minimum 18 | XP, attendance, quizzes, thresholds | `app/services/specs/semester_based/lfa_internship_service.py:107-117` |

| ID | Requested surface | Finding | Evidence / confidence |
|---|---|---|---|
| IN-P01 | Project/workplace activity | Config requires projects at levels 1–3, but no separate project/workplace minimum age was found. | `config/specializations/internship.json:9-102`; HIGH |
| IN-P02 | Instructor/admin approval | Semester/payment and some progression paths use approval/verification, but approval does not alter an explicit age threshold. | `app/services/specs/semester_based/lfa_internship_service.py:233-367`; HIGH |
| IN-P03 | Minor consent/guardian | No Internship-specific parental/guardian consent flow found. Under the 16+ config path, a 16–17-year-old can pass without such consent; under 18+ paths they cannot enter. | specialization validator + user consent model; HIGH |
| IN-P04 | Qualification/completion | No completion-specific age threshold found. The service describes five stages and performance thresholds only. | `app/services/specs/semester_based/lfa_internship_service.py:11-24,373+`; HIGH |
| IN-P05 | Renewal | Internship-specific renewal paths contain no age revalidation; several call adapters not present on the inspected service. Shared renewal also has no age guard. | `app/api/api_v1/endpoints/internship/xp_renewal.py:353-400`; `app/services/license_renewal_service.py`; HIGH |

## 5. Cross-program age rules

| ID | Global/shared rule | Current repository behavior | Affected programs | Evidence | Confidence |
|---|---|---|---|---|---|
| X-01 | Web account minimum/maximum | Registration and age-verification require calendar age 5–120. Profile DOB edit repeats 5–120. | All web users | `app/api/web_routes/auth.py:155-216,263-339`; `app/api/web_routes/profile.py:480-575` | HIGH |
| X-02 | API/admin account creation | Invitation API requires a DOB but applies no age/future-date bound. `UserCreate.date_of_birth` is optional, and admin may set specialization directly with no age/consent validation. | All | `app/api/api_v1/endpoints/auth.py:221-240,292-367`; `app/schemas/user.py:30-42`; `app/api/api_v1/endpoints/users/crud.py:21-77` | HIGH |
| X-03 | DOB storage/calculation | DB DOB is nullable. `User.age` and API schema age use calendar age today. Missing DOB makes `is_minor=False` in the model. | All | `app/models/user.py:39,519-541`; `app/schemas/user.py:127-137` | HIGH |
| X-04 | Shared specialization minimums | Helper: football 5, GānCuju 5, Coach 14, Internship 18; unknown/missing age rejects. | All four | `app/utils/age_requirements.py:8-109` | HIGH |
| X-05 | Config validation | Loads canonical JSON minimums: football 5, GānCuju 5, Coach 14, Internship 16. Missing DOB passes with warning. | All four | `app/services/specialization_validation.py:89-139,153-218`; `app/services/specialization_config_loader.py:32-38` | HIGH |
| X-06 | Parental/guardian consent | Only Coach has program-specific consent rule. Storage is a boolean plus free text/timestamp, not a guardian identity workflow. | Coach only | `app/services/specialization_validation.py:102-116`; `app/models/user.py:209-225` | HIGH |
| X-07 | Biometric adult-only gate | Disclosure acceptance requires calendar age 18+ and treats missing DOB as potentially minor. Parental consent cannot bypass. Native maps 403 `parental_consent_required`. | Any program using biometrics | `app/services/biometric/disclosure_service.py:46-62`; `app/api/api_v1/endpoints/users/biometric_disclosure.py:101`; `ios/LFAEducationCenter/Biometric/BiometricModels.swift:155-210` | HIGH |
| X-08 | Juggling/video consent | Service/training/admin-review consent exists, but no age/guardian threshold was found in that consent path. | Mainly football features, structurally global user route | `app/api/api_v1/endpoints/users/juggling_consent.py:1-70`; `app/api/api_v1/endpoints/users/juggling_videos.py:203-206` | HIGH |
| X-09 | Session enrollment | Specialization services expose age checks in enrollment requirements, but their `can_book_session` methods generally check license/enrollment/payment and do not revalidate current age. Football has additional category logic. | All four | the four specialization services' `get_enrollment_requirements` and `can_book_session` methods | HIGH |
| X-10 | Credit/unlock age guard | Credit balance itself has no age guard. Age is enforced or omitted by unlock entry point: bearer helper yes, central JSON path yes-but-missing-DOB-pass, web onboarding no. | All four | `app/api/web_routes/specialization.py`; `app/api/api_v1/endpoints/specializations/user.py`; `app/api/web_routes/onboarding.py` | HIGH |
| X-11 | License purchase/renewal | Shared renewal has no age or guardian check. Program-specific license routes also do not consistently validate age and some are not operational due to missing adapters. | All four | `app/services/license_renewal_service.py`; program endpoint modules | HIGH |
| X-12 | Competition/tournament | Football has conflicting API/web category policies. No program-specific age tournament restriction was found for Coach, GānCuju, or Internship. | Primarily football | tournament sources in FP-06–FP-08 | HIGH |
| X-13 | Instructor age | Coach program/level ages exist, but teaching permission does not revalidate current age or consent. Instructor role itself has no global minimum age guard. | Coach/instructors | coach service + `app/services/teaching_permission_service.py` | HIGH |

## 6. AGE POLICY CONFLICT REGISTER

| ID | Program | Rule | Source A / value A | Source B / value B | Additional source(s) | Runtime impact |
|---|---|---|---|---|---|---|
| AC-01 | Football | Program minimum | Canonical config/helper/native: 5+ | Session service: 6+ | Tests assert both surfaces independently | Age-5 user may unlock but fails player-service eligibility. |
| AC-02 | Football | PRE bounds | Config/season service: 5–13 | Session service: 6–11 | Public card: `<7`; coach-level label: U6–U8 | Category changes by route/display. |
| AC-03 | Football | YOUTH bounds | Config/season service: 14–18 | Session service: 12–18 | Canonical L3/L4 `min_age: 9`; public card 7–14 | Same DOB maps to different category/level eligibility. |
| AC-04 | Football | Adult assignment | Config: AMATEUR/PRO 14+ | Season auto: 14–18 YOUTH, >18 manual | API tournament may auto-choose sole adult group/default AMATEUR | Adult category authority differs. |
| AC-05 | Football | Season vs calendar age | Season enrollment/API tournament use July-1 age | player service/web helper/cards use current calendar age | — | Birthday can change some behavior midseason. |
| AC-06 | Football | Season persistence | Narrative says locked July–June | Only one mutable enrollment category exists; no base field/history | Enrollment calculates current season on creation only | Lock/history cannot be fully demonstrated. |
| AC-07 | Football | Upward assignment authorization | Override: any instructor/admin; 14+ | Tournament helper: player self-service upward | Player service method: no actor check | Three policies and actor sets. |
| AC-08 | Football | Direction | Docs say upward YOUTH→AMATEUR/PRO | validator accepts any category for 14+; session booking allows downward | admin bypasses under-14 restriction | Downward movement is executable. |
| AC-09 | Football | Web/API tournament | API derives/requires category match | Web does not check tournament category and defaults AMATEUR | Stale helper/docs advertise broader upward policy than API body | Consumer-dependent competition eligibility. |
| AC-10 | Football | User-license representation | Enum says unified `LFA_FOOTBALL_PLAYER` only | player service reads/writes `LFA_PLAYER_{category}` on user license | P0 canonical entitlement policy | Service can create forbidden representation. |
| AC-11 | Football | Season duration | Academy/age model: Jul 1–Jun 30 | PRE monthly; YOUTH calendar-quarter mini periods | Amateur/Pro generator creates ~15-month period | “Season” has several incompatible operational meanings. |
| AC-12 | Coach | Per-level coach age | JSON: every level 14 | Service: 14/16/16/18/18/20/21/23 | Tests assert service ladder | Promotion/qualification policy depends on caller/source. |
| AC-13 | Coach | Target participant bands | Config/service: PRE5–13, YOUTH14–18, AMATEUR14+ | coach-level service: U6–U8, U9–U12, U13–U16 | PRO: config14+, service16+, label U17+ | Teaching/tournament labels disagree. |
| AC-14 | Coach | Consent | Central path: consent required for 14–17 | bearer unlock: age only | web onboarding: neither age nor consent | Minors can obtain entitlement through some routes. |
| AC-15 | Coach | Missing DOB | Generic config validator passes with warning | coach direct validator rejects | admin creation can omit DOB | Eligibility varies by path. |
| AC-16 | Coach | Promotion age/exam | Validator has progressive age table; JSON has per-level exam thresholds | `certify_next_level` skips age and allows missing score; hardcodes 80 if present | — | Declared qualification rules are not enforced by promotion method. |
| AC-17 | GānCuju | Missing DOB | Generic validator passes with warning | direct GānCuju service rejects | web onboarding skips age | Entry policy varies by path. |
| AC-18 | GānCuju | Legacy PLAYER | enum conversion maps PLAYER | deprecation handler rejects after 2026-05-18 | parallel service still uses PLAYER | Legacy requests differ by code path. |
| AC-19 | Internship | Minimum entry age | Canonical config/central validator: 16+ | service/helper/native/parallel: 18+ | owner has approved 18+, implementation not started | 16–17 eligibility depends on entry point. |
| AC-20 | Internship | Missing DOB / unlock | central config validator passes missing DOB | helper/direct service reject | web onboarding performs no check | Eligibility differs across web/API. |
| AC-21 | Internship | Progression structure | canonical config: 3 levels | service/model narrative: 5 semesters / 8 numeric levels | enum doc says 3 levels | No approved mapping or completion meaning. |
| AC-22 | Cross-program | Platform minimum | Web registration/profile: 5–120 | API/admin UserCreate: DOB optional, no age bounds | DB DOB nullable | No universal account-age invariant. |
| AC-23 | Cross-program | Minor semantics | `User.is_minor`: missing DOB is false | biometric: missing DOB treated potentially minor and denied | generic specialization: missing DOB passes | Unknown age has three different meanings. |
| AC-24 | Cross-program | Unlock policy | central config route | bearer helper route | web onboarding route | Different age/consent business rules create the same entitlement. |

## 7. Owner review table

`UNDECIDED` means this inventory makes no recommendation. `OWNER-APPROVED` records only decisions already made before this inventory.

| Program | Category / Level / Rule | Current repository rule(s) | Proposed canonical value | OWNER |
|---|---|---|---|---|
| Football Player | Program minimum | 5+ config/helper/native; 6+ player service | UNDECIDED | ⬜ |
| Football Player | Program maximum | None | UNDECIDED | ⬜ |
| Football Player | Enrollment vs license vs training vs competition | Different checks described in FP-03–FP-08 | UNDECIDED | ⬜ |
| Football Player | PRE | 5–13 config/season; 6–11 service; `<7` card; U6–U8 coach label | UNDECIDED | ⬜ |
| Football Player | YOUTH | 14–18 config/season; 12–18 service; L3/L4 min 9; card 7–14 | UNDECIDED | ⬜ |
| Football Player | AMATEUR | 14+; auto/manual/default behavior differs | UNDECIDED | ⬜ |
| Football Player | PRO | 14+ config; service promotion-only; target labels also 16+/U17+ | UNDECIDED | ⬜ |
| Football Player | Level 1 | PRE; min 5 in JSON | UNDECIDED | ⬜ |
| Football Player | Level 2 | PRE; min 5 in JSON | UNDECIDED | ⬜ |
| Football Player | Level 3 | YOUTH; min 9 in JSON vs category 14 | UNDECIDED | ⬜ |
| Football Player | Level 4 | YOUTH; min 9 in JSON vs category 14 | UNDECIDED | ⬜ |
| Football Player | Level 5 | AMATEUR; min 14 | UNDECIDED | ⬜ |
| Football Player | Level 6 | AMATEUR; min 14 | UNDECIDED | ⬜ |
| Football Player | Level 7 | PRO; min 14 | UNDECIDED | ⬜ |
| Football Player | Level 8 | PRO; min 14 | UNDECIDED | ⬜ |
| Football Player | Season start/end | Repository evidence Jul 1–Jun 30 | **Jul 1–Jun 30 — OWNER-APPROVED** | ☑ |
| Football Player | Season/base category | Repository uses season age but stores one mutable category | **Fixed for season and historically preserved — OWNER-APPROVED** | ☑ |
| Football Player | Birthday effect | Season routes none; calendar-age routes can change behavior | **No automatic midseason base change — OWNER-APPROVED** | ☑ |
| Football Player | Upward movement | Several conflicting actor/direction policies | **Explicit, authorized, audited upward assignment — OWNER-APPROVED principle; actor matrix UNDECIDED** | ◩ |
| Football Player | Downward movement | Executable in validator/session/admin bypass | UNDECIDED | ⬜ |
| Football Player | Parallel base/higher participation | Tournament-only partial behavior; no dual representation | UNDECIDED | ⬜ |
| Football Player | Promotion authorization | Any instructor/admin, self-service tournament, or unchecked service | UNDECIDED | ⬜ |
| Coach | Program minimum | 14+ across main sources | UNDECIDED | ⬜ |
| Coach | Program maximum | None | UNDECIDED | ⬜ |
| Coach | Minor/guardian consent | 14–17 allowed with boolean consent on one path; bypasses elsewhere | UNDECIDED | ⬜ |
| Coach | Level 1 PRE Assistant | JSON 14; service 14 | UNDECIDED | ⬜ |
| Coach | Level 2 PRE Head | JSON 14; service 16 | UNDECIDED | ⬜ |
| Coach | Level 3 Youth Assistant | JSON 14; service 16 | UNDECIDED | ⬜ |
| Coach | Level 4 Youth Head | JSON 14; service 18 | UNDECIDED | ⬜ |
| Coach | Level 5 Amateur Assistant | JSON 14; service 18 | UNDECIDED | ⬜ |
| Coach | Level 6 Amateur Head | JSON 14; service 20 | UNDECIDED | ⬜ |
| Coach | Level 7 PRO Assistant | JSON 14; service 21 | UNDECIDED | ⬜ |
| Coach | Level 8 PRO Head | JSON 14; service 23 | UNDECIDED | ⬜ |
| Coach | PRE target participant band | 5–13 vs U6–U8 | UNDECIDED | ⬜ |
| Coach | YOUTH target participant band | 14–18 vs U9–U12 | UNDECIDED | ⬜ |
| Coach | AMATEUR target participant band | 14+ vs U13–U16 | UNDECIDED | ⬜ |
| Coach | PRO target participant band | 14+ vs 16+ vs U17+ | UNDECIDED | ⬜ |
| Coach | Promotion age enforcement | Progressive ages declared, not enforced by promotion method | UNDECIDED | ⬜ |
| Coach | Exam/qualification | JSON thresholds vary; service optional score/hardcoded 80 | UNDECIDED | ⬜ |
| Coach | Instructor activity age | No re-check after entitlement; level/supervision only | UNDECIDED | ⬜ |
| Coach | Activation/renewal age | No consistent revalidation | UNDECIDED | ⬜ |
| GānCuju | Program minimum | 5+ | UNDECIDED | ⬜ |
| GānCuju | Program maximum | None | UNDECIDED | ⬜ |
| GānCuju | Level 1 Bamboo Disciple | No belt age; performance/progression based | UNDECIDED | ⬜ |
| GānCuju | Level 2 Morning Dew | No belt age; performance/progression based | UNDECIDED | ⬜ |
| GānCuju | Level 3 Flexible Reed | No belt age; performance/progression based | UNDECIDED | ⬜ |
| GānCuju | Level 4 Celestial River | No belt age; performance/progression based | UNDECIDED | ⬜ |
| GānCuju | Level 5 Strong Root | No belt age; performance/progression based | UNDECIDED | ⬜ |
| GānCuju | Level 6 Winter Moon | No belt age; performance/progression based | UNDECIDED | ⬜ |
| GānCuju | Level 7 Midnight Guardian | No belt age; performance/progression based | UNDECIDED | ⬜ |
| GānCuju | Level 8 Dragon Wisdom | No belt age; performance/progression based | UNDECIDED | ⬜ |
| GānCuju | Promotion age/exam | No age; sequential/performance rules only | UNDECIDED | ⬜ |
| GānCuju | Session/competition age | No separate youth/adult or session/competition age rule | UNDECIDED | ⬜ |
| GānCuju | Legacy PLAYER alias age | PLAYER 5+ where accepted; alias deadline conflict | UNDECIDED | ⬜ |
| Internship | Program minimum | Config 16; service/helper/native 18; web onboarding none | **18+ — OWNER-APPROVED** | ☑ |
| Internship | Program maximum | None | UNDECIDED | ⬜ |
| Internship | Config Level 1 Startup Explorer | min 16 | UNDECIDED mapping to approved 18+ | ⬜ |
| Internship | Config Level 2 Startup Contributor | min 16 | UNDECIDED mapping to approved 18+ | ⬜ |
| Internship | Config Level 3 Startup Leader | min 16 | UNDECIDED mapping to approved 18+ | ⬜ |
| Internship | Semester 1 Intern Junior (L1–2) | no stage age; service program 18 | UNDECIDED | ⬜ |
| Internship | Semester 2 Intern Mid-Level (L3–4) | no stage age; service program 18 | UNDECIDED | ⬜ |
| Internship | Semester 3 Intern Senior (L5–6) | no stage age; service program 18 | UNDECIDED | ⬜ |
| Internship | Semester 4 Intern Lead (L7) | no stage age; service program 18 | UNDECIDED | ⬜ |
| Internship | Semester 5 Intern Principal (L8) | no stage age; service program 18 | UNDECIDED | ⬜ |
| Internship | 3-level ↔ 5-semester mapping | No canonical executable mapping | UNDECIDED | ⬜ |
| Internship | Project/workplace age | No separate rule found | UNDECIDED | ⬜ |
| Internship | Instructor/admin approval effect | Does not alter an explicit age rule | UNDECIDED | ⬜ |
| Internship | Minor/guardian consent | No Internship-specific flow | UNDECIDED | ⬜ |
| Internship | Qualification/completion age | No separate rule found | UNDECIDED | ⬜ |
| Cross-program | Minimum account age | Web 5; API/admin can omit DOB | UNDECIDED | ⬜ |
| Cross-program | Maximum account age | Web 120; API/admin no bound | UNDECIDED | ⬜ |
| Cross-program | Unknown DOB semantics | pass, reject, or “not minor” depending path | UNDECIDED | ⬜ |
| Cross-program | Biometric age | 18+; unknown denied; no parental bypass | UNDECIDED | ⬜ |
| Cross-program | Session age revalidation | Inconsistent; generally not repeated at booking | UNDECIDED | ⬜ |
| Cross-program | Unlock age policy | Three different entry-point policies | UNDECIDED | ⬜ |
| Cross-program | Renewal age policy | No age/consent revalidation | UNDECIDED | ⬜ |

## 8. Inventory counts and limits

- Football Player rule groups found: **25** (8 entry/participation, 4 categories, 8 numeric levels, 5 additional season/movement concerns; detailed season register contains 10 rows with overlap).
- Coach rule groups found: **21** (7 program-wide, 8 levels, 6 promotion/qualification/activity/license conditions).
- GānCuju rule groups found: **16** (7 entry/participation/legacy, 8 belts, 1 promotion policy group).
- Internship rule groups found: **18** (9 entry occurrences/policies, 3 config levels, 5 service semesters, 1 grouped activity/consent/completion review; detailed subregister expands the latter).
- Cross-program rule groups found: **13**.
- Conflicts found: **24**.
- Owner-review rows: **76** total; **71 unresolved**, **4 fully owner-approved**, **1 principle approved with actor matrix unresolved**. Thus **72 rows still require owner action** when the unresolved actor matrix is included.

### Negative findings

No repository rule was found for: a universal API account minimum; a global guardian identity workflow; GānCuju belt-specific ages; GānCuju youth/adult split; Internship semester-specific ages; Internship project/workplace-specific age; completion-specific ages; or consistent age/consent revalidation on renewal. A negative finding means the inspected repository contains no such rule; it is not a product recommendation.

### Scope assurance

This artifact records repository state and owner decisions only. It does not modify the accepted WS1 specification, application code, schema, migration history, or data. No WS1 implementation was started.
