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
    assert kwargs["timeout"] == (5, 15)  # short audio: 15 s + half the audio length
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
    assert post.call_args.kwargs["timeout"] == (5, 45)
    api.transcribe_openrouter(b"not a wav", {}, "key")  # not a WAV: base timeout
    assert post.call_args.kwargs["timeout"] == (5, 15)


def test_retry_after_accepts_plain_numbers_only():
    def delay(value):
        return api._retry_after(Mock(headers={"Retry-After": value} if value else {}))
    assert delay("3") == 3 and delay(" 12 ") == 12
    assert delay(None) is None and delay("-1") is None and delay("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert delay("9" * 20) == float("inf")
