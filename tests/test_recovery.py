"""Recovery checkpoints use real files and abrupt subprocess termination."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import wave

import pytest

from apollo_recovery import RecoveryError, RecoveryStore


def wav_samples(path):
    with wave.open(str(path), "rb") as audio:
        return (audio.getframerate(), audio.getnchannels(), audio.readframes(audio.getnframes()))


def test_checkpoint_preserves_all_frames_and_metadata(tmp_path):
    store = RecoveryStore(tmp_path)
    backup = store.create(16000, 1, "polish", "Make wording clear")
    samples = struct.pack("<hhhh", 1, -200, 300, -32768)
    backup.append(samples[:4])
    assert wav_samples(backup.path) == (16000, 1, samples[:4])
    backup.append(samples[4:])
    backup.finish()
    backup.finish()
    assert wav_samples(backup.path) == (16000, 1, samples)
    assert backup.metadata["mode"] == "polish"
    assert backup.metadata["prompt"] == "Make wording clear"
    with pytest.raises(RecoveryError):
        backup.append(samples)


def test_subprocess_exit_between_pcm_and_header_commit_is_recovered(tmp_path):
    # os._exit skips Python close/finalization. The second fsync is interrupted
    # after audio reached disk but before append can update its WAV header.
    script = """
import os, sys
from pathlib import Path
from apollo_recovery import RecoveryStore
store = RecoveryStore(Path(sys.argv[1]))
backup = store.create(16000, 1, 'dictate')
backup.append(bytes.fromhex('01000200'))
original = os.fsync
def stop_after_sync(fd):
    original(fd)
    os._exit(73)
