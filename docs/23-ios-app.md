# 23 · iOS 应用「山行智安」（SwiftUI 原生）

> 目录：`ios/ShanXingZhiAn/`　语言：SwiftUI（iOS 16+）
> 状态：**编译通过**；**真机已运行**（2026-11-16，iPhone 通过个人热点连到 172.20.10.4:8000，
> 成功显示 MEDIUM / 受限通行 / 剩余 8.3 cm）。界面细节仍在调整。

---

## 1. 为什么从"网页壳"改成原生

第一版是 WKWebView 封装（半天可交付）。改为原生重写的原因是**可控性**：
网页壳受限于网页端实现，无法做原生交互（手势、原生告警、离线态），
也无法在答辩时体现客户端能力。

代价是工作量从半天变成两天，且**必须自己保证与网页端行为一致**。

---

## 2. 结构（1043 行 Swift）

```
ios/ShanXingZhiAn/ShanXingZhiAn/
├── ShanXingZhiAnApp.swift    入口
├── RootView.swift            根路由 + 服务器地址设置页
├── DashboardView.swift       监控大屏界面
├── DashboardViewModel.swift  状态机：WebSocket + 轮询 + 过期判定
├── APIClient.swift           /status、影像 URL、WS 地址
├── Models.swift              后端 DTO 与界面读值
├── Theme.swift               配色与通用卡片
└── ServerConfig.swift        地址规范化与持久化
```

---

## 3. 数据链路

```
GET /api/v1/status ──┐
                     ├─→ DashboardViewModel ─→ DashboardView
WS  /ws/status ──────┘
```

| 来源 | 用途 | 频率 |
|---|---|---|
| WebSocket | 实时推送，主力 | 每帧 |
| 轮询 `/status` | 兜底与首屏 | 3 秒 |

**关键约定（与网页端一致）**：

- WS 连上先收到 `{"type":"snapshot","is_history":true}`——**这是历史数据，不是实时**，
  只用来填充界面，**不重置"新鲜"计时**。网页端早期曾在这里出过错。
- 只有 `assessment` 消息才重置计时并累加"已收 N 条"。
- `stale` 由客户端按「距上次实时消息的秒数」自行计算，不直接信服务端布尔值
  （服务端 3 秒才轮询一次，直接用会晚）。

---

## 4. 界面

监控大屏布局，与网页端一致：

- **宽屏（≥820pt）**：左画面 + 右数据两栏
- **窄屏**：上下堆叠

画面区叠加 HUD（风险徽标 / 时间 / 最窄剩余 / 障碍数），过期时整块降透明并盖遮罩。
数据区依次是：总体状态（渐变卡）、A→B 与 B→A 双向卡、障碍与道路卡、替代通行卡。

颜色映射：`SAFE 绿 / LOW 黄 / MEDIUM 橙 / HIGH 红 / UNKNOWN 灰`。

---

## 5. 服务器地址

首次启动进设置页，输入 `192.168.1.5:8000`：

- 自动补 `http://`、补默认端口 8000、去掉路径
- 存 `UserDefaults`（键 `server_base_url`）
- 主界面右上角齿轮可改；**改地址会整体重建视图与 ViewModel**（`.id(base)`），
  否则会继续连旧地址

---

## 6. 网络权限

| 配置 | 作用 |
|---|---|
| `NSAllowsArbitraryLoads` | 允许 HTTP（后端为 http） |
| `NSLocalNetworkUsageDescription` | iOS 14+ 局域网权限弹窗所需 |

> 仅适合内部分发；上架 App Store 需改 HTTPS。

---

## 7. 验证到哪一步

| 项 | 状态 |
|---|---|
| 编译（iphonesimulator / Debug） | ✅ `BUILD SUCCEEDED` |
| 显示名「山行智安」 | ✅ 产物 Info.plist |
| Bundle ID `com.shanxing.zhian` | ✅ |
| 最低系统 iOS 16.0 | ✅ |
| AppIcon（由网页 favicon 复刻） | ✅ 已编入 Assets.car |
| 真机运行 | ✅ 用户截图确认（2026-11-16） |
| 与后端联调 | ✅ 已显示真实判定（MEDIUM / 8.3 cm / 75.9% 覆盖） |
| WebSocket | ⚠️ 已连接（"已收 0 条"）；尚无新帧推送，实时增量未实测 |
| 横竖屏 | ❌ 未验证 |
| 过期提示 | ✅ 已触发并正确显示（停机后 8 秒） |

