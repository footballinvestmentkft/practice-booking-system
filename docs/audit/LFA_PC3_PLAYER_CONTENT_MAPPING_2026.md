# LFA PC3 — Player Content Mapping (2026)

**Status:** owner-review artifact only; implementation has not started

**Baseline:** `LFA_CANONICAL_BASELINE_PC2` → `382b5698eb0252194ce136992918496c78f864aa`

**Program:** `LFA_FOOTBALL_PLAYER`

**Proposed Track:** `LFA Football Player — Core Education`

**Canonical hierarchy:** `Program → Track → Module → Lesson` (each corpus file becomes a Quiz/assessment placement under the mapped Lesson)

## Safety and scope

This mapping is a proposal for owner review. No educational text, answer, explanation, metadata, source JSON, application code or database row was changed. No import, backfill, publication, deployment, remote access or push occurred.

The existing files are mapped as assessment sources. They are not treated as complete lesson bodies. Approval of a mapping does not assert that prose/video/component content already exists for that Lesson.

## Pairing standard

EN/HU files were accepted as one Lesson localization only when all of the following evidence aligned:

- explicit parallel `en/lesson` and `hu/lesson` source paths;
- the same ordered `lesson_XX` number;
- the same question count;
- 100% ordered equality of question type, points, estimated difficulty, cognitive load, average time, option count and correct-option pattern;
- bilingual semantic review of every ordered question and correct answer;
- compatible module/topic meaning.

Similar titles alone were not used. The eight accepted pairs cover 117 English plus 117 Hungarian questions. Their localized `concept_tags` use different strings, so canonical shared taxonomy keys still require an explicit reviewed map. This is a taxonomy normalization issue, not a question/answer translation mismatch.

No exact same-language question text is duplicated within or across the 31 files. Concept-tag overlap exists between related difficulty sets and between Football Awareness and Rules, but it represents topic overlap rather than exact duplicate questions.

## Verified EN/HU pair evidence

| Pair | English file | Hungarian file | Questions per locale | Ordered structural signature | Result |
|---:|---|---|---:|---|---|
| 1 | `lesson_01_physical_culture_background_easy.json` | `lesson_01_testkulturalis_elozmenyek_easy.json` | 15 | `a6c34da4ef55` | `APPROVE` — proven localization pair |
| 2 | `lesson_02_civilizational_development_easy.json` | `lesson_02_bevezetes_civilizacios_problemak_easy.json` | 14 | `a64b4c857827` | `APPROVE` — proven localization pair |
| 3 | `lesson_03_concept_of_training_easy.json` | `lesson_03_edzes_fogalma_easy.json` | 15 | `6ee2718edcf7` | `APPROVE` — proven localization pair |
| 4 | `lesson_04_load_and_adaptation_easy.json` | `lesson_04_terheles_adaptacio_easy.json` | 15 | `7de4ea88741b` | `APPROVE` — proven localization pair |
| 5 | `lesson_05_training_load_components_easy.json` | `lesson_05_edzesterheles_osszetevoi_easy.json` | 15 | `a12e0c206bfc` | `APPROVE` — proven localization pair |
| 6 | `lesson_06_structure_of_training_easy.json` | `lesson_06_edzes_felepitese_easy.json` | 14 | `90cc110bc5c4` | `APPROVE` — proven localization pair |
| 7 | `lesson_07_training_principles_easy.json` | `lesson_07_edzeselvek_easy.json` | 14 | `0507204aad51` | `APPROVE` — proven localization pair |
| 8 | `lesson_08_motor_abilities_easy.json` | `lesson_08_motoros_kepessegek_easy.json` | 15 | `b72cfaea6fa6` | `APPROVE` — proven localization pair |

## Proposed Player education tree

