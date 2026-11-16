import Foundation

/// 服务地址的持久化与规范化。
///
/// 手机连的是局域网里的后端，地址会随网络变化，所以必须可配置，
/// 不能写死。用户只需输入 `192.168.1.5:8000` 这种最简形式。
@MainActor
final class ServerConfig: ObservableObject {
    @Published private(set) var baseURL: URL?

    private let storageKey = "server_base_url"

    init() {
        if let raw = UserDefaults.standard.string(forKey: storageKey) {
            baseURL = URL(string: raw)
        }
    }

    /// 把用户输入整理成合法 URL：补协议、补默认端口、去掉路径。
    static func normalize(_ text: String) -> URL? {
        var value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return nil }
        let lower = value.lowercased()
        if !lower.hasPrefix("http://") && !lower.hasPrefix("https://") {
            value = "http://" + value
        }
        guard let parsed = URL(string: value),
              let host = parsed.host, !host.isEmpty else { return nil }
        var components = URLComponents(url: parsed, resolvingAgainstBaseURL: false)
        if components?.port == nil { components?.port = 8000 }
        components?.path = ""
        components?.query = nil
        components?.fragment = nil
        return components?.url
    }

    @discardableResult
    func save(_ text: String) -> Bool {
        guard let url = Self.normalize(text) else { return false }
        baseURL = url
        UserDefaults.standard.set(url.absoluteString, forKey: storageKey)
        return true
    }

    func clear() {
        baseURL = nil
        UserDefaults.standard.removeObject(forKey: storageKey)
    }

    var displayText: String {
        guard let baseURL else { return "" }
        return "\(baseURL.host ?? ""):\(baseURL.port ?? 8000)"
    }
}
