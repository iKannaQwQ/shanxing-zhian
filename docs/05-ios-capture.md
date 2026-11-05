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
| **真机运行验证** | ✅ **通过**（iPhone 12 Pro Max / iOS 27.0.1，2026-11-05） |
| M2-a 格式实测 | ✅ 已真机确认 |
| M2-b RGB 转码 | ✅ 已真机确认（颜色一致、方向正确） |

M1 已在真机上实际运行并确认全部能力项。实测输出见 §2.1。

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

### 2.1 真机实测结果

设备上「复制诊断信息」得到的原始输出：

```
MountainRoadCapture / M1 能力检查
时间: 2026-11-05T13:08:49Z
结论: 可以进入 M2
---
INFO 设备: iPhone 12 Pro Max（iPhone13,3）
INFO 系统版本: Version 27.0.1 (Build 24A446)
OK   ARKit 世界追踪: 支持
OK   LiDAR 深度（sceneDepth）: 支持
OK   平滑深度（smoothedSceneDepth）: 支持（可选）
OK   相机权限: 已授权
```

| 事实 | 值 | 影响 |
|---|---|---|
| 设备 | iPhone 12 Pro Max | 与项目书写的 "12 Pro" 不同（`iPhone13,2` 才是 12 Pro），但同样带 LiDAR |
| 系统 | **iOS 27.0.1** | 原计划按 iOS 26 评估；现在以 27.0.1 为准 |
| sceneDepth | 支持 | M2 可按计划实现 |
| smoothedSceneDepth | 支持 | 可在 M3 提供平滑预览选项 |
| 相机权限 | 已授权 | 无需重复弹窗 |

> 注意：`supportsFrameSemantics` 只回答"支持"，不回答"深度图多大、什么像素格式"。
> 这些必须在 M2 运行时实际读取，不能照抄文档。

---

## 3. 在真机上运行

1. 用 Xcode 打开 `ios/RockRoadCapture/RockRoadCapture.xcodeproj`。
2. 选中 target → Signing & Capabilities → 选择你的 **Team**。
3. 如 Bundle ID 冲突，改成 `com.<你的名字>.roadcapture`。
4. 连接 iPhone 12 Pro，首次运行需在手机上「设置 → 通用 → VPN 与设备管理」信任开发者证书。
5. 运行后应看到 6 个检查项，全部为绿色时顶部横幅显示
   「可以进入 M2（RGB + 深度采集）」。

实测结果（2026-11-05，iPhone 12 Pro Max / iOS 27.0.1）：

