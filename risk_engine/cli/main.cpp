#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

#include "rock_risk/assessor.hpp"
#include "rock_risk/json_io.hpp"

namespace {

void usage() {
  std::cout << "用法: rock_risk_cli [--input <file>] [--stdin] [--now-ms <ms>]\n"
               "                     [--policy <file>] [--compact] [--stream]\n"
               "      --stream: 从 stdin 逐行读 perception_report JSON，逐行输出 road_status JSON。\n"
               "说明: 读取感知模块输出的 perception_report JSON，输出 RoadStatus JSON。\n"
               "      --now-ms 0 表示使用当前系统时间（默认）。\n";
}

bool load_policy(const std::string& path, rock_risk::Policy& policy, std::string& err) {
  std::ifstream ifs(path);
  if (!ifs) {
    err = "无法打开策略文件: " + path;
    return false;
  }
  std::stringstream ss;
  ss << ifs.rdbuf();
  // 复用 JSON 解析，但这里字段很少，直接手写最简解析不做；
  // 改为调用 json_io 的能力会引入对感知格式的依赖，故此处仅支持键值替换。
  const std::string text = ss.str();
  struct KV { const char* key; double* dst; } kvs[] = {
      {"vehicle_width_m", &policy.vehicle_width_m},
      {"safety_margin_m", &policy.safety_margin_m},
      {"min_pass_width_m", &policy.min_pass_width_m},
      {"min_rock_confidence", &policy.min_rock_confidence},
      {"min_road_coverage", &policy.min_road_coverage},
      {"edge_overlap_max", &policy.edge_overlap_max},
      {"full_overlap_min", &policy.full_overlap_min},
      {"required_lookahead_m", &policy.required_lookahead_m},
      {"alternative_margin_m", &policy.alternative_margin_m},
  };
  for (const auto& kv : kvs) {
    const std::string needle = std::string("\"") + kv.key + "\"";
    auto pos = text.find(needle);
    if (pos == std::string::npos) continue;
    pos = text.find(':', pos);
    if (pos == std::string::npos) continue;
    try {
      *kv.dst = std::stod(text.substr(pos + 1));
    } catch (...) {
      err = std::string("无法解析策略项: ") + kv.key;
      return false;
    }
  }

  // 非数值项：policy_version 用于输出溯源，allow_alternative 是安全开关。
  auto value_of = [&](const char* key, std::string& out) -> bool {
    const std::string needle = std::string("\"") + key + "\"";
    auto pos = text.find(needle);
    if (pos == std::string::npos) return false;
    pos = text.find(':', pos);
    if (pos == std::string::npos) return false;
    auto end = text.find_first_of(",}\n", pos + 1);
    out = text.substr(pos + 1, end - pos - 1);
    return true;
  };

  std::string value;
  if (value_of("policy_version", value)) {
    try {
      policy.policy_version = std::stoi(value);
    } catch (...) {
      err = "无法解析策略项: policy_version";
      return false;
    }
  }
  if (value_of("allow_alternative", value)) {
    if (value.find("true") != std::string::npos) {
      policy.allow_alternative = true;
    } else if (value.find("false") != std::string::npos) {
      policy.allow_alternative = false;
    } else {
      err = "无法解析策略项: allow_alternative";
      return false;
    }
  }
  return true;
}

int64_t now_ms_system() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}

}  // namespace

int main(int argc, char** argv) {
  std::string input_path;
  bool from_stdin = false;
  int64_t now_ms = 0;
  std::string policy_path;
  bool compact = false;
  bool stream = false;
  bool now_given = false;

  for (int i = 1; i < argc; ++i) {
    const std::string a = argv[i];
    if (a == "--input" && i + 1 < argc) {
      input_path = argv[++i];
    } else if (a == "--stdin") {
      from_stdin = true;
    } else if (a == "--now-ms" && i + 1 < argc) {
      now_ms = std::stoll(argv[++i]);
      now_given = true;
    } else if (a == "--policy" && i + 1 < argc) {
      policy_path = argv[++i];
    } else if (a == "--compact") {
      compact = true;
    } else if (a == "--stream") {
      stream = true;
    } else if (a == "-h" || a == "--help") {
      usage();
      return 0;
    } else {
      std::cerr << "未知参数: " << a << "\n";
      usage();
      return 1;
    }
  }

  if (!now_given) now_ms = now_ms_system();

  rock_risk::Policy policy;
  if (!policy_path.empty()) {
    std::string err;
    if (!load_policy(policy_path, policy, err)) {
      std::cerr << err << "\n";
      return 1;
    }
  }
  policy.recompute();

  rock_risk::RiskAssessor assessor(policy);
  std::string perr;
  if (!policy.valid(&perr)) {
    std::cerr << "策略参数非法: " << perr << "\n";
    return 3;
  }

  if (stream) {
    // 常驻模式：每行一帧。未显式给 --now-ms 时，用帧自身时间戳作为"现在"，
    // 即 age=0 —— 跨设备时钟不可比，传输延迟由调用方另行统计。
    std::string line;
    while (std::getline(std::cin, line)) {
      if (line.empty()) continue;
      auto parsed = rock_risk::parse_perception_json(line);
      if (!parsed.ok) {
        auto status = rock_risk::make_error_status(parsed.value.frame_id,
                                                   parsed.value.timestamp_ns,
                                                   parsed.error_code,
                                                   parsed.error_message);
        std::cout << rock_risk::serialize_road_status(status, false) << "\n" << std::flush;
        continue;
      }
      const int64_t effective_now =
          now_given ? now_ms : parsed.value.timestamp_ns / 1000000;
      const auto status = assessor.assess(parsed.value, effective_now);
      std::cout << rock_risk::serialize_road_status(status, false) << "\n" << std::flush;
    }
    return 0;
  }

  std::string text;
  if (from_stdin || input_path.empty()) {
    std::stringstream ss;
    ss << std::cin.rdbuf();
    text = ss.str();
  } else {
    std::ifstream ifs(input_path);
    if (!ifs) {
      std::cerr << "无法打开输入文件: " << input_path << "\n";
      return 1;
    }
    std::stringstream ss;
    ss << ifs.rdbuf();
    text = ss.str();
  }

  auto parsed = rock_risk::parse_perception_json(text);
  if (!parsed.ok) {
    // 解析失败也要输出结构合法的 RoadStatus，便于上游统一处理
    auto status = rock_risk::make_error_status(parsed.value.frame_id,
                                               parsed.value.timestamp_ns,
                                               parsed.error_code,
                                               parsed.error_message);
    std::cout << rock_risk::serialize_road_status(status, !compact) << "\n";
    std::cerr << "解析失败: " << parsed.error_code << " " << parsed.error_message << "\n";
    return 2;
  }

  const auto status = assessor.assess(parsed.value, now_ms);
  std::cout << rock_risk::serialize_road_status(status, !compact) << "\n";
  return 0;
}
