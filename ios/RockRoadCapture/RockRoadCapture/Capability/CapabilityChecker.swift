import ARKit
import AVFoundation
import Foundation

/// M1：ARKit 能力检查。
///
/// 本阶段**不启动 ARSession**、不采集任何数据，只回答一个问题：
/// 「这台设备能不能做 RGB + sceneDepth 同步采集」。
///
/// 权威判据一律取自 ARKit / AVFoundation 的运行时查询；
/// 型号表只用于向用户解释原因，不参与判定。
@MainActor
final class CapabilityChecker: ObservableObject {

    @Published private(set) var items: [CapabilityItem] = []
    @Published private(set) var lastCheckedAt: Date?
    @Published private(set) var isRequestingPermission = false

    /// 是否满足进入 M2（RGB + sceneDepth 采集）的前提。
    var canProceed: Bool {
        !items.contains { $0.isBlocking && $0.status != .ok }
    }

    /// 阻断项清单，用于在界面上直接给出下一步动作。
    var blockers: [CapabilityItem] {
        items.filter { $0.isBlocking && $0.status != .ok }
    }

    func refresh() {
        var result: [CapabilityItem] = []

        // ---- 运行环境 ----
        result.append(CapabilityItem(
            title: "设备",
            detail: "\(DeviceInfo.modelName)（\(DeviceInfo.machineIdentifier)）",
            status: DeviceInfo.isSimulator ? .warning : .info,
            hint: DeviceInfo.isSimulator
                ? "模拟器没有摄像头和 LiDAR，必须使用真机。"
                : nil
        ))

        result.append(CapabilityItem(
            title: "系统版本",
            detail: DeviceInfo.systemVersion,
            status: .info
        ))

        // ---- ARKit 世界追踪 ----
        let worldTrackingOK = ARWorldTrackingConfiguration.isSupported
        result.append(CapabilityItem(
            title: "ARKit 世界追踪",
            detail: worldTrackingOK ? "支持" : "不支持",
            status: worldTrackingOK ? .ok : .fail,
            hint: worldTrackingOK ? nil : "该设备不支持 ARKit 世界追踪，无法采集相机位姿。",
            isBlocking: true
        ))

        // ---- LiDAR sceneDepth（M1 的硬门槛）----
        let depthOK = ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)
        result.append(CapabilityItem(
            title: "LiDAR 深度（sceneDepth）",
            detail: depthOK ? "支持" : "不支持",
            status: depthOK ? .ok : .fail,
            hint: depthOK ? nil : "该设备没有 LiDAR，或系统版本低于 iOS 14。请使用 iPhone 12 Pro 及以上的 Pro 机型。",
            isBlocking: true
        ))

        // ---- 平滑深度（可选能力）----
        let smoothedOK = ARWorldTrackingConfiguration.supportsFrameSemantics(.smoothedSceneDepth)
        result.append(CapabilityItem(
            title: "平滑深度（smoothedSceneDepth）",
            detail: smoothedOK ? "支持（可选）" : "不支持（可选）",
            status: smoothedOK ? .ok : .warning,
            hint: smoothedOK ? nil : "不影响采集，仅影响可选的时序平滑预览。"
        ))

        // ---- 相机权限 ----
        let auth = AVCaptureDevice.authorizationStatus(for: .video)
        let (authStatus, authDetail, authHint): (CapabilityStatus, String, String?) = {
            switch auth {
            case .authorized:
                return (.ok, "已授权", nil)
            case .notDetermined:
                return (.warning, "尚未申请", "点击下方按钮申请相机权限。")
            case .denied:
                return (.fail, "已拒绝", "到「设置 → 隐私与安全性 → 相机」中为本应用开启。")
            case .restricted:
                return (.fail, "受系统限制", "设备策略限制了相机访问。")
            @unknown default:
                return (.fail, "未知状态", "请重新启动应用后重试。")
            }
        }()
        result.append(CapabilityItem(
            title: "相机权限",
            detail: authDetail,
            status: authStatus,
            hint: authHint,
            isBlocking: true
        ))

        items = result
        lastCheckedAt = Date()
    }

    /// 生成纯文本诊断块，便于用户一键复制后反馈。
    ///
    /// 刻意输出「原始值 + 结论」而不是只有结论：
    /// 排查问题时需要知道 supportsFrameSemantics 到底返回了什么。
    func diagnosticText() -> String {
        var lines: [String] = []
        lines.append("MountainRoadCapture / M1 能力检查")
        lines.append("时间: \(ISO8601DateFormatter().string(from: Date()))")
        lines.append("结论: \(canProceed ? "可以进入 M2" : "不满足采集前提")")
        lines.append("---")
        for item in items {
            let mark: String
            switch item.status {
            case .ok: mark = "OK  "
            case .fail: mark = "FAIL"
            case .warning: mark = "WARN"
            case .info: mark = "INFO"
            }
            lines.append("\(mark) \(item.title): \(item.detail)")
        }
        return lines.joined(separator: "\n")
    }

    /// 请求相机权限。返回后自动刷新一次。
    func requestCameraAccess() async {
        guard AVCaptureDevice.authorizationStatus(for: .video) == .notDetermined else {
            refresh()
            return
        }
        isRequestingPermission = true
        defer { isRequestingPermission = false }

        _ = await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
            AVCaptureDevice.requestAccess(for: .video) { granted in
                continuation.resume(returning: granted)
            }
        }
        refresh()
    }
}
