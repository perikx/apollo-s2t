# Changelog

All notable changes to Apollo s2t are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased] - 2026-09-29

### Changed
- OpenRouter-only speech pipeline. Default speech model is `microsoft/mai-transcribe-2`;
  F9/F10 use `google/gemini-3.5-flash-lite` with compact prompts and minimal reasoning.
- One bounded FIFO worker preserves recording order, model/profile snapshots and origin windows.
- Both API stages reuse connections; separate timeouts and incomplete-rewrite fallback are explicit.
- README starts with a copyable Git clone/install command; advanced settings moved to `docs/`.
- Setup preserves existing settings. Launchers share bootstrap logic and refresh changed dependencies.
- Offline self-test is now the default. Paid recording/API checks require `--live`.

### Added
- Versioned, validated configuration with atomic writes and a one-time `config.json.bak` backup.
- `OPENROUTER_API_KEY` environment support without writing the environment value to disk.
- Five-minute recording cap, bounded pending jobs, and clean cancellation on quit.
- Windowed executable first-run key dialog, settings/log shortcuts in the tray, and rotating logs.
- Isolated regression tests and a Windows/Linux Python 3.10/3.13 CI matrix.

### Fixed
- Concurrent dictations could be inserted out of order or use the next recording's origin/context.
- Delayed clipboard restores could overwrite content copied by the user or a later dictation.
- Stale armed timers/click callbacks could consume a newer load.
- Failed origin refocusing no longer pastes into an unrelated window.
- Recording buffers and device handles are released after errors; no late paste after quitting.
- API errors and normal logs no longer expose full dictated text or raw response bodies.
- Dependency installation errors stop launch instead of starting a half-installed environment.

### Removed
- Deepgram API calls, websocket streaming, live typing and the websocket-client dependency.
- Engine selection and obsolete streaming settings from setup and the current example configuration.
  Old provider settings remain only in the migration backup and historical entries below.

## [Earlier unreleased changes] - 2026-07-24

### Added
- Hybrid insertion mode: paste into a detected text field and restore the clipboard;
  otherwise keep the result available for Ctrl+V. Uses UI Automation with a caret fallback.
- Optional single-file Windows executable build through PyInstaller.
- One-click `Apollo.bat`, app logo, and a "Start at login" tray toggle.
- Choosable OpenRouter or Deepgram speech engines with configurable OpenRouter audio models.
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
- Multi-language Deepgram speech recognition and custom Nova-3 vocabulary.
- Armed insertion: paste in the original window or keep the dictation for later Ctrl+V.
- Interactive setup with provider sign-up links, tray icon, autostart and single-instance guard.
- Actionable errors for API, network and microphone failures.
- Colored ASCII startup banner, MIT license and English documentation.
