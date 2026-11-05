#include "rock_risk/assessor.hpp"

#include <algorithm>
#include <limits>
#include <optional>
#include <string>
#include <vector>

namespace rock_risk {
namespace {

Reason mk(const char* code, const std::string& message) { return Reason{code, message}; }

int intrusion_rank(Intrusion v) {
  switch (v) {
    case Intrusion::kNone: return 0;
    case Intrusion::kEdge: return 1;
    case Intrusion::kPartial: return 2;
    case Intrusion::kFull: return 3;
    case Intrusion::kUnknown: return 4;
  }
  return 4;
}

DirectionAssessment make_direction(Direction dir, RiskLevel risk,
                                   std::optional<double> remaining_m,
                                   std::vector<std::string> limiting,
                                   std::vector<Reason> reasons) {
  DirectionAssessment d;
  d.direction = dir;
  d.risk = risk;
  d.passability = to_passability(risk);
  d.remaining_width_m = remaining_m;
  d.limiting_rock_ids = std::move(limiting);
  d.reasons = std::move(reasons);
  return d;
}

void fill_empty_directions(RoadStatus& out, RiskLevel risk, std::vector<Reason> reasons) {
  out.directions.clear();
  out.directions.push_back(make_direction(Direction::kAToB, risk, std::nullopt, {}, reasons));
  out.directions.push_back(make_direction(Direction::kBToA, risk, std::nullopt, {}, reasons));
}

}  // namespace

Passability to_passability(RiskLevel risk) {
  switch (risk) {
    case RiskLevel::kSafe:
    case RiskLevel::kLow:
      return Passability::kPassable;
    case RiskLevel::kMedium:
      return Passability::kRestricted;
    case RiskLevel::kHigh:
      return Passability::kBlocked;
    case RiskLevel::kUnknown:
      return Passability::kUnknown;
  }
  return Passability::kUnknown;
}

RiskLevel classify_rock(const RockObservation& rock, const Policy& policy,
                        std::vector<Reason>* reasons) {
  switch (rock.intrusion) {
    case Intrusion::kFull:
      if (reasons) reasons->push_back(mk("ROCK_FULL_INTRUSION",
                                         "落石完全占据通行区域"));
      return RiskLevel::kHigh;

    case Intrusion::kPartial: {
      bool narrow = rock.remaining_width_m.has_value() &&
                    *rock.remaining_width_m < policy.min_pass_width_m;
      if (narrow) {
        if (reasons) {
          reasons->push_back(mk("REMAINING_WIDTH_BELOW_MIN",
                                "剩余通行宽度小于最小可通行宽度"));
        }
        return RiskLevel::kHigh;
      }
      if (reasons) reasons->push_back(mk("ROCK_PARTIAL_INTRUSION", "落石部分侵入道路"));
      return RiskLevel::kMedium;
    }

    case Intrusion::kEdge:
      if (reasons) reasons->push_back(mk("ROCK_EDGE_INTRUSION", "落石擦到道路边缘"));
      return RiskLevel::kLow;

    case Intrusion::kNone:
      if (reasons) reasons->push_back(mk("ROCK_NEAR_ROAD_NO_INTRUSION",
                                         "落石在道路附近，未发现明显侵入"));
      return RiskLevel::kLow;

    case Intrusion::kUnknown:
      if (reasons) reasons->push_back(mk("ROCK_INTRUSION_UNCLASSIFIED",
                                         "落石侵入关系无法判定"));
      return RiskLevel::kUnknown;
  }
  return RiskLevel::kUnknown;
}

RiskAssessor::RiskAssessor(Policy policy) : policy_(policy) {}

RoadStatus RiskAssessor::assess(const PerceptionInput& input, int64_t now_ms) const {
  RoadStatus out;
  out.frame_id = input.frame_id;
  out.timestamp_ns = input.timestamp_ns;
  out.policy_version = std::to_string(policy_.policy_version);
  out.reasons = input.perception_reasons;

  const int64_t ts_ms = input.timestamp_ns / 1000000;
  const int64_t age = (now_ms > 0 && ts_ms > 0) ? (now_ms - ts_ms) : 0;
  out.data_quality.frame_age_ms = age;
  out.data_quality.depth_usable = input.depth_usable;
  out.data_quality.road_coverage_ratio = input.road_coverage_ratio;
  out.data_quality.rock_count = static_cast<int>(input.rocks.size());
  out.data_quality.reasons = input.depth_reasons;

  // ---- 1. 输入有效性 ----
  if (input.frame_id.empty()) {
    out.system_status = SystemStatus::kInputError;
    out.overall_risk = RiskLevel::kUnknown;
    out.reasons.push_back(mk("INPUT_FRAME_ID_MISSING", "缺少 frame_id"));
    fill_empty_directions(out, RiskLevel::kUnknown,
                          {mk("POLICY_DEFAULT_DENY", "输入不合法，拒绝给出通行结论")});
    out.alternative_path.status = AlternativeStatus::kUnknown;
    out.data_quality.degraded = true;
    return out;
  }

  if (input.status == PerceptionStatus::kInputError ||
      input.status == PerceptionStatus::kTimeout ||
      input.status == PerceptionStatus::kDisconnected ||
      input.status == PerceptionStatus::kUnparsable) {
    out.system_status = SystemStatus::kInputError;
    out.overall_risk = RiskLevel::kUnknown;
    out.reasons.push_back(mk("PERCEPTION_INPUT_ERROR", "感知链路异常，无法评估"));
    fill_empty_directions(out, RiskLevel::kUnknown,
                          {mk("POLICY_DEFAULT_DENY", "感知输入异常")});
    out.alternative_path.status = AlternativeStatus::kUnknown;
    out.data_quality.degraded = true;
    return out;
  }

  // ---- 2. 数据新鲜度 ----
  if (now_ms > 0 && ts_ms > 0 && age > policy_.max_frame_age_ms) {
    out.system_status = SystemStatus::kStale;
    out.overall_risk = RiskLevel::kUnknown;
    out.reasons.push_back(mk("FRAME_STALE",
                             "数据过期 " + std::to_string(age) + " ms，超过阈值 " +
                                 std::to_string(policy_.max_frame_age_ms) + " ms"));
    fill_empty_directions(out, RiskLevel::kUnknown,
                          {mk("POLICY_DEFAULT_DENY", "数据过期")});
    out.alternative_path.status = AlternativeStatus::kUnknown;
    out.data_quality.degraded = true;
    return out;
  }

  // ---- 3. 道路可用性 ----
  const bool road_ok = input.road_detected &&
                       input.road_mask_status == RoadMaskStatus::kOk &&
                       input.road_coverage_ratio >= policy_.min_road_coverage;
  if (!road_ok) {
    out.system_status = SystemStatus::kNoRoad;
    out.overall_risk = RiskLevel::kUnknown;
    out.reasons.push_back(mk("ROAD_NOT_DETECTED", "道路区域不可用"));
    fill_empty_directions(out, RiskLevel::kUnknown,
                          {mk("ROAD_UNKNOWN", "道路不可用，落石关系无法判定")});
    out.alternative_path.status = AlternativeStatus::kUnknown;
    out.alternative_path.verifiable = false;
    out.data_quality.degraded = true;
    return out;
  }

  // ---- 4. 深度可用性（影响"能否宣称安全"，不阻止"发现危险"）----
  const bool depth_ok = input.depth_usable && input.status != PerceptionStatus::kNoDepth;

  // ---- 5. 有效落石筛选 ----
  std::vector<RockObservation> valid;
  valid.reserve(input.rocks.size());
  int ignored_low_conf = 0;
  double min_conf = std::numeric_limits<double>::infinity();
  for (const auto& r : input.rocks) {
    min_conf = std::min(min_conf, r.confidence);
    if (r.confidence < policy_.min_rock_confidence) {
      ++ignored_low_conf;
      continue;
    }
    valid.push_back(r);
  }
  if (!input.rocks.empty()) {
    out.data_quality.min_rock_confidence = min_conf;
  } else {
    out.data_quality.min_rock_confidence = std::nullopt;
  }

  // ---- 6. 逐石分级 ----
  RiskLevel rock_risk = RiskLevel::kSafe;
  std::vector<Reason> rock_reasons;
  std::vector<std::string> limiting;
  std::optional<double> min_gap_m;

  for (const auto& r : valid) {
    std::vector<Reason> rr;
    RiskLevel lvl = classify_rock(r, policy_, &rr);
    if (intrusion_rank(r.intrusion) >= intrusion_rank(Intrusion::kPartial)) {
      limiting.push_back(r.rock_id);
      if (r.remaining_width_m.has_value()) {
        if (!min_gap_m.has_value() || *r.remaining_width_m < *min_gap_m) {
          min_gap_m = r.remaining_width_m;
        }
      }
    }
    rock_risk = worse_of(rock_risk, lvl);
    rock_reasons.insert(rock_reasons.end(), rr.begin(), rr.end());
  }

  if (ignored_low_conf > 0) {
    rock_reasons.push_back(mk("ROCK_LOW_CONFIDENCE_IGNORED",
                              "有 " + std::to_string(ignored_low_conf) +
                                  " 个低置信度目标未参与判定"));
    // 看见了可疑目标就不能宣称 SAFE
    if (rock_risk == RiskLevel::kSafe) rock_risk = RiskLevel::kLow;
  }

  // ---- 7. 质量门控：只有证据充分才允许 SAFE ----
  const bool quality_ok = depth_ok && input.status == PerceptionStatus::kOk;
  RiskLevel final_risk = rock_risk;
  if (input.rocks.empty()) {
    if (quality_ok) {
      final_risk = RiskLevel::kSafe;
      rock_reasons.push_back(mk("NO_INTRUSION_DETECTED",
                                "未发现侵入道路的落石（受观测范围限制）"));
    } else {
      final_risk = RiskLevel::kUnknown;
      rock_reasons.push_back(mk("QUALITY_INSUFFICIENT_FOR_SAFE",
                                "数据质量不足，无法宣称无侵入"));
    }
  } else if (rock_risk == RiskLevel::kLow && !quality_ok) {
    // 有落石、无侵入，但质量不足：保留 LOW（有正向观测），不升级为 SAFE
    final_risk = RiskLevel::kLow;
    rock_reasons.push_back(mk("DEGRADED_OBSERVATION",
                              "观测质量下降，结论置信度受限"));
  }

  // ---- 8. 系统状态 ----
  if (!depth_ok) {
    out.system_status = SystemStatus::kNoDepth;
  } else if (input.status == PerceptionStatus::kDegraded ||
             input.road_mask_status == RoadMaskStatus::kLowConfidence ||
             input.road_mask_status == RoadMaskStatus::kFragmented) {
    out.system_status = SystemStatus::kDegraded;
  } else {
    out.system_status = SystemStatus::kOk;
  }
  out.data_quality.degraded = (out.system_status != SystemStatus::kOk);

  if (!depth_ok) {
    out.reasons.push_back(mk("DEPTH_UNUSABLE", "深度数据不可用，米制结论受限"));
  }

  out.overall_risk = final_risk;
  fill_empty_directions(out, final_risk, rock_reasons);
  for (auto& d : out.directions) {
    d.remaining_width_m = min_gap_m;
    d.limiting_rock_ids = limiting;
  }

  // ---- 9. 替代通行空间 ----
  AlternativePath& ap = out.alternative_path;
  ap.oncoming = input.environment.oncoming;

  if (final_risk == RiskLevel::kSafe || final_risk == RiskLevel::kLow) {
    ap.status = AlternativeStatus::kNotEvaluated;
    ap.available = std::nullopt;
    ap.verifiable = true;
    ap.reasons.push_back(mk("ALTERNATIVE_NOT_REQUIRED", "当前无需替代通行空间"));
  } else if (final_risk == RiskLevel::kUnknown) {
    ap.status = AlternativeStatus::kUnknown;
    ap.available = std::nullopt;
    ap.verifiable = false;
    ap.reasons.push_back(mk("POLICY_DEFAULT_DENY", "整体状态未知，不评估替代路径"));
  } else if (!policy_.allow_alternative) {
    ap.status = AlternativeStatus::kNotEvaluated;
    ap.available = std::nullopt;
    ap.verifiable = false;
    ap.reasons.push_back(mk("ALTERNATIVE_POLICY_DISABLED", "替代通行评估已被策略关闭"));
  } else {
    // 需要替代通行空间：先看能否"确定不可用"，再看是否"无法确认"。
    // 顺序很重要：间隙明确过窄时，即便对向不可知也已经是不可用，
    // 不应因为额外的不确定性把确定结论降级为 UNKNOWN。
    ap.min_gap_m = min_gap_m;
    bool definitive_unavailable = false;
    bool unverifiable = false;

    if (!depth_ok) {
      ap.reasons.push_back(mk("ALTERNATIVE_NO_DEPTH", "缺少深度，无法确认间隙的米制宽度"));
      unverifiable = true;
    }

    if (input.combined_blocked) {
      ap.reasons.push_back(mk("ALTERNATIVE_BLOCKED", "多块落石联合阻断，无可通行间隙"));
      definitive_unavailable = true;
    } else if (depth_ok && min_gap_m.has_value() &&
               *min_gap_m < policy_.min_pass_width_m + policy_.alternative_margin_m) {
      ap.reasons.push_back(mk("ALTERNATIVE_GAP_TOO_NARROW", "剩余间隙小于最小可通行宽度"));
      definitive_unavailable = true;
    }

    if (input.environment.oncoming == Oncoming::kPresent) {
      ap.reasons.push_back(mk("ALTERNATIVE_ONCOMING_PRESENT", "检测到对向交通"));
      definitive_unavailable = true;
    } else if (input.environment.oncoming != Oncoming::kConfirmedNoneVisible) {
      ap.reasons.push_back(mk("ALTERNATIVE_BLIND_CURVE",
                              "对向交通不可确认（盲弯或缺少该路信息）"));
      unverifiable = true;
    }

    if (!input.environment.visibility_horizon_m.has_value()) {
      ap.reasons.push_back(mk("ALTERNATIVE_VISIBILITY_UNKNOWN", "缺少可视视距信息"));
      unverifiable = true;
    } else if (*input.environment.visibility_horizon_m < policy_.required_lookahead_m) {
      ap.reasons.push_back(mk("ALTERNATIVE_VISIBILITY_TOO_SHORT",
                              "可视视距不足以前视确认"));
      unverifiable = true;
    }

    if (definitive_unavailable) {
      ap.status = AlternativeStatus::kUnavailable;
      ap.available = false;
      ap.verifiable = true;   // 不可用这一结论本身是被证实的
    } else if (unverifiable) {
      ap.status = AlternativeStatus::kUnknown;
      ap.available = std::nullopt;
      ap.verifiable = false;
    } else {
      ap.status = AlternativeStatus::kAvailable;
      ap.available = true;
      ap.verifiable = true;
      ap.reasons.push_back(mk("ALTERNATIVE_AVAILABLE",
                              "间隙充足、对向已确认、视距满足"));
    }
  }

  return out;
}

}  // namespace rock_risk
