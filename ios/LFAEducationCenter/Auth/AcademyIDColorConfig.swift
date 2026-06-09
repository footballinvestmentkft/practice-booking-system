import SwiftUI

// iOS-side rendering tokens for the Academy ID card colour system.
//
// DESIGN RULE: every theme defines ALL colour tokens explicitly.
// No token is derived from isLightSurface at runtime — this prevents the
// dark/light system-mode mismatch where e.g. ivory (fixed cream surface)
// would pick up Color(UIColor.label) = white in dark mode, producing
// white-on-cream illegible text.
//
//   official  → adaptive (UIKit dynamic colors follow system dark/light mode)
//   ivory     → always cream surface, always dark warm-brown text
//   charcoal  → always near-black surface, always white text
//   navy      → always deep navy, always white text, gold accent    [Phase 2]
//   burgundy  → always deep wine red, always white text             [Phase 2]
//   forest    → always deep forest green, always white text         [Phase 2]

struct AcademyIDColorConfig {

    let id:            String
    let surfaceColor:  Color
    let borderColor:   Color
    let borderOpacity: Double
    let isLightSurface: Bool

    let textPrimary:   Color
    let textSecondary: Color
    let textMuted:     Color
    let textBrand:     Color
    let panelBorder:   Color

    // MARK: — Static resolver

    static func resolve(_ colorId: String) -> AcademyIDColorConfig {
        switch colorId {

        case "ivory":
            let gold = Color(hex: "#b8a06a")
            return AcademyIDColorConfig(
                id: "ivory",
                surfaceColor:  Color(red: 253/255, green: 250/255, blue: 244/255),
                borderColor:   gold,
                borderOpacity: 0.40,
                isLightSurface: true,
                textPrimary:   Color(red: 0.12, green: 0.10, blue: 0.07),
                textSecondary: Color(red: 0.38, green: 0.33, blue: 0.25),
                textMuted:     Color(red: 0.55, green: 0.50, blue: 0.40).opacity(0.50),
                textBrand:     gold,
                panelBorder:   gold.opacity(0.30)
            )

        case "charcoal":
            return AcademyIDColorConfig(
                id: "charcoal",
                surfaceColor:  Color(red: 28/255, green: 28/255, blue: 30/255),
                borderColor:   .white,
                borderOpacity: 0.18,
                isLightSurface: false,
                textPrimary:   .white,
                textSecondary: Color.white.opacity(0.60),
                textMuted:     Color.white.opacity(0.28),
                textBrand:     Color.white.opacity(0.82),
                panelBorder:   Color.white.opacity(0.15)
            )

        case "navy":
            // Deep institutional navy — all tokens fixed, never adaptive.
            let gold = Color(hex: "#b8a06a")
            return AcademyIDColorConfig(
                id: "navy",
                surfaceColor:  Color(red: 13/255,  green: 27/255,  blue: 42/255),
                borderColor:   .white,
                borderOpacity: 0.18,
                isLightSurface: false,
                textPrimary:   .white,
                textSecondary: Color.white.opacity(0.65),
                textMuted:     Color.white.opacity(0.30),
                textBrand:     gold,
                panelBorder:   Color.white.opacity(0.22)
            )

        case "burgundy":
            // Deep prestige wine red — all tokens fixed, never adaptive.
            let rose = Color(red: 1.0, green: 0.78, blue: 0.82)
            return AcademyIDColorConfig(
                id: "burgundy",
                surfaceColor:  Color(red: 42/255, green: 10/255, blue: 20/255),
                borderColor:   .white,
                borderOpacity: 0.18,
                isLightSurface: false,
                textPrimary:   .white,
                textSecondary: Color.white.opacity(0.65),
                textMuted:     Color.white.opacity(0.30),
                textBrand:     rose,
                panelBorder:   Color.white.opacity(0.20)
            )

        case "forest":
            // Deep institutional green — all tokens fixed, never adaptive.
            let mint = Color(red: 0.64, green: 0.90, blue: 0.70)
            return AcademyIDColorConfig(
                id: "forest",
                surfaceColor:  Color(red: 15/255, green: 36/255, blue: 25/255),
                borderColor:   .white,
                borderOpacity: 0.18,
                isLightSurface: false,
                textPrimary:   .white,
                textSecondary: Color.white.opacity(0.65),
                textMuted:     Color.white.opacity(0.30),
                textBrand:     mint,
                panelBorder:   Color.white.opacity(0.20)
            )

        default: // "official" — fully adaptive
            let gold = Color(hex: "#b8a06a")
            return AcademyIDColorConfig(
                id: "official",
                surfaceColor:  Color(UIColor.secondarySystemBackground),
                borderColor:   gold,
                borderOpacity: 0.28,
                isLightSurface: true,
                textPrimary:   Color(UIColor.label),
                textSecondary: Color(UIColor.secondaryLabel),
                textMuted:     Color(UIColor.tertiaryLabel),
                textBrand:     Theme.Color.secondary,
                panelBorder:   Theme.Color.secondary.opacity(0.30)
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
