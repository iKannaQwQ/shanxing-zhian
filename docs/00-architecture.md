# 山区公路落石检测、通行风险评估与预警系统 — 总体架构

> 状态：**设计草案 v0.1（未实现任何业务代码）**
> 本文只描述架构、协议与开发顺序，不构成完工声明。
> 系统输出为**实验性辅助评估**，不作为真实道路驾驶指令。

---

## 1. 现场勘察结论（已实测，非假设）

| 项 | 实测值 | 影响 |
|---|---|---|
| 主机 | Apple M5 / arm64 / 16 GB / macOS 26.6 | 符合预期 |
| Xcode | 27.2，iOS SDK 27.2 | 可用；需注意 API 弃用 |
| iOS 模拟器运行时 | **未安装（`simctl list runtimes` 为空）** | ARKit/LiDAR 本来也不支持模拟器 → **必须用真机 iPhone 12 Pro** |
| Python | 3.13.18（uv 托管） | 推理栈建议**降到 3.11**（见 §10.1） |
| uv | 0.13.7 | 用它做 Python 环境隔离，不污染系统 |
| Node.js / pnpm | **未安装** | 前端无法启动，P0 必须补 |
| CMake | **未安装** | C++ 引擎无法构建，P0 必须补 |
| Homebrew | 未安装 | 依赖需手动装或用官方安装包 |
| 已有 Python 包 | torch / cv2 / numpy / fastapi **全无** | 干净环境，不存在版本冲突历史 |

**结论：当前不具备"第一天就跑通全链路"的条件。P0 阶段的第一产出必须是可复现的环境脚本，而不是业务代码。**

---

## 2. 目标、非目标与安全边界

### 2.1 目标（v1）

1. iPhone 12 Pro 实时采集 RGB + LiDAR depth + 置信度 + 内参 + 位姿。
2. 道路区域分割（RGB 语义）+ 落石检测（RGB 检测 + 深度几何双通道）。
3. 将像素结果投影到沙盘世界坐标，得到**米制**的落石占路量。
4. 按方向（A→B / B→A）独立评估通行状态。
5. 评估替代通行空间是否**可验证**，而非仅"看起来空"。
6. FastAPI + SQLite + WebSocket 落库与推送。
7. Vue 3 用户页 + 管理后台。

### 2.2 明确的非目标（v1 不做）

- 不做车道线检测（**沙盘无标线**，YOLOP 的 lane head 在本项目中直接禁用）。
- 不做车速/轨迹预测、不做多相机融合、不做车载部署。
- 不做端上推理（iOS 只采集，推理在 PC）——v2 可选 Core ML。
- 不做"安全/不安全"的二元结论，只做四态 + 置信度。

### 2.3 安全边界（写进代码，不只写进文档）

| 编号 | 规则 | 实现位置 |
|---|---|---|
| S1 | 道路分割失败 → 所有方向 `UNKNOWN` | risk_engine 门控 |
| S2 | 深度有效率低于阈值 → 一切米制结论 `UNKNOWN` | risk_engine 门控 |
| S3 | 深度缺失时禁止臆造距离（`depth_source=NONE` 的落石不得参与阻断判定） | perception 标注来源 + engine 拒绝 |
| S4 | 盲弯超出可视视距 → 替代通行 `verifiable=false`，不得给 `available=true` | risk_engine |
| S5 | 仅"道路空白"不足以判定可绕行 → 需 3D 占用证明 + 时间稳定性 | risk_engine |
| S6 | 未检测到落石 ≠ 安全 → `PASSABLE` 还要求观测覆盖率达标 | risk_engine |
| S7 | 每条结论必须带 `reasons[]` 与 `disclaimer_version` | 全链路 |

> 核心思想：**默认拒绝（fail-closed）**。缺证据时输出 `UNKNOWN`，绝不输出 `PASSABLE`。

---

## 3. 分层架构

