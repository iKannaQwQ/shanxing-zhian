import Combine
import Foundation
import QuartzCore

/// M5：带重试与有界队列的帧上传器。
///
/// 设计要点：
///   * **单请求串行**：局域网 10 fps 下不需要并发，串行更容易观察拥塞；
///   * **有界队列**：满了丢最旧的一帧并计数——积压比丢帧更糟，
///     因为队列里的帧到得越晚越没有意义；
///   * **重试有上限**：3 次，指数退避 0.2/0.5/1.0 s，仍失败则丢弃并计数；
///   * 所有状态只在 `stateQueue` 上修改，对外发布快照。
final class FrameUploader: ObservableObject {

    enum Connection: Equatable {
        case unknown
        case checking
        case ok(String)
        case failed(String)
    }

    struct Stats: Equatable {
        var capacity = 16
        var queued = 0
        var inFlight = false
        var sent = 0
        var failed = 0
        var droppedOverflow = 0
        var droppedQuality = 0
        var droppedNoServer = 0
        var lastStatusCode = 0
        var lastLatencyMs: Double = 0
        var avgLatencyMs: Double = 0
        var lastUploadBytes = 0
        var lastError: String?
    }

    /// 端到端回显：服务端返回的 RoadStatus 摘要。
    struct RiskSnapshot: Equatable {
        var overallRisk: String = "-"
        var systemStatus: String = "-"
        var passAToB: String = "-"
        var passBToA: String = "-"
        var processingMs: Double = 0
        var reasons: [String] = []
        var error: String?
        var receivedAt: Date?
        var frameCount: Int = 0
    }

    @Published private(set) var stats = Stats()
    @Published private(set) var connection: Connection = .unknown
    @Published private(set) var lastResult = RiskSnapshot()

    private struct Item {
        var bundle: FrameBundle
        var attempt: Int
    }

    private let stateQueue = DispatchQueue(label: "com.mountain.roadcapture.upload")
    private let session: URLSession
    private let capacity = 16
    private let maxAttempts = 3

    private var queue: [Item] = []
    private var busy = false
    private var ingestURL: URL?
    private var counters = Stats()
    private var latencies: [Double] = []

