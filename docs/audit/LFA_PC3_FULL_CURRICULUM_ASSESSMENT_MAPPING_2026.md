# LFA PC3 Sample Curriculum to Existing Assessment Mapping 2026

**Status:** sample-content mapping sufficient for PC3; implementation has not started

**Canonical baseline:** `LFA_CANONICAL_BASELINE_PC2` → `382b5698eb0252194ce136992918496c78f864aa`

**Audit branch at start:** `codex/pc3-player-content-mapping` → `1de4120f3baf5b1ff56f982189bda329b8526436`

**Program:** `LFA_FOOTBALL_PLAYER`

**Canonical rule:** the assessment corpus does not define the size or final professional structure of the future curriculum. For PC3, the owner-accepted 31-file sample mapping validates architecture and extensibility; later curriculum growth is a versioned content operation.

## Result

The current corpus is test/seed content rather than the complete LFA Football Player curriculum. That is sufficient for PC3 because it exercises one Player Track, seven sample Modules, 21 sample Lessons, 23 logical English assessment sets, eight proven Hungarian localization variants, multiple difficulties, missing-locale fallback, adaptive metadata, and taxonomy mapping.

All 31 files have a sample curriculum placement. Fifteen English files occupy 13 owner-defined Lesson slots, including the three difficulty placements under one `Rules → Laws of the Game` Lesson. The other 16 files form eight accepted EN/HU Lesson-localization pairs and use their current corpus grouping as sample content data.

The missing complete book, final Track/Module/Lesson inventory, full assessment coverage, and complete locale coverage are **EXPECTED FUTURE CONTENT — NOT A BLOCKER**. The sample graph does not constrain the number, names, or order of future Player content.

## Scope and method

The audit inspected:

- the complete PC2 baseline tree and current PC3 documentation branch;
- all reachable local and remote Git refs and object paths for curriculum, book, lesson, training-theory, and LFA Player material;
- the history of the 31 current assessment JSON files;
- the legacy Player curriculum seed and historical curriculum completion document;
- the historical 68-pair training-theory assessment commit;
- nearby LFA project documents in the same local Seafile environment, read-only, to determine whether they were the missing curriculum book.

No application code, assessment content, database row, migration, remote branch, deployment, or publication was changed. No content was generated.

## Evidence standard

A source qualifies as the future complete-curriculum authority only if it establishes all of the following:

- it is the approved `LFA_FOOTBALL_PLAYER` curriculum or book;
- it has an identifiable version or approval state;
- it enumerates Track, Module, Lesson, title, and order;
- it is independent of quiz/adaptive corpus metadata.

Filename order, JSON `module`/`topic`, question wording, title similarity, legacy seed rows, and generated assessment directory layout are insufficient to define the future complete curriculum. They may be used as explicitly accepted sample-fixture metadata for PC3 tests.

## Source discovery register

