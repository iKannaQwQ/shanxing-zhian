import CoreVideo
import Foundation

/// 一帧的"事实"：全部来自同一个 ARFrame，不做任何推断。
///
/// M2-a 的唯一目的就是把这些数字读出来，用实测替换文档里的猜测。
struct FrameFacts: Equatable {
    var frameIndex: Int = 0
    var timestamp: TimeInterval = 0
    var tracking: String = "-"

    // RGB（ARFrame.capturedImage）
    var rgbWidth: Int = 0
    var rgbHeight: Int = 0
    var rgbFormat: String = "-"
    var rgbPlaneCount: Int = 0
    var rgbBytesPerRow: [Int] = []

    // 深度（ARFrame.sceneDepth.depthMap）
    var depthWidth: Int = 0
    var depthHeight: Int = 0
    var depthFormat: String = "-"
    var depthBytesPerRow: Int = 0

    // 置信度（ARFrame.sceneDepth.confidenceMap）
    var confidenceWidth: Int = 0
    var confidenceHeight: Int = 0
    var confidenceFormat: String = "-"
    var confidenceBytesPerRow: Int = 0

    // 内参（参考分辨率 = camera.imageResolution）
    var fx: Double = 0
    var fy: Double = 0
    var cx: Double = 0
    var cy: Double = 0
    var refWidth: Int = 0
    var refHeight: Int = 0

    var fps: Double = 0

    // 深度采样（每 0.5 s 更新一次，仅 float32 格式）
    var depthValidRatio: Double? = nil
    var depthMin: Float? = nil
    var depthMax: Float? = nil

    /// 深度相对 RGB 的缩放比。M5 对齐时要用，所以现在就量出来。
    var depthScaleX: Double {
        guard rgbWidth > 0 else { return 0 }
        return Double(depthWidth) / Double(rgbWidth)
    }

    var depthScaleY: Double {
        guard rgbHeight > 0 else { return 0 }
        return Double(depthHeight) / Double(rgbHeight)
    }

    /// 宽高比是否一致——决定"按比例缩放像素坐标"是否成立。
    var aspectMatches: Bool {
        guard rgbWidth > 0, rgbHeight > 0, depthWidth > 0, depthHeight > 0 else { return false }
        let rgbAspect = Double(rgbWidth) / Double(rgbHeight)
        let depthAspect = Double(depthWidth) / Double(depthHeight)
        return abs(rgbAspect - depthAspect) / rgbAspect < 0.02
    }
}

enum PixelFormatName {

    /// 用系统常量做匹配，而不是靠记忆 fourCC 字符串。
    static func describe(_ format: OSType) -> String {
        switch format {
        case kCVPixelFormatType_DepthFloat32:
            return "DepthFloat32"
        case kCVPixelFormatType_DisparityFloat32:
            return "DisparityFloat32"
        case kCVPixelFormatType_DepthFloat16:
            return "DepthFloat16"
        case kCVPixelFormatType_DisparityFloat16:
            return "DisparityFloat16"
        case kCVPixelFormatType_OneComponent8:
            return "OneComponent8"
        case kCVPixelFormatType_OneComponent32Float:
            return "OneComponent32Float"
        case kCVPixelFormatType_420YpCbCr8BiPlanarFullRange:
            return "420YpCbCr8BiPlanarFullRange"
        case kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange:
            return "420YpCbCr8BiPlanarVideoRange"
        case kCVPixelFormatType_32BGRA:
            return "32BGRA"
        default:
            return "未知(\(fourCC(format)))"
        }
    }

    static func fourCC(_ code: OSType) -> String {
        let bytes: [UInt8] = [
            UInt8((code >> 24) & 0xFF),
            UInt8((code >> 16) & 0xFF),
            UInt8((code >> 8) & 0xFF),
            UInt8(code & 0xFF),
        ]
        let text = String(bytes: bytes, encoding: .ascii) ?? "????"
        return text.trimmingCharacters(in: .whitespaces)
    }
}