    init() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 3
        configuration.timeoutIntervalForResource = 10
        configuration.waitsForConnectivity = false
        session = URLSession(configuration: configuration)
    }

    // MARK: - 对外

    func reset() {
        stateQueue.async {
            self.queue.removeAll()
            self.counters = Stats()
            self.latencies.removeAll()
            self.publish()
        }
    }

    /// 由配置变更在**主线程**调用；内部转投到 stateQueue，避免跨线程读写。
    func setIngestURL(_ url: URL?) {
        stateQueue.async { self.ingestURL = url }
    }

    func enqueue(_ bundle: FrameBundle) {
        stateQueue.async {
            guard self.ingestURL != nil else {
                self.counters.droppedNoServer += 1
                self.publish()
                return
            }
            if self.queue.count >= self.capacity {
                // 拥塞：丢最旧的一帧，保新弃旧
                self.queue.removeFirst()
                self.counters.droppedOverflow += 1
            }
            self.queue.append(Item(bundle: bundle, attempt: 0))
            self.publish()
            self.pump()
        }
    }

    func recordQualityDrop() {
        stateQueue.async {
            self.counters.droppedQuality += 1
            self.publish()
        }
    }

    func testConnection(settings: UploadSettings) async {
        await MainActor.run { self.connection = .checking }
        guard let url = settings.healthURL else {
            await MainActor.run { self.connection = .failed("地址未填写或不合法") }
            return
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 3
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                await MainActor.run { self.connection = .failed("非 HTTP 响应") }
                return
            }
            let text = String(data: data.prefix(120), encoding: .utf8) ?? ""
            let ok = (200...299).contains(http.statusCode)
            await MainActor.run {
                self.connection = ok ? .ok("HTTP \(http.statusCode) \(text)")
                                     : .failed("HTTP \(http.statusCode) \(text)")
            }
        } catch {
            await MainActor.run { self.connection = .failed(error.localizedDescription) }
        }
    }

    // MARK: - 内部

    private func pump() {
        guard !busy, let url = ingestURL, !queue.isEmpty else {
            publish()
            return
        }
        let item = queue.removeFirst()
        busy = true

        let request = makeRequest(item.bundle, url: url)
        let started = CACurrentMediaTime()

        session.dataTask(with: request) { [weak self] data, response, error in
            guard let self else { return }
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            let latency = (CACurrentMediaTime() - started) * 1000
            let bodyText = data.flatMap { String(data: $0.prefix(180), encoding: .utf8) } ?? ""

            self.stateQueue.async {
                self.busy = false
                if let error {
                    let retried = self.handleFailure(item, message: error.localizedDescription)
                    self.publish()
                    if !retried { self.pump() }
                } else if (200...299).contains(status) {
                    self.counters.sent += 1
                    self.counters.lastStatusCode = status
                    self.counters.lastError = nil
                    self.counters.lastLatencyMs = latency
                    self.counters.lastUploadBytes = item.bundle.totalBytes
                    self.recordLatency(latency)
                    self.publishResult(from: data)
                    self.publish()
                    self.pump()
                } else if (400..<500).contains(status) {
                    // 4xx 是请求本身的问题，重试没有意义
                    self.counters.failed += 1
                    self.counters.lastStatusCode = status
                    self.counters.lastError = "HTTP \(status) \(bodyText)"
                    self.publishResult(error: "HTTP \(status) \(bodyText)")
                    self.publish()
                    self.pump()
                } else {
                    let retried = self.handleFailure(item, message: "HTTP \(status) \(bodyText)")
                    self.publish()
                    if !retried { self.pump() }
                }
            }
        }.resume()
    }

    /// 返回 true 表示已安排重试（调用方不要立刻 pump）。
    @discardableResult
    private func handleFailure(_ item: Item, message: String) -> Bool {
        counters.lastError = message
        let nextAttempt = item.attempt + 1
        guard nextAttempt < maxAttempts else {
            counters.failed += 1
            return false
        }
        var retry = item
        retry.attempt = nextAttempt
        queue.insert(retry, at: 0)          // 保持顺序：重试优先于新帧

        let backoff = [0.2, 0.5, 1.0][min(nextAttempt - 1, 2)]
        stateQueue.asyncAfter(deadline: .now() + backoff) { self.pump() }
        return true
    }

    /// 解析服务端返回的 road_status，派发到主线程更新界面。
    private func publishResult(from data: Data?) {
        var snapshot = RiskSnapshot()
        snapshot.frameCount = counters.sent
        snapshot.receivedAt = Date()
        if let data,
           let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            snapshot.processingMs = (root["processing_ms"] as? Double) ?? 0
            if let rs = root["road_status"] as? [String: Any] {
                snapshot.overallRisk = (rs["overall_risk"] as? String) ?? "-"
                snapshot.systemStatus = (rs["system_status"] as? String) ?? "-"
                if let pass = rs["passability"] as? [String: Any] {
                    snapshot.passAToB = (pass["A_TO_B"] as? String) ?? "-"
                    snapshot.passBToA = (pass["B_TO_A"] as? String) ?? "-"
                }
                if let reasons = rs["reasons"] as? [[String: Any]] {
                    snapshot.reasons = Array(reasons.compactMap { $0["code"] as? String }.prefix(3))
                }
            } else {
                snapshot.error = "响应缺少 road_status"
            }
        } else {
            snapshot.error = "响应不是合法 JSON"
        }
        let value = snapshot
        DispatchQueue.main.async { self.lastResult = value }
    }

    private func publishResult(error: String) {
        var snapshot = lastResultSnapshotForError()
        snapshot.error = error
        let value = snapshot
        DispatchQueue.main.async { self.lastResult = value }
    }

    private func lastResultSnapshotForError() -> RiskSnapshot {
        var snapshot = RiskSnapshot()
        snapshot.frameCount = counters.sent
        snapshot.receivedAt = Date()
        return snapshot
    }

    private func recordLatency(_ value: Double) {
        latencies.append(value)
        if latencies.count > 20 { latencies.removeFirst() }
        counters.avgLatencyMs = latencies.reduce(0, +) / Double(latencies.count)
    }

    private func publish() {
        var snapshot = counters
        snapshot.queued = queue.count
        snapshot.inFlight = busy
        snapshot.capacity = capacity
        DispatchQueue.main.async { self.stats = snapshot }
    }

    private func makeRequest(_ bundle: FrameBundle, url: URL) -> URLRequest {
        let boundary = "----RoadCapture-\(UUID().uuidString)"
        var parts: [MultipartBuilder.Part] = [
            MultipartBuilder.Part(name: "meta", filename: nil,
                                  contentType: "application/json",
                                  data: bundle.metadataJSON),
            MultipartBuilder.Part(name: "rgb", filename: "rgb.jpg",
                                  contentType: "image/jpeg", data: bundle.rgbJPEG),
            MultipartBuilder.Part(name: "depth", filename: "depth.bin",
                                  contentType: "application/octet-stream",
                                  data: bundle.depth.data),
        ]
        if let confidence = bundle.confidence {
            parts.append(MultipartBuilder.Part(name: "confidence",
                                               filename: "confidence.bin",
                                               contentType: "application/octet-stream",
                                               data: confidence.data))
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("multipart/form-data; boundary=\(boundary)",
                         forHTTPHeaderField: "Content-Type")
        request.httpBody = MultipartBuilder.makeBody(parts: parts, boundary: boundary)
        return request
    }
}
