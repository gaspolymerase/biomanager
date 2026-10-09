import SwiftUI
import WebKit

/// What the main screen needs to know about the page, and a handle to steer it.
@MainActor
final class LabPage: ObservableObject {
    @Published var progress: Double = 0
    @Published var loading = false
    @Published var offline = false
    @Published var download: URL?          // a finished download, to hand to the share sheet
    @Published var scanForPage = false     // the page (bench mode) asked for the scanner
    @Published var pageHasScan = false     // the page shows a Scan of its own (its phone tab bar)
    weak var webView: WKWebView?

    /// What the scanner read, handed to the page that asked (window.bmScanned).
    func deliverScan(_ value: String) {
        guard let data = try? JSONSerialization.data(withJSONObject: [value]),
              let json = String(data: data, encoding: .utf8) else { return }
        webView?.evaluateJavaScript("window.bmScanned && window.bmScanned(\(json)[0])")
    }

    func load(_ url: URL) {
        offline = false
        webView?.load(URLRequest(url: url))
    }

    func reload() {
        offline = false
        if webView?.url == nil, let server = Server.saved { load(server) } else { webView?.reload() }
    }
}

/// The lab server's BioManager, full screen. Links to the server stay in the
/// app; anything else opens in Safari. Uploads use the system picker (built
/// into WKWebView); downloads go to the share sheet.
struct LabWebView: UIViewRepresentable {
    let server: URL
    @ObservedObject var page: LabPage

    func makeCoordinator() -> Coordinator { Coordinator(server: server, page: page) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()      // stay signed in between launches
        config.allowsInlineMediaPlayback = true
        // Bench mode asks for the scanner: window.webkit.messageHandlers.bmScan.postMessage("scan").
        config.userContentController.add(context.coordinator, name: "bmScan")
        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = context.coordinator
        web.uiDelegate = context.coordinator
        web.allowsBackForwardNavigationGestures = true
        web.customUserAgent = nil
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0"
        web.evaluateJavaScript("navigator.userAgent") { result, _ in
            if let agent = result as? String { web.customUserAgent = agent + " BioManagerIOS/" + version }
        }
        let refresh = UIRefreshControl()
        refresh.addTarget(context.coordinator, action: #selector(Coordinator.pulled(_:)), for: .valueChanged)
        web.scrollView.refreshControl = refresh
        context.coordinator.observe(web)
        page.webView = web
        web.load(URLRequest(url: server))
        return web
    }

    func updateUIView(_ web: WKWebView, context: Context) {}

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate, WKScriptMessageHandler {
        let server: URL
        let page: LabPage
        private var progressWatch: NSKeyValueObservation?
        private var downloads: [WKDownload: URL] = [:]

        init(server: URL, page: LabPage) {
            self.server = server
            self.page = page
        }

        /// Only the lab server's own pages may open the scanner. A page with the
        /// phone tab bar says "page-has-scan": it has a Scan of its own, which asks
        /// for the scanner here, so the app's floating button stays hidden.
        func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
            guard message.name == "bmScan", message.frameInfo.securityOrigin.host == server.host else { return }
            let hasOwn = (message.body as? String) == "page-has-scan"
            Task { @MainActor in
                if hasOwn { page.pageHasScan = true } else { page.scanForPage = true }
            }
        }

        func observe(_ web: WKWebView) {
            progressWatch = web.observe(\.estimatedProgress, options: .new) { [weak self] web, _ in
                Task { @MainActor in self?.page.progress = web.estimatedProgress }
            }
        }

        @objc func pulled(_ control: UIRefreshControl) {
            Task { @MainActor in
                page.reload()
                control.endRefreshing()
            }
        }

        // Links: the server's stay here, everything else goes to Safari.
        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = action.request.url else { return decisionHandler(.cancel) }
            if url.scheme == "about" || url.scheme == "blob" || url.scheme == "data" || Server.owns(url, server: server) {
                return decisionHandler(action.shouldPerformDownload ? .download : .allow)
            }
            UIApplication.shared.open(url)
            decisionHandler(.cancel)
        }

        // A CSV export or a backup zip: download it instead of showing it.
        func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                     decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
            let disposition = (response.response as? HTTPURLResponse)?
                .value(forHTTPHeaderField: "Content-Disposition") ?? ""
            decisionHandler(disposition.lowercased().hasPrefix("attachment") || !response.canShowMIMEType
                            ? .download : .allow)
        }

        func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
            download.delegate = self
        }

        func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
            download.delegate = self
        }

        func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                      suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
            let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
            try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let destination = folder.appendingPathComponent(suggestedFilename)
            downloads[download] = destination
            completionHandler(destination)
        }

        func downloadDidFinish(_ download: WKDownload) {
            let file = downloads.removeValue(forKey: download)
            Task { @MainActor in page.download = file }
        }

        func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
            downloads.removeValue(forKey: download)
        }

        func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
            Task { @MainActor in
                page.loading = true
                page.pageHasScan = false   // each page says again
            }
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            Task { @MainActor in
                page.loading = false
                page.offline = false
            }
        }

        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            failed(webView, error)
        }

        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            failed(webView, error)
        }

        /// Only a server that cannot be reached shows the offline screen;
        /// anything else (a cancelled load, a form page that cannot be shown
        /// again) just starts over at home.
        private func failed(_ webView: WKWebView, _ error: Error) {
            let code = (error as? URLError)?.code
            Task { @MainActor in page.loading = false }
            switch code {
            case .cancelled?:
                return
            case .notConnectedToInternet?, .cannotFindHost?, .cannotConnectToHost?, .timedOut?,
                 .networkConnectionLost?, .dnsLookupFailed?, .secureConnectionFailed?,
                 .serverCertificateUntrusted?, .serverCertificateHasUnknownRoot?:
                Task { @MainActor in page.offline = true }
            default:
                if (error as NSError).domain == "WebKitErrorDomain" { return }  // e.g. a download took over
                webView.load(URLRequest(url: server))
            }
        }

        // window.open / target=_blank to the server: open it here.
        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                     for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let url = action.request.url {
                if Server.owns(url, server: server) { webView.load(action.request) } else { UIApplication.shared.open(url) }
            }
            return nil
        }

        // The app's confirm() prompts ("Sac 3 mice?").
        func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
            present(message: message, buttons: [("Cancel", .cancel, false), ("OK", .default, true)], webView: webView,
                    completion: completionHandler)
        }

        func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
            present(message: message, buttons: [("OK", .default, true)], webView: webView) { _ in completionHandler() }
        }

        private func present(message: String, buttons: [(String, UIAlertAction.Style, Bool)], webView: WKWebView,
                             completion: @escaping (Bool) -> Void) {
            guard let top = webView.window?.rootViewController?.topMost else { return completion(false) }
            let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            for (title, style, value) in buttons {
                alert.addAction(UIAlertAction(title: title, style: style) { _ in completion(value) })
            }
            top.present(alert, animated: true)
        }
    }
}

extension UIViewController {
    var topMost: UIViewController { presentedViewController?.topMost ?? self }
}
