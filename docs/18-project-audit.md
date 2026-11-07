# 18 · 项目审查报告（2026-11-07）

> 本报告只记录**本次实际核对**的结果。凡标"已核对"的，均有命令输出或测试结果支撑；
> 凡未实际跑过的，明确标"未验证"。

---

## A. 项目现状核对

### A.1 总览

| 项 | 核对方式 | 结果 |
|---|---|---|
| 提交数 | `git log` | 63 |
| Python 测试 | `pytest perception/tests backend/tests` | **89 passed** |
| C++ 测试 | `rock_risk_tests` | **46/46** |
| 契约校验 | `validate.py --all` | **12/12** |
| iOS 编译 | `xcodebuild` | **BUILD SUCCEEDED** |
| 工作区 | `git status` | ⚠️ 脏（`logs/train_expE.log` 正在写入） |

### A.2 分模块

| 模块 | 已实现 | 证据 | 未验证 / 偏差 |
|---|---|---|---|
| **iOS** | 28 个 Swift 文件；ARKit RGB-D、10fps 抽帧、打包、上传、WS、热降级、朝向 | 本次重新编译通过；`docs/14` | 真机最近未复测；**俯视 0.64 m 跟踪未验证** |
| **Python 感知** | 22 个文件；道路分割→落石→深度融合→侵入判定；不确定性闸门 | 75 项测试 | 落石分支无权重，恒走 DEGRADED |
| **C++ 风险引擎** | 4 文件 / 46 测试；fail-closed；替代路径策略 | 本次实跑 | **双向判定为名义双向**，见 B.3 |
| **FastAPI** | 15 条路由；推理解耦；文件存储；WS | 14 项测试 | 无鉴权；未压测 |
| **Vue** | 6 个页面（用户 1 + 管理 5）；构建通过 | 构建 + 类型检查 | **未做浏览器目视** |
| **契约** | 5 个 Schema，12/12 | `validate.py` | `model` 块为本轮新增（可选） |
| **AeroScapes** | 数据转换、序列划分、A–D 四组实验、E 运行中 | `docs/15/16/17` | E 未完成；目标域零验证 |
| **落石检测** | `detector.py` + `train_rock_detector.py` + `ROAD_ROCK_WEIGHTS` 接线 | 代码 | **无任何权重文件**；Mars 数据集在盘但未训练 |
| **Windows** | 仅 `build_risk_engine.ps1` | 文件存在 | **从未在 Windows 执行**；无启动脚本 |

### A.3 与原始目标不一致之处

1. **落石检测为空**，系统恒 `DEGRADED` → `UNKNOWN`，业务链条断在"看没看见石头"。
2. **双向通行是名义双向**（见 B.3）。
3. 最近 5 次提交全部围绕道路分割，**与本项目"落石 + 通行风险"的核心目标偏离**。

---

## B. 项目偏离检查

### B.1 是否被简化为道路分割项目？

**有偏科趋势。** 最近 5 次提交：

```
f2e9b1b 不确定性闸门
4620148 Stage 5 接入
2b3ba55 实验 A 结果
8f5749d Stage 2 数据转换
66329ec Stage 1 审计
```

同期落石检测提交数 **0**。道路分割是"眼睛的一部分"，但项目验收标准是
"识别落石 → 空间关系 → 双向通行 → 风险展示"，目前只完成了第一环的一半。

### B.2 落石检测是否已有可用实现？

**没有。**

- 代码有：`perception/src/perception/rocks/detector.py`（`YoloRockDetector`）
- 训练脚本有：`perception/tools/train_rock_detector.py`
- 接线有：`ROAD_ROCK_WEIGHTS` → `PerceptionService`
- **权重：无**。`perception/weights/` 下只有道路分割模型。

Mars Boulder 数据（39 训练 / 6 验证，mAP50 0.467）在盘上占 15 MB，
但**没有对应权重被训练或保留**，不能作为能力证明。

### B.3 风险引擎是否真的支持双向判断？

**不支持，是名义双向。**

代码：`risk_engine/src/assessor.cpp:262`

```cpp
fill_empty_directions(out, final_risk, rock_reasons);
for (auto& d : out.directions) {
  d.remaining_width_m = min_gap_m;
  d.limiting_rock_ids = limiting;
}
```

两个方向被填入**同一个** `final_risk`，再赋同一个剩余宽度。

实测（合成落石推到 HIGH）：

```
overall_risk   : HIGH | system: OK
direction_basis: SHARED_CORRIDOR
  A_TO_B: risk=HIGH pass=BLOCKED width=0.03
  B_TO_A: risk=HIGH pass=BLOCKED width=0.03
双向字段完全一致: True
```

**含义**：系统无法表达"A→B 被阻断、但 B→A 仍可通过"这种真实场景。
当前所有输出里 A→B 与 B→A 永远相等。

### B.4 是否存在把 UNKNOWN 错当安全的路径？

**C++ 层未发现。**

