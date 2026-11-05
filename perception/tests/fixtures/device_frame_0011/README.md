# 真机采集样本（iPhone 12 Pro Max / iOS 27.0.1）

来自 MountainRoadCapture M4 落盘的第 11 帧，用于回归测试 Python 侧解码与置信度语义。

* `metadata.json` — 符合 contracts/schemas/frame_bundle.schema.json
* `depth.bin` — 256×192 float32（米），bytesPerRow=1024
* `confidence.bin` — 256×192 uint8，ARConfidenceLevel 0/1/2

> 未包含 `rgb.jpg`：原始画面含用户环境，不入库。
> 该帧拍摄距离 0.06–0.42 m，**低于 LiDAR 可靠近界**，仅用于格式与解码回归，
> 不代表沙盘工况（沙盘预期 0.4–0.8 m）。
