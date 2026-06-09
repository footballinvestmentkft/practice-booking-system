# AN-3B2B-1 — Skill / GameResult Integrációs Audit + Terv

Státusz: **AUDIT + TERV — implementáció nem kezdődött el.**  
Dátum: 2026-06-17.  
Kapcsolódó terv: `docs/AN3B2B_1_BALL_DETECTION_IMPLEMENTATION_PLAN.md` (v2, type-aware).

---

## 0. Összefoglaló

Az AN-3B2B-1 ball detection + `training_video_type` bevezetése új mérési
adatforrást teremt (labda pozíció, ízületi szögek, hőtérkép). Ez a dokumentum
auditálja, hogyan kapcsolódik ez a meglévő skill / GameResult rendszerhez, és
tervet ad a biztonságos jövőbeli összekötésre.

**Kulcsállítás**: az AN-3B2B-1 PR-ban a ball detection / analysis eredmény
**kizárólag mérési adat** — NEM ír közvetlenül skill értéket. A skill pipeline
összekötése egy későbbi, külön jóváhagyandó fázis.

---

## 1. Meglévő skill pipeline — architektúra audit

### 1.1 Skill update útvonalak (3 db létezik ma)

```
                         ┌─────────────────────────────────┐
                         │  FootballSkillAssessment tábla   │
                         │  (44 skill, percentage-based)    │
                         └─────────────┬───────────────────┘
                                       │ writes
                    ┌──────────────────┼──────────────────┐
                    │                  │                  │
           ┌────────┴──────┐  ┌────────┴──────┐  ┌───────┴──────────┐
           │ Útvonal 1     │  │ Útvonal 2     │  │ Útvonal 3        │
           │ Tournament    │  │ VT Attempt    │  │ Training Session  │
           │ Participation │  │ (mini-game)   │  │ Segment Result   │
           └───────────────┘  └───────────────┘  └──────────────────┘
```

**Útvonal 1: Tournament → EMA → FootballSkillAssessment**
- Forrás: `TournamentParticipation.skill_rating_delta` (EMA-számított)
- Service: `tournament_participation_service.update_skill_assessments()`
- Guard: `ENABLE_TOURNAMENT_SKILL_PROPAGATION` feature flag
- Write: archive old → insert new assessment (audit trail)
- Clamp: `[40.0, 99.0]` percentage
- Write-once: `skill_rating_delta IS NULL` → számítás+write → nem ismétlődik

**Útvonal 2: Virtual Training → VTDeltaComputer → FootballSkillAssessment**
- Forrás: `VirtualTrainingAttempt.skill_deltas` (JSONB, per-skill delta)
- Service: `virtual_training_metrics.compute_vt_skill_deltas()`
- Skill-ök: `reactions`, `decisions`, `concentration`, `anticipation`, `composure`
- Guard: `is_valid=True`, `attempt_index ≤ 3`, bot filter (`avg_reaction_ms ≥ 100`)
- Propagáció: `segment_reward_service.get_training_skill_deltas_for_user()` aggregálja

**Útvonal 3: Training Session → Segment → SkillReward**
- Forrás: `SessionSegmentResult.skill_deltas` (JSONB)
- Service: `segment_reward_service.award_segment_result()`
- Write: `SkillReward` tábla (audit trail) + `FootballSkillAssessment` update
- Guard: instructor-only submission, segment idempotency key

### 1.2 Skill taxonómia (44 skill)

| Kategória | Skill-ek | Videóelemzés releváns? |
|---|---|---|
| **outfield** (19) | ball_control, dribbling, finishing, shot_power, long_shots, volleys, crossing, passing, heading, tackle, marking, shooting, technique, creativity, long_passing, flair, touch, forward_runs, throwing | **Igen** — ball_control, volleys, heading, technique, touch közvetlenül mérhető videóból |
| **set_pieces** (3) | free_kicks, corners, penalties | Közvetetten (ha a videó set piece-t tartalmaz) |
| **mental** (14) | positioning_off/def, vision, aggression, reactions, composure, consistency, tactical_awareness, anticipation, concentration, decisions, determination, teamwork, leadership | Részben — reactions, composure, concentration a VT útvonalból jön; positioning mérhető hőtérképből |
| **physical** (8) | acceleration, sprint_speed, agility, jumping, strength, stamina, balance, work_rate | Részben — agility, balance mérhető pose-ból |

### 1.3 A `gan_footvolley` / `gan_foottennis` / `juggling` támogatottsága

