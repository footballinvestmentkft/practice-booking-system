import SwiftUI

// Live-updating Academy ID preview card shown throughout RegisterView steps.
//
// Layout:
//   Header — LFA branding + ACADEMY ID label
//   Body   — 80pt photo circle (left) | name/profile/location fields (right)
//   Specs  — ⚽ — 🎓 — 🥋 — 💼 — placeholder slots
//   Footer — LFA-ID | ACCESS VERIFIED badge
//
// Field transitions nil → value animate with opacity fade-in.
// ACCESS VERIFIED badge spring-animates when isVerified becomes true.
// Photo shows as 80pt ID-card circle with gold ring; falls back to person icon.
struct AcademyIDCardView: View {
    let firstName:    String?
    let lastName:     String?
    let nickname:     String?
    let age:          Int?
    let nationality:  String    // always present — defaults to "HU"
    let gender:       String?   // "Male" / "Female" / "Other"
    let city:         String?
    let country:      String?
    let profileImage: UIImage?  // local preview only — not uploaded to backend
    let isVerified:   Bool      // true after successful /invitation-codes/validate
    let lfaID:        String

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            headerRow
            Divider().background(Theme.Color.secondary.opacity(0.2))
            bodyRows
            Divider().background(Theme.Color.secondary.opacity(0.12))
            specSlotsRow
            Divider().background(Theme.Color.secondary.opacity(0.12))
            footerRow
        }
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.md)
                .stroke(Theme.Color.secondary.opacity(0.28), lineWidth: 1)
        )
        .shadow(color: .black.opacity(0.06), radius: 6, x: 0, y: 2)
    }

    // MARK: — Header

    private var headerRow: some View {
        HStack(spacing: 10) {
            Image("LFALogo")
                .resizable()
                .scaledToFit()
                .frame(height: 26)
            VStack(alignment: .leading, spacing: 2) {
                Text("LION FOOTBALL ACADEMY")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundColor(Theme.Color.secondary)
                Text("LFA EDUCATION CENTER")
                    .font(.system(size: 7, weight: .medium))
                    .foregroundColor(Theme.Color.muted)
            }
            Spacer()
            Text("ACADEMY ID")
                .font(.system(size: 7, weight: .bold))
                .foregroundColor(Theme.Color.muted)
                .tracking(0.8)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    // MARK: — Body (80pt photo left | data fields right)

    private var bodyRows: some View {
        HStack(alignment: .top, spacing: 14) {
            // Photo column
            VStack(spacing: 5) {
                photoCircle
                Text("PHOTO")
                    .font(.system(size: 6.5, weight: .semibold))
                    .foregroundColor(Theme.Color.muted.opacity(profileImage == nil ? 0.35 : 0))
            }

            // Data column
            VStack(alignment: .leading, spacing: 7) {
                fieldBlock(label: "FULL NAME", value: fullName)
                fieldBlock(label: "NICKNAME",  value: nickname)

                HStack(spacing: 0) {
                    fieldBlock(label: "AGE",  value: age.map { "\($0)" })
                    Spacer()
                    fieldBlock(label: "NAT",  value: nationalityShort, align: .center)
                    Spacer()
                    fieldBlock(label: "GEN",  value: genderShort, align: .trailing)
                }

                fieldBlock(label: "LOCATION", value: locationDisplay)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
    }

    // MARK: — Specialization slots

    private var specSlotsRow: some View {
        HStack(spacing: 0) {
            Text("SPECS")
                .font(.system(size: 7, weight: .semibold))
                .foregroundColor(Theme.Color.muted)
            Spacer()
            HStack(spacing: 12) {
                specSlot("⚽")
                specSlot("🎓")
                specSlot("🥋")
                specSlot("💼")
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 7)
    }

    // MARK: — Footer

    private var footerRow: some View {
        HStack {
            Text("LFA-\(lfaID)")
                .font(.system(size: 8, weight: .bold, design: .monospaced))
                .foregroundColor(Theme.Color.muted)
            Spacer()
            if isVerified {
                HStack(spacing: 4) {
                    Image(systemName: "checkmark.shield.fill")
                        .font(.system(size: 9))
                    Text("ACCESS VERIFIED")
                        .font(.system(size: 8, weight: .bold))
                }
                .foregroundColor(Theme.Color.primary)
                .padding(.horizontal, 7)
                .padding(.vertical, 4)
                .background(Theme.Color.primary.opacity(0.10))
                .cornerRadius(5)
                .transition(.opacity.combined(with: .scale(scale: 0.75, anchor: .trailing)))
            }
        }
        .animation(.spring(response: 0.3, dampingFraction: 0.7), value: isVerified)
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }

    // MARK: — Photo circle (80pt ID-card style)

    private var photoCircle: some View {
        Group {
            if let img = profileImage {
                Image(uiImage: img)
                    .resizable()
                    .scaledToFill()
                    .frame(width: 80, height: 80)
                    .clipShape(Circle())
            } else {
                Circle()
                    .fill(Theme.Color.muted.opacity(0.08))
                    .frame(width: 80, height: 80)
                    .overlay(
                        Image(systemName: "person.fill")
                            .font(.system(size: 32))
                            .foregroundColor(Theme.Color.muted.opacity(0.3))
                    )
            }
        }
        .overlay(Circle().stroke(Theme.Color.secondary.opacity(0.4), lineWidth: 1.5))
        .shadow(color: .black.opacity(0.08), radius: 4, x: 0, y: 1)
        .animation(.easeIn(duration: 0.2), value: profileImage != nil)
    }

    // MARK: — Field block

    @ViewBuilder
    private func fieldBlock(label: String, value: String?, align: HorizontalAlignment = .leading) -> some View {
        VStack(alignment: align, spacing: 2) {
            Text(label)
                .font(.system(size: 7, weight: .semibold))
                .foregroundColor(Theme.Color.muted)
            if let v = value {
                Text(v)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(Theme.Color.onSurface)
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
                    .transition(.opacity)
            } else {
                Text("———")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(Theme.Color.muted.opacity(0.28))
                    .transition(.opacity)
            }
        }
        .animation(.easeIn(duration: 0.2), value: value)
    }

    // MARK: — Spec slot

    private func specSlot(_ icon: String) -> some View {
        HStack(spacing: 2) {
            Text(icon).font(.system(size: 10))
            Text("—")
                .font(.system(size: 9, weight: .semibold))
                .foregroundColor(Theme.Color.muted.opacity(0.4))
        }
    }

    // MARK: — Computed display values

    private var fullName: String? {
        let parts = [firstName, lastName].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " ")
    }

    // Short nationality for narrow column: just the flag + ISO code
    private var nationalityShort: String? {
        let flags: [String: String] = [
            "HU": "🇭🇺", "AT": "🇦🇹", "DE": "🇩🇪", "SK": "🇸🇰",
            "RO": "🇷🇴", "RS": "🇷🇸", "HR": "🇭🇷", "SI": "🇸🇮",
            "UA": "🇺🇦", "PL": "🇵🇱", "CZ": "🇨🇿", "Other": "🌐"
        ]
        return (flags[nationality] ?? "") + " " + nationality
    }

    private var genderShort: String? {
        switch gender {
        case "Male":   return "M"
        case "Female": return "F"
        case "Other":  return "O"
        default:       return nil
        }
    }

    private var locationDisplay: String? {
        let parts = [city, country].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: ", ")
    }
}
