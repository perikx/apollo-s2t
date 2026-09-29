"""Isolated desktop doubles. Real requests/numpy; no hardware or network in tests."""
import sys
from pathlib import Path
import types

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for name in ("keyboard", "sounddevice", "pyperclip"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["keyboard"].KEY_DOWN = "down"
sys.modules["keyboard"].KEY_UP = "up"
sys.modules["pystray"] = None  # never connect to a real desktop while collecting tests
import apollo
ORIGINAL_FOCUSED_IS_EDITABLE = apollo.focused_is_editable


class Clipboard:
    def __init__(self):
        self.text, self.sequence, self.copies = "PREVIOUS", 0, []

    def copy(self, text):
        self.text = text
        self.sequence += 1
        self.copies.append(text)

    def paste(self):
        return self.text


class Timer:
    def __init__(self, interval, function, args=(), kwargs=None):
        self.interval, self.function, self.args, self.kwargs = interval, function, args, kwargs or {}
        self.cancelled, self.daemon = False, False

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self, even_if_cancelled=False):
        if even_if_cancelled or not self.cancelled:
            self.function(*self.args, **self.kwargs)


@pytest.fixture(autouse=True)
def desktop(monkeypatch):
    clipboard, sent, timers = Clipboard(), [], []
    def timer(*args, **kwargs):
        value = Timer(*args, **kwargs)
        timers.append(value)
        return value
    monkeypatch.setattr(apollo, "pyperclip", clipboard)
    monkeypatch.setattr(apollo.keyboard, "send", sent.append, raising=False)
    monkeypatch.setattr(apollo, "_clip_sequence", lambda: clipboard.sequence)
    monkeypatch.setattr(apollo, "_CLIP_GENERATION", 0)
    monkeypatch.setattr(apollo, "get_foreground_window", lambda: "window-a")
    monkeypatch.setattr(apollo, "focus_window", lambda hwnd: True)
    monkeypatch.setattr(apollo, "focused_is_editable", lambda: None)
    monkeypatch.setattr(apollo.threading, "Timer", timer)
    monkeypatch.setattr(apollo, "beep", lambda *args: None)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    import apollo_api
    def no_network(*args, **kwargs):
        raise AssertionError("Tests must not make network requests")
    monkeypatch.setattr(apollo_api._http, "post", no_network)
    return types.SimpleNamespace(clip=clipboard, sent=sent, timers=timers)


@pytest.fixture
def app():
    value = apollo.App({"beep": False, "smoothing": {"api_key": "test-key"}})
    yield value
    value.close()
