"""No network calls: exercise rate rejection recovery, fallback, circuit breaker and hedging."""
import json
import threading
from unittest.mock import Mock

import pytest
import requests

import apollo_api as api

PRIMARY, FALLBACK = "microsoft/mai-transcribe-2", "elevenlabs/scribe-v2"
CFG = {"model": PRIMARY, "fallback_model": FALLBACK}


def response(status, data=None, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data or {}).encode()
    result._content_consumed = True
    result.headers.update(headers or {})
    result.close = Mock()
    return result


def models(post):
    return [call.kwargs["json"]["model"] for call in post.call_args_list]


@pytest.fixture(autouse=True)
def fresh_breaker(monkeypatch):
    monkeypatch.setattr(api, "_primary_down_until", 0.0)


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        elapsed = 0

        def wait(self, delay):
            self.elapsed += delay
            return False

    fake = Clock()
    monkeypatch.setattr(api.time, "monotonic", lambda: fake.elapsed)
    monkeypatch.setattr(api.time, "sleep", fake.wait)
    monkeypatch.setattr(api.random, "uniform", lambda *_: 0.25)
    return fake


def test_429_switches_to_fallback_at_once_ignoring_old_retry_after(monkeypatch, clock):
    rejected = response(429, headers={"Retry-After": "3"})
    accepted = response(200, {"text": " recovered "})
    post = Mock(side_effect=[rejected, accepted])
    monkeypatch.setattr(api._http, "post", post)
    retry, fallback = Mock(), Mock()
    assert api.transcribe_openrouter(b"original wav", CFG, "key", on_retry=retry, on_fallback=fallback) == "recovered"
    retry.assert_called_once_with(2, 0)
    fallback.assert_called_once_with(FALLBACK)
    assert clock.elapsed == 0
    first, second = [call.kwargs["json"] for call in post.call_args_list]
    assert second == dict(first, model=FALLBACK)
    rejected.close.assert_called_once()
    accepted.close.assert_called_once()


def test_breaker_sends_next_recordings_to_fallback_first_for_five_minutes(monkeypatch, clock):
    post = Mock(side_effect=[response(429), response(200, {"text": "a"}), response(200, {"text": "b"}),
                             response(200, {"text": "c"})])
    monkeypatch.setattr(api._http, "post", post)
    notify = Mock()
    api.transcribe_openrouter(b"wav", CFG, "key")
    assert api.transcribe_openrouter(b"wav", CFG, "key", on_fallback=notify) == "b"
    notify.assert_called_once_with(FALLBACK)
    clock.elapsed = api.BREAKER_SECONDS + 1
    assert api.transcribe_openrouter(b"wav", CFG, "key") == "c"
    assert models(post) == [PRIMARY, FALLBACK, FALLBACK, PRIMARY]


def test_second_429_retries_same_model_with_backoff_then_stops(monkeypatch, clock):
    post = Mock(return_value=response(429))
    monkeypatch.setattr(api._http, "post", post)
    with pytest.raises(requests.HTTPError):
        api.transcribe_openrouter(b"wav", CFG, "key")
    assert models(post) == [PRIMARY, FALLBACK, FALLBACK]
    assert clock.elapsed == 4.25


def test_without_fallback_429_honors_retry_after(monkeypatch, clock):
    post = Mock(side_effect=[response(429, headers={"Retry-After": "3"}), response(200, {"text": "ok"})])
    monkeypatch.setattr(api._http, "post", post)
    assert api.transcribe_openrouter(b"wav", {"model": "custom/stt"}, "key") == "ok"
    assert models(post) == ["custom/stt"] * 2
    assert clock.elapsed == 3


@pytest.mark.parametrize("failure", [requests.Timeout(), requests.ConnectionError(), response(500), response(503)])
def test_timeout_connection_and_5xx_retry_once_on_fallback(monkeypatch, clock, failure):
    if isinstance(failure, requests.Response):
        failure = requests.HTTPError(response=failure)
    post = Mock(side_effect=[failure, response(200, {"text": "ok"})])
    monkeypatch.setattr(api._http, "post", post)
    assert api.transcribe_openrouter(b"wav", CFG, "key") == "ok"
    assert models(post) == [PRIMARY, FALLBACK]
    assert api._primary_down_until > 0


def test_failure_on_fallback_after_timeout_is_not_retried_again(monkeypatch, clock):
    post = Mock(side_effect=[requests.Timeout(), requests.Timeout(), response(200, {"text": "never"})])
    monkeypatch.setattr(api._http, "post", post)
    with pytest.raises(requests.Timeout):
        api.transcribe_openrouter(b"wav", CFG, "key")
    assert post.call_count == 2


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 408, 413])
def test_client_errors_are_never_resent(monkeypatch, clock, status):
    post = Mock(return_value=response(status, headers={"Retry-After": "1"}))
    monkeypatch.setattr(api._http, "post", post)
    with pytest.raises(requests.HTTPError):
        api.transcribe_openrouter(b"wav", CFG, "key")
    post.assert_called_once()


def test_cancelled_switch_never_sends_or_announces_fallback(monkeypatch, clock):
    post = Mock(return_value=response(429))
    monkeypatch.setattr(api._http, "post", post)
    notify = Mock()
    with pytest.raises(api.RetryCancelled, match="saved audio"):
        api.transcribe_openrouter(b"wav", CFG, "key", on_fallback=notify, wait=lambda delay: True)
    post.assert_called_once()
    notify.assert_not_called()


