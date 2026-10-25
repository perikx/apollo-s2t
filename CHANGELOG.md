# Changelog

All notable changes to Apollo s2t are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/).

## [0.4.8] - 2026-10-25

### Recording
- Increase the maximum recording duration from 5 to 30 minutes for F8, F9 and F10.
- The recorder buffer and automatic stop use the same limit. At the limit, captured audio
  is saved and processed as before. Incomplete rewrites still fall back to the full raw transcript.

## [0.4.7] - 2026-10-15

### Build and release
- GitHub Actions builds the exe and publishes the release when a `v*.*.*` tag is pushed.
  The workflow tests first, checks the tag against `APP_VERSION` and starts the exe once.
- `packaging/build-constraints.txt` pins the exact build versions; `packaging/build-exe.bat` uses them too.
- `packaging/release_files.py` collects the release files and takes the notes from this file.
- The test workflow runs on push only for `main`. Pull requests get one run per Python version.
- The build drops the Universal C Runtime copy (`ucrtbase.dll`, `api-ms-win-*`) that GitHub's
  runners add. Windows 10 and 11 always load their own copy. The exe stays at 26.5 MB.

## [0.4.6] - 2026-10-15

### Remote Desktop
- F8, F9 and F10 work while a Remote Desktop (mstsc, msrdc) or Citrix window has the focus.
  Apollo has its own keyboard hook now (`apollo_keyhook.py`). While such a window is in front,
  the hook installs itself again every second and is first in the hook chain again.
- In a Remote Desktop window, Apollo pastes with Ctrl+V and keeps the text on the clipboard.

### F10 profiles
- Settings > Prompt profile has a text field for the selected profile and a Create button.
  The first save copies the bundled profiles next to Apollo, so they stay visible.
- Text between `<!--` and `-->` in a profile is a note; Apollo does not send it to the model.
  The bundled `default` profile no longer sends its help text as project context.

## [0.4.5] - 2026-10-15

### Setup and connection
- Apollo trusts the Windows certificate store (`truststore`). Virus scanners, company proxies
  and school networks that check HTTPS no longer block the connection to OpenRouter.
- The key check names the cause: no connection, blocked TLS connection, invalid characters in the key
  or an OpenRouter error. Before, every failure showed "OpenRouter is currently unavailable".
- A key with an invisible or non-ASCII character is not sent. Apollo asks you to copy it again.
- Setup can save the key without a check and continue when there is no connection.
  A rejected key still blocks setup.

## [0.4.4] - 2026-10-14

### Insertion
- Apollo checks the focused control with UI Automation before it pastes, as before 0.4.3.
  - A text field: Apollo pastes and restores the old clipboard.
  - No window, the desktop or no text field: the text stays on the clipboard and the ready beep plays.
  - An unknown control: Apollo pastes, keeps the text on the clipboard and plays the ready beep.
- The ready beep also plays when an Apollo window has the focus or another app changed the clipboard.
- Fixed: a paste without a text field lost the text, because Apollo restored the old clipboard.

### Speed
- Apollo sends each recording to both speech models at once and uses the first transcript.
  A slow MAI-Transcribe 2 no longer delays the text. Before, the second model started only after
  3 s plus 0.1 s per second of audio (18 s for a 2.5-minute recording). The speech cost doubles.
- Each model has its own warm TLS connection.

### Settings
- New check box "Keys also work in full-screen apps and games" (`hotkeys_in_fullscreen`, default on).
  When it is off, games such as Minecraft keep F8, F9 and F10. A running recording always stops.
- New check box "Update automatically" (`auto_update`, default on). The exe checks the latest
  GitHub release every 6 hours, verifies its SHA-256 and restarts after 2 quiet minutes.

### Rewrite
- F9 removes filler words, stutters, doubled words, false starts and self-corrections.
  It keeps every point, deliberate emphasis and the speaker's words.

## [0.4.3] - 2026-10-13

### Speech reliability
- Fallback is `elevenlabs/scribe-v2`, another vendor than the MAI primary.
- A timeout, connection error, 5xx error or 429 on the first request switches to the fallback.
- Hedge: after 3 s (plus 0.1 s per second of audio) without an answer, Apollo asks the other model too.
- Circuit breaker: after a primary failure, new recordings try the fallback first for 5 minutes.
- The speech timeout scales with the audio length (15 s plus half the length, at most 180 s).
- Apollo pastes the text first and saves the files after.
- Each recording runs as its own job. Pastes still run one at a time.
- PortAudio reloads the device list and retries once when the microphone changes (new headset).
- The hotkey hook only queues events. A dispatcher thread runs them.
- Fixed the auto-repeat of a held key, which started and stopped recordings.

### Models
- Settings has an OpenRouter API key field. It checks a new key before it saves it. A rejected key (HTTP 401) opens Settings.
- The saved key now wins over `OPENROUTER_API_KEY`. The environment variable is only a fallback when no key is saved.
- The model picker is ranked, with a tier badge and a reason for each model.
- Apollo stores your own success rate and latency in `model_stats.json` and shows them in the picker.
- Apollo no longer scrapes prices from model pages.

