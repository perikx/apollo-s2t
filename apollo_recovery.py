"""Local, durable PCM recording checkpoints and recoverable transcript files.

Audio is written before the WAV length fields are committed. Opening an old
recording repairs those fields from the bytes actually on disk, including after
a process exits between the payload write and the header update. No credentials
or provider configuration belong in this store.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import struct
import threading
from typing import Any
import uuid


_ID = re.compile(r"[0-9a-f]{32}\Z")
_HEADER_SIZE = 44
_MAX_PCM_BYTES = 0xFFFFFFFF - 36
_STATES = frozenset({"recording", "pending", "processing", "failed", "ready", "too_short", "interrupted"})
_UPDATE_FIELDS = frozenset({"state", "error", "attempts", "last_attempt_at", "completed_at"})


class RecoveryError(ValueError):
    """A local recovery entry is incomplete, unsupported, or corrupt."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _header(rate: int, channels: int, payload_size: int) -> bytes:
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI", b"RIFF", payload_size + 36, b"WAVE", b"fmt ",
        16, 1, channels, rate, rate * channels * 2, channels * 2, 16, b"data", payload_size,
    )


def _sync_directory(path: Path) -> None:
    # Windows does not support opening directories through os.open. Each file
    # is still flushed before replacement; POSIX can additionally sync the name.
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


