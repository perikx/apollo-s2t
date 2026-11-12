<p>
  <img src="assets/apollo-sun.svg" alt="Apollo sun mark" width="72" height="72">
</p>

# apollo

Dictate into the app where you type. Press a key to record, then press it again to insert your words.

**[Download for Windows](https://github.com/perikx/apollo-s2t/releases/latest/download/apollo.exe)** · [Release notes](https://github.com/perikx/apollo-s2t/releases/latest)

## Start dictating

1. Save `apollo.exe` in a writable folder and open it. No Python installation is required.
2. Enter your [OpenRouter key](https://openrouter.ai/keys). Usage is paid from your OpenRouter balance.
3. Select a text field. Press F8 to start, then press F8 to stop.

| Key | Action |
| --- | --- |
| F8 | Dictate |
| F9 | Clean up speech while keeping your words |
| F10 | Build a prompt |

Change keys, models and prompts in Settings. The floating sun opens the controls. Recovery keeps failed recordings for another attempt.

## Keep Apollo current

Automatic updates are enabled by default. In version 0.5.2, select **General → Update now** to check and install immediately.
Keep `config.json` and the `prompts` folder when you replace an older executable.

## Your data

Audio and text are sent through OpenRouter. Recovery is cached locally; configuration can contain your API key.

[Settings](docs/configuration.md) · [Development](docs/development.md) · [Changelog](CHANGELOG.md) · [Issues](https://github.com/perikx/apollo-s2t/issues) · [MIT license](LICENSE)
