# 14 · iOS 采集端功能总览

> 设备：iPhone 12 Pro Max（`iPhone13,3`，带 LiDAR）
> 实现：Swift + SwiftUI + ARKit
> 本文是**功能清单**；逐阶段的验证过程与踩坑记录见 [05-ios-capture.md](05-ios-capture.md)。

---

## 1. 一句话概括

把 ARKit 的 RGB、LiDAR 深度、置信度和逐帧内参，按 10 fps 抽帧打包成
一个自描述的帧束（frame bundle），通过 HTTP 上传给后端，并通过 WebSocket
接收判定结果。

---

## 2. 数据流

```
ARKit 相机 (1920×1440, 原生 30/60 fps)
   │  原生帧回调
   ▼
会话队列 ── 抽帧节流 (默认 10 fps)
   │         ├─ 跟踪状态检查（必须 normal）
   │         ├─ 暖机检查（启动后 ≥1.0 s）
   │         └─ 编码空闲检查（忙则丢弃并计数）
   ▼
打包队列（串行，一次一帧）
   │  ① YUV→RGB（vImage，BT.709）
   │  ② 缩放到 1280×960
   │  ③ JPEG 编码（质量随热状态变化）
   │  ④ 深/置信度原始字节拷贝
   ▼
FramePackager 产出 bundle
   ├─ 保存前 20 帧到 App 沙盒（可导出排查）
   └─ 进入上传队列（容量 32，溢出丢最旧）
        │
        ▼
   POST /api/v1/ingest/frame  (multipart)
        │  超时 3 s，最多重试 3 次
        ▼
   后端 → AI worker → 风险引擎
        │
        ▼
   WebSocket /ws/status  ──►  界面显示端到端风险
```

---

## 3. 功能清单

### 3.1 采集

| 功能 | 实现 | 参数 |
|---|---|---|
| 世界跟踪 | `ARWorldTrackingConfiguration` | 6DoF |
| 深度采集 | `frameSemantics = [.sceneDepth]` | 256×192 float32（米） |
| 置信度 | `sceneDepth.confidenceMap` | 256×192 uint8，0/1/2 |
| 原生帧率 | ARKit 提供 | 实测冷机 60 fps、热机 20 fps |
| 抽帧节流 | 时间戳判定 | 默认 **10 fps**，可调 |
| 逐帧内参 | `camera.intrinsics` | 每帧上报（见 §6） |
| 时间戳 | `sessionEpochNs + frame.timestamp` | 纳秒，避免时钟漂移 |

### 3.2 质量控制（三道门）

| 门 | 条件 | 不满足时 |
|---|---|---|
| 跟踪门 | `tracking == .normal` | 丢弃并计入「质量门控丢弃」 |
| 暖机门 | 距首帧 ≥ **1.0 s** | 同上（此时自动对焦尚未收敛） |
| 编码门 | 上一次打包已完成 | 丢弃并计入「编码繁忙丢帧」 |

> 三者的计数分开统计，**不会混在一起**，便于定位是哪一环丢的帧。

### 3.3 打包

| 项 | 值 |
|---|---|
| 输出分辨率 | **1280×960**（固定） |
| JPEG 质量 | 0.72（正常）/ 0.65（过热）/ 0.60（严重过热） |
| 典型体积 | 约 85–170 KB |
| 深度字节 | 196 608 B（256×192×4，无 padding） |
| 置信度字节 | 49 152 B |
| 单帧合计 | 约 330–380 KB |
| 本地保存 | 每次运行保存**前 20 帧**到沙盒，便于离线排查 |

### 3.4 上传

| 项 | 值 |
|---|---|
| 端点 | `POST /api/v1/ingest/frame`（multipart） |
| 队列容量 | **32**，满时丢最旧并计数 |
| 超时 | 请求 3 s / 资源 10 s |
| 重试 | 最多 **3** 次，指数退避 |
| 丢帧上报 | 每帧携带 `delivery.dropped_since_last`，**不隐藏丢帧** |
| 心跳 | 每 **10 s** 一次 + 热状态变化时 |

### 3.5 热自适应

| 热状态 | 目标 FPS | JPEG 质量 | 标签 |
|---|---|---|---|
| 正常 / 偏热 | 10 | 0.72 | 正常 |
| 过热 | **5** | 0.65 | 降级 |
| 严重过热 | **3** | 0.60 | 严重降级 |

- 每 **2 秒**检查一次 `ProcessInfo.thermalState`
- 状态变化时：调整帧率与质量，并立刻发心跳告知后端
- 实测效果：热机时原本被动丢帧 22–30%，改主动降频后**丢帧归零**

### 3.6 朝向处理

- `capturedImage` 恒为 landscape-right 的原始缓冲
- 读取 `UIWindowScene.interfaceOrientation`，随帧上报 `capture.device_orientation`
- 由**后端**在送模型前转正、产出掩码后转回
- 预览图单独加方向标记，三张预览（RGB/深度/置信度）方向一致

### 3.7 状态通道

| 项 | 实现 |
|---|---|
| 协议 | WebSocket `/ws/status` |
| 断线重连 | 2 s 后自动重连 |
| 看门狗 | 每 5 s 检查，该连未连则强制重连 |
| 前台恢复 | 监听 `willEnterForegroundNotification` 立即重建 |
| 历史数据 | 服务端首帧 `snapshot` 标 `is_history`，不计入「实时」 |
| 显示 | 「已连接（已收 N 条）」+ 重连次数 |

### 3.8 界面与诊断

