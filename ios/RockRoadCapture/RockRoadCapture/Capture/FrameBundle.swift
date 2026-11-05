import Foundation
import simd

/// 相机内参，已完成到**输出分辨率**的换算。
struct FrameIntrinsics {
    var fx: Double
    var fy: Double
    var cx: Double
    var cy: Double
    var width: Int
    var height: Int

    /// ARKit 的内参对应 imageResolution，输出分辨率更小时必须按比例缩放。
    static func scaled(from matrix: simd_float3x3,
                       sourceWidth: Int, sourceHeight: Int,
                       targetWidth: Int, targetHeight: Int) -> FrameIntrinsics {
        let sx = Double(targetWidth) / Double(sourceWidth)
        let sy = Double(targetHeight) / Double(sourceHeight)
        return FrameIntrinsics(
            fx: Double(matrix[0][0]) * sx,
            fy: Double(matrix[1][1]) * sy,
            cx: Double(matrix[2][0]) * sx,
            cy: Double(matrix[2][1]) * sy,
            width: targetWidth,
            height: targetHeight)
    }
}

/// 一帧里与图像缓冲无关的部分，用于跨队列传递。
struct FrameSnapshot {
    var frameID: String
    var sequence: Int
    var sessionID: String
    var deviceID: String
    var tsCaptureNs: Int64
    var intrinsics: FrameIntrinsics
    var pose: [Double]
    var trackingState: String
}

/// 一帧的完整打包结果（M4）。
///
/// 元数据结构严格对齐 `contracts/schemas/frame_bundle.schema.json`，
/// 字段名、枚举值、单位都按契约来，不自行发挥。
struct FrameBundle {
    var schemaVersion = "1.0"
    var snapshot: FrameSnapshot
    var jpegQuality: Double
    var rgbJPEG: Data
    var rgbWidth: Int
    var rgbHeight: Int
    var depth: RawPlane
    var confidence: RawPlane?

    /// multipart/form-data 中 `meta` part 的内容。
    /// - Parameter droppedSinceLast: 上次成功上传以来客户端丢弃的帧数，
    ///   让下游知道数据有缺口，而不是假装连续。
    func metadata(droppedSinceLast: Int = 0) -> [String: Any] {
        var dict: [String: Any] = [
            "schema_version": schemaVersion,
            "msg_type": "frame_bundle",
            "frame_id": snapshot.frameID,
            "device_id": snapshot.deviceID,
            "session_id": snapshot.sessionID,
            "seq": snapshot.sequence,
            "ts_capture_ns": snapshot.tsCaptureNs,
            "capture": [
                "width": rgbWidth,
                "height": rgbHeight,
                "bytes": rgbJPEG.count,
                "format": "jpeg",
                "jpeg_quality": jpegQuality,
                "orientation": "landscape_right",
            ],
            "intrinsics": [
                "fx": snapshot.intrinsics.fx,
                "fy": snapshot.intrinsics.fy,
                "cx": snapshot.intrinsics.cx,
                "cy": snapshot.intrinsics.cy,
                "reference_width": snapshot.intrinsics.width,
                "reference_height": snapshot.intrinsics.height,
            ],
            "depth": [
                "width": depth.width,
                "height": depth.height,
                "encoding": "float32_m",
                "scale_factor": 1.0,
                "invalid_value": 0,
                "aligned_to_rgb": true,
                "source": "arkit.sceneDepth",
            ],
            "pose": [
                "matrix": snapshot.pose,
                "tracking_state": snapshot.trackingState,
            ],
            "delivery": [
                "queue_depth": 0,
                "attempt": 1,
                "dropped_since_last": max(0, droppedSinceLast),
            ],
        ]

        if let confidence {
            dict["confidence"] = [
                "width": confidence.width,
                "height": confidence.height,
                "encoding": "uint8",
                "semantics": "ar_confidence_level",
            ]
        }
        return dict
    }

    func metadataJSON(droppedSinceLast: Int = 0) -> Data {
        (try? JSONSerialization.data(
            withJSONObject: metadata(droppedSinceLast: droppedSinceLast),
            options: [.prettyPrinted, .sortedKeys])) ?? Data()
    }

    var depthBytes: Int { depth.data.count }
    var confidenceBytes: Int { confidence?.data.count ?? 0 }
    var totalBytes: Int { rgbJPEG.count + depthBytes + confidenceBytes + metadataJSON().count }
}
