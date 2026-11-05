import SwiftUI
import UIKit

/// M2-a：采集格式实测界面。
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
                preview
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

    // MARK: - 预览

    private var preview: some View {
        ZStack(alignment: .topLeading) {
            if manager.isRunning {
                ARPreviewView(session: manager.session)
            } else {
                Rectangle()
                    .fill(Color.black)
                    .overlay {
                        VStack(spacing: 8) {
                            Image(systemName: "camera.metering.unknown")
                                .font(.largeTitle)
                            Text(manager.statusText)
                                .font(.footnote)
                        }
                        .foregroundStyle(.white.opacity(0.7))
                    }
            }
            statusBadge
        }
        .frame(height: 260)
        .clipped()
    }

    private var statusBadge: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(manager.isRunning ? Color.green : Color.orange)
                .frame(width: 8, height: 8)
            Text("\(manager.statusText) · \(String(format: "%.1f", facts.fps)) fps")
                .font(.caption2.monospaced())
                .foregroundStyle(.white)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(.black.opacity(0.55), in: Capsule())
        .padding(8)
    }

    // MARK: - 数据

    private var factsList: some View {
        List {
            Section("运行") {
                FactRow(label: "帧序号", value: "\(facts.frameIndex)")
                FactRow(label: "跟踪状态", value: facts.tracking)
                FactRow(label: "FPS", value: String(format: "%.1f", facts.fps))
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
