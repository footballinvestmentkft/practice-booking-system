import SwiftUI

// iOS-side rendering tokens for the Academy ID card colour system.
//
// DESIGN RULE: every theme defines ALL colour tokens explicitly.
// No token is derived from isLightSurface at runtime — this prevents the
// dark/light system-mode mismatch where e.g. ivory (fixed cream surface)
// would pick up Color(UIColor.label) = white in dark mode, producing
// white-on-cream illegible text.
//
// Token hierarchy for premium dark cards:
//   textPrimary   → player data VALUES (name, age…)      → premium accent colour
//   textSecondary → field LABELS (FULL NAME, AGE…)        → muted white, low opacity
//   textMuted     → placeholders, spec dashes             → very low opacity
//   textBrand     → "LION FOOTBALL ACADEMY"               → matches textPrimary accent
//   panelBorder   → photo panel + QR panel border         → subtle accent
//
// Phase 1: official / ivory / charcoal
// Phase 2: navy (gold) / burgundy (rose-gold) / forest (platinum)

struct AcademyIDColorConfig {

    let id:            String
    let surfaceColor:  Color
    let borderColor:   Color
    let borderOpacity: Double
    let isLightSurface: Bool

    // Explicit colour tokens — single source of truth for all rendering on the card.
    let textPrimary:   Color   // field values: name, age, nationality…
    let textSecondary: Color   // field label caps: "FULL NAME", "AGE", "SPECIALIZATION"…
    let textMuted:     Color   // placeholders ("———"), spec dashes, muted metadata
    let textBrand:     Color   // header: "LION FOOTBALL ACADEMY"
    let panelBorder:   Color   // photo panel border + QR panel border

    // MARK: — Static resolver

    /// Map a backend colour ID to iOS rendering tokens.
    /// Unknown IDs fall back to "official" (the default adaptive appearance).
    static func resolve(_ colorId: String) -> AcademyIDColorConfig {
        switch colorId {

        // ── Phase 1 free colours ──────────────────────────────────────────────

        case "ivory":
            // Fixed cream surface — text is always dark warm-brown regardless of system mode.
            let gold = Color(hex: "#b8a06a")
            return AcademyIDColorConfig(
                id:             "ivory",
                surfaceColor:   Color(red: 253/255, green: 250/255, blue: 244/255),
                borderColor:    gold,
                borderOpacity:  0.40,
                isLightSurface: true,
                textPrimary:    Color(red: 0.12, green: 0.10, blue: 0.07),
                textSecondary:  Color(red: 0.38, green: 0.33, blue: 0.25),
                textMuted:      Color(red: 0.55, green: 0.50, blue: 0.40).opacity(0.50),
                textBrand:      gold,
                panelBorder:    gold.opacity(0.30)
            )

        case "charcoal":
            // Fixed near-black surface — text is always white regardless of system mode.
            return AcademyIDColorConfig(
                id:             "charcoal",
                surfaceColor:   Color(red: 28/255, green: 28/255, blue: 30/255),
                borderColor:    .white,
                borderOpacity:  0.18,
                isLightSurface: false,
                textPrimary:    .white,
                textSecondary:  Color.white.opacity(0.60),
                textMuted:      Color.white.opacity(0.28),
                textBrand:      Color.white.opacity(0.82),
                panelBorder:    Color.white.opacity(0.15)
            )

        // ── Phase 2 premium colours ───────────────────────────────────────────

        case "navy":
            // Premium Gold Banking — deep navy surface, warm gold player data,
            // muted silver labels. Bank card aesthetic.
            let gold = Color(hex: "#d4af37")       // warm classic gold for player values
            let goldBorder = Color(hex: "#c9a227") // slightly deeper gold for borders
            return AcademyIDColorConfig(
                id:             "navy",
                surfaceColor:   Color(hex: "#09131e"),
                borderColor:    goldBorder,
                borderOpacity:  0.35,
                isLightSurface: false,
                textPrimary:    gold,                            // player data: warm gold
                textSecondary:  Color.white.opacity(0.42),      // labels: muted warm white
                textMuted:      Color.white.opacity(0.20),      // placeholders: very faint
                textBrand:      gold,                           // LION FOOTBALL ACADEMY: gold
                panelBorder:    goldBorder.opacity(0.40)        // photo/QR border: gold
            )

        case "burgundy":
            // Rose Gold Prestige — deep wine surface, champagne/rose-gold player data.
            // Warmer accent than navy gold, different luxury register.
            let champagne = Color(hex: "#e8c4a0")  // champagne / rose-gold for player values
            let roseBorder = Color(hex: "#d4a090") // warm rose for borders
            return AcademyIDColorConfig(
                id:             "burgundy",
                surfaceColor:   Color(hex: "#160509"),
                borderColor:    roseBorder,
                borderOpacity:  0.30,
                isLightSurface: false,
                textPrimary:    champagne,                       // player data: champagne
                textSecondary:  Color.white.opacity(0.38),      // labels: muted pinkish white
                textMuted:      Color.white.opacity(0.18),      // placeholders: very faint
                textBrand:      champagne,                      // LION FOOTBALL ACADEMY: champagne
                panelBorder:    champagne.opacity(0.35)         // photo/QR border: champagne
            )

        case "forest":
            // Platinum Member — deep forest surface, platinum/silver player data.
            // Not gold — distinguished from navy/burgundy, military/institutional prestige.
            let platinum = Color(hex: "#c8dfd0")   // warm platinum-silver for player values
            let silverBorder = Color(hex: "#90c8a8") // green-silver for borders
            let mintBrand = Color(hex: "#a8d8b8")  // softer mint for brand line
            return AcademyIDColorConfig(
                id:             "forest",
                surfaceColor:   Color(hex: "#080f0b"),
                borderColor:    silverBorder,
                borderOpacity:  0.28,
                isLightSurface: false,
                textPrimary:    platinum,                        // player data: platinum-silver
                textSecondary:  Color.white.opacity(0.40),      // labels: cool muted white
                textMuted:      Color.white.opacity(0.18),      // placeholders: very faint
                textBrand:      mintBrand,                      // LION FOOTBALL ACADEMY: soft mint
                panelBorder:    platinum.opacity(0.32)          // photo/QR border: platinum
            )

        // ── Default — official (adaptive) ────────────────────────────────────

        default: // "official" — fully adaptive: UIKit dynamic colors follow system mode
            let gold = Color(hex: "#b8a06a")
            return AcademyIDColorConfig(
                id:             "official",
                surfaceColor:   Color(UIColor.secondarySystemBackground),
                borderColor:    gold,
                borderOpacity:  0.28,
                isLightSurface: true,
                textPrimary:    Color(UIColor.label),
                textSecondary:  Color(UIColor.secondaryLabel),
                textMuted:      Color(UIColor.tertiaryLabel),
                textBrand:      Theme.Color.secondary,
                panelBorder:    Theme.Color.secondary.opacity(0.30)
            )
        }
    }
}

// MARK: — Hex colour initialiser (iOS 14 compatible)

extension Color {
    init(hex: String) {
        let h = hex.trimmingCharacters(in: .init(charactersIn: "#"))
        var rgb: UInt64 = 0
        Scanner(string: h).scanHexInt64(&rgb)
        let r = Double((rgb >> 16) & 0xFF) / 255
        let g = Double((rgb >>  8) & 0xFF) / 255
        let b = Double( rgb        & 0xFF) / 255
        self.init(red: r, green: g, blue: b)
    }
}
