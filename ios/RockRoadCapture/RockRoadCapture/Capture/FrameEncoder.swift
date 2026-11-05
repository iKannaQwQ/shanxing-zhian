import CoreVideo
import Foundation
import UIKit

/// 单帧编码的耗时与体积统计。
struct EncoderStats: Equatable {
    var convertMs: Double = 0
    var encodeMs: Double = 0
    var jpegBytes: Int = 0
    var error: String?
}

/// RGB 帧的转换 + JPEG 编码。
///
/// M2-b 只验证**正确性与成本**，不做上传、不做队列。
enum FrameEncoder {

    struct Output {
        var image: UIImage?
        var stats: EncoderStats
    }

    /// - Parameters:
    ///   - pixelBuffer: 来自 ARFrame.capturedImage
    ///   - quality: JPEG 质量
    ///   - orientation: ARKit 的 capturedImage 是 landscape-right，
    ///                  显示时需要旋转；编码时方向只写进 EXIF。
    static func encode(_ pixelBuffer: CVPixelBuffer,
                       quality: CGFloat = 0.72,
                       orientation: UIImage.Orientation = .right) -> Output {
        var stats = EncoderStats()

        let conversion = YUVConverter.makeBGRAImage(pixelBuffer)
        stats.convertMs = conversion.milliseconds
        guard let cgImage = conversion.image else {
            stats.error = conversion.error ?? "RGB 转换失败"
            return Output(image: nil, stats: stats)
        }

        let uiImage = UIImage(cgImage: cgImage, scale: 1, orientation: orientation)

        let encodeStart = CFAbsoluteTimeGetCurrent()
        let jpeg = uiImage.jpegData(compressionQuality: quality)
        stats.encodeMs = (CFAbsoluteTimeGetCurrent() - encodeStart) * 1000
        stats.jpegBytes = jpeg?.count ?? 0
        if jpeg == nil {
            stats.error = "JPEG 编码失败"
        }
        return Output(image: uiImage, stats: stats)
    }
}
