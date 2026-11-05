import CoreVideo
import Foundation

/// 直接在**裸内存**上采样深度，不依赖 CVPixelBuffer。
///
/// 这样同一个函数既能校验"从 ARKit 拷出来的数据是否完好"，
/// 也能在 M4 把数据写成文件后重新读取验证——两边用同一套判据。
enum DepthSampler {

    struct Stats: Equatable {
        var validRatio: Double
        var minValue: Float
        var maxValue: Float
        var sampledCount: Int
    }

    /// 只支持 float32 的深度格式；其他格式返回 nil，绝不按猜测的布局解读。
    static func stats(from data: Data,
                      width: Int,
                      height: Int,
                      bytesPerRow: Int,
                      pixelFormat: OSType,
                      stride: Int = 4) -> Stats? {
        guard pixelFormat == kCVPixelFormatType_DepthFloat32
                || pixelFormat == kCVPixelFormatType_DisparityFloat32 else {
            return nil
        }
        let floatsPerRow = bytesPerRow / MemoryLayout<Float32>.size
        guard floatsPerRow >= width, width > 0, height > 0 else { return nil }
        guard data.count >= bytesPerRow * height else { return nil }

        var valid = 0
        var total = 0
        var minValue = Float.greatestFiniteMagnitude
        var maxValue = -Float.greatestFiniteMagnitude

        data.withUnsafeBytes { raw in
            guard let base = raw.baseAddress?.assumingMemoryBound(to: Float32.self) else { return }
            var y = 0
            while y < height {
                var x = 0
                while x < width {
                    let value = base[y * floatsPerRow + x]
                    total += 1
                    if value.isFinite && value > 0 {
                        valid += 1
                        if value < minValue { minValue = value }
                        if value > maxValue { maxValue = value }
                    }
                    x += stride
                }
                y += stride
            }
        }

        guard total > 0, valid > 0 else { return nil }
        return Stats(validRatio: Double(valid) / Double(total),
                     minValue: minValue,
                     maxValue: maxValue,
                     sampledCount: total)
    }
}
