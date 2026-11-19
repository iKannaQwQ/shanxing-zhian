# 04 · C++ 通行状态与风险评估引擎

> 阶段 P1a。纯规则引擎，无机器学习、无 I/O 副作用、不依赖任何平台专属 API。
> **输出属于实验性辅助评估，不能直接作为真实道路安全判据。**

---

## 0. 状态

| 项 | 结果 |
|---|---|
| 语言 / 标准 | C++17 |
| 依赖 | 仅 nlohmann/json（单头文件，已 vendored） |
| 单元测试 | **48 项全部通过** |
| 构建 | macOS/AppleClang 21 通过；**Windows 脚本已写但未实测** |
| 契约样例 | `contracts/samples/road_status/sample_blocked.json` 通过 8 条不变量 |

---

## 1. 在系统中的位置

```
perception (Python)  --perception_report.json-->  risk_engine (C++)  -->  road_status.json
        感知：看到什么                              判定：这意味着什么           后端/前端
```

引擎**只消费几何与质量信息，不接触图像**。因此可以完全离线、无硬件地跑全部测试。

依赖方向：`risk_engine` 只依赖 `contracts`（JSON 结构），不依赖 perception 的代码，
两者通过文件/stdio 通信。

---

## 2. 输入

`perception_report` JSON（见 `contracts/schemas/perception_report.schema.json`），使用其中：

| 字段 | 用途 |
|---|---|
| `frame_id` / `timestamp_ns` | 身份与新鲜度 |
| `system_status` | 链路健康 |
| `road_detected` / `road_mask_status` / `road.coverage_ratio` | 道路可用性 |
| `rockfalls[].confidence` | 置信度过滤 |
| `rockfalls[].intrusion` | NONE/EDGE/PARTIAL/FULL/UNKNOWN |
| `rockfalls[].remaining_width_m` | 剩余通行宽度（米） |
| `rockfalls[].lateral_offset_norm` | 横向位置 |
| `rockfalls[].depth_estimate` | 距离与深度来源 |
| `depth.usable` | 米制结论是否可信 |
| `metrics.combined_blocked` | 多石联合阻断 |
| `environment`（可选） | 可视视距、对向交通 |

**`environment` 缺失时一律按"不可确认"处理**，绝不按"无对向车"处理。

---

## 3. 输出

`road_status` JSON（见 `contracts/schemas/road_status.schema.json`）：

```json
{
  "msg_type": "road_status",
  "road_id": "main",
  "frame_id": "frame-abc",
  "timestamp_ns": 1700000000000000000,
  "overall_risk": "HIGH",
  "system_status": "OK",
  "directions": [
    {"direction": "A_TO_B", "risk": "HIGH", "passability": "BLOCKED",
     "remaining_width_m": 0.05, "limiting_rock_ids": ["R1"], "reasons": [...]},
    {"direction": "B_TO_A", "risk": "HIGH", "passability": "BLOCKED", "...": "..."}
  ],
  "passability": {"A_TO_B": "BLOCKED", "B_TO_A": "BLOCKED"},
  "alternative_path": {
    "status": "UNAVAILABLE", "available": false, "verifiable": true,
    "min_gap_m": 0.05, "oncoming": "UNVERIFIABLE", "reasons": [...]
  },
  "data_quality": {"frame_age_ms": 0, "depth_usable": true, "road_coverage_ratio": 0.2,
                   "min_rock_confidence": 0.91, "rock_count": 1, "degraded": false,
                   "reasons": []},
  "reasons": [...],
  "engine": {"name": "rock_risk", "version": "0.1.0", "policy_version": "1"},
  "disclaimer_version": "0.1"
}
```

---

## 4. 风险等级规则

| 等级 | 判定条件 | 通行性 |
|---|---|---|
| `SAFE` | 无落石 **且** 道路可用、深度可用、链路 OK、未过期 | `PASSABLE` |
| `LOW` | 有落石但仅 `NONE`/`EDGE` 侵入；或有低置信度目标未参与判定 | `PASSABLE` |
| `MEDIUM` | 存在 `PARTIAL` 侵入，且剩余宽度 ≥ 最小可通行宽度 | `RESTRICTED` |
| `HIGH` | 存在 `FULL` 侵入，或 `PARTIAL` 但剩余宽度 < 最小可通行宽度 | `BLOCKED` |
| `UNKNOWN` | 链路异常、数据过期、道路不可用、关键数据缺失、落石关系无法判定 | `UNKNOWN` |

严重度排序：`SAFE < LOW < MEDIUM < HIGH < UNKNOWN`。
**`UNKNOWN` 被排在最后（最不利）**，因为它意味着"无法排除危险"。

