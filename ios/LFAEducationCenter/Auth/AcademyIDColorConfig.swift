import SwiftUI

// iOS-side rendering tokens for the Academy ID card colour system.
//
// DESIGN RULE: every theme defines ALL colour tokens explicitly.
// No token is derived from isLightSurface at runtime — this prevents the
// dark/light system-mode mismatch where e.g. ivory (fixed cream surface)
// would pick up Color(UIColor.label) = white in dark mode, producing
// white-on-cream illegible text.
//
//   official → adaptive (UIKit dynamic colors follow system dark/light mode)
//   ivory    → always cream surface, always dark warm-brown text
//   charcoal → always near-black surface, always white text
//
// Phase 1: official / ivory / charcoal (flat colours, no gradient).
// Phase 2: will extend with gradient surfaces (surfaceEnd field).

struct AcademyIDColorConfig {

    let id:            String
    let surfaceColor:  Color
    let borderColor:   Color
    let borderOpacity: Double
    let isLightSurface: Bool  // kept for callers that need a quick boolean (e.g. glow colour)

    // Explicit colour tokens — single source of truth for all text/icon rendering on the card.
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

        case "ivory":
            // Fixed cream surface — text is always dark warm-brown regardless of system mode.
            let gold = Color(hex: "#b8a06a")
            return AcademyIDColorConfig(
                id:            "ivory",
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
            // Fixed dark surface — text is always white regardless of system mode.
            return AcademyIDColorConfig(
                id:            "charcoal",
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

        default: // "official" — fully adaptive: UIKit dynamic colors follow system mode
            let gold = Color(hex: "#b8a06a")
            return AcademyIDColorConfig(
                id:            "official",
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
