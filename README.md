# 山区公路落石检测、通行风险评估与预警系统

> 80×60 cm 桌面沙盘 + iPhone（LiDAR）俯视采集 + macOS 开发 / Windows 部署（待验证）。
> **系统输出为实验性辅助评估，不构成任何真实道路驾驶指令。**

---

## 怎么跑

**双击即可**（项目根目录）：

| 文件 | 作用 |
|---|---|
| `启动系统.command` | 启动服务并打开浏览器 |
| `停止系统.command` | 停止全部服务 |
| `查看状态.command` | 查看当前状态 |

或者用命令行：

```bash
bash scripts/start_all.sh     # 启动（后端 + AI worker）
bash scripts/status.sh        # 看状态
bash scripts/stop_all.sh      # 停止
```

完整步骤（手机端、排障、数据清理）：**[运行手册](docs/13-runbook.md)**

---

## 现在这套系统到底在做什么

**不再是"识别落石是什么"，而是"路面上有没有东西挡住可通行空间"。**

```
U-Net 道路分割 / 深度平面  →  可通行区
        +
RGB-D 几何异常检测        →  高出地面的障碍
        ↓
侵入判定（接地区域求交、最窄剩余宽度）
        ↓
C++ 风险引擎（fail-closed）
        ↓
Web 页面：风险等级 + 双向通行性 + 剩余厘米数 + 证据来源
```

障碍**不做类别识别**：石块、碎石、土块、树枝在业务上统一按"道路障碍物"处理。
这条路线的依据是 `docs/20` 的方案对比，以及 `docs/22` 的真实数据验证。

---

## 当前状态

| 模块 | 状态 | 说明 |
|---|---|---|
| 契约与校验 | ✅ | 5 个 JSON Schema + 语义不变量，12/12 通过 |
| C++ 风险引擎 | ✅ | 46 项测试；fail-closed；支持 `--policy` 外部标定 |
| iOS 采集端 | ✅ | M1–M7；真机 1086 帧零丢失；热自适应降级；朝向自动转正 |
| 道路分割 | 🟡 | AeroScapes expE，test Road IoU **0.7411**；**未在沙盘验证** |
| RGB-D 几何障碍 | ✅ | 真实数据验证：σ=1.42 mm，可检出 ≥8 mm 凸起，37–43 ms/帧 |
| 风险标定 | ✅ | 沙盘尺度：单车道 4 cm / 双向 8 cm，见 `docs/22 §9` |
| 前端 | 🟡 | 已显示剩余厘米数与证据来源；**未做多浏览器/真机验收** |
| Windows 部署 | ❌ | 脚本已写，**从未在 Windows 上执行** |
| 沙盘目标域 | ❌ | **尚无沙盘数据**；桌面数据已用于方法验证 |

测试总览：Python **99** 项 · C++ **46/46** · 契约 **12/12**。

### 五组道路分割实验（独立测试集 241 张）

| 实验 | 配置 | test Road IoU |
|---|---|---|
| A | 512 / bs4 | 0.6447 |
| B | 512 / bs4 + 增强 | 0.5996 |
| C | 256 / bs8 | 0.6946 |
| D | 256 / bs4 | 0.5813 |
| **E（默认）** | **512 / bs8** | **0.7411** |

数据划分按**视频序列**分组，train/val/test 序列零交集。详见 `docs/15`、`docs/16`。

---

## 架构

```
iPhone (ARKit RGB-D)  --HTTP multipart-->  后端 (FastAPI)
                                              │  落盘 + 入队
                                              ▼
                                        frames(status=stored)
                                              │  轮询
                                              ▼
                                        AI worker（模型在这）
                                         ├─ 道路分割（U-Net / 平面）
                                         └─ 几何障碍（深度）
                                              │  POST 感知结果
                                              ▼
                                        风险引擎 (C++) → SQLite
                                              │
                                        WebSocket 推送
                                              ▼
                                          浏览器
```

**三条硬性边界**（有测试守护）：

1. 后端不加载任何模型（`import perception` 被测试禁止）
2. 网页请求只读库，永不触发推理
3. 大数组走文件，不进 JSON

---

## 目录

```
contracts/     JSON Schema + 样例 + 校验器（跨语言唯一真源）
docs/          架构、协议、风险模型、后端 API、运行手册
ios/           Swift + SwiftUI + ARKit 采集端
perception/    Python 感知库 + 训练工具
risk_engine/   C++17 风险评估引擎（policy/ 下为标定文件）
backend/       FastAPI + SQLite + WebSocket
frontend/      Vue 3 + TS + Element Plus
scripts/       启停、自检、标定、数据准备
data/          运行时产物（gitignore）
```

