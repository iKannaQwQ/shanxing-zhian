import SwiftUI

struct RootView: View {
    @StateObject private var config = ServerConfig()

    var body: some View {
        if let base = config.baseURL {
            DashboardView(config: config, base: base)
        } else {
            SetupView(config: config, isFirstRun: true)
        }
    }
}

/// 首次进入或主动改地址时的设置页。
struct SetupView: View {
    @ObservedObject var config: ServerConfig
    let isFirstRun: Bool

    @Environment(\.dismiss) private var dismiss
    @State private var input = ""
    @State private var errorText: String?
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 40)

            VStack(spacing: 10) {
                Image(systemName: "mountain.2.fill")
                    .font(.system(size: 44, weight: .semibold))
                    .foregroundStyle(Color(red: 0.96, green: 0.62, blue: 0.04))
                Text("山行智安")
                    .font(.system(size: 28, weight: .bold))
                Text("山区公路落石预警")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Spacer(minLength: 28)

            VStack(alignment: .leading, spacing: 10) {
                Text("服务器地址")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.secondary)
                TextField("192.168.1.5:8000", text: $input)
                    .textFieldStyle(.roundedBorder)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .focused($focused)
                    .submitLabel(.go)
                    .onSubmit(connect)

                if let errorText {
                    Text(errorText)
                        .font(.footnote)
                        .foregroundStyle(.red)
                }

                Text("手机与电脑需在同一局域网。地址可只填 IP，端口默认 8000。")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 24)

            Button(action: connect) {
                Text("连接")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
            }
            .buttonStyle(.borderedProminent)
            .padding(.horizontal, 24)
            .padding(.top, 20)

            if !isFirstRun {
                Button("取消") { dismiss() }
                    .padding(.top, 12)
            }

            Spacer(minLength: 40)
        }
        .onAppear { input = config.displayText }
    }

    private func connect() {
        guard config.save(input) else {
            errorText = "地址格式不对，示例：192.168.1.5:8000"
            return
        }
        errorText = nil
        focused = false
        if !isFirstRun { dismiss() }
    }
}

/// 主界面：全屏网页 + 轻量浮层（加载中 / 失败重试 / 设置入口）。
struct DashboardView: View {
    @ObservedObject var config: ServerConfig
    let base: URL

    @State private var isLoading = true
    @State private var loadError: String?
    @State private var reloadToken = 0
    @State private var showSettings = false

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            WebPanel(url: base,
                     isLoading: $isLoading,
                     loadError: $loadError,
                     reloadToken: reloadToken)
                .ignoresSafeArea(edges: .bottom)

            if isLoading && loadError == nil {
                ProgressView("正在连接 \(config.displayText)…")
                    .padding(16)
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Color.black.opacity(0.15))
            }

            if let failure = loadError {
                VStack(spacing: 12) {
                    Image(systemName: "wifi.exclamationmark")
                        .font(.system(size: 34))
                        .foregroundStyle(.orange)
                    Text("连接失败")
                        .font(.headline)
                    Text(failure)
                        .font(.footnote)
                        .multilineTextAlignment(.center)
                        .foregroundStyle(.secondary)
                    Text("确认后端已启动，且手机与电脑在同一网络。")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                    HStack(spacing: 12) {
                        Button("重试") { reloadToken += 1; loadError = nil; isLoading = true }
                            .buttonStyle(.borderedProminent)
                        Button("改地址") { showSettings = true }
                            .buttonStyle(.bordered)
                    }
                    .padding(.top, 4)
                }
                .padding(24)
                .frame(maxWidth: 320)
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }

            Button {
                showSettings = true
            } label: {
                Image(systemName: "gearshape.fill")
                    .font(.system(size: 15, weight: .semibold))
                    .padding(11)
                    .background(.ultraThinMaterial, in: Circle())
            }
            .padding(.trailing, 16)
            .padding(.bottom, 24)
        }
        .sheet(isPresented: $showSettings) {
            SetupView(config: config, isFirstRun: false)
        }
        .onChange(of: config.baseURL) { _ in
            isLoading = true
            loadError = nil
            reloadToken += 1
        }
    }
}