- `to_passability(kUnknown) → Passability::kUnknown`（assessor.cpp:56）
- 替代路径遇 UNKNOWN → `kUnknown` + `verifiable=false` + `POLICY_DEFAULT_DENY`

但存在**未来风险**：道路分割对域外画面给出高置信度假阳性
（室内天花板：覆盖率 32%、置信度 0.89）。当前只因"没有落石检测器"才落到 UNKNOWN。
**一旦接入落石检测器，这条路径就会把假阳性掩码当真**。本轮新增的熵闸门已把
室内帧拦截率提到 7/8，但不是 8/8。

### B.5 数据与指标是否被过度解读？

**文档层面已设限**（`docs/16` 第 7 节明确列出不能宣称的四条），但有两个技术事实需记录：

1. **验证集选模型带来乐观偏差**：val 0.7471 vs test 0.6447（差 0.10）；
2. **实验 C 的归因混杂**：同时改了分辨率与 batch，已补实验 D 才拆开
   （D 256/bs4 = 0.5813，证明 batch 才是主因）。

### B.6 接口 / 文档与代码是否一致？

基本一致（本轮已修正 README 严重过时的状态表、已修正"闸门能拦住桥帧"的错误结论）。

**两处工程卫生问题**：

| 问题 | 现状 |
|---|---|
| `logs/` 被 git 跟踪 | 7 个训练日志入库；其中 `train_expE.log` 正在写入导致工作区常脏 |
| `perception/weights/` 被 gitignore | 权重不入库（合理，7–22 MB/个），但"最佳权重"这一交付物只存在于本机 |

---

## C. 下一阶段执行计划

### C.1 必须立即完成（P0）

| # | 任务 | 预期改动文件 | 测试方案 |
|---|---|---|---|
| 1 | 等实验 E 完成并出 A–E 对比报告 | `docs/16` | 读日志 + 复算指标 |
| 2 | **落石检测数据可行性**：实际下载/检查至少一个候选数据集 | 新增 `docs/19` | 检查真实图像与标注，不凭名称判断 |
| 3 | **落石检测基线训练**：轻量 YOLO，CPU 可推理 | `perception/tools/train_rock_detector.py`（已存在） | mAP50/50-95/PR/混淆矩阵 + CPU 推理耗时 |
| 4 | 落石检测接入 `ROAD_ROCK_WEIGHTS` 并验证 DEGRADED 解除 | `perception/service.py` | 端到端：有石头 → 不再恒 UNKNOWN |

### C.2 需要修复（P1）

| # | 问题 | 最小方案 |
|---|---|---|
| 5 | 双向判定是 SHARED_CORRIDOR | 引入道路坐标轴（由 mask 主轴 + 落石横向位置推断），让 A→B / B→A 可不同；**不改变 JSON 契约** |
| 6 | 侵入判断只用接地区域 | 补测：bbox 中心点 vs 接地区域两种口径在 AeroScapes 上的差异，记录误差来源 |
| 7 | 域外假阳性 1/8 漏网 | 记录为已知限制；等沙盘数据后重标定阈值 |

### C.3 可以暂缓（P2）

| # | 任务 | 理由 |
|---|---|---|
| 8 | Windows 实机部署 | 需用户提供机器；落石模块未稳前不宜大动 |
| 9 | Vue 真机/浏览器验收 | 需真机；清单已在 `docs/11` |
| 10 | 沙盘采集与标注流程 | 需沙盘；规范可先写（`docs/09` 已有基础） |

### C.4 影响面评估

| 改动 | 影响模块 | 风险 |
|---|---|---|
| 落石权重接入 | perception、backend（DEGRADED 消失）、前端（状态变化） | 中：会改变全链路判定结果 |
| 双向判定改造 | C++ 引擎、契约 `directions[]`（字段不变，值可变） | **高**：需 46 项 C++ 测试全过 + 新增用例 |
| 熵闸门阈值 | perception | 低：已有 4 项单测 |

---

## D. 执行方式

1. 每个任务：改代码 → 跑相关测试 → 记录真实输出 → 更新文档 → 提交 → 汇报。
2. **不伪造**：代码写完 ≠ 测试通过 ≠ 真机通过。
3. 遇到缺数据/缺设备，明确报告阻塞，不用公开数据集指标替代目标域结论。
4. 实验记录保留：编号、数据版本、划分、超参、种子、日志、权重、指标、失败案例。

---

## E. 与提示词冲突之处（需确认）

1. 提示词称"落石检测尚无可用于正式业务的可靠权重"——**核对属实**，无冲突。
2. 提示词称"风险引擎支持双向判定"——**与代码事实冲突**：实际是 `SHARED_CORRIDOR`
   名义双向（见 B.3）。建议按 C.2 第 5 项改造，而不是当作"已完成"。
3. 提示词要求"最先完成实验 E"——E 仍在训练（epoch 4/15），且工作区因日志写入变脏，
   建议 E 完成后一并提交日志。
