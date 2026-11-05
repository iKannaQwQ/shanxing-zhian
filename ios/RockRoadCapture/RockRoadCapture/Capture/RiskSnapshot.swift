import Foundation

/// 服务端判定的摘要。HTTP 响应（同步模式）与 WebSocket 推送（异步模式）共用。
struct RiskSnapshot: Equatable {
    var overallRisk = "-"
    var systemStatus = "-"
    var passAToB = "-"
    var passBToA = "-"
    var alternative = "-"
    var roadStatus = "-"
    var rockCount = 0
    var processingMs: Double = 0
    var droppedSinceLast = 0
    var reasons: [String] = []
    var error: String?
    var receivedAt: Date?

    /// 同时兼容两种报文：
    ///   * HTTP 202/200 响应：{ ok, road_status: {...}, processing_ms }
    ///   * WebSocket 推送：   { type: assessment, overall_risk, ... }
    static func parse(_ root: [String: Any]) -> RiskSnapshot? {
        var snapshot = RiskSnapshot()
        snapshot.receivedAt = Date()

        if let roadStatus = root["road_status"] as? [String: Any] {
            snapshot.processingMs = (root["processing_ms"] as? Double) ?? 0
            guard snapshot.fill(fromRoadStatus: roadStatus) else { return nil }
            return snapshot
        }

        if root["overall_risk"] != nil {
            snapshot.overallRisk = (root["overall_risk"] as? String) ?? "-"
            snapshot.systemStatus = (root["system_status"] as? String) ?? "-"
            if let pass = root["passability"] as? [String: Any] {
                snapshot.passAToB = (pass["A_TO_B"] as? String) ?? "-"
                snapshot.passBToA = (pass["B_TO_A"] as? String) ?? "-"
            }
            snapshot.alternative = (root["alternative"] as? String) ?? "-"
            snapshot.roadStatus = (root["road_status"] as? String) ?? "-"
            snapshot.rockCount = (root["rock_count"] as? Int) ?? 0
            snapshot.processingMs = (root["processing_ms"] as? Double) ?? 0
            snapshot.droppedSinceLast = (root["dropped_since_last"] as? Int) ?? 0
            if let codes = root["reasons"] as? [String] {
                snapshot.reasons = Array(codes.prefix(3))
            }
            return snapshot
        }

        return nil
    }

    private mutating func fill(fromRoadStatus roadStatus: [String: Any]) -> Bool {
        overallRisk = (roadStatus["overall_risk"] as? String) ?? "-"
        systemStatus = (roadStatus["system_status"] as? String) ?? "-"
        if let pass = roadStatus["passability"] as? [String: Any] {
            passAToB = (pass["A_TO_B"] as? String) ?? "-"
            passBToA = (pass["B_TO_A"] as? String) ?? "-"
        }
        if let alt = roadStatus["alternative_path"] as? [String: Any] {
            alternative = (alt["status"] as? String) ?? "-"
        }
        if let reasons = roadStatus["reasons"] as? [[String: Any]] {
            self.reasons = Array(reasons.compactMap { $0["code"] as? String }.prefix(3))
        }
        return overallRisk != "-"
    }
}
