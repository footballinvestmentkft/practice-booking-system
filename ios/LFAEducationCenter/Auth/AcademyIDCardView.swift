import SwiftUI

// Live-updating Academy ID preview card shown throughout RegisterView steps.
//
// All name/location fields are optional — nil renders a muted "———" placeholder.
// When a field transitions nil → value, an opacity fade-in animates in.
// The INVITED badge spring-animates in when isInvited becomes true.
struct AcademyIDCardView: View {
    let firstName:   String?
    let lastName:    String?
    let nickname:    String?
    let age:         Int?
    let nationality: String   // always present — defaults to "HU" in RegisterView
    let gender:      String?  // "Male" / "Female" / "Other"
    let city:        String?
    let country:     String?
    let isInvited:   Bool
    let lfaID:       String

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            headerRow
            Divider().background(Theme.Color.secondary.opacity(0.2))
            bodyRows
            Divider().background(Theme.Color.secondary.opacity(0.12))
            footerRow
        }
        .background(Theme.Color.surface)
        .cornerRadius(Theme.Radius.md)
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.md)
                .stroke(Theme.Color.secondary.opacity(0.25), lineWidth: 1)
        )
    }

    // MARK: — Header

    private var headerRow: some View {
        HStack(spacing: 8) {
            Image("LFALogo")
                .resizable()
                .scaledToFit()
                .frame(height: 22)
            VStack(alignment: .leading, spacing: 1) {
                Text("LION FOOTBALL ACADEMY")
                    .font(.system(size: 8, weight: .bold))
                    .foregroundColor(Theme.Color.secondary)
                Text("LFA EDUCATION CENTER")
                    .font(.system(size: 6.5, weight: .medium))
                    .foregroundColor(Theme.Color.muted)
            }
            Spacer()
            Text("ACADEMY ID")
                .font(.system(size: 6.5, weight: .bold))
                .foregroundColor(Theme.Color.muted)
                .tracking(0.5)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    // MARK: — Body

    private var bodyRows: some View {
        VStack(alignment: .leading, spacing: 5) {
            // Row 1: Full Name | Nickname
            HStack(alignment: .top) {
                fieldBlock(label: "FULL NAME", value: fullName)
                Spacer()
                fieldBlock(label: "NICKNAME", value: nickname, align: .trailing)
            }

            // Row 2: Age | Nationality | Gender
            HStack {
                fieldBlock(label: "AGE", value: age.map { "\($0)" })
                Spacer()
                fieldBlock(label: "NATIONALITY", value: nationalityDisplay, align: .center)
                Spacer()
                fieldBlock(label: "GENDER", value: genderShort, align: .trailing)
            }

            // Row 3: Location
            fieldBlock(label: "LOCATION", value: locationDisplay)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    // MARK: — Footer

    private var footerRow: some View {
        HStack {
            Text("LFA-\(lfaID)")
                .font(.system(size: 7.5, weight: .bold, design: .monospaced))
                .foregroundColor(Theme.Color.muted)
            Spacer()
            if isInvited {
                Text("● INVITED")
                    .font(.system(size: 8, weight: .bold))
                    .foregroundColor(Theme.Color.primary)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 3)
                    .background(Theme.Color.primary.opacity(0.10))
                    .cornerRadius(4)
                    .transition(.opacity.combined(with: .scale(scale: 0.75, anchor: .trailing)))
            }
        }
        .animation(.spring(response: 0.3, dampingFraction: 0.7), value: isInvited)
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
    }

    // MARK: — Field block

    @ViewBuilder
    private func fieldBlock(label: String, value: String?, align: HorizontalAlignment = .leading) -> some View {
        VStack(alignment: align, spacing: 2) {
            Text(label)
                .font(.system(size: 6.5, weight: .semibold))
                .foregroundColor(Theme.Color.muted)
            if let v = value {
                Text(v)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(Theme.Color.onSurface)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                    .transition(.opacity)
            } else {
                Text("———")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(Theme.Color.muted.opacity(0.3))
                    .transition(.opacity)
            }
        }
        .animation(.easeIn(duration: 0.2), value: value)
    }

    // MARK: — Computed display values

    private var fullName: String? {
        let parts = [firstName, lastName].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " ")
    }

    private var nationalityDisplay: String? {
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
