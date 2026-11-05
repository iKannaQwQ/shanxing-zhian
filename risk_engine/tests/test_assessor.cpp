// 风险评估核心规则单元测试。
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "rock_risk/assessor.hpp"
#include "rock_risk/policy.hpp"
#include "test_framework.hpp"

using namespace rock_risk;

namespace {

constexpr int64_t kTs = 1'700'000'000'000'000'000LL;  // ns
constexpr int64_t kNowMs = kTs / 1'000'000;           // 同刻，用于避免过期

PerceptionInput base_input() {
  PerceptionInput in;
  in.frame_id = "frame-001";
  in.timestamp_ns = kTs;
  in.status = PerceptionStatus::kOk;
  in.road_detected = true;
  in.road_mask_status = RoadMaskStatus::kOk;
  in.road_coverage_ratio = 0.20;
  in.depth_usable = true;
  return in;
}

RockObservation rock(const std::string& id, Intrusion intr, double conf = 0.9,
                     std::optional<double> remaining_m = std::nullopt) {
  RockObservation r;
  r.rock_id = id;
  r.confidence = conf;
  r.intrusion = intr;
  r.remaining_width_m = remaining_m;
  r.depth_source = DepthSource::kLidar;
  return r;
}

Policy policy() {
  Policy p;
  p.recompute();
  return p;
}

}  // namespace

// ---------- 单石分级 ----------

TEST(classify_none_is_low) {
  auto p = policy();
  std::vector<Reason> rs;
  CHECK(classify_rock(rock("R1", Intrusion::kNone), p, &rs) == RiskLevel::kLow);
  CHECK(!rs.empty());
}

TEST(classify_edge_is_low) {
  auto p = policy();
  CHECK(classify_rock(rock("R1", Intrusion::kEdge), p, nullptr) == RiskLevel::kLow);
}

TEST(classify_partial_is_medium) {
  auto p = policy();
  std::vector<Reason> rs;
  auto lvl = classify_rock(rock("R1", Intrusion::kPartial, 0.9, 0.15), p, &rs);
  CHECK(lvl == RiskLevel::kMedium);
}

TEST(classify_partial_narrow_is_high) {
  auto p = policy();
  std::vector<Reason> rs;
  auto lvl = classify_rock(rock("R1", Intrusion::kPartial, 0.9, 0.03), p, &rs);
  CHECK(lvl == RiskLevel::kHigh);
  bool found = false;
  for (const auto& r : rs) found = found || r.code == "REMAINING_WIDTH_BELOW_MIN";
  CHECK(found);
}

TEST(classify_full_is_high) {
  auto p = policy();
  CHECK(classify_rock(rock("R1", Intrusion::kFull), p, nullptr) == RiskLevel::kHigh);
}

TEST(classify_unknown_is_unknown) {
  auto p = policy();
  CHECK(classify_rock(rock("R1", Intrusion::kUnknown), p, nullptr) == RiskLevel::kUnknown);
}

TEST(passability_mapping) {
  CHECK(to_passability(RiskLevel::kSafe) == Passability::kPassable);
  CHECK(to_passability(RiskLevel::kLow) == Passability::kPassable);
  CHECK(to_passability(RiskLevel::kMedium) == Passability::kRestricted);
  CHECK(to_passability(RiskLevel::kHigh) == Passability::kBlocked);
  CHECK(to_passability(RiskLevel::kUnknown) == Passability::kUnknown);
}

// ---------- 质量门控 ----------

TEST(clean_frame_is_safe) {
  RiskAssessor a(policy());
  auto st = a.assess(base_input(), kNowMs);
  CHECK(st.overall_risk == RiskLevel::kSafe);
  CHECK(st.system_status == SystemStatus::kOk);
  CHECK(st.directions.size() == 2);
  CHECK(st.alternative_path.status == AlternativeStatus::kNotEvaluated);
}

TEST(missing_frame_id_denies) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.frame_id.clear();
  auto st = a.assess(in, kNowMs);
  CHECK(st.overall_risk == RiskLevel::kUnknown);
  CHECK(st.system_status == SystemStatus::kInputError);
  CHECK(st.directions.size() == 2);
  CHECK(st.directions[0].passability == Passability::kUnknown);
}

TEST(stale_frame_is_unknown) {
  RiskAssessor a(policy());
  auto st = a.assess(base_input(), kNowMs + 5000);
  CHECK(st.overall_risk == RiskLevel::kUnknown);
  CHECK(st.system_status == SystemStatus::kStale);
}

TEST(fresh_frame_within_window_ok) {
  RiskAssessor a(policy());
  auto st = a.assess(base_input(), kNowMs + 500);
  CHECK(st.overall_risk == RiskLevel::kSafe);
}

TEST(no_road_is_unknown) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.road_detected = false;
  in.road_mask_status = RoadMaskStatus::kEmpty;
  auto st = a.assess(in, kNowMs);
  CHECK(st.overall_risk == RiskLevel::kUnknown);
  CHECK(st.system_status == SystemStatus::kNoRoad);
  CHECK(st.alternative_path.status == AlternativeStatus::kUnknown);
}

