from copy import deepcopy
import json
from pathlib import Path
import pytest
from apollo_config import (ConfigError, DEFAULTS, DEFAULT_STT_MODEL, DEFAULT_SMOOTHING_MODEL,
                           api_key, default_config, normalize_config, read_config, save_config,
                           scan_codes_of)


def test_removed_keys_are_dropped_and_custom_settings_survive():
    raw = {"stt_engine": "deepgram", "deepgram": {"language": "de", "api_key": "obsolete"},
           "autostart_delay_seconds": 20, "max_record_seconds": 9, "audio": {"samplerate": 8000, "device": 3},
           "openrouter_stt": {"timeout_seconds": 5, "base_url": "https://x", "vocabulary": ["Apollo"]},
           "smoothing": {"api_key": "keep-key", "max_tokens": 8192, "temperature": 1},
           "hotkeys": {"dictate": "f7"}, "prompt_profiles": {"active": "my-project", "dir": "x", "include_karpathy": False},
           "insertion": {"live": True, "restore_delay": 0.4}, "recovery_cache": {"minutes": 30, "max_mb": 64}}
    before = deepcopy(raw)
    cfg, notes = normalize_config(raw)
    assert raw == before and not notes
    assert cfg["smoothing"] == {"api_key": "keep-key", "model": DEFAULT_SMOOTHING_MODEL}
    assert cfg["audio"] == {"device": 3}
    assert cfg["insertion"] == {"restore_clipboard": True}
    assert cfg["recovery_cache"] == {"minutes": 30}
    assert cfg["openrouter_stt"]["vocabulary"] == ["Apollo"]
    assert cfg["hotkeys"]["dictate"] == "f7"
    assert cfg["prompt_profiles"] == {"active": "my-project", "output_language": "english"}
    assert set(cfg) == set(DEFAULTS)


def test_custom_models_not_overwritten():
    cfg, _ = normalize_config({"smoothing": {"model": "custom/text"}, "openrouter_stt": {"model": "custom/stt"}})
    assert cfg["smoothing"]["model"] == "custom/text"
    assert cfg["openrouter_stt"]["model"] == "custom/stt"


def test_version_two_can_explicitly_pin_old_model():
    cfg, _ = normalize_config({"config_version": 2, "openrouter_stt": {"model": "microsoft/mai-transcribe-1.5"}})
    assert cfg["openrouter_stt"]["model"] == "microsoft/mai-transcribe-1.5"


def test_same_vendor_fallback_migrates_to_other_vendor():
    cfg, notes = normalize_config({"config_version": 2, "openrouter_stt": {"fallback_model": "microsoft/mai-transcribe-1.5"}})
    assert cfg["openrouter_stt"]["fallback_model"] == "elevenlabs/scribe-v2" and notes
    assert normalize_config(cfg) == (cfg, [])


def test_other_fallback_choices_are_kept():
    for fallback in ("openai/whisper-1", None):
        cfg, _ = normalize_config({"config_version": 2, "openrouter_stt": {"fallback_model": fallback}})
        assert cfg["openrouter_stt"]["fallback_model"] == fallback


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
    {"recovery_cache": {"minutes": 0}}, {"smoothing": {"api_key": None}},
    {"prompt_profiles": {"active": "../secret"}}, {"beep": "false"},
    {"hotkey_mode": "wat"},
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


def test_scan_code_check_rejects_unknown_and_shared_keys():
    codes = {"a": [1], "b": [2], "c": [1, 3], "none": []}.__getitem__
    assert scan_codes_of(["a", "b"], codes) == [{1}, {2}]
    for keys in (["a", "c"], ["a", "none"]):
        with pytest.raises(ConfigError):
            scan_codes_of(keys, codes)
