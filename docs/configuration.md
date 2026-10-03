# Configuration

`config.json` is created by setup. **Settings** opens the modern in-app controls,
including recording-key capture, behavior, prompt profile and recovery retention.
**Models** offers searchable primary, fallback and rewrite choices with USD pricing.
Changes save atomically and apply to the next recording; key rebinding is blocked
while recording. If you edit the JSON file directly, quit and restart Apollo.
`setup.bat` preserves existing settings; the executable has a full first-run wizard.
[`config.example.json`](../config.example.json) shows every shipped default.

## Models and one API key

The default speech model is `microsoft/mai-transcribe-2`; the rewrite model is
`google/gemini-3.5-flash-lite`. Change `openrouter_stt.model` and `smoothing.model`
to pin other compatible OpenRouter models. `openrouter_stt.fallback_model` selects
a separate fallback for explicit 429 rejections; use `null` to disable it. The two
model IDs must differ. Existing custom primaries without a fallback are preserved. Transcription uses the dedicated
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

F9 instructs the model to make minimal edits: punctuation, capitalization, sentence/paragraph
breaks and small, unambiguous grammar corrections. Meaningful repetitions stay, even when
you repeat a point several times for emphasis. It removes clear hesitation sounds such as
"um" or "ähm", but preserves meaning-bearing words such as "but" and "if", your tone,
qualifications and original order. It must not summarize, paraphrase or infer what you meant.
This is a model instruction, not a deterministic guarantee of identical wording. The raw
transcript remains saved alongside the cleaned version in `recovery/` for comparison.

STT and rewriting have separate read timeouts (`60` and `20` seconds) and a five-second
connection timeout. These are network timeouts, not hard end-to-end latency guarantees.
Only an explicit **HTTP 429 transcription rejection** is automatically retried: at most
three requests in total. After the primary's first 429, the next request uses the
configured fallback with identical audio and language. The switch is immediate unless
the server supplies `Retry-After`. A further 429 allows one retry of the fallback;
there is no switch back or extra request budget. Disabling fallback retries the same
primary instead. Each new recording starts with your configured primary again.
`Retry-After` seconds or HTTP dates are respected even when switching models. Without a
usable header, same-model retries wait about two or four seconds plus a small random delay. A retry
starts only within 30 seconds of the first request starting; a longer server-requested
wait is never shortened to fit this window. Each request still has its own network
timeout, so total processing can exceed 30 seconds. Quitting interrupts retry waits.

