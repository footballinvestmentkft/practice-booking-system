import Foundation

// Football skill taxonomy — mirrors app/skills_config.py exactly.
// Keys, order, and category boundaries must match the backend.
// 44 skills total: Outfield(19) + Set Pieces(3) + Mental(14) + Physical(8).

struct SkillDefinition {
    let key:    String   // snake_case — must match DB / backend key
    let nameEn: String   // English display label
}

struct SkillCategory {
    let nameEn: String
    let skills: [SkillDefinition]
}

enum SkillConfig {

    // MARK: — Categories (order = backend SKILL_CATEGORIES order)

    static let categories: [SkillCategory] = [
        SkillCategory(nameEn: "Outfield Skills", skills: outfield),
        SkillCategory(nameEn: "Set Pieces",      skills: setPieces),
        SkillCategory(nameEn: "Mental & Tactical", skills: mental),
        SkillCategory(nameEn: "Physical Fitness", skills: physical),
    ]

    // MARK: — Flat key list (used for payload building)

    static let allKeys: [String] = categories.flatMap { $0.skills.map(\.key) }

    // MARK: — Outfield (19)

    private static let outfield: [SkillDefinition] = [
        SkillDefinition(key: "ball_control",  nameEn: "Ball Control"),
        SkillDefinition(key: "dribbling",     nameEn: "Dribbling"),
        SkillDefinition(key: "finishing",     nameEn: "Finishing"),
        SkillDefinition(key: "shot_power",    nameEn: "Shot Power"),
        SkillDefinition(key: "long_shots",    nameEn: "Long Shots"),
        SkillDefinition(key: "volleys",       nameEn: "Volleys"),
        SkillDefinition(key: "crossing",      nameEn: "Crossing"),
        SkillDefinition(key: "passing",       nameEn: "Passing"),
        SkillDefinition(key: "heading",       nameEn: "Heading"),
        SkillDefinition(key: "tackle",        nameEn: "Tackle"),
        SkillDefinition(key: "marking",       nameEn: "Marking"),
        SkillDefinition(key: "shooting",      nameEn: "Shooting"),
        SkillDefinition(key: "technique",     nameEn: "Technique"),
        SkillDefinition(key: "creativity",    nameEn: "Creativity"),
        SkillDefinition(key: "long_passing",  nameEn: "Long Passing"),
        SkillDefinition(key: "flair",         nameEn: "Flair"),
        SkillDefinition(key: "touch",         nameEn: "Touch"),
        SkillDefinition(key: "forward_runs",  nameEn: "Forward Runs"),
        SkillDefinition(key: "throwing",      nameEn: "Throwing"),
    ]

    // MARK: — Set Pieces (3)

    private static let setPieces: [SkillDefinition] = [
        SkillDefinition(key: "free_kicks", nameEn: "Free Kicks"),
        SkillDefinition(key: "corners",    nameEn: "Corners"),
        SkillDefinition(key: "penalties",  nameEn: "Penalties"),
    ]

    // MARK: — Mental (14)

    private static let mental: [SkillDefinition] = [
        SkillDefinition(key: "positioning_off",    nameEn: "Positioning (Off)"),
        SkillDefinition(key: "positioning_def",    nameEn: "Positioning (Def)"),
        SkillDefinition(key: "vision",             nameEn: "Vision"),
        SkillDefinition(key: "aggression",         nameEn: "Aggression"),
        SkillDefinition(key: "reactions",          nameEn: "Reactions"),
        SkillDefinition(key: "composure",          nameEn: "Composure"),
        SkillDefinition(key: "consistency",        nameEn: "Consistency"),
        SkillDefinition(key: "tactical_awareness", nameEn: "Tactical Awareness"),
        SkillDefinition(key: "anticipation",       nameEn: "Anticipation"),
        SkillDefinition(key: "concentration",      nameEn: "Concentration"),
        SkillDefinition(key: "decisions",          nameEn: "Decisions"),
        SkillDefinition(key: "determination",      nameEn: "Determination"),
        SkillDefinition(key: "teamwork",           nameEn: "Teamwork"),
        SkillDefinition(key: "leadership",         nameEn: "Leadership"),
    ]

    // MARK: — Physical (8)

    private static let physical: [SkillDefinition] = [
        SkillDefinition(key: "acceleration",  nameEn: "Acceleration"),
        SkillDefinition(key: "sprint_speed",  nameEn: "Sprint Speed"),
        SkillDefinition(key: "agility",       nameEn: "Agility"),
        SkillDefinition(key: "jumping",       nameEn: "Jumping"),
        SkillDefinition(key: "strength",      nameEn: "Strength"),
        SkillDefinition(key: "stamina",       nameEn: "Stamina"),
        SkillDefinition(key: "balance",       nameEn: "Balance"),
        SkillDefinition(key: "work_rate",     nameEn: "Work Rate"),
    ]
}
