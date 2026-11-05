# 03 · AI 感知模块

> 对应里程碑 M1–M7。**沙盘尚未建成**，因此所有落石相关结论都基于公开数据集与合成数据，
> 尚未在目标场景验证。

---

## 0. 状态总表

| 里程碑 | 内容 | 状态 | 证据 |
|---|---|---|---|
| M1 | 原版 YOLOP 推理 | ✅ 完成 | [02-yolop-review.md](02-yolop-review.md) |
| M2 | 道路区域分割适配 | ✅ 完成 | 掩码几何 12 项测试 + 6 张真实图 |
| M3 | 落石数据集与类别适配 | 🟡 **链路打通，模型不可用** | 39 训练图 / mAP50 0.467 |
| M4 | 落石-道路关系计算 | ✅ 完成 | 11 项测试（5 场景全覆盖） |
| M5 | RGB-D 融合 | ✅ 完成（合成数据） | 15 项测试 |
| M6 | 实时视频推理 | ✅ 完成 | 30 帧 @ 27 fps（MPS） |
| M7 | 统一 JSON 输出 | ✅ 完成 | schema 校验 + 11 项测试 |

单元测试合计 **49 项，全部通过**。

---

## 1. 模块结构

```
perception/src/perception/
├── status.py               枚举：道路/侵入/系统/深度状态
├── types.py                内部数据模型（BBox/RoadObservation/FrameReport…）
├── pipeline.py             编排：分割→检测→深度→侵入→状态→序列化
├── report.py               M7 统一 JSON 构造
├── road/
│   ├── mask_ops.py         M2 掩码几何：轮廓/多边形/宽度剖面/孔洞/裁决
│   └── segmenter.py        M2 模型侧：YOLOP drivable 头封装
├── rocks/
│   └── detector.py         M3 模型侧：ultralytics 封装 + 静态桩
├── depth/
│   ├── frame.py            深度解码（uint16 mm / float32 m）
│   ├── align.py            RGB-D 分辨率与对齐可信度
│   └── estimator.py        落石深度估计 + 像素-米制换算
├── geometry/
│   └── intrusion.py        M4 侵入判定（接地区域求交）
└── tools/                  run_video / prepare_boulder_dataset / yolop_infer
```

分层原则：几何算法（`road`/`geometry`/`depth`）不 import 任何模型；
模型封装（`segmenter`/`detector`）不 import 业务判定。这样可以脱离模型跑全部单测。

---

## 2. M2 道路分割：为什么不用车道线

原版 YOLOP 的 lane-line 头在 `segmenter.py` 中被**显式丢弃**（`_, da_out, _ = model(x)`）。
理由不是偏好，是事实：本项目道路没有标线，车道线输出恒为 0 或噪声，
用它推道路边界等于用不存在的证据做判据。

从可行驶区 mask 提取的几何信息：

| 量 | 方法 | 处理的问题 |
|---|---|---|
| 外轮廓多边形 | `findContours` + `approxPolyDP`（不做凸包） | 允许 U 形弯、不规则形状 |
| 宽度剖面 | 逐行带取**最长连续段** | 弯道/分支路段不会被外接框撑宽 |
| 中心线 | 各带左右边界中点 | 相对位置计算 |
| 孔洞率 | 外轮廓填充 - 原掩码 | 山体/落石遮挡形成的"洞" |
| 连通域数 | `connectedComponentsWithStats` | 碎片化分割检测 |

**断口不填补**。掩码上被挖掉的一块，很可能就是落石或山体遮挡，
填补等于篡改证据。代码只度量并上报 `ROAD_HAS_GAPS` / `ROAD_LARGE_HOLE`。

状态裁决（fail-closed）：

```
EMPTY / FAILED  ->  不可用，下游一律 UNKNOWN
TOO_SMALL       ->  视野偏离或分割失败
LOW_CONFIDENCE  ->  模型置信度不足
FRAGMENTED      ->  连通域过多
OK              ->  可用
```

实测（BDD100K 样例 6 张，MPS）：coverage 0.095–0.269，全部 `OK`，
与 M1 阶段 demo 的掩码覆盖率**逐图吻合**，说明封装未改变模型语义。

