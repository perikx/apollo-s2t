import base64
import io
import wave
from unittest.mock import Mock
import pytest
import requests
import apollo_api as api
from apollo_config import DEFAULT_STT_MODEL, DEFAULT_SMOOTHING_MODEL, default_config


SMOOTHING = dict(default_config()["smoothing"], api_key="key")


@pytest.fixture(autouse=True)
def fresh_breaker(monkeypatch):
    monkeypatch.setattr(api, "_primary_down_until", 0.0)


def respond(monkeypatch, data):
    response = Mock()
    response.json.return_value = data
    post = Mock(return_value=response)
    monkeypatch.setattr(api._http, "post", post)
    return post, response


def test_transcription_uses_documented_json_and_auto_language(monkeypatch):
    post, response = respond(monkeypatch, {"text": " Hallo world. "})
    assert api.transcribe_openrouter(b"wave", {}, "test-key") == "Hallo world."
    kwargs = post.call_args.kwargs
    body = kwargs["json"]
    assert body == {"model": DEFAULT_STT_MODEL,
                    "input_audio": {"data": base64.b64encode(b"wave").decode(), "format": "wav"}}
    assert kwargs["timeout"] == (15, 15)  # short audio: 15 s + half the audio length
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"
    response.close.assert_called_once()


def test_explicit_language_and_model_passed(monkeypatch):
    post, _ = respond(monkeypatch, {"text": "test"})
    api.transcribe_openrouter(b"wav", {"model": "custom/stt", "language": "de"}, "key")
    assert post.call_args.kwargs["json"]["language"] == "de"
    assert post.call_args.kwargs["json"]["model"] == "custom/stt"


@pytest.mark.parametrize("data", [{}, {"text": None}, {"text": 42}, {"error": "private payload"}, []])
def test_invalid_stt_response_rejected(monkeypatch, data):
    respond(monkeypatch, data)
    with pytest.raises(api.ResponseError):
        api.transcribe_openrouter(b"wav", {}, "key")


def test_empty_transcript_is_legitimate_silence(monkeypatch):
    respond(monkeypatch, {"text": " "})
    assert api.transcribe_openrouter(b"wav", {}, "key") == ""


def test_rewrite_settings_are_bounded_and_latency_oriented(monkeypatch):
    post, _ = respond(monkeypatch, {"choices": [{"finish_reason": "stop", "message": {"content": " Edited. "}}]})
    assert api.smooth("raw", "instructions", SMOOTHING) == "Edited."
    body = post.call_args.kwargs["json"]
    assert body["model"] == DEFAULT_SMOOTHING_MODEL
    assert body["reasoning"] == {"effort": "minimal", "exclude": True}
    assert body["provider"]["sort"] == "latency"
    assert body["max_tokens"] == 4096
    assert post.call_args.kwargs["timeout"] == (5, 20)


@pytest.mark.parametrize("choice", [
    {"finish_reason": "length", "message": {"content": "truncated"}},
    {"finish_reason": "content_filter", "message": {"content": "filtered"}},
    {"message": {"content": None}}, {"message": {"content": "no", "refusal": "refused"}},
    {}, None,
])
def test_incomplete_rewrite_rejected(monkeypatch, choice):
    respond(monkeypatch, {"choices": [choice]})
    with pytest.raises(api.ResponseError):
        api.smooth("raw", "instructions", SMOOTHING)


def test_timeout_without_fallback_is_not_retried(monkeypatch):
    post = Mock(side_effect=requests.Timeout())
    monkeypatch.setattr(api._http, "post", post)
    with pytest.raises(requests.Timeout):
        api.transcribe_openrouter(b"wav", {}, "key")
    post.assert_called_once()


def test_client_error_does_not_retry_and_closes_response(monkeypatch):
    post, response = respond(monkeypatch, {})
    response.status_code = 400
    response.raise_for_status.side_effect = requests.HTTPError(response=response)
    with pytest.raises(requests.HTTPError):
        api.transcribe_openrouter(b"wav", {}, "key")
    post.assert_called_once()
    response.close.assert_called_once()


def test_http_hint_does_not_disclose_response_body():
    response = requests.Response()
    response.status_code, response._content = 500, b"secret transcript and private-key"
    hint = api.http_error_hint("STT", requests.HTTPError(response=response))
    assert "500" in hint and "secret" not in hint and "private-key" not in hint