- **Track: LFA Football Player — Core Education**
  - **Module: Introduction**
    - **Lesson 1: Physical Culture Background**
      - EN: `content/adaptive_learning/lfa_football_player/en/lesson/lesson_01_physical_culture_background_easy.json`
      - HU: `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_01_testkulturalis_elozmenyek_easy.json`
    - **Lesson 2: Civilizational Development and Lifestyle**
      - EN: `content/adaptive_learning/lfa_football_player/en/lesson/lesson_02_civilizational_development_easy.json`
      - HU: `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_02_bevezetes_civilizacios_problemak_easy.json`
  - **Module: Training Theory Fundamentals**
    - **Lesson 3: Concept of Training**
      - EN: `content/adaptive_learning/lfa_football_player/en/lesson/lesson_03_concept_of_training_easy.json`
      - HU: `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_03_edzes_fogalma_easy.json`
    - **Lesson 4: Load and Adaptation**
      - EN: `content/adaptive_learning/lfa_football_player/en/lesson/lesson_04_load_and_adaptation_easy.json`
      - HU: `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_04_terheles_adaptacio_easy.json`
    - **Lesson 5: Components of Training Load**
      - EN: `content/adaptive_learning/lfa_football_player/en/lesson/lesson_05_training_load_components_easy.json`
      - HU: `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_05_edzesterheles_osszetevoi_easy.json`
    - **Lesson 6: Structure of Training**
      - EN: `content/adaptive_learning/lfa_football_player/en/lesson/lesson_06_structure_of_training_easy.json`
      - HU: `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_06_edzes_felepitese_easy.json`
    - **Lesson 7: Training Principles**
      - EN: `content/adaptive_learning/lfa_football_player/en/lesson/lesson_07_training_principles_easy.json`
      - HU: `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_07_edzeselvek_easy.json`
    - **Lesson 8: Motor Abilities**
      - EN: `content/adaptive_learning/lfa_football_player/en/lesson/lesson_08_motor_abilities_easy.json`
      - HU: `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_08_motoros_kepessegek_easy.json`
  - **Module: Football Awareness**
    - **Lesson: Football Fundamentals**
      - EN: `content/adaptive_learning/lfa_football_player/general/football_awareness_easy.json`
      - HU: **MISSING**
    - **Lesson: Football Governance and Competition**
      - EN: `content/adaptive_learning/lfa_football_player/general/football_awareness_medium.json`
      - HU: **MISSING**
    - **Lesson: Football Governance and Global Structures**
      - EN: `content/adaptive_learning/lfa_football_player/general/football_awareness_hard.json`
      - HU: **MISSING**
  - **Module: Rules**
    - **Lesson: Laws of the Game**
      - EN (EASY): `content/adaptive_learning/lfa_football_player/lesson/rules_easy.json`
      - EN (MEDIUM): `content/adaptive_learning/lfa_football_player/lesson/rules_medium.json`
      - EN (HARD): `content/adaptive_learning/lfa_football_player/lesson/rules_hard.json`
      - HU: **MISSING**
  - **Module: Tactics**
    - **Lesson: Formations and Basic Principles**
      - EN: `content/adaptive_learning/lfa_football_player/lesson/tactics_easy.json`
      - HU: **MISSING**
    - **Lesson: Formations and Systems of Play**
      - EN: `content/adaptive_learning/lfa_football_player/lesson/tactics_medium.json`
      - HU: **MISSING**
    - **Lesson: Advanced Tactics**
      - EN: `content/adaptive_learning/lfa_football_player/lesson/tactics_hard.json`
      - HU: **MISSING**
  - **Module: Conditioning**
    - **Lesson: Physical Conditioning Fundamentals**
      - EN: `content/adaptive_learning/_shared/sports_physiology/conditioning_easy.json`
      - HU: **MISSING**
    - **Lesson: Physical Conditioning Applied**
      - EN: `content/adaptive_learning/_shared/sports_physiology/conditioning_medium.json`
      - HU: **MISSING**
    - **Lesson: Advanced Physiology and Periodization**
      - EN: `content/adaptive_learning/_shared/sports_physiology/conditioning_hard.json`
      - HU: **MISSING**
  - **Module: Athlete Nutrition**
    - **Lesson: Foundations of Sports Nutrition**
      - EN: `content/adaptive_learning/lfa_football_player/nutrition/athlete_nutrition_easy.json`
      - HU: **MISSING**
    - **Lesson: Advanced Sports Nutrition**
      - EN: `content/adaptive_learning/lfa_football_player/nutrition/athlete_nutrition_medium.json`
      - HU: **MISSING**
    - **Lesson: Elite Nutrition Periodization**
      - EN: `content/adaptive_learning/lfa_football_player/nutrition/athlete_nutrition_hard.json`
      - HU: **MISSING**