### Settings and size
- Keys that never change are now constants: URLs, timeouts, temperature, reasoning effort,
  audio format, record limits, queue size, prompt folder, `include_karpathy` and cache size limits.
  Old files keep working. Apollo drops these keys at the next save.
- The rewrite token limit is 4096 (was 8192). A large limit can trigger OpenRouter 402 errors.
- Clipboard restore waits 1.5 s (was 0.4 s). Autostart waits 3 s (was 20 s).
- Removed old migrations (retired speech engine, old model names, old insertion keys). The MAI-1.5 fallback
  still moves to Scribe v2.
- Removed the armed, hybrid, origin and click-to-paste insertion modes.
- One duplicate-key check and one backup writer replace the copies in the app and the terminal setup.

### Interface and build
- Removed the Qt setup wizard and the terminal animations. Setup uses plain terminal prompts.
- The tray is a Qt tray icon.
- Removed numpy, pystray, Pillow, mouse and comtypes. The build drops unused Qt parts.
  The executable is 26.5 MB (was 75 MB).
- The audio callback only copies bytes. The UI timer computes levels and slows to 100 ms when idle.
  Tones use the standard library.
- The fallback switch is logged, not shown. Retry notices appear only for waits of 1 s or more.

## [0.4.2] - 2026-10-07

- Tray Show restores minimized or hidden controls to a visible position and opens the menu.
- Monochrome artwork, Figtree typography, simpler headers and tray controls.
- English, German and Simplified Chinese interface and terminal setup.
- Animated terminal onboarding with hidden key entry and editable validation errors.
- Nested scrolling stays inside its list; scrollbars are hidden and saving preserves position.
- Current model suggestions above the full compatible catalog, without duplicate entries.
- Optional personal vocabulary hints for MAI-Transcribe 2, excluded from unsupported fallbacks.

## [0.4.1] - 2026-10-03

### Changed
- Compact 44 px logo and 32 px controls, closer spacing and icon tooltips instead
  of permanent captions. The live speech bubble replaces the logo while recording.
- Smaller settings/setup windows and controls; display the name as `apollo s2t`.
- Model and profile choices expand in the existing window. Model rows show names
  and explicit USD audio/token prices in one line, with full details on hover.

### Fixed
- Settings/setup are ordinary windows that other apps can cover when switching focus.
- Wheel scrolling works over unfocused settings fields without a preliminary click.

### Added
- Recovery live-debug view: recording/key/duration, stalled audio and backup errors,
  API errors, retries and fallbacks. Keep at most 300 safe diagnostics in RAM.
- Preserve safe failure causes with cached audio across restarts; clear the cause
  when a retry succeeds. Never store provider response bodies in diagnostics.

## [0.4.0] - 2026-10-03

### Changed
- Replace Tk/color-keyed circles and native dropdowns with a Qt overlay: smooth
  transparency, automatic Windows DPI scaling, 88 px full-artwork logo, 64 px
  controls, balanced spacing and large charcoal/gold popups.
- Use the newly supplied transparent glitter/lyre PNG unchanged, plus its Windows icon.
- Display real 20 ms microphone envelopes with logarithmic gain and smooth motion.
- Full windowed first-run setup: validated API key, editable recording keys,
  language, models, fallback and autostart. Invalid entries stay editable.

### Added
- Change/rebind all three recording keys in Settings without restarting; failed
  saves retain working hooks. Apollo input fields allow configured letter keys.
- Choose any compatible primary transcription model and a separate 429 fallback,
  or disable fallback. Keep the existing bounded retry and audio recovery behavior.
- Searchable model cards with public OpenRouter USD prices and explicit audio/token
  billing units. Unknown prices are shown as unavailable, never guessed.

## [0.3.1] - 2026-10-03

### Changed
- Use the supplied white lyre on amber artwork as the Apollo logo in the floating
  controls, tray, Windows icon and README. Preserve its original proportions.

## [0.3.0] - 2026-10-03

### Added
- Draggable floating Apollo logo with Recovery, Settings and Models controls, live microphone
  levels, collapse button, right-edge hiding and tray restoration. No Windows notification balloons.
- Native recovery preview, clipboard copy, retry and deletion with the originally used key.
- Searchable OpenRouter model catalog separated by transcription/text output capabilities.
- Persistent profile/model choices and actual configured key labels in setup and controls.
- Automatic recovery cache retention: 15 minutes, 10 completed entries and 64 MB by default;
  active work is protected. Older version recordings are subject to the same cleanup policy.

## [0.2.0] - 2026-10-03

### Changed
- F9 now requests minimal transcript cleanup, explicitly preserving meaningful repetitions,
  tone, conditions and original phrasing instead of freely polishing or shortening the message.
- Audio is checkpointed locally during recording and all audio/text remains until manually
  deleted from `recovery/`. Recovery files contain private, unencrypted content.
- Explicit transcription HTTP 429 rejections receive at most two retries, respecting
  Retry-After within a 30-second retry-start window. Ambiguous network failures and
  rewrite requests are not automatically resent.
