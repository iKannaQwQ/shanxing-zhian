# 24 · 汇报用合成沙盘演示数据

> 目的：在真实沙盘采集/微调完成前，先有一套**可复现、可讲解、可截图**的演示材料。
> 生成日期：2026-11-18。**输入是合成的，感知与风险引擎是真实代码。**
> 生成脚本：`scripts/make_demo_sandbox.py`
> 输出目录：`data/demo_sandbox/`（gitignore）

---

## 1. 为什么需要它

沙盘尚未完成采集，但汇报材料需要：
- 能讲清"RGB-D → 道路 → 障碍 → 剩余宽度 → 风险"的完整链路；
- 能看到 SAFE / RESTRICTED / BLOCKED 三种结果的对比；
- 能说明**已知盲区**（5 mm 薄片）和负样本（阴影）不误报。

这套数据只解决"材料"问题，**不解决"能力"问题**。

---

## 2. 合成什么、真实什么

| 部分 | 来源 | 说明 |
|---|---|---|
| RGB | 合成 | 80×60 cm 沙盘俯视，5 cm 双向路、沙地纹理、石块/木板/薄片 |
| 深度 | 合成 | 0.64 m 平面 + σ=1.5 mm 噪声 + 按场景放置的凸起 |
| 道路掩码 | **真值** | 直接给出 5 cm 道路带，绕开尚未微调的道路模型 |
| 感知流水线 | **真实代码** | `PerceptionPipeline(obstacle_mode=geometric)` |
| 风险引擎 | **真实代码** | `rock_risk_cli --stream --policy sandbox_2p5cm_lane.json` |
| 判定策略 | 真实 v3 | 单车道 2.5 cm；多石联合空隙低于阈值升级 HIGH |

所有 `perception.json` 的 `source` 都是 `SIMULATED`，Web 页面显示 DEMO 横幅。

---

## 3. 设置

| 项 | 值 |
|---|---|
| 沙盘 | 80 × 60 cm，俯视 0.64 m |
| 道路 | 双向 5 cm，单车道 2.5 cm |
| RGB / 深度 | 1280×960 / 256×192（1 深度像素 ≈ 3.1 mm） |
| 深度噪声 | σ=1.5 mm（与桌面实测同量级） |
| 风险策略 | `risk_engine/policy/sandbox_2p5cm_lane.json`（policy_version 3） |
| 障碍来源 | RGB-D 几何，不使用分类模型 |
| 场景数 | 10（3 个负样本/盲区 + 7 个障碍场景） |

---

## 4. 场景与结果

| # | 场景 | 物理剩余 | 实际剩余 | 实际风险 | 通行性 | 说明 |
|---|---|---|---|---|---|---|
| 01 | 空路面 | 5.0 cm | 5.0 cm | SAFE | 可通行 | 负样本 |
| 02 | 阴影干扰 | 5.0 cm | 5.0 cm | SAFE | 可通行 | 只改颜色，不改深度 |
| 03 | 路肩石块 20 mm | 5.0 cm | 4.9 cm | LOW | 可通行 | 检出但不侵入 |
| 04 | 左侧压 2 cm，剩 3 cm | 3.0 cm | 2.7 cm | MEDIUM | 部分受阻 | 单石 RESTRICTED |
| 05 | 左侧压 3 cm，剩 2 cm | 2.0 cm | 1.8 cm | HIGH | 阻断 | 单石 BLOCKED |
| 06 | 两块石，留 3 cm 缝 | 3.0 cm | 3.0 cm | MEDIUM | 部分受阻 | 多石联合 |
| 07 | 两块石，留 1 cm 缝 | 1.0 cm | 0.6 cm | HIGH | 阻断 | 多石联合阻断 |
| 08 | 10 mm 高横板跨满 5 cm | 0.0 cm | 0.0 cm | HIGH | 阻断 | 低但高于 8 mm 阈值 |
| 09 | 路中 4 cm 大石块 | 0.5 cm | 0.6 cm | HIGH | 阻断 | 居中大石 |
| 10 | 5 mm 薄片跨满 5 cm | 0.0 cm | 5.0 cm | SAFE | 可通行 | **已知盲区：漏检** |

