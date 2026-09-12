# LFA PC3 Full Curriculum to Existing Assessment Mapping 2026

**Status:** evidence audit complete; full curriculum mapping is blocked by the absence of the approved book structure

**Canonical baseline:** `LFA_CANONICAL_BASELINE_PC2` → `382b5698eb0252194ce136992918496c78f864aa`

**Audit branch at start:** `codex/pc3-player-content-mapping` → `1de4120f3baf5b1ff56f982189bda329b8526436`

**Program:** `LFA_FOOTBALL_PLAYER`

**Canonical rule:** the approved LFA Football Player curriculum or book defines Track, Module, Lesson, order, and title. Assessment JSON cannot create or rename curriculum nodes.

## Result

The repository does not contain the approved LFA Football Player book, a versioned table of contents, or another owner-approved curriculum manifest. The repository therefore cannot support a complete row-per-book-Lesson mapping without inventing curriculum structure from assessment files, which the owner explicitly prohibited.

The audit can prove two narrower results:

1. The owner has explicitly approved 13 distinct Lesson slots for 15 English assessment files. These placements are recorded below, but their Track, book title, and Lesson order remain unknown until checked against the approved book.
2. The remaining 16 JSON files form eight approved EN/HU localization pairs, but no repository evidence identifies their approved book Lesson IDs or positions. They remain `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` until the book is available.

Consequently, the total number of canonical Lessons and the count of canonical Lessons without assessment are **not determinable from the repository**. Reporting 21 or 68 canonical Lessons would derive hierarchy from assessment content and would violate the owner rule.

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

A source qualifies as the curriculum authority only if it establishes all of the following:

- it is the approved `LFA_FOOTBALL_PLAYER` curriculum or book;
- it has an identifiable version or approval state;
- it enumerates Track, Module, Lesson, title, and order;
- it is independent of quiz/adaptive corpus metadata.

Filename order, JSON `module`/`topic`, question wording, title similarity, legacy seed rows, and generated assessment directory layout are insufficient by themselves.

## Source discovery register

| Candidate evidence | Location / identifier | Finding | Authority result |
|---|---|---|---|
| Approved Player book or TOC in PC2 baseline | Git tree at `382b5698eb0252194ce136992918496c78f864aa` | No PDF, DOC, DOCX, ODT, EPUB, Pages, RTF, curriculum manifest, or book TOC for `LFA_FOOTBALL_PLAYER` | **MISSING** |
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

The following decisions are authoritative for this mapping even though the book file is missing:

- the eight proven EN/HU pairs represent the same Lesson localization on each pair;
- `Football Awareness` replaces legacy `General` as the canonical Module;
- Football Awareness has three separate Lessons;
- Rules has one Lesson, `Laws of the Game`, with Easy, Medium, and Hard as three separate `LessonAssessment` placements;
- Tactics has three separate Lessons;
- Conditioning has three separate Lessons;
- `Athlete Nutrition` replaces legacy `Nutrition` and has three separate Lessons;
- missing Hungarian assessment content does not block English base publication and remains localization backlog;
- `concept_tags` must become language-neutral taxonomy keys with localized display labels.

These decisions define assessment placement constraints. They do not supply the missing complete book TOC, cross-Module order, or the set of all book Lessons.

## Owner-approved Lesson slots and assessment placements

`UNDECIDED` in Track and order means that the owner-approved book evidence is absent. No order is inferred from filenames or difficulty.

The three Football Awareness titles and `Laws of the Game` were named directly in the owner decision. The Tactics, Conditioning, and Athlete Nutrition titles below are current English corpus-topic labels: the owner approved three separate Lessons in each Module, but the approved book must still confirm their canonical titles.

