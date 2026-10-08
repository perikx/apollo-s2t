from array import array
from datetime import datetime, timedelta, timezone
import os

import pytest

import apollo
from apollo_models import discover_catalog
from apollo_recovery import RecoveryStore
from apollo_config import normalize_config, ConfigError, read_config


def entry(store, hotkey="n"):
    backup = store.create(16000, 1, "polish", hotkey=hotkey)
    backup.append(b"\x00\x00" * 8000)
    backup.finish()
    backup.update(state="ready")
    return backup


def test_retention_protects_busy_and_expires_all_artifacts(tmp_path):
    store = RecoveryStore(tmp_path)
    old, busy = entry(store), entry(store)
    old.save_transcript("raw")
    old.save_transcript("final", final=True)
    now = datetime.now(timezone.utc) + timedelta(minutes=16)
    assert store.prune(protected={busy.id}, now=now) == [old.id]
    assert not list(tmp_path.glob(old.id + "*"))
    assert busy.path.exists()
    assert store.prune(now=now) == [busy.id]


def test_retention_count_bytes_and_unrelated_files(tmp_path):
    store = RecoveryStore(tmp_path)
    old, newest = entry(store), entry(store)
    unrelated = tmp_path / "user-notes.txt"
    unrelated.write_text("keep")
    assert store.prune(max_entries=1) == [old.id]
    assert newest.path.exists()
    assert store.prune(max_mb=0) == [newest.id]
    assert unrelated.read_text() == "keep"


def test_recovery_saves_used_key_and_busy_deletion_is_blocked(app):
    saved = entry(app.recovery)
    assert saved.metadata["hotkey"] == "n"
    app._busy_recordings.add(saved.id)
    app.delete_recovery(saved.id)
    assert saved.path.exists()
    app._busy_recordings.clear()
    app.delete_recovery(saved.id)
    assert not saved.path.exists()


def test_preferences_atomic_failure_keeps_running_config(app, monkeypatch):
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(apollo, "save_config", fail)
    with pytest.raises(OSError):
        app.update_preferences({"overlay": {"visible": False}})
    assert app.cfg["overlay"]["visible"] is True


def test_preferences_saved(app):
    app.update_preferences({"overlay": {"visible": False}, "recovery_cache": {"minutes": 5}})
    app.notify("Provider busy")
    assert app.ui_events.get_nowait() == ("status", "Provider busy")
    cfg, _ = read_config(app.base_dir + "/config.json")
    assert cfg["overlay"]["visible"] is False
    assert cfg["recovery_cache"]["minutes"] == 5


@pytest.mark.parametrize("changes", [{"recovery_cache": {"minutes": 0}}, {"overlay": {"visible": "false"}}, {"overlay": {"x": float("nan")}}])
def test_bad_cache_settings_rejected(changes):
    with pytest.raises(ConfigError):
        normalize_config(changes)


def test_catalog_filters_endpoint_modalities_and_sends_no_key():
    calls = []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"data": [
                {"id": "speech/real", "architecture": {"input_modalities": ["audio"], "output_modalities": ["transcription"]}},
                {"id": "chat/audio", "architecture": {"input_modalities": ["audio", "text"], "output_modalities": ["text"]}},
                {"id": "image/only", "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]}}]}
    def get(url, **kwargs):
        calls.append(kwargs)
        return Response()
    assert list(discover_catalog("transcription", get)) == ["speech/real"]
    assert list(discover_catalog("text", get)) == ["chat/audio"]
    assert calls[0] == {"params": {"output_modalities": "transcription"}, "timeout": 15}


def test_cache_cleans_old_orphans_and_temporary_files_only(tmp_path):
    store = RecoveryStore(tmp_path)
    old_id, busy_id = "a" * 32, "b" * 32
    orphan = tmp_path / (old_id + ".wav")
    corrupt = tmp_path / (old_id + ".json")
    temporary = tmp_path / ("." + old_id + ".txt." + "c" * 32 + ".tmp")
    protected = tmp_path / (busy_id + ".wav")
    unrelated = tmp_path / "personal.wav"
    now = datetime.now(timezone.utc)
    for path in (orphan, corrupt, temporary, protected, unrelated):
        path.write_bytes(b"invalid")
        old = now.timestamp() - 3600
        os.utime(path, (old, old))
    store.prune(protected={busy_id}, now=now)
    assert all(not p.exists() for p in (orphan, corrupt, temporary))
    assert protected.exists() and unrelated.exists()


def test_completed_dictation_never_pastes_into_apollo_dialog(app, desktop):
    app.ui_windows.add("window-a")
    app.insert_text("saved words", 0)
    assert desktop.clip.text == "saved words"
    assert desktop.sent == []


def test_visual_envelope_follows_quiet_speech_and_pauses_without_altering_pcm():
    recorder = apollo.Recorder(16000, 1, None)
    samples = array("h", [0] * 320 + [330] * 320 + [3300] * 320 + [0] * 320).tobytes()
    recorder._callback(samples, len(samples) // 2, None, None)
    levels = recorder.visual_levels()
    assert levels[0] == levels[-1] == 0
    assert .3 < levels[1] < levels[2] <= 1
    assert b"".join(recorder._frames) == samples


def test_visual_levels_are_empty_before_audio_arrives():
    assert apollo.Recorder(16000, 1, None).visual_levels() == ()
