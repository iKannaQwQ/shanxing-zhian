// JSON 输入输出与错误处理单元测试。
#include <string>

#include "nlohmann/json.hpp"
#include "rock_risk/assessor.hpp"
#include "rock_risk/json_io.hpp"
#include "rock_risk/policy.hpp"
#include "test_framework.hpp"

using namespace rock_risk;

namespace {

std::string valid_report() {
  return R"JSON({
  "schema_version": "1.0",
  "frame_id": "frame-abc",
  "timestamp": "2026-11-05T12:00:00+00:00",
  "timestamp_ns": 1700000000000000000,
  "source": "SIMULATED",
  "system_status": "OK",
  "road_detected": true,
  "road_mask_status": "OK",
  "road": {
    "coverage_ratio": 0.21,
    "confidence": 0.9,
    "component_count": 1,
    "hole_ratio": 0.0,
    "polygon": [[10, 10], [100, 10], [100, 200]],
    "width_profile": [],
    "centerline": []
  },
  "rockfalls": [
    {
      "rock_id": "R1",
      "label": "rock",
      "bbox": [200, 350, 330, 430],
      "confidence": 0.88,
      "truncated": false,
      "road_overlap_ratio": 1.0,
      "bbox_overlap_ratio": 0.55,
      "intrusion": "PARTIAL",
      "risk_level": "PARTIAL",
      "risk_level_basis": "INTRUSION_GEOMETRY_ONLY",
      "evidence": "RGB_D",
      "lateral_offset_norm": 0.1,
      "remaining_width_px": 114.0,
      "remaining_width_m": 0.0912,
      "in_road_by_center": true,
      "depth_estimate": {
        "distance_m": 0.8,
        "source": "LIDAR",
        "valid_ratio": 1.0,
        "sample_count": 9,
        "point_xyz_m": [0.0, 0.0, 0.8],
        "reason": "OK"
      },
      "reasons": []
    }
  ],
  "depth": {"usable": true, "reasons": []},
  "metrics": {"rock_count": 1, "max_risk_level": "PARTIAL",
              "combined_remaining_width_px": 114.0, "combined_blocked": false,
              "timings_ms": {}},
  "reasons": [{"code": "NO_ROCK_DETECTED", "message": "x"}],
  "disclaimer": "实验性辅助评估，不构成真实道路驾驶指令"
})JSON";
}

}  // namespace

TEST(parse_valid_report) {
  auto out = parse_perception_json(valid_report());
  CHECK(out.ok);
  CHECK_EQ_STR(out.value.frame_id, "frame-abc");
  CHECK(out.value.timestamp_ns == 1700000000000000000LL);
  CHECK(out.value.status == PerceptionStatus::kOk);
  CHECK(out.value.road_detected);
  CHECK(out.value.road_mask_status == RoadMaskStatus::kOk);
  CHECK_NEAR(out.value.road_coverage_ratio, 0.21, 1e-9);
  CHECK(out.value.rocks.size() == 1);
  CHECK(out.value.rocks[0].intrusion == Intrusion::kPartial);
  CHECK(out.value.rocks[0].depth_source == DepthSource::kLidar);
  CHECK(out.value.rocks[0].remaining_width_m.has_value());
  CHECK_NEAR(*out.value.rocks[0].remaining_width_m, 0.0912, 1e-9);
  CHECK(out.value.depth_usable);
  CHECK(!out.value.combined_blocked);
}

TEST(parse_missing_environment_is_unknown) {
  auto out = parse_perception_json(valid_report());
  CHECK(out.ok);
  CHECK(!out.value.environment.present);
  CHECK(out.value.environment.oncoming == Oncoming::kUnknown);
  CHECK(!out.value.environment.visibility_horizon_m.has_value());
}

TEST(parse_environment_when_present) {
  auto text = valid_report();
  text.insert(text.find("\"depth\":"),
              "\"environment\": {\"visibility_horizon_m\": 1.2, "
              "\"oncoming_traffic\": \"CONFIRMED_NONE_VISIBLE\"},\n  ");
  auto out = parse_perception_json(text);
  CHECK(out.ok);
  CHECK(out.value.environment.present);
  CHECK(out.value.environment.oncoming == Oncoming::kConfirmedNoneVisible);
  CHECK(out.value.environment.visibility_horizon_m.has_value());
}

TEST(parse_rejects_bad_json) {
  auto out = parse_perception_json("{not json");
  CHECK(!out.ok);
  CHECK_EQ_STR(out.error_code, "JSON_PARSE_ERROR");
}