| Track | Module | Lesson order | Lesson title / current corpus label | Source / book evidence | Assessment JSON | Assessment type / difficulty | EN / HU | Missing assessment | Mapping confidence |
|---|---|---:|---|---|---|---|---|---|---|
| `UNDECIDED` | Football Awareness | `UNDECIDED` | Football Fundamentals | Owner decision; book citation unavailable | `content/adaptive_learning/lfa_football_player/general/football_awareness_easy.json` | LessonAssessment / EASY | EN yes; HU missing | No | HIGH for assessment home; UNVERIFIED against book |
| `UNDECIDED` | Football Awareness | `UNDECIDED` | Football Governance and Competition | Owner decision; book citation unavailable | `content/adaptive_learning/lfa_football_player/general/football_awareness_medium.json` | LessonAssessment / MEDIUM | EN yes; HU missing | No | HIGH for assessment home; UNVERIFIED against book |
| `UNDECIDED` | Football Awareness | `UNDECIDED` | Football Governance and Global Structures | Owner decision; book citation unavailable | `content/adaptive_learning/lfa_football_player/general/football_awareness_hard.json` | LessonAssessment / HARD | EN yes; HU missing | No | HIGH for assessment home; UNVERIFIED against book |
| `UNDECIDED` | Rules | `UNDECIDED` | Laws of the Game | Owner decision; book citation unavailable | `content/adaptive_learning/lfa_football_player/lesson/rules_easy.json`; `rules_medium.json`; `rules_hard.json` | 3 LessonAssessments / EASY, MEDIUM, HARD | EN yes; HU missing | No | HIGH for assessment home; UNVERIFIED against book |
| `UNDECIDED` | Tactics | `UNDECIDED` | Formations and Basic Principles | Owner decision: separate Lesson; title from current corpus; book citation unavailable | `content/adaptive_learning/lfa_football_player/lesson/tactics_easy.json` | LessonAssessment / EASY | EN yes; HU missing | No | HIGH for distinct placement; title/order UNVERIFIED against book |
| `UNDECIDED` | Tactics | `UNDECIDED` | Formations and Systems of Play | Owner decision: separate Lesson; title from current corpus; book citation unavailable | `content/adaptive_learning/lfa_football_player/lesson/tactics_medium.json` | LessonAssessment / MEDIUM | EN yes; HU missing | No | HIGH for distinct placement; title/order UNVERIFIED against book |
| `UNDECIDED` | Tactics | `UNDECIDED` | Advanced Tactics | Owner decision: separate Lesson; title from current corpus; book citation unavailable | `content/adaptive_learning/lfa_football_player/lesson/tactics_hard.json` | LessonAssessment / HARD | EN yes; HU missing | No | HIGH for distinct placement; title/order UNVERIFIED against book |
| `UNDECIDED` | Conditioning | `UNDECIDED` | Physical Conditioning Fundamentals | Owner decision: separate Lesson; title from current corpus; book citation unavailable | `content/adaptive_learning/_shared/sports_physiology/conditioning_easy.json` | LessonAssessment / EASY | EN yes; HU missing | No | HIGH for distinct placement; title/order UNVERIFIED against book |
| `UNDECIDED` | Conditioning | `UNDECIDED` | Physical Conditioning Applied | Owner decision: separate Lesson; title from current corpus; book citation unavailable | `content/adaptive_learning/_shared/sports_physiology/conditioning_medium.json` | LessonAssessment / MEDIUM | EN yes; HU missing | No | HIGH for distinct placement; title/order UNVERIFIED against book |
| `UNDECIDED` | Conditioning | `UNDECIDED` | Advanced Physiology and Periodization | Owner decision: separate Lesson; title from current corpus; book citation unavailable | `content/adaptive_learning/_shared/sports_physiology/conditioning_hard.json` | LessonAssessment / HARD | EN yes; HU missing | No | HIGH for distinct placement; title/order UNVERIFIED against book |
| `UNDECIDED` | Athlete Nutrition | `UNDECIDED` | Foundations of Sports Nutrition | Owner decision: separate Lesson; title from current corpus; book citation unavailable | `content/adaptive_learning/lfa_football_player/nutrition/athlete_nutrition_easy.json` | LessonAssessment / EASY | EN yes; HU missing | No | HIGH for distinct placement; title/order UNVERIFIED against book |
| `UNDECIDED` | Athlete Nutrition | `UNDECIDED` | Advanced Sports Nutrition | Owner decision: separate Lesson; title from current corpus; book citation unavailable | `content/adaptive_learning/lfa_football_player/nutrition/athlete_nutrition_medium.json` | LessonAssessment / MEDIUM | EN yes; HU missing | No | HIGH for distinct placement; title/order UNVERIFIED against book |
| `UNDECIDED` | Athlete Nutrition | `UNDECIDED` | Elite Nutrition Periodization | Owner decision: separate Lesson; title from current corpus; book citation unavailable | `content/adaptive_learning/lfa_football_player/nutrition/athlete_nutrition_hard.json` | LessonAssessment / HARD | EN yes; HU missing | No | HIGH for distinct placement; title/order UNVERIFIED against book |

This table records **13 owner-approved distinct Lesson slots** for **15 JSON files**. It is not the full curriculum tree, and nine displayed titles still require confirmation against the book.

## Assessment groups without an approved curriculum home

