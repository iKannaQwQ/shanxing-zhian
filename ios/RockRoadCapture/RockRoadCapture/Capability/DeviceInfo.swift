import Foundation

/// 设备标识与人类可读型号。
///
/// 说明：ARKit 的能力查询（supportsFrameSemantics）才是权威判据，
/// 型号表只用于在 UI 上给用户解释"为什么这台机器不行"，不参与能力判定。
enum DeviceInfo {

    /// 硬件标识符，例如 "iPhone13,2"；模拟器上为 "arm64" / "x86_64"。
    static var machineIdentifier: String {
        var info = utsname()
        guard uname(&info) == 0 else { return "unknown" }
        return withUnsafePointer(to: &info.machine) {
            $0.withMemoryRebound(to: CChar.self, capacity: 1) {
                String(cString: $0)
            }
        }
    }

    /// 已知型号映射（仅列出与 LiDAR 相关的常见机型）。
    private static let friendlyNames: [String: String] = [
        "iPhone13,2": "iPhone 12 Pro",
        "iPhone13,3": "iPhone 12 Pro Max",
        "iPhone14,4": "iPhone 13 mini",
        "iPhone14,2": "iPhone 13 Pro",
        "iPhone14,3": "iPhone 13 Pro Max",
        "iPhone14,7": "iPhone 14",
        "iPhone14,8": "iPhone 14 Plus",
        "iPhone15,2": "iPhone 14 Pro",
        "iPhone15,3": "iPhone 14 Pro Max",
        "iPhone15,4": "iPhone 15",
        "iPhone15,5": "iPhone 15 Plus",
        "iPhone16,1": "iPhone 15 Pro",
        "iPhone16,2": "iPhone 15 Pro Max",
        "iPhone17,3": "iPhone 16",
        "iPhone17,4": "iPhone 16 Plus",
        "iPhone17,1": "iPhone 16 Pro",
        "iPhone17,2": "iPhone 16 Pro Max",
    ]

    static var modelName: String {
        let id = machineIdentifier
        if let name = friendlyNames[id] { return name }
        if isSimulator { return "模拟器" }
        return "未知机型"
    }

    static var isSimulator: Bool {
        #if targetEnvironment(simulator)
        return true
        #else
        return false
        #endif
    }

    static var systemVersion: String {
        ProcessInfo.processInfo.operatingSystemVersionString
    }
}
