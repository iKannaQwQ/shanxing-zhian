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

    @Published private(set) var stats = Stats()
    @Published private(set) var connection: Connection = .unknown
    @Published private(set) var lastResult = RiskSnapshot()

    private struct Item {
        var bundle: FrameBundle
        var attempt: Int
    }

    private let stateQueue = DispatchQueue(label: "com.mountain.roadcapture.upload")
    private let session: URLSession
    private let capacity = 32
    private let maxAttempts = 3

    private var queue: [Item] = []
    private var busy = false
    private var ingestURL: URL?
    private var counters = Stats()
    private var latencies: [Double] = []
    private var reportedDrops = 0

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

    /// 设备心跳：把热状态与降级后的采集参数告诉后端，
    /// 让下游知道"这段时间的数据是降频采集的"，而不是当成正常数据。
    func sendHeartbeat(base: URL, payload: [String: Any]) {
        guard let url = URL(string: "api/v1/devices/heartbeat", relativeTo: base) else { return }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 3
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: payload)
        session.dataTask(with: request).resume()
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

        let drops = max(0, counters.droppedOverflow - reportedDrops)
        reportedDrops = counters.droppedOverflow
        let request = makeRequest(item.bundle, url: url, droppedSinceLast: drops)
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

    /// 解析服务端响应。
    ///
    /// 异步后端返回 202 + accepted，没有 road_status——判定结果走 WebSocket，
    /// 因此这里解析不到时不要报错，保持上一次结果即可。
    private func publishResult(from data: Data?) {
        guard let data,
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return
        }
        if let snapshot = RiskSnapshot.parse(root) {
            DispatchQueue.main.async { self.lastResult = snapshot }
        }
    }

    private func publishResult(error: String) {
        var snapshot = RiskSnapshot()
        snapshot.error = error
        snapshot.receivedAt = Date()
        DispatchQueue.main.async { self.lastResult = snapshot }
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

    private func makeRequest(_ bundle: FrameBundle, url: URL,
                             droppedSinceLast: Int = 0) -> URLRequest {
        let boundary = "----RoadCapture-\(UUID().uuidString)"
        var parts: [MultipartBuilder.Part] = [
            MultipartBuilder.Part(name: "meta", filename: nil,
                                  contentType: "application/json",
                                  data: bundle.metadataJSON(droppedSinceLast: droppedSinceLast)),
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