| Rendszer | `juggling` | `gan_footvolley` | `gan_foottennis` |
|---|---|---|---|
| `skills_config.py` (44 skill) | **Nincs explicit** | **Nincs explicit** | **Nincs explicit** |
| `VirtualTrainingGame.game_type` | **Nincs** (VT: reaction_time, cognitive_inhibition, go_no_go, stroop) | **Nincs** | **Nincs** |
| `SessionSegmentResult` | **Nincs** | **Nincs** | **Nincs** |
| `TournamentParticipation` | **Nincs** | **Nincs** | **Nincs** |
| `SkillReward.source_type` | **TOURNAMENT** és **TRAINING** létezik | **Nincs** | **Nincs** |
| `GameResult` (Session.game_results JSONB) | **Nincs** | **Nincs** | **Nincs** |

**Konklúzió**: a meglévő pipeline **semmilyen formában nem támogatja** a három
`training_video_type`-ot. A videóelemzés eredményéből származó skill update egy
teljesen új (4.) útvonalat igényel.

---

## 2. Releváns skill-ek típusonként

### 2.1 Juggling

A zsonglőrözés elsősorban technikai labdakezelési készséget mér.

| Skill | Relevancia | Mérési forrás |
|---|---|---|
| `ball_control` | **Elsődleges** | Érintési gyakoriság, kontakt típus diverzitás |
| `technique` | **Elsődleges** | Érintési pontosság (labda–testpont távolság) |
| `touch` | **Elsődleges** | Labda kontroll minősége érintések között |
| `volleys` | Másodlagos | Ha lábfejes érintés dominál |
| `heading` | Másodlagos | Ha fejérintés van |
| `balance` | Másodlagos | Pose stability (trunk_lean_deg variancia) |
| `concentration` | Másodlagos | Sorozat hossz / hibamentes érintések aránya |
| `reactions` | Harmadlagos | Érintések közötti idő konzisztenciája |

### 2.2 GAN Foot-Volley

| Skill | Relevancia | Mérési forrás |
|---|---|---|
| `ball_control` | **Elsődleges** | Fogadás + visszajátszás minősége |
| `volleys` | **Elsődleges** | Röplabdás megoldások aránya |
| `heading` | **Elsődleges** | Fejjel játszott labdák |
| `positioning_off` | Másodlagos | Hőtérkép: hálóhoz való pozicionálás |
| `agility` | Másodlagos | Mozgási távolság / irányváltások |
| `anticipation` | Másodlagos | Reakció a labda irányára (érintés előtti pozíció) |
| `teamwork` | Harmadlagos | 2v2 formátumban — passzmintázat |

### 2.3 GAN Foot-Tennis

| Skill | Relevancia | Mérési forrás |
|---|---|---|
| `ball_control` | **Elsődleges** | Fogadás minősége |
| `technique` | **Elsődleges** | Visszajátszás technikai tisztasága |
| `touch` | **Elsődleges** | Átvételi pontosság |
| `positioning_def` | Másodlagos | Hőtérkép: pályaharmad fedése |
| `composure` | Másodlagos | Nyomás alatti technikai pontosság |
| `decisions` | Másodlagos | Visszajátszás irányválasztás (labda irány vs. üres tér) |

---

## 3. A jelenlegi skill update flow NEM törik

### 3.1 Miért nem?

Az AN-3B2B-1 PR **semmilyen meglévő skill pipeline kódot nem módosít**:

| Modul | Módosítva? | Ok |
|---|---|---|
| `football_skill_service.py` | **NEM** | Nincs kód-érintés |
| `segment_reward_service.py` | **NEM** | Nincs kód-érintés |
| `virtual_training_metrics.py` | **NEM** | Nincs kód-érintés |
| `tournament_participation_service.py` | **NEM** | Nincs kód-érintés |
| `skills_config.py` | **NEM** | Nincs kód-érintés |
| `FootballSkillAssessment` model | **NEM** | Nincs kód-érintés |
| `SkillReward` model | **NEM** | Nincs kód-érintés |
| `SessionSegmentResult` model | **NEM** | Nincs kód-érintés |

A `training_video_type` oszlop a `juggling_videos` táblán van, amely **nincs
FK kapcsolatban** a skill/session/tournament táblákkal. A ball detection
eredmény a `juggling_ball_detections` táblába kerül, amely szintén **izolált**
a skill pipeline-tól.

### 3.2 Duplikált skill update kockázat

**Nincs.** Jelenleg a videóelemzés eredménye sehol nem csatlakozik a skill
pipeline-hoz. A leendő 4. útvonal bevezetésekor az alábbi garanciák kellenek
(lásd 4. szakasz).

---

## 4. Jövőbeli integration plan: Analysis Result → Skill Update

### 4.1 Javasolt architektúra: Útvonal 4 — Video Analysis Skill Pipeline

