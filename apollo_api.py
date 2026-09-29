"""OpenRouter-only STT and rewriting. No desktop dependencies or automatic POST retries."""
import base64
import requests
from apollo_config import DEFAULT_STT_MODEL, DEFAULT_SMOOTHING_MODEL

# A single processing worker uses this pool for both requests, reusing TLS connections.
_http = requests.Session()


class ResponseError(ValueError):
    """Malformed or incomplete output; callers must not paste it."""


def http_error_hint(service, exc):
    """Never log response bodies: they can echo dictation or credentials."""
    response = getattr(exc, "response", None)
    if response is None:
        return f"{service}: connection failed or timed out. Check your internet connection."
    code = response.status_code
    hints = {
        400: "Invalid request. Check the model, language and model options in config.json.",
        401: "API key rejected. Run setup.bat or check OPENROUTER_API_KEY.",
        402: "Not enough OpenRouter credit. Top up your account.",
        403: "Access denied. Check your key permissions and provider access.",
        404: "Model or endpoint not found. Check the model ID in config.json.",
        408: "Request timed out. Try a shorter recording.",
        413: "Recording too large. Try a shorter recording.",
        429: "Rate limit reached. Wait briefly before trying again.",
    }
    return f"{service}: HTTP {code}. " + hints.get(code, "Provider error. Try again later.")


def _post(url, key, body, timeout):
    if not key or key.startswith("YOUR_"):
        raise ResponseError("Missing OpenRouter API key. Run setup.bat.")
    response = _http.post(
        url, headers={"Authorization": f"Bearer {key}", "X-Title": "Apollo-s2t"},
        json=body, timeout=(5, timeout),
    )
    try:
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or "error" in data:
            raise ResponseError("OpenRouter returned an invalid response.")
        return data
    finally:
        response.close()


def transcribe_openrouter(wav_bytes, cfg, key):
    body = {
        "model": cfg.get("model", DEFAULT_STT_MODEL),
        "input_audio": {"data": base64.b64encode(wav_bytes).decode("ascii"), "format": "wav"},
    }
    if cfg.get("language"):
        body["language"] = cfg["language"]
    data = _post(cfg.get("base_url", "https://openrouter.ai/api/v1/audio/transcriptions"),
                 key, body, cfg.get("timeout_seconds", 60))
    text = data.get("text")
    if not isinstance(text, str):
        raise ResponseError("OpenRouter returned no valid transcript field.")
    return text.strip()


def smooth(text, system_prompt, cfg):
    body = {
        "model": cfg.get("model", DEFAULT_SMOOTHING_MODEL),
        "temperature": cfg.get("temperature", 0.2),
        "max_tokens": cfg.get("max_tokens", 8192),
        "provider": {"sort": "latency"},
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": text}],
    }
    effort = cfg.get("reasoning_effort", "minimal")
    if effort is not None:
        body["reasoning"] = {"effort": effort, "exclude": True}
    data = _post(cfg.get("base_url", "https://openrouter.ai/api/v1/chat/completions"),
                 cfg.get("api_key", ""), body, cfg.get("timeout_seconds", 20))
    try:
        choice = data["choices"][0]
        if choice.get("finish_reason") not in ("stop", None):
            raise ResponseError("Rewrite was truncated or blocked. Keeping the original transcript.")
        message = choice["message"]
        content = message.get("content")
        if message.get("refusal") or not isinstance(content, str):
            raise ResponseError("No valid rewrite. Keeping the original transcript.")
        return content.strip()
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ResponseError("Malformed rewrite response. Keeping the original transcript.") from exc
