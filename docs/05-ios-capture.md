# 05 · iOS 采集端（MountainRoadCapture）

> 当前阶段：**M1（ARKit 能力检查）**。
> 只做能力探测与界面展示，**尚未采集、尚未上传**。

---

## 0. 状态

| 项 | 结果 |
|---|---|
| 工程 | `ios/RockRoadCapture/RockRoadCapture.xcodeproj` |
| 语言 | Swift 5 语言模式（见 §5 说明） |
| 部署目标 | iOS 16.0 |
| Bundle ID | `com.mountain.roadcapture`（占位，需按你的开发者账号修改） |
| 模拟器 SDK 编译 | ✅ 通过 |
| 真机 SDK（arm64）编译 | ✅ 通过，无警告 |
| **真机运行验证** | ❌ **未进行**（没有设备，且模拟器无 ARKit） |

**没有在 iPhone 上跑过，所以不声称"M1 已通过"。** 编译通过 ≠ 功能验证。

---

## 1. 目录

```
ios/RockRoadCapture/
├── RockRoadCapture.xcodeproj/
│   ├── project.pbxproj
│   └── xcshareddata/xcschemes/RockRoadCapture.xcscheme
└── RockRoadCapture/
    ├── App.swift
    ├── Assets.xcassets/
    └── Capability/
        ├── CapabilityItem.swift     状态与条目模型
        ├── DeviceInfo.swift         硬件标识 -> 可读型号
        ├── CapabilityChecker.swift  能力探测（ARKit / AVFoundation）
        └── CapabilityView.swift     SwiftUI 界面
```

---

## 2. M1 检查了什么

| 检查项 | 判据 | 是否阻断 M2 |
|---|---|---|
| ARKit 世界追踪 | `ARWorldTrackingConfiguration.isSupported` | ✅ 阻断 |
| LiDAR sceneDepth | `ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)` | ✅ **硬门槛** |
| 平滑深度（可选） | `supportsFrameSemantics(.smoothedSceneDepth)` | ⭕ 不阻断 |
| 相机权限 | `AVCaptureDevice.authorizationStatus(for: .video)` | ✅ 阻断 |
| 设备型号 | `uname` 机器标识 + 型号表 | ⭕ 仅展示 |
| 运行环境 | `#if targetEnvironment(simulator)` | ⭕ 提示 |

设计取舍：

- **权威判据一律取运行时查询**，型号表只用于向用户解释原因。
  即使型号表认不出新机型，能力判定依然正确。
- **不用 Bool 表示结论**：`ok / fail / warning / info` 四态，
  因为"未授权"和"不支持"必须区分——前者用户可解决，后者只能换设备。

---

## 3. 在真机上运行

1. 用 Xcode 打开 `ios/RockRoadCapture/RockRoadCapture.xcodeproj`。
2. 选中 target → Signing & Capabilities → 选择你的 **Team**。
3. 如 Bundle ID 冲突，改成 `com.<你的名字>.roadcapture`。
4. 连接 iPhone 12 Pro，首次运行需在手机上「设置 → 通用 → VPN 与设备管理」信任开发者证书。
5. 运行后应看到 6 个检查项，全部为绿色时顶部横幅显示
   「可以进入 M2（RGB + 深度采集）」。

预期结果（iPhone 12 Pro）：

```
设备              iPhone 12 Pro（iPhone13,2）
系统版本           iOS 26.x
ARKit 世界追踪     支持
LiDAR 深度        支持          <- 关键
平滑深度          支持
相机权限          首次需申请
```

如果 LiDAR 一栏是红色，说明这台机器不是 Pro 机型（非 Pro 的 iPhone 没有 LiDAR）。

---

## 4. 验证方式与边界

本次实际执行过：

```bash
xcodebuild -project ios/RockRoadCapture/RockRoadCapture.xcodeproj \
  -scheme RockRoadCapture -sdk iphonesimulator \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath .build/ios CODE_SIGNING_ALLOWED=NO build
# ** BUILD SUCCEEDED **

xcodebuild ... -sdk iphoneos -destination 'generic/platform/iOS' ...
# ** BUILD SUCCEEDED **（arm64，无警告）
```

并确认生成的 `Info.plist` 含：

```
NSCameraUsageDescription = 用于采集道路 RGB 图像以进行落石检测；数据仅发送到本地局域网的开发机。
MinimumOSVersion = 16.0
CFBundleIdentifier = com.mountain.roadcapture
```

**未执行**：真机安装、权限弹窗、ARKit 能力查询的实际返回值。
因此本阶段的结论是"代码可编译、结构合理"，不是"功能已验证"。

---

## 5. 一个刻意的决定：先用 Swift 5 语言模式

Xcode 27 默认倾向 Swift 6 严格并发。M1 没有并发代码，但 M2 会引入
`ARSessionDelegate` 回调（后台线程）+ `@Published` 状态（主线程），
那才是真正需要处理 `Sendable` / actor 隔离的地方。

现在的选择是**显式固定 `SWIFT_VERSION = 5.0`**，把并发迁移推迟到 M2 一并做，
避免在还没有并发代码时先制造一套不成熟的隔离模型。
这不是"忘记升级"，是排期。

---

## 6. M1 已知限制

1. 没有单元测试。M1 的逻辑是"查询系统能力 → 展示"，唯一有效的测试是真机运行。
2. 模拟器无法运行 ARKit，也不能装（本机未安装 iOS 模拟器运行时）。
3. Bundle ID 与 Team 是占位值，真机安装前必须改。
4. 未做本地网络权限声明（`NSLocalNetworkUsageDescription`）——那是 M5 上传阶段的事，
   提前声明会让用户看到一个当前用不到的系统弹窗。

---

## 7. 下一步（M2 预告，尚未实施）

M2 要做 RGB + sceneDepth 同步采集，必须解决的几个问题：

1. **同一帧**：`ARFrame.capturedImage` 与 `sceneDepth` 必须取自同一个 `ARFrame` 实例，
   不能跨帧取。
2. **像素格式**：`capturedImage` 是 `420YpCbCr8BiPlanarFullRange`，需要正确处理
   plane 数与 bytesPerRow 后再转 JPEG。
3. **深度分辨率**：sceneDepth 约 256×192，**保留原分辨率**，不做 resize 后声称对齐。
4. **数据类型**：depthMap 原生是 `kCVPixelFormatType_DisparityFloat32`（米制），
   confidenceMap 是 `kCVPixelFormatType_ConfidenceMapLane8`（0/1/2 三档）。
5. **不阻塞 ARSession**：采集回调里只做浅拷贝，编码与上传放到后台队列。