```
┌─────────────────────────────────────────────────────────────────┐
│ L0 契约层  contracts/                                           │
│    JSON Schema + 测试向量，跨语言唯一真源，先于一切代码存在        │
└───────────┬────────────────┬────────────────┬───────────────────┘
            │                │                │
┌───────────▼──────┐ ┌───────▼────────┐ ┌─────▼──────────────────┐
│ L1 采集 (Swift)  │ │ L2 感知 (Py)   │ │ L3 风险 (C++17)        │
│ ARKit RGB+Depth  │ │ 分割/检测/融合 │ │ 纯函数、无 IO、无模型  │
└───────────┬──────┘ └───────┬────────┘ └─────┬──────────────────┘
            │ multipart      │ Perception     │ RiskAssessment
            └───────►┌───────▼────────────────▼───────┐
                     │ L4 后端 (FastAPI + SQLite)      │
                     │ 接入/编排/存储/WS 广播           │
                     └───────────────┬────────────────┘
                                     │ REST + WebSocket
                     ┌───────────────▼────────────────┐
                     │ L5 前端 (Vue3 + TS + Element+) │
                     │ 用户页 / 管理后台               │
                     └────────────────────────────────┘
```

### 3.1 关键分层原则

- **L3 风险引擎不依赖 OpenCV、不依赖 PyTorch、不做文件 IO。** 输入 JSON、输出 JSON。这样它可以在 Windows 上仅用 MSVC 构建，且有确定的单元测试。
- **L2 感知不知道后端存在。** 提供 `replay` 模式：从磁盘帧目录跑出与在线完全相同的 `PerceptionResult`，用于离线回归。
- **L1 采集不知道风险模型存在。** 只负责"采样 → 编码 → 可靠上传"。
- **L4 是唯一编排者**，持有队列、重试、降级。

---

## 4. 模块依赖关系

### 4.1 构建期依赖（DAG，无环）

```
                    contracts (schema + vectors)
                   ╱       │          │         ╲
              ios        perception   risk_engine  frontend(api client)
                ╲          │            ╱
                 ╲         │           ╱
                  ─────► backend ◄────
```

| 模块 | 依赖 | 被谁依赖 | 语言/工具 |
|---|---|---|---|
| `contracts` | 无 | 全部 | JSON Schema 2020-12 |
| `risk_engine` | contracts, nlohmann/json | backend | C++17 / CMake |
| `perception` | contracts, torch, opencv, numpy | backend | Python 3.11 / uv |
| `ios` | contracts | — | Swift 6 / Xcode |
| `backend` | contracts, perception, risk_engine | frontend | Python / FastAPI |
| `frontend` | contracts (生成的 TS 类型) | — | Node / Vite |

> 依赖方向单向：**contracts 永不被反向修改**；改协议必须走 §5.4 的版本流程。

### 4.2 运行期数据流（单帧）

```
[iPhone] --multipart POST--> /api/v1/ingest/frame
                                  │ 落盘 frame_bundle（原始证据，可重放）
                                  ▼
                            frame_queue (有界，丢最旧)
                                  ▼
                          perception.pipeline
                           ├─ decode RGB/Depth/Conf
                           ├─ road_seg  → mask
                           ├─ rock_det  → boxes
                           ├─ depth     → 点云 + 路面平面拟合
                           └─ fusion    → 米制侵入量
                                  ▼ PerceptionResult.json
                          risk_engine (subprocess / pybind11)
                                  ▼ RiskAssessment.json
                          store(SQLite) + artifacts(overlay.png)
                                  ▼
                          WebSocket broadcast ──► [Vue 前端]
```

**降级路径必须显式**：感知超时 / 引擎崩溃 / 队列溢出时，后端推送 `status=DEGRADED` 事件，前端横幅提示，**不得沿用上一帧的 PASSABLE**。

---

## 5. 数据协议（项目的真正地基）

### 5.1 三条核心消息

| 消息 | 方向 | 关键字段 |
|---|---|---|
| `FrameBundle` | iOS → backend | `frame_id`(uuid) `device_id` `session_id` `seq` `ts_capture` `intrinsics{fx,fy,cx,cy,w,h}` `pose{…}` `depth_meta{scale,w,h,unit,aligned_to_rgb}` + 二进制：JPEG / depth(uint16 mm) / confidence(uint8) |
| `PerceptionResult` | perception → risk_engine | `frame` `road{status,sectors[],mask_uri,coverage}` `rocks[]` `intrusions[]` `quality{…}` `source: LIVE\|REPLAY\|SIMULATED` |
| `RiskAssessment` | risk_engine → backend | `per_direction[]` `alternative_passage{}` `alerts[]` `reasons[]` `disclaimer_version` |

