import ARKit
import SwiftUI

/// 把 ARSession 的画面显示出来。
///
/// 用 ARSCNView 而不是自己解码 CVPixelBuffer：M2-a 只需要确认"会话真的在出图"，
/// YUV→RGB 的转换是 M2-b 的工作，不应提前混进来。
struct ARPreviewView: UIViewRepresentable {
    let session: ARSession

    func makeUIView(context: Context) -> ARSCNView {
        let view = ARSCNView(frame: .zero)
        view.session = session
        view.scene = SCNScene()
        view.automaticallyUpdatesLighting = false
        view.antialiasingMode = .none
        return view
    }

    func updateUIView(_ uiView: ARSCNView, context: Context) {}
}