> 编译通过只说明类型和链接没问题。真机截图证明**能连后端并渲染真实判定**，
> 但实时增量推送（收到新 `assessment`）尚未实测。

---

## 8. 已知限制

1. **影像依赖后端留存窗口**：超出保留期后 `rgb` 返回 404，画面显示"影像不可用"。
2. **无推送通知**：风险升级只在应用前台可见。
3. **无离线缓存**：断网即空态。
4. **双向判定仍共享走廊**：A→B 与 B→A 数值相同（后端 `SHARED_CORRIDOR`），
   界面如实展示，不额外解释。

---

## 9. 真机安装

1. Xcode 打开 `ShanXingZhiAn.xcodeproj`
2. `Signing & Capabilities` 选择 Development Team
3. 连接 iPhone 运行
4. 首次运行后在系统设置里信任开发者证书

**以上步骤未实际执行过。**

---

## 10. iPad 适配（iPadOS 18 / iPad 8）

目标设备：**iPad 8（10.2 英寸，A12，2160×1620px）**。
竖屏约 810×1080pt，横屏约 1080×810pt。

### 10.1 方向与分屏

| 配置 | 值 | 原因 |
|---|---|---|
| `UISupportedInterfaceOrientations~ipad` | 四向（含**倒置竖屏**） | iPad 允许任意方向持握 |
| `UIRequiresFullScreen` | `false` | 允许 Split View / Slide Over；分屏后宽度变化由自适应布局处理 |
| `TARGETED_DEVICE_FAMILY` | `1,2` | 同时支持 iPhone 与 iPad |

> iPad 8 为 A12，不支持台前调度（Stage Manager），因此不需要额外处理窗口化。

### 10.2 布局切换

| 可用宽度 | 布局 | 说明 |
|---|---|---|
| ≥ 900pt | **左右分栏** | 画面 + 数据面板（面板宽 `min(460, 宽度×0.38)`） |
| < 900pt | **上下堆叠** | 画面占满宽度，数据在下方 |

因此：

- iPad **横屏**（1080pt）→ 分栏
- iPad **竖屏**（810pt）→ 堆叠，画面更大
- iPad **分屏一半**（约 500pt）→ 堆叠，与 iPhone 一致

内容区最大宽度 **1280pt** 并居中，避免大屏上被无限拉伸。

### 10.3 字号与间距

| 项 | iPhone | iPad（regular） |
|---|---|---|
| 总体状态字号 | 26 | **32** |
| 双向卡片间距 | 10 | **14** |
| 页面内边距 | 14 | **20** |

### 10.4 设置页

- 包在 `ScrollView` 里：iPad 软键盘较高，不滚动会挡住"连接"按钮
- `.scrollDismissesKeyboard(.interactively)`：下滑即可收起键盘
- 内容最大宽度 520pt，居中，不拉满整屏

### 10.5 验证状态

| 项 | 状态 |
|---|---|
| 编译（iphonesimulator） | ✅ `BUILD SUCCEEDED` |
| 编译（iphoneos / arm64 真机） | ✅ `BUILD SUCCEEDED` |
| 产物 `UISupportedInterfaceOrientations~ipad` | ✅ 四向 |
| 产物 `UIRequiresFullScreen` | ✅ `false` |
| 产物 `UIDeviceFamily` | ✅ `[1, 2]` |
| iPad 真机运行 | ⬜ 设备可用，**尚未执行** |
| 横竖屏切换 | ❌ 未验证 |
| 分屏 / Slide Over | ❌ 未验证 |

> 以上均为编译期与产物核对。iPad 8 设备可用，但**尚未在设备上运行**，
> 因此横竖屏与分屏的实际显示效果仍未验证。