The proposed tree has one Track, seven Modules and 21 Lessons. `Rules → Laws of the Game` intentionally shows three English difficulty-specific assessment files under one Lesson. That granularity remains an owner decision; splitting them would produce 23 Lessons instead of 21. Numbered Lessons 1–8 retain their explicit repository order. The cross-Module order after Lesson 8 is a proposal because the repository has no authoritative whole-course sequence.

## Complete file inventory and proposed placement

| Source file | Language | Current module | Current topic | Current title | Questions | Schema | Proposed Track | Proposed Module | Proposed Lesson |
|---|---|---|---|---|---:|---:|---|---|---|
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_01_physical_culture_background_easy.json` | `en` | `Introduction` | Physical Culture Background | AL — Introduction - Physical Culture Background [EN] | 15 | `1.0` | LFA Football Player — Core Education | Introduction | Physical Culture Background |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_01_testkulturalis_elozmenyek_easy.json` | `hu` | `Bevezetés` | Testkulturális előzmények | AL — Bevezetés - Testkulturális előzmények [HU] | 15 | `1.0` | LFA Football Player — Core Education | Introduction | Physical Culture Background |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_02_civilizational_development_easy.json` | `en` | `Introduction` | The Response to Lifestyle Problems Caused by Civilizational Development | AL — Introduction - Civilizational Development and Lifestyle [EN] | 14 | `1.0` | LFA Football Player — Core Education | Introduction | Civilizational Development and Lifestyle |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_02_bevezetes_civilizacios_problemak_easy.json` | `hu` | `Bevezetés` | A válasz a civilizációs fejlődés életmódra ható problémáira | AL — Bevezetés - Civilizációs fejlődés és életmód [HU] | 14 | `1.0` | LFA Football Player — Core Education | Introduction | Civilizational Development and Lifestyle |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_03_concept_of_training_easy.json` | `en` | `Fundamentals of Training Theory` | The Concept and Interpretation of Training | AL — Fundamentals of Training Theory - The Concept of Training [EN] | 15 | `1.0` | LFA Football Player — Core Education | Training Theory Fundamentals | Concept of Training |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_03_edzes_fogalma_easy.json` | `hu` | `Edzéselmélet alapjai` | Az edzés fogalma és értelmezése | AL — Edzéselmélet alapjai - Az edzés fogalma [HU] | 15 | `1.0` | LFA Football Player — Core Education | Training Theory Fundamentals | Concept of Training |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_04_load_and_adaptation_easy.json` | `en` | `Fundamentals of Training Theory` | The Relationship Between Load and Adaptation | AL — Fundamentals of Training Theory - Load and Adaptation [EN] | 15 | `1.0` | LFA Football Player — Core Education | Training Theory Fundamentals | Load and Adaptation |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_04_terheles_adaptacio_easy.json` | `hu` | `Edzéselmélet alapjai` | A terhelés és alkalmazkodás (adaptáció) összefüggése | AL — Edzéselmélet alapjai - Terhelés és adaptáció [HU] | 15 | `1.0` | LFA Football Player — Core Education | Training Theory Fundamentals | Load and Adaptation |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_05_training_load_components_easy.json` | `en` | `Fundamentals of Training Theory` | Components of Training Load | AL — Fundamentals of Training Theory - Components of Training Load [EN] | 15 | `1.0` | LFA Football Player — Core Education | Training Theory Fundamentals | Components of Training Load |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_05_edzesterheles_osszetevoi_easy.json` | `hu` | `Edzéselmélet alapjai` | Az edzésterhelés összetevői | AL — Edzéselmélet alapjai - Az edzésterhelés összetevői [HU] | 15 | `1.0` | LFA Football Player — Core Education | Training Theory Fundamentals | Components of Training Load |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_06_structure_of_training_easy.json` | `en` | `Fundamentals of Training Theory` | Structure of Training (Parts of a Training Session) | AL — Fundamentals of Training Theory - Structure of Training [EN] | 14 | `1.0` | LFA Football Player — Core Education | Training Theory Fundamentals | Structure of Training |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_06_edzes_felepitese_easy.json` | `hu` | `Edzéselmélet alapjai` | Az edzés felépítése (edzés részei) | AL — Edzéselmélet alapjai - Az edzés felépítése [HU] | 14 | `1.0` | LFA Football Player — Core Education | Training Theory Fundamentals | Structure of Training |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_07_training_principles_easy.json` | `en` | `Fundamentals of Training Theory` | Training Principles (Fundamental Principles) | AL — Fundamentals of Training Theory - Training Principles [EN] | 14 | `1.0` | LFA Football Player — Core Education | Training Theory Fundamentals | Training Principles |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_07_edzeselvek_easy.json` | `hu` | `Edzéselmélet alapjai` | Az edzéselvek (alapelvek) | AL — Edzéselmélet alapjai - Edzéselvek [HU] | 14 | `1.0` | LFA Football Player — Core Education | Training Theory Fundamentals | Training Principles |
| `content/adaptive_learning/lfa_football_player/en/lesson/lesson_08_motor_abilities_easy.json` | `en` | `Fundamentals of Training Theory` | Classification of Motor Abilities | AL — Fundamentals of Training Theory - Motor Abilities [EN] | 15 | `1.0` | LFA Football Player — Core Education | Training Theory Fundamentals | Motor Abilities |
| `content/adaptive_learning/lfa_football_player/hu/lesson/lesson_08_motoros_kepessegek_easy.json` | `hu` | `Edzéselmélet alapjai` | Motoros képességek felosztása | AL — Edzéselmélet alapjai - Motoros képességek [HU] | 15 | `1.0` | LFA Football Player — Core Education | Training Theory Fundamentals | Motor Abilities |
| `content/adaptive_learning/lfa_football_player/general/football_awareness_easy.json` | `en` | `General` | Football Fundamentals | AL — Football Awareness - Football Fundamentals [EN] | 12 | `1.0` | LFA Football Player — Core Education | Football Awareness | Football Fundamentals |
| `content/adaptive_learning/lfa_football_player/general/football_awareness_medium.json` | `en` | `General` | Football Governance & Competition | AL — Football Awareness - Football Governance & Competition [EN] | 10 | `1.0` | LFA Football Player — Core Education | Football Awareness | Football Governance and Competition |
| `content/adaptive_learning/lfa_football_player/general/football_awareness_hard.json` | `en` | `Football Awareness` | Football Governance & Global Structures | AL — Football Awareness - Football Governance & Global Structures [EN] | 12 | `1.0` | LFA Football Player — Core Education | Football Awareness | Football Governance and Global Structures |
| `content/adaptive_learning/lfa_football_player/lesson/rules_easy.json` | `en` | `Rules` | Laws of the Game | AL — Football Rules - Laws of the Game Fundamentals [EN] | 10 | `1.0` | LFA Football Player — Core Education | Rules | Laws of the Game |
| `content/adaptive_learning/lfa_football_player/lesson/rules_medium.json` | `en` | `Rules` | Laws of the Game | AL — Football Rules - Laws of the Game Applied [EN] | 8 | `1.0` | LFA Football Player — Core Education | Rules | Laws of the Game |
| `content/adaptive_learning/lfa_football_player/lesson/rules_hard.json` | `en` | `Rules` | Laws of the Game | AL — Football Rules - Laws of the Game Advanced [EN] | 6 | `1.0` | LFA Football Player — Core Education | Rules | Laws of the Game |
| `content/adaptive_learning/lfa_football_player/lesson/tactics_easy.json` | `en` | `Tactics` | Formations and Basic Principles | AL — Football Tactics - Formations and Basic Principles [EN] | 8 | `1.0` | LFA Football Player — Core Education | Tactics | Formations and Basic Principles |
| `content/adaptive_learning/lfa_football_player/lesson/tactics_medium.json` | `en` | `Tactics` | Formations and Systems of Play | AL — Football Tactics - Formations and Systems of Play [EN] | 6 | `1.0` | LFA Football Player — Core Education | Tactics | Formations and Systems of Play |
| `content/adaptive_learning/lfa_football_player/lesson/tactics_hard.json` | `en` | `Tactics` | Advanced Tactics | AL — Football Tactics - Advanced Tactics [EN] | 10 | `1.0` | LFA Football Player — Core Education | Tactics | Advanced Tactics |
| `content/adaptive_learning/_shared/sports_physiology/conditioning_easy.json` | `en` | `Conditioning` | Physical Conditioning Fundamentals | AL — Conditioning & Fitness - Physical Conditioning Fundamentals [EN] | 10 | `1.0` | LFA Football Player — Core Education | Conditioning | Physical Conditioning Fundamentals |
| `content/adaptive_learning/_shared/sports_physiology/conditioning_medium.json` | `en` | `Conditioning` | Physical Conditioning Applied | AL — Conditioning & Fitness - Physical Conditioning Applied [EN] | 9 | `1.0` | LFA Football Player — Core Education | Conditioning | Physical Conditioning Applied |
| `content/adaptive_learning/_shared/sports_physiology/conditioning_hard.json` | `en` | `Conditioning` | Advanced Physiology & Periodization | AL — Conditioning & Fitness - Advanced Physiology & Periodization [EN] | 10 | `1.0` | LFA Football Player — Core Education | Conditioning | Advanced Physiology and Periodization |
| `content/adaptive_learning/lfa_football_player/nutrition/athlete_nutrition_easy.json` | `en` | `Nutrition` | Foundations of Sports Nutrition | AL — Athlete Nutrition - Foundations of Sports Nutrition [EN] | 10 | `1.0` | LFA Football Player — Core Education | Athlete Nutrition | Foundations of Sports Nutrition |
| `content/adaptive_learning/lfa_football_player/nutrition/athlete_nutrition_medium.json` | `en` | `Nutrition` | Advanced Sports Nutrition | AL — Athlete Nutrition - Advanced Sports Nutrition [EN] | 8 | `1.0` | LFA Football Player — Core Education | Athlete Nutrition | Advanced Sports Nutrition |
| `content/adaptive_learning/lfa_football_player/nutrition/athlete_nutrition_hard.json` | `en` | `Athlete Nutrition` | Elite Nutrition Periodization | AL — Athlete Nutrition - Elite Nutrition Periodization [EN] | 12 | `1.0` | LFA Football Player — Core Education | Athlete Nutrition | Elite Nutrition Periodization |