| Candidate evidence | Location / identifier | Finding | Authority result |
|---|---|---|---|
| Approved Player book or TOC in PC2 baseline | Git tree at `382b5698eb0252194ce136992918496c78f864aa` | No PDF, DOC, DOCX, ODT, EPUB, Pages, RTF, curriculum manifest, or book TOC for `LFA_FOOTBALL_PLAYER` | **EXPECTED FUTURE CONTENT — NOT A BLOCKER** |
| Current 31-file corpus | `content/adaptive_learning/lfa_football_player/**` plus `content/adaptive_learning/_shared/sports_physiology/**` | Assessment JSON only; all files expose quiz metadata and questions, not an approved curriculum release | Assessment source only |
| Initial JSON seeding commit | `2997f85472fe8b09e7530b0de09ee6e2f11f1bb0` | Commit describes JSON-based Adaptive Learning question seeding and seven quiz files | Assessment source only |
| Multilingual corpus commit | `beed8c4906445bea9989ebc1d577717756f91616` | Commit describes eight HU and eight EN question-bank files | Assessment source only |
| Metadata normalization commit | `c91f8c4e61448f7d3946e501cbc025cb008775d5` | Changes `quiz_title` and `language` so the adaptive-learning filter can find files | Runtime metadata, not curriculum |
| Additional HARD content commit | `5cc225eebb071174cd7f9c09e67322ad1fd40946` | Adds Football Awareness and Athlete Nutrition assessment files | Assessment source only |
| Historical L01–L68 branch | commit `c7c975c6635757cfce6b400039f5ab2f468739b4`, reachable only from `backup/pr110-remote-8a47124` | Adds 68 HU and 68 EN assessment JSONs and calls their directory layout “modules”; no independent book or curriculum manifest is included; commit is not an ancestor of PC2 | **REJECTED AS CURRICULUM AUTHORITY** |
| Legacy raw-SQL Player seed | `scripts/seed_player_curriculum.py:20-23`, `31-153` | Uses legacy specialization `PLAYER`, names the Track `GanCuju Player Development Program`, and creates Ganball/challenge Lessons | **LEGACY GĀNCUJU; NOT LFA FOOTBALL PLAYER** |
| Historical “complete curriculum” document | Git blob `4a592a7f33bf52be4a36e75a141907adac37482c` (`TELJES_TANANYAG_RENDSZER_100_PERCENT.md`) | Describes four `PLAYER`/GanCuju Lessons and legacy raw-SQL tables | **LEGACY; NOT CANONICAL BOOK** |
| `LFA_Craft.docx` | adjacent Seafile project, outside repository | Product/Ganball working draft; no approved Track → Module → Lesson TOC | Rejected |
| `Lion Football Academy.docx` | adjacent Seafile project, outside repository | Program/marketing description and service list; no curriculum Lesson sequence | Rejected |
| MLSZ training catalogue DOCX | adjacent Seafile project, outside repository | Third-party coach/adult-training catalogue, not the LFA Football Player curriculum | Rejected |

## Owner-approved mapping constraints

The following decisions are authoritative for the PC3 sample mapping:

- the eight proven EN/HU pairs represent the same Lesson localization on each pair;
- `Football Awareness` replaces legacy `General` as the canonical Module;
- Football Awareness has three separate Lessons;
- Rules has one Lesson, `Laws of the Game`, with Easy, Medium, and Hard as three separate `LessonAssessment` placements;
- Tactics has three separate Lessons;
- Conditioning has three separate Lessons;
- `Athlete Nutrition` replaces legacy `Nutrition` and has three separate Lessons;
- missing Hungarian assessment content does not block English base publication and remains localization backlog;
- `concept_tags` must become language-neutral taxonomy keys with localized display labels.

These decisions define the sample assessment placements. They do not claim a complete book TOC, final cross-Module order, or final Lesson inventory.

## Owner-approved Lesson slots and assessment placements

The stable sample Track key is `lfa-football-player-pc3-sample`. Lesson order is deterministic within each sample Module and remains ordinary versioned content configuration rather than a schema or final-curriculum decision.

The three Football Awareness titles and `Laws of the Game` were named directly in the owner decision. The Tactics, Conditioning, and Athlete Nutrition titles below are current English corpus-topic labels used for the sample release. Future editorial releases may change those display titles without code or schema change.

