import ARKit
import Foundation

/// M4：把一帧的 RGB/深度/置信度/元数据组装成 `FrameBundle`。
///
/// 输出分辨率固定 1280×960：YOLO 输入是 640，1280 给到 2× 余量；
/// 同时把编码耗时、内存分配和网络带宽都压到原来的约 45%。
enum FramePackager {

    static let outputWidth = 1280
    static let outputHeight = 960
    static let jpegQuality: CGFloat = 0.72

    struct Output {
        var bundle: FrameBundle?
        var image: CGImage?
        var stats: EncoderStats
        var error: String?
    }

    static func build(frame: ARFrame,
                      snapshot: FrameSnapshot,
                      depth: RawPlane?,
                      confidence: RawPlane?) -> Output {
        guard let depth else {
            return Output(bundle: nil, image: nil, stats: EncoderStats(),
                          error: "缺少深度拷贝")
        }

        let encoded = FrameEncoder.encode(frame.capturedImage,
                                          targetWidth: outputWidth,
                                          targetHeight: outputHeight,
                                          quality: jpegQuality)
        guard let jpeg = encoded.jpeg, let image = encoded.image else {
            return Output(bundle: nil, image: nil, stats: encoded.stats,
                          error: encoded.stats.error ?? "RGB 编码失败")
        }

        let bundle = FrameBundle(snapshot: snapshot,
                                 jpegQuality: Double(jpegQuality),
                                 rgbJPEG: jpeg,
                                 rgbWidth: outputWidth,
                                 rgbHeight: outputHeight,
                                 depth: depth,
                                 confidence: confidence)
        return Output(bundle: bundle, image: image, stats: encoded.stats, error: nil)
    }
}
