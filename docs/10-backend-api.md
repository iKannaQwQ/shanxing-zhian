# 10 · 后端 API 与部署

> FastAPI 后端：接收帧 → 落盘 → 排队 → 收 AI 结果 → 跑风险引擎 → 落库 → 推送。
> **后端不加载任何模型**，AI 推理在独立进程里。

---

## 1. 进程划分

```
┌─────────────┐   multipart    ┌──────────────────┐
│  iPhone     │ ─────────────► │  backend (8000)  │
└─────────────┘                │  FastAPI         │
                               │  无模型依赖       │
┌─────────────┐   轮询/回传     │  SQLite + 文件    │
│ ai_worker   │ ◄────────────► │  风险引擎(C++)   │
│  模型在这里  │                └────────┬─────────┘
└─────────────┘                         │ WebSocket
                                ┌───────▼─────────┐
                                │  浏览器前端      │
                                └─────────────────┘
```

三条硬性边界（有测试守护）：

| 约束 | 落实方式 |
|---|---|
| 网页请求不触发推理 | `/status` `/assessments` 只读库；推理只由 worker 拉取触发 |
| 后端与 AI 解耦 | 后端源码禁止 `import perception`（`test_backend_does_not_import_perception`） |
| 大数组不进 JSON | 深度/置信度走 multipart 与文件下载，JSON 里只有路径与元数据 |

---

## 2. 启动

### macOS / Linux

```bash
# 1) 后端 API
perception/.venv/bin/python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000

# 2) AI worker（另开一个终端）
perception/.venv/bin/python scripts/ai_worker.py --backend http://127.0.0.1:8000
```

或直接 `bash scripts/start_backend.sh`（只含后端），worker 单独启动。

### Windows（**尚未实机验证**）

```powershell
# 前提: Python 3.11 x64、CMake、Visual Studio Build Tools (C++ 工作负载)

# 1) C++ 风险引擎
powershell -ExecutionPolicy Bypass -File scripts\build_risk_engine.ps1

# 2) Python 依赖
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements-backend.txt
.venv\Scripts\pip install -r requirements-ai-worker.txt

# 3) 启动后端
.venv\Scripts\python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000

# 4) 启动 AI worker（另开窗口）
.venv\Scripts\python scripts\ai_worker.py --backend http://127.0.0.1:8000

# 5) 防火墙放行
New-NetFirewallRule -DisplayName "MountainRoad 8000" -Direction Inbound `
  -Protocol TCP -LocalPort 8000 -Action Allow
```

> ⚠️ 上述 Windows 步骤**从未在 Windows 上执行过**。`build_risk_engine.ps1` 同样未经实测。
> 已知风险：MSVC 对 C++17 的支持、torch CPU 版体积、路径分隔符。

---

## 3. 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `ROAD_DB` | `data/db/mountain.db` | SQLite 路径 |
| `ROAD_ARTIFACTS_DIR` | `data/artifacts` | 原始帧落盘目录 |
| `ROAD_RETENTION_HOURS` | 24 | 数据保留窗口 |
| `ROAD_DEVICE_OFFLINE_S` | 15 | 多久无数据判定离线 |
| `ROAD_FRAME_EXPIRE_S` | 20 | 帧等待 AI 结果的超时 |
| `ROAD_STALE_AFTER_S` | 5 | 最新判定多久算过期 |
| `ROAD_ROAD_MODEL` | `yolop` | 道路分割模型（worker 用） |
| `ROAD_ROCK_WEIGHTS` | 空 | 落石检测权重（worker 用） |

---

## 4. REST API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查，含 `inference: external` |
| GET | `/api/v1/status` | 总览：最新判定、是否过期、队列、设备、存储占用 |
| GET | `/api/v1/assessments?limit&offset` | 历史判定（分页） |
| GET | `/api/v1/events?limit` | 事件流水 |
| GET | `/api/v1/devices` | 设备列表（含 `online` / `silent_ms`） |
| POST | `/api/v1/devices/heartbeat` | 设备心跳 |
| POST | `/api/v1/ingest/frame` | 上传帧（multipart，**202**） |
| GET | `/api/v1/frames/pending?limit` | **AI worker 专用**，拉待处理帧 |
| GET | `/api/v1/frames/{id}` | 单帧元数据 |
| GET | `/api/v1/frames/{id}/{artifact}` | 下载 `rgb`/`depth`/`confidence`/`metadata` |
| POST | `/api/v1/ingest/perception` | **AI worker 专用**，回传感知结果（**202**） |
| WS | `/ws/status` | 实时推送 |

### 上传帧

```http
POST /api/v1/ingest/frame
Content-Type: multipart/form-data

meta=<metadata.json 内容>
rgb=<JPEG 字节>
depth=<float32 小端，宽度*高度*4>
confidence=<uint8，宽度*高度>
```

校验：`frame_id` / `device_id` / `ts_capture_ns` / `depth` / `capture` 必填；
RGB 字节数必须等于 `capture.bytes`；深度字节数不得少于 `宽度×高度×4`。
不通过返回 **422** 并给出具体字段。

响应 **202**：

```json
{"accepted": true, "frame_id": "...", "status": "stored",
 "artifact_dir": ".../data/artifacts/<frame_id>"}
