import Foundation

/// 热自适应策略。
///
/// 背景：实测设备到 `serious` 时，原生帧率从 60 掉到 20，
/// 打包耗时翻倍，队列开始被动丢帧（22–30%）。
/// 与其让队列随机丢帧，不如**主动降采集率**——数据是稀疏的，但是可控、可解释的。
enum ThermalPolicy {

    struct Setting: Equatable {
        var targetFPS: Int
        var jpegQuality: Double
        var label: String
    }

    static func setting(for state: ProcessInfo.ThermalState) -> Setting {
        switch state {
        case .nominal, .fair:
            return Setting(targetFPS: 10, jpegQuality: 0.72, label: "正常")
        case .serious:
            return Setting(targetFPS: 5, jpegQuality: 0.65, label: "降级")
        case .critical:
            return Setting(targetFPS: 3, jpegQuality: 0.60, label: "严重降级")
        @unknown default:
            return Setting(targetFPS: 10, jpegQuality: 0.72, label: "正常")
        }
    }

    static func name(for state: ProcessInfo.ThermalState) -> String {
        switch state {
        case .nominal: return "正常"
        case .fair: return "偏热"
        case .serious: return "过热"
        case .critical: return "严重过热"
        @unknown default: return "未知"
        }
    }
}
