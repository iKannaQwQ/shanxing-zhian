import SwiftUI
import UIKit

/// M2-a + M2-b：采集格式实测 与 RGB 转码校验。
///
/// 页面上每一个数字都是运行时从同一个 ARFrame 读出来的，
/// 没有任何一个来自文档或猜测。
struct SessionView: View {

    @StateObject private var manager = ARSessionManager()
    @ObservedObject private var settings = UploadSettings.shared
    @State private var copied = false

    private var facts: FrameFacts { manager.facts }

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                comparisonStrip
                factsList
            }
            .navigationTitle("采集实测")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        UIPasteboard.general.string = manager.diagnosticText()
                        copied = true
                        Task {
                            try? await Task.sleep(nanoseconds: 1_500_000_000)
                            copied = false
                        }
                    } label: {
                        Label(copied ? "已复制" : "复制实测数据",
                              systemImage: copied ? "checkmark" : "doc.on.doc")
                    }
                }
            }
            .safeAreaInset(edge: .bottom) {
                controls
            }
            .onDisappear {
                if manager.isRunning { manager.pause() }
            }
        }
    }

    // MARK: - 并排对比

    private var comparisonStrip: some View {
        VStack(spacing: 0) {
            HStack(spacing: 6) {
                tile(title: "ARKit 预览") {
                    if manager.isRunning {
                        ARPreviewView(session: manager.session)
                    } else {
                        placeholder(manager.statusText)
                    }
                }
                tile(title: "自转 RGB") {
                    if let image = manager.rgbPreview {
                        Image(uiImage: image).resizable().scaledToFit()
                    } else {
                        placeholder(manager.isRunning ? "等待首帧…" : "未采集")
                    }
                }
            }
            .frame(height: 158)

            HStack(spacing: 6) {
                tile(title: "深度伪彩 0.2–3.0 m") {
                    if let image = manager.depthPreview {
                        Image(uiImage: image)
                            .resizable()
                            .interpolation(.none)
                            .scaledToFit()
                    } else {
                        placeholder(manager.isRunning ? "等待首帧…" : "未采集")
                    }
                }
                tile(title: "置信度 低/中/高") {
                    if let image = manager.confidencePreview {
                        Image(uiImage: image)
                            .resizable()
                            .interpolation(.none)
                            .scaledToFit()
                    } else {
                        placeholder(manager.isRunning ? "等待首帧…" : "未采集")
                    }
                }
            }
            .frame(height: 122)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(Color.black)
        .overlay(alignment: .topLeading) { statusBadge }
    }

    private func tile<Content: View>(title: String,
                                     @ViewBuilder content: () -> Content) -> some View {
        ZStack {
            Rectangle().fill(Color(white: 0.12))
            content()
        }
        .clipped()
        .overlay(alignment: .bottomLeading) {
            Text(title)
                .font(.caption2)
                .padding(.horizontal, 5)
                .padding(.vertical, 2)
                .background(.black.opacity(0.6), in: Capsule())
                .foregroundStyle(.white)
                .padding(4)
        }
    }

    private func placeholder(_ text: String) -> some View {
        VStack(spacing: 6) {
            Image(systemName: "camera.metering.unknown").font(.title3)
            Text(text).font(.caption2)
        }
        .foregroundStyle(.white.opacity(0.6))
    }

    private var statusBadge: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(manager.isRunning ? Color.green : Color.orange)
                .frame(width: 7, height: 7)
            Text("\(manager.statusText) · \(String(format: "%.1f", facts.fps)) fps")
                .font(.caption2.monospaced())
                .foregroundStyle(.white)
        }
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .background(.black.opacity(0.55), in: Capsule())
        .padding(10)
        .opacity(0.9)
    }

    // MARK: - 数据

    private var factsList: some View {
        List {
            Section("运行") {
                FactRow(label: "帧序号", value: "\(facts.frameIndex)")
                FactRow(label: "跟踪状态", value: facts.tracking)
                FactRow(label: "FPS", value: String(format: "%.1f", facts.fps))
            }

            Section {
                FactRow(label: "YUV→BGRA", value: String(format: "%.2f ms", manager.encoderStats.convertMs))
                FactRow(label: "JPEG 编码", value: String(format: "%.2f ms", manager.encoderStats.encodeMs))
                FactRow(label: "JPEG 体积", value: manager.encoderStats.jpegBytes > 0
                        ? "\(manager.encoderStats.jpegBytes / 1024) KB" : "-")
                if let error = manager.encoderStats.error {
                    FactRow(label: "错误", value: error)
                }
            } header: {
                Text("RGB 转码（M2-b）")
            } footer: {
                Text("每 0.5 s 真实编码一次。左边是 ARKit 自己渲染的画面，右边是 vImage 转出来的结果——两者颜色应一致。")
                    .font(.caption2)
            }

            Section {
                FactRow(label: "目标 / 原生 FPS",
                        value: "\(manager.captureStats.targetFPS) / \(String(format: "%.1f", manager.captureStats.nativeFPS))")
                FactRow(label: "实测采集 FPS",
                        value: String(format: "%.1f", manager.captureStats.captureFPS))
                FactRow(label: "已产出帧", value: "\(manager.captureStats.produced)")
                FactRow(label: "节流跳过", value: "\(manager.captureStats.skippedByThrottle)")
                FactRow(label: "编码繁忙丢帧", value: "\(manager.captureStats.droppedEncoderBusy)")
                FactRow(label: "深度拷贝",
                        value: String(format: "%.3f ms · %d KB",
                                      manager.captureStats.depthCopyMs,
                                      manager.captureStats.depthBytes / 1024))
                FactRow(label: "置信度拷贝",
                        value: String(format: "%.3f ms · %d KB",
                                      manager.captureStats.confidenceCopyMs,
                                      manager.captureStats.confidenceBytes / 1024))
                FactRow(label: "内存 起始/当前/峰值",
                        value: String(format: "%.0f / %.0f / %.0f MB",
                                      manager.captureStats.startResidentMB,
                                      manager.captureStats.residentMB,
                                      manager.captureStats.peakResidentMB))
                FactRow(label: "内存变化",
                        value: String(format: "%+.1f MB（%d 个采样点）",
                                      manager.captureStats.trendMB,
                                      manager.captureStats.memorySampleCount))
                FactRow(label: "运行时长",
                        value: String(format: "%.0f s", manager.captureStats.elapsedSeconds))
                FactRow(label: "热状态",
                        value: manager.captureStats.thermalState
                            + (manager.captureStats.lowPowerMode ? " · 低电量模式" : ""))
            } header: {
                Text("采集节流（M2-c）")
            } footer: {
                Text("原生 30 fps 按时间间隔抽帧到目标帧率；编码队列忙时直接丢帧，不在队列里堆积。")
                    .font(.caption2)
            }

            Section {
                FactRow(label: "已打包", value: "\(manager.bundleStats.produced)")
                FactRow(label: "已保存",
                        value: "\(manager.bundleStats.saved) / \(manager.bundleStats.saveLimit)")
                FactRow(label: "metadata.json", value: "\(manager.bundleStats.metadataBytes) B")
                FactRow(label: "rgb.jpg", value: "\(manager.bundleStats.jpegBytes) B")
                FactRow(label: "depth.bin", value: "\(manager.bundleStats.depthBytes) B")
                FactRow(label: "confidence.bin", value: "\(manager.bundleStats.confidenceBytes) B")
                FactRow(label: "单帧合计", value: "\(manager.bundleStats.totalBytes) B")
                FactRow(label: "最近目录", value: manager.bundleStats.directory)
                if let error = manager.bundleStats.error {
                    FactRow(label: "错误", value: error)
                }
                ShareLink(item: manager.bundleRootURL) {
                    Label("导出 FrameBundles 目录", systemImage: "square.and.arrow.up")
                }
            } header: {
                Text("M4 打包（1280×960）")
            } footer: {
                Text("每帧四个文件：metadata.json / rgb.jpg / depth.bin / confidence.bin。只落盘前 20 帧用于校验；也可在「文件」App → 我的 iPhone → RockRoadCapture 中找到。")
                    .font(.caption2)
            }

            Section {
                HStack {
                    Text("IP").font(.subheadline).frame(width: 26, alignment: .leading)
                    TextField("192.168.1.10", text: $settings.host)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                        .font(.subheadline.monospaced())
                }
                HStack {
                    Text("端口").font(.subheadline).frame(width: 26, alignment: .leading)
                    TextField("8000", text: $settings.port)
                        .keyboardType(.numberPad)
                        .font(.subheadline.monospaced())
                }

                Button {
                    Task { await manager.uploader.testConnection(settings: settings) }
                } label: {
                    Label("测试连接", systemImage: "antenna.radiowaves.left.and.right")
                }
                .disabled(!settings.isValid)

                HStack {
                    Text("连接").font(.subheadline)
                    Spacer()
                    Text(connectionText)
                        .font(.caption.monospaced())
                        .foregroundStyle(connectionColor)
                        .multilineTextAlignment(.trailing)
                }

                HStack {
                    Text("端到端风险").font(.subheadline)
                    Spacer()
                    Text(manager.lastRiskResult.overallRisk)
                        .font(.headline.monospaced())
                        .foregroundStyle(riskColor(manager.lastRiskResult.overallRisk))
                }
                FactRow(label: "系统状态", value: manager.lastRiskResult.systemStatus)
                FactRow(label: "A→B / B→A",
                        value: "\(manager.lastRiskResult.passAToB) / \(manager.lastRiskResult.passBToA)")
                FactRow(label: "服务端耗时",
                        value: String(format: "%.0f ms", manager.lastRiskResult.processingMs))
                if !manager.lastRiskResult.reasons.isEmpty {
                    FactRow(label: "理由", value: manager.lastRiskResult.reasons.joined(separator: ", "))
                }
                if let error = manager.lastRiskResult.error {
                    FactRow(label: "回显错误", value: error)
                }

                FactRow(label: "已发送", value: "\(manager.uploadStats.sent)")
                FactRow(label: "失败", value: "\(manager.uploadStats.failed)")
                FactRow(label: "队列",
                        value: "\(manager.uploadStats.queued) / \(manager.uploadStats.capacity)")
                FactRow(label: "溢出丢弃", value: "\(manager.uploadStats.droppedOverflow)")
                FactRow(label: "质量门控丢弃", value: "\(manager.uploadStats.droppedQuality)")
                FactRow(label: "延迟 最近/平均",
                        value: String(format: "%.0f / %.0f ms",
                                      manager.uploadStats.lastLatencyMs,
                                      manager.uploadStats.avgLatencyMs))
                if let error = manager.uploadStats.lastError {
                    FactRow(label: "错误", value: error)
                }
            } header: {
                Text("M5 上传")
            } footer: {
                Text("先在电脑上运行 scripts/dev_receiver.py，填入电脑的局域网 IP 与端口。质量门控会跳过跟踪未就绪与会话开始 1 秒内的帧。")
                    .font(.caption2)
            }

            Section("RGB · capturedImage") {
                FactRow(label: "分辨率", value: "\(facts.rgbWidth) × \(facts.rgbHeight)")
                FactRow(label: "像素格式", value: facts.rgbFormat)
                FactRow(label: "plane 数", value: "\(facts.rgbPlaneCount)")
                ForEach(Array(facts.rgbBytesPerRow.enumerated()), id: \.offset) { index, bpr in
                    FactRow(label: "bytesPerRow[\(index)]", value: "\(bpr)")
                }
            }

            Section("深度 · sceneDepth.depthMap") {
                FactRow(label: "分辨率", value: "\(facts.depthWidth) × \(facts.depthHeight)")
                FactRow(label: "像素格式", value: facts.depthFormat)
                FactRow(label: "bytesPerRow", value: "\(facts.depthBytesPerRow)")
                if let ratio = facts.depthValidRatio,
                   let minValue = facts.depthMin,
                   let maxValue = facts.depthMax {
                    FactRow(label: "有效像素", value: String(format: "%.1f%%", ratio * 100))
                    FactRow(label: "深度范围",
                            value: String(format: "%.3f ~ %.3f m", minValue, maxValue))
                } else {
                    FactRow(label: "深度采样", value: "未采样")
                }
            }

            Section("置信度 · confidenceMap") {
                FactRow(label: "分辨率",
                        value: "\(facts.confidenceWidth) × \(facts.confidenceHeight)")
                FactRow(label: "像素格式", value: facts.confidenceFormat)
                FactRow(label: "bytesPerRow", value: "\(facts.confidenceBytesPerRow)")
            }

            Section("内参 · camera.intrinsics") {
                FactRow(label: "fx / fy",
                        value: String(format: "%.2f / %.2f", facts.fx, facts.fy))
                FactRow(label: "cx / cy",
                        value: String(format: "%.2f / %.2f", facts.cx, facts.cy))
                FactRow(label: "参考分辨率",
                        value: "\(facts.refWidth) × \(facts.refHeight)")
            }

            Section {
                FactRow(label: "深度/RGB 缩放",
                        value: String(format: "x %.4f · y %.4f",
                                      facts.depthScaleX, facts.depthScaleY))
                FactRow(label: "宽高比一致",
                        value: facts.aspectMatches ? "是" : "否")
            } header: {
                Text("对齐关系")
            } footer: {
                Text("宽高比不一致时，仅按比例缩放像素坐标不成立，M5 必须做显式配准。")
                    .font(.caption2)
            }
        }
        .listStyle(.insetGrouped)
    }

    private var connectionText: String {
        switch manager.uploadConnection {
        case .unknown: return "未测试"
        case .checking: return "测试中…"
        case .ok(let detail): return "正常 · \(detail)"
        case .failed(let detail): return "失败 · \(detail)"
        }
    }

    private func riskColor(_ risk: String) -> Color {
        switch risk {
        case "SAFE": return .green
        case "LOW": return .yellow
        case "MEDIUM": return .orange
        case "HIGH": return .red
        default: return .secondary
        }
    }

    private var connectionColor: Color {
        switch manager.uploadConnection {
        case .ok: return .green
        case .failed: return .red
        case .checking: return .orange
        case .unknown: return .secondary
        }
    }

    // MARK: - 控制

    private var controls: some View {
        HStack(spacing: 12) {
            Button {
                manager.start()
            } label: {
                Label("开始", systemImage: "play.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(manager.isRunning || !manager.isDepthSupported)

            Button {
                manager.pause()
            } label: {
                Label("暂停", systemImage: "pause.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .disabled(!manager.isRunning)

            Button {
                manager.stop()
            } label: {
                Label("停止", systemImage: "stop.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .tint(.red)
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(.bar)
    }
}

private struct FactRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack {
            Text(label)
                .font(.subheadline)
            Spacer()
            Text(value)
                .font(.subheadline.monospaced())
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.trailing)
        }
    }
}

#Preview {
    SessionView()
}