### 5.2 传输格式

- **上传用 `multipart/form-data`**（元数据 JSON part + 3 个二进制 part），不用 base64（+33% 带宽）。
- **模块间用 JSON 文件/stdio**，不用 pickle / 不用自定义二进制。
- 深度统一 **uint16 毫米**、`0 = 无效`。禁止用 0 冒充"很近"。
- 所有消息带 `schema_version`（`MAJOR.MINOR`），MAJOR 变更即不兼容。

### 5.3 带宽预算（估算，需实测）

JPEG 1280×960 q0.72 ≈ 200 KB；depth 256×192×2 ≈ 96 KB；conf 256×192 ≈ 49 KB。
10 fps ⇒ **≈ 3.5 MB/s**，5 GHz Wi-Fi 可承受；需在 P2 用真机验证，并准备降帧策略。

### 5.4 协议演进规则

1. 加字段必须可选且有默认值。
2. 删/改语义必须升 MAJOR，并保留旧样例于 `contracts/samples/`。
3. `contracts/validate.py --all` 必须能校验所有样例；CI 卡这一步。

---

## 6. 感知模块设计要点

### 6.1 落石检测为什么必须"双通道"

| 通道 | 得到什么 | 单独使用的问题 |
|---|---|---|
| RGB 语义/检测 | 类别、可信度、边界 | 无绝对尺度；"是不是占了路"判断不了 |
| 深度几何 | 米制高度、占地、离地间隙 | 无类别；坑洞、阴影、坡度误报 |

**融合判据**：RGB 框内 + 深度残差（相对拟合路面平面）> τ 的体素簇 ⇒ 判为"立体障碍物"。
只满足一边 ⇒ `degraded`，不得升级为 `BLOCKED`。

### 6.2 YOLOP 的定位（必须说清的现实）

- YOLOP = 可行驶区分割 + 车道线 + 车辆检测（BDD100K 预训练）。**没有"落石"类**。
- 沙盘为玩具/微缩场景，与 BDD100K 域差异极大，**零样本大概率不可用**。
- 因此：YOLOP 只作为 **road_seg 的基线**，用沙盘实拍数据微调；车道线 head 裁剪掉；落石检测另训小模型（YOLOv8n/YOLO11n 级别即可）。
- 若微调后 mIoU 不达标，退路是自训轻量 U-Net（沙盘场景类别少，数据量需求小）。

### 6.3 为何"沙盘"反而有利

80×60 cm 场景、固定机位、可控光照 ⇒ 可采集高质量小数据集，且**深度可做米制标定**（用直尺量已知台阶高度做验收）。这在真实公路上做不到。

### 6.4 分辨率可行性（估算，待实测）

iPhone 12 Pro LiDAR `sceneDepth` ≈ 256×192。相机距沙盘 0.5 m、水平视场约 69° ⇒ 覆盖宽度 ≈ 0.68 m。
⇒ 横向 **≈ 2.7 mm/像素**，纵向同量级。2–3 cm 碎石对应 8–11 像素，**几何上可检**。
但 LiDAR 近距下限与精度需实测（见 §11 R2）。

---

## 7. 风险评估模型

### 7.1 坐标与道路模型

不用"车道线 + 直线中心线"，改用 **路段图（road graph）**：

1. **标定阶段**（人工一次）：在沙盘上放置 2 个已知间距标记，点击 4 个路缘参考点，得到 `road_frame`（原点、切向、宽度、A/B 端）。
2. 由 road mask + 深度重建路面点云，在 `road_frame` 内拟合中心线与宽度。
3. U 形弯按弧长离散为 **N 个区段（sector）**，每段带 `{弧长区间, 宽度, 方向}`。
4. 方向 = 区段序列的两种遍历序：`A→B`、`B→A`。

> 这样"双向双车道无标线"被建模为**两条共享路面的有向通行链**，而不是两条车道。

### 7.2 状态机

每个方向独立输出：

