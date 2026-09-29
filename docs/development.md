# Development and testing

The desktop app is Windows-only. Configuration/API modules and the test suite are
platform-independent. Use Python 3.10+.

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m compileall -q apollo.py apollo_api.py apollo_config.py selftest.py
```

Tests replace the microphone, keyboard, clipboard and timers with isolated doubles.
They never call a paid provider. FIFO/shutdown tests use real worker threads with explicit
synchronization. Regression coverage includes hotkey toggle behavior, clipboard ownership,
stale timers, origin snapshots, bounded capture, rewrite fallback and configuration migration.
The GitHub Actions matrix runs the suite on Windows and Ubuntu with Python 3.10 and 3.13.

## Code map

- `apollo.py`: recording, tray/hotkeys, FIFO processing, prompts and Windows insertion.
- `apollo_config.py`: one source of defaults, validation, atomic writes and migration.
- `apollo_api.py`: OpenRouter transcription and rewrite request/response contracts.
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
   new defaults and absence of Deepgram options. Never commit real credentials.
7. Run `selftest.py --live`, then build with `packaging\build-exe.bat`; test first launch
   of `dist\Apollo.exe` from a separate writable folder without `config.json`.

Live checks can incur API charges. A silence-only HTTP 200 is not evidence of recognition
quality. Compare real recordings against reference text before claiming accuracy improvements.

## Packaging

`packaging\build-exe.bat` builds a windowed executable with PyInstaller on Windows.
The bundled model/configuration defaults and profiles use the same Python modules as the
source app. First launch uses a Tk key-entry dialog because the executable has no console.
No executable binary is committed by this change; build output stays under `dist/`.
