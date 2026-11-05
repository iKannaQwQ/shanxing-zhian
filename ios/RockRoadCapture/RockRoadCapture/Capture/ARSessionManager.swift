import ARKit
import Combine
import CoreVideo
import Foundation
import UIKit

/// M2：ARKit 采集。
///
/// 阶段划分：
///   * M2-a 把真实格式量出来（分辨率/像素格式/bytesPerRow/内参）
///   * M2-b 打通 RGB 转码与 JPEG 编码
///   * M2-c 节流到目标帧率、原始拷贝深度与置信度、观测资源占用
final class ARSessionManager: NSObject, ObservableObject {

    @Published private(set) var facts = FrameFacts()
    @Published private(set) var isRunning = false
    @Published private(set) var statusText = "未开始"
    @Published private(set) var lastError: String?

    /// M2-b：自转 RGB 预览与编码统计。
    @Published private(set) var rgbPreview: UIImage?
    @Published private(set) var encoderStats = EncoderStats()

    /// M2-c：节流与资源占用。
    @Published private(set) var captureStats = CaptureStats()

    /// M3：从拷贝后的 RawPlane 渲染的伪彩色预览。
    @Published private(set) var depthPreview: UIImage?
    @Published private(set) var confidencePreview: UIImage?

    let session = ARSession()