```
        ┌──────────┐   覆盖率不足/深度缺失/分割失败
        │ UNKNOWN  │◄──────────────────────────────┐
        └────┬─────┘                               │
             │ 证据充分                            │
        ┌────▼─────┐  剩余宽度 ≥ 车宽+余量   ┌─────┴──────┐
        │PASSABLE  │◄──────────────────────┤ RESTRICTED │
        └────┬─────┘                        └─────┬──────┘
             │ 剩余宽度 < 车宽                     │ 剩余宽度 < 0
        ┌────▼──────────────────────────────────▼──┐
        │              BLOCKED                     │
        └──────────────────────────────────────────┘
```

- 状态迁移带**迟滞**（进入需连续 3 帧，退出需连续 5 帧），防抖。
- 任何输入质量下降 ⇒ 立即回到 `UNKNOWN`（快降级、慢恢复）。

### 7.3 替代通行空间

`available=true` 必须同时满足：

1. 目标区段属路面 mask 内（不是路肩/山体）；
2. 该区域深度有效率 ≥ 阈值；
3. 3D 点云在路面上方无高于 τ 的障碍（不是"空白"就算通）；
4. 可行宽度 ≥ 车宽 + 安全余量；
5. 该区段在**可视视距内**（`visibility_horizon_m ≥ required_lookahead`）；
6. 对向交通状态可确认（`oncoming = CONFIRMED_NONE_VISIBLE`）；
7. 以上在最近 K 帧稳定成立。

任一条不满足 ⇒ `available = null`（无法判定）或 `false`，**绝不返回 true**。

### 7.4 对向交通

单相机 + 盲弯 ⇒ 只能确认"可视弧内无来车"。因此枚举值：

`PRESENT` / `CONFIRMED_NONE_VISIBLE` / `UNVERIFIABLE`

`UNVERIFIABLE` 时 `alternative_passage.verifiable = false`，前端显示"盲弯，无法确认对向"。

---

## 8. 工程目录

```
mountain/
├── README.md
├── .gitignore
├── docs/
│   ├── 00-architecture.md      # 本文
│   ├── 01-protocol.md          # 协议细则 + 样例
│   ├── 02-risk-model.md        # 判定规则 + 阈值表
│   ├── 03-calibration.md       # 沙盘标定 SOP
│   ├── 04-deployment-windows.md
│   └── adr/                    # 架构决策记录（为什么这么做）
├── contracts/
│   ├── schemas/                # frame_bundle / perception_result / risk_assessment
│   ├── samples/                # 样例（simulated 的必须标 source=SIMULATED）
│   ├── gen_ts.py               # 生成前端 TS 类型
│   └── validate.py             # 跨语言一致性测试向量
├── ios/
│   └── RockRoadCapture/
│       ├── App/
│       ├── Capture/            # ARSessionManager, DepthSampler, IntrinsicsResolver
│       ├── Transport/          # MultipartEncoder, UploadQueue, RetryPolicy
│       ├── Model/              # 与 contracts 对应的 Codable
│       └── UI/
├── perception/
│   ├── pyproject.toml
│   ├── src/perception/
│   │   ├── device.py           # Mac MPS / Win CPU / CUDA 统一选择
│   │   ├── config.py
│   │   ├── models/road_seg.py, rock_det.py
│   │   ├── depth.py            # 解码、对齐、点云、平面拟合
│   │   ├── geometry.py         # road_frame、sector、投影
│   │   ├── fusion.py
│   │   └── pipeline.py
│   ├── tools/                  # collect / label / eval / replay
│   └── tests/
├── risk_engine/
│   ├── CMakeLists.txt
│   ├── include/rock_risk/{types,road_model,assessor,json_io}.hpp
│   ├── src/{assessor,road_model}.cpp
│   ├── cli/main.cpp            # stdin JSON → stdout JSON
│   ├── bindings/py_module.cpp  # 可选 pybind11
│   └── tests/                  # 与 contracts/samples 共用测试向量
├── backend/
│   ├── pyproject.toml
│   └── app/{main.py, api/, ws/, db/, services/, settings.py}
├── frontend/
│   ├── package.json            # pnpm
│   └── src/{api/, views/, components/, stores/}
├── scripts/
│   ├── bootstrap_mac.sh
│   ├── bootstrap_windows.ps1
│   ├── start_windows.ps1 / start_windows.bat
│   ├── build_risk_engine.sh / .ps1
│   └── check_env.py
└── data/                       # 运行时产物，git 忽略
    ├─frames/  artifacts/  db/  logs/  models/
```

