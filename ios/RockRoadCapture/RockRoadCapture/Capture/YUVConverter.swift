import Accelerate
import CoreGraphics
import CoreVideo
import Foundation

/// YUV(420 biplanar) -> BGRA 的显式转换。
///
/// 为什么不用 CIImage 一行搞定：
///   * 这里需要**显式按 bytesPerRow 建 vImage_Buffer**，把"行跨度可能不等于宽度"这件事
///     落实到代码里，而不是依赖某个高层 API 隐式处理；
///   * 纯 CPU、无 GPU 上下文，便于后续在采集流水线里做确定性测量。
///
/// 颜色矩阵与取值范围都不是猜的：
///   * 取值范围由像素格式决定（FullRange / VideoRange）；
///   * 颜色矩阵优先读 CoreVideo 附件 `kCVImageBufferYCbCrMatrixKey`，
///     读不到才回退 BT.709（ARKit 的 HD 视频默认矩阵）。
enum YUVConverter {

    struct Result {
        var image: CGImage?
        var milliseconds: Double = 0
        var error: String?
    }

    static func makeBGRAImage(_ pixelBuffer: CVPixelBuffer) -> Result {
        let start = CFAbsoluteTimeGetCurrent()
        let format = CVPixelBufferGetPixelFormatType(pixelBuffer)

        let isFullRange: Bool
        switch format {
        case kCVPixelFormatType_420YpCbCr8BiPlanarFullRange:
            isFullRange = true
        case kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange:
            isFullRange = false
        default:
            return Result(image: nil, milliseconds: 0,
                          error: "不支持的 RGB 像素格式: \(PixelFormatName.describe(format))")
        }

        guard CVPixelBufferGetPlaneCount(pixelBuffer) >= 2 else {
            return Result(image: nil, milliseconds: 0, error: "期望 2 个平面，实际 \(CVPixelBufferGetPlaneCount(pixelBuffer))")
        }

        guard CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly) == kCVReturnSuccess else {
            return Result(image: nil, milliseconds: 0, error: "锁定像素缓冲失败")
        }
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }

        let width = CVPixelBufferGetWidth(pixelBuffer)
        let height = CVPixelBufferGetHeight(pixelBuffer)

        guard let lumaBase = CVPixelBufferGetBaseAddressOfPlane(pixelBuffer, 0),
              let chromaBase = CVPixelBufferGetBaseAddressOfPlane(pixelBuffer, 1) else {
            return Result(image: nil, milliseconds: 0, error: "无法取得平面基址")
        }

        let lumaStride = CVPixelBufferGetBytesPerRowOfPlane(pixelBuffer, 0)
        let chromaStride = CVPixelBufferGetBytesPerRowOfPlane(pixelBuffer, 1)

        var sourceLuma = vImage_Buffer(
            data: lumaBase,
            height: vImagePixelCount(height),
            width: vImagePixelCount(width),
            rowBytes: lumaStride)
        var sourceChroma = vImage_Buffer(
            data: chromaBase,
            height: vImagePixelCount(height / 2),
            width: vImagePixelCount(width / 2),
            rowBytes: chromaStride)

        let destinationStride = width * 4
        guard let destinationData = malloc(destinationStride * height) else {
            return Result(image: nil, milliseconds: 0, error: "分配目标缓冲失败")
        }
        var destination = vImage_Buffer(
            data: destinationData,
            height: vImagePixelCount(height),
            width: vImagePixelCount(width),
            rowBytes: destinationStride)

        var matrix = colorMatrix(for: pixelBuffer)
        var range = pixelRange(fullRange: isFullRange)
        var conversionInfo = vImage_YpCbCrToARGB()

        let infoStatus = vImageConvert_YpCbCrToARGB_GenerateConversion(
            &matrix, &range, &conversionInfo,
            kvImage420Yp8_CbCr8, kvImageARGB8888, vImage_Flags(kvImageNoFlags))
        guard infoStatus == kvImageNoError else {
            free(destinationData)
            return Result(image: nil, milliseconds: 0, error: "生成转换信息失败: \(infoStatus)")
        }

        // ARGB8888 在内存中的字节序是 A,R,G,B；CGImage 用 little-endian + alphaFirst
        // 需要内存布局 B,G,R,A，因此通道重排。
        let permuteMap: [UInt8] = [3, 2, 1, 0]
        let convertStatus = vImageConvert_420Yp8_CbCr8ToARGB8888(
            &sourceLuma, &sourceChroma, &destination, &conversionInfo,
            permuteMap, 255, vImage_Flags(kvImageNoFlags))
        guard convertStatus == kvImageNoError else {
            free(destinationData)
            return Result(image: nil, milliseconds: 0, error: "YUV 转 BGRA 失败: \(convertStatus)")
        }

        let data = Data(bytesNoCopy: destinationData,
                        count: destinationStride * height,
                        deallocator: .free)
        guard let provider = CGDataProvider(data: data as CFData) else {
            return Result(image: nil, milliseconds: 0, error: "创建 CGDataProvider 失败")
        }

        let bitmapInfo = CGBitmapInfo(
            rawValue: CGImageAlphaInfo.noneSkipFirst.rawValue
                | CGBitmapInfo.byteOrder32Little.rawValue)
        guard let image = CGImage(
            width: width,
            height: height,
            bitsPerComponent: 8,
            bitsPerPixel: 32,
            bytesPerRow: destinationStride,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: bitmapInfo,
            provider: provider,
            decode: nil,
            shouldInterpolate: true,
            intent: .defaultIntent) else {
            return Result(image: nil, milliseconds: 0, error: "创建 CGImage 失败")
        }

        return Result(image: image,
                      milliseconds: (CFAbsoluteTimeGetCurrent() - start) * 1000,
                      error: nil)
    }

    // MARK: - 参数选择

    private static func colorMatrix(for pixelBuffer: CVPixelBuffer) -> vImage_YpCbCrToARGBMatrix {
        if let attachment = CVBufferCopyAttachment(
            pixelBuffer, kCVImageBufferYCbCrMatrixKey, nil) as? String,
           attachment == (kCVImageBufferYCbCrMatrix_ITU_R_601_4 as String) {
            return kvImage_YpCbCrToARGBMatrix_ITU_R_601_4.pointee
        }
        return kvImage_YpCbCrToARGBMatrix_ITU_R_709_2.pointee
    }

    private static func pixelRange(fullRange: Bool) -> vImage_YpCbCrPixelRange {
        if fullRange {
            // Y: 0..255, CbCr: 0..255 (center 128)
            return vImage_YpCbCrPixelRange(
                Yp_bias: 0, CbCr_bias: 128,
                YpRangeMax: 255, CbCrRangeMax: 255,
                YpMax: 255, YpMin: 0, CbCrMax: 255, CbCrMin: 0)
        }
        // BT.709 video range: Y 16..235, CbCr 16..240
        return vImage_YpCbCrPixelRange(
            Yp_bias: 16, CbCr_bias: 128,
            YpRangeMax: 235, CbCrRangeMax: 240,
            YpMax: 235, YpMin: 16, CbCrMax: 240, CbCrMin: 16)
    }
}
