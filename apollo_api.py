"""OpenRouter STT and rewriting, Zero Data Retention only on both routes.

STT sends multipart audio, uses the primary first and falls back on failure.
"""
from array import array
import contextlib
import ctypes
import io
import json
import logging
import math
import os
import queue
import random
import threading
import time
import wave
import requests
import truststore
from apollo_config import DEFAULT_STT_MODEL, validate_vocabulary

# Trust the Windows certificate store. Virus scanners, company proxies and school
# networks often check HTTPS with their own root certificate; the certifi list
# alone rejects that connection with an SSL error.
truststore.inject_into_ssl()
log = logging.getLogger("apollo")

# Two pools with warm TLS connections. A Session is not thread-safe, so each
# session has a non-blocking lock: warm() skips a busy session, and concurrent
# callers borrow a clean session from _extra_sessions instead of racing _http.
_http = requests.Session()
_hedge_http = requests.Session()
_http_lock = threading.Lock()
_hedge_lock = threading.Lock()
_pool_lock = threading.Lock()
_extra_sessions = []
STT_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
REWRITE_URL = "https://openrouter.ai/api/v1/chat/completions"
REWRITE_TIMEOUT_SECONDS = 20
REWRITE_MAX_TOKENS = 4096  # incomplete rewrites fall back to the full raw transcript
STT_MAX_ATTEMPTS = 3
STT_RETRY_WINDOW_SECONDS = 30
STT_CHUNK_SECONDS = 300  # one request per five minutes; providers accept up to one hour
# None: use one model at a time and send the fallback only after a failure or
# timeout. A number starts a parallel hedge after that many seconds. A parallel
# hedge uploads the same audio twice, which can slow long recordings on a limited
# uplink and costs a second transcription.
HEDGE_SECONDS = None  # plus HEDGE_PER_AUDIO_SECOND for each second of audio
HEDGE_PER_AUDIO_SECOND = 0.0
BREAKER_SECONDS = 300
_primary_down_until = 0.0  # time.monotonic() until which new recordings try the fallback first


@contextlib.contextmanager
def _session_post(post=None, session=None, lock=None):
    """Yield a POST callable without touching a requests.Session from two threads at once."""
    if post is not None:
        yield post
        return
    session = _http if session is None else session
    lock = _http_lock if lock is None else lock
    if lock.acquire(blocking=False):
        try:
            yield session.post
        finally:
            lock.release()
        return
    if "post" in session.__dict__:
        # Tests monkeypatch _http.post / _hedge_http.post; never bypass that with a live Session.
        yield session.post
        return
    with _pool_lock:
        extra = _extra_sessions.pop() if _extra_sessions else requests.Session()
    try:
        yield extra.post
    finally:
        with _pool_lock:
            if len(_extra_sessions) < 4:
                _extra_sessions.append(extra)
            else:
                extra.close()


def _hedge_post(*args, **kwargs):
    with _session_post(session=_hedge_http, lock=_hedge_lock) as send:
        return send(*args, **kwargs)


def _warm_session(session, lock):
    if not lock.acquire(blocking=False):
        return
    try:
        session.head("https://openrouter.ai/api/v1/models", timeout=(3, 3)).close()
    except Exception:
        pass
    finally:
        lock.release()


def warm():
    """Open the TLS connection early; skip any session with a request in flight."""
    _warm_session(_http, _http_lock)
    if HEDGE_SECONDS is not None:
        _warm_session(_hedge_http, _hedge_lock)


KEY_REJECTED = "Der API-Schlüssel wurde abgelehnt. Bitte korrigieren und erneut versuchen."
KEY_INVALID = "Der Schlüssel enthält ungültige Zeichen. Bitte erneut von openrouter.ai/keys kopieren."
KEY_UNUSABLE = (KEY_REJECTED, KEY_INVALID)  # never save these keys
NO_CONNECTION = "Keine Verbindung zu openrouter.ai. Internet, Firewall, VPN und Proxy prüfen."
TLS_BLOCKED = ("Die sichere Verbindung zu openrouter.ai wurde blockiert. Prüfe Datum und Uhrzeit. "
               "Trage Apollo im Virenscanner als Ausnahme ein.")
