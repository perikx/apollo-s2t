"""The live glass captures at the full rate only while something happens."""
import pytest
from PySide6.QtCore import QObject

import apollo_glass as glass


class Stop(Exception):
    pass


class Clock:
    def __init__(self):
        self.now = 100.

    def time(self):
        return self.now

    def sleep(self, seconds):
        raise Stop  # end the capture loop after one pass


@pytest.fixture
def desk(monkeypatch):
    """A fake desktop: one color behind the glass, the input age and a clock."""
    clock = Clock()
    desk = type("Desk", (), {})(); desk.clock, desk.color, desk.input_age = clock, 1, 60.
    monkeypatch.setattr(glass, "time", type("Time", (), {"monotonic": clock.time, "perf_counter": clock.time, "sleep": clock.sleep}))
    monkeypatch.setattr(glass, "_window_rect", lambda hwnd: (0, 0, 4, 4))
    monkeypatch.setattr(glass, "_grab", lambda x, y, width, height: bytes([desk.color])*(width*height*4))
    monkeypatch.setattr(glass, "_input_age", lambda: desk.input_age)
    return desk


def capture(backdrop, desk, at):
    desk.clock.now = at
    payload = backdrop.capture_live()
    backdrop.pending = False  # the Qt thread took the frame
    return payload


def settled(desk):
    """A glass after its first captures: the wide capture after the first move has shrunk again."""
    backdrop = glass.Backdrop(QObject()); backdrop.hwnd, backdrop.dpr = 1, 1.
    assert capture(backdrop, desk, 99.) is not None and capture(backdrop, desk, 99.5) is not None
    return backdrop


def test_still_desktop_drops_to_the_idle_rate_and_a_change_restores_the_full_rate(desk):
    backdrop = settled(desk)
    assert capture(backdrop, desk, 100.) is None and not backdrop.idle  # within STILL of the last change
    assert backdrop.due == pytest.approx(100.+glass.FRAME-.002)
    assert capture(backdrop, desk, 100.6) is None and backdrop.idle
    assert backdrop.due == pytest.approx(100.6+glass.IDLE_FRAME-.002)
    desk.color = 2
    assert capture(backdrop, desk, 100.9) is not None and not backdrop.idle  # the change shows, the full rate is back
    assert backdrop.due == pytest.approx(100.9+glass.FRAME-.002)


def test_input_or_a_move_keeps_the_full_rate(desk, monkeypatch):
    backdrop = settled(desk)
    desk.input_age = .3
    capture(backdrop, desk, 105.); assert not backdrop.idle
    desk.input_age = 60.
    capture(backdrop, desk, 106.); assert backdrop.idle
    monkeypatch.setattr(glass, "_window_rect", lambda hwnd: (10, 0, 14, 4))
    capture(backdrop, desk, 106.2); assert not backdrop.idle


def test_input_wakes_an_idle_glass_at_once(desk):
    class Fake:
        def __init__(self, name, idle):
            self.name, self.idle, self.due = name, idle, desk.clock.now+10
        def capture_live(self):
            taken.append(self.name)
    taken, live = [], glass._Live()
    live.backdrops = {Fake("idle", True), Fake("waiting", False)}
    with pytest.raises(Stop): live.run()
    assert taken == []  # nothing is due and nobody typed
    desk.input_age = .05
    with pytest.raises(Stop): live.run()
    assert taken == ["idle"]  # the full-rate glass keeps its own schedule
