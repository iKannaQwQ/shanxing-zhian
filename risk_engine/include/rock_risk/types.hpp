#pragma once
// 风险引擎的公共数据类型。
// 设计约束：
//   1. 所有"未知"都有独立枚举值，不用 0 / false 冒充；
//   2. 所有可选数值用 std::optional，序列化为 JSON null；
//   3. 不引入任何平台相关类型（无 macOS / Windows 专属 API）。

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace rock_risk {

// ---------- 枚举 ----------

enum class RiskLevel { kSafe, kLow, kMedium, kHigh, kUnknown };

enum class Passability { kPassable, kRestricted, kBlocked, kUnknown };

enum class Direction { kAToB, kBToA };

enum class AlternativeStatus { kAvailable, kUnavailable, kUnknown, kNotEvaluated };

enum class SystemStatus {
  kOk,
  kDegraded,
  kStale,
  kNoRoad,
  kNoDepth,
  kInputError,
};

enum class Intrusion { kNone, kEdge, kPartial, kFull, kUnknown };

enum class DepthSource { kNone, kLidar, kMonocular };

enum class Oncoming { kPresent, kConfirmedNoneVisible, kUnverifiable, kUnknown };

enum class RoadMaskStatus { kOk, kEmpty, kTooSmall, kFragmented, kLowConfidence, kFailed };

// 感知侧上报的链路状态（与 Python 端 SystemStatus 对齐）
enum class PerceptionStatus {
  kOk,
  kDegraded,
  kNoRoad,
  kNoDepth,
  kInputError,
  kTimeout,
  kDisconnected,
  kUnparsable,
};

// ---------- 枚举 <-> 字符串 ----------

const char* to_string(RiskLevel v);
const char* to_string(Passability v);
const char* to_string(Direction v);
const char* to_string(AlternativeStatus v);
const char* to_string(SystemStatus v);
const char* to_string(Intrusion v);
const char* to_string(DepthSource v);
const char* to_string(Oncoming v);
const char* to_string(RoadMaskStatus v);
const char* to_string(PerceptionStatus v);

bool parse_risk_level(const std::string& s, RiskLevel& out);
bool parse_intrusion(const std::string& s, Intrusion& out);
bool parse_oncoming(const std::string& s, Oncoming& out);
bool parse_perception_status(const std::string& s, PerceptionStatus& out);
bool parse_road_mask_status(const std::string& s, RoadMaskStatus& out);
bool parse_depth_source(const std::string& s, DepthSource& out);

// 严重度排序：SAFE < LOW < MEDIUM < HIGH < UNKNOWN（UNKNOWN 视为最不利）
int severity_rank(RiskLevel v);
RiskLevel worse_of(RiskLevel a, RiskLevel b);

// ---------- 结构体 ----------

struct Reason {
  std::string code;
  std::string message;
};

struct RockObservation {
  std::string rock_id;
  double confidence = 0.0;
  Intrusion intrusion = Intrusion::kUnknown;
  std::optional<double> road_overlap_ratio;
  std::optional<double> remaining_width_px;
  std::optional<double> remaining_width_m;
  std::optional<double> lateral_offset_norm;
  DepthSource depth_source = DepthSource::kNone;
  std::optional<double> distance_m;
  double depth_valid_ratio = 0.0;
};

// 感知模块输出的、与具体落石无关的环境信息。
// 缺失时一律按"不可确认"处理，绝不按"无对向车"处理。
struct EnvironmentInfo {
  bool present = false;
  std::optional<double> visibility_horizon_m;
  Oncoming oncoming = Oncoming::kUnknown;
};

struct PerceptionInput {
  std::string frame_id;
  int64_t timestamp_ns = 0;
  std::string source = "LIVE";

  PerceptionStatus status = PerceptionStatus::kUnparsable;
  bool road_detected = false;
  RoadMaskStatus road_mask_status = RoadMaskStatus::kFailed;
  double road_coverage_ratio = 0.0;
  std::optional<double> road_confidence;

  std::vector<RockObservation> rocks;

  bool depth_usable = false;
  std::vector<std::string> depth_reasons;

  bool combined_blocked = false;
  std::optional<double> combined_remaining_width_px;

  EnvironmentInfo environment;

  std::vector<Reason> perception_reasons;
};

struct DirectionAssessment {
  Direction direction = Direction::kAToB;
  RiskLevel risk = RiskLevel::kUnknown;
  Passability passability = Passability::kUnknown;
  std::optional<double> remaining_width_m;
  std::vector<std::string> limiting_rock_ids;
  std::vector<Reason> reasons;
};

struct AlternativePath {
  AlternativeStatus status = AlternativeStatus::kUnknown;
  std::optional<bool> available;  // 仅 AVAILABLE/UNAVAILABLE 时给出
  bool verifiable = false;
  std::optional<double> min_gap_m;
  Oncoming oncoming = Oncoming::kUnknown;
  std::vector<Reason> reasons;
};

struct DataQuality {
  int64_t frame_age_ms = 0;
  bool depth_usable = false;
  double road_coverage_ratio = 0.0;
  std::optional<double> min_rock_confidence;
  int rock_count = 0;
  bool degraded = false;
  std::vector<std::string> reasons;
};

struct RoadStatus {
  std::string schema_version = "1.0";
  std::string msg_type = "road_status";
  std::string road_id = "main";
  std::string frame_id;
  int64_t timestamp_ns = 0;
  RiskLevel overall_risk = RiskLevel::kUnknown;
  SystemStatus system_status = SystemStatus::kInputError;
  std::vector<DirectionAssessment> directions;
  AlternativePath alternative_path;
  DataQuality data_quality;
  std::vector<Reason> reasons;
  std::string direction_basis = "SHARED_CORRIDOR";
  std::string engine_name = "rock_risk";
  std::string engine_version = "0.1.0";
  std::string policy_version = "1";
  std::string disclaimer_version = "0.1";
};

}  // namespace rock_risk
