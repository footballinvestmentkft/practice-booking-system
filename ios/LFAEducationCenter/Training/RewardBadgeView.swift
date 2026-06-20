import SwiftUI

struct RewardBadgeView: View {

    let xpAwarded:     Int
    let creditAwarded: Int
    let onDismiss:     () -> Void

    @State private var opacity: Double  = 0
    @State private var yOffset: CGFloat = 20

    var body: some View {
        HStack(spacing: 6) {
            if xpAwarded > 0 {
                Label("+\(xpAwarded) XP", systemImage: "bolt.fill")
                    .font(.subheadline.weight(.bold))
                    .foregroundColor(.yellow)
            }
            if creditAwarded > 0 {
                if xpAwarded > 0 {
                    Text("·")
                        .foregroundColor(.white.opacity(0.6))
                }
                Label("+\(creditAwarded) Credit", systemImage: "star.fill")
                    .font(.subheadline.weight(.bold))
                    .foregroundColor(.cyan)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(Color.black.opacity(0.82))
        .cornerRadius(20)
        .shadow(color: .black.opacity(0.4), radius: 8, y: 4)
        .opacity(opacity)
        .offset(y: yOffset)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
        .onAppear {
            withAnimation(.easeOut(duration: 0.35)) {
                opacity = 1
                yOffset = 0
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.8) {
                withAnimation(.easeIn(duration: 0.3)) {
                    opacity = 0
                    yOffset = -12
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) {
                    onDismiss()
                }
            }
        }
        .allowsHitTesting(false)
    }

    private var accessibilityText: String {
        var parts: [String] = []
        if xpAwarded > 0 { parts.append("\(xpAwarded) XP kapva") }
        if creditAwarded > 0 { parts.append("\(creditAwarded) kredit kapva") }
        return parts.joined(separator: ", ")
    }
}