| Track | Module | Lesson order | Lesson title / current corpus label | Source / book evidence | Assessment JSON | Assessment type / difficulty | EN / HU | Missing assessment | Mapping confidence |
|---|---|---:|---|---|---|---|---|---|---|
| `lfa-football-player-pc3-sample` | Football Awareness | 1 | Football Fundamentals | Owner decision | `content/adaptive_learning/lfa_football_player/general/football_awareness_easy.json` | LessonAssessment / EASY | EN yes; HU missing | No | ACCEPTED SAMPLE |
| `lfa-football-player-pc3-sample` | Football Awareness | 2 | Football Governance and Competition | Owner decision | `content/adaptive_learning/lfa_football_player/general/football_awareness_medium.json` | LessonAssessment / MEDIUM | EN yes; HU missing | No | ACCEPTED SAMPLE |
| `lfa-football-player-pc3-sample` | Football Awareness | 3 | Football Governance and Global Structures | Owner decision | `content/adaptive_learning/lfa_football_player/general/football_awareness_hard.json` | LessonAssessment / HARD | EN yes; HU missing | No | ACCEPTED SAMPLE |
| `lfa-football-player-pc3-sample` | Rules | 1 | Laws of the Game | Owner decision | `content/adaptive_learning/lfa_football_player/lesson/rules_easy.json`; `rules_medium.json`; `rules_hard.json` | 3 LessonAssessments / EASY, MEDIUM, HARD | EN yes; HU missing | No | ACCEPTED SAMPLE |
| `lfa-football-player-pc3-sample` | Tactics | 1 | Formations and Basic Principles | Owner decision: separate Lesson; title from current corpus | `content/adaptive_learning/lfa_football_player/lesson/tactics_easy.json` | LessonAssessment / EASY | EN yes; HU missing | No | ACCEPTED SAMPLE |
| `lfa-football-player-pc3-sample` | Tactics | 2 | Formations and Systems of Play | Owner decision: separate Lesson; title from current corpus | `content/adaptive_learning/lfa_football_player/lesson/tactics_medium.json` | LessonAssessment / MEDIUM | EN yes; HU missing | No | ACCEPTED SAMPLE |
| `lfa-football-player-pc3-sample` | Tactics | 3 | Advanced Tactics | Owner decision: separate Lesson; title from current corpus | `content/adaptive_learning/lfa_football_player/lesson/tactics_hard.json` | LessonAssessment / HARD | EN yes; HU missing | No | ACCEPTED SAMPLE |
| `lfa-football-player-pc3-sample` | Conditioning | 1 | Physical Conditioning Fundamentals | Owner decision: separate Lesson; title from current corpus | `content/adaptive_learning/_shared/sports_physiology/conditioning_easy.json` | LessonAssessment / EASY | EN yes; HU missing | No | ACCEPTED SAMPLE |
| `lfa-football-player-pc3-sample` | Conditioning | 2 | Physical Conditioning Applied | Owner decision: separate Lesson; title from current corpus | `content/adaptive_learning/_shared/sports_physiology/conditioning_medium.json` | LessonAssessment / MEDIUM | EN yes; HU missing | No | ACCEPTED SAMPLE |
| `lfa-football-player-pc3-sample` | Conditioning | 3 | Advanced Physiology and Periodization | Owner decision: separate Lesson; title from current corpus | `content/adaptive_learning/_shared/sports_physiology/conditioning_hard.json` | LessonAssessment / HARD | EN yes; HU missing | No | ACCEPTED SAMPLE |
| `lfa-football-player-pc3-sample` | Athlete Nutrition | 1 | Foundations of Sports Nutrition | Owner decision: separate Lesson; title from current corpus | `content/adaptive_learning/lfa_football_player/nutrition/athlete_nutrition_easy.json` | LessonAssessment / EASY | EN yes; HU missing | No | ACCEPTED SAMPLE |
| `lfa-football-player-pc3-sample` | Athlete Nutrition | 2 | Advanced Sports Nutrition | Owner decision: separate Lesson; title from current corpus | `content/adaptive_learning/lfa_football_player/nutrition/athlete_nutrition_medium.json` | LessonAssessment / MEDIUM | EN yes; HU missing | No | ACCEPTED SAMPLE |
| `lfa-football-player-pc3-sample` | Athlete Nutrition | 3 | Elite Nutrition Periodization | Owner decision: separate Lesson; title from current corpus | `content/adaptive_learning/lfa_football_player/nutrition/athlete_nutrition_hard.json` | LessonAssessment / HARD | EN yes; HU missing | No | ACCEPTED SAMPLE |

This table records **13 owner-approved distinct sample Lesson slots** for **15 JSON files**. It is not the full curriculum tree; future display-title changes are normal versioned content operations.

## Accepted bilingual sample Lesson groups

The owner approved the localization relationship inside each pair. For PC3 validation, current corpus grouping supplies sample-only Module/Lesson display metadata and stable mapping keys. These are eight logical assessment sets represented by 16 files; they do not define the future complete curriculum.

