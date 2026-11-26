import ctypes
import io
import json
import math
import struct
import sys
import threading
import wave
from unittest.mock import Mock
import pytest
import requests
import apollo_api as api
from apollo_config import DEFAULT_STT_MODEL, DEFAULT_SMOOTHING_MODEL, default_config


def _make_wav(pcm, rate=16000, channels=1, width=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _decode_flac_mf(flac_bytes):
    """Decode FLAC bytes to 16-bit PCM via Windows Media Foundation (no external binaries or numpy)."""
    mf = api._mf_init()
    assert mf is not None, "Windows Media Foundation FLAC codec unavailable"
    GUID, wintypes, ole32_raw, mfplat, mfrw, shlwapi, g = mf
    mfrw.MFCreateSourceReaderFromByteStream.argtypes = (
        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
    )
    mfrw.MFCreateSourceReaderFromByteStream.restype = ctypes.HRESULT
    co = ole32_raw.CoInitializeEx(None, 0) >= 0
    objs = []
    try:
        raw = ctypes.create_string_buffer(flac_bytes, len(flac_bytes))
        istream = ctypes.c_void_p(shlwapi.SHCreateMemStream(raw, len(flac_bytes)))
        assert istream.value
        objs.append(istream)
        bytestream = ctypes.c_void_p()
        mfplat.MFCreateMFByteStreamOnStream(istream, ctypes.byref(bytestream))
        objs.append(bytestream)
        reader = ctypes.c_void_p()
        mfrw.MFCreateSourceReaderFromByteStream(bytestream, None, ctypes.byref(reader))
        objs.append(reader)
        mt = ctypes.c_void_p()
        mfplat.MFCreateMediaType(ctypes.byref(mt))
        objs.append(mt)
        set_guid = api._com_call(mt, 24, ctypes.POINTER(GUID), ctypes.POINTER(GUID))
        set_guid(mt, ctypes.byref(g["major"]), ctypes.byref(g["audio"]))
        set_guid(mt, ctypes.byref(g["subtype"]), ctypes.byref(g["pcm"]))
        first_audio = 0xFFFFFFFD
        api._com_call(reader, 7, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p)(reader, first_audio, None, mt)
        read_sample = api._com_call(
            reader, 9, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(ctypes.c_longlong), ctypes.POINTER(ctypes.c_void_p),
        )
        chunks = []
        while True:
            idx, flags, ts, sample = wintypes.DWORD(), wintypes.DWORD(), ctypes.c_longlong(), ctypes.c_void_p()
            read_sample(reader, first_audio, 0, ctypes.byref(idx), ctypes.byref(flags), ctypes.byref(ts), ctypes.byref(sample))
            if sample.value:
                try:
                    buf = ctypes.c_void_p()
                    api._com_call(sample, 41, ctypes.POINTER(ctypes.c_void_p))(sample, ctypes.byref(buf))
                    try:
                        ptr, cur_len = ctypes.c_void_p(), wintypes.DWORD()
                        api._com_call(buf, 3, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD))(
                            buf, ctypes.byref(ptr), None, ctypes.byref(cur_len))
                        chunks.append(ctypes.string_at(ptr.value, cur_len.value))
                        api._com_call(buf, 4)(buf)
                    finally:
                        api._com_release(buf)
                finally:
                    api._com_release(sample)
            if flags.value & 2:  # MF_SOURCE_READERF_ENDOFSTREAM
                break
        return b"".join(chunks)
    finally:
        for obj in reversed(objs):
            api._com_release(obj)
        if co:
            ole32_raw.CoUninitialize()


def _decode_uploaded_pcm(file_tuple):
    filename, payload, mime = file_tuple
    if mime == "audio/flac":
        assert filename == "audio.flac" and payload[:4] == b"fLaC"
        pcm = _decode_flac_mf(payload)
        return len(pcm) // 2, pcm
    assert (filename, mime) == ("audio.wav", "audio/wav")
    with wave.open(io.BytesIO(payload)) as w:
        return w.getnframes(), w.readframes(w.getnframes())


SMOOTHING = dict(default_config()["smoothing"], api_key="key")