```
juggling_ball_detections          ┐
juggling_pose_snapshots           ├── aggregálás ──► VideoAnalysisResult ──► review gate ──► skill delta
juggling_pitch_configs (optional) ┘                  (ÚJ tábla/modell)       (anti-cheat)     (SkillReward)
                                                                                                   │
                                                                                                   ▼
                                                                                   FootballSkillAssessment
```

### 4.2 Új komponensek (jövőbeli, NEM AN-3B2B-1 scope)

**4.2.1 `VideoAnalysisResult` tábla** (leendő migration)

```sql
video_analysis_results (
    id UUID PK,
    video_id UUID FK→juggling_videos UNIQUE,
    training_video_type VARCHAR(30) NOT NULL,
    
    -- Aggregált metrikák
    total_contacts          INTEGER,
    avg_contact_interval_ms FLOAT,
    contact_type_distribution JSONB,  -- {"right_instep": 12, "head": 3, ...}
    avg_trunk_lean_deg      FLOAT,
    trunk_lean_stability    FLOAT,    -- szórás
    avg_knee_flexion_deg    FLOAT,
    ball_control_score      FLOAT,    -- [0, 1] — derived
    
    -- Review gate
    analysis_status         VARCHAR(20) NOT NULL DEFAULT 'pending',
                            -- pending | reviewed | approved | rejected
    reviewed_by             INTEGER FK→users NULLABLE,
    reviewed_at             TIMESTAMPTZ NULLABLE,
    
    -- Skill delta (NULL amíg nem approved)
    skill_deltas            JSONB NULLABLE,  -- {"ball_control": +1.2, "technique": +0.8, ...}
    skill_deltas_applied    BOOLEAN NOT NULL DEFAULT FALSE,
    skill_deltas_applied_at TIMESTAMPTZ NULLABLE,
    
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
```

**4.2.2 Review gate (anti-cheat)**

A skill delta **csak akkor alkalmazható**, ha:

1. `analysis_status = 'approved'` — reviewer (instructor/admin) jóváhagyta
2. `annotation_review_status = 'confirmed'` — az annotáció maga is megerősített
3. `video.quality_status = 'acceptable'` — a videó minőségi kapun átment
4. `video.training_video_type` konzisztens a skill mapping-gel
5. `skill_deltas_applied = FALSE` — duplikáció-védelem (write-once)

**Triple-gate biztonsági modell**:
```
annotation_review_status = 'confirmed'    ← annotátor megerősítette
+ analysis_status = 'approved'            ← reviewer jóváhagyta az elemzést
+ skill_deltas_applied = FALSE            ← még nem volt skill írás
= skill update engedélyezett
```

**4.2.3 Skill delta számítás**

A `VideoAnalysisSkillComputer` service (jövőbeli) a `VideoAnalysisResult`
aggregált metrikáiból számol delta-t, a `training_video_type`-tól függően
más-más skill mapping-gel:

```python
_SKILL_MAPPING: dict[str, dict[str, Callable]] = {
    "juggling": {
        "ball_control":   lambda r: ball_control_from_contacts(r),
        "technique":      lambda r: technique_from_pose(r),
        "touch":          lambda r: touch_from_interval(r),
        "balance":        lambda r: balance_from_trunk_stability(r),
    },
    "gan_footvolley": {
        "ball_control":   lambda r: ...,
        "volleys":        lambda r: ...,
        "heading":        lambda r: ...,
        "positioning_off":lambda r: ...,
    },
    "gan_foottennis": {
        "ball_control":   lambda r: ...,
        "technique":      lambda r: ...,
        "touch":          lambda r: ...,
        "positioning_def":lambda r: ...,
    },
}
```

**4.2.4 SkillReward source_type bővítés**

A jelenlegi `SourceType` enum (`TOURNAMENT`, `TRAINING`) kibővül:

```python
class SourceType(enum.Enum):
    TOURNAMENT      = "TOURNAMENT"
    TRAINING        = "TRAINING"
    VIDEO_ANALYSIS  = "VIDEO_ANALYSIS"     # ← ÚJ
```

A `SkillReward.source_id` a `video_analysis_results.id`-t tárolja (UUID →
Integer konverzió kérdése, vagy a `source_id` típusának String-re váltása —
ez egy leendő migration).

**4.2.5 Duplikáció-védelem**

```python
def apply_video_analysis_skill_deltas(analysis_result_id, db):
    result = db.query(VideoAnalysisResult).get(analysis_result_id)
    
    # Triple-gate
    assert result.analysis_status == 'approved'
    assert not result.skill_deltas_applied
    video = db.query(JugglingVideo).get(result.video_id)
    # ... annotation review check ...
    
    # Write-once flag FIRST (optimistic lock)
    result.skill_deltas_applied = True
    result.skill_deltas_applied_at = utcnow()
    db.flush()
    
    # Skill update (same pattern as tournament_participation_service)
    for skill_key, delta in result.skill_deltas.items():
        # archive old → insert new FootballSkillAssessment
        # insert SkillReward (source_type=VIDEO_ANALYSIS, source_id=result.id)
```

