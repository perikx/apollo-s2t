"""
Apollo s2t - speech-to-text dictation by holding a key.

Hold a key -> speak -> release -> text is inserted into the active window.
Default hotkeys (all configurable in config.json):
  F8  = plain dictation
  F9  = dictation + polish (LLM cleans up grammar/fillers)
  F10 = dictation + structure as a prompt (LLM, project-aware via prompts/ profiles)

Flow: microphone -> Deepgram (STT) -> optional OpenRouter (LLM) ->
insert via clipboard + Ctrl+V into the focused text field.

Run 'python apollo.py --setup' for the interactive first-time setup.
"""

import base64
import ctypes
import io
import json
import logging
import os
import queue
import sys
import threading
import time
import urllib.parse
import wave

import numpy as np
import requests
import sounddevice as sd
import keyboard
import pyperclip
import websocket  # websocket-client (Streaming)

try:
    import winsound
    HAVE_WINSOUND = True
except Exception:
    HAVE_WINSOUND = False

try:
    import mouse  # optional: only used for insertion.click_to_paste
    HAVE_MOUSE = True
except Exception:
    HAVE_MOUSE = False

try:
    import pystray
    from PIL import Image, ImageDraw
    HAVE_TRAY = True
except Exception:
    HAVE_TRAY = False


