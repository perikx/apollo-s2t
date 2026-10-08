"""OpenRouter STT and rewriting. STT retries 429, falls back to another model and hedges slow requests."""
import base64
import io
import math
import queue
import random
import threading
import time
import wave
import requests
from apollo_config import DEFAULT_STT_MODEL, validate_vocabulary

# A single processing worker uses this pool for both requests, reusing TLS connections.
_http = requests.Session()
STT_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
REWRITE_URL = "https://openrouter.ai/api/v1/chat/completions"
REWRITE_TIMEOUT_SECONDS = 20
REWRITE_MAX_TOKENS = 4096  # enough for any dictation; a large value can trigger 402 credit-limit errors
STT_MAX_ATTEMPTS = 3
STT_RETRY_WINDOW_SECONDS = 30
HEDGE_SECONDS = 3.0  # plus 0.1 s per second of audio
BREAKER_SECONDS = 300
_primary_down_until = 0.0  # time.monotonic() until which new recordings try the fallback first


def warm():
    """Open the TLS connection early; errors do not matter."""
    try:
        _http.head("https://openrouter.ai/api/v1/models", timeout=(3, 3)).close()
    except Exception:
        pass


def check_key(key):
    """Read-only authentication check; no model inference, audio or billing."""
    try:
        response = requests.get("https://openrouter.ai/api/v1/key", headers={"Authorization": "Bearer " + key}, timeout=(5, 15))
        try:
            if response.status_code in (401, 403):
                return "Der API-Schlüssel wurde abgelehnt. Bitte korrigieren und erneut versuchen."
            response.raise_for_status()
            data = response.json()
            if not isinstance(data.get("data"), dict): return "Schlüssel konnte nicht geprüft werden. Bitte erneut versuchen."
            return ""
        finally: response.close()
    except (requests.RequestException, ValueError):
        return "OpenRouter ist gerade nicht erreichbar. Bitte erneut versuchen."


class ResponseError(ValueError):
    """Malformed or incomplete output; callers must not paste it."""


class RetryCancelled(ResponseError):
    """The caller stopped waiting; no further request was sent."""


def _retry_after(response):
    """Numeric seconds only; inf if absurdly large, None if missing or not a plain number."""
    value = (response.headers.get("Retry-After") or "").strip()
    if not (value.isascii() and value.isdigit()):
        return None
    return float(value) if len(value) < 12 else math.inf


def _retryable(exc):
    code = getattr(getattr(exc, "response", None), "status_code", 0)
    return code == 429 or code >= 500 or isinstance(exc, (requests.Timeout, requests.ConnectionError))


def _audio_seconds(wav_bytes):
    """Length from the byte count and the WAV header; 0 if it is not a WAV."""
    try:
        with wave.open(io.BytesIO(wav_bytes)) as w:
            return max(0, len(wav_bytes) - 44) / (w.getframerate() * w.getnchannels() * w.getsampwidth())
    except (wave.Error, EOFError, ZeroDivisionError):
        return 0


def http_error_hint(service, exc):
    """Never log response bodies: they can echo dictation or credentials."""
    response = getattr(exc, "response", None)
    if response is None:
        return f"{service}: connection failed or timed out. Check your connection, then retry the saved audio."
    code = response.status_code
    hints = {
        400: "Invalid request. Check the model, language and model options in config.json.",
        401: "API key rejected. Run setup.bat or check OPENROUTER_API_KEY.",
        402: "OpenRouter credit or spending limit. Check your balance and API-key limit.",
        403: "Access denied. Check your key permissions and provider access.",
        404: "Model or endpoint not found. Check the model ID in config.json.",
        408: "Request timed out. Try a shorter recording.",
        413: "Recording too large. Try a shorter recording.",
        429: "OpenRouter or its upstream provider rejected the request due to a rate/capacity limit. Retry the saved recording later.",
    }
    return f"{service}: HTTP {code}. " + hints.get(code, "Provider error. Try again later.")