TEST(road_too_small_is_unknown) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.road_coverage_ratio = 0.005;  // 低于 min_road_coverage
  auto st = a.assess(in, kNowMs);
  CHECK(st.overall_risk == RiskLevel::kUnknown);
  CHECK(st.system_status == SystemStatus::kNoRoad);
}

TEST(no_depth_no_rocks_cannot_be_safe) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.depth_usable = false;
  in.status = PerceptionStatus::kNoDepth;
  auto st = a.assess(in, kNowMs);
  CHECK(st.overall_risk == RiskLevel::kUnknown);
  CHECK(st.system_status == SystemStatus::kNoDepth);
}

TEST(no_depth_with_full_rock_still_high) {
  // 2D 证据足以证明"危险"，但不能证明"安全"
  RiskAssessor a(policy());
  auto in = base_input();
  in.depth_usable = false;
  in.status = PerceptionStatus::kNoDepth;
  in.rocks.push_back(rock("R1", Intrusion::kFull));
  auto st = a.assess(in, kNowMs);
  CHECK(st.overall_risk == RiskLevel::kHigh);
  CHECK(st.system_status == SystemStatus::kNoDepth);
  CHECK(st.data_quality.degraded);
}

TEST(perception_error_is_input_error) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.status = PerceptionStatus::kTimeout;
  auto st = a.assess(in, kNowMs);
  CHECK(st.overall_risk == RiskLevel::kUnknown);
  CHECK(st.system_status == SystemStatus::kInputError);
}

// ---------- 落石场景 ----------

TEST(rock_near_road_is_low) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.rocks.push_back(rock("R1", Intrusion::kNone));
  auto st = a.assess(in, kNowMs);
  CHECK(st.overall_risk == RiskLevel::kLow);
  CHECK(st.directions[0].passability == Passability::kPassable);
}

TEST(partial_rock_is_medium_restricted) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.rocks.push_back(rock("R1", Intrusion::kPartial, 0.9, 0.15));
  auto st = a.assess(in, kNowMs);
  CHECK(st.overall_risk == RiskLevel::kMedium);
  CHECK(st.directions[0].passability == Passability::kRestricted);
  CHECK(st.directions[1].passability == Passability::kRestricted);
  CHECK(st.directions[0].remaining_width_m.has_value());
  CHECK(st.directions[0].limiting_rock_ids.size() == 1);
}

TEST(full_rock_is_high_blocked) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.rocks.push_back(rock("R1", Intrusion::kFull));
  auto st = a.assess(in, kNowMs);
  CHECK(st.overall_risk == RiskLevel::kHigh);
  CHECK(st.directions[0].passability == Passability::kBlocked);
  CHECK(st.directions[1].passability == Passability::kBlocked);
}

TEST(multiple_rocks_take_worst) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.rocks.push_back(rock("R1", Intrusion::kNone));
  in.rocks.push_back(rock("R2", Intrusion::kPartial, 0.8, 0.2));
  in.rocks.push_back(rock("R3", Intrusion::kEdge));
  auto st = a.assess(in, kNowMs);
  CHECK(st.overall_risk == RiskLevel::kMedium);
  CHECK(st.directions[0].limiting_rock_ids.size() == 1);
  CHECK(st.directions[0].limiting_rock_ids[0] == "R2");
}

TEST(low_confidence_rock_never_safe) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.rocks.push_back(rock("R1", Intrusion::kNone, 0.10));  // 低于阈值
  auto st = a.assess(in, kNowMs);
  CHECK(st.overall_risk != RiskLevel::kSafe);
  CHECK(st.overall_risk == RiskLevel::kLow);
}

TEST(unknown_intrusion_caps_at_unknown) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.rocks.push_back(rock("R1", Intrusion::kUnknown));
  auto st = a.assess(in, kNowMs);
  CHECK(st.overall_risk == RiskLevel::kUnknown);
}

// ---------- 替代通行空间 ----------

TEST(alternative_not_evaluated_when_clear) {
  RiskAssessor a(policy());
  auto st = a.assess(base_input(), kNowMs);
  CHECK(st.alternative_path.status == AlternativeStatus::kNotEvaluated);
  CHECK(!st.alternative_path.available.has_value());
}

TEST(alternative_unknown_without_environment) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.rocks.push_back(rock("R1", Intrusion::kPartial, 0.9, 0.20));
  auto st = a.assess(in, kNowMs);
  CHECK(st.overall_risk == RiskLevel::kMedium);
  CHECK(st.alternative_path.status == AlternativeStatus::kUnknown);
  CHECK(!st.alternative_path.available.has_value());
  CHECK(!st.alternative_path.verifiable);
}

