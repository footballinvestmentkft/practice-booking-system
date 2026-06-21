import SwiftUI
import UIKit

// Interactive Academy ID card preview — opened via fullScreenCover when
// the user taps the static card on the My Academy ID base page.
//
// Front face: AcademyIDPreviewFrontFaceView — portrait layout, same colorConfig tokens.
// Back face:  Lion logo only — no QR, no user data, no header/footer, no badges.
//
// Size fix: both faces share an explicit portrait frame on the ZStack.
//   ZStack gets .frame(maxWidth: .infinity).aspectRatio(1/cardAspectRatio, contentMode: .fit)
//   Both AcademyIDPreviewFrontFaceView and AcademyIDBackFaceView use
//   .frame(maxWidth: .infinity, maxHeight: .infinity) to fill the container.
//   This guarantees identical dimensions on both sides — no size jump on flip.
//
// Interaction:
//   swipe left  → front to back  (medium haptic on commit)
//   swipe right → back to front  (medium haptic on commit)
//   commit threshold: |translation| > 80 pt OR |predictedEnd| > 150 pt
//   live proportional rotation: 1 pt ≈ 0.7°, capped at ±90°
//
// QR: NOT included — always stays on the base My Academy ID page.

// MARK: — Card aspect ratio (portrait ID card: 85.6mm × 54mm flipped to portrait)

private let cardAspectRatio: CGFloat = 85.6 / 54.0  // ≈ 1.585 — height / width

// MARK: — Shimmer overlay

private struct ShimmerOverlay: View {
    let cornerRadius: CGFloat
    @State private var phase: CGFloat = -0.3

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            LinearGradient(
                stops: [
                    .init(color: .clear,                location: 0.00),
                    .init(color: .white.opacity(0.13),  location: 0.45),
                    .init(color: .white.opacity(0.26),  location: 0.50),
                    .init(color: .white.opacity(0.13),  location: 0.55),
                    .init(color: .clear,                location: 1.00),
                ],
                startPoint: .topLeading,
                endPoint:   .bottomTrailing
            )
            .frame(width: w * 3)
            .offset(x: (phase - 0.5) * w * 3.5)
            .blendMode(.overlay)
        }
        .clipShape(RoundedRectangle(cornerRadius: cornerRadius))
        .allowsHitTesting(false)
        .onAppear {
            withAnimation(.linear(duration: 3.4).repeatForever(autoreverses: false)) {
                phase = 1.3
            }
        }
    }
}

private extension View {
    func cardShimmer(cornerRadius: CGFloat = Theme.Radius.md) -> some View {
        overlay(ShimmerOverlay(cornerRadius: cornerRadius))
    }
}

// MARK: — Glow modifier

private struct CardGlowModifier: ViewModifier {
    let color:  Color
    let radius: CGFloat
    let pulse:  Bool
    @State private var pulsing = false

    func body(content: Content) -> some View {
        content
            .shadow(color: color.opacity(pulse ? (pulsing ? 0.70 : 0.30) : 0.35), radius: radius,       x: 0, y: 0)
            .shadow(color: color.opacity(pulse ? (pulsing ? 0.35 : 0.12) : 0.18), radius: radius * 1.8, x: 0, y: 0)
            .shadow(color: color.opacity(pulse ? (pulsing ? 0.18 : 0.06) : 0.08), radius: radius * 3.0, x: 0, y: 0)
            .onAppear {
                guard pulse else { return }
                withAnimation(.easeInOut(duration: 1.6).repeatForever(autoreverses: true)) {
                    pulsing = true
                }
            }
    }
}

private extension View {
    func cardGlow(color: Color, radius: CGFloat = 14, pulse: Bool = false) -> some View {
        modifier(CardGlowModifier(color: color, radius: radius, pulse: pulse))
    }
}

// MARK: — URL photo loader (private copy — URLPhotoView in AcademyIDCardView is private)

private struct URLPhotoViewP: View {
    let urlPath: String
    @State private var image: UIImage?

    var body: some View {
        Group {
            if let img = image {
                Image(uiImage: img).resizable().scaledToFill()
            } else {
                Color(UIColor.secondarySystemFill)
                    .overlay(ProgressView().scaleEffect(0.7))
            }
        }
        .onAppear { load() }
    }

    private func load() {
        guard image == nil, let url = URL(string: APIConfig.baseURL + urlPath) else { return }
        URLSession.shared.dataTask(with: url) { data, _, _ in
            guard let data, let img = UIImage(data: data) else { return }
            DispatchQueue.main.async { image = img }
        }.resume()
    }
}

