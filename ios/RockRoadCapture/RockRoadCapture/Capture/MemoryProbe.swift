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
    }

    private static let lock = NSLock()
    private static var peakMB: Double = 0

    static func current() -> Snapshot {
        let mb = residentMB()
        lock.lock()
        defer { lock.unlock() }
        if mb > peakMB { peakMB = mb }
        return Snapshot(residentMB: mb, peakResidentMB: peakMB)
    }

    static func resetPeak() {
        let mb = residentMB()
        lock.lock()
        peakMB = mb
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
