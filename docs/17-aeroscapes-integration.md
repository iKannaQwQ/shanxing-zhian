# 17 · AeroScapes 模型接入（Stage 5）

> 模型权重：`perception/weights/aeroscapes_unet_512_expA.pt`
> 接入方式：`ROAD_ROAD_MODEL` 环境变量 / `--road-model` 参数，未改任何其他接口。

---

## 1. 接入路径

```
ROAD_ROAD_MODEL=perception/weights/aeroscapes_unet_512_expA.pt
        │
        ▼
PerceptionService(road_model=...)          ← scripts/ai_worker.py --road-model
        │  选择 UNetRoadSegmenter 而非 YOLOP
        ▼
PerceptionPipeline → PerceptionReport
        │  含 mask / polygon / width_profile / confidence / timings / model
        ▼
后端 /api/v1/ingest/perception（契约校验）
        ▼
C++ 风险引擎 → RoadStatus
```

**未改动**：后端接口、JSON 契约结构（只新增可选块）、前端、风险引擎、UNKNOWN/fail-closed 机制。

---

## 2. 修复的接入级 bug

`UNetRoadSegmenter` 原先**忽略权重文件里记录的输入尺寸**，一律使用构造参数的默认值
`(480, 320)`，而模型是在 512×512（或 256×256）上训练的。

后果：推理分辨率与训练不一致，输出碎裂，还被误认为"闸门有效"。

修复：

1. 优先读取 checkpoint 的 `input_size`；
2. 显式传入与权重不一致时打印告警并以权重为准；
3. 同时暴露 `model_info`（版本、输入尺寸、参数量、设备、数据集指纹）。

---

## 3. 契约扩展

`perception_report` 新增**可选**顶层 `model` 块（不影响旧生产者）：

```json
"model": {
  "road_segmenter": "aeroscapes_unet_512_expA",
  "input_size": [512, 512],
  "params": 1927009,
  "device": "mps",
  "dataset_digest": "eb941485aa620b83"
}
```

C++ 风险引擎对该字段无感（已验证不报错）。

---

## 4. 端到端验证（真实进程链路）

```
上传真机帧 → 202
worker（--road-model aeroscapes_unet_512_expA）→ 627.5 ms
perception.json:
  model = {..., input_size: [512,512], params: 1927009, device: mps}
  road_mask_status = OK, road_detected = True, coverage = 0.3235
  polygon = 14 点, width_profile = 16 点
后端判定: risk = UNKNOWN, system = DEGRADED, a_to_b = UNKNOWN
```

**fail-closed 保持**：因为没有落石检测器，无论道路分割结果如何，
系统仍返回 `UNKNOWN`，不会给出"可通行"。

---

## 5. 域外输入实测（必须记录的风险）

| 输入 | road_detected | 覆盖率 | 置信度 | 后端判定 |
|---|---|---|---|---|
| AeroScapes 乡村道路 | True | 0.09 | — | UNKNOWN（无深度） |
| AeroScapes 跨海大桥 | True | 0.31 | — | UNKNOWN（无深度） |
| **真机室内天花板** | **True** | **0.32** | 0.89 | UNKNOWN（DEGRADED） |

**模型在室内天花板上报告"检测到道路，覆盖率 32%"** —— 这是明确的假阳性，
且形态学闸门没有拦住。

结论：

1. 该模型**只在 AeroScapes 分布内被验证**，其测试集 Road IoU 为 0.6447（expA）；
2. **不能**把它当作目标场景（桌面/室内/沙盘）的道路检测依据；
3. 系统当前之所以仍输出 UNKNOWN，是因为**没有落石检测器**，
   而不是因为道路分割可靠——这个安全边界是偶然的，不是设计出来的。

---

## 6. 输出字段核对（对照任务要求）

| 要求 | 是否满足 | 位置 |
|---|---|---|
| 道路分割 mask | ✅ | `road_mask.png`（worker 落盘）+ 内部 mask |
| 道路区域轮廓 | ✅ | `road.polygon`（实测 14 点） |
| 道路宽度剖面 | ✅ | `road.width_profile`（实测 16 点） |
| 道路区域置信度 | ✅ | `road.confidence`（预测为道路像素的平均概率） |
| 推理耗时 | ✅ | `metrics.timings_ms.perception_total` |
| 模型版本 | ✅ | `model.road_segmenter` |
| 输入图像尺寸 | ✅ | `model.input_size` |
| 无法识别时返回 UNKNOWN | ✅ | 端到端验证：三例全部 UNKNOWN |

---

## 7. 未验证 / 下一步

1. **实验 D（256/bs4）仍在训练**，用于把"分辨率"与"batch"的影响拆开。
2. 模型在真实沙盘上完全未验证，接入后无任何精度依据。
3. 落石检测仍为空，`ROAD_ROCK_WEIGHTS` 未设置。
4. 若要降低域外假阳性，需要专门的 OOD 检测或目标域微调，
   当前不做"看起来安全"的补丁。