// MARK: — Portrait front face
// Layout: header / large photo / data fields / spec slots / footer (no QR).
// Both surface and text colours come from colorConfig tokens so Official / Ivory /
// Charcoal all render correctly.  .frame(maxWidth: .infinity, maxHeight: .infinity)
// fills whichever fixed container the caller provides.

private struct AcademyIDPreviewFrontFaceView: View {
    let firstName:                String?
    let lastName:                 String?
    let nickname:                 String?
    let age:                      Int?
    let nationality:              String
    let gender:                   String?
    let city:                     String?
    let country:                  String?
    let profilePhotoURL:          String?
    let profilePhotoProcessedURL: String?
    let isVerified:               Bool
    let lfaAcademyId:             String?
    let colorConfig:              AcademyIDColorConfig

    @Environment(\.colorScheme) private var colorScheme

    private var surface:   Color  { colorConfig.surfaceColor }
    private var border:    Color  { colorConfig.borderColor }
    private var borderOp:  Double { colorConfig.borderOpacity }
    private var valColor:  Color  { colorConfig.textPrimary }
    private var lblColor:  Color  { colorConfig.textSecondary }
    private var mutColor:  Color  { colorConfig.textMuted }
    private var brdColor:  Color  { colorConfig.textBrand }
    private var pnlBorder: Color  { colorConfig.panelBorder }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider().background(border.opacity(0.20))
            photo
                .frame(maxWidth: .infinity)
                .frame(maxHeight: .infinity)
                .clipped()
                .layoutPriority(1)
            Divider().background(border.opacity(0.15))
            fields
            Divider().background(border.opacity(0.12))
            specRow
            Divider().background(border.opacity(0.12))
            footer
        }
        .background(surface)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .cornerRadius(Theme.Radius.md)
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.md)
                .stroke(border.opacity(borderOp), lineWidth: 1)
        )
        .shadow(color: .black.opacity(0.06), radius: 6, x: 0, y: 2)
    }

    // MARK: Header

    private var header: some View {
        HStack(spacing: 10) {
            Image("LFALogo")
                .resizable().scaledToFit().frame(height: 26)
            VStack(alignment: .leading, spacing: 2) {
                Text("LION FOOTBALL ACADEMY")
                    .font(.system(size: 9, weight: .bold))
                    .foregroundColor(brdColor)
                Text("LFA EDUCATION CENTER")
                    .font(.system(size: 7, weight: .medium))
                    .foregroundColor(lblColor)
            }
            Spacer()
            Text("ACADEMY ID")
                .font(.system(size: 7, weight: .bold))
                .foregroundColor(lblColor)
                .tracking(0.8)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    // MARK: Photo (full width, fills remaining portrait height)

    @ViewBuilder
    private var photo: some View {
        if let url = profilePhotoProcessedURL {
            URLPhotoViewP(urlPath: url)
        } else if let url = profilePhotoURL {
            URLPhotoViewP(urlPath: url)
        } else {
            ZStack {
                mutColor.opacity(0.08)
                VStack(spacing: 8) {
                    Image(systemName: "person.fill")
                        .font(.system(size: 52))
                        .foregroundColor(mutColor)
                    Text("PHOTO")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundColor(mutColor)
                }
            }
        }
    }

    // MARK: Fields

    private var fields: some View {
        VStack(alignment: .leading, spacing: 7) {
            fieldBlock(label: "FULL NAME", value: fullName)
            fieldBlock(label: "NICKNAME",  value: nickname)
            HStack(spacing: 0) {
                fieldBlock(label: "AGE",         value: age.map { "\($0) years" })
                Spacer()
                fieldBlock(label: "NATIONALITY", value: nationalityDisplay, align: .center)
                Spacer()
                fieldBlock(label: "GENDER",      value: genderDisplay,      align: .trailing)
            }
            fieldBlock(label: "LOCATION", value: locationDisplay)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    @ViewBuilder
    private func fieldBlock(label: String, value: String?,
                            align: HorizontalAlignment = .leading) -> some View {
        VStack(alignment: align, spacing: 2) {
            Text(label)
                .font(.system(size: 7, weight: .semibold))
                .foregroundColor(lblColor)
            if let v = value {
                Text(v)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(valColor)
                    .lineLimit(1).minimumScaleFactor(0.75)
            } else {
                Text("———")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(mutColor)
            }
        }
    }

    // MARK: Spec slots

    private var specRow: some View {
        HStack(spacing: 0) {
            Text("SPECIALIZATION")
                .font(.system(size: 7, weight: .semibold))
                .foregroundColor(lblColor)
            Spacer()
            HStack(spacing: 12) {
                specSlot("⚽"); specSlot("🎓"); specSlot("🥋"); specSlot("💼")
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 7)
    }

    private func specSlot(_ icon: String) -> some View {
        HStack(spacing: 2) {
            Text(icon).font(.system(size: 10))
            Text("—").font(.system(size: 9, weight: .semibold)).foregroundColor(mutColor)
        }
    }

    // MARK: Footer (LFA ID + verified badge — NO QR; QR stays on base page)

    private var footer: some View {
        HStack(alignment: .center, spacing: 10) {
            VStack(alignment: .leading, spacing: 5) {
                Text(lfaAcademyId ?? "LFA-????-?????")
                    .font(.system(size: 8, weight: .bold, design: .monospaced))
                    .foregroundColor(lfaAcademyId != nil ? lblColor : mutColor)
                if isVerified {
                    HStack(spacing: 4) {
                        Image(systemName: "checkmark.shield.fill").font(.system(size: 9))
                        Text("ACCESS VERIFIED").font(.system(size: 8, weight: .bold))
                    }
                    .foregroundColor(Theme.Color.primary)
                    .padding(.horizontal, 7).padding(.vertical, 4)
                    .background(Theme.Color.primary.opacity(0.10))
                    .cornerRadius(5)
                }
            }
            .animation(.spring(response: 0.3, dampingFraction: 0.7), value: isVerified)
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }

    // MARK: Computed values

    private var fullName: String? {
        let parts = [firstName, lastName].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " ")
    }

    private var nationalityDisplay: String? {
        guard !nationality.isEmpty else { return nil }
        let flags = ["HU":"🇭🇺","AT":"🇦🇹","DE":"🇩🇪","SK":"🇸🇰","RO":"🇷🇴",
                     "RS":"🇷🇸","HR":"🇭🇷","SI":"🇸🇮","UA":"🇺🇦","PL":"🇵🇱",
                     "CZ":"🇨🇿","Other":"🌐"]
        let names = ["HU":"Hungarian","AT":"Austrian","DE":"German","SK":"Slovak",
                     "RO":"Romanian","RS":"Serbian","HR":"Croatian","SI":"Slovenian",
                     "UA":"Ukrainian","PL":"Polish","CZ":"Czech","Other":"Other"]
        let flag = flags[nationality] ?? ""
        let name = names[nationality] ?? nationality
        return flag.isEmpty ? name : "\(flag) \(name)"
    }

    private var genderDisplay: String? {
        switch gender {
        case "Male", "Female", "Other": return gender
        default: return nil
        }
    }

    private var locationDisplay: String? {
        let parts = [city, country].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: ", ")
    }
}

// MARK: — Back face (Lion logo only)
// .frame(maxWidth: .infinity, maxHeight: .infinity) fills whichever fixed container
// the caller provides — same as AcademyIDPreviewFrontFaceView.

private struct AcademyIDBackFaceView: View {
    var body: some View {
        ZStack {
            Theme.Color.surface
            Image("LFALogo")
                .resizable().scaledToFit().frame(width: 110).opacity(0.14)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .cornerRadius(Theme.Radius.md)
        .overlay(
            RoundedRectangle(cornerRadius: Theme.Radius.md)
                .stroke(Theme.Color.secondary.opacity(0.28), lineWidth: 1)
        )
        .shadow(color: .black.opacity(0.06), radius: 6, x: 0, y: 2)
    }
}

// MARK: — Preview view

struct AcademyIDCardPreviewView: View {

    let colorConfig:  AcademyIDColorConfig
    let isVerified:   Bool
    let lfaAcademyId: String?
    let publicToken:  String?

    @EnvironmentObject private var dashboardVM: DashboardViewModel
    @Environment(\.presentationMode) private var presentationMode
    @Environment(\.colorScheme)      private var colorScheme

    @State private var isFlipped           = false
    @State private var dragTranslation: CGFloat = 0

    private var interactiveDegrees: CGFloat { min(max(dragTranslation * 0.7, -90), 90) }
    private var frontDegrees: CGFloat       { (isFlipped ? -180 : 0)  + interactiveDegrees }
    private var backDegrees:  CGFloat       { (isFlipped ?    0 : 180) + interactiveDegrees }
    private var frontVisible: Bool          { abs(frontDegrees) < 90 }

    private var glowColor:  Color   { isVerified ? Theme.Color.primary : Theme.Color.secondary }
    private var glowRadius: CGFloat { isVerified ? 18 : 10 }

    var body: some View {
        NavigationView {
            ZStack {
                Color(UIColor.systemGroupedBackground).ignoresSafeArea()

                VStack(spacing: 0) {
                    Spacer(minLength: Theme.Spacing.lg)

                    flipContainer
                        .padding(.horizontal, Theme.Spacing.md)

                    swipeHint
                        .padding(.top, Theme.Spacing.sm)

                    Spacer(minLength: Theme.Spacing.lg)
                }
            }
            .navigationTitle("Kártya megtekintése")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button { presentationMode.wrappedValue.dismiss() } label: {
                        Image(systemName: "xmark")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(Theme.Color.onSurface)
                    }
                }
            }
        }
        .navigationViewStyle(.stack)
    }

    // MARK: — Flip container
    // Both faces share the same frame via:
    //   ZStack → .frame(maxWidth: .infinity) + .aspectRatio(1/cardAspectRatio, contentMode: .fit)
    // Each face: .frame(maxWidth: .infinity, maxHeight: .infinity)
    // Result: both faces are identical portrait rectangles — no size jump on flip.

    private var flipContainer: some View {
        ZStack {
            frontFace
                .cardShimmer()
                .rotation3DEffect(.degrees(frontDegrees), axis: (x: 0, y: 1, z: 0), perspective: 0.4)
                .opacity(frontVisible ? 1 : 0)

            AcademyIDBackFaceView()
                .cardShimmer()
                .rotation3DEffect(.degrees(backDegrees), axis: (x: 0, y: 1, z: 0), perspective: 0.4)
                .opacity(frontVisible ? 0 : 1)
        }
        .frame(maxWidth: .infinity)
        .aspectRatio(1 / cardAspectRatio, contentMode: .fit)
        .cardGlow(color: glowColor, radius: glowRadius, pulse: isVerified)
        .animation(.spring(response: 0.45, dampingFraction: 0.72), value: isFlipped)
        .simultaneousGesture(swipeGesture)
    }

    // MARK: — Front face (portrait layout)

    private var frontFace: some View {
        AcademyIDPreviewFrontFaceView(
            firstName:                cardFirstName,
            lastName:                 cardLastName,
            nickname:                 dashboardVM.profile?.nickname,
            age:                      dashboardVM.profile?.calculatedAge,
            nationality:              dashboardVM.profile?.nationality ?? "",
            gender:                   dashboardVM.profile?.gender,
            city:                     dashboardVM.profile?.city,
            country:                  dashboardVM.profile?.country,
            profilePhotoURL:          dashboardVM.profile?.profilePhotoUrl,
            profilePhotoProcessedURL: dashboardVM.profile?.profilePhotoProcessedUrl,
            isVerified:               isVerified,
            lfaAcademyId:             lfaAcademyId,
            colorConfig:              colorConfig
        )
    }

    // MARK: — Swipe hint

    private var swipeHint: some View {
        HStack(spacing: 5) {
            Image(systemName: "arrow.left").font(.system(size: 10))
            Text(isFlipped ? "Húzd vissza az előoldalhoz" : "Húzd el a hátoldal megtekintéséhez")
                .font(.system(size: 11))
            Image(systemName: "arrow.right").font(.system(size: 10))
        }
        .foregroundColor(Theme.Color.muted)
        .animation(.easeInOut(duration: 0.2), value: isFlipped)
    }

    // MARK: — Swipe gesture

    private var swipeGesture: some Gesture {
        DragGesture(minimumDistance: 10)
            .onChanged { value in dragTranslation = value.translation.width }
            .onEnded { value in
                let t = value.translation.width
                let p = value.predictedEndTranslation.width
                var didFlip = false
                withAnimation(.spring(response: 0.45, dampingFraction: 0.72)) {
                    dragTranslation = 0
                    if (t < -80 || p < -150) && !isFlipped { isFlipped = true;  didFlip = true }
                    if (t >  80 || p >  150) &&  isFlipped { isFlipped = false; didFlip = true }
                }
                if didFlip { UIImpactFeedbackGenerator(style: .medium).impactOccurred() }
            }
    }

    // MARK: — Helpers

    private var cardFirstName: String? {
        guard let name = dashboardVM.profile?.displayName, !name.isEmpty else { return nil }
        return name.split(separator: " ", maxSplits: 1).first.map(String.init)
    }

    private var cardLastName: String? {
        guard let name = dashboardVM.profile?.displayName else { return nil }
        let parts = name.split(separator: " ", maxSplits: 1)
        return parts.count > 1 ? String(parts[1]) : nil
    }
}
