"""Recording, FIFO processing and shutdown tests with real worker threads, no audio/API."""
from copy import deepcopy
import io
import logging
import threading
import time
import wave
from types import SimpleNamespace

import numpy as np
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
        data = np.full((self.sample_count, 1), self.starts, dtype=np.int16)
        if self.backup is not None:
            self.backup.append(data.tobytes())
            self.backup.finish()
        return data


def capture(app, mode="dictate", origin="window-a"):
    return (mode, origin, deepcopy(app.cfg), apollo.PROMPTS.get(mode, ""))


def run_process(app, mode="dictate"):
    app._process(capture(app, mode), np.zeros((8000, 1), dtype=np.int16), time.monotonic())


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


def test_empty_rewrite_preserves_raw(app, monkeypatch):
    delivered = []
    monkeypatch.setattr(apollo, "transcribe_openrouter", lambda *args, **kwargs: "raw")
    monkeypatch.setattr(apollo, "smooth", lambda *args, **kwargs: "")
    monkeypatch.setattr(app, "insert_text", lambda text, t0: delivered.append(text))
    run_process(app, "polish")
    assert delivered == ["raw"]


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


def test_fifo_and_per_recording_context_snapshots(app, monkeypatch, tmp_path):
    started, release = threading.Event(), threading.Event()
    delivered, seen, windows = [], [], ["a", "b"]
    app.recorder = FakeRecorder()
    app.base_dir = str(tmp_path)
    (tmp_path / "prompts").mkdir()
    profile = tmp_path / "prompts" / "default.md"
    profile.write_text("first context")
    monkeypatch.setattr(apollo, "get_foreground_window", lambda: windows[0])
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
    monkeypatch.setattr(app, "insert_text", lambda text, t0: delivered.append((text, app._origin_hwnd)))
    try:
        app.on_press("prompt")
        app.on_release("prompt")
        assert started.wait(3)
        windows.pop(0)
        profile.write_text("second context")
        app.cfg["openrouter_stt"]["model"] = "custom/new"
        app.on_press("prompt")
        app.on_release("prompt")
        profile.write_text("third context, must not affect queued jobs")
        app.cfg["openrouter_stt"]["model"] = "custom/later"
    finally:
        release.set()
        if app._worker:
            # Wait for queue completion, then stop/join the idle worker in teardown.
            app._jobs.join()
            app.close()
            app._worker.join(3)
    assert seen == ["microsoft/mai-transcribe-2", "custom/new"]
    assert delivered == [("1:first", "a"), ("2:second", "b")]


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
        app._jobs.join()
        app.close()
        app._worker.join(3)


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
        app._worker.join(3)
    assert delivered == []
    assert app._jobs.unfinished_tasks == 0


def test_short_recordings_make_no_job(app):
    app.recorder = FakeRecorder(100)
    for _ in range(5):
        app.on_press("dictate")
        app.on_release("dictate")
    assert app._jobs.empty()
    assert app._worker is None
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


def test_worker_start_failure_does_not_leave_ghost_job(app, monkeypatch):
    app.recorder = FakeRecorder()
    def fail(*args):
        raise RuntimeError("cannot create thread")
    monkeypatch.setattr(apollo.threading.Thread, "start", fail)
    app.on_press("dictate")
    app.on_release("dictate")
    assert app._jobs.empty()
    assert app._slots.acquire(blocking=False)
    app._slots.release()


def test_recorder_bounds_audio_and_releases_buffers():
    recorder = apollo.Recorder(16000, 1, None, max_seconds=1)
    data = np.ones((10000, 1), dtype=np.int16)
    recorder._callback(data, len(data), None, None)
    recorder._callback(data, len(data), None, None)
    recorder._callback(data, len(data), None, None)
    assert recorder.sample_count == 16000
    result = recorder.stop()
    assert len(result) == 16000
    assert recorder._frames == []
    assert recorder.stop() is None


def test_stream_closed_and_frames_released_even_when_stop_raises():
    recorder = apollo.Recorder(16000, 1, None)
    recorder._frames = [np.zeros((10, 1), dtype=np.int16)]
    closed = []
    def fail():
        raise OSError("stop failed")
    recorder._stream = SimpleNamespace(stop=fail, close=lambda: closed.append(True))
    result = recorder.stop()
    assert len(result) == 10
    assert recorder.capture_warning
    assert closed == [True]
    assert recorder._stream is None and recorder._frames == []


def test_wav_round_trip():
    pcm = np.array([[0], [123], [-456]], dtype=np.int16)
    with wave.open(io.BytesIO(apollo.to_wav_bytes(pcm, 16000, 1)), "rb") as audio:
        assert (audio.getnchannels(), audio.getframerate(), audio.getsampwidth()) == (1, 16000, 2)
        assert np.array_equal(np.frombuffer(audio.readframes(3), dtype="<i2"), pcm[:, 0])


def test_prompt_language_and_profile(tmp_path):
    cfg = apollo.default_config()
    cfg["prompt_profiles"]["include_karpathy"] = False
    cfg["prompt_profiles"]["output_language"] = "match"
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "default.md").write_text("Project reference")
    prompt = apollo.build_prompt_system(cfg, str(tmp_path))
    assert "Project reference" in prompt and "Keep the language" in prompt
    assert apollo.KARPATHY_GUIDELINES not in prompt
