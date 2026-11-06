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

## 当前状态

| 模块 | 状态 | 说明 |
|---|---|---|
| 契约与校验 | ✅ | 5 个 JSON Schema + 语义不变量，12/12 通过 |
| C++ 风险引擎 | ✅ | 46 项测试；fail-closed 判定规则 |
| iOS 采集端 | ✅ | M1–M7；真机 1086 帧零丢失；热自适应降级；朝向自动转正 |
| Python 感知 | 🟡 | 71 项测试通过；**道路分割在俯视场景不适用，需微调** |
| FastAPI 后端 | ✅ | 14 项测试；推理与后端解耦；文件存储 + SQLite + WebSocket |
| Vue 前端 | ✅ | 用户端 + 管理后台 5 页；M9 真机待验 |
| 沙盘训练管线 | ✅ | 合成数据冒烟通过（IoU 0.96），真实数据未到 |
| 落石检测器 | ❌ | 无可用权重 → 系统输出 `UNKNOWN` |
| Windows 部署 | ❌ | 脚本已写，**从未在 Windows 上执行** |

测试总览：Python 85 项 · C++ 46 项 · 契约 12/12。

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
risk_engine/   C++17 风险评估引擎
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
6. 未检测到落石 ≠ 安全，还需观测覆盖率达标
7. 所有结论必须携带 `reasons[]` 与 `disclaimer_version`

对应规则编号见 `contracts/validate.py` 中的 `P*` / `R*` / `C*`。

---

## 已知限制

1. **没有落石检测器** → 系统恒为 `DEGRADED`，结论恒为 `UNKNOWN`。这是设计，不是 bug。
2. **YOLOP 在俯视机位结构性不适用**（训练自前视行车视角），沙盘需自训练 U-Net，
   见 [拍摄几何](docs/12-camera-geometry.md)。
3. **深度值的米制精度未标定**（只验证了几何对齐，见 [感知](docs/03-perception.md)）。
4. **风险引擎阈值是占位值**（`vehicle_width_m` 等），需沙盘实测后替换。
5. **Windows 部署未验证**；`build_risk_engine.ps1` 与 PowerShell 步骤均未实机执行。
6. **前端未做真机/多浏览器确认**，清单见 [M9 验收](docs/11-m9-checklist.md)。
7. `contracts/samples/` 中标 `source: SIMULATED` 的均为**构造样例**，不是模型输出。

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
| [前端](docs/08-frontend.md) | 页面结构、枚举、构建 |
| [M9 验收](docs/11-m9-checklist.md) | 跨端逐项检查清单 |
