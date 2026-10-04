from copy import deepcopy
import json
from pathlib import Path
import pytest
from apollo_config import (ConfigError, DEFAULTS, DEFAULT_STT_MODEL, DEFAULT_SMOOTHING_MODEL,
                           api_key, default_config, normalize_config, read_config, save_config)


def test_old_defaults_upgrade_and_custom_settings_survive():
    raw = {"stt_engine": "deepgram", "deepgram": {"language": "de", "api_key": "obsolete"},
           "openrouter_stt": {"model": "microsoft/mai-transcribe-1.5"},
           "smoothing": {"model": "google/gemini-3.1-flash-lite", "api_key": "keep-key"},
           "hotkeys": {"dictate": "f7"}, "prompt_profiles": {"active": "my-project"},
           "insertion": {"live": True, "mode": "armed"}}
    before = deepcopy(raw)
    cfg, notes = normalize_config(raw)
    assert raw == before
    assert cfg["openrouter_stt"]["model"] == DEFAULT_STT_MODEL
    assert cfg["smoothing"]["model"] == DEFAULT_SMOOTHING_MODEL
    assert cfg["smoothing"]["api_key"] == "keep-key"
    assert cfg["openrouter_stt"]["language"] == "de"
    assert cfg["hotkeys"]["dictate"] == "f7"
    assert cfg["prompt_profiles"]["active"] == "my-project"
    assert "deepgram" not in cfg and "stt_engine" not in cfg
    assert "live" not in cfg["insertion"]
    assert notes


def test_custom_models_not_overwritten():
    cfg, _ = normalize_config({"smoothing": {"model": "custom/text"}, "openrouter_stt": {"model": "custom/stt"}})
    assert cfg["smoothing"]["model"] == "custom/text"
    assert cfg["openrouter_stt"]["model"] == "custom/stt"


def test_version_two_can_explicitly_pin_old_model():
    cfg, _ = normalize_config({"config_version": 2, "openrouter_stt": {"model": "microsoft/mai-transcribe-1.5"}})
    assert cfg["openrouter_stt"]["model"] == "microsoft/mai-transcribe-1.5"


def test_deprecated_qwen_versionless_default_is_upgraded():
    cfg, _ = normalize_config({"openrouter_stt": {"model": "qwen/qwen3-asr-flash-2026-02-10"}})
    assert cfg["openrouter_stt"]["model"] == DEFAULT_STT_MODEL


def test_migration_is_idempotent():
    cfg, _ = normalize_config({})
    again, notes = normalize_config(cfg)
    assert again == cfg and not notes


def test_defaults_are_independent():
    cfg = default_config()
    cfg["hotkeys"]["dictate"] = "changed"
    assert default_config() == DEFAULTS


def test_environment_key_takes_precedence_without_being_persisted(monkeypatch):
    cfg = default_config()
    cfg["smoothing"]["api_key"] = "file-key"
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    assert api_key(cfg) == "env-key"
    assert cfg["smoothing"]["api_key"] == "file-key"


def test_placeholder_key_is_not_valid():
    cfg = default_config()
    cfg["smoothing"]["api_key"] = "YOUR_OPENROUTER_KEY"
    assert not api_key(cfg)


@pytest.mark.parametrize("raw", [[], {"audio": None}, {"config_version": 99},
    {"hotkeys": {"polish": "f8"}}, {"hotkeys": {"dictate": "ctrl+f8"}},
    {"max_pending_recordings": 0}, {"max_pending_recordings": True},
    {"audio": {"samplerate": float("nan")}}, {"insertion": {"restore_delay": -1}},
    {"smoothing": {"timeout_seconds": "20"}}, {"smoothing": {"api_key": None}},
    {"prompt_profiles": {"active": "../secret"}}, {"beep": "false"},
    {"min_record_seconds": 5, "max_record_seconds": 2}, {"hotkey_mode": "wat"},
    {"ui_language": "fr"}, {"ui_language": None}, {"ui_language": {}}])
def test_invalid_config_rejected_before_runtime(raw):
    with pytest.raises(ConfigError):
        normalize_config(raw)


def test_backup_preserves_original_and_does_not_change_again(tmp_path):
    path = tmp_path / "config.json"
    original = '{"smoothing":{"api_key":"private-key"}}'
    path.write_text(original)
    cfg, _ = read_config(path)
    assert path.with_name("config.json.bak").read_text() == original
    assert json.loads(path.read_text()) == cfg
    read_config(path)
    assert path.with_name("config.json.bak").read_text() == original


def test_invalid_json_not_overwritten(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"invalid"')
    with pytest.raises(ConfigError):
        read_config(path)
    assert path.read_text() == '{"invalid"'
    assert not path.with_name("config.json.bak").exists()


def test_atomic_save_failure_leaves_original(monkeypatch, tmp_path):
    import apollo_config
    path = tmp_path / "config.json"
    path.write_text("original")
    def fail(*args):
        raise OSError("disk failed")
    monkeypatch.setattr(apollo_config.os, "replace", fail)
    with pytest.raises(OSError):
        save_config(path, default_config())
    assert path.read_text() == "original"
    assert not list(tmp_path.glob("*.tmp"))


def test_read_only_check_does_not_migrate(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{}")
    read_config(path, migrate=False)
    assert path.read_text() == "{}"
    assert not path.with_name("config.json.bak").exists()


def test_example_matches_actual_defaults():
    path = Path(__file__).resolve().parents[1] / "config.example.json"
    assert json.loads(path.read_text()) == default_config()
