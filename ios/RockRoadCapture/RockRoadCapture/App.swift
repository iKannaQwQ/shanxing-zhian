import SwiftUI

@main
struct RockRoadCaptureApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}

/// 两个阶段各自一个 Tab：
/// * 能力检查（M1）：确认设备是否具备采集前提
/// * 采集实测（M2-a）：把真实的图像/深度格式量出来
struct RootView: View {
    var body: some View {
        TabView {
            CapabilityView()
                .tabItem {
                    Label("能力检查", systemImage: "checkmark.seal")
                }
            SessionView()
                .tabItem {
                    Label("采集实测", systemImage: "camera.viewfinder")
                }
        }
    }
}