UNAVAILABLE = "OpenRouter ist gerade nicht erreichbar. Bitte erneut versuchen."


def check_key(key, get=requests.get):
    """Read-only authentication check; no model inference, audio or billing.

    Return "" for a working key, otherwise a message that names the cause.
    """
    # A copied key can carry an invisible character; HTTP headers allow only ASCII.
    if not key or not key.isascii() or not key.isprintable() or " " in key:
        return KEY_INVALID
    try:
        response = get("https://openrouter.ai/api/v1/key", headers={"Authorization": "Bearer " + key}, timeout=(5, 15))
        try:
            if response.status_code in (401, 403):
                return KEY_REJECTED
            response.raise_for_status()
            data = response.json()
            if not isinstance(data.get("data"), dict): return "Schlüssel konnte nicht geprüft werden. Bitte erneut versuchen."
            return ""
        finally: response.close()
    except requests.exceptions.SSLError:  # a subclass of ConnectionError: check it first
        log.warning("Key check: the TLS connection to openrouter.ai failed (SSLError).")
        return TLS_BLOCKED
    except (requests.ConnectionError, requests.Timeout) as exc:
        log.warning("Key check: no connection to openrouter.ai (%s).", type(exc).__name__)
        return NO_CONNECTION
    except (requests.RequestException, ValueError) as exc:
        code = getattr(getattr(exc, "response", None), "status_code", None)
        log.warning("Key check: OpenRouter answered with an error (%s, HTTP %s).", type(exc).__name__, code)
        return UNAVAILABLE


class ResponseError(ValueError):
    """Malformed or incomplete output; callers must not paste it."""


class RetryCancelled(ResponseError):
    """The caller stopped waiting; no further request was sent."""


def _retry_after(response):
    """Numeric seconds only; inf if absurdly large, None if missing or not a plain number."""
    value = (response.headers.get("Retry-After") or "").strip()
    if not (value.isascii() and value.isdigit()):
        return None
    return float(value) if len(value) < 12 else math.inf


def _retryable(exc):
    code = getattr(getattr(exc, "response", None), "status_code", 0)
    return code == 429 or code >= 500 or isinstance(exc, (requests.Timeout, requests.ConnectionError))


def is_retryable(exc):
    """True for failures that can pass by themselves: rate limit, server error, timeout, no connection."""
    return _retryable(exc)


def _audio_seconds(wav_bytes):
    """Length from the byte count and the WAV header; 0 if it is not a WAV."""
    try:
        with wave.open(io.BytesIO(wav_bytes)) as w:
            return max(0, len(wav_bytes) - 44) / (w.getframerate() * w.getnchannels() * w.getsampwidth())
    except (wave.Error, EOFError, ZeroDivisionError):
        return 0


