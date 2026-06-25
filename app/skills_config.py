"""
Football Skills Configuration
Unified skill structure for onboarding, skill progression engine, and dashboard

Skill count: 44 (expanded from 29 in Phase 3 — 2026-05-11)
  outfield:   19 skills (11 original + 8 new)
  set_pieces:  3 skills (unchanged)
  mental:     14 skills (8 original + 6 new)
  physical:    8 skills (7 original + 1 new)

Laterality taxonomy (2026-05-11):
  foot (20): ball_control, dribbling, finishing, shot_power, long_shots, volleys,
             crossing, passing, tackle, marking, shooting, technique, creativity,
             long_passing, flair, touch, forward_runs, free_kicks, corners, penalties
  hand  (1): throwing  — hand-lateral; tracked separately from foot context
  none (23): heading + all mental (14) + all physical (8)
"""

from typing import Dict, List, Literal, TypedDict


class SkillDefinition(TypedDict):
    """Single skill definition"""
    key: str                                               # snake_case key for database
    name_en: str                                           # English display name
    name_hu: str                                           # Hungarian display name
    description_hu: str                                    # Hungarian description
    laterality_domain: Literal["foot", "hand", "none"]    # Laterality classification


class SkillCategory(TypedDict):
    """Skill category definition"""
    key: str
    name_en: str
    name_hu: str
    emoji: str
    skills: List[SkillDefinition]