@pytest.fixture(autouse=True)
def fresh_breaker(monkeypatch):
    monkeypatch.setattr(api, "_primary_down_until", 0.0)


def respond(monkeypatch, data):
    response = Mock()
    response.json.return_value = data
    post = Mock(return_value=response)
    monkeypatch.setattr(api._http, "post", post)
    return post, response


def test_transcription_uses_documented_multipart_and_auto_language(monkeypatch):
    post, response = respond(monkeypatch, {"text": " Hallo world. "})
    assert api.transcribe_openrouter(b"wave", {}, "test-key") == "Hallo world."
    kwargs = post.call_args.kwargs
    assert json.loads(kwargs["data"]["provider"]) == {"zdr": True}  # never route to a provider that keeps the audio
    assert {name: value for name, value in kwargs["data"].items() if name != "provider"} == {"model": DEFAULT_STT_MODEL}
    filename, audio, mime = kwargs["files"]["file"]
    assert (filename, audio, mime) == ("audio.wav", b"wave", "audio/wav")
    assert kwargs["timeout"] == (15, 15)  # short audio: 15 s + half the audio length
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"
    response.close.assert_called_once()


def test_explicit_language_and_model_passed(monkeypatch):
    post, _ = respond(monkeypatch, {"text": "test"})
    api.transcribe_openrouter(b"wav", {"model": "custom/stt", "language": "de"}, "key")
    assert post.call_args.kwargs["data"]["language"] == "de"
    assert post.call_args.kwargs["data"]["model"] == "custom/stt"


@pytest.mark.parametrize("data", [{}, {"text": None}, {"text": 42}, {"error": "private payload"}, []])
def test_invalid_stt_response_rejected(monkeypatch, data):
    respond(monkeypatch, data)
    with pytest.raises(api.ResponseError):
        api.transcribe_openrouter(b"wav", {}, "key")


def test_empty_transcript_is_legitimate_silence(monkeypatch):
    respond(monkeypatch, {"text": " "})
    assert api.transcribe_openrouter(b"wav", {}, "key") == ""


def test_rewrite_settings_are_bounded_and_latency_oriented(monkeypatch):
    post, _ = respond(monkeypatch, {"choices": [{"finish_reason": "stop", "message": {"content": " Edited. "}}]})
    assert api.smooth("raw", "instructions", SMOOTHING) == "Edited."
    body = post.call_args.kwargs["json"]
    assert body["model"] == DEFAULT_SMOOTHING_MODEL
    assert body["reasoning"] == {"effort": "minimal", "exclude": True}
    assert body["provider"]["sort"] == "latency"
    assert body["provider"]["zdr"] is True
    assert body["temperature"] == 0  # a copy edit must not sample
    assert body["max_tokens"] == 4096
    assert post.call_args.kwargs["timeout"] == (5, 20)


@pytest.mark.parametrize("choice", [
    {"finish_reason": "length", "message": {"content": "truncated"}},
    {"finish_reason": "content_filter", "message": {"content": "filtered"}},
    {"message": {"content": None}}, {"message": {"content": "no", "refusal": "refused"}},
    {}, None,
])
def test_incomplete_rewrite_rejected(monkeypatch, choice):
    respond(monkeypatch, {"choices": [choice]})
    with pytest.raises(api.ResponseError):
        api.smooth("raw", "instructions", SMOOTHING)


def test_timeout_without_fallback_is_not_retried(monkeypatch):
    post = Mock(side_effect=requests.Timeout())
    monkeypatch.setattr(api._http, "post", post)
    with pytest.raises(requests.Timeout):
        api.transcribe_openrouter(b"wav", {}, "key")
    post.assert_called_once()


def test_client_error_does_not_retry_and_closes_response(monkeypatch):
    post, response = respond(monkeypatch, {})
    response.status_code = 400
    response.raise_for_status.side_effect = requests.HTTPError(response=response)
    with pytest.raises(requests.HTTPError):
        api.transcribe_openrouter(b"wav", {}, "key")
    post.assert_called_once()
    response.close.assert_called_once()