### 4.3 Meglévő flow-k védelme

| Garancia | Mechanizmus |
|---|---|
| Meglévő GameResult flow nem törik | Nincs kódmódosítás a meglévő skill pipeline-ban |
| Duplikált skill update nem lehetséges | `skill_deltas_applied` write-once flag |
| Nem reviewed analysis nem ír skillt | Triple-gate: annotation confirmed + analysis approved + not-applied |
| Rossz minőségű videó nem ír skillt | `quality_status = 'acceptable'` gate |
| Hibás annotáció nem ír skillt | `annotation_review_status = 'confirmed'` gate |
| Per-video egyediség | `UNIQUE (video_id)` a `video_analysis_results` táblán |

---

## 5. Timeline és fázisok

```
AN-3B2B-1 (most)       → Ball detection + training_video_type
                          ★ NINCS skill írás, csak mérési adat
                          
AN-3B2B-2 (következő)  → Pitch config + reference objects
                          ★ NINCS skill írás
                          
AN-3B2B-3 (utána)      → Movement metrics (trunk_lean_deg, knee_flexion_deg)
                          ★ NINCS skill írás, csak derived metrikák
                          
AN-3B2B-4 (utána)      → Heatmap + movement summary
                          ★ NINCS skill írás, csak aggregált nézet

────────────────────────────────────────────────────────────────────

AN-3B2B-5 (jövő)       → VideoAnalysisResult tábla + aggregáció service
                          ★ Aggregálja a mérési adatokat → review-ra kész

AN-3B2B-6 (jövő)       → Admin review UI + analysis approval flow
                          ★ Instructor/admin jóváhagy/elutasít

AN-3B2B-7 (jövő)       → VideoAnalysisSkillComputer + SkillReward integration
                          ★ CSAK approved analysis → skill delta → FootballSkillAssessment
                          ★ SourceType.VIDEO_ANALYSIS
                          ★ Triple-gate + write-once
```

Az AN-3B2B-5/6/7 fázisok **külön jóváhagyást igényelnek**, és kizárólag
az AN-3B2B-1..4 merge után indulhatnak.

---

## 6. Kockázatok

| Kockázat | Mitigáció |
|---|---|
| Skill delta kalibráció: túl nagy/kicsi delta értékek | A VT precedens (neutral zone, NEG_SCALE, daily cap) mintáját követi; sandbox testing |
| Training_video_type mismatch: rossz skill mapping | A `_SKILL_MAPPING` dict-ben type key lookup; ismeretlen type → üres delta (skip) |
| Review gate megkerülése: API-n keresztül közvetlen skill írás | A `apply_video_analysis_skill_deltas` az egyetlen entry point; endpointról nem hívható bypass-szal |
| Duplikált skill update: retry/crash recovery | `skill_deltas_applied` flag FIRST, flush, THEN write — crash után a flag már true, újrafutás no-op |
| `SkillReward.source_id` Integer vs. UUID mismatch | Leendő migration: vagy `source_id` String-re váltás, vagy külön `source_uuid` oszlop |

---

## 7. Összefoglaló válasz a feltett kérdésekre

| Kérdés | Válasz |
|---|---|
| Támogatja-e a meglévő GameResult/skill pipeline a `gan_footvolley` / `gan_foottennis`-t? | **NEM.** Semmilyen formában. Teljesen új (4.) útvonal szükséges. |
| Mely skill-ekre hatna a videóelemzés? | Típusonként eltérő (lásd 2. szakasz): juggling → ball_control/technique/touch/balance; footvolley → +volleys/heading/positioning; foottennis → +positioning_def/composure/decisions |
| Ír-e az AN-3B2B-1 ball detection közvetlenül skill értéket? | **NEM.** Kizárólag mérési adat. |
| Hogyan lesz biztonságosan összekötve? | Triple-gate review model (annotation confirmed + analysis approved + write-once) a jövőbeli AN-3B2B-5/6/7-ben. |
| Van-e anti-cheat / review gate? | **Igen** — instructor/admin jóváhagyás szükséges a skill íráshoz. |
| Törik-e a meglévő GameResult flow? | **NEM.** Az AN-3B2B-1 semmilyen meglévő skill kódot nem módosít. |
| Lesz-e duplikált skill update? | **NEM.** `skill_deltas_applied` write-once flag + `UNIQUE (video_id)`. |

---

**Implementáció nem indult el — az AN-3B2B-1 ball detection PR csak mérési adatot
ír, skill pipeline összekötés külön jóváhagyandó fázis.**