**多石联合（2026-11-18 新增）**：感知侧给出 `metrics.combined_remaining_width_px`
（所有障碍接地区域求并集后的最宽连续空隙）。引擎用任一有效落石的"米/像素"比例
换算成米；若联合空隙小于 `min_pass_width_m`，或 `combined_blocked=true`，
**主方向也升级为 `HIGH`**，并在方向理由里写入 `MULTI_ROCK_JOINT_BLOCK`。
联合空隙即使大于阈值，也会作为方向的 `remaining_width_m`（取与单块的最小值），
不再只报告单块各自的剩余宽度。这修复了"两块石各自都剩 3 cm、合起来只剩 2 cm
却判 RESTRICTED"的问题。

---

## 5. 质量门控（fail-closed 的具体实现）

引擎里有一条不对称原则：

> **2D 证据可以证明"危险"，但不能证明"安全"。**

于是：

- 深度不可用时，`FULL` 落石仍然判 `HIGH`（看见了就是看见了）；
- 深度不可用且没有落石时，**不会**判 `SAFE`，而是 `UNKNOWN`；
- 低置信度目标被过滤时，风险至少为 `LOW`，不允许回到 `SAFE`。

对应的门控表：

| 条件 | 结果 |
|---|---|
| `system_status ∈ {INPUT_ERROR, TIMEOUT, DISCONNECTED}` | `UNKNOWN` / `INPUT_ERROR` |
| 数据年龄 > `max_frame_age_ms` | `UNKNOWN` / `STALE` |
| 道路未检出或掩码非 OK | `UNKNOWN` / `NO_ROAD` |
| 深度不可用 + 无落石 | `UNKNOWN` / `NO_DEPTH` |
| 深度不可用 + 有 `FULL` 落石 | `HIGH` / `NO_DEPTH` |
| 全部正常 + 无落石 | `SAFE` / `OK` |
| 存在 `intrusion=UNKNOWN` 的有效落石 | `UNKNOWN` |

---

## 6. 替代通行空间

### 6.1 四态

| 状态 | 含义 | `available` |
|---|---|---|
| `AVAILABLE` | 可确认存在安全替代空间 | `true` |
| `UNAVAILABLE` | 可确认**不存在**安全替代空间 | `false` |
| `UNKNOWN` | 无法确认 | `null` |
| `NOT_EVALUATED` | 当前不需要替代空间（道路本身可通行） | `null` |

### 6.2 判定顺序（关键）

先判断"能否确定不可用"，再判断"是否无法确认"：

```
1. 整体 SAFE/LOW  -> NOT_EVALUATED
2. 整体 UNKNOWN   -> UNKNOWN
3. 策略关闭       -> NOT_EVALUATED
4. 逐条检查：
   联合阻断                 -> 确定不可用
   间隙 < 最小可通行宽度     -> 确定不可用（需深度可用）
   检测到对向交通           -> 确定不可用
   对向交通不可确认         -> 无法确认
   可视视距缺失或不足       -> 无法确认
   深度不可用               -> 无法确认
5. 确定不可用 -> UNAVAILABLE（verifiable=true）
   否则无法确认 -> UNKNOWN（verifiable=false）
   否则         -> AVAILABLE
```

这个顺序是有意为之：**间隙明确过窄时，即便盲弯对向不明，也已经是不可用**，
不应把确定结论降级成"未知"。反之，只有"看起来空"绝不足以判 `AVAILABLE`——
必须同时满足：间隙够、对向已确认、视距足够、深度可用。

`AVAILABLE` 是四态里唯一需要主动证据的，其余三态都可以靠"缺证据"得到。

---

## 7. 方向对称性（必须说明）

当前 `direction_basis = "SHARED_CORRIDOR"`：**A→B 与 B→A 得到相同结论**。

原因：感知侧没有车道线、也没有道路坐标系（road frame），两块方向共享同一条物理走廊。
在这种条件下强行制造左右不对称（例如"靠右行驶所以右侧更空"），是在**编造不存在的证据**。

要产生真正有区分度的方向判定，需要上游提供：
- 道路中心线与横向参考；
- 每个方向的通行走廊（哪一半属于哪个方向）；
- 沙盘标定得到的 `A端/B端` 定义。

这三项都还没有。因此引擎把方向输出保留为接口，但结论共享，并在 JSON 里显式标注
`direction_basis`，避免下游误以为这是两个独立评估。

---

## 8. 工程结构

