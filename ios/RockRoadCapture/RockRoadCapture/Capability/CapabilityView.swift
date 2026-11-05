import AVFoundation
import SwiftUI

/// M1 界面：设备能力检查。
struct CapabilityView: View {

    @StateObject private var checker = CapabilityChecker()

    var body: some View {
        NavigationStack {
            List {
                Section {
                    verdictBanner
                }

                Section("检查项") {
                    ForEach(checker.items) { item in
                        CapabilityRow(item: item)
                    }
                }

                if !checker.blockers.isEmpty {
                    Section("下一步") {
                        ForEach(checker.blockers) { item in
                            Text(item.hint ?? "请处理：\(item.title)")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                Section {
                    if AVCaptureDevice.authorizationStatus(for: .video) == .notDetermined {
                        Button {
                            Task { await checker.requestCameraAccess() }
                        } label: {
                            Label("申请相机权限", systemImage: "camera.badge.ellipsis")
                        }
                        .disabled(checker.isRequestingPermission)
                    }

                    Button {
                        checker.refresh()
                    } label: {
                        Label("重新检测", systemImage: "arrow.clockwise")
                    }
                } footer: {
                    Text("M1 仅做能力检查，不启动相机、不采集数据。")
                        .font(.caption2)
                }
            }
            .navigationTitle("设备能力检查")
            .onAppear { checker.refresh() }
            .refreshable { checker.refresh() }
        }
    }

    @ViewBuilder
    private var verdictBanner: some View {
        let ok = checker.canProceed
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: ok ? "checkmark.seal.fill" : "exclamationmark.octagon.fill")
                .font(.title2)
                .foregroundStyle(ok ? .green : .red)
            VStack(alignment: .leading, spacing: 4) {
                Text(ok ? "可以进入 M2（RGB + 深度采集）" : "暂不满足采集前提")
                    .font(.headline)
                Text(ok
                     ? "设备已具备 ARKit 世界追踪、LiDAR 深度与相机权限。"
                     : "请先解决下方「下一步」中的问题。")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
    }
}

private struct CapabilityRow: View {
    let item: CapabilityItem

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: item.status.symbolName)
                .foregroundStyle(item.status.tint)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title).font(.subheadline)
                Text(item.detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let hint = item.hint, item.status != .ok, item.status != .info {
                    Text(hint)
                        .font(.caption2)
                        .foregroundStyle(.orange)
                }
            }
        }
        .padding(.vertical, 2)
    }
}

#Preview {
    CapabilityView()
}
