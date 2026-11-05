import CoreVideo
import Foundation
import UIKit

/// 单帧编码的耗时与体积统计。
struct EncoderStats: Equatable {
    var convertMs: Double = 0
    var encodeMs: Double = 0
    var jpegBytes: Int = 0
    var pixelWidth: Int = 0
    var pixelHeight: Int = 0
    var error: String?
}

/// RGB 帧的转换 + JPEG 编码。
enum FrameEncoder {

    struct Output {
        var image: CGImage?
        var jpeg: Data?
        var stats: EncoderStats
    }

    /// - Parameters:
    ///   - targetWidth/targetHeight: 输出分辨率（M4 起固定 1280×960）
    ///   - quality: JPEG 质量
    static func encode(_ pixelBuffer: CVPixelBuffer,
                       targetWidth: Int,
                       targetHeight: Int,
                       quality: CGFloat = 0.72) -> Output {
        var stats = EncoderStats()
        stats.pixelWidth = targetWidth
        stats.pixelHeight = targetHeight

        let conversion = YUVConverter.makeBGRAImage(pixelBuffer,
                                                    targetWidth: targetWidth,
                                                    targetHeight: targetHeight)
        stats.convertMs = conversion.milliseconds
        guard let cgImage = conversion.image else {
            stats.error = conversion.error ?? "RGB 转换失败"
            return Output(image: nil, jpeg: nil, stats: stats)
        }

        // 方向只影响显示与 EXIF；这里保持图像数据为原始 landscape-right。
        let uiImage = UIImage(cgImage: cgImage, scale: 1, orientation: .up)

        let encodeStart = CFAbsoluteTimeGetCurrent()
        let jpeg = uiImage.jpegData(compressionQuality: quality)
        stats.encodeMs = (CFAbsoluteTimeGetCurrent() - encodeStart) * 1000
        stats.jpegBytes = jpeg?.count ?? 0
        if jpeg == nil {
            stats.error = "JPEG 编码失败"
        }
        return Output(image: cgImage, jpeg: jpeg, stats: stats)
    }
}
