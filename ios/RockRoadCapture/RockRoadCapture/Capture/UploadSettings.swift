import Foundation
import Combine

/// 电脑端地址配置（M5）。
///
/// 单例：采集管理器与设置界面共用同一份配置，
/// 避免"界面上填了但上传用的是旧值"这类调试杀手。
final class UploadSettings: ObservableObject {

    static let shared = UploadSettings()

    private let defaults = UserDefaults.standard
    private enum Key {
        static let host = "upload.host"
        static let port = "upload.port"
    }

    @Published var host: String {
        didSet { defaults.set(host, forKey: Key.host) }
    }

    @Published var port: String {
        didSet { defaults.set(port, forKey: Key.port) }
    }

    private init() {
        host = defaults.string(forKey: Key.host) ?? ""
        port = defaults.string(forKey: Key.port) ?? "8000"
    }

    var isValid: Bool { baseURL != nil }

    var baseURL: URL? {
        let trimmed = host.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              let portValue = Int(port),
              (1...65535).contains(portValue) else { return nil }
        return URL(string: "http://\(trimmed):\(portValue)")
    }

    var ingestURL: URL? { baseURL?.appendingPathComponent("api/v1/ingest/frame") }
    var healthURL: URL? { baseURL?.appendingPathComponent("api/v1/health") }
}