```
risk_engine/
├── CMakeLists.txt
├── include/rock_risk/
│   ├── types.hpp      枚举 + 数据结构 + 字符串互转
│   ├── policy.hpp     阈值参数与自检
│   ├── assessor.hpp   纯函数评估器
│   └── json_io.hpp    解析 / 序列化
├── src/{types,policy,assessor,json_io}.cpp
├── cli/main.cpp       命令行：文件或 stdin -> stdout
├── tests/             48 项单元测试（自带极简框架，零外部依赖）
└── third_party/nlohmann/json.hpp
```

`assess()` 不读取系统时钟——`now_ms` 由调用方传入，因此"数据过期"可确定性测试。

---

## 9. 构建与运行

### macOS / Linux

```bash
bash scripts/build_risk_engine.sh
# 或手动
cmake -S risk_engine -B risk_engine/build -DCMAKE_BUILD_TYPE=Release
cmake --build risk_engine/build -j
risk_engine/build/rock_risk_tests
```

### Windows x86-64

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_risk_engine.ps1
```

前置：Visual Studio 2022 Build Tools（MSVC）+ CMake ≥ 3.16。
代码中无 POSIX 调用、无 macOS API、路径全部由 CMake 处理。

> ⚠️ **Windows 构建脚本尚未在 Windows 上实际执行过**。按项目规矩，在 P8 实机验证前
> 不得声称"Windows 已支持"。当前只能确认代码层面无平台依赖。

### 命令行

```bash
risk_engine/build/rock_risk_cli --input frame.json --now-ms 1700000000000
risk_engine/build/rock_risk_cli --stdin < frame.json
risk_engine/build/rock_risk_cli --input frame.json --policy policy.json --compact
```

退出码：`0` 成功，`2` 输入解析失败（**仍会输出结构合法的 `road_status`**），`3` 策略参数非法。

---

## 10. 策略参数（全部待标定）

| 参数 | 默认值 | 说明 |
|---|---|---|
| `vehicle_width_m` | 0.050 | 沙盘玩具车宽，**占位值** |
| `safety_margin_m` | 0.010 | 单侧余量，**占位值** |
| `min_pass_width_m` | 0.070 | 由上面两项推导 |
| `min_rock_confidence` | 0.35 | 低于此值不参与判定 |
| `min_road_coverage` | 0.02 | 道路面积下限 |
| `max_frame_age_ms` | 2000 | 过期阈值 |
| `required_lookahead_m` | 0.80 | 替代通行所需前视距离，**占位值** |
| `alternative_margin_m` | 0.010 | 替代路径额外余量 |

**这些数字没有一个来自实测。** 沙盘建成后必须用真实尺寸重新标定，
并把标定结果写入 `config/policy.json`，而不是改代码默认值。

---

## 11. 测试覆盖

48 项，覆盖：

- 单石分级（NONE/EDGE/PARTIAL/FULL/UNKNOWN，含"PARTIAL 但过窄升级为 HIGH"）
- 质量门控（无道路、无深度、过期、链路异常、低置信度过滤）
- 多石取最不利；多石联合空隙低于阈值时主方向升级 HIGH，高于阈值时仍作为方向剩余宽度
- 替代通行四态及判定顺序（含"确定不可用优先于无法确认"）
- JSON 往返、缺字段、坏 JSON、未知枚举
- 错误输出仍然结构合法
- 枚举字符串互转、策略自检

契约层另有 8 条不变量（`contracts/validate.py` 的 `S1–S8`），
用于校验 C++ 实际输出的 JSON，防止"代码改了但规则没跟上"。

---

## 12. 已知限制

1. **方向判定不独立**（见 §7），需要道路坐标系才能细化。
2. **无时序滤波**：单帧判定，未做多帧一致性确认，可能出现状态抖动。
3. **阈值未标定**（见 §10）。
4. **未做 pybind11 绑定**，当前只能通过 CLI 子进程调用；
   Python 侧接入时每次调用有一次进程启动开销（约数毫秒）。
5. **Windows 未实测**（见 §9）。
6. **`environment` 上游还没有生产者**：感知模块目前不输出对向交通与可视视距，
   因此替代通行实际上会长期停留在 `UNKNOWN`——这是正确的保守行为，不是 bug。

---

## 13. 下一步

1. Python 后端接一个 `RiskEngineClient`（子进程 + 超时 + 降级），把 CLI 用起来。
2. 多帧迟滞：`SAFE↔LOW↔MEDIUM` 需要连续 N 帧才切换，`HIGH`/`UNKNOWN` 立即生效。
3. 沙盘标定后替换阈值，并补一份 `config/policy.json`。
4. 道路坐标系就绪后，把 `direction_basis` 从 `SHARED_CORRIDOR` 升级为 `SECTOR_BASED`。
