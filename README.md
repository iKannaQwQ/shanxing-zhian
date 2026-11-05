# 山区公路落石检测、通行风险评估与预警系统

> 沙盘模拟 + iPhone 12 Pro (LiDAR) 采集 + 跨平台 (macOS ARM64 开发 / Windows x86-64 部署)。
> **系统输出为实验性辅助评估，不构成任何真实道路驾驶指令。**

## 当前状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | 工程骨架、契约 schema、契约校验器、环境自检 | ✅ 完成 |
| P1a | C++ 风险评估引擎 | ✅ 完成，46 项测试（见 [风险引擎](docs/04-risk-engine.md)） |
| P1b | iOS ARKit 采集端 | 🟡 M1 已验证；M2-a 已真机确认；M2-b 已真机校验；M2 采集完成；M3 伪彩色待确认；热压力已确认需降帧策略（见 [iOS 采集端](docs/05-ios-capture.md)） |
| P2 | 后端最小闭环 | ⬜ 未开始 |
| P3 | 感知-深度几何 | ⬜ 未开始 |
| P4 | 感知模块 M1–M7 | 🟡 代码完成、49 项测试通过，**模型未在沙盘验证**（见 [感知模块](docs/03-perception.md)） |
| P5 | 融合 + 全链路 | ⬜ 未开始 |
| P6 | 后端完善（DB/WS/事件） | ⬜ 未开始 |
| P7 | 前端（用户页 + 管理后台） | ⬜ 未开始 |
| P8 | Windows 部署与实机验证 | ⬜ 未开始 |

详细设计与阶段出口准则见 [docs/00-architecture.md](docs/00-architecture.md)。

## 快速开始（macOS）

```bash
bash scripts/bootstrap_mac.sh      # 工程内自包含安装 Node / cmake / Python3.11 工具环境
source scripts/env.sh              # 激活 PATH
python scripts/check_env.py        # 环境自检
python contracts/validate.py --all # 契约 + 安全不变量校验
```

工具链全部落在 `.tools/`（已 gitignore），不写 `~/.local`、不需要 sudo。

## 目录

```
contracts/     JSON Schema + 样例 + 校验器（跨语言唯一真源）
docs/          架构、协议、风险模型、标定、Windows 部署
ios/           Swift + SwiftUI + ARKit 采集端（未实现）
perception/    Python 感知（未实现）
risk_engine/   C++17 风险评估引擎（未实现）
backend/       FastAPI + SQLite（未实现）
frontend/      Vue 3 + TS + Element Plus（未实现）
scripts/       环境引导、启动、自检
data/          运行时产物（gitignore）
```

## 安全边界（写进校验器，可被 CI 拦截）

1. 道路分割失败 → 一切方向 `UNKNOWN`
2. 深度有效率不足 / 配准失败 → 禁止米制结论
3. `depth_source=NONE` 的落石不得参与阻断判定
4. 盲弯超视距 → 替代通行 `verifiable=false`
5. 仅"道路空白"不构成可绕行证据
6. 未检测到落石 ≠ 安全，还需观测覆盖率达标
7. 所有结论必须携带 `reasons[]` 与 `disclaimer_version`

对应规则编号见 `contracts/validate.py` 中的 `P*` / `R*` / `C*`。

## 已知限制

- 尚未采集任何真实沙盘数据；`contracts/samples/` 中标 `source: SIMULATED` 的均为**构造样例**，不是模型输出。
- Windows 启动脚本与会话尚未编写（属 P8，需在 Windows 实机验证后才可宣称可用）。
- iOS 模拟器无运行时，且 ARKit/LiDAR 本就不支持模拟器 → 采集端必须真机调试。
- YOLOP 原版只检测"车辆"一类，**不含落石**；落石检测器为独立模型，用公开数据集训练，未在沙盘验证。
- 尚无沙盘数据，感知侧所有落石结论仅为工程链路验证。
- iOS 采集端只完成 M1（能力检查，已真机验证）；RGB/深度采集与上传尚未实现。