[MAI-Transcribe-1.5](https://openrouter.ai/microsoft/mai-transcribe-1.5) was still listed
on 2026-10-03. Its normal API price applies when used. Both models can still encounter
provider/platform limits, so fallback does not replace local audio recovery. Apollo's in-app status and log identify the selected fallback; no Windows balloon
is shown. Public model discovery sends no API key or audio. Text prices come from
OpenRouter's Models API; audio prices use the explicit billing units on its public
model pages because the API's raw numeric price omits that unit. Missing metadata
shows “Preis nicht verfügbar”. Prices load in the background without blocking recording.

Timeouts and connection failures are not automatically resent because the server may
already have processed the request. Authentication, credit, other HTTP failures and
rewrite requests are also not automatically retried. Saved audio remains available for
an explicit recovery attempt. Both stages reuse a connection pool.

A 429 can originate at OpenRouter or at an upstream provider, including a provider
capacity limit. Error messages use structured source hints when available and keep the
source uncertain otherwise. They never copy raw provider messages, transcript text or
credentials into the log. Credit/spending limits (402) have separate messages.

API references: [transcription](https://openrouter.ai/docs/guides/overview/multimodal/stt),
[limits](https://openrouter.ai/docs/api_reference/limits),
[errors and Retry-After](https://openrouter.ai/docs/api_reference/errors-and-debugging),
[provider routing](https://openrouter.ai/docs/guides/routing/provider-selection),
[reasoning](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).

## Keys, recording and insertion

| Setting | Meaning |
| --- | --- |
| `hotkeys.dictate/polish/prompt` | Three distinct single keys, default F8/F9/F10. |
| `hotkey_mode` | `toggle` taps to start/stop; `hold` records while held. |
| `audio.device` | `null` uses the system microphone, otherwise a sounddevice name/index. |
| `audio.samplerate/channels` | Default 16,000 Hz, mono. Input must support the chosen settings. |
| `min_record_seconds` | Recordings shorter than 0.3 seconds are not processed or sent; their local audio is retained. |
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
Captured audio stays in the recovery folder, including cancelled work.

## Saved recordings and recovery

`recovery/` lives beside `apollo.py` for source installations or beside `Apollo.exe`
for executable installations. The folder must be writable. A separate save thread
checkpoints captured audio about every 0.5 seconds with disk flushes (`fsync`); normal
stop flushes the remaining audio before processing. Microphone stop/close failures
preserve samples already captured. A checkpoint failure stops recording and reports an
error rather than silently continuing without disk protection.

Each recording uses one shared filename stem:

| File | Contents |
| --- | --- |
| `.wav` | Captured audio, including completed checkpoints from interrupted recordings. |
| `.transcript.txt` | Original recognized text, saved before optional rewriting. |
| `.txt` | Final text, including the raw-text fallback when rewriting fails. |
| `.json` | Recording time, format, state, mode and original prompt context; no copied API credentials. |

Use the tray's **Recover saved dictation** menu to select a saved recording. Existing
final text, or its raw transcript if no final text exists, is copied without a network
request. Otherwise, recovery sends the saved audio using **current** API/model settings
and the original recording mode and prompt context. This can incur normal API charges.
Recovery only puts the result on the clipboard for Ctrl+V; it never pastes automatically
into whichever app happens to be focused. Startup never resends recordings.
The floating logo's **Recovery** window also previews text, copies it and deletes
individual entries. New recordings include the actual hotkey used; older entries
without this information are labelled unknown rather than guessing a default key.

`recovery_cache` defaults to `minutes: 15`, `max_entries: 10`, `max_mb: 64`.
Completed recordings are removed when expired or above the count/size limit; active
capture and queued/processing jobs are protected, so limits can be exceeded temporarily.
Age is measured from the last state update. Cleanup runs at startup, on recording start,
after processing and every 15 seconds while running. Old recordings from previous
versions follow the same policy. Orphan/corrupt UUID files and abandoned temporary
files expire by modification time. Unrelated files and symbolic links are untouched.
The cache is not an archive; copy important text elsewhere before it expires.
At 16 kHz mono, five minutes uses about 9.6 MB. Cache contents remain private,
unencrypted local files, even though the interface no longer requires opening them.

## Floating controls and models

Click the logo to expand Recovery, Settings and Models; the X collapses these controls.
Drag the logo to a monitor's right edge to hide it; choose **Show Apollo** in the tray
to restore it. Visibility and position persist in `overlay`. Windows notification
balloons are disabled. The waveform uses microphone RMS levels; processing has its
own activity ring. Apollo avoids pasting a completed dictation into its own dialogs.

The Models popup loads the public OpenRouter catalog without credentials or audio.
Speech choices require `architecture.output_modalities: ["transcription"]`;
audio-capable chat models alone do not qualify. Cleanup choices require text input
and text output. Type to filter, then use the dropdown to select. Changed choices
must exist in the compatible catalog; if loading fails, existing choices are retained.
Model and profile changes are saved atomically and apply to subsequent recordings.

This is recovery protection, not a guarantee against every failure: an unavailable
microphone cannot supply audio, a full/broken disk cannot save it, and abrupt process
or power loss can lose the last unflushed samples. The half-second interval is a target,
not a hard bound during slow disk writes or system stalls. Keep enough free disk space.

## Clipboard modes

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
profile menu (labelled with your actual prompt hotkey), or the floating Settings window.
Selections are saved for the next startup. `prompt_profiles.active` also accepts `my-project`.
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

Setup only requests an OpenRouter key. Retired speech-engine settings are removed during
upgrade; the old single-language preference is carried over where applicable. Legacy
custom vocabulary remains only in the backup and is not sent as unsupported API parameters.
An existing virtual environment may still contain the unused websocket package; Apollo
no longer imports or installs it. It can be removed manually or by recreating `.venv`.
