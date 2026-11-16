import SwiftUI

/// 监控大屏：宽屏左画面右数据；窄屏上下堆叠。
struct DashboardView: View {
    let base: URL
    @ObservedObject var config: ServerConfig
    @StateObject private var vm: DashboardViewModel
    @State private var showSettings = false

    init(config: ServerConfig, base: URL) {
        self.config = config
        self.base = base
        _vm = StateObject(wrappedValue: DashboardViewModel(base: base))
    }

    var body: some View {
        GeometryReader { geo in
            let wide = geo.size.width >= 820
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    header
                    alerts
                    if wide {
                        HStack(alignment: .top, spacing: 14) {
                            viewer
                            panel.frame(width: min(420, geo.size.width * 0.4))
                        }
                    } else {
                        VStack(spacing: 12) {
                            viewer
                            panel
                        }
                    }
                    footer
                }
                .padding(14)
            }
        }
        .background(Color(.systemGroupedBackground))
        .task { vm.start() }
        .onDisappear { vm.stop() }
        .sheet(isPresented: $showSettings) {
            SetupView(config: config, isFirstRun: false)
        }
        .onChange(of: config.baseURL) { _ in vm.retry() }
    }

    // MARK: 顶栏

    private var header: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(vm.connected ? Color(hex: 0x16A34A) : Color(.systemGray3))
                .frame(width: 8, height: 8)
            Text("山行智安")
                .font(.headline)
            Text("实时监控")
                .font(.caption)
                .foregroundStyle(.secondary)
            Spacer()
            if vm.connected {
                Text("实时 · 已收 \(vm.received) 条")
                    .font(.caption2)
                    .foregroundStyle(Color(hex: 0x16A34A))
            }
            Button {
                showSettings = true
            } label: {
                Image(systemName: "gearshape")
            }
            .font(.footnote)
        }
    }

    // MARK: 告警

    @ViewBuilder
    private var alerts: some View {
        if let error = vm.errorText {
            banner(icon: "exclamationmark.triangle.fill", tint: .red,
                   title: "后端异常", detail: error)
        }
        if vm.stale {
            banner(icon: "clock.badge.exclamationmark", tint: .orange,
                   title: "数据已过期",
                   detail: "最近一次判定在 \(vm.ageText)，画面与结论都是历史结果。")
        }
        if vm.reading?.isSimulated == true {
            banner(icon: "flask", tint: .purple,
                   title: "DEMO 数据",
                   detail: "当前展示的是注入的模拟结果，不是真实推理输出。")
        }
    }

    private func banner(icon: String, tint: Color, title: String, detail: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: icon).foregroundStyle(tint)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.caption.weight(.semibold))
                Text(detail).font(.caption2).foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding(10)
        .background(tint.opacity(0.1), in: RoundedRectangle(cornerRadius: 10))
    }

    // MARK: 画面

    private var viewer: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 14).fill(Theme.panelBackground)

            if let url = vm.imageURL() {
                AsyncImage(url: url, transaction: Transaction(animation: .easeOut(duration: 0.2))) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().scaledToFit()
                    case .failure:
                        placeholder("影像不可用（可能已超出保留窗口）")
                    case .empty:
                        ProgressView().tint(.white)
                    @unknown default:
                        EmptyView()
                    }
                }
            } else {
                placeholder("暂无画面")
            }

            if let reading = vm.reading {
                VStack {
                    HStack(alignment: .top) {
                        HStack(spacing: 6) {
                            Text(reading.risk.rawValue)
                                .font(.caption2.weight(.bold).monospaced())
                                .padding(.horizontal, 7).padding(.vertical, 3)
                                .background(Theme.chip(reading.risk), in: RoundedRectangle(cornerRadius: 5))
                                .foregroundStyle(.white)
                            Text(reading.risk.text)
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.white)
                        }
                        .padding(8)
                        .background(.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 8))

                        Spacer()

                        Text(timeText(reading.timestamp))
                            .font(.caption2.monospaced())
                            .foregroundStyle(.white.opacity(0.85))
                            .padding(8)
                            .background(.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 8))
                    }
                    Spacer()
                    HStack(spacing: 6) {
                        Text("最窄剩余").foregroundStyle(.white.opacity(0.75))
                        Text(reading.widthText).foregroundStyle(.white).fontWeight(.bold)
                        Text("·").foregroundStyle(.white.opacity(0.5))
                        Text("\(reading.rockCount) 处障碍").foregroundStyle(.white)
                        Spacer()
                    }
                    .font(.caption)
                    .padding(8)
                    .background(.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 8))
                }
                .padding(10)
            }

            if vm.stale {
                RoundedRectangle(cornerRadius: 14).fill(.black.opacity(0.45))
                Text("数据已过期")
                    .font(.headline)
                    .foregroundStyle(.white)
            }
        }
        .aspectRatio(4.0 / 3.0, contentMode: .fit)
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    private func placeholder(_ text: String) -> some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(.white.opacity(0.6))
            .multilineTextAlignment(.center)
            .padding()
    }

    // MARK: 数据面板

    @ViewBuilder
    private var panel: some View {
        if let reading = vm.reading {
            VStack(spacing: 10) {
                hero(reading)

                HStack(spacing: 10) {
                    directionCard(title: "A → B", passability: reading.passabilityAToB,
                                  width: reading.widthAToB)
                    directionCard(title: "B → A", passability: reading.passabilityBToA,
                                  width: reading.widthBToA)
                }

                HStack(spacing: 10) {
                    DataCard(label: "障碍预警", trailing: "\(reading.rockCount) 处") {
                        Text(reading.rockCount > 0 ? "发现 \(reading.rockCount) 处障碍" : emptyObstacleText(reading))
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(Theme.tone(reading.overallPassability))
                        Text("证据 \(reading.evidenceSource)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Text("可通行区 \(reading.corridorSource)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    DataCard(label: "道路", trailing: reading.roadStatus ?? "—") {
                        Text("覆盖 \(reading.coverageText)")
                            .font(.subheadline.weight(.semibold))
                        Text(reading.processingMs > 0
                             ? "处理 \(Int(reading.processingMs)) ms"
                             : "处理耗时未上报")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }

                DataCard(label: "替代通行空间", trailing: reading.alternative) {
                    Text(alternativeText(reading.alternative))
                        .font(.subheadline.weight(.semibold))
                }
            }
        } else {
            VStack(spacing: 8) {
                ProgressView()
                Text("等待第一条判定…")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, minHeight: 160)
        }
    }

    private func hero(_ reading: Reading) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("道路总体状态")
                .font(.caption)
                .foregroundStyle(.white.opacity(0.85))
            Text(reading.overallPassability.text)
                .font(.system(size: 26, weight: .bold))
                .foregroundStyle(.white)
            HStack(spacing: 10) {
                Text(reading.risk.rawValue)
                    .font(.caption2.monospaced())
                    .padding(.horizontal, 7).padding(.vertical, 2)
                    .background(.white.opacity(0.18), in: RoundedRectangle(cornerRadius: 5))
                Text("风险：\(reading.risk.text)")
                Text("最窄剩余：\(reading.widthText)")
            }
            .font(.caption)
            .foregroundStyle(.white.opacity(0.95))

            if !reading.reasons.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(reading.reasons.prefix(6), id: \.self) { code in
                            Text(code)
                                .font(.system(size: 9, weight: .medium, design: .monospaced))
                                .padding(.horizontal, 6).padding(.vertical, 2)
                                .background(.white.opacity(0.16), in: RoundedRectangle(cornerRadius: 4))
                                .foregroundStyle(.white.opacity(0.92))
                        }
                    }
                }
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.gradient(reading.risk), in: RoundedRectangle(cornerRadius: 14))
    }

    private func directionCard(title: String, passability: Passability, width: Double?) -> some View {
        DataCard(label: title) {
            Text(passability.text)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(Theme.tone(passability))
            Text(width.map { String(format: "剩余 %.1f cm", $0 * 100) } ?? "剩余 —")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
    }

    private func emptyObstacleText(_ reading: Reading) -> String {
        reading.reasons.contains("OBSTACLE_SOURCE_GEOMETRIC")
            ? "未检出凸起障碍（≥8mm）"
            : "未发现落石"
    }

    private func alternativeText(_ raw: String) -> String {
        switch raw {
        case "AVAILABLE": return "有可绕行空间"
        case "UNAVAILABLE": return "无可绕行空间"
        case "NOT_EVALUATED": return "无需评估"
        case "UNKNOWN": return "无法确认"
        default: return raw
        }
    }

    private var footer: some View {
        HStack {
            Text("数据 \(vm.ageText)")
            Spacer()
            Text(config.displayText)
        }
        .font(.caption2)
        .foregroundStyle(.secondary)
    }

    private func timeText(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss"
        return formatter.string(from: date)
    }
}
