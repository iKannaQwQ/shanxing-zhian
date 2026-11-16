# 23 · iOS 应用「山行智安」

> 把网页前端封装成可安装的 iPhone 应用。目录：`ios/ShanXingZhiAn/`
> 状态：**编译通过（模拟器）**，**未在真机运行过**。

---

## 1. 为什么是"网页壳"而不是原生重写

| 方案 | 工作量 | 风险 |
|---|---|---|
| **WKWebView 封装（已选）** | 半天 | 低；与网页端行为完全一致 |
| SwiftUI 原生重写 | 3–5 天 | 高；要重做全部视图、WebSocket、状态机 |

网页端已经是可用的监控大屏（左画面 / 右数据 + 实时通道）。
原生重写在交付期内做不完，且必然与网页端产生行为差异。
**这是工程取舍，不是技术上限。**

---

## 2. 结构

```
ios/ShanXingZhiAn/
├── ShanXingZhiAn.xcodeproj/
└── ShanXingZhiAn/
    ├── ShanXingZhiAnApp.swift   入口
    ├── RootView.swift           设置页 / 主界面 / 加载与失败态
    ├── WebPanel.swift           WKWebView 封装（UIViewRepresentable）
    ├── ServerConfig.swift       服务地址规范化与持久化
    ├── Info.plist               显示名、局域网权限、ATS 例外
    └── Assets.xcassets/         AppIcon（由网页 favicon.svg 复刻）
```

**图标**：`frontend/public/favicon.svg` 的几何形状（深色圆角底 + 琥珀山形 + 红点）
用 Pillow 以 4× 超采样重绘为 1024×1024 PNG。iOS 图标不允许透明，因此铺满底色。

---

## 3. 服务地址

首次启动进入设置页，输入电脑的局域网地址：

```
192.168.1.5:8000        # 只填 IP 即可，端口默认 8000
http://192.168.1.5:8000 # 完整写法也接受
```

- 规范化：自动补 `http://`、补 8000 端口、去掉路径与查询
- 持久化：`UserDefaults`，键 `server_base_url`
- 主界面右下角齿轮可随时改地址

> 手机与电脑必须在**同一局域网**，且后端已 `bash scripts/start_all.sh` 启动。

---

## 4. 网络权限（两个都必须有）

| 配置 | 作用 |
|---|---|
| `NSAppTransportSecurity → NSAllowsArbitraryLoads` | 允许 HTTP（后端是 http 而非 https） |
| `NSLocalNetworkUsageDescription` | iOS 14+ 访问局域网会弹权限框，没有这个键会直接失败 |

> 生产环境应改为 HTTPS + 关闭 `NSAllowsArbitraryLoads`。当前为局域网演示。

---

## 5. 编译与安装

```bash
# 模拟器编译（已验证）
cd ios/ShanXingZhiAn
xcodebuild -project ShanXingZhiAn.xcodeproj -target ShanXingZhiAn \
  -sdk iphonesimulator -configuration Debug \
  CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO build
```

真机安装需要：

1. 用 Xcode 打开 `ShanXingZhiAn.xcodeproj`
2. `Signing & Capabilities` 里选自己的 **Development Team**
3. 连接 iPhone，选择设备后 Run
4. 首次运行需在 `设置 → 通用 → VPN 与设备管理` 里信任开发者证书

**以上 1–4 未实际执行过**——本仓库没有真机验证记录。

---

## 6. 已核对 / 未核对

| 项 | 状态 |
|---|---|
| 模拟器编译 | ✅ `BUILD SUCCEEDED` |
| 显示名「山行智安」 | ✅ 产物 Info.plist 为中文 |
| Bundle ID `com.shanxing.zhian` | ✅ |
| 最低系统 iOS 16.0 | ✅ |
| AppIcon 编入 Assets.car | ✅ 6 个尺寸变体 |
| **真机运行** | ❌ **未验证** |
| **真机连接后端** | ❌ **未验证** |
| 横竖屏切换 | ❌ 未验证 |
| 弱网 / 后端离线提示 | ⚠️ 代码有失败态与重试，未实测 |

---

## 7. 已知限制

1. **依赖网页端**：网页改了要重新构建前端产物，App 不需要改（它加载的是服务端页面）。
2. **离线不可用**：App 只是壳，没有本地缓存与离线数据。
3. **无推送**：风险升级不会触发系统通知，需保持前台。
4. **不能上架 App Store**：`NSAllowsArbitraryLoads` 与局域网依赖，仅适合内部分发。
