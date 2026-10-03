"""No network calls: exercise rate rejection recovery and its safety boundaries."""
import json
from email.utils import formatdate
from unittest.mock import Mock

import pytest
import requests

import apollo_api as api


def response(status, data=None, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data or {}).encode()
    result._content_consumed = True
    result.headers.update(headers or {})
    result.close = Mock()
    return result


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        elapsed = 0
        epoch = 1_800_000_000

        def wait(self, delay):
            self.elapsed += delay
            return False

    fake = Clock()
    monkeypatch.setattr(api.time, "monotonic", lambda: fake.elapsed)
    monkeypatch.setattr(api.time, "time", lambda: fake.epoch + fake.elapsed)
    monkeypatch.setattr(api.time, "sleep", fake.wait)
    monkeypatch.setattr(api.random, "uniform", lambda *_: 0.25)
    return fake


def test_rejected_audio_is_retried_unchanged_and_all_responses_closed(monkeypatch, clock):
    rejected = response(429, headers={"Retry-After": "3"})
    accepted = response(200, {"text": " recovered "})
    post = Mock(side_effect=[rejected, accepted])
    monkeypatch.setattr(api._http, "post", post)
    notify = Mock()
    assert api.transcribe_openrouter(b"original wav", {}, "key", on_retry=notify) == "recovered"
    notify.assert_called_once_with(2, 3)
    assert clock.elapsed == 3
    first, second = [call.kwargs["json"] for call in post.call_args_list]
    assert first["model"] == "microsoft/mai-transcribe-2"
    assert second == dict(first, model="microsoft/mai-transcribe-1.5")
    rejected.close.assert_called_once()
    accepted.close.assert_called_once()


def test_first_429_switches_immediately_and_next_recording_uses_primary(monkeypatch, clock):
    post = Mock(side_effect=[response(429), response(200, {"text": "fallback text"}),
                             response(200, {"text": "primary text"})])
    monkeypatch.setattr(api._http, "post", post)
    cfg = {"model": "microsoft/mai-transcribe-2", "language": "de", "timeout_seconds": 45}
    original = dict(cfg)
    notify = Mock()
    assert api.transcribe_openrouter(b"private wav", cfg, "key", on_fallback=notify) == "fallback text"
    assert clock.elapsed == 0
    notify.assert_called_once_with("microsoft/mai-transcribe-1.5")
    assert api.transcribe_openrouter(b"new wav", cfg, "key", on_fallback=notify) == "primary text"
    assert notify.call_count == 1
    assert cfg == original
    bodies = [call.kwargs["json"] for call in post.call_args_list]
    assert [body["model"] for body in bodies] == ["microsoft/mai-transcribe-2",
                                                "microsoft/mai-transcribe-1.5", "microsoft/mai-transcribe-2"]
    assert bodies[1] == dict(bodies[0], model="microsoft/mai-transcribe-1.5")
    assert post.call_args_list[0].args == post.call_args_list[1].args
    assert post.call_args_list[0].kwargs["headers"] == post.call_args_list[1].kwargs["headers"]
    assert post.call_args_list[1].kwargs["timeout"] == (5, 45)


def test_both_models_rate_limited_stay_bounded_without_switching_back(monkeypatch, clock):
    post = Mock(return_value=response(429))
    monkeypatch.setattr(api._http, "post", post)
    notify = Mock()
    with pytest.raises(requests.HTTPError):
        api.transcribe_openrouter(b"wav", {}, "key", on_fallback=notify)
    assert [call.kwargs["json"]["model"] for call in post.call_args_list] == [
        "microsoft/mai-transcribe-2", "microsoft/mai-transcribe-1.5", "microsoft/mai-transcribe-1.5"]
    assert clock.elapsed == 4.25
    notify.assert_called_once()


