import SwiftUI

// Live-updating Academy ID preview card — RegisterView preview and Profile view.
//
// Layout:
//   Header — LFA branding + ACADEMY ID label
//   Body   — 80×104pt portrait panel (left) | name/profile/location fields (right)
//   Specs  — ⚽ — 🎓 — 🥋 — 💼 — placeholder slots
//   Footer — lfa_academy_id (left) | QR code 56×56pt (right) | ACCESS VERIFIED badge
//
// Photo priority (first non-nil wins):
//   1. profilePhotoProcessedURL — backend BG-removed transparent PNG
//   2. profilePhotoURL          — backend raw upload
//   3. profileImage             — local UIImage (RegisterView preview, never uploaded)
//   4. silhouette placeholder
//
// QR: generated from publicToken via QRCodeGenerator (CoreImage, no external dep).
//   nil → placeholder qrcode SF Symbol shown instead.
//
// colorConfig: optional AcademyIDColorConfig — nil = official (default system surface).
//   RegisterView and other registration-flow call sites pass nil for backward compat.
struct AcademyIDCardView: View {
    let firstName:                String?
    let lastName:                 String?
    let nickname:                 String?
    let age:                      Int?
    let nationality:              String    // always present — defaults to "HU"
    let gender:                   String?
    let city:                     String?
    let country:                  String?
    let profileImage:             UIImage?  // local preview (RegisterView)
    let profilePhotoURL:          String?   // backend original URL (Academy ID Phase 1)
    let profilePhotoProcessedURL: String?   // backend BG-removed PNG (when rembg active)
    let isVerified:               Bool
    // Phase 2A — Academy ID fields (nil during registration flow, set from /me after login)
    let lfaAcademyId:             String?   // "LFA-2026-00142" — shown on card
    let publicToken:              String?   // UUID for QR — build with VERIFY_BASE_URL
    // Colour system — nil = official appearance (backward-compatible default)
    let colorConfig:              AcademyIDColorConfig?

    // Forces re-render on dark/light mode switch so official's UIKit dynamic
    // colors (Color(UIColor.label) etc.) resolve correctly for the active mode.
    @Environment(\.colorScheme) private var colorScheme

    // MARK: — Derived colours (fall back to system tokens when colorConfig is nil)
    // Uses explicit stored tokens from AcademyIDColorConfig — NOT derived from
    // isLightSurface — so ivory always gives dark text even in iOS dark mode.

