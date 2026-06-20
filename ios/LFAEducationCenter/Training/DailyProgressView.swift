import SwiftUI

struct DailyProgressView: View {

    let dailyXpTotal:   Int
    let dailyTasksDone: Int
    let maxXpPerDay:    Int
    let maxTasksPerDay: Int

    var body: some View {
        VStack(spacing: 6) {
            progressRow(
                icon: "bolt.fill",
                color: .yellow,
                label: "XP",
                current: dailyXpTotal,
                max: maxXpPerDay
            )
            progressRow(
                icon: "square.grid.2x2.fill",
                color: Theme.Color.primary,
                label: "Feladat",
                current: dailyTasksDone,
                max: maxTasksPerDay
            )
        }
        .padding(.horizontal, Theme.Spacing.lg)
        .padding(.vertical, Theme.Spacing.xs)
    }

    private func progressRow(
        icon: String,
        color: Color,
        label: String,
        current: Int,
        max: Int
    ) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon)
                .font(.system(size: 11))
                .foregroundColor(color)
                .frame(width: 14)

            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 3)
                        .fill(color.opacity(0.15))
                    RoundedRectangle(cornerRadius: 3)
                        .fill(color.opacity(0.6))
                        .frame(width: geo.size.width * progressFraction(current, max))
                }
            }
            .frame(height: 6)

            Text("\(current)/\(max)")
                .font(.system(size: 11, weight: .semibold).monospacedDigit())
                .foregroundColor(Theme.Color.muted)
                .frame(width: 52, alignment: .trailing)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(label): \(current) / \(max)")
    }

    private func progressFraction(_ current: Int, _ max: Int) -> CGFloat {
        guard max > 0 else { return 0 }
        return min(1.0, CGFloat(current) / CGFloat(max))
    }
}
