import Foundation

// 21-position taxonomy (v2).
// Canonical values are snake_case strings stored in the DB.
// Mirrors app/utils/football_positions.py — keep in sync.
struct FootballPosition: Identifiable, Equatable, Hashable {
    let id: String        // canonical DB value e.g. "striker"
    let label: String     // "Striker"
    let short: String     // "ST"
    let group: Group

    enum Group: String {
        case forward, midfielder, defender, goalkeeper
        var label: String {
            switch self {
            case .forward:    return "Forwards"
            case .midfielder: return "Midfielders"
            case .defender:   return "Defenders"
            case .goalkeeper: return "Goalkeepers"
            }
        }
    }

    static func == (lhs: FootballPosition, rhs: FootballPosition) -> Bool { lhs.id == rhs.id }
    func hash(into hasher: inout Hasher) { hasher.combine(id) }
}

// MARK: — Static position registry (21 positions)

extension FootballPosition {

    static let all: [FootballPosition] = [
        // Forwards (5)
        .init(id: "striker",               label: "Striker",               short: "ST",  group: .forward),
        .init(id: "centre_forward",        label: "Centre Forward",        short: "CF",  group: .forward),
        .init(id: "left_wing",             label: "Left Wing",             short: "LW",  group: .forward),
        .init(id: "right_wing",            label: "Right Wing",            short: "RW",  group: .forward),
        .init(id: "second_striker",        label: "Second Striker",        short: "SS",  group: .forward),
        // Midfielders (7)
        .init(id: "attacking_midfield",    label: "Attacking Midfielder",  short: "AM",  group: .midfielder),
        .init(id: "centre_midfield",       label: "Central Midfielder",    short: "CM",  group: .midfielder),
        .init(id: "defensive_midfield",    label: "Defensive Midfielder",  short: "DM",  group: .midfielder),
        .init(id: "left_midfield",         label: "Left Midfielder",       short: "LM",  group: .midfielder),
        .init(id: "right_midfield",        label: "Right Midfielder",      short: "RM",  group: .midfielder),
        .init(id: "left_centre_midfield",  label: "Left Centre Mid",       short: "LCM", group: .midfielder),
        .init(id: "right_centre_midfield", label: "Right Centre Mid",      short: "RCM", group: .midfielder),
        // Defenders (7)
        .init(id: "centre_back",           label: "Centre Back",           short: "CB",  group: .defender),
        .init(id: "left_back",             label: "Left Back",             short: "LB",  group: .defender),
        .init(id: "right_back",            label: "Right Back",            short: "RB",  group: .defender),
        .init(id: "left_wing_back",        label: "Left Wing Back",        short: "LWB", group: .defender),
        .init(id: "right_wing_back",       label: "Right Wing Back",       short: "RWB", group: .defender),
        .init(id: "left_centre_back",      label: "Left Centre-Back",      short: "LCB", group: .defender),
        .init(id: "right_centre_back",     label: "Right Centre-Back",     short: "RCB", group: .defender),
        // Goalkeepers (2)
        .init(id: "goalkeeper",            label: "Goalkeeper",            short: "GK",  group: .goalkeeper),
        .init(id: "sweeper_keeper",        label: "Sweeper Keeper",        short: "SK",  group: .goalkeeper),
    ]

    static func byId(_ id: String) -> FootballPosition? {
        all.first { $0.id == id }
    }

    // Positions with no pitch node — shown as chip buttons below the pitch.
    // Mirrors pitch-selector.js which omits second_striker and centre_back from node list.
    static let noPitchPositions: [FootballPosition] = [
        byId("second_striker")!,
        byId("centre_back")!,
    ]
}

// MARK: — Pitch node (visual layout)

// A tappable node on the SwiftUI pitch map.
// ST has two visual nodes (ST1, ST2) both mapping to canonical "striker".
// x/y are fractions of the pitch interior (0-1), matching pitch-selector.js PITCH_NODES.
struct PitchNode: Identifiable {
    let id: String          // unique node key: "GK", "ST1", "ST2", etc.
    let positionId: String  // canonical FootballPosition.id this node represents
    let short: String       // display abbreviation on the button
    let x: Double           // horizontal fraction: 0 = GK end, 1 = ST end
    let y: Double           // vertical fraction:   0 = top,    1 = bottom
}

extension FootballPosition {

    static let pitchNodes: [PitchNode] = [
        PitchNode(id: "GK",  positionId: "goalkeeper",            short: "GK",  x: 0.02, y: 0.50),
        PitchNode(id: "SK",  positionId: "sweeper_keeper",        short: "SK",  x: 0.10, y: 0.50),
        PitchNode(id: "LB",  positionId: "left_back",             short: "LB",  x: 0.19, y: 0.15),
        PitchNode(id: "LCB", positionId: "left_centre_back",      short: "LCB", x: 0.19, y: 0.37),
        PitchNode(id: "RCB", positionId: "right_centre_back",     short: "RCB", x: 0.19, y: 0.63),
        PitchNode(id: "RB",  positionId: "right_back",            short: "RB",  x: 0.19, y: 0.85),
        PitchNode(id: "LWB", positionId: "left_wing_back",        short: "LWB", x: 0.28, y: 0.10),
        PitchNode(id: "RWB", positionId: "right_wing_back",       short: "RWB", x: 0.28, y: 0.90),
        PitchNode(id: "DM",  positionId: "defensive_midfield",    short: "DM",  x: 0.37, y: 0.50),
        PitchNode(id: "LCM", positionId: "left_centre_midfield",  short: "LCM", x: 0.47, y: 0.33),
        PitchNode(id: "CM",  positionId: "centre_midfield",       short: "CM",  x: 0.50, y: 0.50),
        PitchNode(id: "RCM", positionId: "right_centre_midfield", short: "RCM", x: 0.47, y: 0.67),
        PitchNode(id: "LM",  positionId: "left_midfield",         short: "LM",  x: 0.55, y: 0.17),
        PitchNode(id: "RM",  positionId: "right_midfield",        short: "RM",  x: 0.55, y: 0.83),
        PitchNode(id: "AM",  positionId: "attacking_midfield",    short: "AM",  x: 0.68, y: 0.50),
        PitchNode(id: "LW",  positionId: "left_wing",             short: "LW",  x: 0.73, y: 0.07),
        PitchNode(id: "RW",  positionId: "right_wing",            short: "RW",  x: 0.73, y: 0.93),
        PitchNode(id: "CF",  positionId: "centre_forward",        short: "CF",  x: 0.83, y: 0.50),
        PitchNode(id: "ST1", positionId: "striker",               short: "ST",  x: 0.88, y: 0.34),
        PitchNode(id: "ST2", positionId: "striker",               short: "ST",  x: 0.88, y: 0.66),
    ]
}
