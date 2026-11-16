# 交接说明 · 山区公路落石检测与通行预警系统

> 更新：2026-11-16 20:45　距交付（11/20）4 天　当前 80 次提交
> 本文只写**实际核对过**的状态，未验证的一律标注。

---

## 0. 先读这三段

1. **系统回答的是「路还能不能过」，不是「这是不是落石」。**
   障碍不做类别识别，石块/碎石/土块/树枝统一按"道路障碍物"处理。
2. **全链路已跑通，但从未在沙盘上验证过。** 现有真实数据来自一张木桌。
3. **3–5 mm 小颗粒物理上测不到**（LiDAR 光斑大于颗粒），这是硬件限制，不是参数问题。

---

## 1. 目标与验收

**输入**：iPhone RGB-D 俯视拍摄（沙盘 80×60 cm，道路单车道 4 cm、双向 8 cm）
**输出**：风险等级 + 双向通行性 + 剩余可通行厘米数 + 证据来源，经 Web/App 展示

完整链路：

```
iPhone RGB-D → 道路/可通行区 → 障碍 → 侵入判定 → 剩余宽度 → C++ 风险引擎 → Web / iOS
```

**真正的验收标准**：给一组含道路、障碍、深度的真实场景，系统能识别、能判风险、
证据不足时输出 `UNKNOWN`，并在页面上正确展示。
**不是**某个模型的 IoU，也不是代码行数。

---

## 2. 当前状态（2026-11-16 实测）

| 模块 | 状态 | 证据 | 未验证 |
|---|---|---|---|
| 契约 | ✅ | 5 Schema，`validate.py` 12/12 | — |
| C++ 风险引擎 | ✅ | 46/46 测试；支持 `--policy` | 真实道路场景 |
| Python 感知 | ✅ | 99 项测试（含 backend） | 沙盘域 |
| 道路分割 | 🟡 | AeroScapes expE，test Road IoU **0.7411** | 沙盘/桌面 |
| RGB-D 几何障碍 | ✅ | 972 帧实采：σ=1.42 mm，≥8 mm 可检出 | 沙盘域 |
| 风险标定 | ✅ | 单车道 4 cm（`sandbox_4cm_lane.json`） | — |
| FastAPI 后端 | ✅ | 15 路由，14 测试，推理完全解耦 | 压测 |
| Web 前端 | ✅ | 监控大屏布局，显示剩余厘米数 | 多浏览器 |
| iOS App「山行智安」 | 🟡 | SwiftUI 原生，iPhone 真机跑通；模拟器与**真机 SDK** 均编译通过 | iPad 8 上的横竖屏/分屏、实时增量 |
| Windows 部署 | ❌ | 仅 `build_risk_engine.ps1` | **从未执行** |

**当前默认配置**（已核对运行中的后端）：

```
road_model      aeroscapes_unet_512_bs8_expE.pt
obstacle_mode   geometric
road_source     plane          ← ⚠️ 见 §5
risk_policy     sandbox_4cm_lane.json
```

---

## 3. 架构与关键文件

```
iPhone (ARKit) ──HTTP──► FastAPI（落盘+入队，不加载模型）
                              │
                        AI worker（模型在这里）
                         ├─ 道路分割 U-Net / 平面
                         └─ 几何障碍（深度）
                              │ POST perception.json
                              ▼
                         C++ 引擎（--stream 常驻）→ SQLite
                              │ WebSocket / 轮询
                              ▼
                        Web 页面 / iOS App
```

| 关注点 | 文件 |
|---|---|
| 感知主流程 | `perception/src/perception/pipeline.py` |
| 几何障碍 | `perception/src/perception/obstacles/geometry.py` |
| 侵入判定 | `perception/src/perception/geometry/intrusion.py` |
| 风险规则 | `risk_engine/src/assessor.cpp` |
| 策略标定 | `risk_engine/policy/sandbox_4cm_lane.json` |
| 数据契约 | `contracts/schemas/perception_report.schema.json` |
| 后端 | `backend/app/{main,risk,db,config}.py` |
| Web 页面 | `frontend/src/views/user/HomeView.vue` |
| iOS | `ios/ShanXingZhiAn/ShanXingZhiAn/*.swift` |

---

## 4. 怎么跑 / 怎么验证

```bash
bash scripts/start_all.sh      # 后端 + AI worker
bash scripts/status.sh         # 看状态
bash scripts/stop_all.sh

# 回归
perception/.venv/bin/python -m pytest perception/tests backend/tests -q   # 99
risk_engine/build/rock_risk_tests                                        # 46/46
perception/.venv/bin/python contracts/validate.py --all                  # 12/12

# 不接采集的链路演示（合成深度）
perception/.venv/bin/python scripts/demo_geometric_obstacle.py \
  --image data/aeroscapes/processed/images/test/002000_010.jpg \
  --mask  data/aeroscapes/processed/masks/test/002000_010.png \
  --weights perception/weights/aeroscapes_unet_512_bs8_expE.pt --bump-mm 30

# iOS 编译
cd ios/ShanXingZhiAn && xcodebuild -project ShanXingZhiAn.xcodeproj \
  -target ShanXingZhiAn -sdk iphonesimulator -configuration Debug \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO build
```

