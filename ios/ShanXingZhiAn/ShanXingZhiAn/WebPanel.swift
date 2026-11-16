import SwiftUI
import WebKit

/// 用 WKWebView 承载现有网页前端。
///
/// 选择网页壳而不是原生重写：网页端已经是完整可用的监控大屏，
/// 原生重写要重做全部视图与实时通道，4 天内无法交付且必然引入偏差。
struct WebPanel: UIViewRepresentable {
    let url: URL
    @Binding var isLoading: Bool
    @Binding var loadError: String?
    let reloadToken: Int

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.allowsInlineMediaPlayback = true
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = true
        // 网页自己处理安全区与滚动，WebView 不要再加一层
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        webView.isOpaque = false
        webView.backgroundColor = UIColor(red: 0.043, green: 0.071, blue: 0.125, alpha: 1)
        context.coordinator.start(url: url, in: webView)
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard context.coordinator.currentToken != reloadToken else { return }
        context.coordinator.currentToken = reloadToken
        webView.reload()
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(isLoading: $isLoading, loadError: $loadError)
    }

    final class Coordinator: NSObject, WKNavigationDelegate {
        private let isLoading: Binding<Bool>
        private let loadError: Binding<String?>
        var currentToken = 0

        init(isLoading: Binding<Bool>, loadError: Binding<String?>) {
            self.isLoading = isLoading
            self.loadError = loadError
        }

        func start(url: URL, in webView: WKWebView) {
            isLoading.wrappedValue = true
            loadError.wrappedValue = nil
            webView.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData))
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            isLoading.wrappedValue = false
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            report(error)
        }

        func webView(_ webView: WKWebView,
                     didFailProvisionalNavigation navigation: WKNavigation!,
                     withError error: Error) {
            report(error)
        }

        private func report(_ error: Error) {
            isLoading.wrappedValue = false
            // 用户主动取消（比如快速切换地址）不算错误
            let ns = error as NSError
            if ns.domain == NSURLErrorDomain && ns.code == NSURLErrorCancelled { return }
            loadError.wrappedValue = ns.localizedDescription
        }
    }
}