---

## 9. 开发顺序

采用"**契约先行 + 两条并行高风险轨道**"。理由：risk_engine 是安全核心且完全可离线测试；iOS 是硬件不确定性最大的一环，两者互不阻塞。

| 阶段 | 内容 | 出口准则（必须可运行验证） | 依赖 |
|---|---|---|---|
| **P0** | 环境与骨架：git init、目录、contracts v1 schema、env 检查脚本 | `python scripts/check_env.py` 明确报出缺什么；`python contracts/validate.py --all` 通过 | — |
| **P1a** | C++ 风险引擎：类型、road_model、assessor、CLI | 手写 JSON 向量驱动全部单测通过；CMake 在 macOS 构建成功 | P0 |
| **P1b** | iOS 采集端：ARKit 会话、深度采样、multipart 上传 | 真机连续 60 s 上传，后台收到 300+ 帧，字段完整 | P0 |
| **P2** | 后端最小闭环：ingest + 落盘 + 帧回放 | 断网重连不丢帧序；`replay` 能重放已存帧 | P1b |
| **P3** | 感知-几何（**无 ML**）：深度解码、点云、平面拟合、米制校验 | 用直尺量台阶高度，误差 < 目标阈值（实测报告） | P2 |
| **P4** | 感知-语义：数据采集 → 标注 → YOLOP 微调 → 落石检测器 | 留出集 mIoU / mAP 报告；**明确对比零样本基线** | P2 |
| **P5** | 融合 + 接入引擎，全链路贯通 | 端到端 JSON 产物符合 schema；离线回放可复现 | P1a,P3,P4 |
| **P6** | 后端完善：SQLite、事件、设备心跳、WS | 断线重连、历史分页、降级事件有测试 | P5 |
| **P7** | 前端：用户页 + 管理后台 | 实时刷新、历史查询、SIMULATED 横幅 | P6 |
| **P8** | Windows 部署 + 沙盘标定 + 实机验证 | **在 Windows x86-64 上真实跑通并留证** | 全部 |

**P1a 与 P1b 建议并行**（一个写 C++、一个调硬件）。
**P8 不可省，也不可"推断通过"**：没有 Windows 机器就无法声明完成（见 §11 R1）。

---

## 10. 跨平台策略

### 10.1 Python 版本

| 平台 | 建议 | 说明 |
|---|---|---|
| Mac ARM64（开发） | 3.11（uv 安装） | torch/opencv 轮子最稳；3.13 在老代码里易踩坑 |
| Windows x86-64（部署） | 3.11 | 同一 `pyproject`，锁文件分平台 |

统一 `uv sync --extra cpu` / `--extra cuda` 两种 extra，避免"Mac 能跑 Win 不能装"。

### 10.2 CUDA / CPU

- `perception/device.py`：`cuda → mps → cpu` 顺序探测，支持 `ROAD_DEVICE` 环境变量强制覆盖。
- MPS 有算子缺失，需 `PYTORCH_ENABLE_MPS_FALLBACK=1`；**把 fallback 视为性能告警而非正常**。
- Windows 无 NVIDIA 时必须装 **CPU 版 torch**：`--index-url https://download.pytorch.org/whl/cpu`（默认 Windows 轮子会拉 CUDA 依赖，体积巨大且无卡不可用）。
- risk_engine 全程 CPU、无第三方数值库依赖 ⇒ Windows 无 GPU 无压力。

### 10.3 路径与配置

- 禁止绝对路径：Python 用 `pathlib.Path(__file__).parents`，C++ 用 `std::filesystem::path`。
- 所有可变参数进 `config/*.yaml` + `ROAD_*` 环境变量覆盖。
- 前端 `vite build` 产物由 FastAPI 静态托管 ⇒ 生产环境同源，无 CORS。

### 10.4 iOS 侧注意

