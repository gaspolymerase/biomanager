import SwiftUI

/// BioManager full screen, with a Scan button for cage cards and an offline screen.
struct MainView: View {
    let server: URL
    var onChangeServer: () -> Void

    @StateObject private var page = LabPage()
    @State private var scanning = false
    @State private var scanForPage = false    // this scan goes back to the page that asked, not to a link
    @State private var notice: String?
    @State private var keyboardUp = false

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            LabWebView(server: server, page: page)
                .ignoresSafeArea(.container, edges: .bottom)

            if page.loading && page.progress < 1 {
                VStack {
                    ProgressView(value: page.progress).tint(.accentColor)
                    Spacer()
                }
            }

            if page.offline {
                OfflineView(server: server, retry: page.reload, changeServer: onChangeServer)
            }

            if !page.offline && !keyboardUp && !page.pageHasScan {
                Button { scanForPage = false; startScan() } label: {
                    Image(systemName: "qrcode.viewfinder")
                        .font(.system(size: 22, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 56, height: 56)
                        .background(Color.accentColor, in: Circle())
                        .shadow(color: .black.opacity(0.25), radius: 6, y: 3)
                }
                .accessibilityLabel("Scan a cage card")
                .padding(20)
            }
        }
        .sheet(isPresented: $scanning) {
            NavigationStack {
                QRScanner { value in
                    scanning = false
                    if scanForPage {
                        scanForPage = false
                        page.deliverScan(value)
                    } else {
                        open(scanned: value)
                    }
                }
                .ignoresSafeArea()
                .navigationTitle("Scan a cage card")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { scanning = false } } }
            }
        }
        .sheet(item: Binding(get: { page.download.map(SharedFile.init) }, set: { _ in page.download = nil })) { file in
            ShareSheet(items: [file.url])
        }
        .alert(notice ?? "", isPresented: Binding(get: { notice != nil }, set: { if !$0 { notice = nil } })) {
            Button("OK", role: .cancel) {}
        }
        .onChange(of: page.scanForPage) { wanted in
            guard wanted else { return }
            page.scanForPage = false
            scanForPage = true
            startScan()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardWillShowNotification)) { _ in keyboardUp = true }
        .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardWillHideNotification)) { _ in keyboardUp = false }
    }

    private func startScan() {
        if QRScanner.isAvailable {
            scanning = true
        } else {
            notice = "Scanning needs a camera this iPhone lets BioManager use. The Camera app can scan cage cards too."
        }
    }

    private func open(scanned value: String) {
        guard let url = URL(string: value.trimmingCharacters(in: .whitespacesAndNewlines)), url.host != nil else {
            notice = "That code isn't a BioManager link."
            return
        }
        if Server.owns(url, server: server) {
            page.load(url)
        } else {
            notice = "That code is for a different server: \(url.host ?? value)"
        }
    }
}

struct OfflineView: View {
    let server: URL
    var retry: () -> Void
    var changeServer: () -> Void

    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: "wifi.exclamationmark")
                .font(.system(size: 40))
                .foregroundStyle(.secondary)
            Text("Can't reach the lab server").font(.title2.bold())
            Text("Couldn't reach \(server.host ?? server.absoluteString). Check the address, and that your phone is on the lab's network or VPN.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Button("Try again", action: retry)
                .buttonStyle(.borderedProminent)
                .buttonBorderShape(.capsule)
                .padding(.top, 8)
            Button("Change server", action: changeServer)
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(.systemBackground))
    }
}

struct SharedFile: Identifiable {
    let url: URL
    var id: URL { url }
}

struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]
    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