def test_missing_key_fails_before_network(monkeypatch):
    post, _ = respond(monkeypatch, {})
    with pytest.raises(api.ResponseError):
        api.transcribe_openrouter(b"wav", {}, "")
    post.assert_not_called()


def test_stt_read_timeout_scales_with_audio_length(monkeypatch):
    post, _ = respond(monkeypatch, {"text": "x"})
    seconds = 60
    wav = io.BytesIO()
    with wave.open(wav, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(bytes(16000 * 2 * seconds))
    api.transcribe_openrouter(wav.getvalue(), {}, "key")
    upload, read = post.call_args.kwargs["timeout"]
    assert upload == pytest.approx(len(base64.b64encode(wav.getvalue())) / (32 * 1024))
    assert read == 45
    api.transcribe_openrouter(b"not a wav", {}, "key")  # not a WAV: base timeout
    assert post.call_args.kwargs["timeout"] == (15, 15)


def test_long_audio_preserves_every_sample_and_returns_no_partial_result(monkeypatch):
    pcm = bytes(range(256)) * 9000
    source = io.BytesIO()
    with wave.open(source, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm)
    received = []
    def post(*args, **kwargs):
        with wave.open(io.BytesIO(base64.b64decode(kwargs["json"]["input_audio"]["data"]))) as w:
            assert w.getnframes() <= 60 * 16000
            received.append(w.readframes(w.getnframes()))
        return Mock(json=lambda: {"text": str(len(received))})
    monkeypatch.setattr(api._http, "post", post)
    assert api.transcribe_openrouter(source.getvalue(), {}, "key") == "1\n\n2"
    assert b"".join(received) == pcm
    def fail(*args, **kwargs):
        if len(received) > 2: raise requests.Timeout()
        return post(*args, **kwargs)
    monkeypatch.setattr(api._http, "post", fail)
    with pytest.raises(requests.Timeout):
        api.transcribe_openrouter(source.getvalue(), {}, "key")


def test_upload_timeout_is_bounded_and_shared_by_both_models(monkeypatch):
    timeouts = []
    def post(*args, **kwargs):
        timeouts.append(kwargs["timeout"])
        return Mock(json=lambda: {"text": "recovered"})
    monkeypatch.setattr(api._http, "post", post)
    monkeypatch.setattr(api._hedge_http, "post", post)
    api.transcribe_openrouter(bytes(24 * 1024 * 1024), {"fallback_model": "other/stt"}, "key")
    # Both requests use the shared upload limit, including the fallback.
    import time
    deadline = time.monotonic() + 2
    while len(timeouts) < 2 and time.monotonic() < deadline:
        time.sleep(.01)
    assert timeouts == [(180, 15), (180, 15)]


def test_retry_after_accepts_plain_numbers_only():
    def delay(value):
        return api._retry_after(Mock(headers={"Retry-After": value} if value else {}))
    assert delay("3") == 3 and delay(" 12 ") == 12
    assert delay(None) is None and delay("-1") is None and delay("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert delay("9" * 20) == float("inf")


class _KeyResponse:
    def __init__(self, status, data=None):
        self.status_code, self.data = status, data
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)
    def json(self):
        return self.data
    def close(self):
        pass


@pytest.mark.parametrize("failure, message", [
    (requests.exceptions.SSLError("certificate verify failed"), api.TLS_BLOCKED),
    (requests.ConnectionError("no route"), api.NO_CONNECTION),
    (requests.Timeout("slow"), api.NO_CONNECTION),
])
def test_key_check_names_the_network_cause(failure, message):
    def get(*args, **kwargs):
        raise failure
    assert api.check_key("sk-or-v1-abc", get=get) == message


@pytest.mark.parametrize("key", ["sk-or-v1-abc​", "sk-or v1", "sk-or-ä", ""])
def test_key_with_invisible_or_foreign_characters_is_not_sent(key):
    def get(*args, **kwargs):
        raise AssertionError("an invalid key must not be sent")
    assert api.check_key(key, get=get) == api.KEY_INVALID


def test_key_check_results():
    assert api.check_key("k", get=lambda *a, **k: _KeyResponse(200, {"data": {}})) == ""
    assert api.check_key("k", get=lambda *a, **k: _KeyResponse(401)) == api.KEY_REJECTED
    assert api.check_key("k", get=lambda *a, **k: _KeyResponse(502)) == api.UNAVAILABLE


def test_windows_certificate_store_is_used():
    import ssl
    import truststore
    assert ssl.SSLContext is truststore.SSLContext
