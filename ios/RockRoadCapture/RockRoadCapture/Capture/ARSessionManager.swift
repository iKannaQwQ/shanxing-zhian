import ARKit
import Combine
import CoreVideo
import Foundation
import UIKit

/// M2-a：建立 ARSession 并**测量**真实的图像/深度格式。
///
/// 本阶段刻意不做：YUV 转 RGB、JPEG 编码、深度拷贝、网络上传。
/// 先把"到底是什么格式、多大分辨率"测量出来，再决定后续实现。
final class ARSessionManager: NSObject, ObservableObject {

    @Published private(set) var facts = FrameFacts()
    @Published private(set) var isRunning = false
    @Published private(set) var statusText = "未开始"
    @Published private(set) var lastError: String?

    /// M2-b：自转 RGB 预览与编码统计。编码在独立队列上进行，不阻塞 ARSession。
    @Published private(set) var rgbPreview: UIImage?
    @Published private(set) var encoderStats = EncoderStats()

    let session = ARSession()

    /// 只有支持 sceneDepth 的设备才允许开始采集（M1 已确认本项目设备支持）。
    let isDepthSupported = ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)

    private let sessionQueue = DispatchQueue(label: "com.mountain.roadcapture.session")
    private var frameIndex = 0
    private var fpsWindowStart: TimeInterval = 0
    private var fpsWindowFrames = 0
    private var currentFPS: Double = 0
    private var lastPublish: TimeInterval = 0
    private var lastDepthSample: TimeInterval = 0
    private var depthStats: (ratio: Double, min: Float, max: Float)?

    private let encoderQueue = DispatchQueue(label: "com.mountain.roadcapture.encoder",
                                             qos: .userInitiated)
    private let encodeLock = NSLock()
    private var encodeInFlight = false
    private var lastEncodeStart: TimeInterval = 0

    override init() {
        super.init()
        session.delegateQueue = sessionQueue
        session.delegate = self
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
        fpsWindowStart = 0
        fpsWindowFrames = 0
        currentFPS = 0
        lastPublish = 0
        lastDepthSample = 0
        lastEncodeStart = 0
        depthStats = nil

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
        }
        setStatus("已停止")
    }

    // MARK: - 诊断文本

    func diagnosticText() -> String {
        let f = facts
        var lines: [String] = []
        lines.append("MountainRoadCapture / M2-a 采集格式实测")
        lines.append("时间: \(ISO8601DateFormatter().string(from: Date()))")
        lines.append("状态: \(statusText)")
        lines.append("---")
        lines.append("帧序号: \(f.frameIndex)   跟踪: \(f.tracking)   FPS: \(String(format: "%.1f", f.fps))")
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
            lines.append("Depth 采样: 有效=\(String(format: "%.1f%%", ratio * 100)) "
                         + "范围=\(String(format: "%.3f", mn))~\(String(format: "%.3f", mx)) m")
        } else {
            lines.append("Depth 采样: 未采样（格式非 float32）")
        }
        lines.append("RGB 转码: \(String(format: "%.2f", encoderStats.convertMs)) ms  "
                     + "JPEG编码: \(String(format: "%.2f", encoderStats.encodeMs)) ms  "
                     + "体积: \(encoderStats.jpegBytes / 1024) KB")
        if let err = encoderStats.error { lines.append("编码错误: \(err)") }
        if let err = lastError { lines.append("错误: \(err)") }
        return lines.joined(separator: "\n")
    }

    // MARK: - 内部

    private func setStatus(_ text: String) {
        DispatchQueue.main.async { self.statusText = text }
    }

    /// 尝试占用编码槽位；已有编码在进行时直接跳过，宁可少一帧也不排长队。
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

    private func updateFPS(now: TimeInterval) {
        if fpsWindowStart == 0 {
            fpsWindowStart = now
            fpsWindowFrames = 0
        }
        fpsWindowFrames += 1
        let elapsed = now - fpsWindowStart
        if elapsed >= 1.0 {
            currentFPS = Double(fpsWindowFrames) / elapsed
            fpsWindowStart = now
            fpsWindowFrames = 0
        }
    }

    /// 只在 float32 格式下采样；其他格式直接返回 nil，绝不假设内存布局。
    private static func sampleDepth(_ buffer: CVPixelBuffer) -> (Double, Float, Float)? {
        let format = CVPixelBufferGetPixelFormatType(buffer)
        guard format == kCVPixelFormatType_DepthFloat32
                || format == kCVPixelFormatType_DisparityFloat32 else {
            return nil
        }
        guard CVPixelBufferLockBaseAddress(buffer, .readOnly) == kCVReturnSuccess else {
            return nil
        }
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { return nil }

        let width = CVPixelBufferGetWidth(buffer)
        let height = CVPixelBufferGetHeight(buffer)
        let bytesPerRow = CVPixelBufferGetBytesPerRow(buffer)
        let floatsPerRow = bytesPerRow / MemoryLayout<Float32>.size
        let pointer = base.assumingMemoryBound(to: Float32.self)

        var valid = 0
        var total = 0
        var minValue = Float.greatestFiniteMagnitude
        var maxValue = -Float.greatestFiniteMagnitude

        var y = 0
        while y < height {
            var x = 0
            while x < width {
                let value = pointer[y * floatsPerRow + x]
                total += 1
                if value.isFinite && value > 0 {
                    valid += 1
                    minValue = min(minValue, value)
                    maxValue = max(maxValue, value)
                }
                x += 4
            }
            y += 4
        }

        guard total > 0, valid > 0 else { return nil }
        return (Double(valid) / Double(total), minValue, maxValue)
    }
}