@pytest.mark.parametrize("header", ["60", "999999999999999999999999999999"])
def test_long_retry_after_is_not_shortened(monkeypatch, clock, header):
    post = Mock(return_value=response(429, headers={"Retry-After": header}))
    monkeypatch.setattr(api._http, "post", post)
    with pytest.raises(requests.HTTPError):
        api.transcribe_openrouter(b"wav", {"model": "custom/stt"}, "key")
    post.assert_called_once()
    assert clock.elapsed == 0


@pytest.mark.parametrize("header", [None, "nonsense", "-2", "NaN", "Infinity", "Wed, 21 Oct 2026 07:28:00 GMT"])
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
        api.transcribe_openrouter(b"wav", {"model": "custom/stt"}, "key")
    post.assert_called_once()
    assert clock.elapsed == 29


def test_delayed_wakeup_cannot_send_past_retry_window(monkeypatch, clock):
    def delayed_wait(delay):
        clock.elapsed += 31

    post = Mock(return_value=response(429, headers={"Retry-After": "1"}))
    monkeypatch.setattr(api._http, "post", post)
    with pytest.raises(requests.HTTPError):
        api.transcribe_openrouter(b"wav", {"model": "custom/stt"}, "key", wait=delayed_wait)
    post.assert_called_once()


def test_slow_failure_still_falls_back_after_retry_window(monkeypatch, clock):
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs["json"]["model"])
        if len(calls) == 1:
            clock.elapsed += 60
            raise requests.Timeout()
        return response(200, {"text": "ok"})
    monkeypatch.setattr(api._http, "post", post)
    assert api.transcribe_openrouter(b"wav", CFG, "key") == "ok"
    assert calls == [PRIMARY, FALLBACK]


def test_error_inside_success_response_tries_the_fallback_once(monkeypatch, clock):
    post = Mock(return_value=response(200, {"error": {"code": 429}}))
    monkeypatch.setattr(api._http, "post", post)
    with pytest.raises(api.ResponseError):
        api.transcribe_openrouter(b"wav", CFG, "key")
    assert [c.kwargs["json"]["model"] for c in post.call_args_list] == [PRIMARY, FALLBACK]


def test_rewrite_rate_rejection_is_not_retried(monkeypatch, clock):
    post = Mock(return_value=response(429, headers={"Retry-After": "1"}))
    monkeypatch.setattr(api._http, "post", post)
    cfg = dict(api_key="key", model="m")
    with pytest.raises(requests.HTTPError):
        api.smooth("dictation", "polish", cfg)
    post.assert_called_once()


def test_on_attempt_reports_every_finished_request(monkeypatch, clock):
    monkeypatch.setattr(api._http, "post", Mock(side_effect=[response(429), response(200, {"text": "ok"})]))
    seen = Mock()
    api.transcribe_openrouter(b"wav", CFG, "key", on_attempt=seen)
    assert [call.args[:2] for call in seen.call_args_list] == [(PRIMARY, False), (FALLBACK, True)]


def test_hint_never_discloses_response_body_and_has_fixed_text():
    rejected = response(429, {"error": {"message": "secret dictation", "metadata": {"provider_code": "private-key"}}},
                        {"Retry-After": "12"})
    hint = api.http_error_hint("Transcription", requests.HTTPError(response=rejected))
    assert "HTTP 429" in hint and "rate/capacity" in hint
    assert "private-key" not in hint and "secret" not in hint


def test_connection_hint_hides_exception_text():
    hint = api.http_error_hint("STT", requests.Timeout("secret url/private-key"))
    assert "timed out" in hint
    assert "secret" not in hint and "private-key" not in hint


@pytest.fixture
def hedge(monkeypatch):
    monkeypatch.setattr(api, "HEDGE_SECONDS", 0.05)


def test_hedge_returns_fallback_transcript_without_waiting_for_slow_primary(monkeypatch, hedge):
    release = threading.Event()
    def slow(*args, **kwargs):
        release.wait(5)
        return response(200, {"text": "slow primary"})
    monkeypatch.setattr(api._http, "post", slow)
    hedged = Mock(return_value=response(200, {"text": "fast fallback"}))
    monkeypatch.setattr(api.requests, "post", hedged)
    notify = Mock()
    try:
        assert api.transcribe_openrouter(b"wav", CFG, "key", on_fallback=notify) == "fast fallback"
    finally:
        release.set()
    notify.assert_called_once_with(FALLBACK)
    assert hedged.call_args.kwargs["json"]["model"] == FALLBACK


def test_hedge_both_fail_raises_primary_error(monkeypatch, hedge):
    release = threading.Event()
    def slow(*args, **kwargs):
        release.wait(5)
        return response(500)
    def rejected(*args, **kwargs):
        release.set()
        return response(429)
    monkeypatch.setattr(api._http, "post", slow)
    monkeypatch.setattr(api.requests, "post", rejected)
    with pytest.raises(requests.HTTPError) as caught:
        api.transcribe_openrouter(b"wav", CFG, "key")
    assert caught.value.response.status_code == 500


def test_hedge_waits_for_primary_when_fallback_fails(monkeypatch, hedge):
    def slow(*args, **kwargs):
        threading.Event().wait(0.2)
        return response(200, {"text": "primary wins"})
    monkeypatch.setattr(api._http, "post", slow)
    monkeypatch.setattr(api.requests, "post", Mock(return_value=response(429)))
    assert api.transcribe_openrouter(b"wav", CFG, "key") == "primary wins"
