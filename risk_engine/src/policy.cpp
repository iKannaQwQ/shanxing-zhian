#include "rock_risk/policy.hpp"

namespace rock_risk {

void Policy::recompute() {
  min_pass_width_m = vehicle_width_m + 2.0 * safety_margin_m;
}

bool Policy::valid(std::string* error) const {
  auto fail = [&](const char* msg) {
    if (error != nullptr) *error = msg;
    return false;
  };
  if (!(vehicle_width_m > 0.0)) return fail("vehicle_width_m 必须为正");
  if (safety_margin_m < 0.0) return fail("safety_margin_m 不能为负");
  if (!(min_pass_width_m > 0.0)) return fail("min_pass_width_m 必须为正");
  if (min_rock_confidence < 0.0 || min_rock_confidence > 1.0)
    return fail("min_rock_confidence 必须在 [0,1]");
  if (min_road_coverage < 0.0 || min_road_coverage > 1.0)
    return fail("min_road_coverage 必须在 [0,1]");
  if (max_frame_age_ms <= 0) return fail("max_frame_age_ms 必须为正");
  if (edge_overlap_max < 0.0 || edge_overlap_max > 1.0)
    return fail("edge_overlap_max 必须在 [0,1]");
  if (full_overlap_min < edge_overlap_max)
    return fail("full_overlap_min 不得小于 edge_overlap_max");
  if (required_lookahead_m < 0.0) return fail("required_lookahead_m 不能为负");
  return true;
}

}  // namespace rock_risk