| Pair | Corpus label only | EN assessment | HU assessment | Questions per locale | Approved fact | Status |
|---:|---|---|---|---:|---|---|
| 1 | Physical Culture Background / Testkulturális előzmények | `content/adaptive_learning/lfa_football_player/en/lesson/lesson_01_physical_culture_background_easy.json` | `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_01_testkulturalis_elozmenyek_easy.json` | 15 | Same Lesson localization pair | `ACCEPTED SAMPLE PLACEMENT` |
| 2 | Civilizational Development and Lifestyle | `content/adaptive_learning/lfa_football_player/en/lesson/lesson_02_civilizational_development_easy.json` | `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_02_bevezetes_civilizacios_problemak_easy.json` | 14 | Same Lesson localization pair | `ACCEPTED SAMPLE PLACEMENT` |
| 3 | Concept of Training | `content/adaptive_learning/lfa_football_player/en/lesson/lesson_03_concept_of_training_easy.json` | `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_03_edzes_fogalma_easy.json` | 15 | Same Lesson localization pair | `ACCEPTED SAMPLE PLACEMENT` |
| 4 | Load and Adaptation | `content/adaptive_learning/lfa_football_player/en/lesson/lesson_04_load_and_adaptation_easy.json` | `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_04_terheles_adaptacio_easy.json` | 15 | Same Lesson localization pair | `ACCEPTED SAMPLE PLACEMENT` |
| 5 | Components of Training Load | `content/adaptive_learning/lfa_football_player/en/lesson/lesson_05_training_load_components_easy.json` | `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_05_edzesterheles_osszetevoi_easy.json` | 15 | Same Lesson localization pair | `ACCEPTED SAMPLE PLACEMENT` |
| 6 | Structure of Training | `content/adaptive_learning/lfa_football_player/en/lesson/lesson_06_structure_of_training_easy.json` | `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_06_edzes_felepitese_easy.json` | 14 | Same Lesson localization pair | `ACCEPTED SAMPLE PLACEMENT` |
| 7 | Training Principles | `content/adaptive_learning/lfa_football_player/en/lesson/lesson_07_training_principles_easy.json` | `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_07_edzeselvek_easy.json` | 14 | Same Lesson localization pair | `ACCEPTED SAMPLE PLACEMENT` |
| 8 | Motor Abilities | `content/adaptive_learning/lfa_football_player/en/lesson/lesson_08_motor_abilities_easy.json` | `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_08_motoros_kepessegek_easy.json` | 15 | Same Lesson localization pair | `ACCEPTED SAMPLE PLACEMENT` |

The numeric filename prefix is retained as deterministic sample-fixture order only. It is not accepted as the order of the future complete curriculum.

## Complete 31-file assessment disposition

