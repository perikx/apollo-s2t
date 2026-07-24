# Changelog

All notable changes to Apollo s2t are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Hybrid insertion mode** (`insertion.mode: "hybrid"`): if a text field is focused it pastes
  straight in and restores your clipboard (no clutter); if not, it keeps the text on the clipboard
  for `Ctrl+V`. Detects browser/Electron chat boxes via UI Automation (optional `comtypes`), with a
  system-caret + paste-and-keep fallback so text is never lost. Pick it in the wizard.
- **Optional single-file `.exe` build** — `packaging/build-exe.bat` (PyInstaller) produces a
  double-click `Apollo.exe` with the logo that needs no Python on the target PC.
- **One-click `Apollo.bat`** — the first run creates the environment, asks for a single key,
  enables autostart, and launches Apollo into the tray. No more separate install/setup/start steps.
- **App logo** (`assets/apollo.ico`) shown as the tray/taskbar icon.
- **"Start at login" tray toggle** — turn autostart on or off from the tray menu.
- **Choosable speech engine** (`stt_engine`): OpenRouter (default) or Deepgram, with any
  OpenRouter audio model via `openrouter_stt.model` — e.g. `microsoft/mai-transcribe-1.5`
  (100+ languages incl. Chinese) or `nvidia/parakeet-tdt-0.6b-v3` (cheapest, EU).
- **Toggle hotkeys** (`hotkey_mode: "toggle"`): tap to start, tap to stop — no need to hold
  the key during long dictation. Default stays `"hold"`.

### Changed
- **Default hotkey mode is now `"toggle"`** (tap to start, tap again to stop) instead of `"hold"`.
  Set `hotkey_mode: "hold"` for the old press-and-hold behavior.
- **OpenRouter is now the default speech engine** — one OpenRouter key powers both
  speech-to-text (default model `microsoft/mai-transcribe-1.5`) and the F9/F10 LLM. No second key.
- **Default F9/F10 model** is now `google/gemini-3.1-flash-lite`.
- **Autostart is enabled automatically** during first-time setup (still delayed at boot via
  `autostart_delay_seconds`, still removable from the tray).
- **Simpler setup wizard** — OpenRouter-first; only the key is required, everything else defaults.
- **Fewer launchers** — `install.bat`, `start.bat`, `start-debug.bat`, `autostart-enable.bat`
  and `autostart-disable.bat` are replaced by `Apollo.bat` (run), `debug.bat` (logs) and
  `setup.bat` (reconfigure).
- Setup wizard: **press the key** you want for a hotkey instead of typing its name.

### Fixed
- **Toggle mode could get stuck recording** — the second tap never stopped it. The handler
  reset its auto-repeat guard only on key-up, which a suppressed global hook doesn't deliver
  reliably. Toggle now debounces by time and no longer depends on key-up.
- **Beeps** now play through the real audio output (sounddevice) instead of `winsound.Beep`,
  which often went silent after a reboot. Falls back to `winsound` if playback fails.
- **Autostart reliability**: the boot launch (`--autostart`) waits `autostart_delay_seconds`
  (default 20) before hooking keys/audio, so it works when audio/hooks aren't ready yet at login.
- Translated all remaining German code comments, docstrings and log messages to English.

## [0.1.0] - 2026-06-21

First public release.

### Added
- Push-to-talk dictation: hold a key, speak, release — text is inserted into the active field.
- Three modes (all hotkeys configurable): **F8** plain dictation, **F9** LLM polish,
  **F10** structure-as-prompt.
- **F10 prompt profiles** — per-project context in `prompts/*.md`, switchable from the tray.
- **Karpathy coding guidelines** woven into F10 prompts (toggle with `include_karpathy`).
- **Output language** for F10 (`output_language`) — e.g. dictate Chinese, get an English prompt.
- **Multi-language STT** via Deepgram, including Chinese (`zh`), Japanese, and more.
- **Armed insertion mode** — load the dictation and paste it when you stay in the window,
  or fire it later with `Ctrl+V`.
- **Custom vocabulary** — boost recognition of names and jargon via `deepgram.keyterms` (Nova-3).
- Interactive **setup wizard** (`python apollo.py --setup`) with sign-up links.
- Tray icon, optional autostart, and a single-instance guard.
- Clear, actionable error messages for API, network and microphone failures.
- Colored ASCII startup banner, MIT license, English docs.
