import CoreGraphics
import Foundation

/// 用 CGContext 直接缩放，替代 `UIImage.preparingThumbnail`。
///
/// 后者走 ImageIO 解码/编码，在热降频的设备上可能耗时上百毫秒；
/// 而它原先是在**打包队列**里执行的，等于把打包卡住。
enum ImageDownscale {

    static func thumbnail(_ image: CGImage, maxWidth: Int) -> CGImage? {
        guard maxWidth > 0, image.width > 0, image.height > 0 else { return nil }
        if image.width <= maxWidth { return image }

        let scale = Double(maxWidth) / Double(image.width)
        let height = max(1, Int((Double(image.height) * scale).rounded()))
        let bitmapInfo = CGImageAlphaInfo.noneSkipFirst.rawValue
            | CGBitmapInfo.byteOrder32Little.rawValue
        guard let context = CGContext(data: nil, width: maxWidth, height: height,
                                      bitsPerComponent: 8, bytesPerRow: 0,
                                      space: CGColorSpaceCreateDeviceRGB(),
                                      bitmapInfo: bitmapInfo) else { return nil }
        context.interpolationQuality = .medium
        context.draw(image, in: CGRect(x: 0, y: 0, width: maxWidth, height: height))
        return context.makeImage()
    }
}
