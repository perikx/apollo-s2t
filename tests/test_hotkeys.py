"""
Unit tests for the hotkey handler - especially toggle mode (tap to start/stop).

Regression guard for the bug where the second F8 tap never stopped recording: the
old handler reset its auto-repeat guard only on key-up, which a suppressed global
hook does not deliver reliably, so it got stuck. The handler now debounces by time
and never depends on key-up.

Run:  python tests/test_hotkeys.py   (or: pytest tests/test_hotkeys.py)
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


# Stub the platform/hardware modules apollo imports so it loads headless.
sys.modules.setdefault("pyperclip", _stub("pyperclip", copy=lambda s: None, paste=lambda: ""))
sys.modules.setdefault("sounddevice", _stub("sounddevice", play=lambda *a, **k: None,
                                            InputStream=object,
                                            default=types.SimpleNamespace(device=[0, 0])))
sys.modules.setdefault("websocket", _stub("websocket", WebSocketApp=object))
if "keyboard" not in sys.modules:
    sys.modules["keyboard"] = _stub("keyboard", KEY_DOWN="down", KEY_UP="up",
                                    hook_key=lambda *a, **k: None, send=lambda *a, **k: None,
                                    wait=lambda *a, **k: None)
for opt in ("numpy", "requests"):
    try:
        __import__(opt)
    except Exception:
        s = _stub(opt)
        if opt == "requests":
            s.HTTPError = type("HTTPError", (Exception,), {})
            s.RequestException = type("RequestException", (Exception,), {})
        sys.modules[opt] = s

import apollo  # noqa: E402

DOWN = types.SimpleNamespace(event_type=apollo.keyboard.KEY_DOWN)
UP = types.SimpleNamespace(event_type=apollo.keyboard.KEY_UP)


class FakeApp:
    """Just enough of App to drive the handler: press/release flip recording state."""
    def __init__(self):
        self.recording = False
        self.active_mode = None
        self.events = []

    def on_press(self, mode):
        self.recording = True
        self.active_mode = mode
        self.events.append(("start", mode))

    def on_release(self, mode):
        self.recording = False
        self.active_mode = None
        self.events.append(("stop", mode))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_toggle_tap_starts_then_stops():
    app, clk = FakeApp(), Clock()
    h = apollo.make_key_handler(app, "dictate", toggle_mode=True, clock=clk)
    h(DOWN); h(UP)                       # tap 1
    assert app.recording is True
    clk.t += 1.0
    h(DOWN); h(UP)                       # tap 2
    assert app.recording is False
    assert [e[0] for e in app.events] == ["start", "stop"]


def test_toggle_stops_even_without_keyup():
    # The actual bug: key-up never arrives. The second tap must still stop.
    app, clk = FakeApp(), Clock()
    h = apollo.make_key_handler(app, "dictate", toggle_mode=True, clock=clk)
    h(DOWN)                              # tap 1, no key-up ever
    assert app.recording is True
    clk.t += 0.5
    h(DOWN)                              # tap 2, still no key-up
    assert app.recording is False        # would hang "on" before the fix


def test_toggle_ignores_autorepeat_burst():
    app, clk = FakeApp(), Clock()
    h = apollo.make_key_handler(app, "dictate", toggle_mode=True, clock=clk)
    h(DOWN)                              # start
    assert app.recording is True
    for _ in range(6):                  # holding -> fast auto-repeat key-downs
        clk.t += 0.03
        h(DOWN)
    assert app.recording is True         # not toggled off by auto-repeat
    assert app.events == [("start", "dictate")]


def test_toggle_switches_mode_while_recording():
    app, clk = FakeApp(), Clock()
    hd = apollo.make_key_handler(app, "dictate", toggle_mode=True, clock=clk)
    hp = apollo.make_key_handler(app, "polish", toggle_mode=True, clock=clk)
    hd(DOWN)                             # start dictate
    assert app.recording is True and app.active_mode == "dictate"
    clk.t += 1.0
    hp(DOWN)                             # a different mode key -> starts that mode
    assert app.active_mode == "polish"


def test_hold_mode_down_starts_up_stops():
    app = FakeApp()
    h = apollo.make_key_handler(app, "dictate", toggle_mode=False)
    h(DOWN)
    assert app.recording is True
    h(UP)
    assert app.recording is False


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
            print("  FAIL", t.__name__, "->", e or "assert failed")
        except Exception as e:
            failed += 1
            print("  ERROR", t.__name__, "->", repr(e))
    print("\n%d passed, %d failed, %d total" % (len(tests) - failed, failed, len(tests)))
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