- ATS 默认阻断明文 HTTP ⇒ 需 `NSAllowsLocalNetworking`；iOS 14+ 还需 `NSLocalNetworkUsageDescription` 与用户授权。
- 免费签名 7 天过期，需定期重签。
- 手机与主机须同网段；**部署到 Windows 时手机直连 Windows 即可**，架构上无需改动。

---

## 11. 风险登记册

| ID | 风险 | 影响 | 缓解 |
|---|---|---|---|
| **R1** | ~~无 Windows 实机~~ **已确认具备**（用户确认） | 无阻塞 | P8 直接实机验证；仍须逐阶段留存运行证据 |
| **R2** | LiDAR 近距（0.4–0.8 m）精度未知 | 米制结论可能不可用 | P3 用直尺标定实测；不足则改用结构光/单目+已知尺寸补偿 |
| **R3** | `sceneDepth` 与 `capturedImage` 对齐方式 | 投影错位 → 假侵入 | P1b 实测校验；把 `aligned_to_rgb` 做成协议字段而非硬编码假设 |
| **R4** | YOLOP 域差异过大 | 道路分割不可用 | P4 设"零样本基线 vs 微调"对比，不达标即换自训 U-Net |
| **R5** | 落石无公开数据集 | 检测器无训练数据 | 沙盘自采 + 半自动标注（深度残差预筛） |
| **R6** | 单相机盲弯 | 对向交通不可知 | 架构上已用 `UNVERIFIABLE` 建模，不做超视距承诺 |
| **R7** | 玩具车/微缩场景外观差异 | 泛化差 | 固定机位 + 固定光照，v1 不追求跨场景泛化 |
| **R8** | 手机与 PC 时钟不同步 | 延迟统计失真 | 用 `seq` 排序；延迟用 ping 估算偏移，不直接相减 |

---

## 12. 已确认决策与遗留问题

| # | 决策 | 结论 |
|---|---|---|
| 1 | Windows 实机 | **具备**，P8 可在实机验证并留证 |
| 2 | 训练数据来源 | 用户倾向**复用公开数据集** → 见 §6.5 的现实约束，采用"公开数据预训练 + 沙盘实拍微调 + 深度几何兜底" |
| 3 | Node.js | 已以**工程内便携版**（v24.22.2）安装，不污染系统 |
| 4 | CMake | 已随工具 venv 安装（4.4.4）；Windows 侧需另装 CMake + MSVC Build Tools |

遗留待确认：是否需要多机位/多设备（影响 `device_id` 与状态聚合）；公开数据集的具体 license 是否允许内部使用。

### 6.5 公开数据集的可用性与域差异（重要）

检索到的公开落石/滑坡数据资源（[Kaggle: Rockfall Detection on Mars and Moon](https://www.kaggle.com/datasets/chujianta/rockfall-detection-on-mars-and-moon)、[Roboflow Universe: Rockfall Detection](https://universe.roboflow.com/manaswis-workspace/rockfall-detection-dkv8f)、以及若干 VOC+YOLO 格式的落石/滑坡数据集，如 958 张 3 类、19151 张 4 类）。

**共性风险**：这些数据几乎都是真实山区、航拍或远景拍摄，与 80×60 cm 桌面沙盘在**尺度、视角、光照、背景纹理、类别定义**上差异极大。直接套用几乎必然在沙盘上失效，且失效时可能表现为"一部分落石被检出、一部分完全漏检"——这比完全检不出更危险。

**因此的用法**（按优先级）：

1. **预训练**：用公开数据训练骨干或类别先验，作为沙盘微调的初始化。
2. **基线对比**：必须实测"公开权重零样本 → 沙盘"的成绩并留存报告；这是判断"复用公开数据是否够用"的唯一客观依据。
3. **不可替代**：最终模型必须在沙盘实拍数据上验证；哪怕只有 100–200 张实拍，也显著优于纯公开数据。
4. **兜底**：无论 RGB 模型表现如何，深度几何通道（P3）都必须独立成立，不能把可用性押在检测器上。

**许可**：使用前逐个核对 license 与再分发条款；来源不明或转售性质的数据集只记录获取方式，不纳入仓库。