```
设备              iPhone 12 Pro Max（iPhone13,3）
系统版本           Version 27.0.1 (Build 24A446)
ARKit 世界追踪     支持
LiDAR 深度        支持          <- 关键，已确认
平滑深度          支持
相机权限          已授权
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

**已执行**：真机安装、权限授权、ARKit 能力查询并取得实际返回值（见 §2.1）。
**仍未执行**：任何数据采集——M1 本身不采集，这是范围决定，不是遗漏。

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

1. 没有单元测试。M1 的逻辑是"查询系统能力 → 展示"，唯一有效的测试是真机运行——
   已于 2026-11-05 在 iPhone 12 Pro Max 上完成。
2. 模拟器无法运行 ARKit，也不能装（本机未安装 iOS 模拟器运行时），故无模拟器回归。
3. Bundle ID 与 Team 是占位值，真机安装前必须改。
4. 未做本地网络权限声明（`NSLocalNetworkUsageDescription`）——那是 M5 上传阶段的事，
   提前声明会让用户看到一个当前用不到的系统弹窗。

---

## 7. M2-a：采集格式实测（已实现，待真机验证）

M2 的第一步**不是**写编码和上传，而是把格式量出来。因为
`supportsFrameSemantics(.sceneDepth)` 只回答"支不支持"，不回答"多大、什么格式"。

### 7.1 做了什么

新增 Tab「采集实测」，启动 `ARWorldTrackingConfiguration`（`frameSemantics = [.sceneDepth]`），
把**同一个 `ARFrame`** 里的以下数值实时显示并支持一键复制：

| 组 | 内容 |
|---|---|
| 运行 | 帧序号、跟踪状态、实测 FPS |
| RGB | 分辨率、像素格式、plane 数、每个 plane 的 bytesPerRow |
| 深度 | 分辨率、像素格式、bytesPerRow、有效像素比例、深度范围（米） |
| 置信度 | 分辨率、像素格式、bytesPerRow |
| 内参 | fx / fy / cx / cy、参考分辨率 |
| 对齐 | 深度/RGB 缩放比、宽高比是否一致 |

### 7.2 三个刻意的实现选择

1. **像素格式用系统常量匹配，不靠 fourCC 字符串记忆**。
   `PixelFormatName.describe` 里是 `case kCVPixelFormatType_DepthFloat32:` 这类分支，
   认不出的才回退到打印 fourCC。这样显示的名字不可能因为记忆错误而错。
2. **深度采样只在 float32 格式下进行**。其他格式直接返回"未采样"，
   绝不按猜测的内存布局去读——读错内存比读不到更糟。
3. **预览用 ARSCNView**，不自己做 YUV→RGB。M2-a 只需确认"会话真的在出图"，
   YUV 转换是 M2-b 的活，不应提前混进来。

### 7.3 实测结果（两次独立运行）

| 项 | 第一次 | 第二次 |
|---|---|---|
| RGB | 1920×1440 `420YpCbCr8BiPlanarFullRange`，2 plane，bpr=[1920, 1920] | 同左 |
| 深度 | 256×192 `DepthFloat32`，bpr=1024 | 同左 |
| 置信度 | 256×192 `OneComponent8`，bpr=256 | 同左 |
| 深度范围 | 0.190–2.883 m，有效 100% | 0.162–2.936 m，有效 100% |
| 深度/RGB 缩放 | 0.1333（= 1/7.5），宽高比一致 | 同左 |
| FPS | 30.0 | 30.0 |

结论：`bytesPerRow` 恰好都等于「宽度×每像素字节数」，即**无 padding**；
但代码仍按 `bytesPerRow` 走，不依赖这个巧合。

### 7.4 重要发现：内参不是常量

| 参数 | 第一次 | 第二次 | 变化 |
|---|---|---|---|
| fx = fy | 1474.81 | 1487.42 | +0.86% |
| cx | 932.79 | 935.89 | +3.1 px |
| cy | 719.97 | 712.43 | −7.5 px |

自动对焦会改变焦距（focus breathing），ARKit 的动态标定也会微调主点。
**因此内参必须逐帧记录，不能在启动时取一次复用。**

0.86% 的焦距误差在 0.5 m 处约等于 4 mm 的横向偏差——对 2 cm 的落石是不可忽略的比例。
`frame_bundle` 契约本来就是逐帧内参字段，但实现上必须真的按帧写。

### 7.5 这一版要回答的问题

- 深度图真实分辨率是多少？（文档说 256×192，但 iOS 27 上没实测过）
- 深度像素格式是 `DepthFloat32` 还是 `DisparityFloat32`？
- RGB 是几个 plane？每 plane 的 bytesPerRow 是多少？
- `camera.intrinsics` 的参考分辨率是不是 1920×1440？
- 深度与 RGB 的宽高比是否一致（决定 M5 能否按比例缩放）？

回答完这些，M2-b（YUV→RGB + JPEG）才有依据。

## 8. M2-b：RGB 转码与编码（已实现，待真机目视校验）

### 8.1 做了什么

| 文件 | 职责 |
|---|---|
| `YUVConverter.swift` | vImage 显式转换 `420YpCbCr8BiPlanar → BGRA` |
| `FrameEncoder.swift` | 转 CGImage / UIImage，并做 JPEG 编码，统计耗时与体积 |

界面上把 **ARKit 自己渲染的画面** 与 **vImage 转出来的画面** 并排显示，
颜色一致才算转码正确。

### 8.2 三个关键实现点

1. **显式使用 `bytesPerRow` 建 `vImage_Buffer`**，而不是假设行跨度等于宽度。
   实测这台设备恰好无 padding（1920 / 1920 / 1024 / 256），但代码不能依赖这个巧合。
2. **颜色矩阵与取值范围都不猜**：
   - 取值范围由像素格式决定（`FullRange` vs `VideoRange`）；
   - 矩阵优先读 CoreVideo 附件 `kCVImageBufferYCbCrMatrixKey`，读不到才回退 BT.709。
3. **通道重排 `[3,2,1,0]`**：vImage 输出的是内存序 A,R,G,B；
   CGImage 用 `byteOrder32Little + noneSkipFirst` 需要 B,G,R,A。

### 8.3 不阻塞 ARSession

编码放在独立串行队列 `com.mountain.roadcapture.encoder`，并且：
- 每 0.5 s 才触发一次（M2-b 是校验阶段，不需要满帧率）；
- 用 `NSLock` 保护的 `encodeInFlight` 做背压：上一帧还没编完就**直接跳过**，
  宁可少一帧也不在队列里堆积；
- 闭包同时持有 `ARFrame`：ARKit 的像素缓冲生命周期绑定在 ARFrame 上，
  只保留 `CVPixelBuffer` 在极端情况下可能遇到缓冲被回收。

### 8.4 要目视确认的两件事

1. **颜色**：左右两张图的色调应基本一致。若左边正常、右边偏绿/偏紫，
   说明矩阵或通道重排错了。
2. **方向**：`capturedImage` 原生是 landscape-right，
   竖屏下若显示为横躺，说明 `UIImage.Orientation` 选错，改 `.right` / `.up` 即可。

这两项只能靠眼睛看，代码层面无法自证。

### 8.5 真机校验结果（2026-11-05）

| 校验项 | 结果 |
|---|---|
| 颜色 | ✅ 与 ARKit 预览色调一致 |
| 方向 | ✅ 正向，与预览一致 |
| YUV→BGRA | **1.29 ms** |
| JPEG 编码 | **31.26 ms** |
| JPEG 体积 | **468 KB**（q=0.72） |

单帧合计约 32.5 ms。10 FPS 下约占单核 1/3，能跑但无冗余——
M2-c 若实测持续丢帧，退路是降到 1280×960 或改用硬件 `VTCompressionSession`。

### 8.6 未做

- 编码结果**不落盘、不上传**，只用于测量与显示；
- JPEG 的最终方向与命名（是否烘焙旋转）留给 M4 打包时决定。

## 9. 下一步（M2-c，尚未实施）

M2-c 的目标是把「能采」变成「能稳定地以 10 FPS 采」，具体四件事：

1. **节流**：ARKit 原生 30 fps，每 3 帧取 1 帧，不改 session 配置。
2. **深度/置信度原始拷贝**：`Float32` 保留 256×192，按 `bytesPerRow` 逐行拷贝
   （不假设紧凑），置信度按 `uint8` 拷贝。
3. **内存管理**：`autoreleasepool` 包裹、只保留当前帧、记录峰值占用。
4. **稳定性观察**：持续运行下的实际出帧率、丢帧数、发热与内存曲线。

之后才是 M3（深度/置信度伪彩色可视化）。
