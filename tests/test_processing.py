"""Recording, FIFO processing and shutdown tests with real worker threads, no audio/API."""
from copy import deepcopy
from array import array
import io
import logging
import threading
import time
import wave
from types import SimpleNamespace

import pytest
import requests
import apollo


class FakeRecorder:
    def __init__(self, samples=8000):
        self.sample_count = samples
        self.starts = self.stops = 0

    capture_warning = backup_failed = False

    def start(self, backup=None, on_error=None):
        self.backup = backup
        self.starts += 1

    def stop(self):
        self.stops += 1
        data = (array("h", [self.starts]) * self.sample_count).tobytes()
        backup = self.backup
        def save():
            if backup is not None:
                backup.append(data)
                backup.finish()
            return True
        return data, save


def capture(app, mode="dictate"):
    return (mode, deepcopy(app.cfg), apollo.PROMPTS.get(mode, ""))


def run_process(app, mode="dictate"):
    pcm = bytes(16000)
    backup = app.recovery.create(16000, 1, mode)
    backup.append(pcm)
    backup.finish()
    app._process(capture(app, mode), pcm, time.monotonic(), backup)


@pytest.mark.parametrize("mode", ["dictate", "polish", "prompt"])
def test_modes_use_correct_calls(app, monkeypatch, mode):
    calls, delivered = [], []
    monkeypatch.setattr(apollo, "transcribe_openrouter", lambda *args, **kwargs: "raw")
    def rewrite(text, prompt, cfg):
        calls.append((text, prompt, cfg["api_key"]))
        return "edited"
    monkeypatch.setattr(apollo, "smooth", rewrite)
    monkeypatch.setattr(app, "insert_text", lambda text, t0: delivered.append(text))
    run_process(app, mode)
    assert delivered == ["raw" if mode == "dictate" else "edited"]
    assert len(calls) == (0 if mode == "dictate" else 1)
    if calls:
        assert calls[0] == ("raw", apollo.PROMPTS[mode], "test-key")


@pytest.mark.parametrize("failure", [requests.Timeout("private text"), apollo.ResponseError("truncated"), RuntimeError("private text")])
def test_rewrite_failure_preserves_raw_and_keeps_content_out_of_log(app, monkeypatch, caplog, failure):
    secret, delivered = "personal dictation 123", []
    monkeypatch.setattr(apollo, "transcribe_openrouter", lambda *args, **kwargs: secret)
    def fail(*args):
        raise failure
    monkeypatch.setattr(apollo, "smooth", fail)
    monkeypatch.setattr(app, "insert_text", lambda text, t0: delivered.append(text))
    with caplog.at_level(logging.INFO, logger="apollo"):
        run_process(app, "polish")
    assert delivered == [secret]
    assert secret not in caplog.text
    assert "private text" not in caplog.text


def test_text_is_pasted_before_transcript_is_saved(app, monkeypatch):
    order = []
    monkeypatch.setattr(apollo, "transcribe_openrouter", lambda *args, **kwargs: "raw")
    monkeypatch.setattr(app, "insert_text", lambda text, t0: order.append("paste"))
    from apollo_recovery import RecordingBackup
    save = RecordingBackup.save_transcript
    monkeypatch.setattr(RecordingBackup, "save_transcript", lambda self, *a, **k: (order.append("save"), save(self, *a, **k)))
    run_process(app)
    assert order[0] == "paste" and "save" in order


def test_empty_rewrite_preserves_raw(app, monkeypatch):
    delivered = []
    monkeypatch.setattr(apollo, "transcribe_openrouter", lambda *args, **kwargs: "raw")
    monkeypatch.setattr(apollo, "smooth", lambda *args, **kwargs: "")
    monkeypatch.setattr(app, "insert_text", lambda text, t0: delivered.append(text))
    run_process(app, "polish")
    assert delivered == ["raw"]


