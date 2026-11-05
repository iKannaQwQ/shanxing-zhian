# 01 · 数据协议

> Schema 是唯一真源：`contracts/schemas/*.schema.json`。
> 本文只解释**怎么用**，字段细节以 schema 为准。所有样例在 `contracts/samples/`。

## 1. 传输格式

### 1.1 采集端 → 后端

`POST /api/v1/ingest/frame`，`multipart/form-data`：

| part 名 | Content-Type | 必选 | 内容 |
|---|---|---|---|
| `meta` | `application/json` | 是 | `FrameBundle`（不含二进制） |
| `rgb` | `image/jpeg` | 是 | 编码后的彩色帧 |
| `depth` | `application/octet-stream` | 是 | 原始深度，编码见 `meta.depth.encoding` |
| `confidence` | `application/octet-stream` | 否 | `uint8` 置信度图 |

**不采用 base64**（体积 +33%，且大 JSON 解析更慢）。
**不采用自定义二进制头**（Windows/Swift/Python 三方对齐成本高于 multipart）。

### 1.2 模块之间

一律 JSON 文件或 stdio。`perception → risk_engine` 与 `backend → frontend` 用同一套 schema。

## 2. 时间与序列

- `seq`：会话内单调递增。**排序、丢帧检测只信 `seq`**。
- `ts_capture_ns`：设备单调时钟，仅用于同设备间隔计算。
- **禁止**把手机时间戳与主机时间戳直接相减得到延迟——未做时钟同步时该值无意义。跨设备延迟用 ping 偏移估算，或直接不展示。
- `ts_processed_ns` / `engine_ms` / `processing_ms` 是同主机内的可信耗时。

## 3. 深度约定

| 项 | 约定 |
|---|---|
| 编码 | `uint16_mm`（默认）或 `float32_m` |
| 无效值 | `0`（或 `depth.invalid_value`）。**无效 ≠ 很近**，任何代码不得把 0 当有效深度 |
| 对齐 | `depth.aligned_to_rgb` 必须由采集端**实测填写**，不允许默认 true |
| 单位 | 统一米制；像素→米由 `scale_factor` 完成 |

`quality.depth_aligned_ok=false` 时，风险引擎必须拒绝一切米制结论（规则 R2/P5）。

## 4. 版本演进

- `schema_version` 形如 `MAJOR.MINOR`。
- 加可选字段 ⇒ `MINOR+1`；删字段、改语义、改枚举值 ⇒ `MAJOR+1`，旧样例保留在 `contracts/samples/` 下。
- `risk_engine` 收到不认识的 `MAJOR` 必须**拒绝处理并上报 DEGRADED**，不得猜测。
- 所有对象 `additionalProperties: false`：拼错字段名会立刻失败，而不是静默丢数据（跨语言协作下这是净收益）。

## 5. 不变量清单

校验器 `contracts/validate.py` 逐条实现，`--all` 必须通过，CI 卡这一步。

### 感知侧

| 编号 | 规则 | 为什么 |
|---|---|---|
| P1 | 引用岩体必须存在；`depth_source=NONE` 的落石不得出现在 `intrusions` | 禁止臆造距离 |
| P2 | `depth_source=NONE` 时 `height/footprint/distance/base_point` 必须为 null | 同上 |
| P3 | `road.status=FAILED` 时 `coverage_ratio` 不得 >0.30 | 防止分割失败却假装有路 |
| P4 | `depth_valid_ratio` 低于阈值必须标 `LOW_DEPTH_VALIDITY` | 降级必须显式 |
| P5 | `depth_aligned_ok=false` 必须标 `DEPTH_ALIGN_FAILED` | 同上 |

### 风险侧

| 编号 | 规则 | 为什么 |
|---|---|---|
| R1 | `overall_status` = 各方向最不利者（`PASSABLE< RESTRICTED< UNKNOWN< BLOCKED`） | 不允许"挑好的报" |
| R2 | `PASSABLE` 必须满足：道路 OK、深度有效率 ≥0.50、观测覆盖 ≥0.60、且带支撑理由码 | **没看到 ≠ 安全** |
| R3 | `alternative_passage.available=true` ⇒ `verifiable=true` 且对向可确认 且 `min_gap_m` 非空 | 盲弯不得宣称可绕行 |
| R4 | `BLOCKED` 必须带 `BLOCKED_BY_ROCK` 或 `FULL_ROCK_INTRUSION` | 阻断要有归因 |
| R5 | 存在 `UNKNOWN` 方向时整体不得为 `PASSABLE` | 同上 |
| R6 | `reasons` 不得为空 | 结论必须可追溯 |

### 跨文件

| 编号 | 规则 |
|---|---|
| C1 | `inputs_echo.road_status` 与对应感知结果一致 |
| C2 | `blocking_rock_ids` 必须在感知结果的 `rocks` 中存在 |
| C3 | `inputs_echo.rock_count` 与感知结果一致 |

## 6. 新增字段的流程

1. 改 schema（+`MINOR`）→ 2. 加/改合法样例 → 3. 加**非法样例**到 `samples/invalid/`（证明新规则能拦住错误）→ 4. `validate.py --all` 通过 → 5. 更新本文 → 6. 通知 C++/Swift/TS 三方同步。

> 经验：**先写非法样例再写实现**。安全规则只有在能拒绝错误输入时才算存在。
