"""Guard the one-provider setup and the original project presentation.

Legacy names appear only in fixtures so upgrades from real old configurations stay tested.
No desktop, microphone, registry writes or paid requests are used.
"""
import hashlib
import json
from pathlib import Path
import sys
import types

import pytest

import apollo

ROOT = Path(__file__).resolve().parents[1]


def prepare_setup(monkeypatch, tmp_path, legacy):
    path = tmp_path / "config.json"
    monkeypatch.setattr(apollo, "CONFIG_PATH", str(path))
    calls = []
    monkeypatch.setattr(apollo, "enable_autostart", lambda: calls.append("enable"))
    monkeypatch.setattr(apollo, "disable_autostart", lambda: calls.append("disable"))
    if legacy:
        path.write_text(json.dumps({
            "stt_engine": "deepgram",
            "deepgram": {"api_key": "retired-key", "language": "de", "keyterms": ["Apollo"]},
            "smoothing": {"api_key": "saved-openrouter-key"},
            "hotkeys": {"dictate": "f7"},
            "prompt_profiles": {"active": "my-project"},
        }), encoding="utf-8")
    return path, calls


def check_saved_setup(path, legacy):
    cfg = json.loads(path.read_text(encoding="utf-8"))
    assert "deepgram" not in cfg
    assert "stt_engine" not in cfg
    assert "live" not in cfg["insertion"]
    assert cfg["smoothing"]["api_key"] == ("saved-openrouter-key" if legacy else "new-openrouter-key")
    if legacy:
        assert cfg["openrouter_stt"]["language"] == "de"
        assert cfg["hotkeys"]["dictate"] == "f7"
        assert cfg["prompt_profiles"]["active"] == "my-project"
        backup = json.loads(path.with_name("config.json.bak").read_text(encoding="utf-8"))
        assert backup["deepgram"]["api_key"] == "retired-key"


@pytest.mark.parametrize("legacy", [False, True], ids=["fresh", "upgrade"])
def test_console_setup_only_offers_openrouter(monkeypatch, tmp_path, capsys, legacy):
    path, startup = prepare_setup(monkeypatch, tmp_path, legacy)
    prompts = []
    answers = iter(["en", "n", "n", "y"])

    def get_key(prompt):
        prompts.append(prompt)
        return "" if legacy else "new-openrouter-key"

    def answer(prompt):
        prompts.append(prompt)
        return next(answers)

    import apollo_terminal
    import apollo_setup
    monkeypatch.setattr(apollo_terminal.getpass, "getpass", get_key)
    monkeypatch.setattr(apollo_setup, "check_key", lambda key: "")
    monkeypatch.setattr("builtins.input", answer)
    apollo.run_setup()
    output = capsys.readouterr().out + "\n".join(prompts)
    assert "OpenRouter" in output
    assert "deepgram" not in output.lower()
    assert "retired-key" not in output
    assert "saved-openrouter-key" not in output
    assert "new-openrouter-key" not in output
    assert startup == ["disable"]
    check_saved_setup(path, legacy)


@pytest.mark.parametrize("legacy", [False, True], ids=["fresh", "upgrade"])
def test_windowed_setup_passes_preserved_config_to_full_wizard(monkeypatch, tmp_path, legacy):
    path, startup = prepare_setup(monkeypatch, tmp_path, legacy)
    calls = []
    module = types.ModuleType("apollo_setup")
    def wizard(cfg, received_path, autostart, initial_error=""):
        assert received_path == str(path)
        assert not initial_error
        calls.append(cfg)
        if not apollo.api_key(cfg): cfg["smoothing"]["api_key"] = "new-openrouter-key"
        apollo.save_config(received_path, cfg)
        autostart(False)
        return True
    module.run_windowed_setup = wizard
    monkeypatch.setitem(sys.modules, "apollo_setup", module)
    assert apollo.run_setup_gui() is True
    assert len(calls) == 1 and startup == ["disable"]
    check_saved_setup(path, legacy)


def test_cancelled_windowed_setup_does_not_save_or_enable_autostart(monkeypatch, tmp_path):
    path, startup = prepare_setup(monkeypatch, tmp_path, False)
    module = types.ModuleType("apollo_setup")
    module.run_windowed_setup = lambda *args, **kwargs: False
    monkeypatch.setitem(sys.modules, "apollo_setup", module)
    assert apollo.run_setup_gui() is False
    assert not path.exists() and startup == []


def test_invalid_config_stays_intact_when_setup_cancelled(monkeypatch, tmp_path):
    path, startup = prepare_setup(monkeypatch, tmp_path, False)
    path.write_text("invalid", encoding="utf-8")
    module = types.ModuleType("apollo_setup")
    def cancel(cfg, received_path, autostart, initial_error=""):
        assert initial_error and cfg == apollo.default_config()
        return False
    module.run_windowed_setup = cancel
    monkeypatch.setitem(sys.modules, "apollo_setup", module)
    assert not apollo.run_setup_gui()
    assert path.read_text() == "invalid" and startup == []


@pytest.mark.parametrize("relative", [
    "README.md", "CHANGELOG.md", "docs/configuration.md", "docs/development.md",
    "apollo.py", "apollo_api.py", "selftest.py", "config.example.json",
    "Apollo.bat", "setup.bat", "debug.bat", "bootstrap.bat", "requirements.txt",
])
def test_user_facing_files_do_not_reintroduce_retired_provider(relative):
    assert "deepgram" not in (ROOT / relative).read_text(encoding="utf-8").lower()


def test_current_logo_and_install_commands_are_present():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert 'src="assets/apollo-monochrome.png"' in readme
    assert readme.index('src="assets/apollo-monochrome.png"') < readme.index("Download for Windows")
    assert "https://github.com/perikx/apollo-s2t/releases/latest" in readme
    assert "docs/configuration.md" in readme and "docs/development.md" in readme
    assert "img.shields.io" not in readme
    assert len(readme.split()) < 170
    artwork = (ROOT / "assets/apollo-monochrome.png").read_bytes()
    assert hashlib.sha256(artwork).hexdigest() == "ba697c9e3ab47fc909ef3ff1af3780edc0b5f0ee2603280d49f32e45b4fafece"
    # Keep the user-supplied original artwork intact.
    logo = (ROOT / "assets/apollo.png").read_bytes()
    assert hashlib.sha256(logo).hexdigest() == "ba697c9e3ab47fc909ef3ff1af3780edc0b5f0ee2603280d49f32e45b4fafece"