def test_fallback_is_logged_and_only_real_waits_notify(app, monkeypatch, caplog):
    def stt(wav, cfg, key, *, on_retry, on_fallback, on_attempt, wait):
        on_fallback("fallback/model")
        on_retry(2, 0)
        on_retry(3, 4.2)
        on_attempt("fallback/model", True, 0.5)
        return "raw"
    monkeypatch.setattr(apollo, "transcribe_openrouter", stt)
    monkeypatch.setattr(app, "insert_text", lambda text, t0: None)
    with caplog.at_level(logging.INFO, logger="apollo"):
        run_process(app)
    notices = []
    while not app.ui_events.empty():
        notices.append(app.ui_events.get_nowait()[1])
    assert notices == ["Transcription busy. Audio saved; retrying in 4 seconds.", "Ready"]
    assert "fallback/model" in caplog.text
    import json
    stats = json.loads(open(app.stats_path, encoding="utf-8").read())
    assert stats["fallback/model"]["ok"] == 1


@pytest.mark.parametrize("failure", [None, requests.Timeout("secret"), apollo.ResponseError("bad")])
def test_empty_or_failed_stt_never_pastes(app, monkeypatch, failure):
    delivered = []
    def stt(*args, **kwargs):
        if failure:
            raise failure
        return ""
    monkeypatch.setattr(apollo, "transcribe_openrouter", stt)
    monkeypatch.setattr(app, "insert_text", lambda *args, **kwargs: delivered.append(args))
    run_process(app)
    assert delivered == []


def test_per_recording_context_snapshots(app, monkeypatch, tmp_path):
    started, release = threading.Event(), threading.Event()
    delivered, seen = [], []
    app.recorder = FakeRecorder()
    app.base_dir = str(tmp_path)
    (tmp_path / "prompts").mkdir()
    profile = tmp_path / "prompts" / "default.md"
    profile.write_text("first context")
    def stt(audio, cfg, key, **kwargs):
        seen.append(cfg["model"])
        if len(seen) == 1:
            started.set()
            assert release.wait(3)
        return str(len(seen))
    def rewrite(text, prompt, cfg):
        return "1:first" if "first context" in prompt else "2:second"
    monkeypatch.setattr(apollo, "transcribe_openrouter", stt)
    monkeypatch.setattr(apollo, "smooth", rewrite)
    monkeypatch.setattr(app, "insert_text", lambda text, t0: delivered.append(text))
    try:
        app.on_press("prompt")
        app.on_release("prompt")
        assert started.wait(3)
        profile.write_text("second context")
        app.cfg["openrouter_stt"]["model"] = "custom/new"
        app.on_press("prompt")
        app.on_release("prompt")
        profile.write_text("third context, must not affect queued jobs")
        app.cfg["openrouter_stt"]["model"] = "custom/later"
    finally:
        release.set()
        app._join_jobs()
        app.close()
    assert seen == ["microsoft/mai-transcribe-2", "custom/new"]
    assert sorted(delivered) == ["1:first", "2:second"]  # jobs run in parallel and paste when done


def test_queue_limit_rejects_new_capture_without_overwriting_audio(app, monkeypatch):
    started, release = threading.Event(), threading.Event()
    app.recorder = FakeRecorder()
    def process(*args):
        started.set()
        assert release.wait(3)
    monkeypatch.setattr(app, "_process", process)
    try:
        for _ in range(3):
            app.on_press("dictate")
            app.on_release("dictate")
        assert started.wait(3)
        app.on_press("dictate")
        assert not app.recording
        assert app.recorder.starts == 3
    finally:
        release.set()
        app._join_jobs()
        app.close()


