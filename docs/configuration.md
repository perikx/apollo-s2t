# Configuration

`config.json` is created by setup. **Settings** opens the in-app controls:
recording keys, behavior, prompt profile, vocabulary and recovery retention.
**Models** offers a ranked list of primary, fallback and rewrite models with USD pricing.
Changes save atomically and apply to the next recording; key rebinding is blocked
while recording. If you edit the JSON file directly, quit and restart Apollo.
`setup.bat` preserves existing settings; the executable opens the same terminal setup on first run.
[`config.example.json`](../config.example.json) shows every shipped default.
Apollo keeps only settings that you may want to change. Values that never change
(audio format, URLs, timeouts, token limit) are constants in the code. Old files
may still contain them. Apollo ignores such keys and removes them at the next save.

## Models and one API key

The default speech model is `microsoft/mai-transcribe-2`; the rewrite model is
`google/gemini-3.5-flash-lite`. Change `openrouter_stt.model` and `smoothing.model`
to pin other compatible OpenRouter models. `openrouter_stt.fallback_model` selects
the fallback model. The default is `elevenlabs/scribe-v2`, which comes from another vendor than
MAI, so one vendor outage does not stop dictation. Use `null` to disable the fallback.
The two model IDs must differ. A custom primary without a fallback keeps no fallback. Transcription uses the dedicated
`/audio/transcriptions` endpoint, not the chat endpoint. A chat/audio-capable model
is not necessarily compatible with this transcription API.

Keep the API key in `smoothing.api_key`, which supplies **both** stages for compatibility
with existing installations. `OPENROUTER_API_KEY` takes precedence; its value is not
written into configuration. Keys beginning with `YOUR_` are treated as placeholders.

To change the key, open **Settings → OpenRouter API key**. Paste the new key and click
**Check and save**. Apollo checks the key with OpenRouter and does not save a rejected key.
When OpenRouter is not reachable, Apollo saves the key and marks it as not checked.
When OpenRouter rejects the key during a dictation (HTTP 401), Apollo opens this page.

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

F9 and F10 use fixed rewrite settings: minimal reasoning, temperature 0.2, at most
4096 output tokens, a 20-second timeout and latency-first provider routing.
Reasoning is not included in pasted output. An incomplete response falls back to the raw text.

F9 instructs the model to make minimal edits: punctuation, capitalization, sentence/paragraph
breaks and small, unambiguous grammar corrections. Meaningful repetitions stay, even when
you repeat a point several times for emphasis. It removes clear hesitation sounds such as
"um" or "ähm", but preserves meaning-bearing words such as "but" and "if", your tone,
qualifications and original order. It must not summarize, paraphrase or infer what you meant.
This is a model instruction, not a deterministic guarantee of identical wording. The raw
transcript remains saved alongside the cleaned version in `recovery/` for comparison.

## Fallback, hedge and circuit breaker

The speech timeout grows with the audio: 15 seconds plus half the audio length, at most 180 seconds.
The rewrite timeout is 20 seconds. The connection timeout is 5 seconds.

On the first request, a 429, timeout, connection error or 5xx error switches to the fallback
model at once, with the same audio and language. Apollo also hedges: if the primary has not
answered after 3 seconds (plus 0.1 second per second of audio), Apollo sends the same audio to
the fallback model. The first transcript wins. A later 429 retries the same model and respects
`Retry-After`. Apollo makes at most three requests in a row, within 30 seconds of the first.

After a primary failure, the circuit breaker opens for 5 minutes. New recordings try the fallback
first, then the primary. Authentication, credit and other HTTP errors do not switch models.
Rewrite requests are not retried. Saved audio stays available for an explicit recovery.
Apollo logs the fallback switch and shows no balloon. Public model discovery sends no API key or audio.

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
| `insertion.restore_clipboard` | Restore previous text only while Apollo still owns the clipboard. Default true. |
| `beep` | Enable start, stop, ready and error tones. |

Fixed limits: Apollo records 16,000 Hz mono audio. A recording under 0.3 seconds is not sent, but its
audio stays in recovery. A recording stops and processes after 300 seconds. At most 3 jobs
(the active recording plus processing jobs) run at once. A full queue rejects a new start with an error tone.
Apollo restores the clipboard 1.5 seconds after a paste. At login, Apollo waits 3 seconds for the
audio device and keyboard hooks.

Each recording runs in its own job, so a second recording does not wait for the first.
Pastes happen one at a time, in the order the results arrive. Apollo pastes the text first and saves
the files after. Quit cancels pending work and prevents late result insertion.
Captured audio stays in the recovery folder, including cancelled work.

## Saved recordings and recovery

`recovery/` lives beside `apollo.py` for source installations or beside `apollo.exe`
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

