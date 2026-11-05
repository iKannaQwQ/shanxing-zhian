import Foundation
import Darwin

/// 进程内存占用探测。
///
/// 只读取 `resident_size`，不引入任何需要额外权限的接口；
/// 目的是观察"长时间运行会不会一路涨"，而不是精确计量。
enum MemoryProbe {

    struct Snapshot {
        var residentMB: Double = 0
        var peakResidentMB: Double = 0
        var startResidentMB: Double = 0
        var trendMB: Double = 0        // 当前 - 起始，正数代表在涨
        var sampleCount: Int = 0
    }

    private static let lock = NSLock()
    private static var peakMB: Double = 0
    private static var startMB: Double = 0
    private static var lastSampleAt: Date = .distantPast

    /// 每 5 s 记录一次，只保留最近 24 个点（约 2 分钟窗口）。
    /// 单点快照无法区分"波动"与"泄漏"，趋势才可以。
    private static var samples: [Double] = []

    static func current() -> Snapshot {
        let mb = residentMB()
        lock.lock()
        defer { lock.unlock() }
        if startMB == 0 { startMB = mb }
        if mb > peakMB { peakMB = mb }

        let now = Date()
        if now.timeIntervalSince(lastSampleAt) >= 5 {
            lastSampleAt = now
            samples.append(mb)
            if samples.count > 24 { samples.removeFirst() }
        }
        let trend = samples.count >= 2 ? (mb - startMB) : 0
        return Snapshot(residentMB: mb,
                        peakResidentMB: peakMB,
                        startResidentMB: startMB,
                        trendMB: trend,
                        sampleCount: samples.count)
    }

    static func resetPeak() {
        let mb = residentMB()
        lock.lock()
        peakMB = mb
        startMB = mb
        samples = [mb]
        lastSampleAt = Date()
        lock.unlock()
    }

    private static func residentMB() -> Double {
        var info = mach_task_basic_info()
        var count = mach_msg_type_number_t(
            MemoryLayout<mach_task_basic_info>.size / MemoryLayout<natural_t>.size)
        let status = withUnsafeMutablePointer(to: &info) { pointer in
            pointer.withMemoryRebound(to: integer_t.self, capacity: 1) { rebound in
                task_info(mach_task_self_, task_flavor_t(MACH_TASK_BASIC_INFO), rebound, &count)
            }
        }
        guard status == KERN_SUCCESS else { return 0 }
        return Double(info.resident_size) / 1024.0 / 1024.0
    }
}
