import Foundation

/// 后端接口的最小封装。
struct APIClient {
    let base: URL

    func status() async throws -> StatusPayload {
        let url = base.appendingPathComponent("api/v1/status")
        var request = URLRequest(url: url)
        request.timeoutInterval = 6
        request.cachePolicy = .reloadIgnoringLocalCacheData
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode(StatusPayload.self, from: data)
    }

    /// 最新画面。`version` 用判定时间戳，保证换帧时 URL 变化从而绕过缓存。
    func frameImageURL(frameId: String, version: Int64) -> URL? {
        var components = URLComponents(
            url: base.appendingPathComponent("api/v1/frames/\(frameId)/rgb"),
            resolvingAgainstBaseURL: false)
        components?.queryItems = [URLQueryItem(name: "v", value: String(version))]
        return components?.url
    }

    func socketURL() -> URL? {
        guard var components = URLComponents(url: base, resolvingAgainstBaseURL: false) else {
            return nil
        }
        components.scheme = (components.scheme == "https") ? "wss" : "ws"
        components.path = "/ws/status"
        components.query = nil
        return components.url
    }
}
