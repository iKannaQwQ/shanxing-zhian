# 06 · 端到端最小链路（方案 B）

> 目标：把「手机采集 → 服务端感知 → 风险引擎 → 结果回显」串成一条真实链路，
> 先证明**整条链路成立**，再谈数据库与前端。

---

## 1. 拓扑

```
iPhone (M2–M5)                Mac
┌──────────────┐   multipart  ┌─────────────────────────────┐
│ ARKit 采集    │ ───────────► │ live_pipeline.py            │
│ 10 fps        │              │  ├ YOLOP 道路分割 (~55 ms)   │
│ 1280×960 JPEG │              │  ├ 深度/置信度解码            │
│ 256×192 深度  │              │  └ rock_risk_cli --stream   │
│ 有界队列/重试  │ ◄─────────── │     └ RoadStatus (0.3 ms)   │
└──────────────┘  road_status └─────────────────────────────┘
```

媒体流是单向的（手机 → 电脑），但 HTTP 响应里带回了判定结果，
所以手机上能直接看到风险等级——这就是"闭环"。

---

## 2. 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查（iOS 的"测试连接"） |
| GET | `/api/v1/latest` | 最近一帧的判定摘要 |
| POST | `/api/v1/ingest/frame` | multipart 上传，**响应体带 RoadStatus** |

响应示例：

```json
{
  "ok": true,
  "frame_id": "...",
  "processing_ms": 58.9,
  "perception_ms": 56.1,
  "engine_ms": 0.3,
  "road_status": { "overall_risk": "UNKNOWN", "system_status": "DEGRADED", "...": "..." }
}
```

---

## 3. 关键设计：串行处理 = 天然背压

服务端对上传请求**加锁串行处理**，不在内部堆积队列。

* 手机端本来就是单请求串行（`FrameUploader` 一次只有一个在飞）；
* 服务端处理 55 ms < 帧间隔 100 ms，队列不会增长；
* 万一服务端变慢，手机的 HTTP 超时（3 s）会触发重试，重试仍失败则按 M5 的策略丢帧，
  **不会出现"服务端内存被未处理帧撑爆"**。

代价是吞吐上限被单帧耗时锁死（约 18 fps）。当前 10 fps 有 45 ms 余量，够用。

---

## 4. 实测（空载）

| 环节 | 耗时 |
|---|---|
| YOLOP 分割 + 掩码几何 + 报告 | 50–58 ms |
| C++ 风险评估（常驻进程） | **0.3 ms** |
| HTTP + 解码 + 总计 | 53–61 ms |

> 引擎从"每帧起进程"（24 ms）改为常驻 `--stream` 模式后降到 0.3 ms，
> 单帧总成本下降约 30%。

---

## 4.1 联调时的容量实测（有负载）

第二次联调时手机端出现队列溢出，实测数据：

| 指标 | 数值 |
|---|---|
| 客户端发送 | 729 帧 / 83 s |
| 队列溢出丢弃 | **86 帧（10.5%）** |
| 往返延迟 | 105 ms（此前 18 ms） |
| 服务端容量 | **3.5–4.8 fps** |

原因不是单一因素：

1. **同步耦合**：客户端发一帧、等服务端算完、再发下一帧，
   所以吞吐被 `1 / 单帧处理时间` 锁死。
2. **开发机负载**：当时 load average 5.67、pageouts 约 195 万、空闲内存 35%。
   感知耗时从空载的 55 ms 涨到 126 ms。
   `pmset -g therm` 显示无热告警，所以**不是降频，是内存压力与 CPU 争抢**。

有界队列按设计工作：溢出不堆积、不涨内存、计数准确、保新弃旧。

> 结论：这个瓶颈是**架构性**的（同步等待），不是参数问题。
> M6 的异步设计（立即 202 + 后台处理 + WebSocket 推送）才是正解。

## 4.2 一处被修正的诚实性缺陷

`delivery.dropped_since_last` 在契约里存在，但 iOS 端一直硬编码为 0——
即便确实丢了 86 帧。这等于对下游隐瞒了数据缺口。

已修复：上传时填入"自上次成功发送以来丢弃的帧数"，服务端在日志中醒目打印，
下游因此知道"这段序列有缺口"，而不是把它当作连续帧处理。

## 5. fail-closed 的实际表现

**当前没有部署落石检测器。** 感知层因此把结果显式标为 `DEGRADED`：

```python
if not rock_detector:
    report["system_status"] = "DEGRADED"
    report["reasons"].append({"code": "ROCK_DETECTOR_UNAVAILABLE", ...})
```

链路输出的实际结果：

```
overall: UNKNOWN   system: DEGRADED
pass: {A_TO_B: UNKNOWN, B_TO_A: UNKNOWN}
reasons: [ROAD_HAS_GAPS, NO_ROCK_DETECTED, ROCK_DETECTOR_UNAVAILABLE]
```

这是**预期行为，不是缺陷**：没有落石检测能力时，系统拒绝回答"安全"。
整条链路上没有任何一处把"没检出"翻译成"安全"。

---

## 6. 运行

```bash
# 1. 先确保风险引擎已构建
bash scripts/build_risk_engine.sh

# 2. 启动端到端服务
perception/.venv/bin/python scripts/live_pipeline.py --port 8000

# 3. 查本机局域网地址
ipconfig getifaddr en0

# 4. iOS: 填入 IP:8000 → 测试连接 → 开始
```

可选参数：

| 参数 | 说明 |
|---|---|
| `--rock-weights <path>` | 加载落石检测器；不提供则降级为 UNKNOWN |
| `--save-dir <dir>` | 落盘每一帧（默认**不落盘**，避免每轮几百 MB） |
| `--device cpu\|mps\|auto` | YOLOP 推理设备 |

---

## 7. 数据清理

* 服务端默认不落盘；需要留证时才加 `--save-dir`。
* iOS 每次点「开始」会清空上一轮的 `Documents/FrameBundles`。
* 已产生的 `data/incoming/` 已清理（428 MB）。

> 正式的清理策略（保留窗口、容量上限、自动轮转）属于 M6 后端的工作，
> 当前只是"不制造垃圾"。

---

## 8. 合成注入模式（仅用于验证风险路径）

未部署落石检测器时，链路只能输出 `UNKNOWN`，无法验证中高风险分支。
为此提供**明确标注**的注入模式：

```bash
perception/.venv/bin/python scripts/live_pipeline.py --port 8000 --simulate-rock high
```

它会在感知结果里插入一个合成落石，并同时：

* 把 `source` 置为 `SIMULATED`；
* 给落石和整体都加上 `SYNTHETIC_DETECTION` 理由码；
* 启动日志打印醒目的警告。

实测（`--simulate-rock high`）：

```
risk: HIGH   pass: {A_TO_B: BLOCKED, B_TO_A: BLOCKED}   alt: UNAVAILABLE
remaining: 0.03 m   limiting: [SIM-1]
```

这不是伪造检测：数据来源被显式标注，任何看到界面的人都能分辨。
**默认关闭**，需要时才开。

## 9. 下一步

1. **M6 正式后端**：SQLite 持久化、事件表、WebSocket 推送、历史查询、设备心跳。
2. **落石检测器**：沙盘建成后用实拍数据重训，替换 `ROCK_DETECTOR_UNAVAILABLE`。
3. **时序滤波**：多帧一致性确认，抑制 UNKNOWN↔HIGH 抖动。
