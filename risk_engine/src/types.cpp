#include "rock_risk/types.hpp"

#include <algorithm>
#include <string>

namespace rock_risk {

const char* to_string(RiskLevel v) {
  switch (v) {
    case RiskLevel::kSafe: return "SAFE";
    case RiskLevel::kLow: return "LOW";
    case RiskLevel::kMedium: return "MEDIUM";
    case RiskLevel::kHigh: return "HIGH";
    case RiskLevel::kUnknown: return "UNKNOWN";
  }
  return "UNKNOWN";
}

const char* to_string(Passability v) {
  switch (v) {
    case Passability::kPassable: return "PASSABLE";
    case Passability::kRestricted: return "RESTRICTED";
    case Passability::kBlocked: return "BLOCKED";
    case Passability::kUnknown: return "UNKNOWN";
  }
  return "UNKNOWN";
}

const char* to_string(Direction v) {
  return v == Direction::kAToB ? "A_TO_B" : "B_TO_A";
}

const char* to_string(AlternativeStatus v) {
  switch (v) {
    case AlternativeStatus::kAvailable: return "AVAILABLE";
    case AlternativeStatus::kUnavailable: return "UNAVAILABLE";
    case AlternativeStatus::kUnknown: return "UNKNOWN";
    case AlternativeStatus::kNotEvaluated: return "NOT_EVALUATED";
  }
  return "UNKNOWN";
}

const char* to_string(SystemStatus v) {
  switch (v) {
    case SystemStatus::kOk: return "OK";
    case SystemStatus::kDegraded: return "DEGRADED";
    case SystemStatus::kStale: return "STALE";
    case SystemStatus::kNoRoad: return "NO_ROAD";
    case SystemStatus::kNoDepth: return "NO_DEPTH";
    case SystemStatus::kInputError: return "INPUT_ERROR";
  }
  return "INPUT_ERROR";
}

const char* to_string(Intrusion v) {
  switch (v) {
    case Intrusion::kNone: return "NONE";
    case Intrusion::kEdge: return "EDGE";
    case Intrusion::kPartial: return "PARTIAL";
    case Intrusion::kFull: return "FULL";
    case Intrusion::kUnknown: return "UNKNOWN";
  }
  return "UNKNOWN";
}

const char* to_string(DepthSource v) {
  switch (v) {
    case DepthSource::kNone: return "NONE";
    case DepthSource::kLidar: return "LIDAR";
    case DepthSource::kMonocular: return "MONOCULAR";
  }
  return "NONE";
}

const char* to_string(Oncoming v) {
  switch (v) {
    case Oncoming::kPresent: return "PRESENT";
    case Oncoming::kConfirmedNoneVisible: return "CONFIRMED_NONE_VISIBLE";
    case Oncoming::kUnverifiable: return "UNVERIFIABLE";
    case Oncoming::kUnknown: return "UNKNOWN";
  }
  return "UNKNOWN";
}

const char* to_string(RoadMaskStatus v) {
  switch (v) {
    case RoadMaskStatus::kOk: return "OK";
    case RoadMaskStatus::kEmpty: return "EMPTY";
    case RoadMaskStatus::kTooSmall: return "TOO_SMALL";
    case RoadMaskStatus::kFragmented: return "FRAGMENTED";
    case RoadMaskStatus::kLowConfidence: return "LOW_CONFIDENCE";
    case RoadMaskStatus::kFailed: return "FAILED";
  }
  return "FAILED";
}

const char* to_string(PerceptionStatus v) {
  switch (v) {
    case PerceptionStatus::kOk: return "OK";
    case PerceptionStatus::kDegraded: return "DEGRADED";
    case PerceptionStatus::kNoRoad: return "NO_ROAD";
    case PerceptionStatus::kNoDepth: return "NO_DEPTH";
    case PerceptionStatus::kInputError: return "INPUT_ERROR";
    case PerceptionStatus::kTimeout: return "TIMEOUT";
    case PerceptionStatus::kDisconnected: return "DISCONNECTED";
    case PerceptionStatus::kUnparsable: return "UNPARSABLE";
  }
  return "UNPARSABLE";
}

bool parse_risk_level(const std::string& s, RiskLevel& out) {
  if (s == "SAFE") { out = RiskLevel::kSafe; return true; }
  if (s == "LOW") { out = RiskLevel::kLow; return true; }
  if (s == "MEDIUM") { out = RiskLevel::kMedium; return true; }
  if (s == "HIGH") { out = RiskLevel::kHigh; return true; }
  if (s == "UNKNOWN") { out = RiskLevel::kUnknown; return true; }
  return false;
}

bool parse_intrusion(const std::string& s, Intrusion& out) {
  if (s == "NONE") { out = Intrusion::kNone; return true; }
  if (s == "EDGE") { out = Intrusion::kEdge; return true; }
  if (s == "PARTIAL") { out = Intrusion::kPartial; return true; }
  if (s == "FULL") { out = Intrusion::kFull; return true; }
  if (s == "UNKNOWN") { out = Intrusion::kUnknown; return true; }
  return false;
}

bool parse_oncoming(const std::string& s, Oncoming& out) {
  if (s == "PRESENT") { out = Oncoming::kPresent; return true; }
  if (s == "CONFIRMED_NONE_VISIBLE") { out = Oncoming::kConfirmedNoneVisible; return true; }
  if (s == "UNVERIFIABLE") { out = Oncoming::kUnverifiable; return true; }
  if (s == "UNKNOWN") { out = Oncoming::kUnknown; return true; }
  return false;
}

bool parse_perception_status(const std::string& s, PerceptionStatus& out) {
  if (s == "OK") { out = PerceptionStatus::kOk; return true; }
  if (s == "DEGRADED") { out = PerceptionStatus::kDegraded; return true; }
  if (s == "NO_ROAD") { out = PerceptionStatus::kNoRoad; return true; }
  if (s == "NO_DEPTH") { out = PerceptionStatus::kNoDepth; return true; }
  if (s == "INPUT_ERROR") { out = PerceptionStatus::kInputError; return true; }
  if (s == "TIMEOUT") { out = PerceptionStatus::kTimeout; return true; }
  if (s == "DISCONNECTED") { out = PerceptionStatus::kDisconnected; return true; }
  return false;
}

bool parse_road_mask_status(const std::string& s, RoadMaskStatus& out) {
  if (s == "OK") { out = RoadMaskStatus::kOk; return true; }
  if (s == "EMPTY") { out = RoadMaskStatus::kEmpty; return true; }
  if (s == "TOO_SMALL") { out = RoadMaskStatus::kTooSmall; return true; }
  if (s == "FRAGMENTED") { out = RoadMaskStatus::kFragmented; return true; }
  if (s == "LOW_CONFIDENCE") { out = RoadMaskStatus::kLowConfidence; return true; }
  if (s == "FAILED") { out = RoadMaskStatus::kFailed; return true; }
  return false;
}

bool parse_depth_source(const std::string& s, DepthSource& out) {
  if (s == "NONE") { out = DepthSource::kNone; return true; }
  if (s == "LIDAR") { out = DepthSource::kLidar; return true; }
  if (s == "MONOCULAR") { out = DepthSource::kMonocular; return true; }
  return false;
}

int severity_rank(RiskLevel v) {
  switch (v) {
    case RiskLevel::kSafe: return 0;
    case RiskLevel::kLow: return 1;
    case RiskLevel::kMedium: return 2;
    case RiskLevel::kHigh: return 3;
    case RiskLevel::kUnknown: return 4;
  }
  return 4;
}

RiskLevel worse_of(RiskLevel a, RiskLevel b) {
  return severity_rank(a) >= severity_rank(b) ? a : b;
}

}  // namespace rock_risk