Use the floating logo's **Recovery** window to select a saved recording. Existing
final text, or its raw transcript if no final text exists, is copied without a network
request. Otherwise, recovery sends the saved audio using **current** API/model settings
and the original recording mode and prompt context. This can incur normal API charges.
Recovery only puts the result on the clipboard for Ctrl+V; it never pastes automatically
into whichever app happens to be focused. Startup never resends recordings.
The floating logo's **Recovery** window also previews text, copies it and deletes
individual entries. New recordings include the actual hotkey used; older entries
without this information are labelled unknown rather than guessing a default key.
Safe failure causes (for example transcription HTTP 429 and its reported limit source)
remain with each cache entry across restarts. Successful recovery clears that cause.
The embedded Live Debug shows microphone/capture state, current configured key,
duration, missing audio, backup problems and the current run's API/retry/fallback logs.
Its last 300 diagnostics live in memory only; closing the panel does not discard them.
Provider bodies, dictated text and API keys are excluded from diagnostic messages.

`recovery_cache.minutes` defaults to 15 (5 to 60). The cache also keeps at most 10 recordings and 64 MB.
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
Drag the logo to a monitor's right edge to hide it; choose **Show apollo s2t** in the tray
to restore it. Visibility and position persist in `overlay`. Windows notification
balloons are disabled. The waveform uses microphone RMS levels; processing has its
own activity ring. The speech bubble replaces the logo during recording. Only the
floating control stays above other apps; settings/setup participate in normal window
stacking. Apollo avoids pasting a completed dictation into its own dialogs.

The Models popup loads the public OpenRouter catalog without credentials or audio.
Speech choices require `architecture.output_modalities: ["transcription"]`;
audio-capable chat models alone do not qualify. Cleanup choices require text input
and text output. Apollo ranks the models, best first, from a built-in benchmark table.
Each row shows a tier badge, USD prices and a short reason. A model that is not in the table
appears only under "Show all". Click a selector to expand a search and a one-line list.
Text prices come from OpenRouter's Models API. Audio prices come from the public model pages,
because the API omits the billing unit. A missing price shows a note instead of a number.
Click a row to select; Escape closes just the list. Changed choices must exist in the compatible
catalog; if loading fails, existing choices are retained.

Apollo also counts your own results. It writes the success and failure count and the last 50
latencies of each speech model to `model_stats.json` beside `config.json`. The file holds no
transcripts or audio. After 5 attempts the picker shows your success rate and median time for the model.
Delete the file to reset the numbers. Model and profile changes apply to the next recording.

This is recovery protection, not a guarantee against every failure: an unavailable
microphone cannot supply audio, a full/broken disk cannot save it, and abrupt process
or power loss can lose the last unflushed samples. The half-second interval is a target,
not a hard bound during slow disk writes or system stalls. Keep enough free disk space.

## Clipboard

Apollo pastes the complete result into the focused window. The old clipboard text is
restored only if it has not changed in the meantime. Other clipboard formats, such as
images or rich formatting, are not preserved by the text-only clipboard library.
If an Apollo window has focus when the result is ready, the text is only copied.
There is no other insertion mode.

## F10 profiles

Create `prompts/my-project.md` with the project context and select it in the floating
Settings window. `prompt_profiles.active` also accepts `my-project`.
Profile contents are read at recording start, so edits affect the next recording.

`prompt_profiles.output_language: "english"` produces an English prompt. `"match"`
keeps the dictated language; any other language name forces that language. This affects
F10 only. F9 keeps the original language. Code identifiers remain unchanged.
F10 always adds a short guidance line for coding requests.

In an executable build, custom profiles go next to `apollo.exe` in `prompts/`.
Once that directory exists it takes precedence over bundled profiles; copy any bundled
profiles you want to retain into the same directory.

## Upgrading older configurations

The schema is version 2. When a file has removed keys or a changed value, Apollo validates the whole
configuration, saves `config.json.bak` without overwriting an existing backup, and atomically
writes the cleaned file. Invalid JSON is never overwritten automatically. Keep both files private.

Apollo drops keys that it no longer uses, for example `stt_engine`, `insertion.mode`,
`smoothing.temperature`, `max_record_seconds` or `autostart_delay_seconds`. The built-in values
apply instead. Your models, keys, hotkeys and profiles survive. A fallback of
`microsoft/mai-transcribe-1.5` with the MAI-2 primary changes to `elevenlabs/scribe-v2`.

## Personal vocabulary

Settings → Personal vocabulary stores up to 100 explicit phrases in
`openrouter_stt.vocabulary`, one phrase per line (1–100 characters each).
Use the exact spelling, such as `PANDU`. This small list needs no database,
background service or automatic learning. It is empty by default.

For MAI-Transcribe 2, Apollo sends the list with the audio through the documented
OpenRouter option `provider.options.azure.phraseList.phrases`. This biases speech
recognition; it does not guarantee the spelling, train a personal model or replace
words after transcription. Other models, including the fallback, currently
receive no vocabulary options because their exact integration has not been verified.
See [OpenRouter's MAI-2 integration](https://openrouter.ai/microsoft/mai-transcribe-2).

## Terminal onboarding

First launch without a key and `--setup` open a real terminal. Its normal colors
remain unchanged. Choose English, German or Simplified Chinese, enter the key
without echo, then keep the defaults or customize keys, language and compatible
models. Model discovery and key validation make read-only requests; they send no
audio or text to a model. `/cancel` or Ctrl+C exits without saving. Invalid key or
key choices remain editable. Saving preserves existing profiles and settings.
The normal floating app runs without a terminal.
