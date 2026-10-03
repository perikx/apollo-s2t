# Development and testing

The desktop app is Windows-only. Configuration/API modules and the test suite are
platform-independent. Use Python 3.10+.

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m compileall -q apollo.py apollo_api.py apollo_config.py apollo_recovery.py selftest.py
```

Tests replace the microphone, keyboard, clipboard and timers with isolated doubles.
They never call a paid provider. FIFO/shutdown tests use real worker threads with explicit
synchronization. Regression coverage includes hotkey toggle behavior, clipboard ownership,
stale timers, origin snapshots, bounded capture, rewrite fallback and configuration migration.
Recovery regressions use temporary folders to check WAV checkpoints, interrupted writes,
saved raw/final text, restart recovery and clipboard-only recovery. API doubles cover
429 recovery, Retry-After seconds/dates, retry limits, cancellation, ambiguous network
failures and privacy-safe error hints. These tests do not reproduce physical power loss
or prove durability on every disk/controller.
The GitHub Actions matrix runs the suite on Windows and Ubuntu with Python 3.10 and 3.13.
Setup regressions exercise the console and windowed first-run paths, including upgrades
from old configurations. Presentation checks retain the original README banner and prevent
retired-provider references from returning to setup, launchers or user documentation.

## Code map

- `apollo.py`: recording, tray/hotkeys, FIFO processing, prompts and Windows insertion.
- `apollo_config.py`: one source of defaults, validation, atomic writes and migration.
- `apollo_api.py`: OpenRouter transcription and rewrite request/response contracts.
- `apollo_recovery.py`: local WAV checkpoints, recovery metadata and raw/final text files.
- `apollo_overlay.py`: floating control/speech bubble and in-app settings/recovery/debug.
- `apollo_design.py` / `apollo_widgets.py`: Qt surfaces, vector icons and selectors.
- `apollo_setup.py`: three-step setup, read-only API-key validation and editable errors.
- `apollo_models.py`: capability-filtered public catalog and explicitly labeled pricing.
- `bootstrap.bat`: shared environment creation and dependency refresh.
- `selftest.py`: offline checks, with explicitly opt-in paid end-to-end diagnostics.

## Windows acceptance check

Automated headless tests do not prove microphone, desktop or provider accuracy.
Before a release, run these checks on a Windows PC with an OpenRouter balance:

1. Fresh clone: run `Apollo.bat`, enter a key, verify the tray icon and start/stop tones.
2. Dictate German, English and a mixed technical sentence with F8. Verify original
   words, names, numbers and negations. Try F9 and F10 without losing requirements.
3. Start successive recordings while the first is processing. Verify order and the
   captured origin window. Check instant, hybrid, armed and a closed origin window.
4. Copy unrelated text while a result is being restored. Verify it is not overwritten.
5. Quit while a request is pending: no late paste. Restart and verify hotkeys and autostart.
6. Upgrade a copy of a legacy configuration: verify backup, preserved keys/hotkeys/profiles,
   new defaults and an OpenRouter-only setup. Never commit real credentials.
7. Run `selftest.py --live`, then build with `packaging\build-exe.bat`; test first launch
   of `dist\apollo.exe` from a separate writable folder without `config.json`.
8. Record several minutes. Verify a growing playable WAV in `recovery/`, then simulate
   a transcription rejection with a local test double. Verify retry progress and that
   failed audio remains available. Do not provoke real provider limits for this check.
9. Restart with a saved failed recording. Verify no automatic API request. Select it
   through **Recover saved dictation**, then paste with Ctrl+V. Confirm a cached text
   recovery makes no API call and does not inject text into the focused app.
10. Interrupt an isolated test process during capture and reopen its saved audio. Verify
    completed checkpoints remain usable; do not claim the unflushed tail was preserved.

Live checks can incur API charges. A silence-only HTTP 200 is not evidence of recognition
quality. Compare real recordings against reference text before claiming accuracy improvements.

Recovery data is private and expires under the configured cache policy. Use synthetic recordings
for tests, temporary recovery directories, and never commit `recovery/`, transcripts or
real prompt context. Source changes take effect only after the running Apollo process
is quit and restarted; an older running instance still uses the previous code.

`python tests/native_overlay_smoke.py .pytest_cache/overlay-preview` exercises the actual
Windows floating controls with synthetic recordings and a fake model catalog. It checks
satellite-button navigation, stored key/text preview, incompatible-model rejection,
waveform rendering, right-edge hiding, silent errors while hidden, tray-style reopening
and X collapse. It also checks invalid-input correction, editable keys, optional
fallback, model prices and all three wizard steps. It verifies normal Windows
z-order against a second test window, wheel scrolling over unfocused fields,
inline selectors without additional top-level windows, persistent safe failure
causes, live microphone/stall/backup state and the bounded 300-message debug view.
Screenshots use Qt's own render
capture, so a locked desktop does not produce an empty screenshot. Inspect both
normal and high DPI output; real microphone/recognition checks remain separate.
Screenshots remain local for visual inspection. The Windows pytest
suite also runs this isolated smoke test; it does not record, paste or call providers.

## Packaging

`packaging\build-exe.bat` builds a windowed executable with PyInstaller on Windows.
The bundled model/configuration defaults and profiles use the same Python modules as the
source app. The windowed executable uses a complete Qt setup wizard because it has no console.
QtCore/QtGui/QtWidgets come from PySide6 Essentials on Windows; Tk is excluded.
The original PNG stays intact. Widgets crop its transparent outer viewport for a
circle that fills the complete control. Qt handles per-monitor DPI/transparency.
No executable binary is committed by this change; build output stays under `dist/`.

Run `python packaging/check-exe.py` after building. It checks the original logo,
Qt platform plugin, absence of private config and unrelated ICU DLLs, then launches
the real executable without configuration and cancels its setup. This exercises
actual DLL loading; source/GUI tests alone do not prove a frozen app starts.