---

## 安全边界（写进校验器，可被 CI 拦截）

1. 道路分割失败 → 一切方向 `UNKNOWN`
2. 深度有效率不足 / 配准失败 → 禁止米制结论
3. `depth_source=NONE` 的落石不得参与阻断判定
4. 盲弯超视距 → 替代通行 `verifiable=false`
5. 仅"道路空白"不构成可绕行证据
6. 未检测到落石 ≠ 安全，还需观测覆盖率与数据质量达标
7. 所有结论必须携带 `reasons[]` 与 `disclaimer_version`

对应规则编号见 `contracts/validate.py` 中的 `P*` / `R*` / `C*`。

---

## 已知限制

1. **3–5 mm 小颗粒不可见**（物理限制，非参数问题）。
   实测：每深度像素 2.01 mm，颗粒区最大高度 1.3 mm，噪声 σ=1.31 mm。
   LiDAR 光斑大于颗粒，信号被平均进桌面。见 `docs/22 §3.1`。
2. **只认"凸起"，不认"平躺"**：薄片、贴地石块、透明物体不会触发。
3. **`road_source=plane` 只适用于固定俯视**：它把整片平整地面当可通行区，
   真实道路上会把人行道、路肩也算进去。沙盘里若需区分 4 cm 道路，必须用 `road_model`。
4. **道路分割未在沙盘验证**：0.7411 是 AeroScapes 航拍测试集成绩，
   不能外推到桌面/沙盘域。
5. **没有语义分类**：系统不会告诉你"这是落石还是纸箱"，只说"这里有东西高出地面"。
6. **风险阈值按沙盘标定**（单车道 4 cm），换场景需改 `risk_engine/policy/*.json`。
7. **Windows 部署未验证**；`build_risk_engine.ps1` 与 PowerShell 步骤均未实机执行。
8. **前端未做多浏览器/真机确认**，清单见 [M9 验收](docs/11-m9-checklist.md)。
9. `contracts/samples/` 中标 `source: SIMULATED` 的均为**构造样例**，不是模型输出。
10. **双向判定目前共享同一走廊**（`SHARED_CORRIDOR`）：A→B 与 B→A 恒相同。
    改造方案已记录在 `docs/18 §C.2`，尚未实施。

---

## 文档

| 文档 | 内容 |
|---|---|
| [运行手册](docs/13-runbook.md) | 傻瓜版启动 / 停止 / 排障 |
| [架构](docs/00-architecture.md) | 分层、依赖 DAG、阶段出口 |
| [协议](docs/01-protocol.md) | multipart 布局、时间/深度约定 |
| [感知](docs/03-perception.md) | 道路分割、深度融合、侵入判定 |
| [风险引擎](docs/04-risk-engine.md) | 判定规则、门控表、局限性 |
| [iOS 采集](docs/05-ios-capture.md) | 真机验证、热降级、朝向 |
| [iOS 功能总览](docs/14-ios-capture-features.md) | 采集端能力清单与参数 |
| [后端 API](docs/10-backend-api.md) | 接口、WebSocket 协议、数据库 |
| [拍摄几何](docs/12-camera-geometry.md) | 俯视机位、视场计算、安装高度 |
| [沙盘管线](docs/09-sandbox-pipeline.md) | 数据约定、训练、评估、切换模型 |
| [AeroScapes 训练](docs/15-aeroscapes-stage1.md) | 数据集核实、类别解析、划分、训练 |
| [训练结果](docs/16-aeroscapes-training-results.md) | A–E 指标、失效分析、闸门评估 |
| [模型接入](docs/17-aeroscapes-integration.md) | ROAD_ROAD_MODEL 接线、契约扩展、域外风险 |
| [项目审查](docs/18-project-audit.md) | 现状核对、偏离检查、下一阶段计划 |
| [落石数据集调研](docs/19-rockfall-dataset-survey.md) | 候选核查、失败原因、备选路径 |
| [技术路线重估](docs/20-obstacle-route-evaluation.md) | 从识别落石转向可通行空间；方案对比 |
| [目标域采集](docs/21-target-domain-capture.md) | 俯视拍摄规范、场景清单、最小可接受版本 |
| [目标域验证](docs/22-target-domain-validation.md) | 972 帧真实数据、尺度换算、策略标定 |
| [前端](docs/08-frontend.md) | 页面结构、枚举、构建 |
| [M9 验收](docs/11-m9-checklist.md) | 跨端逐项检查清单 |
