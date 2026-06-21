# Changelog

All notable changes to Apollo s2t are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/).

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
