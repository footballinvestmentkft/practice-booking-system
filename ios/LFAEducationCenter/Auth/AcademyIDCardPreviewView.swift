import SwiftUI
import UIKit

// Interactive Academy ID card preview — opened via fullScreenCover when
// the user taps the static card on the My Academy ID base page.
//
// Front face: AcademyIDCardView with the active colorConfig (same data as base page).
// Back face:  Lion logo only — no QR, no user data, no header/footer, no badges.
//
// Interaction:
//   swipe left  → front to back  (medium haptic on commit)
//   swipe right → back to front  (medium haptic on commit)
//   commit threshold: |translation| > 80 pt OR |predictedEnd| > 150 pt
//   live proportional rotation: 1 pt ≈ 0.7°, capped at ±90° while dragging
//
// Effects (only here, never on the base page):
//   shimmer — continuous diagonal light sweep on both faces
//   glow    — coloured shadow; pulses when isVerified
//
// QR: NOT included — always stays on the base My Academy ID page.

// MARK: — Shimmer overlay

private struct ShimmerOverlay: View {
    let cornerRadius: CGFloat
    @State private var phase: CGFloat = -0.3

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            LinearGradient(
                stops: [
                    .init(color: .clear,               location: 0.00),
                    .init(color: .white.opacity(0.13),  location: 0.45),
                    .init(color: .white.opacity(0.26),  location: 0.50),
                    .init(color: .white.opacity(0.13),  location: 0.55),
                    .init(color: .clear,               location: 1.00),
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

// MARK: — Back face (Lion logo only)
// maxWidth/maxHeight .infinity fills the ZStack sized by the front card.
// Strictly no QR, no user data, no header text, no footer text.

private struct AcademyIDBackFaceView: View {
    var body: some View {
        ZStack {
            Theme.Color.surface
            Image("LFALogo")
                .resizable()
                .scaledToFit()
                .frame(width: 110)
                .opacity(0.14)
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

    // Passed from AcademyIDFullScreenView
    let colorConfig:  AcademyIDColorConfig
    let isVerified:   Bool
    let lfaAcademyId: String?
    let publicToken:  String?

    @EnvironmentObject private var dashboardVM: DashboardViewModel
    @Environment(\.presentationMode) private var presentationMode
    @Environment(\.colorScheme)      private var colorScheme

    @State private var isFlipped           = false
    @State private var dragTranslation: CGFloat = 0

    // MARK: — Rotation math

    private var interactiveDegrees: CGFloat { min(max(dragTranslation * 0.7, -90), 90) }
    private var frontDegrees: CGFloat       { (isFlipped ? -180 : 0)  + interactiveDegrees }
    private var backDegrees:  CGFloat       { (isFlipped ?    0 : 180) + interactiveDegrees }
    private var frontVisible: Bool          { abs(frontDegrees) < 90 }

    private var glowColor:  Color    { isVerified ? Theme.Color.primary : Theme.Color.secondary }
    private var glowRadius: CGFloat  { isVerified ? 18 : 10 }

    // MARK: — Body

    var body: some View {
        NavigationView {
            ZStack {
                Color(UIColor.systemGroupedBackground).ignoresSafeArea()

                VStack(spacing: 0) {
                    Spacer()

                    flipContainer
                        .padding(.horizontal, Theme.Spacing.md)

                    swipeHint
                        .padding(.top, Theme.Spacing.sm)

                    Spacer()
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
    // simultaneousGesture: safe even though there's no ScrollView here —
    // ensures the drag isn't accidentally swallowed if the hierarchy changes.

    private var flipContainer: some View {
        ZStack {
            frontCard
                .cardShimmer()
                .rotation3DEffect(
                    .degrees(frontDegrees),
                    axis: (x: 0, y: 1, z: 0),
                    perspective: 0.4
                )
                .opacity(frontVisible ? 1 : 0)

            AcademyIDBackFaceView()
                .cardShimmer()
                .rotation3DEffect(
                    .degrees(backDegrees),
                    axis: (x: 0, y: 1, z: 0),
                    perspective: 0.4
                )
                .opacity(frontVisible ? 0 : 1)
        }
        .cardGlow(color: glowColor, radius: glowRadius, pulse: isVerified)
        .animation(.spring(response: 0.45, dampingFraction: 0.72), value: isFlipped)
        .simultaneousGesture(swipeGesture)
    }

    // MARK: — Front card

    private var frontCard: some View {
        AcademyIDCardView(
            firstName:                cardFirstName,
            lastName:                 cardLastName,
            nickname:                 dashboardVM.profile?.nickname,
            age:                      dashboardVM.profile?.calculatedAge,
            nationality:              dashboardVM.profile?.nationality ?? "",
            gender:                   dashboardVM.profile?.gender,
            city:                     dashboardVM.profile?.city,
            country:                  dashboardVM.profile?.country,
            profileImage:             nil,
            profilePhotoURL:          dashboardVM.profile?.profilePhotoUrl,
            profilePhotoProcessedURL: dashboardVM.profile?.profilePhotoProcessedUrl,
            isVerified:               isVerified,
            lfaAcademyId:             lfaAcademyId,
            publicToken:              publicToken,
            colorConfig:              colorConfig
        )
    }

    // MARK: — Swipe hint

    private var swipeHint: some View {
        HStack(spacing: 5) {
            Image(systemName: "arrow.left")
                .font(.system(size: 10))
            Text(isFlipped ? "Húzd vissza az előoldalhoz" : "Húzd el a hátoldal megtekintéséhez")
                .font(.system(size: 11))
            Image(systemName: "arrow.right")
                .font(.system(size: 10))
        }
        .foregroundColor(Theme.Color.muted)
        .animation(.easeInOut(duration: 0.2), value: isFlipped)
    }

    // MARK: — Swipe gesture
    // left  (translation < 0) → show back
    // right (translation > 0) → show front

    private var swipeGesture: some Gesture {
        DragGesture(minimumDistance: 10)
            .onChanged { value in
                dragTranslation = value.translation.width
            }
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
