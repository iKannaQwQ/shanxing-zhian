import SwiftUI

/// 与网页端一致的配色。
enum Theme {
    static func gradient(_ risk: RiskLevel) -> LinearGradient {
        let colors: [Color]
        switch risk {
        case .safe: colors = [Color(hex: 0x15803D), Color(hex: 0x16A34A)]
        case .low: colors = [Color(hex: 0xA16207), Color(hex: 0xCA8A04)]
        case .medium: colors = [Color(hex: 0xC2410C), Color(hex: 0xEA580C)]
        case .high: colors = [Color(hex: 0x991B1B), Color(hex: 0xDC2626)]
        case .unknown: colors = [Color(hex: 0x374151), Color(hex: 0x6B7280)]
        }
        return LinearGradient(colors: colors, startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    static func chip(_ risk: RiskLevel) -> Color {
        switch risk {
        case .safe: return Color(hex: 0x16A34A)
        case .low: return Color(hex: 0xCA8A04)
        case .medium: return Color(hex: 0xEA580C)
        case .high: return Color(hex: 0xDC2626)
        case .unknown: return Color(hex: 0x6B7280)
        }
    }

    static func tone(_ passability: Passability) -> Color {
        switch passability {
        case .passable: return Color(hex: 0x16A34A)
        case .restricted: return Color(hex: 0xEA580C)
        case .blocked: return Color(hex: 0xDC2626)
        case .unknown: return Color(hex: 0x6B7280)
        }
    }

    static let panelBackground = Color(hex: 0x06090D)
    static let cardBackground = Color(hex: 0x0F172A).opacity(0.04)
    static let mono = Font.system(.caption, design: .monospaced)
}

extension Color {
    init(hex: UInt32) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: 1
        )
    }
}

/// 通用数据卡。
struct DataCard<Content: View>: View {
    let label: String
    let trailing: String?
    let content: () -> Content

    init(label: String, trailing: String? = nil, @ViewBuilder content: @escaping () -> Content) {
        self.label = label
        self.trailing = trailing
        self.content = content
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(label)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Spacer()
                if let trailing {
                    Text(trailing)
                        .font(.caption2.monospaced())
                        .foregroundStyle(.secondary)
                }
            }
            content()
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.cardBackground, in: RoundedRectangle(cornerRadius: 12))
    }
}
