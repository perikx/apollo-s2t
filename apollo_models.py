"""Public OpenRouter catalog, curated model ranking and local model stats.

Never sends credentials or recordings. Prices are numbers; price_text formats them.
"""
import json
import os
import statistics
import threading
from decimal import Decimal, InvalidOperation

import requests

from apollo_i18n import t

# Speech models, best first. Artificial Analysis STT leaderboard, reviewed 2026-10-08.
# Scores are English-only benchmarks. Row: tier, WER % (lower is better),
# speed (audio seconds per processing second), USD per 1000 audio minutes, reason.
# A live model missing here is "unrated" and is shown only under "Show all".
STT = {
    "microsoft/mai-transcribe-2": ("best", 2.0, 337, 1.67, "Most accurate and fastest; can be rate limited"),
    "elevenlabs/scribe-v2": ("best", 2.2, 82, 3.67, "Top accuracy, different vendor: best fallback"),
    "google/gemini-3.5-transcribe": ("best", 2.6, 91, 5.00, "Accurate, different vendor"),
    "microsoft/mai-transcribe-1.5": ("good", 2.4, 84, 6.00, "Accurate; same vendor as MAI 2"),
    "mistralai/voxtral-small-24b-2507-stt": ("good", 2.8, 66, 4.00, "Accurate, slower"),
    "openai/gpt-transcribe": ("ok", 3.3, 52, 4.50, ""),
    "x-ai/grok-stt-1.0": ("ok", 4.0, 170, 1.67, "Fast and cheap, more errors"),
    "openai/gpt-4o-transcribe": ("ok", 4.0, 36, 6.00, ""),
    "openai/gpt-4o-mini-transcribe": ("weak", 4.5, 50, 3.00, ""),
    "nvidia/parakeet-tdt-0.6b-v3": ("weak", 4.5, 417, 1.50, ""),
    "openai/whisper-large-v3-turbo": ("weak", 4.6, 121, 0.67, ""),
    "deepgram/nova-3": ("weak", 5.2, 662, 4.30, ""),
    "google/chirp-3": ("avoid", 4.3, 30, 16.00, "Slow and expensive"),
    "openai/whisper-1": ("avoid", 4.1, 28, 6.00, "Old and slow"),
}
# Rewrite models, best first. Prices come from the catalog.
TEXT = {
    "qwen/qwen3.8-flash": "Current inexpensive text editing",
    "z-ai/glm-5.3-flash": "Inexpensive alternative from Z.ai",
    "qwen/qwen3.7-flash": "Very inexpensive text editing",
    "google/gemini-3.5-flash-lite": "Low initial latency · higher output cost",
}


BADGES = {"best": "Best", "good": "Good", "ok": "OK", "weak": "Weak", "avoid": "Avoid"}


def curated(kind):
    """Ranked {model: (tier, reason)}; the order is the rank."""
    if kind == "text":
        return {model: ("", reason) for model, reason in TEXT.items()}
    return {model: (row[0], row[4]) for model, row in STT.items()}


def discover_catalog(kind, get=requests.get):
    """One catalog request. Price is ("audio", USD per 1000 min), ("tokens", in, out) or None."""
    if kind not in ("transcription", "text"):
        raise ValueError("Unsupported model type")
    response = get("https://openrouter.ai/api/v1/models",
                   params={"output_modalities": kind}, timeout=15)
    response.raise_for_status()
    result = {}
    for model in response.json().get("data", []):
        architecture = model.get("architecture") or {}
        outputs = architecture.get("output_modalities") or []
        inputs = architecture.get("input_modalities") or []
        # Audio-input chat models are not transcription-endpoint models.
        if kind not in outputs or (kind == "text" and "text" not in inputs):
            continue
        name = model.get("id")
        if isinstance(name, str) and name and len(name) < 250:
            result[name] = {"name": str(model.get("name") or name), "price":
                            token_price(model.get("pricing", {})) if kind == "text" else audio_price(name)}
    if not result:
        raise ValueError("No compatible models returned")
    return dict(sorted(result.items()))


def audio_price(model):
    return ("audio", STT[model][3]) if model in STT else None


def token_price(pricing):
    try:
        incoming = Decimal(pricing["prompt"]) * 1_000_000
        outgoing = Decimal(pricing["completion"]) * 1_000_000
        if not incoming.is_finite() or not outgoing.is_finite() or min(incoming, outgoing) < 0:
            raise ValueError()
        return ("tokens", float(incoming), float(outgoing))
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return None


def price_text(price):
    if not price: return ""
    if price[0] == "audio": return t("${price:.2f} / 1000 min").format(price=price[1])
    return t("${incoming:.2f} in · ${outgoing:.2f} out / 1M tokens").format(incoming=price[1], outgoing=price[2])


def model_note(model, ranked, stat):
    """Badge, your own results and the reason, as one secondary line."""
    tier, reason = ranked.get(model, ("", ""))
    parts = [t(BADGES[tier]) if tier else ""]
    if stat and stat["ok"]+stat["fail"] >= 5:
        parts.append(t("You: {percent}% ok").format(percent=round(100*stat["ok"]/(stat["ok"]+stat["fail"])))
                     + (f' · {stat["median_s"]:.1f} s' if stat["median_s"] is not None else ""))
    parts.append(t(reason))
    return " · ".join(filter(None, parts))


# Local per-model stats. Only counts and latencies; never transcript text.
_lock = threading.Lock()


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record_attempt(path, model, ok, seconds):
    """Count one transcription attempt. Never raises: stats must not break dictation."""
    try:
        with _lock:
            data = _read(path)
            entry = data.get(model)
            if not isinstance(entry, dict):
                entry = data[model] = {}
            entry["ok" if ok else "fail"] = int(entry.get("ok" if ok else "fail", 0)) + 1
            if ok:  # latency of failures says nothing about speed
                entry["seconds"] = (list(entry.get("seconds", [])) + [round(float(seconds), 2)])[-50:]
            temporary = str(path) + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            os.replace(temporary, path)
    except (OSError, ValueError, TypeError):
        pass


def load_stats(path):
    """{model: {"ok": n, "fail": n, "median_s": seconds or None}}; {} if missing or broken."""
    stats = {}
    for model, entry in _read(path).items():
        try:
            seconds = [float(s) for s in entry.get("seconds", [])]
            stats[model] = {"ok": int(entry.get("ok", 0)), "fail": int(entry.get("fail", 0)),
                            "median_s": statistics.median(seconds) if seconds else None}
        except (AttributeError, TypeError, ValueError):
            continue
    return stats