    private var cardSurface:       Color  { colorConfig?.surfaceColor  ?? Color(UIColor.secondarySystemBackground) }
    private var cardBorderColor:   Color  { colorConfig?.borderColor   ?? Color(hex: "#b8a06a") }
    private var cardBorderOpacity: Double { colorConfig?.borderOpacity ?? 0.28 }
    private var textValue:         Color  { colorConfig?.textPrimary   ?? Color(UIColor.label) }
    private var textLabel:         Color  { colorConfig?.textSecondary ?? Color(UIColor.secondaryLabel) }
    private var textMuted:         Color  { colorConfig?.textMuted     ?? Color(UIColor.tertiaryLabel) }
    private var textBrand:         Color  { colorConfig?.textBrand     ?? Theme.Color.secondary }
    private var photoBorder:       Color  { colorConfig?.panelBorder   ?? Theme.Color.secondary.opacity(0.30) }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            headerRow
            Divider().background(cardBorderColor.opacity(0.20))
            bodyRows
            Divider().background(cardBorderColor.opacity(0.12))
            specSlotsRow
            Divider().background(cardBorderColor.opacity(0.12))
            footerRow
        }
        .background(cardSurface)
        .cornerRadius(Theme.Radius.md)
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.md)
                .stroke(cardBorderColor.opacity(cardBorderOpacity), lineWidth: 1)
        )
        .shadow(color: .black.opacity(0.06), radius: 6, x: 0, y: 2)
        .animation(.easeInOut(duration: 0.25), value: colorConfig?.id)
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
                    .foregroundColor(textBrand)
                Text("LFA EDUCATION CENTER")
                    .font(.system(size: 7, weight: .medium))
                    .foregroundColor(textLabel)
            }
            Spacer()
            Text("ACADEMY ID")
                .font(.system(size: 7, weight: .bold))
                .foregroundColor(textLabel)
                .tracking(0.8)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    // MARK: — Body (portrait photo panel left | data fields right)

    private var bodyRows: some View {
        HStack(alignment: .top, spacing: 14) {
            photoPanel
            VStack(alignment: .leading, spacing: 7) {
                fieldBlock(label: "FULL NAME", value: fullName)
                fieldBlock(label: "NICKNAME",  value: nickname)
                HStack(spacing: 0) {
                    fieldBlock(label: "AGE",         value: age.map { "\($0) years" })
                    Spacer()
                    fieldBlock(label: "NATIONALITY", value: nationalityDisplay, align: .center)
                    Spacer()
                    fieldBlock(label: "GENDER",      value: genderDisplay, align: .trailing)
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
            Text("SPECIALIZATION")
                .font(.system(size: 7, weight: .semibold))
                .foregroundColor(textLabel)
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

    // MARK: — Footer (Academy ID + QR panel)

    private var footerRow: some View {
        HStack(alignment: .center, spacing: 10) {
            VStack(alignment: .leading, spacing: 5) {
                Text(lfaAcademyId ?? "LFA-????-?????")
                    .font(.system(size: 8, weight: .bold, design: .monospaced))
                    .foregroundColor(lfaAcademyId != nil ? textLabel : textLabel.opacity(0.35))
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
                    .transition(.opacity.combined(with: .scale(scale: 0.75, anchor: .leading)))
                }
            }
            .animation(.spring(response: 0.3, dampingFraction: 0.7), value: isVerified)

            Spacer()
            qrPanel
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }

    // QR panel: shows generated QR when publicToken is available, placeholder otherwise.
    @ViewBuilder
    private var qrPanel: some View {
        if let token = publicToken,
           let qrImage = QRCodeGenerator.image(
               from: "\(APIConfig.verifyBaseURL)/verify/\(token)"
           ) {
            Image(uiImage: qrImage)
                .interpolation(.none)
                .resizable()
                .scaledToFit()
                .frame(width: 56, height: 56)
                .background(Color.white)
                .cornerRadius(4)
                .overlay(
                    RoundedRectangle(cornerRadius: 4)
                        .stroke(photoBorder, lineWidth: 0.5)
                )
        } else {
            RoundedRectangle(cornerRadius: 4)
                .fill(textMuted.opacity(0.15))
                .frame(width: 56, height: 56)
                .overlay(
                    Image(systemName: "qrcode")
                        .font(.system(size: 22))
                        .foregroundColor(textMuted)
                )
        }
    }

    // MARK: — Portrait photo panel (ID-card style, 80×104pt, not circular)

    private var photoPanel: some View {
        ZStack {
            if let url = profilePhotoProcessedURL {
                URLPhotoView(urlPath: url)
                    .frame(width: 80, height: 104).clipped()
            } else if let url = profilePhotoURL {
                URLPhotoView(urlPath: url)
                    .frame(width: 80, height: 104).clipped()
            } else if let img = profileImage {
                Image(uiImage: img)
                    .resizable().scaledToFill()
                    .frame(width: 80, height: 104).clipped()
            } else {
                Rectangle()
                    .fill(textMuted.opacity(0.15))
                    .frame(width: 80, height: 104)
                    .overlay(
                        VStack(spacing: 5) {
                            Image(systemName: "person.fill")
                                .font(.system(size: 30))
                                .foregroundColor(textMuted)
                            Text("PHOTO")
                                .font(.system(size: 6.5, weight: .semibold))
                                .foregroundColor(textMuted)
                        }
                    )
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .overlay(
            RoundedRectangle(cornerRadius: 6)
                .stroke(photoBorder, lineWidth: 1)
        )
    }

    // MARK: — Field block

    @ViewBuilder
    private func fieldBlock(label: String, value: String?, align: HorizontalAlignment = .leading) -> some View {
        VStack(alignment: align, spacing: 2) {
            Text(label)
                .font(.system(size: 7, weight: .semibold))
                .foregroundColor(textLabel)
            if let v = value {
                Text(v)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(textValue)
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
                    .transition(.opacity)
            } else {
                Text("———")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(textMuted)
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
                .foregroundColor(textMuted)
        }
    }

    // MARK: — Computed display values

    private var fullName: String? {
        let parts = [firstName, lastName].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " ")
    }

    private var nationalityDisplay: String? {
        guard !nationality.isEmpty else { return nil }
        let flags: [String: String] = [
            "HU": "🇭🇺", "AT": "🇦🇹", "DE": "🇩🇪", "SK": "🇸🇰",
            "RO": "🇷🇴", "RS": "🇷🇸", "HR": "🇭🇷", "SI": "🇸🇮",
            "UA": "🇺🇦", "PL": "🇵🇱", "CZ": "🇨🇿", "Other": "🌐",
        ]
        let names: [String: String] = [
            "HU": "Hungarian",  "AT": "Austrian",  "DE": "German",
            "SK": "Slovak",     "RO": "Romanian",  "RS": "Serbian",
            "HR": "Croatian",   "SI": "Slovenian", "UA": "Ukrainian",
            "PL": "Polish",     "CZ": "Czech",     "Other": "Other",
        ]
        let flag = flags[nationality] ?? ""
        let name = names[nationality] ?? nationality
        return flag.isEmpty ? name : "\(flag) \(name)"
    }

    private var genderDisplay: String? {
        switch gender {
        case "Male", "Female", "Other": return gender
        default:                         return nil
        }
    }

    private var locationDisplay: String? {
        let parts = [city, country].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: ", ")
    }
}

// MARK: — URL-based image loader (iOS 14 compatible, no AsyncImage)

private struct URLPhotoView: View {
    let urlPath: String
    @State private var image: UIImage? = nil

    var body: some View {
        Group {
            if let img = image {
                Image(uiImage: img)
                    .resizable()
                    .scaledToFill()
            } else {
                Rectangle()
                    .fill(Theme.Color.muted.opacity(0.08))
                    .overlay(ProgressView().scaleEffect(0.6))
            }
        }
        .onAppear { loadImage() }
    }

    private func loadImage() {
        guard image == nil,
              let url = URL(string: APIConfig.baseURL + urlPath) else { return }
        URLSession.shared.dataTask(with: url) { data, _, _ in
            guard let data = data, let img = UIImage(data: data) else { return }
            DispatchQueue.main.async { self.image = img }
        }.resume()
    }
}
