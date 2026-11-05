import SwiftUI

/// 单项能力检查的结论。
///
/// 刻意不用 Bool：能力检查里「未知/待授权」和「不支持」是两类完全不同的情况，
/// 合并成一个布尔值会导致 UI 给出错误结论。
enum CapabilityStatus {
    case ok          // 明确可用
    case fail        // 明确不可用（阻断后续阶段）
    case warning     // 可用但有前提（未授权、模拟器、低电量等）
    case info        // 仅展示信息，不参与结论

    var symbolName: String {
        switch self {
        case .ok: return "checkmark.circle.fill"
        case .fail: return "xmark.octagon.fill"
        case .warning: return "exclamationmark.triangle.fill"
        case .info: return "info.circle"
        }
    }

    var tint: Color {
        switch self {
        case .ok: return .green
        case .fail: return .red
        case .warning: return .orange
        case .info: return .secondary
        }
    }
}

/// 一条能力检查记录。
struct CapabilityItem: Identifiable {
    let id = UUID()
    let title: String
    let detail: String
    let status: CapabilityStatus
    /// 不满足时给用户的可执行建议；满足时为 nil
    let hint: String?
    /// 是否阻断进入下一阶段（M2 采集）
    let isBlocking: Bool

    init(title: String,
         detail: String,
         status: CapabilityStatus,
         hint: String? = nil,
         isBlocking: Bool = false) {
        self.title = title
        self.detail = detail
        self.status = status
        self.hint = hint
        self.isBlocking = isBlocking
    }
}
