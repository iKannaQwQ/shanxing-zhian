import SwiftUI

struct RootView: View {
    @StateObject private var config = ServerConfig()

    var body: some View {
        if let base = config.baseURL {
            // 换服务器地址时整体重建，否则 ViewModel 仍指向旧地址
            DashboardView(config: config, base: base).id(base)
        } else {
            SetupView(config: config, isFirstRun: true)
        }
    }
}

/// 服务地址设置。首次启动必填；之后可从主界面进入修改。
struct SetupView: View {
    @ObservedObject var config: ServerConfig
    let isFirstRun: Bool

    @Environment(\.dismiss) private var dismiss
    @State private var input = ""
    @State private var errorText: String?
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 32)

            VStack(spacing: 10) {
                Image(systemName: "mountain.2.fill")
                    .font(.system(size: 46, weight: .semibold))
                    .foregroundStyle(Color(hex: 0xF59E0B))
                Text("山行智安")
                    .font(.system(size: 28, weight: .bold))
                Text("山区公路落石预警")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }

            Spacer(minLength: 26)

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
            .padding(.top, 18)

            if !isFirstRun {
                Button("取消") { dismiss() }
                    .padding(.top, 12)
            }

            Spacer(minLength: 32)
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
