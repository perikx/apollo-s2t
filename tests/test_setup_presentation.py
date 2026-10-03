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
    answers = iter(["n", "n"])

    def get_key(prompt):
        prompts.append(prompt)
        return "" if legacy else "new-openrouter-key"

    def answer(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr(apollo.getpass, "getpass", get_key)
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
def test_windowed_setup_only_offers_openrouter(monkeypatch, tmp_path, legacy):
    path, startup = prepare_setup(monkeypatch, tmp_path, legacy)
    dialogs, key_prompts, root_events = [], [], []
    root = types.SimpleNamespace(
        withdraw=lambda: root_events.append("withdraw"),
        destroy=lambda: root_events.append("destroy"),
    )

    def ask_key(title, message, **kwargs):
        assert kwargs["show"] == "*"
        assert kwargs["parent"] is root
        key_prompts.append(message)
        return "new-openrouter-key"

    def startup_choice(title, message, **kwargs):
        dialogs.append(message)
        return False

    def unexpected_error(*args, **kwargs):
        pytest.fail("Setup unexpectedly showed an error dialog")

    tkinter = types.ModuleType("tkinter")
    tkinter.Tk = lambda: root
    tkinter.simpledialog = types.SimpleNamespace(askstring=ask_key)
    tkinter.messagebox = types.SimpleNamespace(
        askyesno=startup_choice,
        showinfo=lambda title, message, **kwargs: dialogs.append(message),
        showerror=unexpected_error,
    )
    monkeypatch.setitem(sys.modules, "tkinter", tkinter)
    assert apollo.run_setup_gui() is True
    assert len(key_prompts) == (0 if legacy else 1)
    if not legacy:
        assert "OpenRouter" in key_prompts[0]
    assert "deepgram" not in "\n".join(dialogs + key_prompts).lower()
    assert startup == ["disable"]
    assert root_events == ["withdraw", "destroy"]
    check_saved_setup(path, legacy)


def test_cancelled_windowed_setup_does_not_save_or_enable_autostart(monkeypatch, tmp_path):
    path, startup = prepare_setup(monkeypatch, tmp_path, False)
    destroyed = []
    tkinter = types.ModuleType("tkinter")
    tkinter.Tk = lambda: types.SimpleNamespace(withdraw=lambda: None, destroy=lambda: destroyed.append(True))
    tkinter.simpledialog = types.SimpleNamespace(askstring=lambda *args, **kwargs: None)
    tkinter.messagebox = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "tkinter", tkinter)
    assert apollo.run_setup_gui() is False
    assert not path.exists()
    assert startup == []
    assert destroyed == [True]


@pytest.mark.parametrize("relative", [
    "README.md", "CHANGELOG.md", "docs/configuration.md", "docs/development.md",
    "apollo.py", "apollo_api.py", "selftest.py", "config.example.json",
    "Apollo.bat", "setup.bat", "debug.bat", "bootstrap.bat", "requirements.txt",
])
def test_user_facing_files_do_not_reintroduce_retired_provider(relative):
    assert "deepgram" not in (ROOT / relative).read_text(encoding="utf-8").lower()


def test_current_logo_and_install_commands_are_present():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert 'src="assets/apollo.png"' in readme
    assert readme.index('src="assets/banner.svg"') < readme.index("## Install and run")
    assert readme.index("git clone https://github.com/perikx/apollo-s2t.git") < readme.index("## Use it")
    assert "cd apollo-s2t\n.\\Apollo.bat" in readme
    assert "git pull --ff-only" in readme
    for label in ("License: MIT", "Platform: Windows 10/11", "Python 3.10+", "GitHub stars"):
        assert f'alt="{label}"' in readme
    # Compare Git's blob digest, normalizing checkout newlines on Windows.
    banner = (ROOT / "assets/banner.svg").read_text(encoding="utf-8").encode("utf-8")
    digest = hashlib.sha1(f"blob {len(banner)}\0".encode("ascii") + banner).hexdigest()
    assert digest == "7030e0a3d2db062b631e942ba7e475aa2be81523"