def quiet_cut(frames, rate, channels, window_seconds=5.):
    """How many 16-bit sample frames to keep of `frames`: cut at the quietest 100 ms near its end.

    A part boundary inside a spoken word loses that word. The sender cuts into a pause instead,
    and the next part starts where this one stopped, so no sample is dropped or repeated.
    """
    step = max(1, rate // 10) * channels
    samples = array("h", frames) if len(frames) % 2 == 0 else array("h", frames[:-1])
    first = max(len(samples) // 2, len(samples) - int(rate * window_seconds) * channels)
    candidates = range(first, len(samples) - step + 1, step)
    if not candidates:
        return max(1, len(samples) // channels)
    cut = min(candidates, key=lambda i: sum(abs(v) for v in samples[i:i + step]))
    return max(1, (cut + step // 2) // channels)


def http_error_hint(service, exc):
    """Never log response bodies: they can echo dictation or credentials."""
    response = getattr(exc, "response", None)
    if response is None:
        return f"{service}: connection or audio upload failed. Retry the saved audio. Short recordings may still work."
    code = response.status_code
    hints = {
        400: "Invalid request. Check the model, language and model options in config.json.",
        401: "API key rejected. Set a new key in Settings.",
        402: "OpenRouter credit or spending limit. Check your balance and API-key limit.",
        403: "Access denied. Check your key permissions and provider access.",
        404: "Model or endpoint not found. Check the model ID in config.json; the model may also have no Zero Data Retention endpoint.",
        408: "Request timed out. Try a shorter recording.",
        413: "Recording too large. Try a shorter recording.",
        429: "OpenRouter or its upstream provider rejected the request due to a rate/capacity limit. Retry the saved recording later.",
    }
    return f"{service}: HTTP {code}. " + hints.get(code, "Provider error. Try again later.")


_MF_LOCK = threading.Lock()
_MF = None  # lazily initialized Windows Media Foundation handles, or False when unavailable


def _mf_init():
    """Bind the built-in Windows 10/11 Media Foundation FLAC encoder lazily, with zero bundled DLLs."""
    global _MF
    if _MF is not None:
        return _MF or None
    with _MF_LOCK:
        if _MF is not None:
            return _MF or None
        if os.name != "nt":
            _MF = False
            return None
        try:
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

            # Use private DLL handles so our argtypes never mutate global ctypes.windll / oledll.
            ole32_raw = ctypes.WinDLL("ole32")
            ole32 = ctypes.OleDLL("ole32")
            mfplat = ctypes.OleDLL("mfplat")
            mfrw = ctypes.OleDLL("mfreadwrite")
            shlwapi = ctypes.WinDLL("shlwapi")

            ole32_raw.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
            ole32_raw.CoInitializeEx.restype = ctypes.c_long
            ole32_raw.CoUninitialize.argtypes = ()
            ole32_raw.CoUninitialize.restype = None
            ole32.CLSIDFromString.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(GUID))
            ole32.CLSIDFromString.restype = ctypes.HRESULT
            shlwapi.SHCreateMemStream.argtypes = (ctypes.c_void_p, wintypes.UINT)
            shlwapi.SHCreateMemStream.restype = ctypes.c_void_p
            mfplat.MFStartup.argtypes = (wintypes.ULONG, wintypes.DWORD)
            mfplat.MFStartup.restype = ctypes.HRESULT
            mfplat.MFCreateMFByteStreamOnStream.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
            mfplat.MFCreateMFByteStreamOnStream.restype = ctypes.HRESULT
            mfplat.MFCreateMediaType.argtypes = (ctypes.POINTER(ctypes.c_void_p),)
            mfplat.MFCreateMediaType.restype = ctypes.HRESULT
            mfplat.MFCreateMemoryBuffer.argtypes = (wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))
            mfplat.MFCreateMemoryBuffer.restype = ctypes.HRESULT
            mfplat.MFCreateSample.argtypes = (ctypes.POINTER(ctypes.c_void_p),)
            mfplat.MFCreateSample.restype = ctypes.HRESULT
            mfrw.MFCreateSinkWriterFromURL.argtypes = (
                wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
            )
            mfrw.MFCreateSinkWriterFromURL.restype = ctypes.HRESULT

            mfplat.MFStartup(0x00020070, 1)  # MF_VERSION (Win7+), MFSTARTUP_NOSOCKET

            def guid(text):
                value = GUID()
                ole32.CLSIDFromString(text, ctypes.byref(value))
                return value

            guids = {
                "major": guid("{48eba18e-f8c9-4687-bf11-0a74c9f96a8f}"),         # MF_MT_MAJOR_TYPE
                "subtype": guid("{f7e34c9a-42e8-4714-b74b-cb29d72c35e5}"),       # MF_MT_SUBTYPE
                "channels": guid("{37e48bf5-645e-4c5b-89de-ada9e29b696a}"),      # MF_MT_AUDIO_NUM_CHANNELS
                "rate": guid("{5faeeae7-0290-4c31-9e8a-c534f68d9dba}"),          # MF_MT_AUDIO_SAMPLES_PER_SECOND
                "bits": guid("{f2deb57f-40fa-4764-aa33-ed4f2d1ff669}"),          # MF_MT_AUDIO_BITS_PER_SAMPLE
                "block_align": guid("{322de230-9eeb-43bd-ab7a-ff412251541d}"),   # MF_MT_AUDIO_BLOCK_ALIGNMENT
                "avg_bps": guid("{1aab75c8-cfef-451c-ab95-ac034b8e1731}"),       # MF_MT_AUDIO_AVG_BYTES_PER_SECOND
                "independent": guid("{c9173739-5e56-461c-b713-46fb995cb95f}"),   # MF_MT_ALL_SAMPLES_INDEPENDENT
                "audio": guid("{73647561-0000-0010-8000-00AA00389B71}"),         # MFMediaType_Audio
                "pcm": guid("{00000001-0000-0010-8000-00AA00389B71}"),           # MFAudioFormat_PCM
                "flac": guid("{0000F1AC-0000-0010-8000-00AA00389B71}"),          # MFAudioFormat_FLAC
            }
            _MF = (GUID, wintypes, ole32_raw, mfplat, mfrw, shlwapi, guids)
            return _MF
        except Exception:
            _MF = False
            return None


def _com_call(pointer, index, *argtypes):
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtable[index])


def _com_release(pointer):
    if pointer and getattr(pointer, "value", pointer):
        vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])(pointer)


