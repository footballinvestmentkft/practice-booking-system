import SwiftUI

// Live-updating Academy ID preview card — RegisterView preview and (future) Profile view.
//
// Layout:
//   Header — LFA branding + ACADEMY ID label
//   Body   — 80×104pt portrait panel (left) | name/profile/location fields (right)
//   Specs  — ⚽ — 🎓 — 🥋 — 💼 — placeholder slots
//   Footer — LFA-ID | ACCESS VERIFIED badge
//
// Photo priority (first non-nil wins):
//   1. profilePhotoProcessedURL — backend BG-removed transparent PNG
//   2. profilePhotoURL          — backend raw upload
//   3. profileImage             — local UIImage (RegisterView preview, never uploaded)
//   4. silhouette placeholder
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
    let lfaID:                    String

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

    // MARK: — Body (portrait photo panel left | data fields right)

    private var bodyRows: some View {
        HStack(alignment: .top, spacing: 14) {
            // Portrait photo panel (ID-card style, not circular)
            photoPanel

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

    // MARK: — Portrait photo panel (ID-card style, 80×104pt, not circular)
    //
    // Priority: processedURL → originalURL → local UIImage → silhouette

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
                    .fill(Theme.Color.muted.opacity(0.06))
                    .frame(width: 80, height: 104)
                    .overlay(
                        VStack(spacing: 5) {
                            Image(systemName: "person.fill")
                                .font(.system(size: 30))
                                .foregroundColor(Theme.Color.muted.opacity(0.22))
                            Text("PHOTO")
                                .font(.system(size: 6.5, weight: .semibold))
                                .foregroundColor(Theme.Color.muted.opacity(0.22))
                        }
                    )
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .overlay(
            RoundedRectangle(cornerRadius: 6)
                .stroke(Theme.Color.secondary.opacity(0.3), lineWidth: 1)
        )
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

// MARK: — URL-based image loader (iOS 14 compatible, no AsyncImage)

// Loads an image from a server-relative path (e.g. "/static/uploads/profile_photos/...").
// Prepends APIConfig.baseURL. Caches loaded UIImage in @State so re-renders don't refetch.
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
                    .overlay(
                        ProgressView()
                            .scaleEffect(0.6)
                    )
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
