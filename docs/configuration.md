# Configuration

`config.json` is created by setup. Quit Apollo, edit the file, and restart with `Apollo.bat`.
The tray's **Open settings** item opens this file. `setup.bat` preserves existing settings.
[`config.example.json`](../config.example.json) shows every shipped default.

## Models and one API key

The default speech model is `microsoft/mai-transcribe-2`; the rewrite model is
`google/gemini-3.5-flash-lite`. Change `openrouter_stt.model` and `smoothing.model`
to pin other compatible OpenRouter models. Transcription uses the dedicated
`/audio/transcriptions` endpoint, not the chat endpoint. A chat/audio-capable model
is not necessarily compatible with this transcription API.

Keep the API key in `smoothing.api_key`, which supplies **both** stages for compatibility
with existing installations. `OPENROUTER_API_KEY` takes precedence; its value is not
written into configuration. Keys beginning with `YOUR_` are treated as placeholders.

Model selection checked **2026-09-29**:

- [MAI-Transcribe-2](https://openrouter.ai/microsoft/mai-transcribe-2) was released on
  September 3, 2026 and supports 60 languages, including German, English and code-switching.
  [Microsoft's announcement](https://microsoft.ai/news/mai-transcribe-2-is-the-fastest-most-accurate-and-cheapest-speech-recognition-model-in-the-world/)
  describes the successor's accuracy and latency improvements over prior models.
- [Gemini 3.5 Flash Lite](https://openrouter.ai/google/gemini-3.5-flash-lite) was released
  on July 21, 2026 and is a lightweight text model suitable for the rewrite stage.
- [Qwen3 ASR Flash](https://openrouter.ai/qwen/qwen3-asr-flash-2026-02-10) is another
  compatible multilingual model. MAI-2 is selected here as the newer successor to
  Apollo's former Microsoft default, not on the basis of a measured voice-specific comparison.

These are compatibility/default choices, not a measured accuracy ranking for your voice.
Check current provider availability and pricing before changing models.

`openrouter_stt.language: ""` enables automatic detection, suitable for German/English
mixing. Set `"de"` or `"en"` to force a single language. `auto`/`multi` normalize to empty.
Do not force German when you want entire English passages transcribed as English.

F9/F10 use `smoothing.reasoning_effort: "minimal"`, `temperature: 0.2`,
`max_tokens: 8192`, and latency-first provider routing. For another model that does not
support reasoning options, set `reasoning_effort` to `null` to omit the parameter.
Reasoning is not included in pasted output. Short prompts avoid adding generic instructions.
The token limit bounds the response, not the input; incomplete responses fall back to raw text.

STT and rewriting have separate read timeouts (`60` and `20` seconds) and a five-second
connection timeout. These are network timeouts, not hard end-to-end latency guarantees.
No automatic client-side retry of paid POST requests is performed. OpenRouter can still
route to a fallback provider for the selected model. Both stages reuse a connection pool.

API references: [transcription](https://openrouter.ai/docs/guides/overview/multimodal/audio#speech-to-text-transcription),
[provider routing](https://openrouter.ai/docs/guides/routing/provider-selection),
[reasoning](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).

## Keys, recording and insertion

| Setting | Meaning |
| --- | --- |
| `hotkeys.dictate/polish/prompt` | Three distinct single keys, default F8/F9/F10. |
| `hotkey_mode` | `toggle` taps to start/stop; `hold` records while held. |
| `audio.device` | `null` uses the system microphone, otherwise a sounddevice name/index. |
| `audio.samplerate/channels` | Default 16,000 Hz, mono. Input must support the chosen settings. |
| `min_record_seconds` | Recordings shorter than 0.3 seconds are ignored without an API call. |
| `max_record_seconds` | Auto-stop and process after 300 seconds. Audio buffering is capped too. |
| `max_pending_recordings` | Maximum 3 jobs including the active recording and processing. A full queue rejects a new start with an error tone. |
| `insertion.mode` | `instant`, `hybrid` or `armed`, described below. |
| `insertion.target` | `focused` pastes at completion; `origin` attempts the window active at recording start. |
| `insertion.restore_clipboard` | Restore previous text only while Apollo still owns the clipboard. Default true. |
| `insertion.restore_delay` | Wait 0.4 seconds before restoring; increase for slow applications. |
| `insertion.armed_timeout` | Stop waiting for optional click insertion after 30 seconds. |
| `insertion.click_to_paste` | Opt-in click insertion for a loaded result; default false. |
| `beep` | Enable start, stop, ready and error tones. |
| `autostart_delay_seconds` | Wait 20 seconds at login for the audio device and keyboard hooks. |

Completed recordings are processed **in recording order**, using the model, origin window
and F10 context captured when each recording started. No overlapping API workers can
reorder results. Additional recordings may queue; this trades parallel throughput for
predictable insertion. Quit cancels pending work and prevents late result insertion.

**Instant:** paste the complete result into the target field. The old clipboard text is
restored only if it has not changed in the meantime. Other clipboard formats, such as
images or rich formatting, are not preserved by the text-only clipboard library.

**Hybrid:** if a text field is confidently detected, paste and restore. If clearly not,
keep the result on the clipboard. If detection is uncertain, attempt a paste and keep
a recoverable clipboard copy until the load expires. This fallback can restore the
previous clipboard text on expiry, but never over newer user clipboard content.

**Armed:** staying in the recording's original window pastes immediately and retains
the result on the clipboard. After switching windows, the result stays on the clipboard
for native Ctrl+V. With `click_to_paste: true`, a click can insert it instead; clearly
non-editable targets do not consume the load. Native Ctrl+V is not intercepted and can
paste more than once; the optional click remains armed until a click, expiry or replacement.

`target: "origin"` operates at the **window** level, not the browser-tab level. If Windows
rejects refocusing or the origin window closed, text stays on the clipboard instead of
being pasted into an unrelated window. Field detection is best effort, not an accessibility guarantee.

## F10 profiles

Create `prompts/my-project.md` with the project context and select it in the tray's
**F10 profile** menu. Or set `prompt_profiles.active` to `my-project` in configuration.
Tray selection is session-only; editing the configuration sets the startup choice.
Profile contents are read at recording start, so edits affect the next recording.

`prompt_profiles.output_language: "english"` produces an English prompt. `"match"`
keeps the dictated language; any other language name forces that language. This affects
F10 only. F9 keeps the original language. Code identifiers remain unchanged.
`include_karpathy: false` disables the small coding-specific guidance.

In an executable build, custom profiles go next to `Apollo.exe` in `prompts/`.
Once that directory exists it takes precedence over bundled profiles; copy any bundled
profiles you want to retain into the same directory.

## Upgrading older configurations

The schema is version 2. On first load, Apollo validates the whole configuration, saves
`config.json.bak` without overwriting an existing backup, and atomically writes the upgrade.
Invalid JSON is never overwritten automatically. Keep both files private.

Versionless old installations using MAI-1.5 or the dated Qwen ASR model move
to MAI-2. The former Gemini 3.1 Flash Lite default moves to Gemini 3.5 Flash Lite.
Other custom model IDs and unrelated settings survive. Once `config_version: 2` is present,
model choices are explicit and are not automatically changed again. To keep Qwen,
set its model ID after upgrading.

Deepgram configuration, API calls, websocket code and live typing were removed.
Its old single-language preference is carried over where applicable; old keyterms remain
only in the backup. They are not silently sent as unsupported OpenRouter parameters.
An existing virtual environment may still contain the unused websocket package; Apollo
no longer imports or installs it. It can be removed manually or by recreating `.venv`.
