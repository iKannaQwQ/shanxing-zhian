import Foundation

// MARK: - 后端数据模型

enum RiskLevel: String, Decodable {
    case safe = "SAFE", low = "LOW", medium = "MEDIUM", high = "HIGH", unknown = "UNKNOWN"

    var text: String {
        switch self {
        case .safe: return "未见侵入"
        case .low: return "附近有落石"
        case .medium: return "部分侵入"
        case .high: return "严重阻断"
        case .unknown: return "未知"
        }
    }
}

enum Passability: String, Decodable {
    case passable = "PASSABLE", restricted = "RESTRICTED", blocked = "BLOCKED", unknown = "UNKNOWN"

    var text: String {
        switch self {
        case .passable: return "可通行"
        case .restricted: return "受限通行"
        case .blocked: return "不可通行"
        case .unknown: return "无法判断"
        }
    }

    /// 双向里更差的一档，用于总体状态。
    var severity: Int {
        switch self {
        case .blocked: return 3
        case .restricted: return 2
        case .passable: return 1
        case .unknown: return 0
        }
    }
}

/// /api/v1/status 的顶层结构。未就绪时后端只返回 `{"ready": false}`。
struct StatusPayload: Decodable {
    let ready: Bool?
    let serverNs: Int64?
    let latest: AssessmentRow?
    let ageMs: Double?
    let stale: Bool?
    let staleAfterMs: Int64?

    enum CodingKeys: String, CodingKey {
        case ready
        case serverNs = "server_ns"
        case latest
        case ageMs = "age_ms"
        case stale
        case staleAfterMs = "stale_after_ms"
    }
}

/// 数据库里的一行判定。`json` 是引擎原始输出，里面才有双向剩余宽度。
struct AssessmentRow: Decodable {
    let frameId: String
    let deviceId: String
    let tsAssessedNs: Int64
    let overallRisk: String
    let systemStatus: String
    let aToB: String?
    let bToA: String?
    let alternativeStatus: String?
    let roadDetected: Int?
    let roadStatus: String?
    let roadCoverage: Double?
    let rockCount: Int?
    let processingMs: Double?
    let json: String?
    let source: String?

    enum CodingKeys: String, CodingKey {
        case frameId = "frame_id"
        case deviceId = "device_id"
        case tsAssessedNs = "ts_assessed_ns"
        case overallRisk = "overall_risk"
        case systemStatus = "system_status"
        case aToB = "a_to_b"
        case bToA = "b_to_a"
        case alternativeStatus = "alternative_status"
        case roadDetected = "road_detected"
        case roadStatus = "road_status"
        case roadCoverage = "road_coverage"
        case rockCount = "rock_count"
        case processingMs = "processing_ms"
        case json
        case source
    }
}

/// 引擎 JSON 中本项目关心的部分。
struct EngineDetail: Decodable {
    struct Direction: Decodable {
        let direction: String
        let passability: String?
        let remainingWidthM: Double?

        enum CodingKeys: String, CodingKey {
            case direction, passability
            case remainingWidthM = "remaining_width_m"
        }
    }

    struct Reason: Decodable {
        let code: String
    }

    let directions: [Direction]?
    let reasons: [Reason]?
}

// MARK: - 界面使用的读值

/// 界面只需要这些字段，其余的都是内部细节。
struct Reading: Identifiable {
    let id: String              // frame_id，兼作影像缓存键
    let risk: RiskLevel
    let systemStatus: String
    let passabilityAToB: Passability
    let passabilityBToA: Passability
    let widthAToB: Double?
    let widthBToA: Double?
    let alternative: String
    let roadStatus: String?
    let coverage: Double?
    let rockCount: Int
    let reasons: [String]
    let processingMs: Double
    let timestamp: Date
    let isSimulated: Bool

    var overallPassability: Passability {
        passabilityAToB.severity <= passabilityBToA.severity ? passabilityBToA : passabilityAToB
    }

    /// 双向中最窄的剩余宽度（米）。
    var minWidth: Double? {
        [widthAToB, widthBToA].compactMap { $0 }.min()
    }

    var widthText: String {
        guard let minWidth else { return "—" }
        return String(format: "%.1f cm", minWidth * 100)
    }

    var coverageText: String {
        guard let coverage else { return "—" }
        return String(format: "%.1f%%", coverage * 100)
    }

    /// 证据来源：几何 / 检测器 / 不可用。
    var evidenceSource: String {
        if reasons.contains("OBSTACLE_SOURCE_GEOMETRIC") { return "深度几何" }
        if reasons.contains("ROCK_DETECTOR_UNAVAILABLE") { return "不可用" }
        return rockCount > 0 ? "检测器" : "—"
    }

    var corridorSource: String {
        reasons.contains("CORRIDOR_FROM_PLANE") ? "平整地面" : "道路分割"
    }

    static func from(row: AssessmentRow) -> Reading {
        var directions: [EngineDetail.Direction] = []
        var reasons: [String] = []
        if let raw = row.json, let data = raw.data(using: .utf8),
           let detail = try? JSONDecoder().decode(EngineDetail.self, from: data) {
            directions = detail.directions ?? []
            reasons = (detail.reasons ?? []).map(\.code)
        }

        func width(_ name: String) -> Double? {
            directions.first { $0.direction == name }?.remainingWidthM
        }

        return Reading(
            id: row.frameId,
            risk: RiskLevel(rawValue: row.overallRisk) ?? .unknown,
            systemStatus: row.systemStatus,
            passabilityAToB: Passability(rawValue: row.aToB ?? "") ?? .unknown,
            passabilityBToA: Passability(rawValue: row.bToA ?? "") ?? .unknown,
            widthAToB: width("A_TO_B"),
            widthBToA: width("B_TO_A"),
            alternative: row.alternativeStatus ?? "UNKNOWN",
            roadStatus: row.roadStatus,
            coverage: row.roadCoverage,
            rockCount: row.rockCount ?? 0,
            reasons: reasons,
            processingMs: row.processingMs ?? 0,
            timestamp: Date(timeIntervalSince1970: Double(row.tsAssessedNs) / 1_000_000_000),
            isSimulated: row.source == "SIMULATED"
        )
    }

    /// WebSocket 推送的 assessment 消息是扁平结构，单独构造。
    static func from(message: [String: Any]) -> Reading? {
        guard let frameId = message["frame_id"] as? String else { return nil }
        let passability = message["passability"] as? [String: Any] ?? [:]
        let directions = message["directions"] as? [[String: Any]] ?? []

        func width(_ name: String) -> Double? {
            directions.first { ($0["direction"] as? String) == name }?["remaining_width_m"] as? Double
        }

        return Reading(
            id: frameId,
            risk: RiskLevel(rawValue: message["overall_risk"] as? String ?? "") ?? .unknown,
            systemStatus: message["system_status"] as? String ?? "-",
            passabilityAToB: Passability(rawValue: passability["A_TO_B"] as? String ?? "") ?? .unknown,
            passabilityBToA: Passability(rawValue: passability["B_TO_A"] as? String ?? "") ?? .unknown,
            widthAToB: width("A_TO_B"),
            widthBToA: width("B_TO_A"),
            alternative: message["alternative"] as? String ?? "UNKNOWN",
            roadStatus: message["road_status"] as? String,
            coverage: message["road_coverage"] as? Double,
            rockCount: message["rock_count"] as? Int ?? 0,
            reasons: message["reasons"] as? [String] ?? [],
            processingMs: message["processing_ms"] as? Double ?? 0,
            timestamp: Date(),
            isSimulated: (message["source"] as? String) == "SIMULATED"
        )
    }
}