class RecoveryStore:
    """A folder of UUID-named WAV, metadata, and transcript files.

    ``list_recordings`` only reads metadata: it never modifies a live WAV.
    Call ``open`` only once the application has excluded an active recording
    with that ID. A store serializes operations on each of its entries.
    """

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._locks: dict[str, threading.RLock] = {}
        self._locks_lock = threading.Lock()

    def _lock(self, recording_id: str) -> threading.RLock:
        if not isinstance(recording_id, str) or not _ID.fullmatch(recording_id):
            raise RecoveryError("Invalid recording ID")
        with self._locks_lock:
            return self._locks.setdefault(recording_id, threading.RLock())

    def _path(self, recording_id: str, suffix: str) -> Path:
        self._lock(recording_id)
        path = self.root / f"{recording_id}{suffix}"
        if path.is_symlink() or path.resolve().parent != self.root:
            raise RecoveryError("Invalid recovery file path")
        return path

    def _read_metadata(self, recording_id: str) -> dict[str, Any]:
        try:
            path = self._path(recording_id, ".json")
            if path.stat().st_size > 1024 * 1024:
                raise RecoveryError("Recovery metadata is too large")
            metadata = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(metadata, dict)
                or metadata.get("version") != 1
                or metadata.get("id") != recording_id
                or type(metadata.get("samplerate")) is not int
                or not 1 <= metadata["samplerate"] <= 384000
                or type(metadata.get("channels")) is not int
                or not 1 <= metadata["channels"] <= 8
                or metadata.get("sample_width") != 2
                or metadata.get("state") not in _STATES
                or not isinstance(metadata.get("mode"), str)
                or not isinstance(metadata.get("prompt"), str)
                or not isinstance(metadata.get("created_at"), str)
            ):
                raise RecoveryError("Invalid recovery metadata")
            datetime.fromisoformat(metadata["created_at"])
            return metadata
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            if isinstance(exc, RecoveryError):
                raise
            raise RecoveryError("Cannot read recovery metadata") from exc

    def create(self, samplerate: int, channels: int, mode: str, prompt: str = "", *, hotkey: str = "") -> RecordingBackup:
        if type(samplerate) is not int or not 1 <= samplerate <= 384000:
            raise ValueError("Invalid recording sample rate")
        if type(channels) is not int or not 1 <= channels <= 8:
            raise ValueError("Invalid recording channel count")
        if not isinstance(mode, str) or not mode or len(mode) > 128:
            raise ValueError("Invalid recording mode")
        if not isinstance(prompt, str) or len(prompt.encode("utf-8")) > 900000:
            raise ValueError("Invalid recording prompt")
        recording_id = uuid.uuid4().hex
        timestamp = _now()
        metadata = {
            "version": 1, "id": recording_id, "samplerate": samplerate,
            "channels": channels, "sample_width": 2, "mode": mode, "prompt": prompt,
            "created_at": timestamp, "updated_at": timestamp, "state": "recording", "hotkey": hotkey,
        }
        with self._lock(recording_id):
            # Complete the initial header before this backup can accept samples.
            _atomic_write(self._path(recording_id, ".wav"), _header(samplerate, channels, 0))
            _atomic_write(self._path(recording_id, ".json"), json.dumps(metadata, ensure_ascii=False).encode("utf-8"))
        return RecordingBackup(self, recording_id)

    def open(self, recording_id: str) -> RecordingBackup:
        with self._lock(recording_id):
            backup = RecordingBackup(self, recording_id)
            backup._repair_wav()
            return backup

    def delete(self, recording_id: str) -> None:
        """Caller must exclude active entries under its application lock."""
        with self._lock(recording_id):
            paths = [self._path(recording_id, s) for s in (".wav", ".txt", ".transcript.txt", ".json")]
            for path in paths:
                path.unlink(missing_ok=True)

    def prune(self, *, minutes=15, max_entries=10, max_mb=64, protected=(), now=None):
        """Expire completed entries; active capture/processing can temporarily exceed limits.

        Retention starts at the last state update, so a long recording still has
        a full recovery window after processing. Only our UUID files are touched.
        """
        now = now or datetime.now(timezone.utc)
        total = count = 0
        removed = []
        for entry in self.list_recordings():
            if entry.id in protected:
                continue
            meta = entry.metadata
            stamp = datetime.fromisoformat(meta.get("updated_at", meta["created_at"]))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            paths = [self._path(entry.id, s) for s in (".wav", ".json", ".txt", ".transcript.txt")]
            size = sum(p.stat().st_size for p in paths if p.exists())
            if ((now - stamp).total_seconds() >= minutes * 60 or count >= max_entries
                    or total + size > max_mb * 1024 * 1024 or meta["state"] == "too_short"):
                self.delete(entry.id)
                removed.append(entry.id)
            else:
                total += size
                count += 1
        # A crash can leave an orphan WAV, corrupt JSON or atomic-write temp.
        # Expire only our exact filenames; never follow symlinks or delete user files.
        for path in self.root.iterdir():
            match = re.fullmatch(r"\.?([0-9a-f]{32})\.(?:wav|json|txt|transcript\.txt)(?:\.[0-9a-f]{32}\.tmp)?", path.name)
            if not match or match[1] in protected or path.is_symlink() or not path.is_file():
                continue
            try:
                metadata = self._read_metadata(match[1])
                if not path.name.endswith(".tmp") and self._path(match[1], ".wav").exists():
                    continue
            except (OSError, ValueError):
                pass
            if now.timestamp() - path.stat().st_mtime >= minutes * 60:
                path.unlink(missing_ok=True)
        return removed

    def list_recordings(self) -> list[RecordingBackup]:
        entries: list[tuple[str, RecordingBackup]] = []
        for metadata_path in self.root.glob("*.json"):
            recording_id = metadata_path.stem
            if not _ID.fullmatch(recording_id):
                continue
            try:
                with self._lock(recording_id):
                    metadata = self._read_metadata(recording_id)
                    if not self._path(recording_id, ".wav").is_file():
                        continue
                    entries.append((metadata["created_at"], RecordingBackup(self, recording_id, finished=True)))
            except (OSError, RecoveryError):
                # Preserve malformed files for manual recovery; no cleanup is
                # performed by merely opening the recovery menu.
                continue
        return [entry for _, entry in sorted(entries, key=lambda item: item[0], reverse=True)]