**手机端**：`scripts/start_all.sh` 会打印局域网地址（如 `http://172.20.10.4:8000`），
App 里填这个地址即可。

---

## 5. 关键决策（推翻前请先读文档）

| 决策 | 原因 | 文档 |
|---|---|---|
| 放弃"落石类别检测" | 公开数据集全是火星/月球或露天矿；无可用许可数据 | `docs/19` |
| 改用 RGB-D 几何异常 | 零标注；σ=1.42 mm，8 mm 即 5.6σ；CPU 37–43 ms | `docs/20` `docs/22` |
| 保留"落石"字样但语义泛化 | `rockfalls[]` 17 字段中只有 `label` 与类别有关 | `docs/20 §2` |
| 单车道 4 cm 作为通行阈值 | 沙盘不放小车，用"一车道"代替车宽 | `docs/22 §9` |
| iOS 用原生 SwiftUI | 第一版是 WKWebView 壳，可控性不足；用户要求重写 | `docs/23` |
| `snapshot` 不算实时 | 网页端早期踩过：历史数据被当成实时，显示"刚刚" | `docs/23 §3` |

---

## 6. 十个坑（按踩到的频率）

1. **`ROAD_ROAD_SOURCE=plane` 只适用于固定俯视**。它把整片平整地面当可通行区；
   沙盘里道路 4 cm、两侧桌面也平，会判成"整张桌子都能开"。**沙盘到位后必须换 `road_model`。**
2. **采集帧只保留 0.5 小时**（`ROAD_ARTIFACT_HOURS`）。972 帧验证数据已被清理，仅剩 14 帧。
   **重要采集先备份**，否则 `docs/22` 的结论无法复现。
3. **契约 `model` 块 `additionalProperties: false`**：多加字段会被 422 拒绝；
   被拒的帧曾导致 worker 无限重试（已修，但新增字段务必先看 Schema）。
4. **后端禁止 `import perception`**，有测试守护；模型只能在 worker 里。
5. **`start_all.sh` 不 source `env.sh`**：改 `ROAD_*` 变量要改 `start_all.sh` 或导出环境变量。
6. **AeroScapes 是 5–50 m 航拍**，与 0.39 m 桌面差异极大；0.7411 不能外推到沙盘。
7. **几何检测只认"凸起"**：薄片、贴地石块、透明物体不触发。
8. **贴边物体被抑制**（边框 2 px）：画面边缘的真实物体可能不报。
9. **Swift 编译器在 `if let x`（与属性同名）处断言崩溃**，不是语法错误，改个绑定名即可。
10. **`data/aeroscapes` 829 MB**（原始 789 MB），已被 gitignore，换机器要重新准备。

---

## 7. 未完成与优先级（4 天）

| P | 事项 | 说明 | 阻塞 |
|---|---|---|---|
| **P0** | iPad 真机验证 | 适配已写，设备（iPad 8）可用但尚未运行 | 无（可在 Xcode 直接跑） |
| **P0** | 实时增量验证 | App 当前"已收 0 条"，只验证了轮询兜底 | 需采集端同时开 |
| **P0** | 沙盘采集 → 道路模型微调 | 卡在沙盘本身；`docs/21` 有拍摄规范 | **需沙盘** |
| P1 | 答辩材料 / 演示脚本 | 可用 `demo_geometric_obstacle.py` | — |
| P2 | 双向 `SHARED_CORRIDOR` 改造 | A→B 与 B→A 恒相同；方案见 `docs/18 §C.2` | — |
| P3 | Windows 部署 | 用户已明确暂缓 | 需机器 |

---

## 8. 环境

| 项 | 值 |
|---|---|
| 开发机 | Apple M5 / 16 GB / macOS |
| Python | `perception/.venv`（3.11） |
| Node | `.tools/node`（构建前端需加 PATH） |
| 磁盘 | 可用 128 GiB |
| 数据集 | AeroScapes 829 MB（`data/aeroscapes`） |
| 权重 | `perception/weights/*.pt`（gitignore，不随仓库分发） |

---

## 9. 交接检查清单

接手后按顺序做：

- [ ] `bash scripts/start_all.sh`，打开 `http://127.0.0.1:8000` 确认页面出数
- [ ] 跑三组回归（99 / 46 / 12）
- [ ] 读 `docs/22` 弄清"已证明 / 未证明"的分界
- [ ] 读 `docs/23 §3` 弄清 snapshot 与 assessment 的区别
- [ ] 确认 `ROAD_ROAD_SOURCE` 的当前取值，想清楚是否适合当前场景
- [ ] 若要重跑验证：先确认采集帧是否还在（大概率已被清理）

---

## 10. 一句话给下一位

**这套系统已经能"看见凸起、算剩余宽度、给出受限/阻断"，但它还没有在沙盘上被证明过。**
所有结论的下限都在 `docs/22 §6`——那里写着这次验证证明了什么、没证明什么。
