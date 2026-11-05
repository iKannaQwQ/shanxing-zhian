import SwiftUI
import UIKit

/// M2-a + M2-b：采集格式实测 与 RGB 转码校验。
///
/// 页面上每一个数字都是运行时从同一个 ARFrame 读出来的，
/// 没有任何一个来自文档或猜测。
struct SessionView: View {

    @StateObject private var manager = ARSessionManager()
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
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                } else {
                    placeholder(manager.isRunning ? "等待首帧…" : "未采集")
                }
            }
        }
        .frame(height: 180)
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
