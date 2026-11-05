#pragma once
// 判定策略参数。
//
// 重要：所有"米制"阈值都是**沙盘尺度的占位值**，尚未用真实沙盘标定。
// 标定前不得把这些数值当成物理事实引用。

#include <cstdint>
#include <string>

namespace rock_risk {

struct Policy {
  // --- 通行能力 ---
  double vehicle_width_m = 0.050;      // 沙盘玩具车宽（占位，待标定）
  double safety_margin_m = 0.010;      // 单侧安全余量（占位，待标定）
  // 由上面两项推导，但允许直接覆盖（例如从标定文件读入）
  double min_pass_width_m = 0.070;

  // --- 检测质量 ---
  double min_rock_confidence = 0.35;   // 低于此值不参与判定
  double min_road_coverage = 0.02;     // 道路面积占比下限
  int64_t max_frame_age_ms = 2000;     // 超过即视为过期数据

  // --- 侵入分级（与感知侧保持一致，用于交叉校验）---
  double edge_overlap_max = 0.15;
  double full_overlap_min = 0.85;

  // --- 替代通行 ---
  double required_lookahead_m = 0.80;  // 需要的前视距离（占位，待标定）
  double alternative_margin_m = 0.010; // 替代路径额外余量
  bool allow_alternative = true;       // 总开关，便于保守部署

  int policy_version = 1;

  // 用 vehicle_width/safety_margin 重新推导 min_pass_width_m
  void recompute();
  // 基本自检：阈值必须自洽
  bool valid(std::string* error) const;
};

}  // namespace rock_risk
