#pragma once
#include <cstdint>
#include <string>
#include <vector>

#include "rock_risk/policy.hpp"
#include "rock_risk/types.hpp"

namespace rock_risk {

// 纯函数式评估器：不进行 I/O，不依赖时钟（now_ms 由调用方传入），
// 因此可以在没有硬件的情况下完整单元测试。
class RiskAssessor {
 public:
  explicit RiskAssessor(Policy policy = Policy{});

  RoadStatus assess(const PerceptionInput& input, int64_t now_ms) const;

  const Policy& policy() const { return policy_; }

 private:
  Policy policy_;
};

// --- 可单独测试的规则函数 ---

// 单块落石 -> 风险等级（不考虑整体质量门控）
RiskLevel classify_rock(const RockObservation& rock, const Policy& policy,
                        std::vector<Reason>* reasons);

Passability to_passability(RiskLevel risk);

}  // namespace rock_risk