def _encode_pcm_flac(pcm, rate, channels, width=2):
    """Encode 16-bit PCM bytes to a complete FLAC stream in memory, or return None if unavailable."""
    if not pcm or width != 2 or channels not in (1, 2) or rate <= 0 or len(pcm) > 0x7FFFFFFF:
        return None
    mf = _mf_init()
    if mf is None:
        return None
    GUID, wintypes, ole32_raw, mfplat, mfrw, shlwapi, g = mf
    co_initialized = ole32_raw.CoInitializeEx(None, 0) >= 0  # COINIT_MULTITHREADED; RPC_E_CHANGED_MODE is fine
    objects = []
    try:
        istream = ctypes.c_void_p(shlwapi.SHCreateMemStream(None, 0))
        if not istream.value:
            return None
        objects.append(istream)
        bytestream = ctypes.c_void_p()
        mfplat.MFCreateMFByteStreamOnStream(istream, ctypes.byref(bytestream))
        objects.append(bytestream)
        writer = ctypes.c_void_p()
        mfrw.MFCreateSinkWriterFromURL("audio.flac", bytestream, None, ctypes.byref(writer))
        objects.append(writer)

        block_align = channels * width
        avg_bps = rate * block_align

        def media_type(subtype):
            mt = ctypes.c_void_p()
            mfplat.MFCreateMediaType(ctypes.byref(mt))
            objects.append(mt)
            set_guid = _com_call(mt, 24, ctypes.POINTER(GUID), ctypes.POINTER(GUID))
            set_u32 = _com_call(mt, 21, ctypes.POINTER(GUID), wintypes.UINT)
            set_guid(mt, ctypes.byref(g["major"]), ctypes.byref(g["audio"]))
            set_guid(mt, ctypes.byref(g["subtype"]), ctypes.byref(subtype))
            set_u32(mt, ctypes.byref(g["channels"]), channels)
            set_u32(mt, ctypes.byref(g["rate"]), rate)
            set_u32(mt, ctypes.byref(g["bits"]), width * 8)
            set_u32(mt, ctypes.byref(g["block_align"]), block_align)
            set_u32(mt, ctypes.byref(g["avg_bps"]), avg_bps)
            set_u32(mt, ctypes.byref(g["independent"]), 1)
            return mt

        out_type = media_type(g["flac"])
        in_type = media_type(g["pcm"])
        stream_index = wintypes.DWORD()
        _com_call(writer, 3, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD))(
            writer, out_type, ctypes.byref(stream_index))  # AddStream
        _com_call(writer, 4, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p)(
            writer, stream_index, in_type, None)  # SetInputMediaType
        _com_call(writer, 5)(writer)  # BeginWriting

        duration = (len(pcm) // block_align) * 10_000_000 // rate
        buf = ctypes.c_void_p()
        mfplat.MFCreateMemoryBuffer(len(pcm), ctypes.byref(buf))
        try:
            ptr = ctypes.c_void_p()
            _com_call(buf, 3, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_void_p)(
                buf, ctypes.byref(ptr), None, None)  # Lock
            ctypes.memmove(ptr.value, pcm, len(pcm))
            _com_call(buf, 4)(buf)  # Unlock
            _com_call(buf, 6, wintypes.DWORD)(buf, len(pcm))  # SetCurrentLength

            sample = ctypes.c_void_p()
            mfplat.MFCreateSample(ctypes.byref(sample))
            try:
                _com_call(sample, 42, ctypes.c_void_p)(sample, buf)  # AddBuffer
                _com_call(sample, 36, ctypes.c_longlong)(sample, 0)  # SetSampleTime
                _com_call(sample, 38, ctypes.c_longlong)(sample, duration)  # SetSampleDuration
                _com_call(writer, 6, wintypes.DWORD, ctypes.c_void_p)(writer, stream_index, sample)  # WriteSample
            finally:
                _com_release(sample)
        finally:
            _com_release(buf)

        _com_call(writer, 11)(writer)  # Finalize

        seek = _com_call(istream, 5, ctypes.c_longlong, wintypes.DWORD, ctypes.POINTER(ctypes.c_ulonglong))
        pos = ctypes.c_ulonglong()
        seek(istream, 0, 2, ctypes.byref(pos))  # STREAM_SEEK_END
        size = pos.value
        if size <= 0:
            return None
        seek(istream, 0, 0, ctypes.byref(pos))  # STREAM_SEEK_SET
        out = ctypes.create_string_buffer(size)
        read_n = wintypes.ULONG()
        _com_call(istream, 3, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG))(
            istream, out, size, ctypes.byref(read_n))  # Read
        data = out.raw[:read_n.value]
        return data if read_n.value == size and data[:4] == b"fLaC" else None
    except Exception:
        return None
    finally:
        for obj in reversed(objects):
            _com_release(obj)
        if co_initialized:
            ole32_raw.CoUninitialize()