| File | Language | Category | Difficulty | Current JSON module | Current JSON topic | Questions | Curriculum disposition |
|---|---|---|---|---|---|---:|---|
| `content/adaptive_learning/_shared/sports_physiology/conditioning_easy.json` | en | SPORTS_PHYSIOLOGY | EASY | Conditioning | Physical Conditioning Fundamentals | 10 | Owner-approved home: Conditioning → Physical Conditioning Fundamentals |
| `content/adaptive_learning/_shared/sports_physiology/conditioning_hard.json` | en | SPORTS_PHYSIOLOGY | HARD | Conditioning | Advanced Physiology & Periodization | 10 | Owner-approved home: Conditioning → Advanced Physiology and Periodization |
| `content/adaptive_learning/_shared/sports_physiology/conditioning_medium.json` | en | SPORTS_PHYSIOLOGY | MEDIUM | Conditioning | Physical Conditioning Applied | 9 | Owner-approved home: Conditioning → Physical Conditioning Applied |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_01_physical_culture_background_easy.json` | en | LESSON | EASY | Introduction | Physical Culture Background | 15 | `ACCEPTED SAMPLE PLACEMENT` |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_02_civilizational_development_easy.json` | en | LESSON | EASY | Introduction | The Response to Lifestyle Problems Caused by Civilizational Development | 14 | `ACCEPTED SAMPLE PLACEMENT` |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_03_concept_of_training_easy.json` | en | LESSON | EASY | Fundamentals of Training Theory | The Concept and Interpretation of Training | 15 | `ACCEPTED SAMPLE PLACEMENT` |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_04_load_and_adaptation_easy.json` | en | LESSON | EASY | Fundamentals of Training Theory | The Relationship Between Load and Adaptation | 15 | `ACCEPTED SAMPLE PLACEMENT` |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_05_training_load_components_easy.json` | en | LESSON | EASY | Fundamentals of Training Theory | Components of Training Load | 15 | `ACCEPTED SAMPLE PLACEMENT` |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_06_structure_of_training_easy.json` | en | LESSON | EASY | Fundamentals of Training Theory | Structure of Training (Parts of a Training Session) | 14 | `ACCEPTED SAMPLE PLACEMENT` |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_07_training_principles_easy.json` | en | LESSON | EASY | Fundamentals of Training Theory | Training Principles (Fundamental Principles) | 14 | `ACCEPTED SAMPLE PLACEMENT` |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_08_motor_abilities_easy.json` | en | LESSON | EASY | Fundamentals of Training Theory | Classification of Motor Abilities | 15 | `ACCEPTED SAMPLE PLACEMENT` |
| `content/adaptive_learning/lfa_football_player/general/football_awareness_easy.json` | en | GENERAL | EASY | General | Football Fundamentals | 12 | Owner-approved home; canonical Module is Football Awareness |
| `content/adaptive_learning/lfa_football_player/general/football_awareness_hard.json` | en | GENERAL | HARD | Football Awareness | Football Governance & Global Structures | 12 | Owner-approved home: Football Awareness → Football Governance and Global Structures |
| `content/adaptive_learning/lfa_football_player/general/football_awareness_medium.json` | en | GENERAL | MEDIUM | General | Football Governance & Competition | 10 | Owner-approved home; canonical Module is Football Awareness |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_01_testkulturalis_elozmenyek_easy.json` | hu | LESSON | EASY | Bevezetés | Testkulturális előzmények | 15 | `ACCEPTED SAMPLE PLACEMENT` |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_02_bevezetes_civilizacios_problemak_easy.json` | hu | LESSON | EASY | Bevezetés | A válasz a civilizációs fejlődés életmódra ható problémáira | 14 | `ACCEPTED SAMPLE PLACEMENT` |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_03_edzes_fogalma_easy.json` | hu | LESSON | EASY | Edzéselmélet alapjai | Az edzés fogalma és értelmezése | 15 | `ACCEPTED SAMPLE PLACEMENT` |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_04_terheles_adaptacio_easy.json` | hu | LESSON | EASY | Edzéselmélet alapjai | A terhelés és alkalmazkodás (adaptáció) összefüggése | 15 | `ACCEPTED SAMPLE PLACEMENT` |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_05_edzesterheles_osszetevoi_easy.json` | hu | LESSON | EASY | Edzéselmélet alapjai | Az edzésterhelés összetevői | 15 | `ACCEPTED SAMPLE PLACEMENT` |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_06_edzes_felepitese_easy.json` | hu | LESSON | EASY | Edzéselmélet alapjai | Az edzés felépítése (edzés részei) | 14 | `ACCEPTED SAMPLE PLACEMENT` |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_07_edzeselvek_easy.json` | hu | LESSON | EASY | Edzéselmélet alapjai | Az edzéselvek (alapelvek) | 14 | `ACCEPTED SAMPLE PLACEMENT` |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_08_motoros_kepessegek_easy.json` | hu | LESSON | EASY | Edzéselmélet alapjai | Motoros képességek felosztása | 15 | `ACCEPTED SAMPLE PLACEMENT` |
| `content/adaptive_learning/lfa_football_player/lesson/rules_easy.json` | en | LESSON | EASY | Rules | Laws of the Game | 10 | Owner-approved LessonAssessment under Rules → Laws of the Game |
| `content/adaptive_learning/lfa_football_player/lesson/rules_hard.json` | en | LESSON | HARD | Rules | Laws of the Game | 6 | Owner-approved LessonAssessment under Rules → Laws of the Game |
| `content/adaptive_learning/lfa_football_player/lesson/rules_medium.json` | en | LESSON | MEDIUM | Rules | Laws of the Game | 8 | Owner-approved LessonAssessment under Rules → Laws of the Game |
| `content/adaptive_learning/lfa_football_player/lesson/tactics_easy.json` | en | LESSON | EASY | Tactics | Formations and Basic Principles | 8 | Owner-approved home: Tactics → Formations and Basic Principles |
| `content/adaptive_learning/lfa_football_player/lesson/tactics_hard.json` | en | LESSON | HARD | Tactics | Advanced Tactics | 10 | Owner-approved home: Tactics → Advanced Tactics |
| `content/adaptive_learning/lfa_football_player/lesson/tactics_medium.json` | en | LESSON | MEDIUM | Tactics | Formations and Systems of Play | 6 | Owner-approved home: Tactics → Formations and Systems of Play |
| `content/adaptive_learning/lfa_football_player/nutrition/athlete_nutrition_easy.json` | en | NUTRITION | EASY | Nutrition | Foundations of Sports Nutrition | 10 | Owner-approved home; canonical Module is Athlete Nutrition |
| `content/adaptive_learning/lfa_football_player/nutrition/athlete_nutrition_hard.json` | en | NUTRITION | HARD | Athlete Nutrition | Elite Nutrition Periodization | 12 | Owner-approved home: Athlete Nutrition → Elite Nutrition Periodization |
| `content/adaptive_learning/lfa_football_player/nutrition/athlete_nutrition_medium.json` | en | NUTRITION | MEDIUM | Nutrition | Advanced Sports Nutrition | 8 | Owner-approved home; canonical Module is Athlete Nutrition |

