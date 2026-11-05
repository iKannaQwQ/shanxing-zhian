import Foundation

/// 构建标记：用于在诊断信息里区分"你跑的到底是哪个版本"。
/// 每次交付新构建时手动递增。
enum BuildInfo {
    static let tag = "m6-pack-metrics-1"
}