The owner approved the localization relationship inside each pair. The repository still does not prove the approved book Module, Lesson identifier, title, or order. These are eight logical assessment sets represented by 16 files.

| Pair | Corpus label only | EN assessment | HU assessment | Questions per locale | Approved fact | Status |
|---:|---|---|---|---:|---|---|
| 1 | Physical Culture Background / Testkulturális előzmények | `content/adaptive_learning/lfa_football_player/en/lesson/lesson_01_physical_culture_background_easy.json` | `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_01_testkulturalis_elozmenyek_easy.json` | 15 | Same Lesson localization pair | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| 2 | Civilizational Development and Lifestyle | `content/adaptive_learning/lfa_football_player/en/lesson/lesson_02_civilizational_development_easy.json` | `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_02_bevezetes_civilizacios_problemak_easy.json` | 14 | Same Lesson localization pair | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| 3 | Concept of Training | `content/adaptive_learning/lfa_football_player/en/lesson/lesson_03_concept_of_training_easy.json` | `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_03_edzes_fogalma_easy.json` | 15 | Same Lesson localization pair | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| 4 | Load and Adaptation | `content/adaptive_learning/lfa_football_player/en/lesson/lesson_04_load_and_adaptation_easy.json` | `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_04_terheles_adaptacio_easy.json` | 15 | Same Lesson localization pair | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| 5 | Components of Training Load | `content/adaptive_learning/lfa_football_player/en/lesson/lesson_05_training_load_components_easy.json` | `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_05_edzesterheles_osszetevoi_easy.json` | 15 | Same Lesson localization pair | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| 6 | Structure of Training | `content/adaptive_learning/lfa_football_player/en/lesson/lesson_06_structure_of_training_easy.json` | `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_06_edzes_felepitese_easy.json` | 14 | Same Lesson localization pair | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| 7 | Training Principles | `content/adaptive_learning/lfa_football_player/en/lesson/lesson_07_training_principles_easy.json` | `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_07_edzeselvek_easy.json` | 14 | Same Lesson localization pair | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| 8 | Motor Abilities | `content/adaptive_learning/lfa_football_player/en/lesson/lesson_08_motor_abilities_easy.json` | `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_08_motoros_kepessegek_easy.json` | 15 | Same Lesson localization pair | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |

The numeric filename prefix is retained as corpus identity only. It is not accepted as canonical Lesson order.

## Complete 31-file assessment disposition

| File | Language | Category | Difficulty | Current JSON module | Current JSON topic | Questions | Curriculum disposition |
|---|---|---|---|---|---|---:|---|
| `content/adaptive_learning/_shared/sports_physiology/conditioning_easy.json` | en | SPORTS_PHYSIOLOGY | EASY | Conditioning | Physical Conditioning Fundamentals | 10 | Owner-approved home: Conditioning → Physical Conditioning Fundamentals |
| `content/adaptive_learning/_shared/sports_physiology/conditioning_hard.json` | en | SPORTS_PHYSIOLOGY | HARD | Conditioning | Advanced Physiology & Periodization | 10 | Owner-approved home: Conditioning → Advanced Physiology and Periodization |
| `content/adaptive_learning/_shared/sports_physiology/conditioning_medium.json` | en | SPORTS_PHYSIOLOGY | MEDIUM | Conditioning | Physical Conditioning Applied | 9 | Owner-approved home: Conditioning → Physical Conditioning Applied |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_01_physical_culture_background_easy.json` | en | LESSON | EASY | Introduction | Physical Culture Background | 15 | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_02_civilizational_development_easy.json` | en | LESSON | EASY | Introduction | The Response to Lifestyle Problems Caused by Civilizational Development | 14 | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_03_concept_of_training_easy.json` | en | LESSON | EASY | Fundamentals of Training Theory | The Concept and Interpretation of Training | 15 | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_04_load_and_adaptation_easy.json` | en | LESSON | EASY | Fundamentals of Training Theory | The Relationship Between Load and Adaptation | 15 | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_05_training_load_components_easy.json` | en | LESSON | EASY | Fundamentals of Training Theory | Components of Training Load | 15 | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_06_structure_of_training_easy.json` | en | LESSON | EASY | Fundamentals of Training Theory | Structure of Training (Parts of a Training Session) | 14 | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_07_training_principles_easy.json` | en | LESSON | EASY | Fundamentals of Training Theory | Training Principles (Fundamental Principles) | 14 | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_08_motor_abilities_easy.json` | en | LESSON | EASY | Fundamentals of Training Theory | Classification of Motor Abilities | 15 | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| `content/adaptive_learning/lfa_football_player/general/football_awareness_easy.json` | en | GENERAL | EASY | General | Football Fundamentals | 12 | Owner-approved home; canonical Module is Football Awareness |
| `content/adaptive_learning/lfa_football_player/general/football_awareness_hard.json` | en | GENERAL | HARD | Football Awareness | Football Governance & Global Structures | 12 | Owner-approved home: Football Awareness → Football Governance and Global Structures |
| `content/adaptive_learning/lfa_football_player/general/football_awareness_medium.json` | en | GENERAL | MEDIUM | General | Football Governance & Competition | 10 | Owner-approved home; canonical Module is Football Awareness |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_01_testkulturalis_elozmenyek_easy.json` | hu | LESSON | EASY | Bevezetés | Testkulturális előzmények | 15 | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_02_bevezetes_civilizacios_problemak_easy.json` | hu | LESSON | EASY | Bevezetés | A válasz a civilizációs fejlődés életmódra ható problémáira | 14 | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_03_edzes_fogalma_easy.json` | hu | LESSON | EASY | Edzéselmélet alapjai | Az edzés fogalma és értelmezése | 15 | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_04_terheles_adaptacio_easy.json` | hu | LESSON | EASY | Edzéselmélet alapjai | A terhelés és alkalmazkodás (adaptáció) összefüggése | 15 | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_05_edzesterheles_osszetevoi_easy.json` | hu | LESSON | EASY | Edzéselmélet alapjai | Az edzésterhelés összetevői | 15 | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_06_edzes_felepitese_easy.json` | hu | LESSON | EASY | Edzéselmélet alapjai | Az edzés felépítése (edzés részei) | 14 | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_07_edzeselvek_easy.json` | hu | LESSON | EASY | Edzéselmélet alapjai | Az edzéselvek (alapelvek) | 14 | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_08_motoros_kepessegek_easy.json` | hu | LESSON | EASY | Edzéselmélet alapjai | Motoros képességek felosztása | 15 | `ASSESSMENT WITHOUT APPROVED CURRICULUM HOME` |
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