All 31 JSON files use schema version `1.0`. They contain 375 questions: 258 English and 117 Hungarian.

## Future complete curriculum expansion

The future complete Player curriculum is intentionally open-ended. New Tracks, Modules, Lessons and Components are added through versioned content operations after PC3, without changing application code or database schema. A new Lesson may have no assessment; a later Track release may attach one. Missing localizations remain explicit backlog and do not block English base content.

The current sample mapping is an acceptance fixture. It neither claims that every future Lesson has an assessment nor fixes the final curriculum count, titles, grouping or sequence.

## Coverage that can be stated without inventing curriculum

| Measure | Proven result |
|---|---|
| Current assessment files | 31 |
| Logical assessment sets | 23 English assessment sets; eight have proven HU localizations |
| Questions | 375 total: 258 EN, 117 HU |
| Accepted sample Track | 1 |
| Accepted sample Modules | 7 |
| Accepted sample Lessons | 21 |
| Logical assessment sets mapped | 23/23 |
| Assessment files mapped | 31/31 |
| EN coverage of sample Lessons | 21/21 Lessons have at least one EN assessment |
| HU coverage of sample Lessons | 8/21 have a proven HU assessment localization; 13 remain backlog |
| Future complete-curriculum coverage | Open-ended content operation; not a PC3 metric |
| Sample Lessons without assessment | 0; separate extensibility tests must create and publish an allowed no-assessment Lesson |

## Future content decisions — not PC3 blockers

- final production Track display names and release metadata;
- future Module/Lesson inventory, titles, grouping and order;
- separately authored/reviewed Lesson components and assessments;
- additional EN/HU or other locale variants.

These decisions are handled through the canonical draft/release content lifecycle. None is required before PC3 architecture implementation starts.

## Cost and change guard

- GitHub push performed: **NO**
- Expected GitHub Actions runs from this audit: **0**
- macOS or paid GitHub runner used: **NO**
- Application implementation started: **NO**
- Database or migration work started: **NO**
- Educational content changed or generated: **NO**

**EXPECTED FUTURE CONTENT — NOT A BLOCKER**
