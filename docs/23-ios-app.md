# 23 · iOS 应用「山行智安」（SwiftUI 原生）

> 目录：`ios/ShanXingZhiAn/`　语言：SwiftUI（iOS 16+）
> 状态：**编译通过**；**运行时未验证**（本机无模拟器运行时，也未上真机）。

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
| **运行时行为** | ❌ **未验证**（无模拟器运行时，未上真机） |
| 与后端联调 | ❌ **未验证** |
| 横竖屏 | ❌ 未验证 |

> 编译通过只说明类型和链接没问题，**不说明界面正确、也不说明能连上后端**。

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
