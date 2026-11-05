import CoreGraphics
import CoreVideo
import Foundation

/// 深度与置信度的伪彩色渲染（M3）。
///
/// 关键点：输入是 **M2-c 拷贝出来的 `RawPlane`**，不是 CVPixelBuffer。
/// 因此这一层如果画错了，说明拷贝链路有问题——可视化同时承担了校验职责。
enum Visualizers {

    /// 深度可视化使用的固定区间（米）。
    /// 固定而不是逐帧自适应，是为了让不同时刻的颜色可比较；
    /// 实测范围 0.16–3.17 m，这里留一点余量。
    static let depthMinMeters: Double = 0.2
    static let depthMaxMeters: Double = 3.0

    // MARK: - 深度

    static func depthColorized(_ plane: RawPlane,
                               minMeters: Double = depthMinMeters,
                               maxMeters: Double = depthMaxMeters) -> CGImage? {
        guard plane.pixelFormat == kCVPixelFormatType_DepthFloat32
                || plane.pixelFormat == kCVPixelFormatType_DisparityFloat32 else {
            return nil
        }
        let floatsPerRow = plane.bytesPerRow / MemoryLayout<Float32>.size
        guard floatsPerRow >= plane.width else { return nil }

        let count = plane.width * plane.height
        var pixels = [UInt8](repeating: 0, count: count * 4)
        var invalidPixels = 0

        plane.data.withUnsafeBytes { raw in
            guard let base = raw.baseAddress?.assumingMemoryBound(to: Float32.self) else { return }
            for y in 0..<plane.height {
                for x in 0..<plane.width {
                    let index = y * plane.width + x
                    let value = base[y * floatsPerRow + x]
                    let offset = index * 4

                    if !value.isFinite || value <= 0 {
                        // 无效像素：纯黑，与"很远"的深蓝区分开
                        pixels[offset] = 0
                        pixels[offset + 1] = 0
                        pixels[offset + 2] = 0
                        pixels[offset + 3] = 255
                        invalidPixels += 1
                        continue
                    }
                    let t = ((Double(value) - minMeters) / (maxMeters - minMeters)).clamped01()
                    let (r, g, b) = ramp(t)
                    pixels[offset] = b
                    pixels[offset + 1] = g
                    pixels[offset + 2] = r
                    pixels[offset + 3] = 255
                }
            }
        }

        _ = invalidPixels
        return makeImage(pixels: pixels, width: plane.width, height: plane.height)
    }

    // MARK: - 置信度

    static func confidenceColorized(_ plane: RawPlane) -> CGImage? {
        guard plane.pixelFormat == kCVPixelFormatType_OneComponent8 else { return nil }
        guard plane.bytesPerRow >= plane.width else { return nil }

        let count = plane.width * plane.height
        var pixels = [UInt8](repeating: 0, count: count * 4)

        plane.data.withUnsafeBytes { raw in
            guard let base = raw.baseAddress?.assumingMemoryBound(to: UInt8.self) else { return }
            for y in 0..<plane.height {
                for x in 0..<plane.width {
                    let level = base[y * plane.bytesPerRow + x]
                    let (r, g, b): (UInt8, UInt8, UInt8)
                    switch level {
                    case 0: (r, g, b) = (235, 70, 70)      // 低：红
                    case 1: (r, g, b) = (240, 205, 60)     // 中：黄
                    case 2: (r, g, b) = (90, 220, 120)     // 高：绿
                    default: (r, g, b) = (60, 60, 60)      // 未知：灰
                    }
                    let offset = (y * plane.width + x) * 4
                    pixels[offset] = b
                    pixels[offset + 1] = g
                    pixels[offset + 2] = r
                    pixels[offset + 3] = 255
                }
            }
        }

        return makeImage(pixels: pixels, width: plane.width, height: plane.height)
    }

    // MARK: - 辅助

    /// t ∈ [0,1] 映射成近红 → 黄 → 绿 → 青 → 蓝远。
    private static func ramp(_ t: Double) -> (UInt8, UInt8, UInt8) {
        let stops: [(Double, (Double, Double, Double))] = [
            (0.00, (255, 70, 70)),
            (0.25, (255, 205, 60)),
            (0.50, (90, 220, 120)),
            (0.75, (60, 190, 255)),
            (1.00, (50, 60, 210)),
        ]
        for index in 0..<(stops.count - 1) {
            let (t0, c0) = stops[index]
            let (t1, c1) = stops[index + 1]
            if t <= t1 {
                let local = (t - t0) / (t1 - t0)
                return (UInt8(c0.0 + (c1.0 - c0.0) * local),
                        UInt8(c0.1 + (c1.1 - c0.1) * local),
                        UInt8(c0.2 + (c1.2 - c0.2) * local))
            }
        }
        let last = stops[stops.count - 1].1
        return (UInt8(last.0), UInt8(last.1), UInt8(last.2))
    }

    private static func makeImage(pixels: [UInt8], width: Int, height: Int) -> CGImage? {
        guard width > 0, height > 0 else { return nil }
        let data = Data(pixels)
        guard let provider = CGDataProvider(data: data as CFData) else { return nil }
        let bitmapInfo = CGBitmapInfo(
            rawValue: CGImageAlphaInfo.noneSkipFirst.rawValue
                | CGBitmapInfo.byteOrder32Little.rawValue)
        return CGImage(width: width,
                       height: height,
                       bitsPerComponent: 8,
                       bitsPerPixel: 32,
                       bytesPerRow: width * 4,
                       space: CGColorSpaceCreateDeviceRGB(),
                       bitmapInfo: bitmapInfo,
                       provider: provider,
                       decode: nil,
                       shouldInterpolate: false,
                       intent: .defaultIntent)
    }
}

private extension Double {
    func clamped01() -> Double { Swift.min(1.0, Swift.max(0.0, self)) }
}
