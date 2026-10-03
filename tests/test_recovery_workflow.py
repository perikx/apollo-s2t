"""Durable capture through API failure, restart, shutdown and clipboard failure."""
from copy import deepcopy
import io
import logging
import threading
import time
from types import SimpleNamespace
import wave

import numpy as np
import pytest
import requests

import apollo
from apollo_recovery import RecoveryError


def capture(app, mode="dictate"):
    return mode, "original-window", deepcopy(app.cfg), apollo.PROMPTS.get(mode, "")


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
    monkeypatch.setattr(apollo.sd, "InputStream", Stream, raising=False)


def read_pcm(path):
    with wave.open(str(path), "rb") as audio:
        return audio.readframes(audio.getnframes())


def test_three_minute_429_survives_restart_and_explicit_retry(app, monkeypatch, desktop, caplog):
    input_stream(monkeypatch)
    audio = np.full((16000 * 180, 1), 321, dtype=np.int16)
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
    app.recorder._callback(audio, len(audio), None, None)
    with caplog.at_level(logging.INFO, logger="apollo"):
        app.on_release("dictate")
        app._jobs.join()
    assert len(posts) == 3
    assert [body["model"] for body in posts] == ["microsoft/mai-transcribe-2",
                                               "microsoft/mai-transcribe-1.5", "microsoft/mai-transcribe-1.5"]
    entry = app.recovery_items()[0]
    assert read_pcm(entry.path) == audio.tobytes()
    assert entry.metadata["state"] == "failed"
    assert "HTTP 429" in entry.metadata["error"] and "upstream provider" in entry.metadata["error"]
    assert "private transcript" not in entry.path.with_suffix(".json").read_text()
    assert "private transcript" not in caplog.text
    assert desktop.sent == []
    app.close()
    app._worker.join(3)
    restarted = apollo.App(app.cfg)
    sent_audio = []
    def succeed(wav, *args, **kwargs):
        with wave.open(io.BytesIO(wav), "rb") as stream:
            sent_audio.append(stream.readframes(stream.getnframes()))
        return "recovered three minute dictation"
    monkeypatch.setattr(apollo, "transcribe_openrouter", succeed)
    try:
        assert restarted._worker is None  # Startup never resends private audio.
        assert "HTTP 429" in restarted.recovery_items()[0].metadata["error"]
        restarted.recover(entry.id)
        restarted._jobs.join()
        assert sent_audio == [audio.tobytes()]
        assert desktop.clip.text == "recovered three minute dictation"
        assert desktop.sent == []  # Explicit recovery copies, never auto-pastes.
        assert entry.read_transcript() == desktop.clip.text
        assert entry.metadata["state"] == "ready" and entry.metadata["error"] == ""
        assert "recovered three minute dictation" not in caplog.text
        assert "test-key" not in entry.path.with_suffix(".json").read_text()
    finally:
        restarted.close()
        restarted._worker.join(3)


