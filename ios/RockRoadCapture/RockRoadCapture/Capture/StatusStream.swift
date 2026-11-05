import Combine
import Foundation

/// WebSocket 状态流：接收服务端推送的判定结果（M6 异步架构）。
///
/// 上传改成"立即 202"之后，判定结果不再随 HTTP 响应返回，
/// 而是通过 `/ws/status` 持续推送——这样客户端的发送速率就不会被服务端算力限速。
final class StatusStream: NSObject, ObservableObject {

    @Published private(set) var state = "未连接"
    @Published private(set) var lastSnapshot: RiskSnapshot?

    private let session = URLSession(configuration: .default)
    private var task: URLSessionWebSocketTask?
    private var currentURL: URL?
    private var shouldRun = false
    private var reconnectScheduled = false
    private var stopping = false
    private var messages = 0

    func update(url: URL?) {
        guard url != currentURL else { return }
        currentURL = url
        if shouldRun { reconnect() }
    }

    func start() {
        shouldRun = true
        reconnect()
    }

    func stop() {
        stopping = true
        shouldRun = false
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        DispatchQueue.main.async { self.state = "已断开" }
    }

    // MARK: - 内部

    private func reconnect() {
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        guard shouldRun, let url = currentURL else {
            DispatchQueue.main.async { self.state = "未配置" }
            return
        }
        stopping = false
        let socket = session.webSocketTask(with: url)
        task = socket
        socket.resume()
        DispatchQueue.main.async { self.state = "连接中…" }
        listen(on: socket)
    }

    private func listen(on socket: URLSessionWebSocketTask) {
        socket.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .failure(let error):
                // 主动停止时收到的收尾错误不显示，避免把"正常关闭"报成故障
                guard !self.stopping else { return }
                DispatchQueue.main.async {
                    self.state = "断开: \(error.localizedDescription)"
                }
                self.scheduleReconnect()
            case .success(let message):
                if case .string(let text) = message,
                   let data = text.data(using: .utf8),
                   let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    let snapshot = RiskSnapshot.parse(root)
                    self.messages += 1
                    DispatchQueue.main.async {
                        self.state = "已连接（已收 \(self.messages) 条）"
                        if let snapshot { self.lastSnapshot = snapshot }
                    }
                }
                self.listen(on: socket)
            }
        }
    }

    private func scheduleReconnect() {
        guard shouldRun, !reconnectScheduled else { return }
        reconnectScheduled = true
        DispatchQueue.global().asyncAfter(deadline: .now() + 2.0) { [weak self] in
            guard let self else { return }
            self.reconnectScheduled = false
            self.reconnect()
        }
    }
}