```

### AI 回传感知结果

```json
POST /api/v1/ingest/perception
{"frame_id": "...", "perception": { ...PerceptionReport... }}
```

响应 **202**：`{"accepted":true,"frame_id":"...","overall_risk":"UNKNOWN","system_status":"DEGRADED"}`

错误码：

| 状态 | code | 含义 |
|---|---|---|
| 404 | `UNKNOWN_FRAME` | 帧不存在（未上传或已被清理） |
| 200 | `ALREADY_PROCESSED` | 重复回传，忽略 |
| 500 | `ASSESS_FAILED` | 风险引擎出错，帧标记 failed |

### 状态总览

```json
GET /api/v1/status
{"ready":true,"server_ns":...,"latest":{...},"age_ms":120,
 "stale":false,"stale_after_ms":5000,
 "queue":{"depth":0,"capacity":null},
 "counters":{"queued":0,"processed":128,"failed":0,"dropped":0},
 "devices":[{"device_id":"...","online":true,"silent_ms":120}],
 "db":{"frames":128,"assessments":128,"events":3},
 "artifacts":{"files":512,"bytes":47000000},
 "ws_clients":1,"last_processing_ms":0.4,
 "ai_fps":0.5,
 "system":{"backend":"OK","ai_worker":"ONLINE","ai_last_result_ms":321,
           "pending_frames":0,"devices_online":1,"devices_total":3,
           "ws_clients":1,"frames_failed":1}}
```

`ai_fps` = 最近 10 秒完成的判定数 / 10。
`system.ai_worker`：`ONLINE`（5 秒内有结果）/ `IDLE`（无新数据）/
`STALLED`（有积压却长时间无结果，通常是 worker 挂了）。

`stale=true` 表示最新判定已经超过 `stale_after_ms`，**前端必须显式提示**，
不能把旧数据当成实时路况。

---

## 5. WebSocket 协议

连接 `ws://<host>:8000/ws/status`。

**连上立即收到一条快照**，带 `is_history: true`——它是补发的历史值，
不是此刻的实时状态：

```json
{"type":"snapshot","latest":{...},"is_history":true,"server_ns":...}
```

之后的消息类型：

| type | 触发 | 关键字段 |
|---|---|---|
| `assessment` | AI 结果被处理完 | `overall_risk` `system_status` `passability` `alternative` `rock_count` `processing_ms` `reasons` |
| `stale` | 最新判定超时 | `age_ms` |
| `fresh` | 恢复新鲜 | `age_ms` |
| `device_offline` | 设备无数据 | `device_id` `silent_ms` |
| `device_online` | 设备恢复 | `device_id` |
| `error` | 帧过期 / 判定失败 | `code`（`FRAME_EXPIRED` / `ASSESS_FAILED`）`frame_id` |

示例：

```json
{"type":"assessment","frame_id":"0C06...","device_id":"iphone13,3",
 "overall_risk":"UNKNOWN","system_status":"DEGRADED",
 "passability":{"A_TO_B":"UNKNOWN","B_TO_A":"UNKNOWN"},
 "alternative":"UNKNOWN","road_status":"OK","road_coverage":0.1165,
 "rock_count":0,"processing_ms":0.4,"dropped_since_last":0,
 "reasons":["ROAD_HAS_GAPS","ROCK_DETECTOR_UNAVAILABLE"]}
```

客户端要求：断线 2 秒重连；重连后靠 `snapshot` 恢复；收到 `assessment` 才算实时。

---

## 6. 数据库

| 表 | 主要字段 |
|---|---|
| `devices` | `device_id` `first_seen_ns` `last_seen_ns` `last_heartbeat_ns` `frames` `thermal` `target_fps` `meta_json` |
| `frames` | `frame_id` `device_id` `ts_capture_ns` `ts_received_ns` `processed_ns` `width` `height` `jpeg_bytes` `depth_bytes` `status` `error` `artifact_dir` `rgb_path` `depth_path` `confidence_path` |
| `assessments` | `frame_id` `ts_assessed_ns` `overall_risk` `system_status` `a_to_b` `b_to_a` `alternative_status` `road_coverage` `rock_count` `processing_ms` `json` |
| `events` | `ts_ns` `frame_id` `device_id` `level` `code` `message` |

`frames.status` 取值：`stored` → `processed` / `failed` / `expired`。

图像与深度**不进数据库**，只存路径；文件按 `<artifacts>/<frame_id>/` 组织。

迁移：启动时对老库执行 `ALTER TABLE ADD COLUMN` 补齐新列（幂等）。

---

## 7. 测试

```bash
# 单元 + 集成（14 项，不需要模型）
perception/.venv/bin/python -m pytest backend/tests -q

# 端到端自检（需要后端已启动；不需要模型）
perception/.venv/bin/python scripts/smoke_test_backend.py
```

自检覆盖 10 项：健康、解耦标记、上传 202、落盘、入队、队列可读、
AI 回传、风险引擎判定、状态查询、原始帧下载。

---

## 8. 与初版（M6）的差异

| 项 | M6 | 现在 |
|---|---|---|
| 推理位置 | 后端进程内 | **独立 worker** |
| 原始帧 | 处理后丢弃 | **落盘保留**，库中存路径 |
| 设备状态 | 只有首末次出现 | 在线/离线、静默时长、热状态 |
| 数据过期 | 未暴露 | `stale` / `age_ms` / WS `stale` 消息 |
| 失败可见性 | 队列溢出 | 帧过期、设备离线、判定失败均推送 |
| 深度数据 | 只在内存 | 文件存储 + 下载接口 |

旧模块 `backend/app/pipeline.py`、`worker.py` 已删除（职责迁移到
`perception/service.py` 与 `scripts/ai_worker.py`）。