## Complete owner-review evidence table

`APPROVE` below means “evidence supports this proposed mapping; awaiting owner confirmation.” It does not record owner approval yet. `CHANGE` identifies a proposed correction to conflicting current module metadata. `MISSING` means the hierarchy mapping is clear but the HU counterpart is absent. `AMBIGUOUS` requires a hierarchy decision.

| File | EN/HU counterpart | Pair evidence | Confidence | Conflict / ambiguity | Duplicate / overlap suspicion | Missing translation | Owner review status |
|---|---|---|---|---|---|---|---|
| `lesson_01_physical_culture_background_easy.json` | `lesson_01_testkulturalis_elozmenyek_easy.json` | lesson number + 100% ordered structure + bilingual semantic review; signature `a6c34da4ef55` | `HIGH` | No content mismatch found. Localized `concept_tags` differ and need an explicit shared taxonomy-key map. | Verified localization pair, not a duplicate; identical ordered structural signature. | `NO` | `APPROVE` |
| `lesson_01_testkulturalis_elozmenyek_easy.json` | `lesson_01_physical_culture_background_easy.json` | lesson number + 100% ordered structure + bilingual semantic review; signature `a6c34da4ef55` | `HIGH` | No content mismatch found. Localized `concept_tags` differ and need an explicit shared taxonomy-key map. | Verified localization pair, not a duplicate; identical ordered structural signature. | `NO` | `APPROVE` |
| `lesson_02_civilizational_development_easy.json` | `lesson_02_bevezetes_civilizacios_problemak_easy.json` | lesson number + 100% ordered structure + bilingual semantic review; signature `a64b4c857827` | `HIGH` | No content mismatch found. Localized `concept_tags` differ and need an explicit shared taxonomy-key map. | Verified localization pair, not a duplicate; identical ordered structural signature. | `NO` | `APPROVE` |
| `lesson_02_bevezetes_civilizacios_problemak_easy.json` | `lesson_02_civilizational_development_easy.json` | lesson number + 100% ordered structure + bilingual semantic review; signature `a64b4c857827` | `HIGH` | No content mismatch found. Localized `concept_tags` differ and need an explicit shared taxonomy-key map. | Verified localization pair, not a duplicate; identical ordered structural signature. | `NO` | `APPROVE` |
| `lesson_03_concept_of_training_easy.json` | `lesson_03_edzes_fogalma_easy.json` | lesson number + 100% ordered structure + bilingual semantic review; signature `6ee2718edcf7` | `HIGH` | No content mismatch found. Localized `concept_tags` differ and need an explicit shared taxonomy-key map. | Verified localization pair, not a duplicate; identical ordered structural signature. | `NO` | `APPROVE` |
| `lesson_03_edzes_fogalma_easy.json` | `lesson_03_concept_of_training_easy.json` | lesson number + 100% ordered structure + bilingual semantic review; signature `6ee2718edcf7` | `HIGH` | No content mismatch found. Localized `concept_tags` differ and need an explicit shared taxonomy-key map. | Verified localization pair, not a duplicate; identical ordered structural signature. | `NO` | `APPROVE` |
| `lesson_04_load_and_adaptation_easy.json` | `lesson_04_terheles_adaptacio_easy.json` | lesson number + 100% ordered structure + bilingual semantic review; signature `7de4ea88741b` | `HIGH` | No content mismatch found. Localized `concept_tags` differ and need an explicit shared taxonomy-key map. | Verified localization pair, not a duplicate; identical ordered structural signature. | `NO` | `APPROVE` |
| `lesson_04_terheles_adaptacio_easy.json` | `lesson_04_load_and_adaptation_easy.json` | lesson number + 100% ordered structure + bilingual semantic review; signature `7de4ea88741b` | `HIGH` | No content mismatch found. Localized `concept_tags` differ and need an explicit shared taxonomy-key map. | Verified localization pair, not a duplicate; identical ordered structural signature. | `NO` | `APPROVE` |
| `lesson_05_training_load_components_easy.json` | `lesson_05_edzesterheles_osszetevoi_easy.json` | lesson number + 100% ordered structure + bilingual semantic review; signature `a12e0c206bfc` | `HIGH` | No content mismatch found. Localized `concept_tags` differ and need an explicit shared taxonomy-key map. | Verified localization pair, not a duplicate; identical ordered structural signature. | `NO` | `APPROVE` |
| `lesson_05_edzesterheles_osszetevoi_easy.json` | `lesson_05_training_load_components_easy.json` | lesson number + 100% ordered structure + bilingual semantic review; signature `a12e0c206bfc` | `HIGH` | No content mismatch found. Localized `concept_tags` differ and need an explicit shared taxonomy-key map. | Verified localization pair, not a duplicate; identical ordered structural signature. | `NO` | `APPROVE` |
| `lesson_06_structure_of_training_easy.json` | `lesson_06_edzes_felepitese_easy.json` | lesson number + 100% ordered structure + bilingual semantic review; signature `90cc110bc5c4` | `HIGH` | No content mismatch found. Localized `concept_tags` differ and need an explicit shared taxonomy-key map. | Verified localization pair, not a duplicate; identical ordered structural signature. | `NO` | `APPROVE` |
| `lesson_06_edzes_felepitese_easy.json` | `lesson_06_structure_of_training_easy.json` | lesson number + 100% ordered structure + bilingual semantic review; signature `90cc110bc5c4` | `HIGH` | No content mismatch found. Localized `concept_tags` differ and need an explicit shared taxonomy-key map. | Verified localization pair, not a duplicate; identical ordered structural signature. | `NO` | `APPROVE` |
| `lesson_07_training_principles_easy.json` | `lesson_07_edzeselvek_easy.json` | lesson number + 100% ordered structure + bilingual semantic review; signature `0507204aad51` | `HIGH` | No content mismatch found. Localized `concept_tags` differ and need an explicit shared taxonomy-key map. | Verified localization pair, not a duplicate; identical ordered structural signature. | `NO` | `APPROVE` |
| `lesson_07_edzeselvek_easy.json` | `lesson_07_training_principles_easy.json` | lesson number + 100% ordered structure + bilingual semantic review; signature `0507204aad51` | `HIGH` | No content mismatch found. Localized `concept_tags` differ and need an explicit shared taxonomy-key map. | Verified localization pair, not a duplicate; identical ordered structural signature. | `NO` | `APPROVE` |
| `lesson_08_motor_abilities_easy.json` | `lesson_08_motoros_kepessegek_easy.json` | lesson number + 100% ordered structure + bilingual semantic review; signature `b72cfaea6fa6` | `HIGH` | No content mismatch found. Localized `concept_tags` differ and need an explicit shared taxonomy-key map. | Verified localization pair, not a duplicate; identical ordered structural signature. | `NO` | `APPROVE` |
| `lesson_08_motoros_kepessegek_easy.json` | `lesson_08_motor_abilities_easy.json` | lesson number + 100% ordered structure + bilingual semantic review; signature `b72cfaea6fa6` | `HIGH` | No content mismatch found. Localized `concept_tags` differ and need an explicit shared taxonomy-key map. | Verified localization pair, not a duplicate; identical ordered structural signature. | `NO` | `APPROVE` |
| `football_awareness_easy.json` | `—` | No counterpart in repository | `MEDIUM` | Current `module=General`; file family and title prefix say Football Awareness. The proposal changes the module mapping. | Concept overlap with the awareness set and Football Rules; 0 exact duplicate questions. | `HU` | `CHANGE` |
| `football_awareness_medium.json` | `—` | No counterpart in repository | `MEDIUM` | Current `module=General`; file family and title prefix say Football Awareness. The proposal changes the module mapping. | Concept overlap with the awareness set and Football Rules; 0 exact duplicate questions. | `HU` | `CHANGE` |
| `football_awareness_hard.json` | `—` | No counterpart in repository | `HIGH` | Current module, file family and title prefix agree. | Concept overlap with awareness medium and Football Rules; 0 exact duplicate questions. | `HU` | `MISSING` |
| `rules_easy.json` | `—` | No counterpart in repository | `MEDIUM` | All three difficulty files share one topic. Proposal: one Lesson with three assessment sets; owner must confirm one Lesson versus three staged Lessons. | Thematic overlap across Rules and Football Awareness; 0 exact duplicate questions. | `HU` | `AMBIGUOUS` |
| `rules_medium.json` | `—` | No counterpart in repository | `MEDIUM` | All three difficulty files share one topic. Proposal: one Lesson with three assessment sets; owner must confirm one Lesson versus three staged Lessons. | Thematic overlap across Rules and Football Awareness; 0 exact duplicate questions. | `HU` | `AMBIGUOUS` |
| `rules_hard.json` | `—` | No counterpart in repository | `MEDIUM` | All three difficulty files share one topic. Proposal: one Lesson with three assessment sets; owner must confirm one Lesson versus three staged Lessons. | Thematic overlap across Rules and Football Awareness; 0 exact duplicate questions. | `HU` | `AMBIGUOUS` |
| `tactics_easy.json` | `—` | No counterpart in repository | `HIGH` | Current module/topic and file family agree. | Expected tactics-family concept overlap; 0 exact duplicate questions. | `HU` | `MISSING` |
| `tactics_medium.json` | `—` | No counterpart in repository | `HIGH` | Current module/topic and file family agree. | Expected tactics-family concept overlap; 0 exact duplicate questions. | `HU` | `MISSING` |
| `tactics_hard.json` | `—` | No counterpart in repository | `HIGH` | Current module/topic and file family agree. | Expected tactics-family concept overlap; 0 exact duplicate questions. | `HU` | `MISSING` |
| `conditioning_easy.json` | `—` | No counterpart in repository | `HIGH` | Source is under `_shared`, but `specializations` explicitly contains only `LFA_FOOTBALL_PLAYER`; no Program ambiguity. | Expected conditioning-family concept overlap; 0 exact duplicate questions. | `HU` | `MISSING` |
| `conditioning_medium.json` | `—` | No counterpart in repository | `HIGH` | Source is under `_shared`, but `specializations` explicitly contains only `LFA_FOOTBALL_PLAYER`; no Program ambiguity. | Expected conditioning-family concept overlap; 0 exact duplicate questions. | `HU` | `MISSING` |
| `conditioning_hard.json` | `—` | No counterpart in repository | `HIGH` | Source is under `_shared`, but `specializations` explicitly contains only `LFA_FOOTBALL_PLAYER`; no Program ambiguity. | Expected conditioning-family concept overlap; 0 exact duplicate questions. | `HU` | `MISSING` |
| `athlete_nutrition_easy.json` | `—` | No counterpart in repository | `MEDIUM` | Current `module=Nutrition`; file family/title and the hard file say Athlete Nutrition. The proposal changes the module mapping. | Concept overlap with nutrition medium and Conditioning; 0 exact duplicate questions. | `HU` | `CHANGE` |
| `athlete_nutrition_medium.json` | `—` | No counterpart in repository | `MEDIUM` | Current `module=Nutrition`; file family/title and the hard file say Athlete Nutrition. The proposal changes the module mapping. | Concept overlap with nutrition easy/hard and Conditioning; 0 exact duplicate questions. | `HU` | `CHANGE` |
| `athlete_nutrition_hard.json` | `—` | No counterpart in repository | `HIGH` | Current module, file family and title prefix agree. | Concept overlap with nutrition medium and Conditioning; 0 exact duplicate questions. | `HU` | `MISSING` |