@pytest.mark.parametrize("model", ["custom/stt", "microsoft/mai-transcribe-1.5"])
def test_custom_or_old_primary_is_not_replaced(monkeypatch, clock, model):
    post = Mock(side_effect=[response(429), response(200, {"text": "ok"})])
    monkeypatch.setattr(api._http, "post", post)
    notify = Mock()
    assert api.transcribe_openrouter(b"wav", {"model": model}, "key", on_fallback=notify) == "ok"
    assert [call.kwargs["json"]["model"] for call in post.call_args_list] == [model, model]
    assert clock.elapsed == 2.25
    notify.assert_not_called()


def test_cancelled_immediate_fallback_never_sends_or_announces_it(monkeypatch, clock):
    post = Mock(return_value=response(429))
    monkeypatch.setattr(api._http, "post", post)
    notify = Mock()
    with pytest.raises(api.RetryCancelled):
        api.transcribe_openrouter(b"wav", {}, "key", on_fallback=notify, wait=lambda delay: True)
    post.assert_called_once()
    notify.assert_not_called()


def test_http_date_retry_after_is_honored(monkeypatch, clock):
    rejected = response(429, headers={"Retry-After": formatdate(clock.epoch + 12, usegmt=True)})
    post = Mock(side_effect=[rejected, response(200, {"text": "ok"})])
    monkeypatch.setattr(api._http, "post", post)
    assert api.transcribe_openrouter(b"wav", {}, "key") == "ok"
    assert clock.elapsed == 12


def test_http_date_cannot_retry_early_when_local_clock_is_ahead(monkeypatch, clock):
    rejected = response(429, headers={
        "Date": formatdate(clock.epoch - 60, usegmt=True),
        "Retry-After": formatdate(clock.epoch - 48, usegmt=True),
    })
    post = Mock(side_effect=[rejected, response(200, {"text": "ok"})])
    monkeypatch.setattr(api._http, "post", post)
    assert api.transcribe_openrouter(b"wav", {}, "key") == "ok"
    assert clock.elapsed == 12


@pytest.mark.parametrize("header", ["60", "999999999999999999999999999999", "date"])
def test_long_retry_after_is_not_shortened(monkeypatch, clock, header):
    if header == "date":
        header = formatdate(clock.epoch + 120, usegmt=True)
    post = Mock(return_value=response(429, headers={"Retry-After": header}))
    monkeypatch.setattr(api._http, "post", post)
    with pytest.raises(requests.HTTPError):
        api.transcribe_openrouter(b"wav", {}, "key")
    post.assert_called_once()
    assert clock.elapsed == 0


@pytest.mark.parametrize("header", [None, "nonsense", "-2", "NaN", "Infinity"])
def test_missing_or_invalid_delay_uses_bounded_backoff(monkeypatch, clock, header):
    post = Mock(return_value=response(429, headers={"Retry-After": header} if header else {}))
    monkeypatch.setattr(api._http, "post", post)
    notify = Mock()
    with pytest.raises(requests.HTTPError):
        api.transcribe_openrouter(b"wav", {"model": "custom/stt"}, "key", on_retry=notify)
    assert post.call_count == 3
    assert [call.args for call in notify.call_args_list] == [(2, 2.25), (3, 4.25)]
    assert clock.elapsed == 6.5


def test_network_time_counts_against_retry_start_window(monkeypatch, clock):
    def reject(*args, **kwargs):
        clock.elapsed += 29
        return response(429, headers={"Retry-After": "2"})

    post = Mock(side_effect=reject)
    monkeypatch.setattr(api._http, "post", post)
    with pytest.raises(requests.HTTPError):
        api.transcribe_openrouter(b"wav", {}, "key")
    post.assert_called_once()
    assert clock.elapsed == 29


def test_delayed_wakeup_cannot_send_past_retry_window(monkeypatch, clock):
    def delayed_wait(delay):
        clock.elapsed += 31

    post = Mock(return_value=response(429, headers={"Retry-After": "1"}))
    monkeypatch.setattr(api._http, "post", post)
    with pytest.raises(requests.HTTPError):
        api.transcribe_openrouter(b"wav", {}, "key", wait=delayed_wait)
    post.assert_called_once()


