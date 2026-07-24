"""
Unit tests for the insertion logic - especially the new "hybrid" mode.

These run headless on any OS: the platform/hardware modules Apollo imports
(sounddevice, keyboard, pyperclip, websocket) are replaced with tiny fakes so we
can import apollo and drive App.deliver_hybrid() without a real desktop. The
Windows text-field detection (focused_is_editable) is stubbed per test to simulate
"in a field" / "not in a field" / "can't tell".

Run:  python tests/test_insertion.py       (or: pytest tests/test_insertion.py)
"""
import os
import sys
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --- fake the modules apollo imports that need real hardware / Windows ---------
class FakeClipboard(types.ModuleType):
    def __init__(self):
        super().__init__("pyperclip")
        self._data = ""
        self.copies = []

    def copy(self, s):
        self._data = s
        self.copies.append(s)

    def paste(self):
        return self._data


class FakeKeyboard(types.ModuleType):
    KEY_DOWN = "down"
    KEY_UP = "up"

    def __init__(self):
        super().__init__("keyboard")
        self.sent = []

    def send(self, combo):
        self.sent.append(combo)

    def hook_key(self, *a, **k):
        pass

    def wait(self, *a, **k):
        pass


def _empty_module(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


CLIP = FakeClipboard()
KB = FakeKeyboard()
sys.modules["pyperclip"] = CLIP
sys.modules["keyboard"] = KB
sys.modules["sounddevice"] = _empty_module("sounddevice", play=lambda *a, **k: None,
                                           InputStream=object, default=types.SimpleNamespace(device=[0, 0]))
sys.modules["websocket"] = _empty_module("websocket", WebSocketApp=object)
for opt in ("numpy", "requests"):
    try:
        __import__(opt)
    except Exception:  # pragma: no cover - only if the real dep is absent
        stub = _empty_module(opt)
        if opt == "requests":
            stub.HTTPError = type("HTTPError", (Exception,), {})
            stub.RequestException = type("RequestException", (Exception,), {})
            stub.post = lambda *a, **k: None
        sys.modules[opt] = stub

import apollo  # noqa: E402  (import after the stubs are in place)


def _app(mode="hybrid", streaming=False, live=True):
    cfg = {
        "audio": {"samplerate": 16000, "channels": 1, "device": None},
        "stt_engine": "openrouter",
        "deepgram": {"mode": "streaming" if streaming else "batch"},
        "smoothing": {}, "openrouter_stt": {}, "prompt_profiles": {},
        "beep": False,
        "min_record_seconds": 0.3,
        "insertion": {"mode": mode, "live": live, "restore_clipboard": True,
                      "restore_delay": 0.0, "armed_timeout": 5},
    }
    return apollo.App(cfg)


def _reset(previous="PREVIOUS"):
    KB.sent.clear()
    CLIP.copies.clear()
    CLIP._data = previous


# --- tests --------------------------------------------------------------------
def test_detection_safe_default_off_windows():
    # On this (non-Windows) box detection must be "unknown", never a false positive.
    assert apollo.focused_is_editable() is None


def test_hybrid_in_text_field_pastes_and_restores_clipboard():
    app = _app("hybrid")
    apollo.focused_is_editable = lambda: True
    _reset("PREVIOUS")
    app.deliver_hybrid("hello world")
    assert "ctrl+v" in KB.sent                 # pasted straight in
    assert "hello world" in CLIP.copies        # used the clipboard to paste
    time.sleep(0.1)
    assert CLIP._data == "PREVIOUS"            # ...but restored it -> no clutter
    assert app._pending_text is None           # nothing left armed


def test_hybrid_not_a_field_keeps_on_clipboard():
    app = _app("hybrid")
    apollo.focused_is_editable = lambda: False
    _reset("PREVIOUS")
    app.deliver_hybrid("keep me")
    assert "ctrl+v" not in KB.sent             # no blind paste when we know it's not a field
    assert CLIP._data == "keep me"             # left on the clipboard for Ctrl+V
    assert app._pending_text == "keep me"      # armed and waiting
    app.disarm()
    assert CLIP._data == "keep me"             # armed mode keeps it (no restore)


def test_hybrid_undetected_pastes_and_keeps_then_restores_on_expiry():
    app = _app("hybrid")
    apollo.focused_is_editable = lambda: None
    _reset("PREVIOUS")
    app.deliver_hybrid("both ways")
    assert "ctrl+v" in KB.sent                 # attempts the paste (in case we're in a field)
    assert CLIP._data == "both ways"           # and keeps it as a fallback
    assert app._pending_text == "both ways"
    app.disarm()                               # load expires
    time.sleep(0.1)
    assert CLIP._data == "PREVIOUS"            # previous clipboard restored


def test_hybrid_disables_live_typing():
    app = _app("hybrid", streaming=True, live=True)
    assert app.insert_live is False            # hybrid inserts once at the end, never live


def test_instant_still_pastes():
    app = _app("instant")
    _reset("OLD")
    app.insert_text("new text", t0=time.time())
    assert "ctrl+v" in KB.sent
    time.sleep(0.05)
    assert CLIP._data == "OLD"                  # instant + restore_clipboard cleans up


def test_paste_text_restores_previous_clipboard():
    _reset("OLD")
    apollo.paste_text("fresh", {"restore_clipboard": True, "restore_delay": 0.0})
    assert "ctrl+v" in KB.sent
    assert "fresh" in CLIP.copies
    time.sleep(0.05)
    assert CLIP._data == "OLD"


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print("  PASS", t.__name__)
        except AssertionError as e:
            failed += 1
            print("  FAIL", t.__name__, "->", e or "assertion failed")
        except Exception as e:  # pragma: no cover
            failed += 1
            print("  ERROR", t.__name__, "->", repr(e))
    print("\n%d passed, %d failed, %d total" % (len(tests) - failed, failed, len(tests)))
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
