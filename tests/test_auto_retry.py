"""Failed dictations are sent again by themselves, without opening Recovery."""
from array import array
from copy import deepcopy
import time

import requests

import apollo


def capture(app, mode="dictate"):
    return mode, deepcopy(app.cfg), apollo.PROMPTS.get(mode, "")


def failed_dictation(app, monkeypatch, failure, t0=None):
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(apollo, "transcribe_openrouter", fail)
    pcm = array("h", range(8000)).tobytes()
    backup = app.recovery.create(16000, 1, "dictate")
    backup.append(pcm)
    backup.finish()
    app._process(capture(app), pcm, time.monotonic() if t0 is None else t0, backup)
    return backup


def retries(desktop):
    return [timer for timer in desktop.timers if timer.function.__name__ == "_auto_retry"]


def http_error(code):
    response = requests.Response()
    response.status_code = code
    return requests.HTTPError(response=response)


def test_network_failure_is_retried_and_pasted_without_recovery(app, monkeypatch, desktop):
    notices = []
    monkeypatch.setattr(app, "notify", notices.append)
    backup = failed_dictation(app, monkeypatch, requests.ConnectionError())
    assert backup.metadata["state"] == "failed"
    assert [timer.interval for timer in retries(desktop)] == [5]
    assert notices == ["Transcription failed. Audio saved; retrying automatically in 5 seconds."]
    sent_audio = []
    def succeed(wav, *args, **kwargs):
        sent_audio.append(wav)
        return "back online"
    monkeypatch.setattr(apollo, "transcribe_openrouter", succeed)
    retries(desktop)[0].fire()
    app._join_jobs()
    assert sent_audio == [backup.path.read_bytes()]
    assert desktop.sent == ["ctrl+v"]  # a quick retry pastes like a normal dictation
    assert backup.read_transcript() == "back online"
    assert backup.metadata["state"] == "ready" and backup.metadata["error"] == ""
    assert not app._retry_timers


def test_late_retry_copies_instead_of_pasting_into_another_field(app, monkeypatch, desktop):
    t0 = time.monotonic() - apollo.AUTO_RETRY_PASTE_SECONDS - 1
    backup = failed_dictation(app, monkeypatch, requests.Timeout(), t0=t0)
    monkeypatch.setattr(apollo, "transcribe_openrouter", lambda *args, **kwargs: "late text")
    retries(desktop)[0].fire()
    app._join_jobs()
    assert desktop.sent == []
    assert desktop.clip.text == "late text"
    assert backup.metadata["state"] == "ready"


def test_retries_back_off_and_stop_after_the_window(app, monkeypatch, desktop):
    notices = []
    monkeypatch.setattr(app, "notify", notices.append)
    failed_dictation(app, monkeypatch, http_error(503))
    for _ in range(6):
        retries(desktop)[-1].fire()
        app._join_jobs()
    assert [timer.interval for timer in retries(desktop)] == [5, 15, 30, 60, 120, 120, 120]
    # Near the end of the window there is no further timer and Recovery takes over.
    monkeypatch.setattr(apollo, "AUTO_RETRY_WINDOW_SECONDS", 0)
    retries(desktop)[-1].fire()
    app._join_jobs()
    assert len(retries(desktop)) == 7
    assert notices[-1] == "Transcription failed. Audio saved. Retry in Recovery."
    assert not app._retry_timers


def test_incomplete_response_is_retried(app, monkeypatch, desktop):
    failed_dictation(app, monkeypatch, apollo.ResponseError("cut off"))
    assert [timer.interval for timer in retries(desktop)] == [5]


def test_errors_that_do_not_pass_by_themselves_are_not_retried(app, monkeypatch, desktop):
    for failure in (http_error(401), http_error(402), http_error(400)):
        failed_dictation(app, monkeypatch, failure)
    assert retries(desktop) == []
    assert not app._retry_timers


def test_only_the_first_failure_plays_the_error_tone(app, monkeypatch, desktop):
    tones = []
    monkeypatch.setattr(apollo, "beep", lambda kind, enabled: tones.append(kind))
    failed_dictation(app, monkeypatch, requests.ConnectionError())
    retries(desktop)[0].fire()
    app._join_jobs()
    assert tones == ["error"]


def test_close_cancels_pending_retry(app, monkeypatch, desktop):
    failed_dictation(app, monkeypatch, requests.ConnectionError())
    calls = []
    monkeypatch.setattr(apollo, "transcribe_openrouter", lambda *args, **kwargs: calls.append(1) or "text")
    app.close()
    timer = retries(desktop)[0]
    assert timer.cancelled
    timer.fire(even_if_cancelled=True)  # a timer that already started must not send after close
    app._join_jobs()
    assert calls == []


def test_explicit_recovery_or_delete_replaces_the_automatic_retry(app, monkeypatch, desktop):
    first = failed_dictation(app, monkeypatch, requests.ConnectionError())
    second = failed_dictation(app, monkeypatch, requests.ConnectionError())
    calls = []
    monkeypatch.setattr(apollo, "transcribe_openrouter", lambda *args, **kwargs: calls.append(1) or "text")
    app.recover(first.id)
    app._join_jobs()
    app.delete_recovery(second.id)
    assert all(timer.cancelled for timer in retries(desktop))
    for timer in retries(desktop):
        timer.fire(even_if_cancelled=True)  # late timers find nothing left to do
    app._join_jobs()
    assert calls == [1]
    assert first.metadata["state"] == "ready"


def test_waiting_dictation_survives_cache_cleanup(app, monkeypatch, desktop):
    backup = failed_dictation(app, monkeypatch, requests.ConnectionError())
    app.cfg["recovery_cache"]["minutes"] = 0
    app.prune_recovery()
    assert backup.path.exists()
