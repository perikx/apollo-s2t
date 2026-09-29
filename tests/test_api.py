import base64
from unittest.mock import Mock
import pytest
import requests
import apollo_api as api
from apollo_config import DEFAULT_STT_MODEL, DEFAULT_SMOOTHING_MODEL


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
    assert kwargs["timeout"] == (5, 60)
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
    assert api.smooth("raw", "instructions", {"api_key": "key"}) == "Edited."
    body = post.call_args.kwargs["json"]
    assert body["model"] == DEFAULT_SMOOTHING_MODEL
    assert body["reasoning"] == {"effort": "minimal", "exclude": True}
    assert body["provider"]["sort"] == "latency"
    assert body["max_tokens"] == 8192
    assert post.call_args.kwargs["timeout"] == (5, 20)


def test_reasoning_can_be_omitted_for_custom_models(monkeypatch):
    post, _ = respond(monkeypatch, {"choices": [{"message": {"content": "ok"}}]})
    api.smooth("raw", "instructions", {"api_key": "key", "reasoning_effort": None})
    assert "reasoning" not in post.call_args.kwargs["json"]


@pytest.mark.parametrize("choice", [
    {"finish_reason": "length", "message": {"content": "truncated"}},
    {"finish_reason": "content_filter", "message": {"content": "filtered"}},
    {"message": {"content": None}}, {"message": {"content": "no", "refusal": "refused"}},
    {}, None,
])
def test_incomplete_rewrite_rejected(monkeypatch, choice):
    respond(monkeypatch, {"choices": [choice]})
    with pytest.raises(api.ResponseError):
        api.smooth("raw", "instructions", {"api_key": "key"})


def test_timeout_does_not_retry_paid_post(monkeypatch):
    post = Mock(side_effect=requests.Timeout())
    monkeypatch.setattr(api._http, "post", post)
    with pytest.raises(requests.Timeout):
        api.transcribe_openrouter(b"wav", {}, "key")
    post.assert_called_once()


def test_http_error_does_not_retry_and_closes_response(monkeypatch):
    post, response = respond(monkeypatch, {})
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
