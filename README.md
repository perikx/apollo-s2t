# apollo s2t

<p align="center">
  <img src="assets/apollo.png" alt="Apollo s2t – weiße Lyra auf goldenem Hintergrund" width="240">
</p>

<p align="center">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Platform: Windows 10/11" src="https://img.shields.io/badge/platform-Windows%2010%2F11-0078D6">
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776AB">
  <img alt="GitHub stars" src="https://img.shields.io/github/stars/perikx/apollo-s2t?style=social">
</p>

**Tap a key, speak, tap again. Your words appear in the active text field.**

Windows 10/11 dictation for ChatGPT, VS Code, Word, browsers and other text fields.
One OpenRouter API key. No subscription to Apollo. MIT licensed.

## Install and run

For the standalone Windows app, download `apollo.exe` from the
[latest release](https://github.com/perikx/apollo-s2t/releases/latest) and put it in a writable folder.
First launch runs a three-step setup for your OpenRouter API key, recording keys,
models and fallback. Invalid entries remain editable in the same window. Existing users should quit Apollo
before replacing the executable, keeping their configuration, prompts and recovery folder.

Install [Python 3.10+](https://www.python.org/downloads/) with **Add Python to PATH**
and [Git](https://git-scm.com/downloads/win), then paste this into PowerShell:

```powershell
git clone https://github.com/perikx/apollo-s2t.git
cd apollo-s2t
.\Apollo.bat
```

The launcher installs dependencies, asks for your [OpenRouter API key](https://openrouter.ai/keys),
and starts Apollo in the system tray. The key is masked while entering it.
**Add credit to OpenRouter:** creating a key is free, but transcription and rewriting are paid API usage.

No Git? Use **Code → Download ZIP**, extract the folder, then double-click `Apollo.bat`.

## Use it

Click into a text field, **tap F8**, speak, then **tap F8 again**. The high tone means
recording started; the low tone means it stopped. Wait for the final text to appear.

| Key | Result |
| --- | --- |
| **F8** | Transcription only. |
| **F9** | Light cleanup of punctuation, structure and clear hesitation fillers. Preserves your wording, tone and repetitions. |
| **F10** | A compact prompt for another AI. English output by default. |

Apollo inserts the complete result after recording, not word by word while speaking.
A second mode key does not interrupt an active recording. Stop it with the key that started it.
The default recording limit is five minutes; reaching it stops and processes the recording.

Click the floating Apollo circle to open **Recovery, Settings and Models** on its left.
The X on its right collapses the controls. Drag the logo anywhere on your desktop;
drop it at the right edge of a monitor to hide it. **Show apollo s2t** in the tray brings it back.
The compact 44 px logo and 32 px controls scale with Windows display settings;
hover an icon for its label. Settings windows move behind other apps when switching focus.
While recording, a speech bubble replaces the logo and follows real syllables and pauses. Errors appear
inside Apollo instead of Windows notification balloons, including when the logo is hidden.
Settings and model changes apply to the next recording and persist across restarts.
The interface and setup display your configured keys; F8/F9/F10 above are the defaults.
In **Settings**, click a recording key and press its replacement. Save to apply it;
keys cannot change during an active recording. Model selectors expand inside the same window with search and one-line USD prices
and a separate, optional fallback. Use `setup.bat` for console setup or `debug.bat`
to see diagnostic messages.

## Recover a dictation

Apollo saves audio locally while you speak and keeps it when transcription, rewriting,
or insertion fails. Click **Recovery** beside the floating logo to preview recent text,
copy it, retry saved audio or delete a recording. Each entry identifies its original key
and mode. The result is copied to the clipboard; press **Ctrl+V** where you want it.
Recovery includes live microphone/capture state and a bounded debug history of the
current run (300 messages in memory). It reports API errors, retries and fallbacks
without response bodies, transcript content or API keys. Safe failure causes are
kept with each recording and remain visible after restarting, until its cache expires.
Saved text is reused without an API call. If only audio is available, this action sends it
again using your current API settings and the original F8/F9/F10 mode and F10 context.
Restarting Apollo never resends saved recordings automatically.

Recovery is a short-lived local cache: **15 minutes, at most 10 completed recordings and
64 MB** by default. Settings offer 5–60 minutes. Older entries, including recordings from
earlier Apollo versions, are automatically deleted on startup and during use. Keep text
you need by copying it elsewhere. Active recording/processing is protected and can
temporarily exceed these limits; retention starts from its last state update.
The internal `recovery/` cache contains private, unencrypted audio, text and prompt context.
There is no need to open that folder to recover a dictation.

Audio is flushed to disk roughly every half second and at normal stop. This protects
against an API failure or an interrupted process, but disk, power or microphone failures
can still lose audio, including the last unflushed portion. Details are in the
[recovery guide](docs/configuration.md#saved-recordings-and-recovery).

## Defaults

| Stage | Model |
| --- | --- |
| Speech, including German and English | `microsoft/mai-transcribe-2` |
| F9/F10 text rewriting | `google/gemini-3.5-flash-lite` |

Speech language is detected automatically, including mixed-language dictation.
F9/F10 use minimal reasoning and latency-first provider routing. If rewriting fails,
returns empty text or is cut off, Apollo uses the original transcript instead.
Models remain configurable. See [configuration and model notes](docs/configuration.md).

## Update an existing installation

**Quit Apollo from its tray menu first**, then run these commands in your Apollo folder:

```powershell
git pull --ff-only
.\Apollo.bat
```

Dependencies are updated only when `requirements.txt` changes. Old shipped model defaults
are upgraded automatically, while your key, custom model choices, hotkeys and profiles are preserved.
The first configuration upgrade saves the old file as `config.json.bak`.
All speech uses OpenRouter. Setup has no speech-provider selector or live-typing option.

A downloaded ZIP or executable does not update itself when the repository changes.
For a ZIP installation, download the current source into a new folder and copy your
`config.json` and custom `prompts/` files before launching. Rebuild an older executable
with `packaging\build-exe.bat` to use the current program and setup.

## Check a problem

```powershell
.venv\Scripts\python.exe selftest.py
```

This checks configuration and audio settings **without making an API call or recording**.
For an explicit paid microphone → STT → rewrite smoke test:

```powershell
.venv\Scripts\python.exe selftest.py --live
```

For hotkey or paste problems, quit the tray copy and use `debug.bat`. Check Windows
microphone permissions and the selected input. Pasting into an elevated app can require
the same privilege level. API authentication, missing credit and invalid-model errors
have separate messages. Logs contain counts and timings, not dictated text.

**HTTP 429** means OpenRouter or the speech provider rejected a request because of a
rate or capacity limit; it can happen even when you have made few requests. Apollo now
switches to your selected fallback after the first 429, reusing the same audio.
The shipped choice is MAI-Transcribe-2 → MAI-Transcribe-1.5. **Models** lets you
choose another compatible pair or turn fallback off. Each new recording starts with your configured primary model again. There are
at most three requests total, respecting the server's wait instruction. Longer waits
and other failures leave the recording available for recovery.
The old generic error message cannot identify which service imposed a particular limit.
See [OpenRouter's rate-limit explanation](https://openrouter.ai/docs/api_reference/limits).

## More

[Configuration, profiles and clipboard modes](docs/configuration.md) ·
[Contributing and tests](docs/development.md) · [Changelog](CHANGELOG.md)

Build a standalone Windows executable with `packaging\build-exe.bat`.
It opens a key-entry dialog on first launch. Python is not needed on the target PC.

## Privacy

Audio is saved in the local `recovery/` folder and sent to OpenRouter for transcription.
Raw and final transcripts are saved there too, along with the recording mode and original
prompt context. These files are **not encrypted** and remain until you delete them.
Recovery metadata does not copy your API configuration or credentials; the folder is
ignored by Git. Do not share it without checking its contents.

F9/F10 additionally send the transcript, and F10's selected project context, for rewriting.
Provider data policies apply. No Apollo telemetry. Saved recordings are not uploaded on startup.
Clipboard insertion necessarily makes the text available through the system clipboard.

`config.json` and its migration backup can contain an API key in plain text; both are
ignored by Git. Alternatively set `OPENROUTER_API_KEY` in your environment.
Do not share configuration files or commit private prompt profiles.

[MIT License](LICENSE) · [Report a problem](https://github.com/perikx/apollo-s2t/issues)