def wav_to_flac(wav_bytes):
    """Losslessly compress a 16-bit PCM WAV into FLAC bytes, or return None to fall back to WAV."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as audio:
            channels = audio.getnchannels()
            width = audio.getsampwidth()
            rate = audio.getframerate()
            nframes = audio.getnframes()
            if nframes <= 0 or width != 2 or channels not in (1, 2) or rate <= 0:
                return None
            pcm = audio.readframes(nframes)
        if len(pcm) != nframes * channels * width:
            return None
        flac = _encode_pcm_flac(pcm, rate, channels, width)
        return flac if flac and flac[:4] == b"fLaC" and len(flac) < len(wav_bytes) else None
    except Exception:
        return None


def _post(url, key, body, timeout, post=None):
    if not key:
        raise ResponseError("Missing OpenRouter API key. Run setup.bat.")
    with _session_post(post) as send:
        response = send(
            url, headers={"Authorization": f"Bearer {key}", "X-Title": "Apollo-s2t"},
            json=body, timeout=(5, timeout),
        )
    try:
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or "error" in data:
            raise ResponseError("OpenRouter returned an invalid response.")
        return data
    finally:
        response.close()


def _post_stt(key, model, wav_bytes, language, provider, timeout, post=None):
    """Multipart STT upload: lossless FLAC when available, otherwise WAV."""
    if not key:
        raise ResponseError("Missing OpenRouter API key. Run setup.bat.")
    fields = {"model": model}
    if language:
        fields["language"] = language
    if provider:
        # Multipart fields are strings; the API decodes this JSON object.
        fields["provider"] = json.dumps(provider, separators=(",", ":"))
    flac = wav_bytes if wav_bytes[:4] == b"fLaC" else wav_to_flac(wav_bytes)
    if flac:
        files = {"file": ("audio.flac", flac, "audio/flac")}
        payload_size = len(flac)
    else:
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        payload_size = len(wav_bytes)
    upload_timeout = max(15, min(300, payload_size / (32 * 1024)))
    with _session_post(post) as send:
        response = send(
            STT_URL, headers={"Authorization": f"Bearer {key}", "X-Title": "Apollo-s2t"},
            data=fields, files=files, timeout=(upload_timeout, timeout),
        )
    try:
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or "error" in data:
            raise ResponseError("OpenRouter returned an invalid response.")
        return data
    finally:
        response.close()


def transcribe_openrouter(wav_bytes, cfg, key, *, on_retry=None, on_fallback=None, on_attempt=None, wait=None):
    """Transcribe with the primary model; fall back to another vendor when it fails.

    On the first request, 429, timeout, connection error and 5xx switch to the fallback
    at once. Later 429s retry the same model (Retry-After honored, 30 s window).
    With HEDGE_SECONDS set, a request with no answer after that delay also goes to the
    other model; the first transcript wins. After a primary failure new recordings try
    the fallback first for BREAKER_SECONDS. ``on_retry`` gets
    (next_attempt_number, delay_seconds); ``wait(seconds)`` can return True to cancel
    (e.g. threading.Event.wait). ``on_fallback`` gets the fallback model when its
    transcript is used. ``on_attempt`` gets (model, ok, seconds) for every finished request.
    """
    if _audio_seconds(wav_bytes) > STT_CHUNK_SECONDS:
        transcripts = []
        with wave.open(io.BytesIO(wav_bytes)) as audio:
            rate, width, channels = audio.getframerate(), audio.getsampwidth(), audio.getnchannels()
            while audio.tell() < audio.getnframes():
                if wait and wait(0):
                    raise RetryCancelled("Transcription cancelled; saved audio can be retried later.")
                start = audio.tell()
                frames = audio.readframes(rate * STT_CHUNK_SECONDS)
                if width == 2 and audio.tell() < audio.getnframes():
                    # Cut at the quietest 100 ms in the last five seconds.
                    keep = quiet_cut(frames, rate, channels)
                    frames = frames[:keep * channels * width]
                    audio.setpos(start + keep)
                part = io.BytesIO()
                with wave.open(part, "wb") as output:
                    output.setparams(audio.getparams())
                    output.writeframes(frames)
                transcripts.append(transcribe_openrouter(part.getvalue(), cfg, key, on_retry=on_retry,
                    on_fallback=on_fallback, on_attempt=on_attempt, wait=wait))
        return "\n\n".join(text for text in transcripts if text)
    primary = cfg.get("model", DEFAULT_STT_MODEL)
    fallback = cfg.get("fallback_model")
    language = cfg.get("language")
    vocabulary = validate_vocabulary(cfg.get("vocabulary", []))
    seconds = _audio_seconds(wav_bytes)
    read_timeout = min(300, 15 + 0.5 * seconds)
    upload_bytes = wav_to_flac(wav_bytes) or wav_bytes
    first, other = (fallback, primary) if fallback and time.monotonic() < _primary_down_until else (primary, fallback)

    def request(model, post=None):
        global _primary_down_until
        # ZDR only: providers may not keep the audio or the transcript.
        provider = {"zdr": True}
        # Only the documented MAI 2 Azure integration is enabled. Keep unsupported
        # models usable; never turn the list into transcript replacements.
        if vocabulary and model == "microsoft/mai-transcribe-2":
            provider["options"] = {"azure": {"phraseList": {"phrases": vocabulary}}}
        t0, ok = time.monotonic(), False
        try:
            text = _post_stt(key, model, upload_bytes, language, provider, read_timeout, post).get("text")
            if not isinstance(text, str):
                raise ResponseError("OpenRouter returned no valid transcript field.")
            ok = True
            return text.strip()
        except Exception as exc:
            if model == primary and _retryable(exc):
                _primary_down_until = time.monotonic() + BREAKER_SECONDS
            raise
        finally:
            if on_attempt is not None:
                try:
                    on_attempt(model, ok, time.monotonic() - t0)
                except Exception:
                    pass

    def announce(model):
        if model == fallback and on_fallback is not None:
            on_fallback(model)

    sent = [first]

    def first_request():
        if not other:
            announce(first)
            return request(first)
        if HEDGE_SECONDS is None:
            # One upload at a time. The outer retry loop sends the fallback after
            # a failure or timeout, so normal recordings do not pay for two uploads.
            announce(first)
            return request(first)
        results = queue.Queue()
        def go(model, post):
            try:
                results.put((model, request(model, post), None))
            except Exception as exc:
                results.put((model, None, exc))
        def spawn(model, post=None):
            threading.Thread(target=go, args=(model, post), daemon=True).start()
        spawn(first)
        delay = HEDGE_SECONDS + HEDGE_PER_AUDIO_SECOND * seconds
        try:
            if delay <= 0:
                raise queue.Empty  # race: start the second model now, even if the first already failed
            item = results.get(timeout=delay)
        except queue.Empty:
            sent.append(other)
            spawn(other, _hedge_post)
            item = results.get()
        errors = {}
        while True:
            model, text, exc = item
            if exc is None:
                announce(model)
                return text  # a slower request keeps running in its daemon thread
            errors[model] = exc
            if len(errors) == len(sent):
                raise errors.get(primary, exc)
            item = results.get()

    model = first
    deadline = time.monotonic() + STT_RETRY_WINDOW_SECONDS
    for attempt in range(1, STT_MAX_ATTEMPTS + 1):
        try:
            return first_request() if attempt == 1 else request(model)
        except (requests.RequestException, ResponseError) as exc:
            if attempt == 1 and len(sent) > 1:
                model = primary  # both models failed: only a 429 of the primary (the raised error) is retried
            nxt = other if attempt == 1 and other and len(sent) == 1 else model
            code = getattr(getattr(exc, "response", None), "status_code", 0)
            # A malformed reply is worth one try on the other model, never a same-model retry.
            switchable = _retryable(exc) or (isinstance(exc, ResponseError) and nxt != model)
            if (attempt == STT_MAX_ATTEMPTS or not switchable
                    or (nxt == model and code != 429)):
                raise
            delay = 0  # a different model does not owe the old one's Retry-After
            if nxt == model:
                delay = _retry_after(exc.response)
                if delay is None:
                    delay = 2 ** attempt + random.uniform(0, 0.5)
                # Never shorten a provider's Retry-After to fit our local budget.
                if time.monotonic() + delay > deadline:
                    raise
            if on_retry is not None:
                on_retry(attempt + 1, delay)
            if (wait or time.sleep)(delay):
                raise RetryCancelled("Transcription retry cancelled; saved audio can be retried later.") from None
            if nxt == model and time.monotonic() > deadline:
                raise
            if nxt != model:
                announce(nxt)
            model = nxt


def smooth(text, system_prompt, cfg):
    body = {
        "model": cfg["model"],
        "temperature": 0,
        "max_tokens": REWRITE_MAX_TOKENS,
        "provider": {"sort": "latency", "zdr": True},
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": text}],
        "reasoning": {"effort": "minimal", "exclude": True},
    }
    data = _post(REWRITE_URL, cfg["api_key"], body, REWRITE_TIMEOUT_SECONDS)
    try:
        choice = data["choices"][0]
        if choice.get("finish_reason") not in ("stop", None):
            raise ResponseError("Rewrite was truncated or blocked. Keeping the original transcript.")
        message = choice["message"]
        content = message.get("content")
        if message.get("refusal") or not isinstance(content, str):
            raise ResponseError("No valid rewrite. Keeping the original transcript.")
        return content.strip()
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ResponseError("Malformed rewrite response. Keeping the original transcript.") from exc