- MAI-Transcribe-2's first 429 switches that recording to MAI-Transcribe-1.5, within the
  existing request budget. New recordings retain the configured primary; custom models
  are not replaced. The same saved audio is used and the switch is reported in the tray/log.
- OpenRouter-only speech pipeline. Default speech model is `microsoft/mai-transcribe-2`;
  F9/F10 use `google/gemini-3.5-flash-lite` with compact prompts and minimal reasoning.
- One bounded FIFO worker preserves recording order, model/profile snapshots and origin windows.
- Both API stages reuse connections; separate timeouts and incomplete-rewrite fallback are explicit.
- README keeps the original banner and badges, followed by a copyable Git clone/install command;
  advanced settings moved to `docs/`.
- Setup preserves existing settings. Launchers share bootstrap logic and refresh changed dependencies.
- Offline self-test is now the default. Paid recording/API checks require `--live`.

### Added
- Tray recovery for saved dictations and a shortcut to local audio/text. Existing text
  is reused offline; audio-only recovery uses current API settings and saved mode/context,
  then copies the result for Ctrl+V. Startup never resends recordings automatically.
- Separate raw and final transcript files, recoverable WAV checkpoints and local metadata
  without copied API credentials. Disk writes run outside the microphone callback.
- Tests for interrupted recovery files, retry timing/cancellation, privacy-safe diagnostics
  and clipboard-only recovery.
- Versioned, validated configuration with atomic writes and a one-time `config.json.bak` backup.
- `OPENROUTER_API_KEY` environment support without writing the environment value to disk.
- Five-minute recording cap, bounded pending jobs, and clean cancellation on quit.
- Windowed executable first-run key dialog, settings/log shortcuts in the tray, and rotating logs.
- Isolated regression tests and a Windows/Linux Python 3.10/3.13 CI matrix.
- Console/windowed setup and presentation regressions: one key, no retired-provider UI,
  preserved legacy settings, and the original banner above the install instructions.

### Fixed
- API errors, interrupted processing and microphone stop/close failures no longer discard
  already saved dictation. Checkpoints reduce process-crash loss; disk/power/device failures
  and an unflushed audio tail remain possible.
- Rate-limit messages distinguish OpenRouter/platform and upstream hints when the response
  provides evidence, without logging provider bodies or dictated content.
- Restored the original README banner and badges after the documentation cleanup.
- Removed retired-provider branding from the current README, configuration guide and changelog.
- Concurrent dictations could be inserted out of order or use the next recording's origin/context.
- Delayed clipboard restores could overwrite content copied by the user or a later dictation.
- Stale armed timers/click callbacks could consume a newer load.
- Failed origin refocusing no longer pastes into an unrelated window.
- Recording buffers and device handles are released after errors; no late paste after quitting.
- API errors and normal logs no longer expose full dictated text or raw response bodies.
- Dependency installation errors stop launch instead of starting a half-installed environment.

### Removed
- Legacy speech-provider API calls, websocket streaming, live typing and the websocket-client dependency.
- Engine selection and obsolete streaming settings from setup and the current example configuration.
  Retired settings are stripped on upgrade; the original file is retained in the migration backup.

## [Earlier unreleased changes] - 2026-07-24

### Added
- Hybrid insertion mode: paste into a detected text field and restore the clipboard;
  otherwise keep the result available for Ctrl+V. Uses UI Automation with a caret fallback.
- Optional single-file Windows executable build through PyInstaller.
- One-click `Apollo.bat`, app logo, and a "Start at login" tray toggle.
- Configurable speech engines and OpenRouter audio models; engine selection has since been removed.
- Configurable toggle hotkeys alongside press-and-hold mode.

### Changed
- Toggle became the default hotkey mode.
- OpenRouter became the default speech engine, using MAI-Transcribe-1.5 and Gemini 3.1 Flash Lite.
- Setup became OpenRouter-first, with one key for STT and F9/F10 and automatic login startup.
- Separate install/start/autostart scripts were replaced by Apollo.bat, debug.bat and setup.bat.
- Setup allowed selecting hotkeys by pressing them.

### Fixed
- Toggle could remain stuck recording because a suppressed key-up event never arrived.
  The handler was changed to time-based debounce.
- Feedback tones moved to the actual sounddevice output, with winsound fallback.
- Login startup waits for audio and keyboard hooks to become available.
- Remaining German code comments/docstrings/log messages were translated to English.

## [0.1.0] - 2026-06-21

First public release.

### Added
- Push-to-talk dictation: hold a key, speak, release; insert text into the active field.
- Configurable F8 dictation, F9 LLM polish and F10 prompt-building modes.
- Per-project F10 context in `prompts/*.md`, selected from the tray.
- Optional Karpathy coding guidelines and an output-language setting for F10.
- Multi-language speech recognition and custom vocabulary through the original speech engine.
- Armed insertion: paste in the original window or keep the dictation for later Ctrl+V.
- Interactive setup with provider sign-up links, tray icon, autostart and single-instance guard.
- Actionable errors for API, network and microphone failures.
- Colored ASCII startup banner, MIT license and English documentation.
