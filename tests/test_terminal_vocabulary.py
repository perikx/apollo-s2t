from copy import deepcopy
import json
from unittest.mock import Mock
import pytest
import requests
import apollo_api as api
from apollo_config import ConfigError, default_config, normalize_config
from apollo_terminal import Terminal, run_terminal_setup


def terminal(answers, secrets=()):
    answers, secrets = iter(answers), iter(secrets)
    output = []
    return Terminal(read=lambda _: next(answers), secret=lambda _: next(secrets), write=output.append, effects=False), output


@pytest.mark.parametrize("language", ["en", "de", "zh"])
def test_terminal_setup_auth_retry_preserves_config_and_hides_key(tmp_path, monkeypatch, language):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    cfg = default_config(); cfg["prompt_profiles"]["active"] = "private-profile"
    path = tmp_path / "config.json"; original = json.dumps(cfg); path.write_text(original)
    ui, output = terminal([language, "n", "n", "y"], ["invalid-private-key", "valid-private-key"])
    startup = []; check = Mock(side_effect=["Der API-Schlüssel wurde abgelehnt. Bitte korrigieren und erneut versuchen.", ""])
    assert run_terminal_setup(cfg, path, startup.append, terminal=ui, check=check)
    saved = json.loads(path.read_text()); assert saved["smoothing"]["api_key"] == "valid-private-key"
    assert saved["ui_language"] == language and saved["prompt_profiles"]["active"] == "private-profile"
    assert cfg["smoothing"]["api_key"] == "" and startup == [False]
    assert "private-key" not in "\n".join(output)
    assert path.with_name("config.json.bak").read_text() == original


@pytest.mark.parametrize("answers,secrets", [(["/cancel"], []), (["en"], ["/cancel"]), (["en", "n", "n", "n"], ["key"])])
def test_cancel_does_not_save_or_touch_startup(tmp_path, monkeypatch, answers, secrets):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    ui, _ = terminal(answers, secrets); startup = []
    path = tmp_path / "config.json"
    assert not run_terminal_setup(default_config(), path, startup.append, terminal=ui, check=lambda _: "")
    assert not path.exists() and startup == []


def test_invalid_keys_can_be_corrected_without_leaving_setup(tmp_path, monkeypatch):
    import keyboard
    monkeypatch.setattr(keyboard, "key_to_scan_codes", lambda key: (sum(map(ord,key)),), raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    ui, output = terminal(["en", "y", "f8", "f8", "f10", "toggle", "auto", "n", "m", "p", "hold", "de", "n", "y"], ["key"])
    def unavailable(_): raise requests.ConnectionError()
    path = tmp_path / "config.json"
    assert run_terminal_setup(default_config(), path, lambda _: None, terminal=ui, check=lambda _: "", discover=unavailable)
    cfg = json.loads(path.read_text()); assert cfg["hotkeys"] == {"dictate": "n", "polish": "m", "prompt": "p"}
    assert cfg["hotkey_mode"] == "hold" and cfg["openrouter_stt"]["language"] == "de"


def test_vocabulary_biases_recognition_without_replacing_output(monkeypatch):
    post = Mock(return_value={"text": "Pando"}); monkeypatch.setattr(api, "_post", post)
    cfg = {"vocabulary": [" PANDU ", "PANDU", "SUPERBASE"]}; before = deepcopy(cfg)
    assert api.transcribe_openrouter(b"wave", cfg, "key") == "Pando"
    assert post.call_args.args[2]["provider"] == {"options": {"azure": {"phraseList": {"phrases": ["PANDU", "SUPERBASE"]}}}}
    assert cfg == before


def test_fallback_removes_model_specific_hints_but_preserves_audio(monkeypatch):
    response = requests.Response(); response.status_code = 429
    post = Mock(side_effect=[requests.HTTPError(response=response), {"text": "PANDU"}]); monkeypatch.setattr(api, "_post", post)
    assert api.transcribe_openrouter(b"wave", {"vocabulary": ["PANDU"], "language": "de"}, "key", wait=lambda _: False) == "PANDU"
    first, second = [call.args[2] for call in post.call_args_list]
    assert "provider" in first and "provider" not in second
    assert first["input_audio"] == second["input_audio"] and second["language"] == "de"


@pytest.mark.parametrize("value", [None, "PANDU", [""], ["a"*101], ["a\nsecret"], [2], ["a"]*101])
def test_invalid_vocabulary_blocked_before_network(monkeypatch, value):
    post = Mock(); monkeypatch.setattr(api, "_post", post)
    with pytest.raises(ConfigError): api.transcribe_openrouter(b"wave", {"vocabulary": value}, "key")
    with pytest.raises(ConfigError): normalize_config({"openrouter_stt": {"vocabulary": value}})
    post.assert_not_called()


def test_other_models_do_not_receive_unverified_options(monkeypatch):
    post = Mock(return_value={"text": "ok"}); monkeypatch.setattr(api, "_post", post)
    api.transcribe_openrouter(b"wave", {"model": "openai/gpt-4o-mini-transcribe", "vocabulary": ["PANDU"]}, "key")
    assert "provider" not in post.call_args.args[2]
