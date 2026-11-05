import CoreVideo
import Foundation

/// 一块平面的原始像素拷贝。
///
/// 刻意保留 `bytesPerRow` 而不是转成紧凑矩阵：
/// 下游（可视化、打包）都必须按真实行跨度解读，不能假设 padding 不存在。
struct RawPlane {
    var data: Data
    var width: Int
    var height: Int
    var bytesPerRow: Int
    var pixelFormat: OSType

    /// 拷贝一个 CVPixelBuffer 平面。返回 nil 表示锁定或取址失败。
    static func copy(from buffer: CVPixelBuffer, plane: Int = 0) -> RawPlane? {
        guard CVPixelBufferLockBaseAddress(buffer, .readOnly) == kCVReturnSuccess else {
            return nil
        }
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddressOfPlane(buffer, plane) else { return nil }

        let width = CVPixelBufferGetWidthOfPlane(buffer, plane)
        let height = CVPixelBufferGetHeightOfPlane(buffer, plane)
        let bytesPerRow = CVPixelBufferGetBytesPerRowOfPlane(buffer, plane)
        let size = bytesPerRow * height
        guard size > 0, width > 0, height > 0 else { return nil }

        let data = Data(bytes: base, count: size)
        return RawPlane(data: data,
                        width: width,
                        height: height,
                        bytesPerRow: bytesPerRow,
                        pixelFormat: CVPixelBufferGetPixelFormatType(buffer))
    }
}
