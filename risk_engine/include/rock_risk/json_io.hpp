#pragma once
#include <string>

#include "rock_risk/types.hpp"

namespace rock_risk {

struct ParseOutcome {
  bool ok = false;
  std::string error_code;
  std::string error_message;
  PerceptionInput value;
};

// 解析感知模块输出的 perception_report JSON。
// 解析失败时不抛异常，而是返回 ok=false 并尽量保留 frame_id / timestamp。
ParseOutcome parse_perception_json(const std::string& text);

// 序列化为 RoadStatus JSON（2 空格缩进）。
std::string serialize_road_status(const RoadStatus& status, bool pretty = true);

// 解析失败时也要输出结构合法的 RoadStatus，避免下游拿到空响应。
RoadStatus make_error_status(const std::string& frame_id, int64_t timestamp_ns,
                             const std::string& code, const std::string& message);

}  // namespace rock_risk
