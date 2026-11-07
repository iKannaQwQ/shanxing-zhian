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

---

## 8. 新增：不确定性（域外）闸门

### 8.1 为什么需要

第 5 节记录的事实：模型对着室内天花板报告"道路覆盖率 32%、置信度 0.89"，
而**道路像素平均置信度无法区分域外输入**（室内 0.89 高于我们本该设的阈值）。

### 8.2 实测有效的信号：全局预测熵

| 样本组 | 平均熵 | 模糊像素比（0.3–0.7） |
|---|---|---|
| 域内 AeroScapes（20 张） | **0.094** | 4.1% |
| 跨海大桥（域内但异常） | 0.215 | 7.1% |
| 真机室内（8 帧） | **0.149–0.346** | 12–18% |

熵与 IoU 相关性 0.794，比"道路像素平均置信度"更能反映"模型自己是否拿不准"。

### 8.3 实现

1. `UNetRoadSegmenter.predict` 计算平均二元熵并存入 `last_uncertainty`；
2. `PerceptionPipeline._segment_road` 把它传给 `assess_road`；
3. `assess_road(uncertainty, max_uncertainty=0.20)`：**优先于覆盖率与置信度检查**，
   超过阈值直接判 `LOW_CONFIDENCE` + 理由码 `ROAD_HIGH_UNCERTAINTY`；
4. YOLOP 没有该信号时为 `None`，闸门自动跳过（向后兼容）。

阈值 0.20 的选择依据：0.15 会误杀好帧（实测一张 IoU 0.916 的乡村道路帧熵为 0.150），
0.20 能拦住跨海大桥（0.351）与 7/8 真机室内帧，同时只拒掉 12% 的熵单独判定。

### 8.4 效果（204 张有道路的测试帧）

| 指标 | 加闸门前 | 加闸门后 |
|---|---|---|
| 放行帧 Road IoU 均值 | 0.727 | **0.769** |
| 放行帧 Road IoU 中位 | 0.785 | 0.786 |
| **放行帧中 IoU < 0.3** | **17 张** | **2 张** |
| 拒绝率 | 15.7% | 35.8% |
| 真机室内帧拦截 | 0/8 | **7/8** |

**代价是用可用率换可靠性**：拒掉 36% 的帧，但放行的那批里坏帧从 17 张降到 2 张。

### 8.5 端到端验证

真机室内帧走完整链路（上传 → worker → 后端 → 风险引擎）：

```
road_detected: False
road_mask_status: LOW_CONFIDENCE
reasons: [ROAD_HIGH_UNCERTAINTY, ROAD_LOW_QUALITY, NO_ROAD, ROCK_DETECTOR_UNAVAILABLE]
后端: risk=UNKNOWN
```

### 8.6 仍然不解决的问题

1. **1/8 的室内帧没被拦住**（该帧熵 0.149，低于阈值）——闸门不是万无一失；
2. 阈值是在 AeroScapes 测试集 + 8 张真机室内帧上选的，**样本量小**，换场景需重新标定；
3. 落石检测仍为空，系统对落石依旧返回 UNKNOWN。

新增 4 项单元测试覆盖该闸门（含"大覆盖率 + 高熵必须被拒"这一关键行为）。