// MARK: - ARSessionDelegate

extension ARSessionManager: ARSessionDelegate {

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        frameIndex += 1
        updateFPS(now: frame.timestamp)

        guard let sceneDepth = frame.sceneDepth else {
            if frame.timestamp - lastPublish >= 1.0 {
                lastPublish = frame.timestamp
                setStatus("未收到 sceneDepth（可能被系统临时降级）")
            }
            return
        }

        let rgb = frame.capturedImage
        let depth = sceneDepth.depthMap
        let confidence = sceneDepth.confidenceMap

        var snapshot = FrameFacts()
        snapshot.frameIndex = frameIndex
        snapshot.timestamp = frame.timestamp
        snapshot.fps = currentFPS
        snapshot.tracking = Self.describe(frame.camera.trackingState)

        snapshot.rgbWidth = CVPixelBufferGetWidth(rgb)
        snapshot.rgbHeight = CVPixelBufferGetHeight(rgb)
        snapshot.rgbFormat = PixelFormatName.describe(CVPixelBufferGetPixelFormatType(rgb))
        snapshot.rgbPlaneCount = CVPixelBufferGetPlaneCount(rgb)
        snapshot.rgbBytesPerRow = (0..<snapshot.rgbPlaneCount).map {
            CVPixelBufferGetBytesPerRowOfPlane(rgb, $0)
        }

        snapshot.depthWidth = CVPixelBufferGetWidth(depth)
        snapshot.depthHeight = CVPixelBufferGetHeight(depth)
        snapshot.depthFormat = PixelFormatName.describe(CVPixelBufferGetPixelFormatType(depth))
        snapshot.depthBytesPerRow = CVPixelBufferGetBytesPerRow(depth)

        if let confidence {
            snapshot.confidenceWidth = CVPixelBufferGetWidth(confidence)
            snapshot.confidenceHeight = CVPixelBufferGetHeight(confidence)
            snapshot.confidenceFormat = PixelFormatName.describe(
                CVPixelBufferGetPixelFormatType(confidence))
            snapshot.confidenceBytesPerRow = CVPixelBufferGetBytesPerRow(confidence)
        }

        let intrinsics = frame.camera.intrinsics
        snapshot.fx = Double(intrinsics[0][0])
        snapshot.fy = Double(intrinsics[1][1])
        snapshot.cx = Double(intrinsics[2][0])
        snapshot.cy = Double(intrinsics[2][1])
        snapshot.refWidth = Int(frame.camera.imageResolution.width.rounded())
        snapshot.refHeight = Int(frame.camera.imageResolution.height.rounded())

        if frame.timestamp - lastDepthSample >= 0.5 {
            lastDepthSample = frame.timestamp
            depthStats = Self.sampleDepth(depth)
        }
        if let stats = depthStats {
            snapshot.depthValidRatio = stats.ratio
            snapshot.depthMin = stats.min
            snapshot.depthMax = stats.max
        }

        if frame.timestamp - lastPublish >= 0.2 {
            lastPublish = frame.timestamp
            DispatchQueue.main.async {
                self.facts = snapshot
                self.statusText = "采集中"
            }
        }

        // M2-b：每 0.5 s 做一次真实的转换 + 编码，用于校验颜色与测量成本。
        // 放在独立队列，不占用 ARSession 的 delegate 队列。
        if frame.timestamp - lastEncodeStart >= 0.5, beginEncodeIfIdle() {
            lastEncodeStart = frame.timestamp
            // 连同 ARFrame 一起持有：ARKit 的像素缓冲生命周期绑定在 ARFrame 上，
            // 只保留 CVPixelBuffer 在极端情况下可能遇到缓冲被回收。
            encoderQueue.async { [weak self] in
                let output = FrameEncoder.encode(frame.capturedImage)
                DispatchQueue.main.async {
                    self?.rgbPreview = output.image
                    self?.encoderStats = output.stats
                }
                self?.endEncode()
            }
        }
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
