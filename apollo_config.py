"""Configuration defaults, validation and non-destructive upgrades (no Windows imports)."""
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import tempfile

CONFIG_VERSION = 2
DEFAULT_STT_MODEL = "microsoft/mai-transcribe-2"
DEFAULT_SMOOTHING_MODEL = "google/gemini-3.5-flash-lite"

DEFAULTS = {
    "config_version": CONFIG_VERSION,
    "hotkeys": {"dictate": "f8", "polish": "f9", "prompt": "f10"},
    "hotkey_mode": "toggle",
    "autostart_delay_seconds": 20,
    "openrouter_stt": {
        "model": DEFAULT_STT_MODEL,
        "fallback_model": "microsoft/mai-transcribe-1.5",
        "base_url": "https://openrouter.ai/api/v1/audio/transcriptions",
        "language": "", "timeout_seconds": 60,
    },
    "smoothing": {
        "api_key": "", "model": DEFAULT_SMOOTHING_MODEL,
        "base_url": "https://openrouter.ai/api/v1/chat/completions",
        "temperature": 0.2, "reasoning_effort": "minimal",
        "max_tokens": 8192, "timeout_seconds": 20,
    },
    "prompt_profiles": {
        "active": "default", "dir": "prompts", "include_karpathy": True,
        "output_language": "english",
    },
    "audio": {"samplerate": 16000, "channels": 1, "device": None},
    "insertion": {
        "mode": "instant", "target": "focused", "click_to_paste": False,
        "armed_timeout": 30, "restore_clipboard": True, "restore_delay": 0.4,
    },
    "min_record_seconds": 0.3, "max_record_seconds": 300,
    "max_pending_recordings": 3, "beep": True,
    "overlay": {"visible": True, "x": None, "y": None},
    "recovery_cache": {"minutes": 15, "max_entries": 10, "max_mb": 64},
}


class ConfigError(ValueError):
    """An actionable configuration error, safe to display without exposing secrets."""


def default_config():
    return deepcopy(DEFAULTS)


def _merge(defaults, supplied, prefix=""):
    result = deepcopy(defaults)
    for key, value in supplied.items():
        if key in defaults and isinstance(defaults[key], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"{prefix}{key} must be a JSON object.")
            result[key] = _merge(defaults[key], value, prefix + key + ".")
        else:
            result[key] = deepcopy(value)
    return result


def normalize_config(raw):
    """Return (validated config, upgrade notes); never mutate the input.

    Versionless installations upgrade old shipped defaults once. Custom model IDs,
    keys, profiles and hotkeys survive. Version 2 explicitly pins model choices.
    """
    if not isinstance(raw, dict):
        raise ConfigError("config.json must contain a JSON object.")
    raw = deepcopy(raw)
    version = raw.get("config_version", 1)
    if type(version) is not int or version not in (1, CONFIG_VERSION):
        raise ConfigError("Unsupported config_version. Update Apollo before using this config.")
    notes = []
    legacy = raw.pop("deepgram", {})
    old_engine = raw.pop("stt_engine", None)
    if legacy or old_engine:
        notes.append("Removed the legacy speech engine; all dictation now uses OpenRouter.")
    cfg = _merge(DEFAULTS, raw)
    supplied_stt = raw.get("openrouter_stt", {})
    if "fallback_model" not in supplied_stt and cfg["openrouter_stt"]["model"] != DEFAULT_STT_MODEL:
        cfg["openrouter_stt"]["fallback_model"] = None
    if version < CONFIG_VERSION:
        stt = cfg["openrouter_stt"]
        if stt["model"] in ("microsoft/mai-transcribe-1.5", "qwen/qwen3-asr-flash-2026-02-10"):
            stt["model"] = DEFAULT_STT_MODEL
            notes.append("Updated the old speech model to MAI-Transcribe-2.")
        if cfg["smoothing"]["model"] == "google/gemini-3.1-flash-lite":
            cfg["smoothing"]["model"] = DEFAULT_SMOOTHING_MODEL
            notes.append("Updated the old rewrite model to Gemini 3.5 Flash Lite.")
        if old_engine == "deepgram" and isinstance(legacy, dict):
            lang = legacy.get("language", "")
            if not stt["language"] and lang not in (None, "", "multi", "auto"):
                stt["language"] = lang
            if legacy.get("keyterms"):
                notes.append("Legacy keyterms are in config.json.bak; they were not silently forwarded to another provider.")
    for key in ("live", "live_corrections", "type_delay", "method"):
        cfg["insertion"].pop(key, None)
    cfg["smoothing"].pop("provider", None)
    cfg["config_version"] = CONFIG_VERSION
    validate_config(cfg)
    return cfg, notes


def _number(value, label, low, high, integer=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high
            or (integer and type(value) is not int)):
        raise ConfigError(f"{label} must be {'an integer' if integer else 'a number'} between {low} and {high}.")