    /// 只有支持 sceneDepth 的设备才允许开始采集（M1 已确认本项目设备支持）。
    let isDepthSupported = ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)

    /// 目标采集帧率。ARKit 原生 30 fps，这里按时间间隔抽帧。
    var targetFPS: Int = 10 {
        didSet {
            if targetFPS <= 0 { targetFPS = 1 }
            captureInterval = 1.0 / Double(targetFPS)
        }
    }

    private let sessionQueue = DispatchQueue(label: "com.mountain.roadcapture.session")
    private let encoderQueue = DispatchQueue(label: "com.mountain.roadcapture.encoder",
                                             qos: .userInitiated)
    private let encodeLock = NSLock()
    private var encodeInFlight = false

    private var captureInterval: TimeInterval = 0.1

    // 原生帧率统计
    private var frameIndex = 0
    private var nativeWindowStart: TimeInterval = 0
    private var nativeWindowFrames = 0
    private var nativeFPS: Double = 0

    // 采集帧率统计
    private var captureWindowStart: TimeInterval = 0
    private var captureWindowCount = 0
    private var captureFPS: Double = 0

    // 节流
    private var lastCaptureTimestamp: TimeInterval = 0
    private var produced = 0
    private var skippedByThrottle = 0
    private var droppedEncoderBusy = 0

    // 拷贝
    private var depthCopyMs: Double = 0
    private var confidenceCopyMs: Double = 0
    private var depthBytes = 0
    private var confidenceBytes = 0

    /// RGB 转码/编码只服务于**预览与成本测量**，不需要跟采集同频。
    /// 与采集率解耦：10 fps 采集 + 2 fps 预览。
    private let previewInterval: TimeInterval = 0.5
    private var lastEncodeStart: TimeInterval = 0

    private var startedAt: TimeInterval = 0
    private var lastPublish: TimeInterval = 0
    private var lastStatusPublish: TimeInterval = 0

    override init() {
        super.init()
        session.delegateQueue = sessionQueue
        session.delegate = self
        captureInterval = 1.0 / Double(targetFPS)
    }

    // MARK: - 控制

    func start() {
        guard ARWorldTrackingConfiguration.isSupported else {
            setStatus("设备不支持 ARKit 世界追踪")
            return
        }
        guard isDepthSupported else {
            setStatus("设备不支持 sceneDepth，无法采集")
            return
        }

        let configuration = ARWorldTrackingConfiguration()
        configuration.planeDetection = []
        configuration.environmentTexturing = .none
        configuration.frameSemantics.insert(.sceneDepth)

        frameIndex = 0
        nativeWindowStart = 0
        nativeWindowFrames = 0
        nativeFPS = 0
        captureWindowStart = 0
        captureWindowCount = 0
        captureFPS = 0
        lastCaptureTimestamp = 0
        produced = 0
        skippedByThrottle = 0
        droppedEncoderBusy = 0
        depthCopyMs = 0
        confidenceCopyMs = 0
        depthBytes = 0
        confidenceBytes = 0
        lastPublish = 0
        lastStatusPublish = 0
        lastEncodeStart = 0
        startedAt = CACurrentMediaTime()
        MemoryProbe.resetPeak()

        session.run(configuration, options: [.resetTracking, .removeExistingAnchors])
        DispatchQueue.main.async {
            self.isRunning = true
            self.lastError = nil
        }
        setStatus("采集中")
    }

    func pause() {
        session.pause()
        DispatchQueue.main.async { self.isRunning = false }
        setStatus("已暂停")
    }

    func stop() {
        session.pause()
        DispatchQueue.main.async {
            self.isRunning = false
            self.facts = FrameFacts()
            self.rgbPreview = nil
            self.depthPreview = nil
            self.confidencePreview = nil
        }
        setStatus("已停止")
    }

    // MARK: - 诊断文本

    func diagnosticText() -> String {
        let f = facts
        let c = captureStats
        var lines: [String] = []
        lines.append("MountainRoadCapture / M2 采集实测")
        lines.append("时间: \(ISO8601DateFormatter().string(from: Date()))")
        lines.append("状态: \(statusText)")
        lines.append("---")
        lines.append("帧序号: \(f.frameIndex)   跟踪: \(f.tracking)   原生FPS: \(String(format: "%.1f", f.fps))")
        lines.append("RGB: \(f.rgbWidth)x\(f.rgbHeight)  \(f.rgbFormat)  planes=\(f.rgbPlaneCount)  bpr=\(f.rgbBytesPerRow)")
        lines.append("Depth: \(f.depthWidth)x\(f.depthHeight)  \(f.depthFormat)  bpr=\(f.depthBytesPerRow)")
        lines.append("Confidence: \(f.confidenceWidth)x\(f.confidenceHeight)  \(f.confidenceFormat)  bpr=\(f.confidenceBytesPerRow)")
        lines.append("Intrinsics: fx=\(String(format: "%.2f", f.fx)) fy=\(String(format: "%.2f", f.fy)) "
                     + "cx=\(String(format: "%.2f", f.cx)) cy=\(String(format: "%.2f", f.cy)) "
                     + "ref=\(f.refWidth)x\(f.refHeight)")
        lines.append("Depth/RGB 缩放: x=\(String(format: "%.4f", f.depthScaleX)) "
                     + "y=\(String(format: "%.4f", f.depthScaleY)) "
                     + "宽高比一致=\(f.aspectMatches ? "是" : "否")")
        if let ratio = f.depthValidRatio, let mn = f.depthMin, let mx = f.depthMax {
            lines.append("Depth 采样(来自拷贝): 有效=\(String(format: "%.1f%%", ratio * 100)) "
                         + "范围=\(String(format: "%.3f", mn))~\(String(format: "%.3f", mx)) m")
        } else {
            lines.append("Depth 采样: 未采样")
        }
        lines.append("RGB 转码: \(String(format: "%.2f", encoderStats.convertMs)) ms  "
                     + "JPEG编码: \(String(format: "%.2f", encoderStats.encodeMs)) ms  "
                     + "体积: \(encoderStats.jpegBytes / 1024) KB")
        lines.append("--- 采集节流 ---")
        lines.append("目标FPS: \(c.targetFPS)   实测采集FPS: \(String(format: "%.1f", c.captureFPS))   "
                     + "已产出: \(c.produced)   节流跳过: \(c.skippedByThrottle)   "
                     + "编码繁忙丢帧: \(c.droppedEncoderBusy)")
        lines.append("深度拷贝: \(String(format: "%.3f", c.depthCopyMs)) ms  \(c.depthBytes / 1024) KB   "
                     + "置信度拷贝: \(String(format: "%.3f", c.confidenceCopyMs)) ms  \(c.confidenceBytes / 1024) KB")
        lines.append("内存: 起始 \(String(format: "%.1f", c.startResidentMB)) MB   "
                     + "当前 \(String(format: "%.1f", c.residentMB)) MB   "
                     + "峰值 \(String(format: "%.1f", c.peakResidentMB)) MB   "
                     + "变化 \(String(format: "%+.1f", c.trendMB)) MB")
        lines.append("内存采样点: \(c.memorySampleCount)（每 5 s 一个）   运行 \(String(format: "%.0f", c.elapsedSeconds)) s")
        lines.append("热状态: \(c.thermalState)   低电量模式: \(c.lowPowerMode ? "开" : "关")")
        if let err = encoderStats.error { lines.append("编码错误: \(err)") }
        if let err = lastError { lines.append("错误: \(err)") }
        return lines.joined(separator: "\n")
    }

    // MARK: - 内部

    private func setStatus(_ text: String) {
        DispatchQueue.main.async { self.statusText = text }
    }

    private func updateNativeFPS(now: TimeInterval) {
        if nativeWindowStart == 0 { nativeWindowStart = now; nativeWindowFrames = 0 }
        nativeWindowFrames += 1
        let elapsed = now - nativeWindowStart
        if elapsed >= 1.0 {
            nativeFPS = Double(nativeWindowFrames) / elapsed
            nativeWindowStart = now
            nativeWindowFrames = 0
        }
    }

    private func updateCaptureFPS(now: TimeInterval) {
        if captureWindowStart == 0 { captureWindowStart = now; captureWindowCount = 0 }
        captureWindowCount += 1
        let elapsed = now - captureWindowStart
        if elapsed >= 1.0 {
            captureFPS = Double(captureWindowCount) / elapsed
            captureWindowStart = now
            captureWindowCount = 0
        }
    }

    private func beginEncodeIfIdle() -> Bool {
        encodeLock.lock()
        defer { encodeLock.unlock() }
        if encodeInFlight { return false }
        encodeInFlight = true
        return true
    }

    private func endEncode() {
        encodeLock.lock()
        encodeInFlight = false
        encodeLock.unlock()
    }

}