# Complete skill structure
SKILL_CATEGORIES: List[SkillCategory] = [
    {
        "key": "outfield",
        "name_en": "Outfield",
        "name_hu": "Mezőnyjátékos technikai készségek",
        "emoji": "🟦",
        "skills": [
            {
                "key": "ball_control",
                "name_en": "Ball Control",
                "name_hu": "Labdakontroll",
                "description_hu": "A labda átvételének és kezelésének minősége különböző szituációkban.",
                "laterality_domain": "foot",
            },
            {
                "key": "dribbling",
                "name_en": "Dribbling",
                "name_hu": "Cselezés",
                "description_hu": "Ellenféllel szembeni labdavezetési és irányváltási képesség.",
                "laterality_domain": "foot",
            },
            {
                "key": "finishing",
                "name_en": "Finishing",
                "name_hu": "Befejezés",
                "description_hu": "Helyzetek gólra váltásának hatékonysága.",
                "laterality_domain": "foot",
            },
            {
                "key": "shot_power",
                "name_en": "Shot Power",
                "name_hu": "Lövőerő",
                "description_hu": "A lövések ereje, különösen távolról vagy nagy intenzitású helyzetekben.",
                "laterality_domain": "foot",
            },
            {
                "key": "long_shots",
                "name_en": "Long Shots",
                "name_hu": "Távoli lövések",
                "description_hu": "Pontosság és hatékonyság 16 méteren kívüli lövéseknél.",
                "laterality_domain": "foot",
            },
            {
                "key": "volleys",
                "name_en": "Volleys",
                "name_hu": "Röplabdás lövések",
                "description_hu": "Levegőből, pattanás nélkül elvégzett lövések minősége.",
                "laterality_domain": "foot",
            },
            {
                "key": "crossing",
                "name_en": "Crossing",
                "name_hu": "Beadások",
                "description_hu": "Oldalról érkező labdák pontossága és használhatósága.",
                "laterality_domain": "foot",
            },
            {
                "key": "passing",
                "name_en": "Passing",
                "name_hu": "Passzok",
                "description_hu": "Rövid és középtávú passzok pontossága és időzítése.",
                "laterality_domain": "foot",
            },
            {
                "key": "heading",
                "name_en": "Heading",
                "name_hu": "Fejelési pontosság",
                "description_hu": "Fejesek irányíthatósága támadásban és védekezésben.",
                "laterality_domain": "none",
            },
            {
                "key": "tackle",
                "name_en": "Tackle",
                "name_hu": "Szerelés állva",
                "description_hu": "Labdaszerzés álló helyzetben, szabályosan.",
                "laterality_domain": "foot",
            },
            {
                "key": "marking",
                "name_en": "Marking",
                "name_hu": "Emberfogás",
                "description_hu": "Ellenfél követése, leválás megakadályozása.",
                "laterality_domain": "foot",
            },
            {
                "key": "shooting",
                "name_en": "Shooting",
                "name_hu": "Lövések",
                "description_hu": "Általános lövéskészség és pontosság különböző szituációkban.",
                "laterality_domain": "foot",
            },
            {
                "key": "technique",
                "name_en": "Technique",
                "name_hu": "Technika",
                "description_hu": "Labdakezelési technika és mozdulatok finomsága és pontossága.",
                "laterality_domain": "foot",
            },
            {
                "key": "creativity",
                "name_en": "Creativity",
                "name_hu": "Kreativitás",
                "description_hu": "Szokatlan, váratlan megoldások és játékvariációk alkalmazása.",
                "laterality_domain": "foot",
            },
            {
                "key": "long_passing",
                "name_en": "Long Passing",
                "name_hu": "Hosszú passzok",
                "description_hu": "Pontos hosszú passzok és mélységi indítások.",
                "laterality_domain": "foot",
            },
            {
                "key": "flair",
                "name_en": "Flair",
                "name_hu": "Zseniális megoldások",
                "description_hu": "Egyedi, látványos technikai elemek és improvizált megoldások.",
                "laterality_domain": "foot",
            },
            {
                "key": "touch",
                "name_en": "Touch",
                "name_hu": "Labdaérintés",
                "description_hu": "Első érintés minősége, labda leállításának finomsága.",
                "laterality_domain": "foot",
            },
            {
                "key": "forward_runs",
                "name_en": "Forward Runs",
                "name_hu": "Előretörések",
                "description_hu": "Offenzív lefutások időzítése és hatékonysága mélységi labdáknál.",
                "laterality_domain": "foot",
            },
            {
                "key": "throwing",
                "name_en": "Throwing",
                "name_hu": "Dobáskészség",
                "description_hu": "Általános dobáskészség és pontosság — bedobásoknál és kapusnál egyaránt.",
                "laterality_domain": "hand",
            },
        ]
    },
    {
        "key": "set_pieces",
        "name_en": "Set Pieces",
        "name_hu": "Rögzített helyzetek",
        "emoji": "🟨",
        "skills": [
            {
                "key": "free_kicks",
                "name_en": "Free Kicks",
                "name_hu": "Szabadrúgások",
                "description_hu": "Közvetlen és közvetett szabadrúgások minősége.",
                "laterality_domain": "foot",
            },
            {
                "key": "corners",
                "name_en": "Corners",
                "name_hu": "Szögletrúgások",
                "description_hu": "Szögletek pontossága és veszélyessége.",
                "laterality_domain": "foot",
            },
            {
                "key": "penalties",
                "name_en": "Penalties",
                "name_hu": "Tizenegyesek",
                "description_hu": "Büntetők értékesítésének megbízhatósága.",
                "laterality_domain": "foot",
            },
        ]
    },
    {
        "key": "mental",
        "name_en": "Mental",
        "name_hu": "Mentális és taktikai készségek",
        "emoji": "🟩",
        "skills": [
            {
                "key": "positioning_off",
                "name_en": "Positioning (Off)",
                "name_hu": "Helyezkedés támadásban",
                "description_hu": "Üres területek felismerése, jó pozíciók felvétele.",
                "laterality_domain": "none",
            },
            {
                "key": "positioning_def",
                "name_en": "Positioning (Def)",
                "name_hu": "Helyezkedés védekezésben",
                "description_hu": "Védekező pozíciók megtartása, zárások.",
                "laterality_domain": "none",
            },
            {
                "key": "vision",
                "name_en": "Vision",
                "name_hu": "Játéklátás",
                "description_hu": "Passzsávok, lehetőségek felismerése.",
                "laterality_domain": "none",
            },
            {
                "key": "aggression",
                "name_en": "Aggression",
                "name_hu": "Agresszivitás",
                "description_hu": "Párharcok intenzitása, harciasság.",
                "laterality_domain": "none",
            },
            {
                "key": "reactions",
                "name_en": "Reactions",
                "name_hu": "Reakcióidő",
                "description_hu": "Váratlan helyzetekre adott gyors válaszok.",
                "laterality_domain": "none",
            },
            {
                "key": "composure",
                "name_en": "Composure",
                "name_hu": "Hidegvér",
                "description_hu": "Nyomás alatti döntéshozatal minősége.",
                "laterality_domain": "none",
            },
            {
                "key": "consistency",
                "name_en": "Consistency",
                "name_hu": "Kiegyensúlyozottság",
                "description_hu": "Teljesítmény stabilitása mérkőzésről mérkőzésre.",
                "laterality_domain": "none",
            },
            {
                "key": "tactical_awareness",
                "name_en": "Tactical Awareness",
                "name_hu": "Taktikai tudatosság",
                "description_hu": "Csapatstruktúra és játékelvek megértése.",
                "laterality_domain": "none",
            },
            {
                "key": "anticipation",
                "name_en": "Anticipation",
                "name_hu": "Helyzetfelismerés",
                "description_hu": "Játékhelyzetek és ellenfél szándékának előre jelzése.",
                "laterality_domain": "none",
            },
            {
                "key": "concentration",
                "name_en": "Concentration",
                "name_hu": "Koncentráció",
                "description_hu": "Figyelmi fókusz fenntartása a mérkőzés teljes ideje alatt.",
                "laterality_domain": "none",
            },
            {
                "key": "decisions",
                "name_en": "Decisions",
                "name_hu": "Döntéshozatal",
                "description_hu": "Gyors és helyes döntések labdánál és labda nélkül egyaránt.",
                "laterality_domain": "none",
            },
            {
                "key": "determination",
                "name_en": "Determination",
                "name_hu": "Elszántság",
                "description_hu": "Kitartás és belső motiváció nehéz helyzetekben és lemaradásban.",
                "laterality_domain": "none",
            },
            {
                "key": "teamwork",
                "name_en": "Teamwork",
                "name_hu": "Csapatmunka",
                "description_hu": "Együttműködési képesség csapattársakkal, önzetlenség a pályán.",
                "laterality_domain": "none",
            },
            {
                "key": "leadership",
                "name_en": "Leadership",
                "name_hu": "Vezetői képesség",
                "description_hu": "Csapat irányítása, motiválása és hangolása mérkőzés közben.",
                "laterality_domain": "none",
            },
        ]
    },
    {
        "key": "physical",
        "name_en": "Physical Fitness",
        "name_hu": "Fizikai képességek",
        "emoji": "🟥",
        "skills": [
            {
                "key": "acceleration",
                "name_en": "Acceleration",
                "name_hu": "Gyorsulás",
                "description_hu": "Első lépések robbanékonysága.",
                "laterality_domain": "none",
            },
            {
                "key": "sprint_speed",
                "name_en": "Sprint Speed",
                "name_hu": "Végsebesség",
                "description_hu": "Maximális futási sebesség.",
                "laterality_domain": "none",
            },
            {
                "key": "agility",
                "name_en": "Agility",
                "name_hu": "Agilitás",
                "description_hu": "Gyors irányváltás, testkontroll.",
                "laterality_domain": "none",
            },
            {
                "key": "jumping",
                "name_en": "Jumping",
                "name_hu": "Ugróképesség",
                "description_hu": "Fejpárbajokhoz és levegőben való játékhoz.",
                "laterality_domain": "none",
            },
            {
                "key": "strength",
                "name_en": "Strength",
                "name_hu": "Erő",
                "description_hu": "Test-test elleni párharcokban mutatott fizikai fölény.",
                "laterality_domain": "none",
            },
            {
                "key": "stamina",
                "name_en": "Stamina",
                "name_hu": "Állóképesség",
                "description_hu": "Terhelhetőség a mérkőzés teljes ideje alatt.",
                "laterality_domain": "none",
            },
            {
                "key": "balance",
                "name_en": "Balance",
                "name_hu": "Egyensúly",
                "description_hu": "Stabilitás mozgás és kontakt közben.",
                "laterality_domain": "none",
            },
            {
                "key": "work_rate",
                "name_en": "Work Rate",
                "name_hu": "Munkabírás",
                "description_hu": "Lefutott távolság és aktivitás mérkőzésen — védekezésben és támadásban egyaránt.",
                "laterality_domain": "none",
            },
        ]
    }
]