def test_wait_can_cancel_without_another_send(monkeypatch, clock):
    post = Mock(return_value=response(429, headers={"Retry-After": "2"}))
    monkeypatch.setattr(api._http, "post", post)
    with pytest.raises(api.RetryCancelled, match="saved audio"):
        api.transcribe_openrouter(b"wav", {}, "key", wait=lambda seconds: True)
    post.assert_called_once()


@pytest.mark.parametrize("status", [400, 401, 402, 403, 408, 500, 502, 503])
def test_other_http_errors_are_never_automatically_resent(monkeypatch, clock, status):
    post = Mock(return_value=response(status, headers={"Retry-After": "1"}))
    monkeypatch.setattr(api._http, "post", post)
    with pytest.raises(requests.HTTPError):
        api.transcribe_openrouter(b"wav", {}, "key")
    post.assert_called_once()


@pytest.mark.parametrize("failure", [requests.Timeout, requests.ConnectionError])
def test_ambiguous_error_after_rate_rejection_stops_retries(monkeypatch, clock, failure):
    post = Mock(side_effect=[response(429), failure()])
    monkeypatch.setattr(api._http, "post", post)
    with pytest.raises(failure):
        api.transcribe_openrouter(b"wav", {}, "key")
    assert post.call_count == 2


def test_error_inside_success_response_is_not_retried(monkeypatch, clock):
    post = Mock(return_value=response(200, {"error": {"code": 429}}))
    monkeypatch.setattr(api._http, "post", post)
    with pytest.raises(api.ResponseError):
        api.transcribe_openrouter(b"wav", {}, "key")
    post.assert_called_once()


def test_rewrite_rate_rejection_is_not_retried(monkeypatch, clock):
    post = Mock(return_value=response(429, headers={"Retry-After": "1"}))
    monkeypatch.setattr(api._http, "post", post)
    with pytest.raises(requests.HTTPError):
        api.smooth("dictation", "polish", {"api_key": "key"})
    post.assert_called_once()


def test_provider_hint_uses_fixed_labels_and_omits_sensitive_metadata(clock):
    rejected = response(429, {"error": {"message": "secret dictation", "metadata": {
        "provider_code": "private-key", "provider_name": "secret provider", "raw": "private-key",
        "remedy_hint": "print private-key", "limit_source": "private-key",
    }}}, {"Retry-After": "12", "X-Request-ID": "private-key"})
    hint = api.http_error_hint("Transcription", requests.HTTPError(response=rejected))
    assert "upstream provider" in hint and "12 seconds" in hint
    assert "private-key" not in hint and "secret" not in hint


def test_platform_rate_headers_identify_platform_without_echoing_values(clock):
    rejected = response(429, headers={name: "secret" for name in
                                    ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset")})
    hint = api.http_error_hint("STT", requests.HTTPError(response=rejected))
    assert "platform rate limit" in hint
    assert "secret" not in hint


def test_unknown_rate_limit_does_not_claim_a_specific_origin(clock):
    hint = api.http_error_hint("STT", requests.HTTPError(response=response(429)))
    assert "OpenRouter or its upstream provider" in hint


@pytest.mark.parametrize("source, expected", [
    ("openrouter_key_limit", "API-key spending limit"),
    ("openrouter_credits", "credits cannot cover"),
    ("openrouter_in_flight_budget", "in-flight spending budget"),
    ({"bad": "structure"}, "credit or spending limit"),
])
def test_credit_failures_have_actionable_fixed_hints(clock, source, expected):
    rejected = response(402, {"error": {"metadata": {"limit_source": source}}})
    assert expected in api.http_error_hint("STT", requests.HTTPError(response=rejected))


def test_connection_hint_explains_why_it_was_not_resent():
    hint = api.http_error_hint("STT", requests.Timeout("secret url/private-key"))
    assert "outcome is unknown" in hint and "Not automatically resent" in hint
    assert "secret" not in hint and "private-key" not in hint