TEST(parse_rejects_missing_frame_id) {
  auto text = valid_report();
  text.replace(text.find("\"frame-abc\""), 11, "\"\"       ");
  auto out = parse_perception_json(text);
  CHECK(!out.ok);
  CHECK_EQ_STR(out.error_code, "MISSING_FRAME_ID");
}

TEST(parse_rejects_missing_timestamp) {
  auto text = valid_report();
  text.replace(text.find("\"timestamp_ns\": 1700000000000000000,"),
               std::string("\"timestamp_ns\": 1700000000000000000,").size(), "");
  auto out = parse_perception_json(text);
  CHECK(!out.ok);
  CHECK_EQ_STR(out.error_code, "MISSING_TIMESTAMP");
}

TEST(parse_rejects_unknown_status) {
  auto text = valid_report();
  text.replace(text.find("\"OK\",\n  \"road_detected\""), 4, "\"WEIRD\"");
  auto out = parse_perception_json(text);
  CHECK(!out.ok);
  CHECK_EQ_STR(out.error_code, "UNKNOWN_SYSTEM_STATUS");
}

TEST(parse_rejects_missing_rockfalls) {
  auto text = R"({"frame_id":"f","timestamp_ns":1,"system_status":"OK",
    "road_detected":true,"road_mask_status":"OK","road":{"coverage_ratio":0.2},
    "depth":{"usable":true,"reasons":[]}})";
  auto out = parse_perception_json(text);
  CHECK(!out.ok);
  CHECK_EQ_STR(out.error_code, "MISSING_ROCKFALLS");
}

TEST(serialize_has_required_fields) {
  RiskAssessor a(Policy{});
  auto parsed = parse_perception_json(valid_report());
  CHECK(parsed.ok);
  auto st = a.assess(parsed.value, parsed.value.timestamp_ns / 1000000);
  const auto text = serialize_road_status(st);
  auto doc = nlohmann::json::parse(text);

  for (const char* key : {"msg_type", "road_id", "frame_id", "timestamp_ns", "overall_risk",
                          "system_status", "directions", "passability",
                          "alternative_path", "data_quality", "reasons",
                          "engine", "disclaimer_version"}) {
    CHECK(doc.contains(key));
  }
  CHECK(doc["directions"].is_array());
  CHECK(doc["directions"].size() == 2);
  CHECK(doc["directions"][0].contains("passability"));
  CHECK(doc["alternative_path"].contains("status"));
  CHECK(doc["data_quality"].contains("frame_age_ms"));
  CHECK_EQ_STR(doc["overall_risk"].get<std::string>(), "MEDIUM");
}

TEST(serialize_nulls_are_json_null) {
  RoadStatus s;
  s.frame_id = "f";
  s.directions.push_back(DirectionAssessment{});
  s.directions[0].remaining_width_m = std::nullopt;
  const auto doc = nlohmann::json::parse(serialize_road_status(s));
  CHECK(doc["directions"][0]["remaining_width_m"].is_null());
  CHECK(doc["alternative_path"]["available"].is_null());
  CHECK(doc["data_quality"]["min_rock_confidence"].is_null());
}

TEST(error_status_is_structurally_valid) {
  auto st = make_error_status("f-1", 42, "JSON_PARSE_ERROR", "bad");
  const auto doc = nlohmann::json::parse(serialize_road_status(st));
  CHECK_EQ_STR(doc["overall_risk"].get<std::string>(), "UNKNOWN");
  CHECK_EQ_STR(doc["system_status"].get<std::string>(), "INPUT_ERROR");
  CHECK(doc["directions"].size() == 2);
  CHECK_EQ_STR(doc["directions"][0]["passability"].get<std::string>(), "UNKNOWN");
  CHECK(doc["data_quality"]["degraded"].get<bool>());
}

TEST(enum_string_roundtrip) {
  CHECK_EQ_STR(to_string(RiskLevel::kHigh), "HIGH");
  CHECK_EQ_STR(to_string(AlternativeStatus::kNotEvaluated), "NOT_EVALUATED");
  CHECK_EQ_STR(to_string(SystemStatus::kStale), "STALE");
  RiskLevel rl;
  CHECK(parse_risk_level("MEDIUM", rl) && rl == RiskLevel::kMedium);
  CHECK(!parse_risk_level("NOPE", rl));
}