def test_shutdown_during_network_call_cannot_paste(app, monkeypatch):
    started, release = threading.Event(), threading.Event()
    app.recorder = FakeRecorder()
    delivered = []
    def stt(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return "late"
    monkeypatch.setattr(apollo, "transcribe_openrouter", stt)
    monkeypatch.setattr(app, "insert_text", lambda *args, **kwargs: delivered.append(args))
    app.on_press("dictate")
    app.on_release("dictate")
    try:
        assert started.wait(3)
        app.close()
    finally:
        release.set()
        app._join_jobs(3)
    assert delivered == []
    assert not app._threads


def test_short_recordings_make_no_job(app):
    app.recorder = FakeRecorder(100)
    for _ in range(5):
        app.on_press("dictate")
        app.on_release("dictate")
    assert not app._threads
    assert app.recorder.starts == 5


def test_wrong_mode_cannot_stop_another_recording(app):
    app.recorder = FakeRecorder(100)
    app.on_press("dictate")
    app.on_press("polish")
    app.on_release("polish")
    assert app.recording and app.active_mode == "dictate"
    assert app.recorder.starts == 1
    app.on_release("dictate")
    assert not app.recording


def test_stale_max_duration_timer_cannot_stop_next_capture(app, desktop):
    app.recorder = FakeRecorder(100)
    app.on_press("dictate")
    old = desktop.timers[-1]
    app.on_release("dictate")
    app.on_press("dictate")
    old.fire(even_if_cancelled=True)
    assert app.recording
    desktop.timers[-1].fire()
    assert not app.recording


def test_microphone_failure_recovers_slot_and_state(app, monkeypatch):
    app.recorder = FakeRecorder()
    def fail():
        raise OSError("device unavailable")
    monkeypatch.setattr(app.recorder, "start", fail)
    for _ in range(5):
        app.on_press("dictate")
        assert not app.recording
    assert app._slots.acquire(blocking=False)
    app._slots.release()


def test_job_start_failure_does_not_leave_ghost_job(app, monkeypatch):
    app.recorder = FakeRecorder()
    def fail(*args):
        raise RuntimeError("cannot create thread")
    monkeypatch.setattr(app, "_start_job", fail)
    app.on_press("dictate")
    app.on_release("dictate")
    assert not app._threads
    assert app._slots.acquire(blocking=False)
    app._slots.release()


def test_recorder_bounds_audio_and_releases_buffers():
    recorder = apollo.Recorder(16000, 1, None, max_seconds=1)
    data = b"\x01\x00" * 10000
    recorder._callback(data, 10000, None, None)
    recorder._callback(data, 10000, None, None)
    recorder._callback(data, 10000, None, None)
    assert recorder.sample_count == 16000
    result, _ = recorder.stop()
    assert len(result) == 16000 * 2
    assert recorder._frames == []
    assert recorder.stop()[0] is None


def test_stream_closed_and_frames_released_even_when_stop_raises():
    recorder = apollo.Recorder(16000, 1, None)
    recorder._frames = [bytes(20)]
    closed = []
    def fail():
        raise OSError("stop failed")
    recorder._stream = SimpleNamespace(stop=fail, close=lambda: closed.append(True))
    result, _ = recorder.stop()
    assert len(result) == 20
    assert recorder.capture_warning
    assert closed == [True]
    assert recorder._stream is None and recorder._frames == []


def test_wav_round_trip():
    pcm = array("h", [0, 123, -456]).tobytes()
    with wave.open(io.BytesIO(apollo.to_wav_bytes(pcm, 16000, 1)), "rb") as audio:
        assert (audio.getnchannels(), audio.getframerate(), audio.getsampwidth()) == (1, 16000, 2)
        assert audio.readframes(3) == pcm


def test_prompt_language_and_profile(tmp_path):
    cfg = apollo.default_config()
    cfg["prompt_profiles"]["output_language"] = "match"
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "default.md").write_text("Project reference")
    prompt = apollo.build_prompt_system(cfg, str(tmp_path))
    assert "Project reference" in prompt and "Keep the language" in prompt
    assert apollo.KARPATHY_GUIDELINES in prompt
