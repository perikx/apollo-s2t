"""Public OpenRouter discovery; never sends credentials or recordings."""
import requests
import json
import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

# Reviewed against the live OpenRouter catalog and model pages on 2026-10-04.
# These are task-specific suggestions, not a benchmark ranking. Only models
# present in the compatible live catalog are ever promoted.
RECOMMENDATIONS = {
    "transcription": {
        "microsoft/mai-transcribe-2": "Hauptmodell · mehrsprachig, schnell und günstig",
        "qwen/qwen3-asr-1.7b": "Günstige mehrsprachige Alternative",
        "qwen/qwen3-asr-0.6b": "Besonders günstige Transkription",
        "mistralai/voxtral-mini-3b-2507": "Alternative von Mistral",
    },
    "text": {
        "qwen/qwen3.8-flash": "Aktuelle günstige Textbearbeitung",
        "z-ai/glm-5.3-flash": "Günstige Alternative von Z.ai",
        "qwen/qwen3.7-flash": "Besonders günstige Textbearbeitung",
        "deepseek/deepseek-v4-flash-20260731": "Alternative von DeepSeek",
        "google/gemini-3.5-flash-lite": "Geringe Startlatenz · höhere Ausgabekosten",
    },
}


def discover_catalog(kind, get=requests.get):
    if kind not in ("transcription", "text"):
        raise ValueError("Unsupported model type")
    response = get("https://openrouter.ai/api/v1/models",
                   params={"output_modalities": kind}, timeout=15)
    response.raise_for_status()
    payload = response.json()
    result = {}
    for model in payload.get("data", []):
        architecture = model.get("architecture") or {}
        outputs = architecture.get("output_modalities") or []
        inputs = architecture.get("input_modalities") or []
        # Audio-input chat models are not transcription-endpoint models.
        if kind not in outputs or (kind == "text" and "text" not in inputs):
            continue
        name = model.get("id")
        if isinstance(name, str) and name and len(name) < 250:
            result[name] = {"name": str(model.get("name") or name), "price":
                            token_price(model.get("pricing", {})) if kind == "text" else "Preis wird geladen …"}
    if not result:
        raise ValueError("No compatible models returned")
    return dict(sorted(result.items()))


def discover_models(kind, get=requests.get):
    return {key: value["name"] for key, value in discover_catalog(kind, get).items()}


def token_price(pricing):
    try:
        incoming = Decimal(pricing["prompt"]) * 1_000_000
        outgoing = Decimal(pricing["completion"]) * 1_000_000
        if not incoming.is_finite() or not outgoing.is_finite() or min(incoming, outgoing) < 0:
            raise ValueError()
        return f"${incoming:,.2f} Eingabe · ${outgoing:,.2f} Ausgabe / Mio. Tokens"
    except (KeyError, InvalidOperation, TypeError, ValueError):
        return "Preis nicht verfügbar"


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.parts = []
    def handle_starttag(self, tag, attrs):
        self.inside = tag == "script"
    def handle_endtag(self, tag):
        if tag == "script":
            self.inside = False
    def handle_data(self, data):
        if self.inside:
            self.parts.append(data)


def price_from_page(html):
    """Read explicit public price units; never assume audio prices are token prices.

    The Models API omits audio billing units. OpenRouter's public model page
    supplies display_pricing SKUs. Missing/changed markup means unavailable.
    """
    parser = _Scripts()
    parser.feed(html)
    for script in parser.parts:
        if not script.startswith("self.__next_f.push("):
            continue
        try:
            values = json.loads(script.removeprefix("self.__next_f.push(").rstrip(";")[:-1])
            payload = values[1]
            match = re.search(r'"display_pricing":(\[.*?\])', payload)
            if not match:
                continue
            prices = json.loads(match[1])
            labels = []
            for price in prices:
                value = Decimal(price["price"]) * Decimal(str(price.get("displayMultiplier", 1)))
                unit = price["unitLabel"]
                if not value.is_finite() or value < 0 or not isinstance(unit, str) or not unit:
                    return "Preis nicht verfügbar"
                unit = {"/hour": "/ Std. Audio", "/second": "/ Sek. Audio", "/M tokens": "/ Mio. Tokens"}.get(unit, unit)
                label = price.get("sku_label", "")
                number = f"{value:.6f}".rstrip("0").rstrip(".")
                labels.append(f"{label}: ${number} {unit}")
            if labels:
                return " · ".join(labels)
        except (ValueError, TypeError, IndexError, KeyError, InvalidOperation):
            continue
    return "Preis nicht verfügbar"


def discover_price(model, get=requests.get):
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]+/[A-Za-z0-9_.:-]+", model):
        raise ValueError("Invalid model ID")
    response = get("https://openrouter.ai/" + model, timeout=15)
    response.raise_for_status()
    return price_from_page(response.text)
