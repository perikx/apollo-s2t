"""The real Windows hook: keys arrive, blocked keys stop, and a reinstall puts Apollo first again."""
import ctypes
import os
import subprocess
import sys
import time
import pytest
import apollo_keyhook

F13_VK, F13_SCAN = 0x7C, 0x64  # no application uses F13
# Real hooks need an interactive desktop with keyboard input; CI runners may not have one.
pytestmark = pytest.mark.skipif(bool(os.environ.get("CI")), reason="needs an interactive desktop")

SWALLOW = r'''
import ctypes
from ctypes import wintypes
HOOKPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user = ctypes.WinDLL("user32")
user.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user.SetWindowsHookExW.restype = wintypes.HHOOK
user.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user.CallNextHookEx.restype = wintypes.LPARAM
def proc(code, wp, lp):
    if code == 0 and ctypes.cast(lp, ctypes.POINTER(wintypes.DWORD))[0] == 0x7C:
        return 1  # like Remote Desktop: the key never reaches older hooks
    return user.CallNextHookEx(None, code, wp, lp)
keep = HOOKPROC(proc)
print("ready" if user.SetWindowsHookExW(13, keep, None, 0) else "failed", flush=True)
user.SetTimer(None, 0, 15000, None)
msg = wintypes.MSG()
user.GetMessageW(ctypes.byref(msg), None, 0, 0)
'''


def press_f13():
    ctypes.windll.user32.keybd_event(F13_VK, F13_SCAN, 0, 0)
    ctypes.windll.user32.keybd_event(F13_VK, F13_SCAN, 2, 0)
    time.sleep(0.3)


@pytest.fixture
def hook(monkeypatch):
    monkeypatch.setattr(apollo_keyhook, "REINSTALL_SECONDS", 0.2)
    seen, front = [], [False]
    def callback(event):
        if event.scan_code == F13_SCAN:
            seen.append(event.event_type)
            return False  # block F13, as Apollo blocks its recording keys
        return True
    value = apollo_keyhook.KeyHook(callback, in_front=lambda: front[0])
    yield value, seen, front
    value.remove()


def test_hook_sees_and_blocks_its_key(hook):
    _, seen, _ = hook
    press_f13()
    assert seen == ["down", "up"]


def test_reinstall_wins_over_a_later_remote_desktop_hook(hook, tmp_path):
    value, seen, front = hook
    script = tmp_path / "swallow.py"
    script.write_text(SWALLOW)
    other = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    try:
        assert other.stdout.readline().strip() == "ready"
        press_f13()
        assert seen == []  # the later hook is first and eats the key
        front[0] = True
        deadline = time.monotonic() + 3
        while value.reinstalls == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        press_f13()
        assert seen == ["down", "up"]
    finally:
        other.kill()


def test_remote_desktop_check_never_raises():
    assert apollo_keyhook.remote_desktop_in_front() in (True, False)