## Owner decisions requested

1. **Track name:** approve or change `LFA Football Player — Core Education`. One Track is proposed.
2. **Module names and grouping:** approve the seven-Module grouping. In particular, confirm moving `football_awareness_easy/medium` from current `General` to `Football Awareness`, and moving `athlete_nutrition_easy/medium` from `Nutrition` to `Athlete Nutrition`.
3. **Rules granularity:** approve one `Laws of the Game` Lesson with three difficulty-specific assessment sets, or change to three staged Lessons. The proposal is one Lesson because all three files have the exact same current `topic`.
4. **EN/HU localization pairs:** approve the eight proven pairs. Their content is aligned, but localized concept tags need explicit language-neutral taxonomy keys during implementation.
5. **Missing Hungarian content:** decide whether the 15 English-only files may be published initially with explicit English fallback, or must wait for separately authored/reviewed Hungarian translations. PC3 will not generate translations.
6. **Lesson sequence:** approve or change the displayed Module/Lesson order. Repository metadata supplies file grouping and difficulty, but no authoritative whole-course ordering across these seven Modules.

## Ambiguity and conflict register

| ID | Files | Finding | Proposed handling | Owner status |
|---|---|---|---|---|
| PCM-01 | `football_awareness_easy.json`, `football_awareness_medium.json` | `module=General` conflicts with filename family/title prefix and the hard file’s `module=Football Awareness`. | Map all three to Football Awareness. | `CHANGE` |
| PCM-02 | `athlete_nutrition_easy.json`, `athlete_nutrition_medium.json` | `module=Nutrition` conflicts with filename family/title prefix and hard file’s `module=Athlete Nutrition`. | Map all three to Athlete Nutrition. | `CHANGE` |
| PCM-03 | `rules_easy/medium/hard.json` | Same topic (`Laws of the Game`) but different difficulty/title suffix; repository does not say whether these are one Lesson or three. | One Lesson with three assessment placements. | `AMBIGUOUS` |
| PCM-04 | eight EN/HU pairs | Content structure and semantics match, but `concept_tags` are localized strings rather than shared stable keys. | Approve localization pair; create reviewed language-neutral taxonomy mapping later without editing content. | `APPROVE` |
| PCM-05 | 15 English-only files | No HU counterpart exists. | Keep missing explicitly; do not generate content. | `MISSING` |
| PCM-06 | all 31 files | Files contain assessment questions, not complete Lesson component content. | Map as LessonAssessment sources; Lesson prose/components require separate approved content inventory. | `AMBIGUOUS` at content-completeness level |

## Counts

- Files mapped: **31/31**
- Questions represented: **375/375**
- Proven EN/HU pairs: **8** (**16 files**, **117 questions per locale**)
- Translation mismatches: **0**
- Files with missing HU counterpart: **15**
- Hierarchy mappings requiring specific owner attention: **7 files** (four conflicting module labels and three Rules granularity rows)
- Exact duplicate questions found: **0**
- Proposed Tracks: **1**
- Proposed Modules: **7**
- Proposed Lessons: **21** under the one-Laws-of-the-Game proposal; **23** if Rules is split into three Lessons

## Cost guard

No push or PR was created. Expected GitHub Actions runs: `0`. GitHub macOS/paid runner use: `NO`.

**NO IMPLEMENTATION — WAITING FOR OWNER APPROVAL**