## Required full book table

The requested row-per-canonical-Lesson table cannot be populated safely until an authoritative source is identified. The blocked fields are shown explicitly:

| Track | Module | Lesson order | Lesson title | Source / book evidence | Assessment present | JSON | Type / difficulty | EN / HU | Missing assessment | Confidence |
|---|---|---:|---|---|---|---|---|---|---|---|
| `UNDETERMINED` | `UNDETERMINED` | `UNDETERMINED` | **Approved book TOC unavailable** | No qualifying source in repository | `UNDETERMINED` | `UNDETERMINED` | `UNDETERMINED` | `UNDETERMINED` | `UNDETERMINED` | NONE |

This placeholder is intentionally not expanded from the 31 assessment files.

## Coverage that can be stated without inventing curriculum

| Measure | Proven result |
|---|---|
| Current assessment files | 31 |
| Logical assessment sets | 23 English assessment sets; eight have proven HU localizations |
| Questions | 375 total: 258 EN, 117 HU |
| Owner-approved distinct Lesson slots | 13 Lessons |
| Files assigned to owner-approved placements | 15 EN files |
| Logical assessment sets without approved curriculum home | 8 |
| Files without approved curriculum home | 16: eight EN and eight HU |
| EN coverage of the 13 known homes | 13/13 Lessons have at least one EN assessment |
| HU coverage of the 13 known homes | 0/13; all remain localization backlog |
| Full-curriculum EN coverage | `UNDETERMINED` |
| Full-curriculum HU coverage | `UNDETERMINED` |
| Canonical Lessons without assessment | `UNDETERMINED` |

## Owner input required to finish the full mapping

1. Identify the exact approved LFA Football Player curriculum/book artifact and version, or add/provide a read-only path to its approved table of contents.
2. Confirm the canonical Track identity/title if the book itself does not define it.
3. Confirm that the supplied artifact is the publication authority for Module names, Lesson titles, and order.

After that source is available, the next audit pass can enumerate every book Lesson, attach the 15 already approved files, evidence-match the eight bilingual sets without using title similarity alone, and mark every remaining book row `CURRICULUM LESSON WITHOUT ASSESSMENT`.

## Cost and change guard

- GitHub push performed: **NO**
- Expected GitHub Actions runs from this audit: **0**
- macOS or paid GitHub runner used: **NO**
- Application implementation started: **NO**
- Database or migration work started: **NO**
- Educational content changed or generated: **NO**