def validate_config(cfg):
    stt = cfg["openrouter_stt"]
    fallback = stt.get("fallback_model")
    if fallback is not None and (not isinstance(fallback, str) or not fallback.strip()):
        raise ConfigError("Bitte ein Fallback-Modell wählen oder den Fallback deaktivieren.")
    if fallback == stt["model"]:
        raise ConfigError("Hauptmodell und Fallback müssen verschieden sein.")
    cache = cfg["recovery_cache"]
    _number(cache["minutes"], "recovery_cache.minutes", 5, 60, True)
    _number(cache["max_entries"], "recovery_cache.max_entries", 1, 30, True)
    _number(cache["max_mb"], "recovery_cache.max_mb", 16, 256, True)
    if type(cfg["overlay"]["visible"]) is not bool:
        raise ConfigError("overlay.visible must be true or false.")
    for axis in ("x", "y"):
        if cfg["overlay"][axis] is not None:
            _number(cfg["overlay"][axis], "overlay." + axis, -100000, 100000, True)
    keys = cfg["hotkeys"]
    names = [keys[name] for name in ("dictate", "polish", "prompt")]
    if any(not isinstance(k, str) or not k.strip() or "+" in k for k in names):
        raise ConfigError("Hotkeys must be single key names, e.g. f8, f9, f10.")
    if len({k.strip().lower() for k in names}) != len(names):
        raise ConfigError("Bitte drei verschiedene Aufnahmetasten wählen.")
    for name in keys:
        if isinstance(keys[name], str):
            keys[name] = keys[name].strip().lower()
    if cfg["hotkey_mode"] not in ("toggle", "hold"):
        raise ConfigError("hotkey_mode must be toggle or hold.")
    ins = cfg["insertion"]
    if ins["mode"] not in ("instant", "hybrid", "armed"):
        raise ConfigError("insertion.mode must be instant, hybrid or armed.")
    if ins["target"] not in ("focused", "origin"):
        raise ConfigError("insertion.target must be focused or origin.")
    for section in ("openrouter_stt", "smoothing"):
        for field in ("model", "base_url"):
            value = cfg[section][field]
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"{section}.{field} must be a non-empty string.")
        _number(cfg[section]["timeout_seconds"], section + ".timeout_seconds", 1, 300)
    if not isinstance(cfg["smoothing"]["api_key"], str):
        raise ConfigError("smoothing.api_key must be a string.")
    if not isinstance(cfg["openrouter_stt"]["language"], str):
        raise ConfigError("openrouter_stt.language must be a string or empty for auto-detection.")
    language = cfg["openrouter_stt"]["language"].strip().lower()
    cfg["openrouter_stt"]["language"] = "" if language in ("auto", "multi") else language
    if cfg["smoothing"]["reasoning_effort"] not in (None, "none", "minimal", "low", "medium", "high"):
        raise ConfigError("smoothing.reasoning_effort is invalid; use null to omit it.")
    _number(cfg["smoothing"]["temperature"], "smoothing.temperature", 0, 2)
    _number(cfg["smoothing"]["max_tokens"], "smoothing.max_tokens", 256, 65536, True)
    _number(cfg["audio"]["samplerate"], "audio.samplerate", 8000, 192000, True)
    _number(cfg["audio"]["channels"], "audio.channels", 1, 2, True)
    _number(cfg["min_record_seconds"], "min_record_seconds", 0.05, 10)
    _number(cfg["max_record_seconds"], "max_record_seconds", 1, 1800)
    if cfg["max_record_seconds"] <= cfg["min_record_seconds"]:
        raise ConfigError("max_record_seconds must exceed min_record_seconds.")
    _number(cfg["max_pending_recordings"], "max_pending_recordings", 1, 10, True)
    _number(cfg["autostart_delay_seconds"], "autostart_delay_seconds", 0, 300)
    _number(ins["armed_timeout"], "insertion.armed_timeout", 1, 3600)
    _number(ins["restore_delay"], "insertion.restore_delay", 0, 10)
    for section, field in ((cfg, "beep"), (ins, "restore_clipboard"), (ins, "click_to_paste"),
                           (cfg["prompt_profiles"], "include_karpathy")):
        if type(section[field]) is not bool:
            raise ConfigError(f"{field} must be true or false, not a string.")
    for field in ("active", "dir", "output_language"):
        if not isinstance(cfg["prompt_profiles"][field], str):
            raise ConfigError(f"prompt_profiles.{field} must be a string.")
    profile = cfg["prompt_profiles"]["active"]
    if "/" in profile or "\\" in profile or profile in (".", ".."):
        raise ConfigError("prompt_profiles.active must be a profile name, not a path.")


def api_key(cfg):
    key = (os.environ.get("OPENROUTER_API_KEY") or cfg["smoothing"].get("api_key", "")).strip()
    return "" if key.startswith("YOUR_") else key


def save_config(path, cfg):
    """Atomic replacement: interruption must not leave half a JSON document."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle, indent=2, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_config(path, migrate=True):
    path = Path(path)
    try:
        source = path.read_bytes()
        raw = json.loads(source.decode("utf-8-sig"))
    except FileNotFoundError as exc:
        raise ConfigError("config.json not found. Run Apollo.bat or setup.bat first.") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("config.json is not valid UTF-8 JSON. Fix it or run setup.bat.") from exc
    cfg, notes = normalize_config(raw)
    if migrate and cfg != raw:
        backup = path.with_name(path.name + ".bak")
        # Never overwrite the original pre-upgrade backup.
        try:
            with backup.open("xb") as handle:
                handle.write(source)
        except FileExistsError:
            pass
        save_config(path, cfg)
    return cfg, notes
