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
    private var connectedFlag = false
    private var messages = 0
    private var attempts = 0
    private var watchdog: Timer?

    func update(url: URL?) {
        guard url != currentURL else { return }
        currentURL = url
        if shouldRun { reconnect() }
    }

    func start() {
        shouldRun = true
        stopping = false
        startWatchdog()
        reconnect()
    }

    /// 从后台回到前台时调用：socket 通常已被系统杀掉，必须重建。
    func resumeFromBackground() {
        guard shouldRun else { return }
        connectedFlag = false
        reconnect()
    }

    /// 每 5 秒检查一次：该连却没连上就重试。
    /// 依赖"失败回调"来重连是不够的——熄屏/切后台会直接掐断 socket，
    /// 回调未必有机会执行。
    private func startWatchdog() {
        watchdog?.invalidate()
        watchdog = Timer.scheduledTimer(withTimeInterval: 5.0, repeats: true) { [weak self] _ in
            guard let self else { return }
            guard self.shouldRun, !self.connectedFlag else { return }
            self.reconnect()
        }
    }

    func stop() {
        stopping = true
        shouldRun = false
        connectedFlag = false
        watchdog?.invalidate()
        watchdog = nil
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        DispatchQueue.main.async { self.state = "已断开" }
    }

    // MARK: - 内部

    private func reconnect() {
        attempts += 1
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
        DispatchQueue.main.async { self.state = "连接中…（第 \(self.attempts) 次）" }
        listen(on: socket)
    }

    private func listen(on socket: URLSessionWebSocketTask) {
        socket.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .failure(let error):
                self.connectedFlag = false
                // 主动停止时收到的收尾错误不显示，避免把"正常关闭"报成故障
                guard !self.stopping else { return }
                DispatchQueue.main.async {
                    self.state = "断开: \(error.localizedDescription)（第 \(self.attempts) 次尝试）"
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
        guard shouldRun, !stopping, !reconnectScheduled else { return }
        reconnectScheduled = true
        DispatchQueue.global().asyncAfter(deadline: .now() + 2.0) { [weak self] in
            guard let self else { return }
            self.reconnectScheduled = false
            self.reconnect()
        }
    }
}