> 01/02 的实际剩余宽度引擎不输出，表中按 5 cm 路宽显示；03 的 `4.9 cm` 是
> 单块 NONE 侵入时联合空隙换算出的一般值。
> 2.5 cm 阈值附近有 ±3–6 mm 量化不确定度，**不要用恰好 2.5 cm 的案例做演示**。

---

## 5. 产物

```
data/demo_sandbox/
├── contact_sheet.png       10 场景总览（汇报首页）
├── figure_pipeline.png     单场景四联图（RGB → 掩码/框 → 深度 → 判定）
├── summary.md/json         场景表与完整实际结果
├── demo.db                 独立演示数据库（预置 10 条判定）
├── artifacts/              演示数据库对应的 RGB/深度/掩码/感知结果
└── scenes/<scene_id>/
    ├── rgb.jpg / depth.bin / confidence.bin / metadata.json
    ├── road_mask.png / perception.json / road_status.json
    ├── overlay.png / overlay_zoom.png
    └── depth_vis.png / depth_zoom.png
```

重新生成：

```bash
perception/.venv/bin/python scripts/make_demo_sandbox.py
# 不生成演示数据库：
perception/.venv/bin/python scripts/make_demo_sandbox.py --no-db
```

---

## 6. Web/App 演示

演示数据库不依赖 AI worker，判定结果是预置的。用 8001 端口起一个独立后端：

```bash
ROAD_DB="$PWD/data/demo_sandbox/demo.db" \
ROAD_ARTIFACTS_DIR="$PWD/data/demo_sandbox/artifacts" \
ROAD_PORT=8001 ROAD_STALE_AFTER_S=86400 ROAD_DEVICE_OFFLINE_S=86400 \
ROAD_ARTIFACT_HOURS=72 ROAD_RETENTION_HOURS=72 \
perception/.venv/bin/python -m uvicorn backend.app.main:app \
  --host 0.0.0.0 --port 8001
```

- 用户端：`http://127.0.0.1:8001/`（首屏是 BLOCKED 场景）
- 管理后台：`http://127.0.0.1:8001/#/admin`（历史页可翻到其余场景）
- 所有页面显示 DEMO 横幅；不要把这个环境当成实时系统。
- iOS「山行智安」也可以填 `IP:8001` 连接，但它是快照/历史数据，不是实时推送。

---

## 7. 不能宣称

1. 这不是沙盘实采，不能证明真实检测能力；
2. 合成深度没有真实 LiDAR 的飞点、反射、多路径等失效模式；
3. 5 mm 薄片场景是**漏检**，说明 8 mm 阈值以下不可见，应放在"限制与改进"；
4. 道路掩码是真值，绕开了道路模型域适配；真实闭环仍需沙盘数据微调；
5. 不能把这套数据的 SAFE/MEDIUM/HIGH 当作现场结论。

---

## 8. 顺带修复的引擎问题

做这套演示时发现并修复了一个**真实缺陷**：多块石各自剩余宽度都够，
但联合空隙不足时，主方向仍判 RESTRICTED。现在：

- 引擎读取 `metrics.combined_remaining_width_px`，用落石的 米/像素 比例换算；
- 联合空隙低于 `min_pass_width_m` 或 `combined_blocked=true` 时，主方向升级 HIGH，
  理由码 `MULTI_ROCK_JOINT_BLOCK`；
- 联合空隙大于阈值时，也作为方向的 `remaining_width_m`（与单块取最小）。

C++ 测试从 46 增至 **48 项**（新增联合空隙低于/高于阈值两个用例），
Python 99 项、契约 12/12 全过。