def test_http_hint_does_not_disclose_response_body():
    response = requests.Response()
    response.status_code, response._content = 500, b"secret transcript and private-key"
    hint = api.http_error_hint("STT", requests.HTTPError(response=response))
    assert "500" in hint and "secret" not in hint and "private-key" not in hint


def test_missing_key_fails_before_network(monkeypatch):
    post, _ = respond(monkeypatch, {})
    with pytest.raises(api.ResponseError):
        api.transcribe_openrouter(b"wav", {}, "")
    post.assert_not_called()


def test_stt_read_timeout_scales_with_audio_length(monkeypatch):
    post, _ = respond(monkeypatch, {"text": "x"})
    seconds = 60
    # Non-silent audio so even FLAC is large enough to scale upload timeout above the 15 s floor.
    pcm = bytes(range(256)) * (16000 * 2 * seconds // 256)
    wav = _make_wav(pcm)
    api.transcribe_openrouter(wav, {}, "key")
    upload, read = post.call_args.kwargs["timeout"]
    uploaded = post.call_args.kwargs["files"]["file"][1]
    assert upload == pytest.approx(max(15, len(uploaded) / (32 * 1024)))
    assert upload > 15
    assert read == 45
    api.transcribe_openrouter(b"not a wav", {}, "key")  # not a WAV: base timeout
    assert post.call_args.kwargs["timeout"] == (15, 15)


def test_long_audio_preserves_every_sample_and_returns_no_partial_result(monkeypatch):
    pcm = bytes(range(256)) * 41250  # 330 seconds at 16 kHz, 16 bit mono
    source = _make_wav(pcm)
    received = []
    def post(*args, **kwargs):
        assert kwargs["data"]["model"] == DEFAULT_STT_MODEL
        nframes, decoded = _decode_uploaded_pcm(kwargs["files"]["file"])
        assert nframes <= api.STT_CHUNK_SECONDS * 16000
        received.append(decoded)
        return Mock(json=lambda: {"text": str(len(received))})
    monkeypatch.setattr(api._http, "post", post)
    assert api.transcribe_openrouter(source, {}, "key") == "1\n\n2"
    assert b"".join(received) == pcm
    def fail(*args, **kwargs):
        raise requests.Timeout()
    monkeypatch.setattr(api._http, "post", fail)
    with pytest.raises(requests.Timeout):
        api.transcribe_openrouter(source, {}, "key")


def test_real_wav_uploads_as_smaller_lossless_flac_and_non_wav_stays_wav(monkeypatch):
    post, _ = respond(monkeypatch, {"text": "hello"})
    samples = [int(12000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(16000)]
    pcm = struct.pack(f"<{len(samples)}h", *samples)
    wav = _make_wav(pcm)
    assert api.transcribe_openrouter(wav, {}, "key") == "hello"
    filename, payload, mime = post.call_args.kwargs["files"]["file"]
    if sys.platform == "win32":
        assert (filename, mime) == ("audio.flac", "audio/flac")
        assert payload[:4] == b"fLaC"
        assert len(payload) < len(wav)
        assert _decode_flac_mf(payload) == pcm
    else:
        assert (filename, payload, mime) == ("audio.wav", wav, "audio/wav")

    assert api.transcribe_openrouter(b"not a wav", {}, "key") == "hello"
    assert post.call_args.kwargs["files"]["file"] == ("audio.wav", b"not a wav", "audio/wav")


@pytest.mark.parametrize("broken", [lambda *a, **k: None, Mock(side_effect=OSError("no encoder"))])
def test_missing_flac_encoder_silently_falls_back_to_wav(monkeypatch, broken):
    post, _ = respond(monkeypatch, {"text": "fallback ok"})
    monkeypatch.setattr(api, "_encode_pcm_flac", broken)
    wav = _make_wav(b"\x01\x00" * 8000)
    assert api.transcribe_openrouter(wav, {}, "key") == "fallback ok"
    assert post.call_args.kwargs["files"]["file"] == ("audio.wav", wav, "audio/wav")


def test_warm_only_warms_sessions_in_use(monkeypatch):
    primary_head = Mock(return_value=Mock())
    hedge_head = Mock(return_value=Mock())
    monkeypatch.setattr(api._http, "head", primary_head)
    monkeypatch.setattr(api._hedge_http, "head", hedge_head)
    monkeypatch.setattr(api, "HEDGE_SECONDS", None)
    api.warm()
    primary_head.assert_called_once()
    hedge_head.assert_not_called()

    monkeypatch.setattr(api, "HEDGE_SECONDS", 1.0)
    api.warm()
    assert primary_head.call_count == 2
    hedge_head.assert_called_once()


def test_warm_skips_session_while_request_is_in_flight(monkeypatch):
    in_flight, release = threading.Event(), threading.Event()
    primary_head = Mock(return_value=Mock())
    monkeypatch.setattr(api._http, "head", primary_head)
    monkeypatch.setattr(api, "HEDGE_SECONDS", None)
    def slow_post(*args, **kwargs):
        in_flight.set()
        assert release.wait(3)
        return Mock(json=lambda: {"text": "ok"})
    monkeypatch.setattr(api._http, "post", slow_post)
    result = []
    worker = threading.Thread(target=lambda: result.append(api.transcribe_openrouter(b"wav", {}, "key")))
    worker.start()
    try:
        assert in_flight.wait(3)
        api.warm()
        primary_head.assert_not_called()  # never touch _http while a job is using it
    finally:
        release.set()
        worker.join(3)
    assert result == ["ok"]
    api.warm()
    primary_head.assert_called_once()


def test_concurrent_request_uses_separate_session_while_warm_holds_primary(monkeypatch):
    # Use a fresh unpatched Session as _http so _session_post exercises the real pool path.
    primary = requests.Session()
    lock = threading.Lock()
    lock.acquire()
    try:
        with api._session_post(session=primary, lock=lock) as send:
            assert send.__self__ is not primary
            assert isinstance(send.__self__, requests.Session)
    finally:
        lock.release()
        primary.close()


def test_upload_timeout_is_bounded_and_uses_one_request(monkeypatch):
    timeouts = []
    def post(*args, **kwargs):
        timeouts.append(kwargs["timeout"])
        return Mock(json=lambda: {"text": "recovered"})
    monkeypatch.setattr(api._http, "post", post)
    api.transcribe_openrouter(bytes(24 * 1024 * 1024), {"fallback_model": "other/stt"}, "key")
    # No parallel hedge by default: one upload, bounded at 300 seconds.
    assert timeouts == [(300, 15)]


def test_retry_after_accepts_plain_numbers_only():
    def delay(value):
        return api._retry_after(Mock(headers={"Retry-After": value} if value else {}))
    assert delay("3") == 3 and delay(" 12 ") == 12
    assert delay(None) is None and delay("-1") is None and delay("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert delay("9" * 20) == float("inf")


class _KeyResponse:
    def __init__(self, status, data=None):
        self.status_code, self.data = status, data
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)
    def json(self):
        return self.data
    def close(self):
        pass


@pytest.mark.parametrize("failure, message", [
    (requests.exceptions.SSLError("certificate verify failed"), api.TLS_BLOCKED),
    (requests.ConnectionError("no route"), api.NO_CONNECTION),
    (requests.Timeout("slow"), api.NO_CONNECTION),
])
def test_key_check_names_the_network_cause(failure, message):
    def get(*args, **kwargs):
        raise failure
    assert api.check_key("sk-or-v1-abc", get=get) == message


@pytest.mark.parametrize("key", ["sk-or-v1-abc​", "sk-or v1", "sk-or-ä", ""])
def test_key_with_invisible_or_foreign_characters_is_not_sent(key):
    def get(*args, **kwargs):
        raise AssertionError("an invalid key must not be sent")
    assert api.check_key(key, get=get) == api.KEY_INVALID


def test_key_check_results():
    assert api.check_key("k", get=lambda *a, **k: _KeyResponse(200, {"data": {}})) == ""
    assert api.check_key("k", get=lambda *a, **k: _KeyResponse(401)) == api.KEY_REJECTED
    assert api.check_key("k", get=lambda *a, **k: _KeyResponse(502)) == api.UNAVAILABLE


def test_windows_certificate_store_is_used():
    import ssl
    import truststore
    assert ssl.SSLContext is truststore.SSLContext
