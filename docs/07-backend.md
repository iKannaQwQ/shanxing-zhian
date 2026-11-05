# 07 · 正式后端（M6）

> FastAPI + SQLite + WebSocket。核心目标是**把上传与处理解耦**：
> 上一版同步链路里，客户端速率被服务端算力锁死在 3.5–4.8 fps。

---

## 1. 架构

```
POST /api/v1/ingest/frame
   │  解析 multipart + 校验 + 落库 + 入队   ← 只做这些
   ├──────────────► 202 Accepted（1–3 ms）
   │
   ▼  有界队列（32）
后台 worker（串行）
   ├─ 解码 RGB / 深度 / 置信度
   ├─ YOLOP 道路分割
   ├─ C++ 风险引擎（常驻 --stream）
   ├─ 写 assessments / events
   └─ WebSocket 广播  ──► /ws/status ──► iOS / 前端
```

关键变化：**HTTP 响应不再携带判定结果**。结果走 WebSocket，
因此客户端发送 10 fps 不会被服务端处理速度拖住。

---

## 2. 实测对比

| 指标 | 同步链路（M5 版） | 异步后端（M6） |
|---|---|---|
| POST 延迟 | 55–105 ms | **1–3 ms** |
| 客户端吞吐上限 | 被服务端锁死 | 只受网络限制 |
| 单帧处理 | 55–126 ms | 36–45 ms |
| 队列溢出 | 客户端丢弃 86 帧 | 服务端丢弃并留审计 |

> 客户端不再因为"服务端算得慢"而丢帧。服务端算不过来时，
> 由服务端自己丢最旧的帧，并在 `events` 表里留下 `QUEUE_OVERFLOW` 记录。

---

## 3. 数据模型

| 表 | 作用 |
|---|---|
| `devices` | 设备首次/最后出现、帧数、元数据 |
| `frames` | 每帧的身份、尺寸、状态（queued/done/failed/dropped） |
| `assessments` | 判定结果（风险等级、双向通行性、替代路径、完整 JSON） |
| `events` | 审计与告警（风险变化、队列溢出、客户端丢帧、处理失败） |

SQLite 开 WAL，单连接 + 线程锁。写入都是毫秒级，不做连接池。

**幂等性**：`frames.frame_id` 是主键，重复上传同一帧只会覆盖，不会产生重复记录。

---

## 4. 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查（含是否部署落石检测器） |
| GET | `/api/v1/status` | 队列深度、计数器、DB 统计、最近判定 |
| GET | `/api/v1/assessments?limit&offset` | 历史判定（分页） |
| GET | `/api/v1/events?limit` | 事件流水 |
| GET | `/api/v1/devices` | 设备列表 |
| POST | `/api/v1/devices/heartbeat` | 设备心跳 |
| POST | `/api/v1/ingest/frame` | 上传（**202**） |
| WS | `/ws/status` | 判定推送（先发一条 `snapshot`，之后逐帧推 `assessment`） |

---

## 5. 保留与清理

`ROAD_RETENTION_HOURS`（默认 24）控制保留窗口，后台任务每
`ROAD_CLEANUP_INTERVAL`（默认 300 s）清理一次过期帧、判定与事件。

这是 M6 补上的**主动清理能力**：上一版只能靠人工 `rm -rf data/incoming`。

---

## 6. 配置（全部走环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `ROAD_PORT` | 8000 | 监听端口 |
| `ROAD_DB` | `data/db/mountain.db` | SQLite 路径 |
| `ROAD_QUEUE_CAPACITY` | 32 | 内存队列容量 |
| `ROAD_RETENTION_HOURS` | 24 | 保留小时数 |
| `ROAD_DEVICE` | auto | YOLOP 设备 |
| `ROAD_ROCK_WEIGHTS` | 空 | 落石检测权重（不填则降级 UNKNOWN） |
| `ROAD_SIMULATE_ROCK` | none | `medium` / `high` 注入测试落石（标注 SIMULATED） |

---

## 7. 启动

```bash
bash scripts/start_backend.sh          # 或：
perception/.venv/bin/python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

首次启动会加载 YOLOP（约 10–25 s），期间 `/api/v1/health` 返回 `starting`，
上传返回 503。客户端会自动重试。

---

## 8. 测试

`backend/tests/test_backend.py`，9 项，不依赖真实模型（用 FakePipeline）：

接收 202、后台处理落库、尺寸校验拒绝、缺字段拒绝、风险变化事件、
客户端丢帧事件、分页、保留清理、广播摘除死连接。

```bash
perception/.venv/bin/python -m pytest backend/tests -q
```

---

## 9. 已知限制

1. **单 worker 串行**：吞吐上限 = `1 / 单帧处理时间`（当前约 25 fps 理论值，实测 36–45 ms/帧）。
   多 worker 需要给 C++ 引擎开多个进程，暂未做。
2. **无鉴权**：局域网实验环境，没做认证。
3. **WebSocket 无背压**：慢客户端会被摘除，但不保证不丢消息。
4. **时钟未同步**：`ts_capture_ns` 来自手机，`ts_received_ns` 来自电脑，两者不可直接相减。
5. **前端未做**：M7 才接 Vue。

---

## 10. 下一步

1. **M7 前端**：用户页（实时状态）+ 管理后台（历史、事件、设备）。
2. 落石检测器接入后，把 `ROCK_DETECTOR_UNAVAILABLE` 的降级解除。
3. 多 worker / 多设备聚合。