class RecordingBackup:
    def __init__(self, store: RecoveryStore, recording_id: str, *, finished: bool = False):
        self._store = store
        self.id = recording_id
        self._lock = store._lock(recording_id)
        self._finished = finished

    @property
    def path(self) -> Path:
        return self._store._path(self.id, ".wav")

    @property
    def metadata(self) -> dict[str, Any]:
        with self._lock:
            return self._store._read_metadata(self.id)

    def _repair_wav(self) -> None:
        metadata = self.metadata
        try:
            with self.path.open("r+b", buffering=0) as stream:
                actual_size = os.fstat(stream.fileno()).st_size
                if actual_size < _HEADER_SIZE or actual_size - _HEADER_SIZE > _MAX_PCM_BYTES:
                    raise RecoveryError("Invalid recovery WAV length")
                old_header = stream.read(_HEADER_SIZE)
                canonical = _header(metadata["samplerate"], metadata["channels"], 0)
                if old_header[:4] != canonical[:4] or old_header[8:40] != canonical[8:40]:
                    raise RecoveryError("Invalid recovery WAV format")
                frame_size = metadata["channels"] * 2
                payload_size = actual_size - _HEADER_SIZE
                payload_size -= payload_size % frame_size
                if actual_size != payload_size + _HEADER_SIZE:
                    # An interrupted OS write may leave an incomplete frame.
                    stream.truncate(payload_size + _HEADER_SIZE)
                repaired = _header(metadata["samplerate"], metadata["channels"], payload_size)
                if old_header != repaired or actual_size != payload_size + _HEADER_SIZE:
                    stream.seek(0)
                    stream.write(repaired)
                    stream.flush()
                    os.fsync(stream.fileno())
        except OSError as exc:
            raise RecoveryError("Cannot read recovery audio") from exc

    def append(self, pcm: bytes) -> None:
        with self._lock:
            if self._finished:
                raise RecoveryError("Recording checkpoint is already finished")
            if not isinstance(pcm, bytes):
                raise TypeError("PCM checkpoint must be bytes")
            metadata = self.metadata
            if len(pcm) % (metadata["channels"] * 2):
                raise ValueError("PCM checkpoint must contain complete int16 frames")
            if not pcm:
                return
            with self.path.open("r+b") as stream:
                stream.seek(0, os.SEEK_END)
                payload_size = stream.tell() - _HEADER_SIZE + len(pcm)
                if payload_size > _MAX_PCM_BYTES:
                    raise RecoveryError("Recording exceeds the WAV size limit")
                stream.write(pcm)
                stream.flush()
                os.fsync(stream.fileno())
                stream.seek(0)
                stream.write(_header(metadata["samplerate"], metadata["channels"], payload_size))
                stream.flush()
                os.fsync(stream.fileno())

    def finish(self) -> None:
        with self._lock:
            if not self._finished:
                self._repair_wav()
                self._finished = True

    def replace_audio(self, pcm: bytes) -> None:
        """Atomically save the final in-memory audio after a checkpoint failed.

        The application must first stop the checkpoint writer. Failed writes
        leave the existing WAV untouched; successful replacement closes this
        backup to further appends, including when called after ``finish``.
        """
        with self._lock:
            if not isinstance(pcm, bytes):
                raise TypeError("PCM replacement must be bytes")
            metadata = self.metadata
            if len(pcm) % (metadata["channels"] * 2):
                raise ValueError("PCM replacement must contain complete int16 frames")
            if len(pcm) > _MAX_PCM_BYTES:
                raise RecoveryError("Recording exceeds the WAV size limit")
            _atomic_write(self.path, _header(metadata["samplerate"], metadata["channels"], len(pcm)) + pcm)
            self._finished = True

    def update(self, **fields: Any) -> None:
        if set(fields) - _UPDATE_FIELDS:
            raise ValueError("Unsupported recovery metadata field")
        if "state" in fields and fields["state"] not in _STATES:
            raise ValueError("Invalid recovery state")
        if "attempts" in fields and (type(fields["attempts"]) is not int or fields["attempts"] < 0):
            raise ValueError("Invalid recovery attempt count")
        for key in ("error", "last_attempt_at", "completed_at"):
            value = fields.get(key)
            if value is not None and (not isinstance(value, str) or len(value) > 1000):
                raise ValueError("Invalid recovery metadata value")
        with self._lock:
            metadata = self.metadata
            metadata.update(fields, updated_at=_now())
            _atomic_write(self._store._path(self.id, ".json"), json.dumps(metadata, ensure_ascii=False).encode("utf-8"))

    def save_transcript(self, text: str, final: bool = False) -> None:
        if not isinstance(text, str):
            raise TypeError("Transcript must be text")
        with self._lock:
            _atomic_write(self._store._path(self.id, ".txt" if final else ".transcript.txt"), text.encode("utf-8"))

    def read_transcript(self) -> str | None:
        with self._lock:
            for suffix in (".txt", ".transcript.txt"):
                try:
                    return self._store._path(self.id, suffix).read_text(encoding="utf-8")
                except FileNotFoundError:
                    continue
                except UnicodeError as exc:
                    raise RecoveryError("Cannot read recovery transcript") from exc
        return None