**实时预览**：RGB（含方向修正）、深度伪彩色、置信度伪彩色，各带时间戳。
缩略图 1 Hz 刷新，走独立队列，不阻塞打包。

**诊断文本**（可整段复制），包含：

| 分组 | 内容 |
|---|---|
| 设备 | 帧序号、跟踪状态、原生 FPS |
| 格式 | RGB 尺寸/像素格式/planes/bpr、深度与置信度尺寸、内参、缩放比 |
| 采样 | 深度有效率、最小/最大值 |
| 性能 | RGB 转码耗时、JPEG 编码耗时、体积 |
| 打包 | **等待 / 计算**分列、丢帧计数 |
| 节流 | 目标 FPS、实测 FPS、产出数、跳过数 |
| 内存 | 起始/当前/峰值/变化，5 秒一个采样点 |
| 热 | 热状态、热策略、目标 FPS、质量、降级次数 |
| 网络 | 服务器地址、状态流、端到端风险、已发送/失败/队列/丢帧、延迟 |
| 其他 | 界面朝向、构建标记 |

---

## 4. 产出的数据

每帧一个 bundle，四个文件：

```
metadata.json   约 1.5 KB
rgb.jpg         1280×960，85–170 KB
depth.bin       196 608 B（256×192 float32，单位米，0 表示无效）
confidence.bin   49 152 B（256×192 uint8，ARConfidenceLevel 0/1/2）
```

`metadata.json` 主要字段：

| 字段 | 说明 |
|---|---|
| `frame_id` / `device_id` / `session_id` / `seq` | 追踪标识 |
| `ts_capture_ns` | 采集时刻（纳秒） |
| `capture.width/height/bytes/format/jpeg_quality` | RGB 描述 |
| `capture.orientation` | 恒为 `landscape_right` |
| `capture.device_orientation` | 采集时的界面朝向 |
| `intrinsics.fx/fy/cx/cy` | **逐帧**内参（参考分辨率下） |
| `depth.*` / `confidence.*` | 尺寸、编码、语义 |
| `delivery.dropped_since_last` | 自上次成功上传以来丢掉的帧数 |

---

## 5. 已验证的事实（真机）

| 项 | 结果 |
|---|---|
| 连续上传 | **1086 帧发送 = 1086 帧接收**，0 拒绝，平均延迟 18 ms |
| 冷机性能 | 原生 60 fps，打包计算 15.3 ms，丢帧 **0** |
| 热机表现 | 原生降到 20 fps，被动丢帧 22–30% |
| 热降级后 | 自动降到 3 fps，**丢帧 0**，内存稳定 310 MB 无泄漏 |
| 内参漂移 | 前 11 帧 fx 从 983 → 1022（+3.9%），源于自动对焦收敛 |
| LiDAR 近界 | <0.2 m 置信度骤降，>0.4 m 稳定高置信 |
| 深度对齐 | 与 RGB 按 1/7.5 缩放对齐，偏移 ±2 px 内 |

---

## 6. 设计上刻意的选择

1. **逐帧内参**，不假设固定焦距——自动对焦会让 fx 漂移近 4%。
2. **暖机 1 秒**再上传——漂移期间的数据会污染几何计算。
3. **丢帧必须上报**——`dropped_since_last` 若恒为 0 就是欺骗。
4. **热降级要告知下游**——否则下游会把 5 fps 当成 10 fps 的正常覆盖。
5. **预览不阻塞主链路**——缩略图走独立队列，并且用 `CGContext` 而非 ImageIO
   （曾因缩略图阻塞打包队列导致 22% 丢帧）。
6. **不做本地判定**——采集端只采、只传，风险判定统一在后端，避免两份逻辑。

---

## 7. 已知限制

| 限制 | 说明 |
|---|---|
| **俯视机位未验证** | 目前所有测试都是手持对准室内场景，未验证 0.64 m 垂直向下的跟踪稳定性 |
| 无沙盘数据 | 采集端本身不涉及模型，但产出的数据从未在沙盘上使用过 |
| 屏幕锁定会断流 | 靠看门狗与前台恢复兜底，但熄屏期间确实不采集 |
| 单设备 | 未做多机位同步 |

---

## 8. 文件索引

| 文件 | 职责 |
|---|---|
| `ARSessionManager.swift` | 会话、抽帧、门控、热策略、统计、诊断 |
| `FramePackager.swift` | YUV→RGB→缩放→JPEG→打包 |
| `FrameBundle.swift` | 帧束结构与 `metadata.json` |
| `FrameEncoder.swift` | JPEG 编码 |
| `YUVConverter.swift` | vImage 色彩空间转换 |
| `RawPlane.swift` | 深度/置信度平面拷贝 |
| `DepthSampler.swift` | 深度有效率与范围统计 |
| `Visualizers.swift` | 深度/置信度伪彩色 |
| `FrameUploader.swift` | 上传队列、重试、心跳 |
| `MultipartBuilder.swift` | multipart 组装 |
| `StatusStream.swift` | WebSocket 订阅与重连 |
| `RiskSnapshot.swift` | 判定结果解析 |
| `ThermalPolicy.swift` | 热状态 → 帧率/质量 |
| `ThermalPolicy` / `BuildInfo.swift` | 策略表与构建标记 |
| `CaptureStats.swift` | 统计数据结构 |
| `BundleStore.swift` | 本地保存前 20 帧 |
| `MemoryProbe.swift` | 内存采样 |
| `UploadSettings.swift` | 服务器地址与端点 |
| `CapabilityChecker/View.swift` | M1 能力检查 |
| `SessionView.swift` | 主界面 |
