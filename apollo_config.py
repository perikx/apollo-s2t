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
    "ui_language": "en",
    "hotkeys": {"dictate": "f8", "polish": "f9", "prompt": "f10"},
    "hotkey_mode": "toggle",
    "openrouter_stt": {
        "model": DEFAULT_STT_MODEL,
        "fallback_model": "elevenlabs/scribe-v2",
        "language": "", "vocabulary": [],
    },
    "smoothing": {"api_key": "", "model": DEFAULT_SMOOTHING_MODEL},
    "prompt_profiles": {"active": "default", "output_language": "english"},
    "audio": {"device": None},
    "insertion": {"restore_clipboard": True},
    "beep": True,
    "overlay": {"visible": True, "x": None, "y": None},
    "recovery_cache": {"minutes": 15},
}


class ConfigError(ValueError):
    """An actionable configuration error, safe to display without exposing secrets."""


def default_config():
    return deepcopy(DEFAULTS)


def _merge(defaults, supplied, prefix=""):
    result = deepcopy(defaults)
    for key, value in supplied.items():
        if key not in defaults:
            continue  # removed or unknown keys are dropped on the next save
        if isinstance(defaults[key], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"{prefix}{key} must be a JSON object.")
            result[key] = _merge(defaults[key], value, prefix + key + ".")
        else:
            result[key] = deepcopy(value)
    return result


def normalize_config(raw):
    """Return (validated config, upgrade notes); never mutate the input.

    Keys that Apollo no longer uses are dropped. Custom models, keys, profiles and hotkeys survive.
    """
    if not isinstance(raw, dict):
        raise ConfigError("config.json must contain a JSON object.")
    version = raw.get("config_version", 1)
    if type(version) is not int or version not in (1, CONFIG_VERSION):
        raise ConfigError("Unsupported config_version. Update Apollo before using this config.")
    notes = []
    cfg = _merge(DEFAULTS, raw)
    stt = cfg["openrouter_stt"]
    if "fallback_model" not in raw.get("openrouter_stt", {}) and stt["model"] != DEFAULT_STT_MODEL:
        stt["fallback_model"] = None
    if stt["fallback_model"] == "microsoft/mai-transcribe-1.5" and stt["model"] == DEFAULT_STT_MODEL:
        stt["fallback_model"] = "elevenlabs/scribe-v2"
        notes.append("Changed the fallback model to ElevenLabs Scribe v2 (another vendor, fewer shared outages).")
    cfg["config_version"] = CONFIG_VERSION
    validate_config(cfg)
    return cfg, notes


def _number(value, label, low, high, integer=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high
            or (integer and type(value) is not int)):
        raise ConfigError(f"{label} must be {'an integer' if integer else 'a number'} between {low} and {high}.")


def validate_vocabulary(words):
    """Small explicit spelling list; no transcripts or automatic learning."""
    if not isinstance(words, list) or len(words) > 100:
        raise ConfigError("Use at most 100 vocabulary entries, one per line.")
    result = []
    for word in words:
        if not isinstance(word, str) or not 1 <= len(word.strip()) <= 100 or any(ord(c) < 32 for c in word):
            raise ConfigError("Vocabulary entries must contain 1–100 characters without control characters.")
        word = word.strip()
        if word not in result: result.append(word)
    return result


def validate_config(cfg):
    if cfg["ui_language"] not in ("en", "de", "zh"):
        raise ConfigError("ui_language must be en, de or zh.")
    stt = cfg["openrouter_stt"]
    stt["vocabulary"] = validate_vocabulary(stt.get("vocabulary", []))
    fallback = stt.get("fallback_model")
    if fallback is not None and (not isinstance(fallback, str) or not fallback.strip()):
        raise ConfigError("Choose a fallback model or disable fallback.")
    if fallback == stt["model"]:
        raise ConfigError("Primary and fallback models must be different.")
    cache = cfg["recovery_cache"]
    _number(cache["minutes"], "recovery_cache.minutes", 5, 60, True)
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
        raise ConfigError("Choose three different recording keys.")
    for name in keys:
        if isinstance(keys[name], str):
            keys[name] = keys[name].strip().lower()
    if cfg["hotkey_mode"] not in ("toggle", "hold"):
        raise ConfigError("hotkey_mode must be toggle or hold.")
    for section in ("openrouter_stt", "smoothing"):
        value = cfg[section]["model"]
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{section}.model must be a non-empty string.")
    if not isinstance(cfg["smoothing"]["api_key"], str):
        raise ConfigError("smoothing.api_key must be a string.")
    if not isinstance(cfg["openrouter_stt"]["language"], str):
        raise ConfigError("openrouter_stt.language must be a string or empty for auto-detection.")
    language = cfg["openrouter_stt"]["language"].strip().lower()
    cfg["openrouter_stt"]["language"] = "" if language in ("auto", "multi") else language
    for section, field in ((cfg, "beep"), (cfg["insertion"], "restore_clipboard")):
        if type(section[field]) is not bool:
            raise ConfigError(f"{field} must be true or false, not a string.")
    for field in ("active", "output_language"):
        if not isinstance(cfg["prompt_profiles"][field], str):
            raise ConfigError(f"prompt_profiles.{field} must be a string.")
    profile = cfg["prompt_profiles"]["active"]
    if "/" in profile or "\\" in profile or profile in (".", ".."):
        raise ConfigError("prompt_profiles.active must be a profile name, not a path.")


def api_key(cfg):
    # The key saved in Settings wins; the environment is only a fallback for an empty config.
    key = (cfg["smoothing"].get("api_key", "").strip() or os.environ.get("OPENROUTER_API_KEY", "")).strip()
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


def backup_config(path, source):
    """Keep the first copy of the old file; never overwrite it."""
    try:
        with path.with_name(path.name + ".bak").open("xb") as handle:
            handle.write(source)
    except FileExistsError:
        pass


def scan_codes_of(keys, to_scan_codes):
    """Return the scan codes of each key. Raise ConfigError for an unknown or shared key."""
    seen, result = set(), []
    for key in keys:
        codes = set(to_scan_codes(key))
        if not codes:
            raise ConfigError("Key not recognized. Use for example F8, F9, F10 or a letter.")
        if codes & seen:
            raise ConfigError("These keys are identical on your keyboard. Choose three different keys.")
        seen |= codes
        result.append(codes)
    return result


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
        backup_config(path, source)
        save_config(path, cfg)
    return cfg, notes