---

## 3. M3 落石检测：现实与差距

### 3.1 我们用了什么

公开数据集 [Mirali33/mb-boulder_det](https://huggingface.co/datasets/Mirali33/mb-boulder_det)（CC-BY-4.0），
火星 HiRISE 影像巨石检测，1 类 `boulder`。

| 项 | 值 |
|---|---|
| 训练图 / 框 | 39 / 5536（平均 142 框/图） |
| 验证图 / 框 | 6 / 904 |
| 目标尺寸 | 6–17 px（极小目标） |
| 训练：YOLO11n，40 epoch，640px，batch 8，MPS，seed 0 | 136 s |
| **mAP50** | **0.467** |
| **mAP50-95** | **0.160** |
| Precision / Recall | 0.540 / 0.502 |

> 这组数字**不能当作落石检测能力**：验证集只有 6 张图，且完全来自火星轨道影像。

### 3.2 关键证据：域差异是真实的，不是理论担忧

把训练好的模型与 YOLOP 串起来，在验证图上跑完整流水线：

```
val_00000.jpg  status=NO_ROAD  road=EMPTY  cov=0.000  rocks=4   max_risk=UNKNOWN
val_00001.jpg  status=NO_ROAD  road=EMPTY  cov=0.000  rocks=50  max_risk=UNKNOWN
val_00002.jpg  status=NO_ROAD  road=EMPTY  cov=0.000  rocks=6   max_risk=UNKNOWN
```

两件事同时被验证：

1. **YOLOP 在非道路场景下完全失效**（coverage 0.000）——它在沙盘上大概率同样失效，
   必须等真实沙盘数据才能判断；
2. **安全架构生效**：明明检出了 4–50 个落石，但因为不知道道路在哪，
   全部判 `UNKNOWN`，**没有一条被误报成 PARTIAL/FULL**。
   这条不变量已固化为回归测试 `test_rocks_without_road_stay_unknown`。

### 3.3 类别适配的真实成本

原版 YOLOP 的 `nc=1` 意味着检测头卷积输出是 `3×(1+5)=18` 通道；
加一个 rock 类需要 21 通道。**这不是改个数字，是权重形状不匹配，必须重训检测分支。**

本项目的做法是**解耦**：YOLOP 只做道路分割（权重不动），落石检测用独立模型。
好处是：换检测器不影响道路逻辑，换道路模型不影响检测逻辑。

### 3.4 结论

M3 的**工程链路**（数据集转换 → 训练 → 推理 → 接入流水线）已全部打通并可复现；
但**模型本身不可用于本项目**。当沙盘可用时，必须用沙盘实拍重训，
否则就是拿火星的石头去判断地球沙盘的路。

---

## 4. M4 侵入判定：为什么不能用中心点

中心点法的失败是构造性的：一个细长落石横在路上，框中心可能恰好落在路面外；
一个高耸落石，框上半部分压在路上但接地部分在路肩。

本项目用**接地区域**（检测框底部 18%）与道路掩码求交：

```
overlap_ratio = |接地区域 ∩ 道路| / |接地区域|
```

判定分级（阈值集中在 `IntrusionPolicy`）：

| 等级 | 条件 |
|---|---|
| `NONE` | overlap ≤ 0.03 |
| `EDGE` | 0.03 < overlap ≤ 0.15 |
| `PARTIAL` | 剩余通行宽度 > 最小可通行宽度 |
| `FULL` | 剩余通行宽度 ≤ 最小可通行宽度 |
| `UNKNOWN` | 道路不可用 |

剩余宽度不是取该行，而是**接地区域覆盖的所有行里取最窄**——保守估计。

**多石联合阻断**单独计算：两块石头各自只占一半，单看都是 `PARTIAL`，
合起来却把路堵死。`assess_multiple_intrusions` 会对所有接地区域求并集再算一次。
测试 `test_multiple_rocks_joint_block` 固定了这条。

中心点判定仍保留为 `in_road_by_center` 字段，仅用于对照；
两者结论不一致时会写入 `CENTER_METHOD_DIFFERS` 理由码。

---

## 4.9 设备朝向：一个静默失效源

ARKit 的 `capturedImage` **恒为 landscape-right 的原始缓冲**，与手机怎么拿无关。
手机竖持时，画面里的道路是横躺的，而 YOLOP 是在正立图像上训练的——
喂进去会显著降低分割质量，**且不会报错**。

处理方式：`perception/road/orientation.py` 把界面朝向翻译成顺时针旋转角，
在**模型输入端**转正、**输出端把掩码转回**。几何链路（内参、深度、投影）完全不受影响。

### 实测（frame 0C065E7A，模拟竖持）

| 场景 | 道路覆盖率 |
|---|---|
| 正立原图 | 0.1166 |
| 模拟竖持、不纠正 | **0.0000** |
| 模拟竖持、纠正 90° | **0.1166** |

纠正后的掩码与"正立原图掩码转置后"的 **IoU = 1.0000，像素级一致率 100%**。

这组数字说明：不纠正时不是"分割变差"，而是**直接归零**——
系统会以为画面里没有路，进而把整帧判成 `NO_ROAD`。
这是一个不崩溃、不告警、只有结果悄悄变错的典型失效。

## 5. M5 RGB-D 融合：不确定性的处理

### 5.1 分辨率不同 ≠ 可以直接 resize

iPhone 12 Pro 的 RGB 约 1280×960，深度约 256×192。**同源共网格时**，
按比例缩放像素坐标是合法的；**不同源时**，缩放只是猜。

代码把这两种情况显式分开：

| 采集端声明 | 宽高比校验 | 结果 |
|---|---|---|
| `aligned_to_rgb=true` | 一致 | 允许映射 |
| `aligned_to_rgb=true` | 不一致 | 拒绝（`ASPECT_MISMATCH`） |
| `aligned_to_rgb=false` | 任意 | 拒绝（`DEPTH_NOT_ALIGNED_TO_RGB`） |

拒绝时返回 `None`，上层产出的 `depth_estimate.source = NONE`，
`evidence = RGB_ONLY`，米制字段为 `null`——**保留不确定状态，不猜。**

### 5.2 采样与量程

- 采样点选**接地点**（框底中心），不是框中心：框上半是空气或背景。
- 3×3 小窗口取**中位数**，抵抗窗口内背景深度的离群值。
- 窗口内有效点占比 < 0.34 → 判定稀疏，返回 `NONE`。
- confidenceMap 均值 < 0.3 → 返回 `NONE`。
- 超出 0.2–5.0 m → 返回 `NONE`（该区间需用真机实测校准）。
- `0` 永远解码为**无效**，不是"距离 0 米"。

### 5.3 深度的用途与限制

| 用途 | 可行性 |
|---|---|
| 落石距离估计 | ✅ 接地点中位深度，配合内参得到 3D 点 |
| 像素宽度 → 米制宽度 | ✅ `px × Z / fx`，依赖内参正确 |
| 落石高度估计 | ⚠️ 需要路面平面拟合，当前未实现 |
| 遮挡/盲区推断 | ❌ 单视角深度无法外推 |
| 近距（0.4–0.8 m）精度 | ❓ **未实测**，LiDAR 近距表现必须真机验证 |

---

## 6. M6 实时推理

```bash
python perception/tools/run_video.py --source demo.mp4 --device mps \
    --jsonl out.jsonl --save-video annotated.mp4
```

实测：1280×720、30 帧，**27.2 fps**（MPS，含分割 + 报告生成）。
单帧分割耗时 27–49 ms（预热后）。

视频 / 图片目录 / 摄像头（`--source 0`）三种输入均支持；
读帧失败时走 `disconnected()` 输出 `DISCONNECTED` 状态，而不是静默停止。

---

## 7. M7 统一 JSON

Schema：`contracts/schemas/perception_report.schema.json`

示例（**合成数据**，非真实沙盘采集）：

```json
{
  "frame_id": "...", "timestamp": "2026-11-05T12:00:00+00:00",
  "system_status": "OK",
  "road_detected": true,
  "road_mask_status": "OK",
  "road": { "coverage_ratio": 0.186, "polygon": [[...]], "width_profile": [...] },
  "rockfalls": [{
    "rock_id": "R1", "bbox": [200,350,330,430], "confidence": 0.88,
    "road_overlap_ratio": 1.0, "bbox_overlap_ratio": 0.55,
    "intrusion": "PARTIAL", "risk_level": "PARTIAL",
    "risk_level_basis": "INTRUSION_GEOMETRY_ONLY",
    "evidence": "RGB_D",
    "remaining_width_px": 114.0, "remaining_width_m": 0.0912,
    "depth_estimate": {"distance_m": 0.8, "source": "LIDAR", "valid_ratio": 1.0},
    "reasons": [{"code": "ROCK_PARTIAL", "message": "..."}]
  }],
  "metrics": {"rock_count": 1, "max_risk_level": "PARTIAL",
              "combined_remaining_width_px": 114.0, "combined_blocked": false},
  "reasons": [...], "disclaimer": "..."
}
```

**`risk_level` 的语义被刻意收窄**：它只表示"落石对道路的几何侵入程度"，
不是"这条路能不能走"。因此输出里永远：
- `risk_level_basis = "INTRUSION_GEOMETRY_ONLY"`；
- 附带 `evidence`（`RGB_D` / `RGB_ONLY` / `UNAVAILABLE`）；
- 永远不出现 `SAFE` 字样（有测试断言）。

通行判定由 C++ 风险引擎负责，不在本模块越界。

---

## 8. 异常处理清单

| 情况 | 输出 | 测试 |
|---|---|---|
| 没有道路 | `NO_ROAD` + 所有落石 `UNKNOWN` | ✅ |
| 没有落石 | `NONE` + `NO_ROCK_DETECTED` 理由码 | ✅ |
| 分割失败 | `NO_ROAD` / `FAILED` | ✅ |
| 置信度不足 | `LOW_CONFIDENCE` → `DEGRADED` | ✅ |
| 深度不可用 | `NO_DEPTH`，米制字段 null | ✅ |
| 输入异常 | `INPUT_ERROR` | ✅ |
| 摄像头断开 | `DISCONNECTED` | ✅ |
| 推理超时 | `TIMEOUT`（超过预算即降级） | ✅ |

---

## 9. 运行方法

```bash
source scripts/env.sh

# 单元测试（不需要模型、不需要数据）
perception/.venv/bin/python -m pytest perception/tests -q

# 单帧/视频推理
perception/.venv/bin/python perception/tools/run_video.py \
    --source <视频|目录|摄像头序号> --device mps --jsonl out.jsonl

# 数据集转换（公开数据 -> YOLO）
perception/.venv/bin/python perception/tools/prepare_boulder_dataset.py

# 训练
perception/.venv/bin/python -c "
from ultralytics import YOLO
YOLO('yolo11n.pt').train(data='data/public/mb-boulder/yolo/data.yaml',
    epochs=40, imgsz=640, batch=8, device='mps', workers=0)"
```

---

## 10. 已知限制（必须与结论一起引用）

1. **没有任何沙盘数据**。落石检测模型的输出不能用于判断真实沙盘。
2. **验证集只有 6 张图**，mAP 的置信区间极宽，不应作为性能承诺。
3. **LiDAR 近距精度未实测**（0.4–0.8 m），深度阈值是保守假设。
4. **`aligned_to_rgb` 的正确性依赖采集端**，iOS 端尚未实现，无法交叉验证。
5. **落石高度未估计**，需要路面平面拟合（下一步）。
6. **部分遮挡的落石**只在检测层面依赖模型，尚未做几何补全。
7. 视频推理实测只有 30 帧合成视频，未做长时间稳定性测试。

---

## 11. 下一步

1. 沙盘建成后，采集 200–500 张实拍 → 重训检测器 → 在真实数据上评估（这是唯一有效的路径）。
2. 路面平面拟合，补上落石高度与体积估计。
3. 时序滤波：多帧一致才升级风险等级，抑制单帧抖动。
4. iOS 采集端接入后，用真实 `frame_bundle` 替换合成深度测试。
