import Foundation

/// M2-c：节流、拷贝与资源占用的统计。
///
/// 所有计数都在 ARSession 的 delegate 队列上更新，
/// 只有快照会被派发到主线程——避免跨线程写同一个变量。
struct CaptureStats: Equatable {
    var targetFPS: Int = 10
    var nativeFPS: Double = 0
    var captureFPS: Double = 0
    var elapsedSeconds: Double = 0

    var produced: Int = 0
    var skippedByThrottle: Int = 0
    var droppedEncoderBusy: Int = 0

    var depthCopyMs: Double = 0
    var confidenceCopyMs: Double = 0
    var depthBytes: Int = 0
    var confidenceBytes: Int = 0

    var residentMB: Double = 0
    var peakResidentMB: Double = 0
    var startResidentMB: Double = 0
    var trendMB: Double = 0
    var memorySampleCount: Int = 0
}