// MARK: - ARSessionDelegate

extension ARSessionManager: ARSessionDelegate {

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        frameIndex += 1
        updateNativeFPS(now: frame.timestamp)

        // 节流：按时间间隔抽帧。用 0.9 的容差，避免
        // 「3 × 33.33ms = 99.99ms < 100ms」导致实际掉到 7.5 fps。
        guard frame.timestamp - lastCaptureTimestamp >= captureInterval * 0.9 else {
            skippedByThrottle += 1
            return
        }
        lastCaptureTimestamp = frame.timestamp
        produced += 1
        updateCaptureFPS(now: frame.timestamp)

        guard let sceneDepth = frame.sceneDepth else {
            if frame.timestamp - lastStatusPublish >= 1.0 {
                lastStatusPublish = frame.timestamp
                setStatus("未收到 sceneDepth（可能被系统临时降级）")
            }
            return
        }

        autoreleasepool {
            capture(frame: frame, sceneDepth: sceneDepth)
        }
    }

    private func capture(frame: ARFrame, sceneDepth: ARDepthData) {
        // --- 深度原始拷贝 ---
        let depthStart = CFAbsoluteTimeGetCurrent()
        let depthCopy = RawPlane.copy(from: sceneDepth.depthMap)
        depthCopyMs = (CFAbsoluteTimeGetCurrent() - depthStart) * 1000
        if let depthCopy { depthBytes = depthCopy.data.count }

        // --- 置信度原始拷贝 ---
        var confidenceCopy: RawPlane?
        if let confidenceMap = sceneDepth.confidenceMap {
            let start = CFAbsoluteTimeGetCurrent()
            confidenceCopy = RawPlane.copy(from: confidenceMap)
            confidenceCopyMs = (CFAbsoluteTimeGetCurrent() - start) * 1000
            if let confidenceCopy { confidenceBytes = confidenceCopy.data.count }
        }

        // --- 从拷贝出来的数据采样，验证拷贝保真 ---
        var depthStats: DepthSampler.Stats?
        if let depthCopy {
            depthStats = DepthSampler.stats(from: depthCopy.data,
                                            width: depthCopy.width,
                                            height: depthCopy.height,
                                            bytesPerRow: depthCopy.bytesPerRow,
                                            pixelFormat: depthCopy.pixelFormat)
        }

        // --- RGB 编码（独立队列；按预览频率节流，不跟采集同频）---
        let previewDue = frame.timestamp - lastEncodeStart >= previewInterval
        if previewDue, beginEncodeIfIdle() {
            lastEncodeStart = frame.timestamp
            encoderQueue.async { [weak self] in
                let output = FrameEncoder.encode(frame.capturedImage)
                // 只保留缩略图：整幅 1920x1440 BGRA 是 11 MB，
                // 长期持有会让常驻内存被预览图吃掉。
                let thumbnail = output.image?.preparingThumbnail(of: CGSize(width: 640, height: 480))
                DispatchQueue.main.async {
                    self?.rgbPreview = thumbnail ?? output.image
                    self?.encoderStats = output.stats
                }
                self?.endEncode()
            }
        } else {
            droppedEncoderBusy += 1
        }

        // --- 周期性发布快照 ---
        guard frame.timestamp - lastPublish >= 0.2 else { return }
        lastPublish = frame.timestamp

        let snapshot = makeFacts(frame: frame, depthCopy: depthCopy,
                                 confidenceCopy: confidenceCopy, depthStats: depthStats)
        let depthImage = depthCopy.flatMap { Visualizers.depthColorized($0) }
            .map { UIImage(cgImage: $0) }
        let confidenceImage = confidenceCopy.flatMap { Visualizers.confidenceColorized($0) }
            .map { UIImage(cgImage: $0) }

        let memory = MemoryProbe.current()
        let stats = CaptureStats(
            targetFPS: targetFPS,
            nativeFPS: nativeFPS,
            captureFPS: captureFPS,
            elapsedSeconds: CACurrentMediaTime() - startedAt,
            produced: produced,
            skippedByThrottle: skippedByThrottle,
            droppedEncoderBusy: droppedEncoderBusy,
            depthCopyMs: depthCopyMs,
            confidenceCopyMs: confidenceCopyMs,
            depthBytes: depthBytes,
            confidenceBytes: confidenceBytes,
            thermalState: Self.describe(ProcessInfo.processInfo.thermalState),
            lowPowerMode: ProcessInfo.processInfo.isLowPowerModeEnabled,
            residentMB: memory.residentMB,
            peakResidentMB: memory.peakResidentMB,
            startResidentMB: memory.startResidentMB,
            trendMB: memory.trendMB,
            memorySampleCount: memory.sampleCount)

        DispatchQueue.main.async {
            self.facts = snapshot
            self.captureStats = stats
            self.depthPreview = depthImage
            self.confidencePreview = confidenceImage
            self.statusText = "采集中"
        }
    }

    private func makeFacts(frame: ARFrame,
                           depthCopy: RawPlane?,
                           confidenceCopy: RawPlane?,
                           depthStats: DepthSampler.Stats?) -> FrameFacts {
        let rgb = frame.capturedImage
        var snapshot = FrameFacts()
        snapshot.frameIndex = frameIndex
        snapshot.timestamp = frame.timestamp
        snapshot.fps = nativeFPS
        snapshot.tracking = Self.describe(frame.camera.trackingState)

        snapshot.rgbWidth = CVPixelBufferGetWidth(rgb)
        snapshot.rgbHeight = CVPixelBufferGetHeight(rgb)
        snapshot.rgbFormat = PixelFormatName.describe(CVPixelBufferGetPixelFormatType(rgb))
        snapshot.rgbPlaneCount = CVPixelBufferGetPlaneCount(rgb)
        snapshot.rgbBytesPerRow = (0..<snapshot.rgbPlaneCount).map {
            CVPixelBufferGetBytesPerRowOfPlane(rgb, $0)
        }

        if let depthCopy {
            snapshot.depthWidth = depthCopy.width
            snapshot.depthHeight = depthCopy.height
            snapshot.depthFormat = PixelFormatName.describe(depthCopy.pixelFormat)
            snapshot.depthBytesPerRow = depthCopy.bytesPerRow
        }
        if let confidenceCopy {
            snapshot.confidenceWidth = confidenceCopy.width
            snapshot.confidenceHeight = confidenceCopy.height
            snapshot.confidenceFormat = PixelFormatName.describe(confidenceCopy.pixelFormat)
            snapshot.confidenceBytesPerRow = confidenceCopy.bytesPerRow
        }

        let intrinsics = frame.camera.intrinsics
        snapshot.fx = Double(intrinsics[0][0])
        snapshot.fy = Double(intrinsics[1][1])
        snapshot.cx = Double(intrinsics[2][0])
        snapshot.cy = Double(intrinsics[2][1])
        snapshot.refWidth = Int(frame.camera.imageResolution.width.rounded())
        snapshot.refHeight = Int(frame.camera.imageResolution.height.rounded())

        if let depthStats {
            snapshot.depthValidRatio = depthStats.validRatio
            snapshot.depthMin = depthStats.minValue
            snapshot.depthMax = depthStats.maxValue
        }
        return snapshot
    }

    func session(_ session: ARSession, didFailWithError error: Error) {
        let message = error.localizedDescription
        DispatchQueue.main.async {
            self.isRunning = false
            self.lastError = message
            self.statusText = "会话失败"
        }
    }

    func sessionWasInterrupted(_ session: ARSession) {
        setStatus("会话被中断")
    }

    func sessionInterruptionEnded(_ session: ARSession) {
        setStatus("会话已恢复")
    }

    private static func describe(_ state: ProcessInfo.ThermalState) -> String {
        switch state {
        case .nominal: return "正常"
        case .fair: return "偏热"
        case .serious: return "过热"
        case .critical: return "严重过热"
        @unknown default: return "未知"
        }
    }

    private static func describe(_ state: ARCamera.TrackingState) -> String {
        switch state {
        case .normal:
            return "正常"
        case .notAvailable:
            return "不可用"
        case .limited(let reason):
            switch reason {
            case .initializing: return "受限(初始化)"
            case .excessiveMotion: return "受限(移动过快)"
            case .insufficientFeatures: return "受限(特征不足)"
            case .relocalizing: return "受限(重定位)"
            @unknown default: return "受限"
            }
        }
    }
}