# Flat mapping for quick lookup: skill_key -> skill definition
ALL_SKILLS: Dict[str, SkillDefinition] = {}
for category in SKILL_CATEGORIES:
    for skill in category["skills"]:
        ALL_SKILLS[skill["key"]] = skill


# Flat laterality lookup: skill_key -> "foot" | "hand" | "none"
# Used by the tournament reward orchestrator to guard foot-lateral bucket writes.
# If a skill_key is absent here it is a configuration error — callers must not
# silently fall back to "foot".
SKILL_LATERALITY: Dict[str, str] = {
    key: defn["laterality_domain"]
    for key, defn in ALL_SKILLS.items()
}


# Default baseline for new skills (existing players migration)
# NOTE: The EMA engine uses DEFAULT_BASELINE = 60.0 (from skill_progression/_formulas.py).
# This legacy constant predates the SYSTEM_BASELINE = 60.0 correction — do not use for new code.
DEFAULT_SKILL_BASELINE = 50.0


def get_all_skill_keys() -> List[str]:
    """Return list of all skill keys"""
    return list(ALL_SKILLS.keys())


def get_skill_display_name(skill_key: str, lang: str = "hu") -> str:
    """Get display name for a skill"""
    skill = ALL_SKILLS.get(skill_key)
    if not skill:
        return skill_key.replace("_", " ").title()
    return skill[f"name_{lang}"] if f"name_{lang}" in skill else skill["name_en"]


def get_skill_description(skill_key: str) -> str:
    """Get Hungarian description for a skill"""
    skill = ALL_SKILLS.get(skill_key)
    return skill.get("description_hu", "") if skill else ""


def get_category_by_key(category_key: str) -> SkillCategory | None:
    """Get category definition by key"""
    for category in SKILL_CATEGORIES:
        if category["key"] == category_key:
            return category
    return None