@pytest.mark.parametrize("failure", [requests.Timeout(), apollo.ResponseError("bad response")])
def test_failed_processing_keeps_exact_audio(app, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(apollo, "transcribe_openrouter", fail)
    pcm = np.arange(8000, dtype=np.int16).reshape(-1, 1)
    app._process(capture(app), pcm, time.monotonic())
    entry = app.recovery_items()[0]
    assert read_pcm(entry.path) == pcm.tobytes()
    assert entry.metadata["state"] == "failed"
    assert entry.metadata["error"]  # Safe cause is available inside recovery after restart.


def test_clipboard_failure_keeps_text_and_recovery_does_not_pay_again(app, monkeypatch, desktop):
    monkeypatch.setattr(apollo, "transcribe_openrouter", lambda *args, **kwargs: "raw result")
    monkeypatch.setattr(apollo, "smooth", lambda *args: "edited result")
    def fail(*args):
        raise OSError("clipboard locked")
    monkeypatch.setattr(app, "insert_text", fail)
    app._process(capture(app, "polish"), np.zeros((8000, 1), dtype=np.int16), time.monotonic())
    entry = app.recovery_items()[0]
    assert entry.read_transcript() == "edited result"
    assert entry.path.with_suffix(".transcript.txt").read_text() == "raw result"
    monkeypatch.setattr(apollo, "transcribe_openrouter", fail)
    app.recover(entry.id)
    app._jobs.join()
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
    audio = np.ones((16000, 1), dtype=np.int16)
    app.recorder._callback(audio, len(audio), None, None)
    assert saved.wait(3)
    assert app.recording
    assert read_pcm(backup.path) == audio.tobytes()
    app.close()
    assert read_pcm(backup.path) == audio.tobytes()
    assert backup.metadata["state"] == "interrupted"
    assert "Programm während der Aufnahme geschlossen" in backup.metadata["error"]


def test_quit_keeps_active_and_queued_audio_without_late_paste(app, monkeypatch, desktop):
    input_stream(monkeypatch)
    started, release = threading.Event(), threading.Event()
    def stt(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return "late result"
    monkeypatch.setattr(apollo, "transcribe_openrouter", stt)
    pcm = np.ones((8000, 1), dtype=np.int16)
    try:
        for index in range(3):
            app.on_press("dictate")
            app.recorder._callback(pcm * (index + 1), len(pcm), None, None)
            if index < 2:
                app.on_release("dictate")
            if index == 0:
                assert started.wait(3)
        app.close()
    finally:
        release.set()
        app._worker.join(5)
    assert len(app.recovery_items()) == 3
    assert sorted(read_pcm(entry.path)[0] for entry in app.recovery_items()) == [1, 2, 3]
    assert app._jobs.unfinished_tasks == 0
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


def test_worker_completion_refreshes_native_recovery_menu(app, monkeypatch):
    updates = []
    app._tray = SimpleNamespace(update_menu=lambda: updates.append(True), notify=lambda *args: None)
    input_stream(monkeypatch)
    monkeypatch.setattr(apollo, "transcribe_openrouter", lambda *args, **kwargs: "saved")
    app.on_press("dictate")
    app.recorder._callback(np.ones((8000, 1), dtype=np.int16), 8000, None, None)
    assert app.recovery_items() == []
    app.on_release("dictate")
    app._jobs.join()
    assert updates and len(app.recovery_items()) == 1


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
        app._jobs.join()
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


def test_checkpoint_failure_repairs_full_audio_before_transcription(app, monkeypatch):
    input_stream(monkeypatch)
    pcm = np.ones((8000, 1), dtype=np.int16)
    app.on_press("dictate")
    entry = app._backup
    app.recorder._callback(pcm, len(pcm), None, None)
    app.recorder.backup_failed = True
    def stt(*args, **kwargs):
        assert read_pcm(entry.path) == pcm.tobytes()
        return "repaired"
    monkeypatch.setattr(apollo, "transcribe_openrouter", stt)
    app.on_release("dictate")
    app._jobs.join()
    assert entry.read_transcript() == "repaired"


def test_failed_final_disk_repair_reports_incomplete_and_releases_slot(app, monkeypatch):
    input_stream(monkeypatch)
    notices = []
    monkeypatch.setattr(app, "notify", notices.append)
    app.on_press("dictate")
    entry = app._backup
    app.recorder._callback(np.ones((8000, 1), dtype=np.int16), 8000, None, None)
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(entry, "replace_audio", fail)
    app.recorder.backup_failed = True
    app._capture_failed(app._capture)
    assert not app.recording and app._jobs.empty()
    assert "incomplete" in notices[-1]
    assert app._slots.acquire(blocking=False)
    app._slots.release()


def test_five_minute_limit_preserves_all_samples_and_notifies(app, monkeypatch, desktop):
    input_stream(monkeypatch)
    notices = []
    monkeypatch.setattr(app, "notify", notices.append)
    monkeypatch.setattr(apollo, "transcribe_openrouter", lambda *args, **kwargs: "full recording")
    app.on_press("dictate")
    pcm = np.ones((16000 * 301, 1), dtype=np.int16)
    app.recorder._callback(pcm, len(pcm), None, None)
    desktop.timers[-1].fire()
    app._jobs.join()
    assert not app.recording
    assert len(read_pcm(app.recovery_items()[0].path)) == 16000 * 300 * 2
    assert any("time limit reached" in notice for notice in notices)
