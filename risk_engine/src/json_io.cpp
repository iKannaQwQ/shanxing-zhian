#include "rock_risk/json_io.hpp"

#include <string>
#include <vector>

#include "nlohmann/json.hpp"

namespace rock_risk {
namespace {

using nlohmann::json;

bool get_number(const json& obj, const char* key, double& out) {
  auto it = obj.find(key);
  if (it == obj.end() || it->is_null() || !it->is_number()) return false;
  out = it->get<double>();
  return true;
}

std::optional<double> opt_number(const json& obj, const char* key) {
  double v = 0.0;
  if (get_number(obj, key, v)) return v;
  return std::nullopt;
}

std::string get_string(const json& obj, const char* key, const std::string& fallback = "") {
  auto it = obj.find(key);
  if (it == obj.end() || it->is_null() || !it->is_string()) return fallback;
  return it->get<std::string>();
}

json opt_to_json(const std::optional<double>& v) {
  if (!v.has_value()) return nullptr;
  return *v;
}

json reasons_to_json(const std::vector<Reason>& reasons) {
  json arr = json::array();
  for (const auto& r : reasons) {
    arr.push_back({{"code", r.code}, {"message", r.message}});
  }
  return arr;
}

}  // namespace

ParseOutcome parse_perception_json(const std::string& text) {
  ParseOutcome outcome;
  json doc;
  try {
    doc = json::parse(text);
  } catch (const std::exception& e) {
    outcome.error_code = "JSON_PARSE_ERROR";
    outcome.error_message = std::string("JSON 解析失败: ") + e.what();
    return outcome;
  }

  if (!doc.is_object()) {
    outcome.error_code = "JSON_NOT_OBJECT";
    outcome.error_message = "顶层必须是 JSON 对象";
    return outcome;
  }

  PerceptionInput in;

  // 先取 frame_id / timestamp，便于错误场景仍能对齐
  in.frame_id = get_string(doc, "frame_id");
  auto ts_it = doc.find("timestamp_ns");
  if (ts_it != doc.end() && ts_it->is_number_integer()) {
    in.timestamp_ns = ts_it->get<int64_t>();
  }
  outcome.value = in;
  outcome.value.status = PerceptionStatus::kUnparsable;

  // --- 必填字段 ---
  auto fail = [&](const char* code, const std::string& msg) {
    outcome.ok = false;
    outcome.error_code = code;
    outcome.error_message = msg;
    return outcome;
  };

  if (in.frame_id.empty()) return fail("MISSING_FRAME_ID", "缺少 frame_id");
  if (in.timestamp_ns <= 0) return fail("MISSING_TIMESTAMP", "缺少 timestamp_ns");

  auto status_it = doc.find("system_status");
  if (status_it == doc.end() || !status_it->is_string()) {
    return fail("MISSING_SYSTEM_STATUS", "缺少 system_status");
  }
  if (!parse_perception_status(status_it->get<std::string>(), in.status)) {
    return fail("UNKNOWN_SYSTEM_STATUS",
                "未知 system_status: " + status_it->get<std::string>());
  }

  auto road_it = doc.find("road_detected");
  if (road_it == doc.end() || !road_it->is_boolean()) {
    return fail("MISSING_ROAD_DETECTED", "缺少 road_detected");
  }
  in.road_detected = road_it->get<bool>();

  auto mask_it = doc.find("road_mask_status");
  if (mask_it == doc.end() || !mask_it->is_string() ||
      !parse_road_mask_status(mask_it->get<std::string>(), in.road_mask_status)) {
    return fail("BAD_ROAD_MASK_STATUS", "road_mask_status 缺失或非法");
  }

  auto road = doc.find("road");
  if (road != doc.end() && road->is_object()) {
    in.road_coverage_ratio = opt_number(*road, "coverage_ratio").value_or(0.0);
    in.road_confidence = opt_number(*road, "confidence");
  }

  // --- 深度 ---
  auto depth = doc.find("depth");
  if (depth != doc.end() && depth->is_object()) {
    auto usable = depth->find("usable");
    if (usable != depth->end() && usable->is_boolean()) {
      in.depth_usable = usable->get<bool>();
    }
    auto rs = depth->find("reasons");
    if (rs != depth->end() && rs->is_array()) {
      for (const auto& r : *rs) {
        if (r.is_string()) in.depth_reasons.push_back(r.get<std::string>());
      }
    }
  }

  // --- 落石 ---
  auto rocks = doc.find("rockfalls");
  if (rocks == doc.end() || !rocks->is_array()) {
    return fail("MISSING_ROCKFALLS", "缺少 rockfalls 数组");
  }
  for (const auto& r : *rocks) {
    if (!r.is_object()) continue;
    RockObservation rock;
    rock.rock_id = get_string(r, "rock_id", "unknown");
    rock.confidence = opt_number(r, "confidence").value_or(0.0);
    rock.road_overlap_ratio = opt_number(r, "road_overlap_ratio");
    rock.remaining_width_px = opt_number(r, "remaining_width_px");
    rock.remaining_width_m = opt_number(r, "remaining_width_m");
    rock.lateral_offset_norm = opt_number(r, "lateral_offset_norm");

    auto intr = r.find("intrusion");
    if (intr != r.end() && intr->is_string()) {
      parse_intrusion(intr->get<std::string>(), rock.intrusion);
    }

    auto dep = r.find("depth_estimate");
    if (dep != r.end() && dep->is_object()) {
      auto src = dep->find("source");
      if (src != dep->end() && src->is_string()) {
        parse_depth_source(src->get<std::string>(), rock.depth_source);
      }
      rock.distance_m = opt_number(*dep, "distance_m");
      rock.depth_valid_ratio = opt_number(*dep, "valid_ratio").value_or(0.0);
    }
    in.rocks.push_back(rock);
  }

  // --- 联合阻断（可选）---
  auto metrics = doc.find("metrics");
  if (metrics != doc.end() && metrics->is_object()) {
    auto cb = metrics->find("combined_blocked");
    if (cb != metrics->end() && cb->is_boolean()) {
      in.combined_blocked = cb->get<bool>();
    }
    in.combined_remaining_width_px = opt_number(*metrics, "combined_remaining_width_px");
  }

  // --- 环境信息（可选；缺失即"不可确认"）---
  auto env = doc.find("environment");
  if (env != doc.end() && env->is_object()) {
    in.environment.present = true;
    in.environment.visibility_horizon_m = opt_number(*env, "visibility_horizon_m");
    auto oc = env->find("oncoming_traffic");
    if (oc != env->end() && oc->is_string()) {
      parse_oncoming(oc->get<std::string>(), in.environment.oncoming);
    }
  }

  auto rs = doc.find("reasons");
  if (rs != doc.end() && rs->is_array()) {
    for (const auto& r : *rs) {
      if (!r.is_object()) continue;
      in.perception_reasons.push_back(
          Reason{get_string(r, "code", "PERCEPTION_REASON"),
                 get_string(r, "message", "")});
    }
  }

  outcome.ok = true;
  outcome.value = in;
  return outcome;
}

std::string serialize_road_status(const RoadStatus& s, bool pretty) {
  json doc;
  doc["schema_version"] = s.schema_version;
  doc["msg_type"] = s.msg_type;
  doc["road_id"] = s.road_id;
  doc["frame_id"] = s.frame_id;
  doc["timestamp_ns"] = s.timestamp_ns;
  doc["overall_risk"] = to_string(s.overall_risk);
  doc["system_status"] = to_string(s.system_status);
  doc["direction_basis"] = s.direction_basis;

  json dirs = json::array();
  for (const auto& d : s.directions) {
    json dj;
    dj["direction"] = to_string(d.direction);
    dj["risk"] = to_string(d.risk);
    dj["passability"] = to_string(d.passability);
    dj["remaining_width_m"] = opt_to_json(d.remaining_width_m);
    dj["limiting_rock_ids"] = d.limiting_rock_ids;
    dj["reasons"] = reasons_to_json(d.reasons);
    dirs.push_back(std::move(dj));
  }
  doc["directions"] = dirs;

  // 汇总视图，方便前端直接使用
  json pass = json::object();
  for (const auto& d : s.directions) {
    pass[to_string(d.direction)] = to_string(d.passability);
  }
  doc["passability"] = pass;

  const AlternativePath& ap = s.alternative_path;
  doc["alternative_path"] = {
      {"status", to_string(ap.status)},
      {"available", ap.available.has_value() ? json(*ap.available) : json(nullptr)},
      {"verifiable", ap.verifiable},
      {"min_gap_m", opt_to_json(ap.min_gap_m)},
      {"oncoming", to_string(ap.oncoming)},
      {"reasons", reasons_to_json(ap.reasons)},
  };

  doc["data_quality"] = {
      {"frame_age_ms", s.data_quality.frame_age_ms},
      {"depth_usable", s.data_quality.depth_usable},
      {"road_coverage_ratio", s.data_quality.road_coverage_ratio},
      {"min_rock_confidence", opt_to_json(s.data_quality.min_rock_confidence)},
      {"rock_count", s.data_quality.rock_count},
      {"degraded", s.data_quality.degraded},
      {"reasons", s.data_quality.reasons},
  };

  doc["reasons"] = reasons_to_json(s.reasons);
  doc["engine"] = {
      {"name", s.engine_name},
      {"version", s.engine_version},
      {"policy_version", s.policy_version},
  };
  doc["disclaimer_version"] = s.disclaimer_version;

  return pretty ? doc.dump(2) : doc.dump();
}

RoadStatus make_error_status(const std::string& frame_id, int64_t timestamp_ns,
                             const std::string& code, const std::string& message) {
  RoadStatus out;
  out.frame_id = frame_id;
  out.timestamp_ns = timestamp_ns;
  out.overall_risk = RiskLevel::kUnknown;
  out.system_status = SystemStatus::kInputError;
  out.data_quality.degraded = true;
  out.reasons.push_back(Reason{code, message});
  for (auto dir : {Direction::kAToB, Direction::kBToA}) {
    DirectionAssessment d;
    d.direction = dir;
    d.risk = RiskLevel::kUnknown;
    d.passability = Passability::kUnknown;
    d.reasons.push_back(Reason{"POLICY_DEFAULT_DENY", "输入不合法"});
    out.directions.push_back(d);
  }
  out.alternative_path.status = AlternativeStatus::kUnknown;
  out.alternative_path.available = std::nullopt;
  out.alternative_path.verifiable = false;
  return out;
}

}  // namespace rock_risk