TEST(alternative_unavailable_when_gap_too_narrow) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.rocks.push_back(rock("R1", Intrusion::kPartial, 0.9, 0.02));
  in.environment.present = true;
  in.environment.oncoming = Oncoming::kConfirmedNoneVisible;
  in.environment.visibility_horizon_m = 1.5;
  auto st = a.assess(in, kNowMs);
  // 窄间隙会让单石直接升为 HIGH，替代路径因为间隙不足判 UNAVAILABLE
  CHECK(st.overall_risk == RiskLevel::kHigh);
  CHECK(st.alternative_path.status == AlternativeStatus::kUnavailable);
  CHECK(st.alternative_path.available.has_value());
  CHECK(*st.alternative_path.available == false);
}

TEST(alternative_unavailable_when_combined_blocked) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.rocks.push_back(rock("R1", Intrusion::kPartial, 0.9, 0.30));
  in.combined_blocked = true;
  in.environment.present = true;
  in.environment.oncoming = Oncoming::kConfirmedNoneVisible;
  in.environment.visibility_horizon_m = 1.5;
  auto st = a.assess(in, kNowMs);
  CHECK(st.alternative_path.status == AlternativeStatus::kUnavailable);
}

TEST(alternative_available_only_with_full_evidence) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.rocks.push_back(rock("R1", Intrusion::kPartial, 0.9, 0.30));
  in.environment.present = true;
  in.environment.oncoming = Oncoming::kConfirmedNoneVisible;
  in.environment.visibility_horizon_m = 1.5;
  auto st = a.assess(in, kNowMs);
  CHECK(st.overall_risk == RiskLevel::kMedium);
  CHECK(st.alternative_path.status == AlternativeStatus::kAvailable);
  CHECK(*st.alternative_path.available == true);
  CHECK(st.alternative_path.verifiable);
}

TEST(alternative_blocked_by_oncoming_traffic) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.rocks.push_back(rock("R1", Intrusion::kPartial, 0.9, 0.30));
  in.environment.present = true;
  in.environment.oncoming = Oncoming::kPresent;
  in.environment.visibility_horizon_m = 1.5;
  auto st = a.assess(in, kNowMs);
  CHECK(st.alternative_path.status == AlternativeStatus::kUnavailable);
}

TEST(alternative_unknown_when_visibility_short) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.rocks.push_back(rock("R1", Intrusion::kPartial, 0.9, 0.30));
  in.environment.present = true;
  in.environment.oncoming = Oncoming::kConfirmedNoneVisible;
  in.environment.visibility_horizon_m = 0.2;  // 低于 required_lookahead
  auto st = a.assess(in, kNowMs);
  CHECK(st.alternative_path.status == AlternativeStatus::kUnknown);
}

TEST(alternative_unknown_when_no_depth) {
  RiskAssessor a(policy());
  auto in = base_input();
  in.depth_usable = false;
  in.status = PerceptionStatus::kNoDepth;
  in.rocks.push_back(rock("R1", Intrusion::kPartial, 0.9, 0.30));
  in.environment.present = true;
  in.environment.oncoming = Oncoming::kConfirmedNoneVisible;
  in.environment.visibility_horizon_m = 1.5;
  auto st = a.assess(in, kNowMs);
  CHECK(st.alternative_path.status == AlternativeStatus::kUnknown);
}

TEST(alternative_policy_can_disable) {
  Policy p = policy();
  p.allow_alternative = false;
  RiskAssessor a(p);
  auto in = base_input();
  in.rocks.push_back(rock("R1", Intrusion::kPartial, 0.9, 0.30));
  auto st = a.assess(in, kNowMs);
  CHECK(st.alternative_path.status == AlternativeStatus::kNotEvaluated);
}

// ---------- 严重度聚合 ----------

TEST(severity_ordering) {
  CHECK(severity_rank(RiskLevel::kSafe) < severity_rank(RiskLevel::kLow));
  CHECK(severity_rank(RiskLevel::kLow) < severity_rank(RiskLevel::kMedium));
  CHECK(severity_rank(RiskLevel::kMedium) < severity_rank(RiskLevel::kHigh));
  CHECK(severity_rank(RiskLevel::kHigh) < severity_rank(RiskLevel::kUnknown));
  CHECK(worse_of(RiskLevel::kMedium, RiskLevel::kHigh) == RiskLevel::kHigh);
  CHECK(worse_of(RiskLevel::kUnknown, RiskLevel::kHigh) == RiskLevel::kUnknown);
}

TEST(policy_validation) {
  Policy p;
  std::string err;
  CHECK(p.valid(&err));
  p.vehicle_width_m = -1.0;
  CHECK(!p.valid(&err));
}

TEST(alternative_definitive_beats_unverifiable) {
  // 间隙明确过窄 -> 即便对向不可确认，也应判 UNAVAILABLE 而不是 UNKNOWN
  RiskAssessor a(policy());
  auto in = base_input();
  in.rocks.push_back(rock("R1", Intrusion::kPartial, 0.9, 0.02));
  auto st = a.assess(in, kNowMs);
  CHECK(st.alternative_path.status == AlternativeStatus::kUnavailable);
  CHECK(st.alternative_path.available.has_value());
  CHECK(*st.alternative_path.available == false);
  CHECK(st.alternative_path.verifiable);
}
