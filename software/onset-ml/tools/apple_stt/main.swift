// apple-stt -- transcribe a WAV with Apple's on-device SpeechAnalyzer (macOS 26+).
//
// Nothing leaves the machine and no model is downloaded from a third party: the
// SpeechTranscriber asset is an OS-managed download, installed on first use.
//
//   apple-stt <file.wav> [--locale en-US]
//   -> JSON on stdout: {"ok":true,"text":"...","locale":"en-US","seconds":3.0}
import AVFoundation
import Foundation
import Speech

struct Out: Encodable {
    var ok: Bool
    var text: String = ""
    var locale: String = ""
    var seconds: Double = 0
    var error: String = ""
}

func emit(_ o: Out) -> Never {
    let enc = JSONEncoder()
    enc.outputFormatting = [.withoutEscapingSlashes]
    if let d = try? enc.encode(o), let s = String(data: d, encoding: .utf8) { print(s) }
    exit(o.ok ? 0 : 1)
}

let args = CommandLine.arguments
guard args.count >= 2 else { emit(Out(ok: false, error: "usage: apple-stt <file.wav> [--locale en-US]")) }
let path = args[1]
var localeID = "en-US"
if let i = args.firstIndex(of: "--locale"), i + 1 < args.count { localeID = args[i + 1] }

let url = URL(fileURLWithPath: path)
guard FileManager.default.fileExists(atPath: path) else { emit(Out(ok: false, error: "no such file: \(path)")) }

let sem = DispatchSemaphore(value: 0)
var result = Out(ok: false, locale: localeID)

Task {
    do {
        let locale = Locale(identifier: localeID)
        let supported = await SpeechTranscriber.supportedLocales
        guard supported.contains(where: { $0.identifier(.bcp47) == locale.identifier(.bcp47) }) else {
            result = Out(ok: false, locale: localeID,
                         error: "locale not supported; available: "
                              + supported.map { $0.identifier(.bcp47) }.sorted().joined(separator: ","))
            sem.signal(); return
        }

        let transcriber = SpeechTranscriber(locale: locale, preset: .transcription)
        // OS-managed asset; a no-op once installed.
        if let req = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
            try await req.downloadAndInstall()
        }

        let analyzer = SpeechAnalyzer(modules: [transcriber])
        let file = try AVAudioFile(forReading: url)
        let seconds = Double(file.length) / file.fileFormat.sampleRate

        let collect = Task { () -> String in
            var acc = ""
            for try await r in transcriber.results {
                acc += String(r.text.characters)
            }
            return acc
        }

        _ = try await analyzer.analyzeSequence(from: file)
        try await analyzer.finalizeAndFinishThroughEndOfInput()
        let text = try await collect.value
        result = Out(ok: true, text: text.trimmingCharacters(in: .whitespacesAndNewlines),
                     locale: localeID, seconds: seconds)
    } catch {
        result = Out(ok: false, locale: localeID, error: "\(error)")
    }
    sem.signal()
}
sem.wait()
emit(result)
