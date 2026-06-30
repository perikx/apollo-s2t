# Changelog

All notable changes to Apollo s2t are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **Choosable speech engine** (`stt_engine`): Deepgram, or transcribe via **OpenRouter** with any
  audio model (`openrouter_stt.model`) — e.g. `microsoft/mai-transcribe-1.5` (100+ languages incl.
  Chinese) or `nvidia/parakeet-tdt-0.6b-v3` (cheapest, EU). One key for STT + LLM. Picked in the wizard.

- **Toggle hotkeys** (`hotkey_mode: "toggle"`): tap to start, tap to stop — no need to hold
  the key during long dictation. Default stays `"hold"`.

### Changed
- Setup wizard: **press the key** you want for a hotkey instead of typing its name.

### Fixed
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
