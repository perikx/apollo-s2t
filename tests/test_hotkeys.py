"""Preserve tap/hold regressions, including lost suppressed key-up events."""
from types import SimpleNamespace
import apollo

DOWN, UP = SimpleNamespace(event_type="down"), SimpleNamespace(event_type="up")


class FakeApp:
    recording = False
    active_mode = None
    def __init__(self):
        self.events = []
    def on_press(self, mode):
        self.recording, self.active_mode = True, mode
        self.events.append(("start", mode))
    def on_release(self, mode, **kw):
        self.recording, self.active_mode = False, None
        self.events.append(("stop", mode))


def test_toggle_tap_starts_then_stops():
    app, clock = FakeApp(), [1.0]
    handler = apollo.make_key_handler(app, "dictate", True, clock=lambda: clock[0])
    handler(DOWN); handler(UP)
    assert app.recording
    clock[0] += 1
    handler(DOWN); handler(UP)
    assert not app.recording
    assert app.events == [("start", "dictate"), ("stop", "dictate")]


def test_toggle_stops_even_without_keyup():
    app, clock = FakeApp(), [1.0]
    handler = apollo.make_key_handler(app, "dictate", True, clock=lambda: clock[0])
    handler(DOWN)
    clock[0] += 0.5
    handler(DOWN)
    assert not app.recording


def test_toggle_ignores_autorepeat_burst():
    app, clock = FakeApp(), [1.0]
    handler = apollo.make_key_handler(app, "dictate", True, clock=lambda: clock[0])
    handler(DOWN)
    for _ in range(6):
        clock[0] += 0.03
        handler(DOWN)
    assert app.events == [("start", "dictate")]


def test_toggle_delegates_other_mode_to_app():
    app = FakeApp()
    apollo.make_key_handler(app, "dictate", True)(DOWN)
    apollo.make_key_handler(app, "polish", True)(DOWN)
    assert app.events[-1] == ("start", "polish")


def test_hold_mode_down_starts_up_stops():
    app = FakeApp()
    handler = apollo.make_key_handler(app, "dictate", False)
    handler(DOWN)
    assert app.recording
    handler(UP)
    assert not app.recording