def _post(url, key, body, timeout, post=None):
    if not key:
        raise ResponseError("Missing OpenRouter API key. Run setup.bat.")
    response = (post or _http.post)(
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


def transcribe_openrouter(wav_bytes, cfg, key, *, on_retry=None, on_fallback=None, on_attempt=None, wait=None):
    """Transcribe with the primary model; fall back to another vendor when it fails.

    On the first request, 429, timeout, connection error and 5xx switch to the fallback
    at once. Later 429s retry the same model (Retry-After honored, 30 s window).
    A request with no answer after HEDGE_SECONDS also goes to the other model; the first
    transcript wins. After a primary failure new recordings try the fallback first for
    BREAKER_SECONDS. ``on_retry`` gets (next_attempt_number, delay_seconds);
    ``wait(seconds)`` can return True to cancel (e.g. threading.Event.wait).
    ``on_fallback`` gets the fallback model when a request to it starts.
    ``on_attempt`` gets (model, ok, seconds) for every finished request.
    """
    primary = cfg.get("model", DEFAULT_STT_MODEL)
    fallback = cfg.get("fallback_model")
    body = {"model": primary, "input_audio": {"data": base64.b64encode(wav_bytes).decode("ascii"), "format": "wav"}}
    if cfg.get("language"):
        body["language"] = cfg["language"]
    vocabulary = validate_vocabulary(cfg.get("vocabulary", []))
    seconds = _audio_seconds(wav_bytes)
    read_timeout = min(180, 15 + 0.5 * seconds)
    first, other = (fallback, primary) if fallback and time.monotonic() < _primary_down_until else (primary, fallback)

    def request(model, post=None):
        global _primary_down_until
        req = dict(body, model=model)
        # Only the documented MAI 2 Azure integration is enabled. Keep unsupported
        # models usable; never turn the list into transcript replacements.
        if vocabulary and model == "microsoft/mai-transcribe-2":
            req["provider"] = {"options": {"azure": {"phraseList": {"phrases": vocabulary}}}}
        t0, ok = time.monotonic(), False
        try:
            text = _post(STT_URL, key, req, read_timeout, post).get("text")
            if not isinstance(text, str):
                raise ResponseError("OpenRouter returned no valid transcript field.")
            ok = True
            return text.strip()
        except Exception as exc:
            if model == primary and _retryable(exc):
                _primary_down_until = time.monotonic() + BREAKER_SECONDS
            raise
        finally:
            if on_attempt is not None:
                try:
                    on_attempt(model, ok, time.monotonic() - t0)
                except Exception:
                    pass

    def announce(model):
        if model == fallback and on_fallback is not None:
            on_fallback(model)

    sent = [first]

    def first_request():
        announce(first)
        if not other:
            return request(first)
        results = queue.Queue()
        def go(model, post):
            try:
                results.put((model, request(model, post), None))
            except Exception as exc:
                results.put((model, None, exc))
        def spawn(model, post=None):
            threading.Thread(target=go, args=(model, post), daemon=True).start()
        spawn(first)
        try:
            item = results.get(timeout=HEDGE_SECONDS + 0.1 * seconds)
        except queue.Empty:
            # Hedge: requests.post uses its own session (a Session is not thread-safe).
            announce(other)
            sent.append(other)
            spawn(other, requests.post)
            item = results.get()
        errors = {}
        while True:
            model, text, exc = item
            if exc is None:
                return text  # a slower request keeps running in its daemon thread
            errors[model] = exc
            if len(errors) == len(sent):
                raise errors.get(primary, exc)
            item = results.get()

    model = first
    deadline = time.monotonic() + STT_RETRY_WINDOW_SECONDS
    for attempt in range(1, STT_MAX_ATTEMPTS + 1):
        try:
            return first_request() if attempt == 1 else request(model)
        except (requests.RequestException, ResponseError) as exc:
            nxt = other if attempt == 1 and other else model
            code = getattr(getattr(exc, "response", None), "status_code", 0)
            # A malformed reply is worth one try on the other model, never a same-model retry.
            switchable = _retryable(exc) or (isinstance(exc, ResponseError) and nxt != model)
            if (len(sent) > 1 or attempt == STT_MAX_ATTEMPTS or not switchable
                    or (nxt == model and code != 429)):
                raise
            delay = 0  # a different model does not owe the old one's Retry-After
            if nxt == model:
                delay = _retry_after(exc.response)
                if delay is None:
                    delay = 2 ** attempt + random.uniform(0, 0.5)
                # Never shorten a provider's Retry-After to fit our local budget.
                if time.monotonic() + delay > deadline:
                    raise
            if on_retry is not None:
                on_retry(attempt + 1, delay)
            if (wait or time.sleep)(delay):
                raise RetryCancelled("Transcription retry cancelled; saved audio can be retried later.") from None
            if nxt == model and time.monotonic() > deadline:
                raise
            if nxt != model:
                announce(nxt)
            model = nxt


def smooth(text, system_prompt, cfg):
    body = {
        "model": cfg["model"],
        "temperature": 0.2,
        "max_tokens": REWRITE_MAX_TOKENS,
        "provider": {"sort": "latency"},
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": text}],
        "reasoning": {"effort": "minimal", "exclude": True},
    }
    data = _post(REWRITE_URL, cfg["api_key"], body, REWRITE_TIMEOUT_SECONDS)
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
