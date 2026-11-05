import Foundation

/// M4：把打包结果写成磁盘上的四个文件，便于离线核对。
///
/// 目录结构：
///   Documents/FrameBundles/frame_0001_<uuid>/
///       metadata.json
///       rgb.jpg
///       depth.bin
///       confidence.bin
///
/// 只保存前若干帧：10 fps 连续写盘会产生持续 I/O，
/// 而 M4 只需要样本用于校验。
final class BundleStore {

    struct Stats {
        var saved = 0
        var saveLimit = 20
        var lastDirectory: String = "-"
        var lastTotalBytes = 0
        var lastMetadataBytes = 0
        var lastJpegBytes = 0
        var lastDepthBytes = 0
        var lastConfidenceBytes = 0
        var error: String?
    }

    private var stats = Stats()
    private let statsLock = NSLock()
    private let rootDirectory: URL
    private let queue = DispatchQueue(label: "com.mountain.roadcapture.store", qos: .utility)

    init(saveLimit: Int = 20) {
        stats.saveLimit = saveLimit
        let documents = FileManager.default.urls(for: .documentDirectory,
                                                 in: .userDomainMask)[0]
        rootDirectory = documents.appendingPathComponent("FrameBundles", isDirectory: true)
        try? FileManager.default.createDirectory(at: rootDirectory,
                                                 withIntermediateDirectories: true)
    }

    var rootURL: URL { rootDirectory }

    func snapshot() -> Stats {
        statsLock.lock()
        defer { statsLock.unlock() }
        return stats
    }

    /// 开始新会话时清空旧目录：每次运行只保留当轮样本，避免磁盘无限增长。
    func reset() {
        try? FileManager.default.removeItem(at: rootDirectory)
        try? FileManager.default.createDirectory(at: rootDirectory,
                                                 withIntermediateDirectories: true)
        statsLock.lock()
        stats = Stats()
        statsLock.unlock()
    }

    /// 异步写盘；超过上限直接跳过，不阻塞采集。
    func save(_ bundle: FrameBundle) {
        guard snapshot().saved < snapshot().saveLimit else { return }

        let directory = rootDirectory
            .appendingPathComponent(String(format: "frame_%04d_%@",
                                           bundle.snapshot.sequence,
                                           bundle.snapshot.frameID),
                                    isDirectory: true)
        queue.async { [weak self] in
            guard let self else { return }
            do {
                try FileManager.default.createDirectory(at: directory,
                                                        withIntermediateDirectories: true)
                try bundle.metadataJSON.write(to: directory.appendingPathComponent("metadata.json"))
                try bundle.rgbJPEG.write(to: directory.appendingPathComponent("rgb.jpg"))
                try bundle.depth.data.write(to: directory.appendingPathComponent("depth.bin"))
                if let confidence = bundle.confidence {
                    try confidence.data.write(to: directory.appendingPathComponent("confidence.bin"))
                }

                self.statsLock.lock()
                self.stats.saved += 1
                self.stats.lastDirectory = directory.lastPathComponent
                self.stats.lastTotalBytes = bundle.totalBytes
                self.stats.lastMetadataBytes = bundle.metadataJSON.count
                self.stats.lastJpegBytes = bundle.rgbJPEG.count
                self.stats.lastDepthBytes = bundle.depthBytes
                self.stats.lastConfidenceBytes = bundle.confidenceBytes
                self.stats.error = nil
                self.statsLock.unlock()
            } catch {
                self.statsLock.lock()
                self.stats.error = "写盘失败: \(error.localizedDescription)"
                self.statsLock.unlock()
            }
        }
    }
}
