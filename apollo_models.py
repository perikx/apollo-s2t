"""Public OpenRouter discovery; never sends credentials or recordings."""
import requests


def discover_models(kind, get=requests.get):
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
            result[name] = str(model.get("name") or name)
    if not result:
        raise ValueError("No compatible models returned")
    return dict(sorted(result.items()))