os.fsync = stop_after_sync
backup.append(bytes.fromhex('03000400'))
"""
    completed = subprocess.run([sys.executable, "-c", script, str(tmp_path)], cwd=Path(__file__).resolve().parents[1])
    assert completed.returncode == 73
    store = RecoveryStore(tmp_path)
    entry = store.list_recordings()[0]
    assert wav_samples(entry.path)[2] == bytes.fromhex("01000200")
    recovered = store.open(entry.id)
    assert wav_samples(recovered.path) == (16000, 1, bytes.fromhex("0100020003000400"))


def test_interrupted_partial_frame_is_trimmed_only_on_explicit_open(tmp_path):
    store = RecoveryStore(tmp_path)
    backup = store.create(44100, 2, "dictate")
    backup.append(bytes.fromhex("01000200"))
    with backup.path.open("ab") as stream:
        stream.write(bytes.fromhex("03000400ff"))
    before = backup.path.read_bytes()
    store.list_recordings()
    assert backup.path.read_bytes() == before
    recovered = store.open(backup.id)
    assert wav_samples(recovered.path) == (44100, 2, bytes.fromhex("0100020003000400"))


def test_transcripts_are_atomic_and_final_is_preferred(tmp_path, monkeypatch):
    store = RecoveryStore(tmp_path)
    backup = store.create(16000, 1, "polish")
    assert backup.read_transcript() is None
    backup.save_transcript("original Ä words")
    assert backup.read_transcript() == "original Ä words"
    backup.save_transcript("revised words", final=True)
    assert backup.read_transcript() == "revised words"
    original_replace = os.replace

    def fail_replace(source, target):
        raise OSError("disk failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        backup.save_transcript("incomplete replacement", final=True)
    assert backup.read_transcript() == "revised words"
    assert not list(tmp_path.glob("*.tmp"))
    monkeypatch.setattr(os, "replace", original_replace)
    assert (tmp_path / f"{backup.id}.transcript.txt").read_text(encoding="utf-8") == "original Ä words"


def test_list_is_latest_first_and_never_repairs_live_audio(tmp_path):
    store = RecoveryStore(tmp_path)
    older = store.create(16000, 1, "dictate")
    newer = store.create(16000, 1, "prompt")
    with newer.path.open("ab") as stream:
        stream.write(b"\x01\x00")
    before = newer.path.read_bytes()
    assert [item.id for item in store.list_recordings()] == [newer.id, older.id]
    assert newer.path.read_bytes() == before
    newer.append(b"\x02\x00")
    assert wav_samples(newer.path)[2] == b"\x01\x00\x02\x00"


@pytest.mark.parametrize("recording_id", ["..", "../private", "a/../../secret", "C:\\secret", "A" * 32, "f" * 32 + ".json", ""])
def test_path_traversal_and_noncanonical_ids_are_rejected(tmp_path, recording_id):
    with pytest.raises(RecoveryError):
        RecoveryStore(tmp_path).open(recording_id)


def test_corrupt_metadata_is_skipped_and_audio_is_preserved(tmp_path):
    store = RecoveryStore(tmp_path)
    backup = store.create(16000, 1, "dictate")
    backup.append(b"\x01\x00")
    before = backup.path.read_bytes()
    (tmp_path / f"{backup.id}.json").write_text("{broken", encoding="utf-8")
    assert store.list_recordings() == []
    with pytest.raises(RecoveryError):
        store.open(backup.id)
    assert backup.path.read_bytes() == before


def test_corrupt_audio_format_is_never_rewritten(tmp_path):
    store = RecoveryStore(tmp_path)
    backup = store.create(16000, 1, "dictate")
    backup.path.write_bytes(b"not a WAV" * 10)
    before = backup.path.read_bytes()
    with pytest.raises(RecoveryError):
        store.open(backup.id)
    assert backup.path.read_bytes() == before


def test_metadata_updates_are_atomic_and_cannot_store_configuration(tmp_path):
    store = RecoveryStore(tmp_path)
    backup = store.create(16000, 1, "dictate")
    backup.update(state="failed", error="rate_limited")
    reopened = store.open(backup.id)
    assert reopened.metadata["state"] == "failed"
    assert reopened.metadata["error"] == "rate_limited"
    with pytest.raises(ValueError):
        backup.update(api_key="must-never-be-stored")
    with pytest.raises(ValueError):
        backup.update(state="unknown")
    with pytest.raises(ValueError):
        backup.update(samplerate=1)
    metadata = json.loads((tmp_path / f"{backup.id}.json").read_text(encoding="utf-8"))
    assert metadata["samplerate"] == 16000
    assert "api_key" not in metadata


def test_incomplete_frames_are_rejected_without_changing_audio(tmp_path):
    backup = RecoveryStore(tmp_path).create(16000, 2, "dictate")
    before = backup.path.read_bytes()
    with pytest.raises(ValueError):
        backup.append(b"\x01\x00")
    assert backup.path.read_bytes() == before


def test_final_audio_replaces_checkpoint_after_finish(tmp_path):
    backup = RecoveryStore(tmp_path).create(16000, 1, "dictate")
    backup.append(bytes.fromhex("01000200"))
    backup.finish()
    complete_audio = bytes.fromhex("0100020003000400")
    backup.replace_audio(complete_audio)
    assert wav_samples(backup.path) == (16000, 1, complete_audio)
    backup.finish()
    with pytest.raises(RecoveryError):
        backup.append(b"\x05\x00")


def test_failed_audio_replacement_preserves_previous_checkpoint(tmp_path, monkeypatch):
    backup = RecoveryStore(tmp_path).create(44100, 2, "dictate")
    backup.append(bytes.fromhex("01000200"))
    before = backup.path.read_bytes()

    def fail_replace(source, target):
        raise OSError("disk failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        backup.replace_audio(bytes.fromhex("0100020003000400"))
    assert backup.path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("pcm", [b"\x01\x00", b"\x01", bytearray(b"\x01\x00\x02\x00")])
def test_audio_replacement_rejects_incomplete_or_invalid_frames(tmp_path, pcm):
    backup = RecoveryStore(tmp_path).create(16000, 2, "dictate")
    before = backup.path.read_bytes()
    with pytest.raises((TypeError, ValueError)):
        backup.replace_audio(pcm)
    assert backup.path.read_bytes() == before
