"""OpenRouter STT and rewriting; only explicit STT rate rejections are retried."""
import base64
from datetime import timezone
from email.utils import parsedate_to_datetime
import math
import random
import time
import requests
from apollo_config import DEFAULT_STT_MODEL, DEFAULT_SMOOTHING_MODEL

# A single processing worker uses this pool for both requests, reusing TLS connections.
_http = requests.Session()
STT_MAX_ATTEMPTS = 3
STT_RETRY_WINDOW_SECONDS = 30
STT_RATE_LIMIT_FALLBACKS = {"microsoft/mai-transcribe-2": "microsoft/mai-transcribe-1.5"}


class ResponseError(ValueError):
    """Malformed or incomplete output; callers must not paste it."""


class RetryCancelled(ResponseError):
    """The caller stopped waiting; no further request was sent."""


def _retry_after(response):
    """Return a safe numeric delay, never an untrusted header string."""
    value = response.headers.get("Retry-After")
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.isascii() and value.isdigit():
        # Arbitrarily large valid delays must suppress retries, not overflow.
        return float(value) if len(value) < 12 else math.inf
    try:
        deadline = parsedate_to_datetime(value)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        delay = deadline.timestamp() - time.time()
        # A wrong local wall clock must not make an HTTP-date retry early.
        server_date = response.headers.get("Date")
        if isinstance(server_date, str):
            try:
                server_time = parsedate_to_datetime(server_date)
                if server_time.tzinfo is None:
                    server_time = server_time.replace(tzinfo=timezone.utc)
                delay = max(delay, deadline.timestamp() - server_time.timestamp())
            except (ValueError, TypeError, OverflowError):
                pass
        return max(0, delay)
    except (ValueError, TypeError, OverflowError):
        return None


def _error_metadata(response):
    """Inspect only structured metadata; callers never display its raw values."""
    try:
        data = response.json()
    except (ValueError, TypeError):
        return {}
    error = data.get("error") if isinstance(data, dict) else None
    metadata = error.get("metadata") if isinstance(error, dict) else None
    return metadata if isinstance(metadata, dict) else {}


def http_error_hint(service, exc):
    """Never log response bodies: they can echo dictation or credentials."""
    response = getattr(exc, "response", None)
    if response is None:
        return (f"{service}: connection failed or timed out; the server outcome is unknown. "
                "Not automatically resent. Check your connection before retrying saved audio.")
    code = response.status_code
    hints = {
        400: "Invalid request. Check the model, language and model options in config.json.",
        401: "API key rejected. Run setup.bat or check OPENROUTER_API_KEY.",
        402: "OpenRouter credit or spending limit. Check your balance and API-key limit.",
        403: "Access denied. Check your key permissions and provider access.",
        404: "Model or endpoint not found. Check the model ID in config.json.",
        408: "Request timed out. Try a shorter recording.",
        413: "Recording too large. Try a shorter recording.",
        429: "OpenRouter or its upstream provider rejected the request due to a rate/capacity limit.",
    }
    hint = hints.get(code, "Provider error. Try again later.")
    if code in (402, 429):
        metadata = _error_metadata(response)
        source = metadata.get("limit_source")
        if code == 402:
            hint = {
                "openrouter_key_limit": "OpenRouter API-key spending limit reached. Check your key's limit.",
                "openrouter_credits": "OpenRouter credits cannot cover this request. Check your balance and request size.",
                "openrouter_in_flight_budget": "OpenRouter in-flight spending budget is occupied. Retry after other requests settle.",
            }.get(source, hint) if isinstance(source, str) else hint
        elif all(isinstance(response.headers.get(name), str) for name in
                 ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset")):
            hint = "OpenRouter platform rate limit reached. Check your OpenRouter usage limits."
        elif metadata.get("provider_code") is not None or metadata.get("provider_name") is not None:
            hint = "The upstream provider reported a rate/capacity limit."
        delay = _retry_after(response)
        if delay is not None:
            hint += (f" Server requested at least {math.ceil(delay)} seconds before retry."
                     if math.isfinite(delay) else " Server requested a long wait before retry.")
        elif code == 429:
            hint += " Retry the saved recording later."
    return f"{service}: HTTP {code}. " + hint


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


def transcribe_openrouter(wav_bytes, cfg, key, *, on_retry=None, on_fallback=None, wait=None):
    """Retry only HTTP 429, at most twice, within a 30-second retry-start window.

    Each request still has its own configured network timeout. ``on_retry`` gets
    (next_attempt_number, delay_seconds); ``wait(seconds)`` can return True to
    cancel (e.g. threading.Event.wait). Timeouts/connection failures have an
    ambiguous server outcome and are deliberately never automatically resent.
    MAI-2's first 429 switches the next attempt to MAI-1.5 for this recording.
    ``on_fallback`` receives the replacement model just before that attempt.
    """
    body = {
        "model": cfg.get("model", DEFAULT_STT_MODEL),
        "input_audio": {"data": base64.b64encode(wav_bytes).decode("ascii"), "format": "wav"},
    }
    if cfg.get("language"):
        body["language"] = cfg["language"]
    deadline = time.monotonic() + STT_RETRY_WINDOW_SECONDS
    for attempt in range(1, STT_MAX_ATTEMPTS + 1):
        try:
            data = _post(cfg.get("base_url", "https://openrouter.ai/api/v1/audio/transcriptions"),
                         key, body, cfg.get("timeout_seconds", 60))
            break
        except requests.HTTPError as exc:
            response = exc.response
            if response is None or response.status_code != 429 or attempt == STT_MAX_ATTEMPTS:
                raise
            fallback_model = STT_RATE_LIMIT_FALLBACKS.get(body["model"])
            delay = _retry_after(response)
            if delay is None:
                delay = 0 if fallback_model else 2 ** attempt + random.uniform(0, 0.5)
            # Never shorten a provider's Retry-After to fit our local budget.
            if time.monotonic() + delay > deadline:
                raise
            if on_retry is not None:
                on_retry(attempt + 1, delay)
            if (wait or time.sleep)(delay):
                raise RetryCancelled("Transcription retry cancelled; saved audio can be retried later.") from None
            if time.monotonic() > deadline:
                raise
            if fallback_model:
                # Reuse the exact recording/options, without changing the configured
                # primary or mutating the body associated with the previous request.
                body = dict(body, model=fallback_model)
                if on_fallback is not None:
                    on_fallback(fallback_model)
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
