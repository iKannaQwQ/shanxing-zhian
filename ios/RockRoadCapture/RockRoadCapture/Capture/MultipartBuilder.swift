import Foundation

/// multipart/form-data 组装。
///
/// 与 `scripts/dev_receiver.py` 的字段约定一致：
///   meta        表单字段（无 filename），application/json
///   rgb         文件 rgb.jpg
///   depth       文件 depth.bin
///   confidence  文件 confidence.bin（可选）
enum MultipartBuilder {

    struct Part {
        var name: String
        var filename: String?
        var contentType: String?
        var data: Data
    }

    static func makeBody(parts: [Part], boundary: String) -> Data {
        var body = Data()
        for part in parts {
            body.appendString("--\(boundary)\r\n")
            if let filename = part.filename {
                body.appendString("Content-Disposition: form-data; name=\"\(part.name)\"; "
                                  + "filename=\"\(filename)\"\r\n")
            } else {
                body.appendString("Content-Disposition: form-data; name=\"\(part.name)\"\r\n")
            }
            if let contentType = part.contentType {
                body.appendString("Content-Type: \(contentType)\r\n")
            }
            body.appendString("\r\n")
            body.append(part.data)
            body.appendString("\r\n")
        }
        body.appendString("--\(boundary)--\r\n")
        return body
    }
}

private extension Data {
    mutating func appendString(_ string: String) {
        if let data = string.data(using: .utf8) {
            append(data)
        }
    }
}
