"""Durable capture through API failure, restart, shutdown and clipboard failure."""
from array import array
from copy import deepcopy
import io
import logging
import threading
import time
import wave

import pytest
import requests

import apollo
from apollo_recovery import RecoveryError


def capture(app, mode="dictate"):
    return mode, deepcopy(app.cfg), apollo.PROMPTS.get(mode, "")


def process(app, pcm, mode="dictate"):
    backup = app.recovery.create(16000, 1, mode)
    backup.append(pcm)
    backup.finish()
    app._process(capture(app, mode), pcm, time.monotonic(), backup)


def pcm_of(value, frames):
    return (array("h", [value]) * frames).tobytes()


def input_stream(monkeypatch):
    class Stream:
        active = False
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
        def start(self):
            self.active = True
        def stop(self):
            self.active = False
        def close(self):
            self.active = False
    monkeypatch.setattr(apollo.sd, "RawInputStream", Stream, raising=False)


def read_pcm(path):
    with wave.open(str(path), "rb") as audio:
        return audio.readframes(audio.getnframes())


def test_three_minute_429_survives_restart_and_explicit_retry(app, monkeypatch, desktop, caplog):
    input_stream(monkeypatch)
    audio = pcm_of(321, 16000 * 180)
    posts = []
    import apollo_api
    def rejected(*args, **kwargs):
        posts.append(kwargs["json"])
        response = requests.Response()
        response.status_code = 429
        response.headers["Retry-After"] = "0"
        response._content = b'{"error":{"message":"private transcript test-key","metadata":{"provider_code":"429"}}}'
        response._content_consumed = True
        return response
    monkeypatch.setattr(apollo_api._http, "post", rejected)
    app.on_press("dictate")
    app.recorder._callback(audio, len(audio) // 2, None, None)
    with caplog.at_level(logging.INFO, logger="apollo"):
        app.on_release("dictate")
        app._join_jobs()
    assert len(posts) == 3
    assert [body["model"] for body in posts] == ["microsoft/mai-transcribe-2",
                                               "elevenlabs/scribe-v2", "elevenlabs/scribe-v2"]
    entry = app.recovery_items()[0]
    assert read_pcm(entry.path) == audio
    assert entry.metadata["state"] == "failed"
    assert "HTTP 429" in entry.metadata["error"] and "upstream provider" in entry.metadata["error"]
    assert "private transcript" not in entry.path.with_suffix(".json").read_text()
    assert "private transcript" not in caplog.text
    assert desktop.sent == []
    app.close()
    app._join_jobs(3)
    restarted = apollo.App(app.cfg)
    sent_audio = []
    def succeed(wav, *args, **kwargs):
        with wave.open(io.BytesIO(wav), "rb") as stream:
            sent_audio.append(stream.readframes(stream.getnframes()))
        return "recovered three minute dictation"
    monkeypatch.setattr(apollo, "transcribe_openrouter", succeed)
    try:
        assert not restarted._threads  # Startup never resends private audio.
        assert "HTTP 429" in restarted.recovery_items()[0].metadata["error"]
        restarted.recover(entry.id)
        restarted._join_jobs()
        assert sent_audio == [audio]
        assert desktop.clip.text == "recovered three minute dictation"
        assert desktop.sent == []  # Explicit recovery copies, never auto-pastes.
        assert entry.read_transcript() == desktop.clip.text
        assert entry.metadata["state"] == "ready" and entry.metadata["error"] == ""
        assert "recovered three minute dictation" not in caplog.text
        assert "test-key" not in entry.path.with_suffix(".json").read_text()
    finally:
        restarted.close()
        restarted._join_jobs(3)


@pytest.mark.parametrize("failure", [requests.Timeout(), apollo.ResponseError("bad response")])
def test_failed_processing_keeps_exact_audio(app, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(apollo, "transcribe_openrouter", fail)
    pcm = array("h", range(8000)).tobytes()
    process(app, pcm)
    entry = app.recovery_items()[0]
    assert read_pcm(entry.path) == pcm
    assert entry.metadata["state"] == "failed"
    assert entry.metadata["error"]  # Safe cause is available inside recovery after restart.


def test_clipboard_failure_keeps_text_and_recovery_does_not_pay_again(app, monkeypatch, desktop):
    monkeypatch.setattr(apollo, "transcribe_openrouter", lambda *args, **kwargs: "raw result")
    monkeypatch.setattr(apollo, "smooth", lambda *args: "edited result")
    def fail(*args):
        raise OSError("clipboard locked")
    monkeypatch.setattr(app, "insert_text", fail)
    process(app, bytes(16000), "polish")
    entry = app.recovery_items()[0]
    assert entry.read_transcript() == "edited result"
    assert entry.path.with_suffix(".transcript.txt").read_text() == "raw result"
    monkeypatch.setattr(apollo, "transcribe_openrouter", fail)
    app.recover(entry.id)
    app._join_jobs()
    assert desktop.clip.text == "edited result"
    assert desktop.sent == []


def test_audio_checkpoint_exists_while_still_recording(app, monkeypatch):
    input_stream(monkeypatch)
    app.on_press("dictate")
    backup = app._backup
    saved = threading.Event()
    original = backup.append
    def append(data):
        original(data)
        saved.set()
    monkeypatch.setattr(backup, "append", append)
    audio = pcm_of(1, 16000)
    app.recorder._callback(audio, 16000, None, None)
    assert saved.wait(3)
    assert app.recording
    assert read_pcm(backup.path) == audio
    app.close()
    assert read_pcm(backup.path) == audio
    assert backup.metadata["state"] == "interrupted"
    assert "Apollo closed during recording" in backup.metadata["error"]


def test_quit_keeps_active_and_queued_audio_without_late_paste(app, monkeypatch, desktop):
    input_stream(monkeypatch)
    started, release = threading.Event(), threading.Event()
    def stt(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return "late result"
    monkeypatch.setattr(apollo, "transcribe_openrouter", stt)
    try:
        for index in range(3):
            app.on_press("dictate")
            app.recorder._callback(pcm_of(index + 1, 8000), 8000, None, None)
            if index < 2:
                app.on_release("dictate")
            if index == 0:
                assert started.wait(3)
        app.close()
    finally:
        release.set()
        app._join_jobs(5)
    assert len(app.recovery_items()) == 3
    assert sorted(read_pcm(entry.path)[0] for entry in app.recovery_items()) == [1, 2, 3]
    assert not app._threads
    assert desktop.sent == []


def test_failed_start_and_failed_cleanup_never_leak_slots(app, monkeypatch):
    input_stream(monkeypatch)
    def fail(*args, **kwargs):
        raise OSError("disk unavailable")
    def bad_metadata(*args, **kwargs):
        raise RecoveryError("metadata unavailable")
    original = app.recovery.create
    def create(*args, **kwargs):
        entry = original(*args, **kwargs)
        monkeypatch.setattr(entry, "finish", fail)
        monkeypatch.setattr(entry, "update", bad_metadata)
        return entry
    monkeypatch.setattr(app.recovery, "create", create)
    monkeypatch.setattr(app.recorder, "start", fail)
    for _ in range(5):
        app.on_press("dictate")
        assert not app.recording
        assert app._backup is None
    assert app._slots.acquire(blocking=False)
    app._slots.release()


def test_recovery_storage_failure_prevents_microphone_start(app, monkeypatch):
    starts = []
    def fail(*args):
        raise OSError("no space")
    monkeypatch.setattr(app.recovery, "create", fail)
    monkeypatch.setattr(app.recorder, "start", lambda *args, **kwargs: starts.append(True))
    app.on_press("dictate")
    assert not app.recording and not starts


def test_worker_completion_lists_saved_recording(app, monkeypatch):
    input_stream(monkeypatch)
    monkeypatch.setattr(apollo, "transcribe_openrouter", lambda *args, **kwargs: "saved")
    app.on_press("dictate")
    app.recorder._callback(pcm_of(1, 8000), 8000, None, None)
    assert app.recovery_items() == []
    app.on_release("dictate")
    app._join_jobs()
    assert len(app.recovery_items()) == 1


def test_duplicate_recovery_is_not_sent_twice(app, monkeypatch):
    entry = app.recovery.create(16000, 1, "dictate")
    entry.append(b"\x01\x00" * 8000)
    entry.finish()
    started, release = threading.Event(), threading.Event()
    calls = []
    def stt(*args, **kwargs):
        calls.append(True)
        started.set()
        assert release.wait(3)
        return "saved"
    monkeypatch.setattr(apollo, "transcribe_openrouter", stt)
    try:
        app.recover(entry.id)
        assert started.wait(3)
        app.recover(entry.id)
    finally:
        release.set()
        app._join_jobs()
    assert calls == [True]


def test_stale_capture_failure_does_not_stop_new_capture_or_notify(app, monkeypatch):
    input_stream(monkeypatch)
    notices = []
    monkeypatch.setattr(app, "notify", notices.append)
    app.on_press("dictate")
    old = app._capture
    app.on_release("dictate")
    app.on_press("dictate")
    app._capture_failed(old)
    assert app.recording and notices == []


def test_checkpoint_failure_repairs_full_audio_while_transcribing(app, monkeypatch):
    input_stream(monkeypatch)
    pcm = pcm_of(1, 8000)
    app.on_press("dictate")
    entry = app._backup
    app.recorder._callback(pcm, 8000, None, None)
    app.recorder.backup_failed = True
    monkeypatch.setattr(apollo, "transcribe_openrouter", lambda *args, **kwargs: "repaired")
    app.on_release("dictate")
    app._join_jobs()  # a job ends only after its audio is on disk
    assert read_pcm(entry.path) == pcm
    assert entry.read_transcript() == "repaired" and entry.metadata["state"] == "ready"


def test_failed_final_disk_repair_reports_incomplete_and_releases_slot(app, monkeypatch):
    input_stream(monkeypatch)
    notices = []
    monkeypatch.setattr(app, "notify", notices.append)
    app.on_press("dictate")
    entry = app._backup
    app.recorder._callback(pcm_of(1, 8000), 8000, None, None)
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(entry, "replace_audio", fail)
    monkeypatch.setattr(apollo, "transcribe_openrouter", lambda *args, **kwargs: "from memory")
    app.recorder.backup_failed = True
    app._capture_failed(app._capture)
    app._join_jobs()  # the text still arrives from the audio in memory
    assert not app.recording and not app._threads
    assert any("incomplete" in notice for notice in notices)
    assert app._slots.acquire(blocking=False)
    app._slots.release()


def test_five_minute_limit_preserves_all_samples_and_notifies(app, monkeypatch, desktop):
    input_stream(monkeypatch)
    notices = []
    monkeypatch.setattr(app, "notify", notices.append)
    monkeypatch.setattr(apollo, "transcribe_openrouter", lambda *args, **kwargs: "full recording")
    app.on_press("dictate")
    app.recorder._callback(pcm_of(1, 16000 * 301), 16000 * 301, None, None)
    desktop.timers[-1].fire()
    app._join_jobs()
    assert not app.recording
    assert len(read_pcm(app.recovery_items()[0].path)) == 16000 * 300 * 2
    assert any("time limit reached" in notice for notice in notices)