# --------------------------------------------------------------------------
# Paths & logging
# --------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
LOG_PATH = os.path.join(BASE_DIR, "apollo.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("ptt")


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
def load_config():
    if not os.path.exists(CONFIG_PATH):
        log.error("config.json not found. Run 'python apollo.py --setup' "
                  "(or setup.bat) to create it.")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# Startup banner
# --------------------------------------------------------------------------
APP_NAME = "Apollo s2t"

_BANNER_LINES = [
    " █████╗ ██████╗  ██████╗ ██╗     ██╗      ██████╗ ",
    "██╔══██╗██╔══██╗██╔═══██╗██║     ██║     ██╔═══██╗",
    "███████║██████╔╝██║   ██║██║     ██║     ██║   ██║",
    "██╔══██║██╔═══╝ ██║   ██║██║     ██║     ██║   ██║",
    "██║  ██║██║     ╚██████╔╝███████╗███████╗╚██████╔╝",
    "╚═╝  ╚═╝╚═╝      ╚═════╝ ╚══════╝╚══════╝ ╚═════╝ ",
]
_SUN = (226, 220, 214, 208, 202, 166)  # yellow -> orange gradient (Apollo, the sun god)


def _setup_console():
    """Enable ANSI colors + UTF-8 output on a Windows console. Returns True if
    colored output is appropriate (stdout exists and is a real terminal)."""
    if sys.stdout is None:
        return False
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def render_banner(color=True):
    rows = []
    for i, line in enumerate(_BANNER_LINES):
        rows.append("\033[1;38;5;%dm%s\033[0m" % (_SUN[i], line) if color else line)
    sub = "        s2t  ·  speech → text, push-to-talk"
    sub = "\033[2;38;5;250m%s\033[0m" % sub if color else sub
    return "\n" + "\n".join(rows) + "\n" + sub + "\n"


def print_banner():
    """Print the banner, but never crash a windowless (pythonw) start."""
    color = _setup_console()
    if sys.stdout is None:
        return
    try:
        print(render_banner(color=color))
    except Exception:
        pass


_INSTANCE_MUTEX = None


def ensure_single_instance():
    """Exit if another Apollo instance is already running, so the global hotkeys
    aren't hooked (and fired) multiple times."""
    global _INSTANCE_MUTEX
    if os.name != "nt":
        return
    try:
        k = ctypes.windll.kernel32
        _INSTANCE_MUTEX = k.CreateMutexW(None, False, "Apollo_s2t_single_instance")
        if k.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            log.error("Another Apollo instance is already running - exiting this one.")
            sys.exit(0)
    except Exception as e:
        log.debug("single-instance check skipped: %s", e)


# --------------------------------------------------------------------------
# LLM prompts for F9 (polish) / F10 (structure as prompt)
# --------------------------------------------------------------------------
PROMPTS = {
    "polish": (
        "You are a precise editing assistant. You receive dictated raw text. "
        "Clean it up: fix grammar, punctuation, slips of the tongue and filler words, "
        "make it flow and read clearly. Keep the language, content, technical terms and "
        "meaning exactly. Do not add anything and do not shorten the meaning. "
        "Return ONLY the revised text - no preamble, no quotes, no comment."
    ),
    "prompt": (
        "You turn dictated raw text into a clear, well-structured prompt for an AI language "
        "model. Make the task, context and desired result understandable (with short bullet "
        "points where helpful). Do not invent requirements, keep the original intent. "
        "Return ONLY the finished prompt - no preamble, no quotes, no comment."
    ),
}

# Karpathy coding guidelines. These shape HOW the F10 prompt is written (concise,
# surgical, assumption-aware) - they must NOT be dumped verbatim into the output.
# Source: https://x.com/karpathy/status/2015883857489522876
KARPATHY_GUIDELINES = (
    "Shape the prompt in the spirit of these principles, but DO NOT copy them into the "
    "output or append a generic checklist - keep the prompt about the user's actual request: "
    "favor the smallest, most surgical change; make the task and desired result unambiguous; "
    "surface assumptions instead of guessing; and, only where it genuinely fits the task, add "
    "one short line on how success is verified."
)


def list_profiles(cfg, base_dir):
    """Available prompt profiles (markdown files in the profiles dir), sorted."""
    pp = cfg.get("prompt_profiles", {})
    d = os.path.join(base_dir, pp.get("dir", "prompts"))
    if not os.path.isdir(d):
        return []
    return sorted(os.path.splitext(f)[0] for f in os.listdir(d) if f.endswith(".md"))


def load_profile_text(cfg, base_dir):
    """Read the active profile's project context (markdown). Returns '' if none."""
    pp = cfg.get("prompt_profiles", {})
    name = pp.get("active", "default")
    if not name:
        return ""
    path = os.path.join(base_dir, pp.get("dir", "prompts"), name + ".md")
    if not os.path.exists(path):
        log.warning("Prompt profile '%s' not found (%s) - using no project context.", name, path)
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        log.warning("Could not read prompt profile '%s': %s", name, e)
        return ""


def build_prompt_system(cfg, base_dir):
    """Assemble the F10 system prompt: base + output-language + Karpathy + active profile."""
    pp = cfg.get("prompt_profiles", {})
    parts = [PROMPTS["prompt"]]
    # Force the prompt's output language so e.g. Chinese dictation still yields an
    # English prompt -> English code, comments and identifiers. "match"/"auto" = keep
    # the dictation's language.
    out_lang = (pp.get("output_language") or "english").strip()
    if out_lang.lower() in ("match", "auto", "same", "keep"):
        parts.append("Write the prompt in the same language as the dictation.")
    elif out_lang:
        parts.append(
            "Always write the prompt itself in %s, regardless of the language spoken in "
            "the dictation, so the target coding AI produces %s code, comments and "
            "identifiers." % (out_lang, out_lang)
        )
    if pp.get("include_karpathy", True):
        parts.append(KARPATHY_GUIDELINES)
    profile = load_profile_text(cfg, base_dir)
    if profile:
        parts.append("Project context - take this into account when building the prompt:\n" + profile)
    return "\n\n".join(parts)


# --------------------------------------------------------------------------
# Audio recording
# --------------------------------------------------------------------------
class Recorder:
    def __init__(self, samplerate, channels, device):
        self.samplerate = samplerate
        self.channels = channels
        self.device = device
        self._frames = []
        self._stream = None
        self._sample_count = 0
        self.on_chunk = None  # optional: bekommt rohe PCM-Bytes (Streaming)

    def _callback(self, indata, frames, time_info, status):
        if status:
            log.debug("Audio-Status: %s", status)
        self._sample_count += len(indata)
        cb = self.on_chunk
        if cb is not None:
            cb(indata.tobytes())
        else:
            self._frames.append(indata.copy())

    def start(self):
        self._frames = []
        self._sample_count = 0
        self._stream = sd.InputStream(
            samplerate=self.samplerate,
            channels=self.channels,
            dtype="int16",
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                log.warning("Error while closing the stream: %s", e)
            self._stream = None
        if not self._frames:
            return None
        return np.concatenate(self._frames, axis=0)

    @property
    def sample_count(self):
        return self._sample_count


def to_wav_bytes(data, samplerate, channels):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)  # int16 = 2 Bytes
        wf.setframerate(samplerate)
        wf.writeframes(data.tobytes())
    return buf.getvalue()


# --------------------------------------------------------------------------
# Deepgram (STT)
# --------------------------------------------------------------------------
def keyterms_for(dg):
    """Key terms to boost recognition of (names, jargon). Nova-3 only ('keyterm')."""
    terms = [t for t in (dg.get("keyterms") or []) if t]
    if not terms or "nova-3" not in dg.get("model", "nova-3"):
        return []
    return terms[:100]  # Deepgram caps keyterms at 100


def transcribe(wav_bytes, cfg):
    params = {
        "model": cfg.get("model", "nova-3"),
        "smart_format": "true" if cfg.get("smart_format", True) else "false",
    }
    if cfg.get("punctuate", True):
        params["punctuate"] = "true"
    lang = cfg.get("language")
    if lang:
        params["language"] = lang
    kt = keyterms_for(cfg)
    if kt:
        params["keyterm"] = kt

    resp = requests.post(
        "https://api.deepgram.com/v1/listen",
        params=params,
        headers={
            "Authorization": f"Token {cfg['api_key']}",
            "Content-Type": "audio/wav",
        },
        data=wav_bytes,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["results"]["channels"][0]["alternatives"][0]["transcript"].strip()


def transcribe_openrouter(wav_bytes, cfg, api_key):
    """Batch transcription via OpenRouter's audio API. Works with any OpenRouter
    transcription model (set cfg["model"]) - e.g. microsoft/mai-transcribe-1.5
    (100+ languages, as of 2026-06) or nvidia/parakeet-tdt-0.6b-v3 (EU, cheapest).
    Uses your OpenRouter key (same as F9/F10). Batch only, no streaming."""
    body = {
        "model": cfg.get("model", "microsoft/mai-transcribe-1.5"),
        "input_audio": {
            "data": base64.b64encode(wav_bytes).decode("ascii"),
            "format": "wav",
        },
    }
    lang = cfg.get("language")
    if lang:
        body["language"] = lang
    resp = requests.post(
        cfg.get("base_url", "https://openrouter.ai/api/v1/audio/transcriptions"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    resp.raise_for_status()
    return (resp.json().get("text") or "").strip()


# --------------------------------------------------------------------------
# Deepgram (STT) - live streaming over WebSocket
# --------------------------------------------------------------------------
# Audio is sent and transcribed live while you speak. On release only the last
# bit needs to be flushed -> minimal lag.
_FINISH = object()


class DeepgramLive:
    def __init__(self, cfg, samplerate, channels, interim=False):
        self.cfg = cfg
        self.samplerate = samplerate
        self.channels = channels
        self.interim = interim
        self.on_update = None  # optional: callback(transcript, is_final) for live typing
        self.ws = None
        self.transcripts = []
        self.error = None
        self._queue = queue.Queue()
        self._ws_ready = threading.Event()
        self._reader = None
        self._stop = False

    def _build_url(self):
        dg = self.cfg
        params = {
            "model": dg.get("model", "nova-3"),
            "smart_format": "true" if dg.get("smart_format", True) else "false",
            "encoding": "linear16",
            "sample_rate": str(self.samplerate),
            "channels": str(self.channels),
            "interim_results": "true" if self.interim else "false",
        }
        if dg.get("language"):
            params["language"] = dg["language"]
        if dg.get("punctuate", True):
            params["punctuate"] = "true"
        kt = keyterms_for(dg)
        if kt:
            params["keyterm"] = kt
        return "wss://api.deepgram.com/v1/listen?" + urllib.parse.urlencode(params, doseq=True)

    def open_async(self):
        """Connect in the background and then send the audio queue. This lets
        on_press return immediately; the handshake is hidden."""
        threading.Thread(target=self._connect_and_send, daemon=True).start()

    def _connect_and_send(self):
        try:
            self.ws = websocket.create_connection(
                self._build_url(),
                header=[f"Authorization: Token {self.cfg['api_key']}"],
                timeout=15,
            )
        except Exception as e:
            self.error = e
            self._ws_ready.set()
            return
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._ws_ready.set()
        while True:
            item = self._queue.get()
            if item is _FINISH:
                try:
                    self.ws.send('{"type":"Finalize"}')
                    self.ws.send('{"type":"CloseStream"}')
                except Exception:
                    pass
                break
            if item is None:
                break
            try:
                self.ws.send_binary(item)
            except Exception:
                break

    def _read_loop(self):
        while not self._stop:
            try:
                msg = self.ws.recv()
            except Exception:
                break
            if not msg or isinstance(msg, (bytes, bytearray)):
                if not msg:
                    break
                continue
            try:
                data = json.loads(msg)
            except Exception:
                continue
            if data.get("type") == "Results":
                try:
                    alt = data["channel"]["alternatives"][0]
                except (KeyError, IndexError):
                    continue
                text = (alt.get("transcript") or "").strip()
                is_final = bool(data.get("is_final"))
                # live typing: report every (non-empty) hypothesis immediately
                if self.on_update is not None and (text or is_final):
                    try:
                        self.on_update(text, is_final)
                    except Exception as e:
                        log.debug("on_update error: %s", e)
                if text and is_final:
                    self.transcripts.append(text)

    def send(self, pcm_bytes):
        self._queue.put(pcm_bytes)

    def finish(self, timeout=2.5):
        """Flush pending audio and briefly wait for the last finals."""
        self._ws_ready.wait(timeout=3)
        if self.error is not None:
            return ""
        self._queue.put(_FINISH)
        if self._reader is not None:
            self._reader.join(timeout=timeout)
        self._stop = True
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass
        return " ".join(self.transcripts).strip()

    def abort(self):
        """Discard the connection (e.g. when the recording was too short)."""
        self._stop = True
        self._queue.put(None)
        try:
            if self.ws is not None:
                self.ws.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# OpenRouter (polish / prompt structuring)
# --------------------------------------------------------------------------
_http = requests.Session()  # reused connection (keep-alive)


def http_error_hint(service, exc):
    """Turn an API error into a short, actionable one-line message."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return "%s: no response - check your internet connection." % service
    code = resp.status_code
    try:
        body = resp.text or ""
    except Exception:
        body = ""
    low = body.lower()
    if code in (401, 403):
        return "%s: API key rejected (HTTP %d) - check the key in config.json." % (service, code)
    if code == 402:
        return "%s: out of credit (HTTP 402) - top up your account." % service
    if code == 429:
        return "%s: rate limited (HTTP 429) - slow down or upgrade your plan." % service
    if code == 404 or (code == 400 and "model" in low):
        return "%s: model not found - fix the model slug in config.json." % service
    if code == 400 and ("language" in low or "lang" in low):
        return "%s: bad language code - check deepgram.language." % service
    return "%s: HTTP %d - %s" % (service, code, body[:160])


def smooth(text, system_prompt, cfg):
    resp = _http.post(
        cfg.get("base_url", "https://openrouter.ai/api/v1/chat/completions"),
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
            "X-Title": "Apollo-s2t",
        },
        json={
            "model": cfg["model"],
            "temperature": cfg.get("temperature", 0.3),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"].get("content")
    return (content or "").strip()  # None/empty -> caller falls back to the raw text


# --------------------------------------------------------------------------
# Live typing (word by word, with self-correction)
# --------------------------------------------------------------------------
class LiveTyper:
    """Type Deepgram hypotheses live into the focused field.

    To stay silent, only text is appended WHILE you speak (no backspaces) -> Windows
    plays no system sound. Only "stable" words are typed, i.e. all but the last word of
    an interim hypothesis (the last one may still change). Once a sentence part is final,
    the segment is reconciled ONCE (casing, punctuation) - this happens during the speech
    pause and only deletes text that was actually typed.

    With live_corrections=False this final reconcile is skipped too (fully backspace-free,
    but the casing stays "raw").
    """

    def __init__(self, type_delay=0.0, corrections=True):
        self.seg = ""             # exact typed text of the current segment (incl. any leading space)
        self.n_typed = 0          # number of words already typed in the segment
        self.committed_any = False
        self.typed_anything = False
        self.type_delay = type_delay
        self.corrections = corrections
        self._lock = threading.Lock()

    def update(self, transcript, is_final):
        with self._lock:
            words = transcript.split()
            if is_final:
                if self.corrections and transcript:
                    target = (" " if self.committed_any else "") + transcript
                    self._reconcile(target)           # one-time cleanup during the pause
                elif len(words) > self.n_typed:
                    self._append(words[self.n_typed:])  # backspace-free: only append the rest
                if self.seg.strip():
                    self.committed_any = True
                self.seg = ""
                self.n_typed = 0
                return
            # interim hypothesis: only append STABLE words (without the last, volatile one)
            stable = max(0, len(words) - 1)
            if stable > self.n_typed:
                self._append(words[self.n_typed:stable])
                self.n_typed = stable

    def _append(self, new_words):
        if not new_words:
            return
        chunk = " ".join(new_words)
        if self.seg or self.committed_any:
            chunk = " " + chunk            # separator to the previous word/segment
        keyboard.write(chunk, delay=self.type_delay)
        self.typed_anything = True
        self.seg += chunk

    def _reconcile(self, target):
        cur = self.seg
        n = min(len(cur), len(target))
        i = 0
        while i < n and cur[i] == target[i]:
            i += 1
        for _ in range(len(cur) - i):     # only deletes text that was actually typed
            keyboard.send("backspace")
        suffix = target[i:]
        if suffix:
            keyboard.write(suffix, delay=self.type_delay)
            self.typed_anything = True
        self.seg = target


# --------------------------------------------------------------------------
# Window focus (for insertion.target = "origin")
# --------------------------------------------------------------------------
def get_foreground_window():
    """Handle of the currently focused window (None if unavailable / non-Windows)."""
    if os.name != "nt":
        return None
    try:
        return ctypes.windll.user32.GetForegroundWindow()
    except Exception:
        return None


def focus_window(hwnd):
    """Best-effort: bring hwnd to the foreground so the next paste lands there.
    Uses AttachThreadInput to work around Windows' focus-stealing prevention."""
    if not hwnd or os.name != "nt":
        return False
    try:
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)  # SW_RESTORE
        fg = u.GetForegroundWindow()
        if fg == hwnd:
            return True
        cur = k.GetCurrentThreadId()
        t_fg = u.GetWindowThreadProcessId(fg, None)
        t_tgt = u.GetWindowThreadProcessId(hwnd, None)
        u.AttachThreadInput(cur, t_fg, True)
        u.AttachThreadInput(cur, t_tgt, True)
        u.SetForegroundWindow(hwnd)
        u.BringWindowToTop(hwnd)
        u.AttachThreadInput(cur, t_fg, False)
        u.AttachThreadInput(cur, t_tgt, False)
        return u.GetForegroundWindow() == hwnd
    except Exception as e:
        log.debug("focus_window failed: %s", e)
        return False


# --------------------------------------------------------------------------
# Insert text (clipboard + Ctrl+V)
# --------------------------------------------------------------------------
def paste_text(text, ins_cfg):
    restore = ins_cfg.get("restore_clipboard", True)
    delay = ins_cfg.get("restore_delay", 0.4)

    previous = None
    if restore:
        try:
            previous = pyperclip.paste()
        except Exception:
            previous = None

    pyperclip.copy(text)
    threading.Event().wait(0.05)  # brief pause so the clipboard is set before pasting
    keyboard.send("ctrl+v")

    if restore and previous is not None:
        def _restore():
            threading.Event().wait(delay)
            try:
                pyperclip.copy(previous)
            except Exception:
                pass
        threading.Thread(target=_restore, daemon=True).start()


# --------------------------------------------------------------------------
# Akustisches Feedback
# --------------------------------------------------------------------------
def beep(kind, enabled):
    if not enabled or not HAVE_WINSOUND:
        return
    try:
        if kind == "start":
            winsound.Beep(900, 70)
        elif kind == "stop":
            winsound.Beep(600, 70)
        elif kind == "ready":          # text loaded, waiting to be fired
            winsound.Beep(1100, 55)
            winsound.Beep(1350, 55)
        elif kind == "error":
            winsound.Beep(350, 160)
            winsound.Beep(280, 160)
    except Exception:
        pass


# --------------------------------------------------------------------------
# App-Logik
# --------------------------------------------------------------------------
class App:
    def __init__(self, config):
        self.cfg = config
        self.base_dir = BASE_DIR
        audio = config.get("audio", {})
        self.samplerate = audio.get("samplerate", 16000)
        self.channels = audio.get("channels", 1)
        self.recorder = Recorder(
            samplerate=self.samplerate,
            channels=self.channels,
            device=audio.get("device"),
        )
        self.stt_engine = config.get("stt_engine", "deepgram")
        self.streaming = config.get("deepgram", {}).get("mode", "streaming") == "streaming"
        if self.stt_engine == "openrouter":
            self.streaming = False  # OpenRouter transcription is batch-only
        ins = config.get("insertion", {})
        # "instant" (default) = paste straight into the focused field on release.
        # "armed"   = keep the text loaded on the clipboard; fire it yourself with
        #             Ctrl+V (always) or, with click_to_paste, on your next left click.
        self.insert_mode = ins.get("mode", "instant")
        self.click_to_paste = ins.get("click_to_paste", False)
        self.armed_timeout = ins.get("armed_timeout", 30)
        # "focused" = paste wherever focus is at insertion time (default).
        # "origin"  = remember the window focused when you pressed the key and paste
        #             back into it, even if you switched away meanwhile.
        self.insert_target = ins.get("target", "focused")
        self._origin_hwnd = None
        # Live typing (word by word) only for plain dictation (F8) and only in streaming.
        # Disabled in armed mode (insertion happens only when fired).
        self.insert_live = (ins.get("live", True) and self.streaming
                            and self.insert_mode != "armed")
        self.type_delay = ins.get("type_delay", 0.0)
        self.live_corrections = ins.get("live_corrections", False)
        self.beep_enabled = config.get("beep", True)
        self.min_samples = int(config.get("min_record_seconds", 0.3) * self.samplerate)
        self._lock = threading.Lock()
        self.recording = False
        self.active_mode = None
        self.live = None
        self.typer = None
        # armed-mode state
        self._pending_text = None
        self._click_handle = None
        self._disarm_timer = None
        self._arm_lock = threading.Lock()

    def _live_for(self, mode):
        return self.insert_live and mode == "dictate"

    def set_profile(self, name):
        self.cfg.setdefault("prompt_profiles", {})["active"] = name
        log.info("Active F10 prompt profile: %s", name)

    # ---- armed mode: load the text and wait for it to be fired ----------------
    def deliver_armed(self, text):
        """If you're still in the window you dictated from, paste right away (no
        click needed) - but keep the text on the clipboard so Ctrl+V still works.
        Otherwise load it and wait to be fired by a click or Ctrl+V."""
        if self._origin_hwnd and get_foreground_window() == self._origin_hwnd:
            try:
                pyperclip.copy(text)            # load it; do NOT restore afterwards
            except Exception as e:
                log.error("Clipboard copy failed: %s", e)
                beep("error", self.beep_enabled)
                return
            threading.Event().wait(0.05)
            keyboard.send("ctrl+v")
            log.info("Pasted into the window you dictated from; still on the clipboard "
                     "for Ctrl+V (%d chars).", len(text))
            return
        self.arm(text)

    def arm(self, text):
        """Load the text onto the clipboard and wait. The user fires it with Ctrl+V
        (always) or, if click_to_paste is on, on the next left click."""
        try:
            pyperclip.copy(text)
        except Exception as e:
            log.error("Clipboard copy failed, cannot load text: %s", e)
            beep("error", self.beep_enabled)
            return
        with self._arm_lock:
            self._cancel_arm_locked()           # replace any previous load
            self._pending_text = text
            armed_click = self.click_to_paste and self._arm_click_locked()
            self._disarm_timer = threading.Timer(self.armed_timeout, self.disarm)
            self._disarm_timer.daemon = True
            self._disarm_timer.start()
        beep("ready", self.beep_enabled)
        log.info("Loaded %d chars - %s, or press Ctrl+V.", len(text),
                 "click into a field to insert" if armed_click else "press Ctrl+V to insert")

    def _arm_click_locked(self):
        """Hook the next left-button release to fire one paste. Returns True if armed."""
        if not HAVE_MOUSE:
            log.warning("click_to_paste needs the 'mouse' package (pip install mouse). "
                        "Text is on the clipboard - use Ctrl+V.")
            return False

        def on_left_up():
            # offload to a thread so the mouse hook isn't blocked by the UIA check
            threading.Thread(target=self._try_fire_on_click, daemon=True).start()

        self._click_handle = mouse.on_button(on_left_up, buttons=(mouse.LEFT,),
                                              types=(mouse.UP,))
        return True

    def _try_fire_on_click(self):
        """Fire the loaded paste on the next click (after the focus settles)."""
        time.sleep(0.12)                    # let the click settle the focus first
        with self._arm_lock:
            if self._pending_text is None:
                return
            self._pending_text = None
            self._cancel_arm_locked()
        keyboard.send("ctrl+v")
        log.info("Fired armed text on click.")

    def _cancel_arm_locked(self):
        """Unhook the click handler and cancel the timeout. Caller holds _arm_lock."""
        if self._click_handle is not None and HAVE_MOUSE:
            try:
                mouse.unhook(self._click_handle)
            except Exception:
                pass
        self._click_handle = None
        if self._disarm_timer is not None:
            self._disarm_timer.cancel()
            self._disarm_timer = None

    def disarm(self):
        """Stop waiting for a click. The text stays on the clipboard for Ctrl+V."""
        with self._arm_lock:
            was_pending = self._pending_text is not None
            self._pending_text = None
            self._cancel_arm_locked()
        if was_pending:
            log.info("Armed text timed out (still on clipboard, Ctrl+V works).")

    def on_press(self, mode):
        with self._lock:
            if self.recording:
                return  # key auto-repeat or a second key -> ignore
            self.recording = True
            self.active_mode = mode
            # remember the window we started in (origin target + armed auto-paste)
            if self.insert_target == "origin" or self.insert_mode == "armed":
                self._origin_hwnd = get_foreground_window()
            beep("start", self.beep_enabled)
            try:
                if self.streaming:
                    live_typing = self._live_for(mode)
                    self.live = DeepgramLive(self.cfg["deepgram"], self.samplerate,
                                             self.channels, interim=live_typing)
                    if live_typing:
                        self.typer = LiveTyper(type_delay=self.type_delay,
                                               corrections=self.live_corrections)
                        self.live.on_update = self.typer.update
                    else:
                        self.typer = None
                    self.live.open_async()           # handshake in the background
                    self.recorder.on_chunk = self.live.send
                else:
                    self.recorder.on_chunk = None
                self.recorder.start()
            except Exception as e:
                log.error("Microphone could not start - is a mic connected and not in use? (%s)", e)
                self.recording = False
                self.active_mode = None
                self.recorder.on_chunk = None
                if self.live:
                    self.live.abort()
                    self.live = None
                self.typer = None
                beep("error", self.beep_enabled)
                return
            log.info("Recording started (mode: %s, %s%s)", mode,
                     "stream" if self.streaming else "batch",
                     ", live" if self._live_for(mode) else "")

    def on_release(self, mode):
        with self._lock:
            if not self.recording or self.active_mode != mode:
                return
            self.recording = False
            self.active_mode = None
            data = self.recorder.stop()
            samples = self.recorder.sample_count
            self.recorder.on_chunk = None
            live = self.live
            typer = self.typer
            self.live = None
            self.typer = None
            beep("stop", self.beep_enabled)
        t0 = time.time()
        threading.Thread(target=self._process,
                         args=(mode, samples, data, live, typer, t0),
                         daemon=True).start()

    def _process(self, mode, samples, data, live, typer, t0):
        try:
            if samples < self.min_samples:
                log.info("Recording too short or empty, ignored.")
                if live is not None:
                    live.abort()
                return

            # ---- live dictation (F8): text was already typed while you spoke ----
            if typer is not None:
                text = live.finish()  # flush the last words -> reader types them via on_update
                if live.error is not None:
                    log.error("Deepgram streaming failed (check your key/connection): %s", live.error)
                    beep("error", self.beep_enabled)
                    return
                if not typer.typed_anything:
                    log.warning("Nothing recognized.")
                    beep("error", self.beep_enabled)
                    return
                log.info("Live dictation done (%.0f ms after release): %s",
                         (time.time() - t0) * 1000, text)
                return

            # ---- otherwise: transcribe, optionally polish, insert once ----
            if self.streaming:
                text = live.finish() if live is not None else ""
                if live is not None and live.error is not None:
                    log.error("Deepgram streaming failed (check your key/connection): %s", live.error)
                    beep("error", self.beep_enabled)
                    return
            else:
                wav = to_wav_bytes(data, self.samplerate, self.channels)
                if self.stt_engine == "openrouter":
                    text = transcribe_openrouter(wav, self.cfg.get("openrouter_stt", {}),
                                                 self.cfg.get("smoothing", {}).get("api_key", ""))
                else:
                    text = transcribe(wav, self.cfg["deepgram"])

            if not text:
                log.warning("Empty transcript from Deepgram.")
                beep("error", self.beep_enabled)
                return
            log.info("STT (%.0f ms): %s", (time.time() - t0) * 1000, text)

            if mode in ("polish", "prompt"):
                try:
                    system_prompt = (build_prompt_system(self.cfg, self.base_dir)
                                     if mode == "prompt" else PROMPTS["polish"])
                    refined = smooth(text, system_prompt, self.cfg["smoothing"])
                    if refined:
                        text = refined
                        log.info("Refined (%s): %s", mode, text)
                except requests.HTTPError as e:
                    log.error("%s Using the raw dictation instead.", http_error_hint("OpenRouter", e))
                except requests.RequestException as e:
                    log.error("OpenRouter: network error (%s). Using the raw dictation.", e)
                except Exception as e:
                    log.error("Smoothing failed (%s). Using the raw dictation.", e)

            if self.insert_mode == "armed":
                self.deliver_armed(text)       # paste if in a field, else load and wait
            else:
                if self.insert_target == "origin" and self._origin_hwnd:
                    if focus_window(self._origin_hwnd):
                        time.sleep(0.12)       # let the window settle before pasting
                    else:
                        log.warning("Could not refocus origin window; pasting into current focus.")
                paste_text(text, self.cfg.get("insertion", {}))
                log.info("Inserted (%d chars, total %.0f ms after release).",
                         len(text), (time.time() - t0) * 1000)
        except requests.HTTPError as e:
            svc = "OpenRouter STT" if self.stt_engine == "openrouter" else "Deepgram"
            log.error(http_error_hint(svc, e))
            beep("error", self.beep_enabled)
        except requests.RequestException as e:
            svc = "OpenRouter STT" if self.stt_engine == "openrouter" else "Deepgram"
            log.error("%s: network error - %s", svc, e)
            beep("error", self.beep_enabled)
        except Exception as e:
            log.exception("Unexpected error while processing: %s", e)
            beep("error", self.beep_enabled)


# --------------------------------------------------------------------------
# Tray icon
# --------------------------------------------------------------------------
def make_tray_image():
    img = Image.new("RGB", (64, 64), (24, 24, 28))
    d = ImageDraw.Draw(img)
    d.ellipse((22, 8, 42, 36), fill=(240, 70, 70))      # microphone head
    d.rectangle((30, 36, 34, 48), fill=(240, 70, 70))   # stand
    d.rectangle((24, 48, 40, 52), fill=(240, 70, 70))   # base
    return img


def run_tray(on_quit, app):
    items = [pystray.MenuItem(APP_NAME + " running", None, enabled=False)]

    profiles = list_profiles(app.cfg, app.base_dir)
    if profiles:
        def make_select(name):
            return lambda icon, item: app.set_profile(name)
        profile_items = [
            pystray.MenuItem(
                name,
                make_select(name),
                checked=lambda item, n=name: app.cfg.get("prompt_profiles", {}).get("active", "default") == n,
                radio=True,
            )
            for name in profiles
        ]
        items.append(pystray.MenuItem("F10 prompt profile", pystray.Menu(*profile_items)))

    items.append(pystray.MenuItem("Quit", lambda icon, item: on_quit(icon)))
    icon = pystray.Icon("apollo", make_tray_image(), APP_NAME, menu=pystray.Menu(*items))
    icon.run()


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    print_banner()
    ensure_single_instance()
    config = load_config()
    app = App(config)

    hotkeys = config.get("hotkeys", {})
    mapping = {
        hotkeys.get("dictate", "f8"): "dictate",
        hotkeys.get("polish", "f9"): "polish",
        hotkeys.get("prompt", "f10"): "prompt",
    }

    def make_handler(mode):
        def handler(event):
            if event.event_type == keyboard.KEY_DOWN:
                app.on_press(mode)
            elif event.event_type == keyboard.KEY_UP:
                app.on_release(mode)
        return handler

    for key, mode in mapping.items():
        keyboard.hook_key(key, make_handler(mode), suppress=True)

    # Startup warnings
    if app.stt_engine == "deepgram":
        dg_key = config.get("deepgram", {}).get("api_key", "")
        if not dg_key or dg_key.startswith("YOUR_"):
            log.warning("No valid Deepgram key in config.json -> STT will fail. Run --setup.")
    or_key = config.get("smoothing", {}).get("api_key", "")
    if not or_key or or_key.startswith("YOUR_"):
        used_for = "STT, F9/F10" if app.stt_engine == "openrouter" else "F9/F10"
        log.warning("No valid OpenRouter key in config.json -> %s will fail. Run --setup.", used_for)

    log.info("=" * 60)
    log.info("%s running.", APP_NAME)
    for key, mode in mapping.items():
        label = {"dictate": "dictate", "polish": "dictate + polish",
                 "prompt": "dictate + as prompt"}[mode]
        log.info("  %-4s = %s", key.upper(), label)
    if app.stt_engine == "openrouter":
        log.info("STT engine: OpenRouter (%s, %s)",
                 config.get("openrouter_stt", {}).get("model", "microsoft/mai-transcribe-1.5"),
                 config.get("openrouter_stt", {}).get("language") or "auto")
    else:
        log.info("STT engine: deepgram %s (%s)", config["deepgram"].get("model"),
                 config["deepgram"].get("language", "auto"))
    log.info("Mode:       %s%s", "streaming" if app.streaming else "batch",
             ", live typing on dictate" if app.insert_live else "")
    log.info("LLM:        %s", config.get("smoothing", {}).get("model", "-"))
    log.info("F10 profile: %s", config.get("prompt_profiles", {}).get("active", "default"))
    if app.insert_mode == "armed":
        log.info("Insertion:  armed (paste if you stay; else Ctrl+V%s)",
                 " or click" if app.click_to_paste else "")
    if app.insert_target == "origin":
        log.info("Target:     origin window (pastes back where you started)")
    log.info("Hold -> speak -> release. Quit via tray icon.")
    log.info("=" * 60)

    def on_quit(icon=None):
        log.info("Quitting %s ...", APP_NAME)
        try:
            app.disarm()
        except Exception:
            pass
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        if icon is not None:
            icon.stop()
        else:
            os._exit(0)

    if HAVE_TRAY:
        try:
            run_tray(on_quit, app)
        except Exception as e:
            log.warning("Tray unavailable (%s). Running headless, Ctrl+C quits.", e)
            keyboard.wait()
    else:
        log.info("(No tray icon available - Ctrl+C quits.)")
        keyboard.wait()


# --------------------------------------------------------------------------
# Interactive setup wizard (python apollo.py --setup)
# --------------------------------------------------------------------------
def read_hotkey_press(action, default):
    """Let the user press the key they want. Enter keeps the default. suppress=True
    stops the key from reaching the console (so e.g. F7 doesn't open its history popup).
    Falls back to typed input if global key capture isn't available."""
    print("   %s key - press a key now (or Enter to keep [%s]): " % (action, default),
          end="", flush=True)
    try:
        while True:
            ev = keyboard.read_event(suppress=True)
            if ev.event_type != keyboard.KEY_DOWN:
                continue
            name = ev.name
            if name in ("enter", "esc"):
                print(default)
                return default
            print(name)
            return name
    except Exception:
        v = input("\n   (type a key name like f7 instead) [%s]: " % default).strip()
        return v.lower() or default


def run_setup():
    print_banner()
    print("Interactive setup - let's create your config.json.\n")

    if os.path.exists(CONFIG_PATH):
        if input("config.json already exists. Overwrite? [y/N] ").strip().lower() != "y":
            print("Aborted. Nothing changed.")
            return

    example = os.path.join(BASE_DIR, "config.example.json")
    with open(example, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    print("\n1) Speech-to-text engine")
    print("   deepgram   = Deepgram cloud; supports Chinese + live streaming; needs its own key")
    print("   openrouter = transcribe via OpenRouter (one key for STT + LLM); batch only")
    eng = input("   Engine [deepgram/openrouter] [%s]: " % cfg.get("stt_engine", "deepgram")).strip().lower()
    if eng in ("deepgram", "openrouter"):
        cfg["stt_engine"] = eng
    engine = cfg.get("stt_engine", "deepgram")

    step = 2
    if engine == "deepgram":
        print("\n%d) Deepgram key (speech-to-text)" % step)
        print("   Sign up for a free key ($200 credit): https://console.deepgram.com/signup")
        dg = input("   Deepgram API key: ").strip()
        if dg:
            cfg["deepgram"]["api_key"] = dg
        step += 1

    or_for = "STT + F9/F10 polish" if engine == "openrouter" else "F9/F10 polish/prompt"
    print("\n%d) OpenRouter key (for %s) - one key, any model" % (step, or_for))
    print("   Get a key: https://openrouter.ai/keys    Browse models: https://openrouter.ai/models")
    ork = input("   OpenRouter API key: ").strip()
    if ork:
        cfg["smoothing"]["api_key"] = ork
    model = input("   LLM model slug for F9/F10 [%s]: " % cfg["smoothing"]["model"]).strip()
    if model:
        cfg["smoothing"]["model"] = model
    step += 1

    if engine == "openrouter":
        os_cfg = cfg.setdefault("openrouter_stt", {})
        print("\n%d) Transcription model (OpenRouter audio slug)" % step)
        print("   microsoft/mai-transcribe-1.5  - 100+ languages incl. Chinese, auto-detect (~$0.006/min)")
        print("   nvidia/parakeet-tdt-0.6b-v3   - English/EU only, cheapest (~$0.0015/min)")
        cur_m = os_cfg.get("model", "microsoft/mai-transcribe-1.5")
        m = input("   Model [%s]: " % cur_m).strip()
        os_cfg["model"] = m or cur_m
        print("   Language: leave empty for auto-detect, or a code like 'de', 'en', 'zh'.")
        l = input("   Language [%s]: " % (os_cfg.get("language") or "auto")).strip()
        if l:
            os_cfg["language"] = l
        step += 1
    else:
        print("\n%d) Language" % step)
        print("   A single code is most accurate. Common: en, de, es, fr, it, pt, nl, ru, hi, ja,")
        print("   zh (Chinese Simplified), zh-Hant (Traditional), zh-HK (Cantonese).")
        print("   Or 'multi' for a mix of EN/DE/ES/FR/IT/PT/NL/RU/HI/JA (note: 'multi' excludes Chinese).")
        lang = input("   Language [%s]: " % cfg["deepgram"].get("language", "multi")).strip()
        if lang:
            cfg["deepgram"]["language"] = lang
        step += 1

    print("\n%d) Hotkeys - press the key you want for each (or Enter to keep the default)" % step)
    for action in ("dictate", "polish", "prompt"):
        cur = cfg["hotkeys"].get(action)
        cfg["hotkeys"][action] = read_hotkey_press(action, cur)
    step += 1

    print("\n%d) Text insertion" % step)
    print("   instant = paste into the focused field immediately (you must be in the field)")
    print("   armed   = keep the text loaded; fire it with Ctrl+V or your next click")
    ins = cfg.setdefault("insertion", {})
    m = input("   Mode [instant/armed] [%s]: " % ins.get("mode", "instant")).strip().lower()
    if m in ("instant", "armed"):
        ins["mode"] = m
    if ins.get("mode") == "armed":
        c = input("   Also insert on your next left click (needs 'mouse')? [y/N]: ").strip().lower()
        ins["click_to_paste"] = c in ("y", "yes", "j", "ja")

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

    print("\n[ok] Saved config.json")
    print("Next: run  start-debug.bat  (or  python apollo.py) and hold your dictate key.")
    print("Tip: customize the F10 prompt per project in the prompts/ folder.")


if __name__ == "__main__":
    if "--setup" in sys.argv:
        run_setup()
    else:
        main()
