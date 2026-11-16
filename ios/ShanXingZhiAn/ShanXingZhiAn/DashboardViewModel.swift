import Foundation

/// 数据来源：WebSocket 为主，轮询兜底。
///
/// 注意 `snapshot` 是**历史数据**，不能当作实时推送——网页端曾在这里出过错，
/// 原生版沿用同样的判定：只有 `assessment` 才重置"新鲜"计时。
@MainActor
final class DashboardViewModel: ObservableObject {
    @Published private(set) var reading: Reading?
    @Published private(set) var connected = false
    @Published private(set) var received = 0
    @Published private(set) var stale = false
    @Published private(set) var errorText: String?
    @Published private(set) var imageVersion: Int64 = 0

    private let api: APIClient
    private var socketTask: URLSessionWebSocketTask?
    private var reconnectTask: Task<Void, Never>?
    private var pollTask: Task<Void, Never>?
    private var tickTask: Task<Void, Never>?
    private var stopped = false

    private var lastLiveAt: Date?
    private var fetchedAt: Date?
    private var fetchedAgeMs: Double = 0
    private var staleAfterMs: Double = 5000

    init(base: URL) {
        self.api = APIClient(base: base)
    }

    // MARK: 生命周期

    func start() {
        stopped = false
        connectSocket()
        startPolling()
        startTicking()
    }

    func stop() {
        stopped = true
        socketTask?.cancel(with: .goingAway, reason: nil)
        socketTask = nil
        reconnectTask?.cancel()
        pollTask?.cancel()
        tickTask?.cancel()
        connected = false
    }

    func retry() {
        errorText = nil
        stopped = false
        connectSocket()
        Task { await refreshOnce() }
    }

    func imageURL() -> URL? {
        guard let reading else { return nil }
        return api.frameImageURL(frameId: reading.id, version: imageVersion)
    }

    // MARK: 轮询

    private func startPolling() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refreshOnce()
                try? await Task.sleep(nanoseconds: 3_000_000_000)
            }
        }
    }

    func refreshOnce() async {
        do {
            let payload = try await api.status()
            guard payload.ready != false else {
                errorText = "后端尚未就绪"
                return
            }
            staleAfterMs = Double(payload.staleAfterMs ?? 5000)
            fetchedAgeMs = payload.ageMs ?? 0
            fetchedAt = Date()
            if let row = payload.latest {
                apply(row: row, isLive: false)
            }
            errorText = nil
        } catch {
            errorText = "无法连接后端：\(error.localizedDescription)"
        }
        evaluateStale()
    }

    // MARK: WebSocket

    private func connectSocket() {
        guard !stopped, let url = api.socketURL() else { return }
        socketTask?.cancel(with: .goingAway, reason: nil)
        let task = URLSession.shared.webSocketTask(with: url)
        socketTask = task
        task.resume()
        connected = true
        listen(on: task)
    }

    private func listen(on task: URLSessionWebSocketTask) {
        Task { [weak self] in
            while true {
                guard let self else { return }
                do {
                    let message = try await task.receive()
                    await MainActor.run { self.handle(message: message) }
                } catch {
                    await MainActor.run {
                        self.connected = false
                        self.scheduleReconnect()
                    }
                    return
                }
            }
        }
    }

    private func scheduleReconnect() {
        guard !stopped else { return }
        reconnectTask?.cancel()
        reconnectTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard let self, !Task.isCancelled, !self.stopped else { return }
            self.connectSocket()
        }
    }

    private func handle(message: URLSessionWebSocketTask.Message) {
        guard case .string(let text) = message,
              let data = text.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let type = object["type"] as? String else { return }

        switch type {
        case "snapshot":
            // 历史快照：只用来填充界面，不重置"新鲜"计时
            if let latest = object["latest"] as? [String: Any],
               let row = try? decodeRow(latest) {
                apply(row: row, isLive: false)
            }
        case "assessment":
            received += 1
            if let reading = Reading.from(message: object) {
                self.reading = reading
                imageVersion = Int64(Date().timeIntervalSince1970 * 1000)
                lastLiveAt = Date()
            }
        case "stale":
            stale = true
        case "fresh":
            stale = false
        case "error":
            errorText = object["code"] as? String
        default:
            break
        }
        evaluateStale()
    }

    private func decodeRow(_ dict: [String: Any]) throws -> AssessmentRow {
        let data = try JSONSerialization.data(withJSONObject: dict)
        return try JSONDecoder().decode(AssessmentRow.self, from: data)
    }

    // MARK: 状态合并

    private func apply(row: AssessmentRow, isLive: Bool) {
        let next = Reading.from(row: row)
        // 同一条判定不重复刷新画面
        if reading?.id != next.id {
            reading = next
            imageVersion = Int64(row.tsAssessedNs)
        }
        if isLive { lastLiveAt = Date() }
        evaluateStale()
    }

    private func startTicking() {
        tickTask?.cancel()
        tickTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                await MainActor.run { self?.evaluateStale() }
            }
        }
    }

    private func evaluateStale() {
        // 还没有任何判定时不算"过期"，否则首屏会误报
        guard reading != nil else {
            if stale { stale = false }
            return
        }
        let age: TimeInterval
        if let lastLiveAt {
            age = Date().timeIntervalSince(lastLiveAt)
        } else if let fetchedAt {
            age = fetchedAgeMs / 1000 + Date().timeIntervalSince(fetchedAt)
        } else {
            age = .greatestFiniteMagnitude
        }
        let computed = age * 1000 > staleAfterMs
        if computed != stale { stale = computed }
    }

    var ageText: String {
        guard let reading else { return "尚无数据" }
        let seconds = Int(Date().timeIntervalSince(reading.timestamp))
        if lastLiveAt != nil, seconds < 2 { return "刚刚" }
        if seconds < 60 { return "\(seconds) 秒前" }
        if seconds < 3600 { return "\(seconds / 60) 分钟前" }
        return "\(seconds / 3600) 小时前"
    }
}
