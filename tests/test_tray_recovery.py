"""Windows native tray smoke test; isolated process, synthetic recovery files only."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]

# A child avoids conftest's pystray replacement while preventing all unrelated
# desktop and provider operations. Only its own icon/window/menu are created.
NATIVE_TRAY_SMOKE = r'''
import ctypes
from ctypes import wintypes
import sys
import threading
import time
import traceback
import types

import pystray

for name in ("keyboard", "sounddevice", "pyperclip", "mouse"):
    sys.modules[name] = types.ModuleType(name)

import apollo
import apollo_api

def forbidden(*args, **kwargs):
    raise AssertionError("Native tray smoke must not use hardware, clipboard, or network")

apollo_api._http.post = forbidden
apollo.beep = forbidden
apollo.HAVE_WINREG = False
apollo.BASE_DIR = sys.argv[1]
apollo.APP_NAME = "Apollo recovery smoke test"
assert apollo.pystray is pystray and apollo.HAVE_TRAY
app = apollo.App({"beep": False, "smoothing": {"api_key": "synthetic-test-key"}})
app.notify = lambda message: None
dispatched = []
app.recover = dispatched.append
errors = []

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetMenuItemCount.argtypes = (wintypes.HMENU,)
user32.GetMenuItemCount.restype = ctypes.c_int
user32.GetSubMenu.argtypes = (wintypes.HMENU, ctypes.c_int)
user32.GetSubMenu.restype = wintypes.HMENU
user32.GetMenuStringW.argtypes = (
    wintypes.HMENU, wintypes.UINT, wintypes.LPWSTR, ctypes.c_int, wintypes.UINT)
user32.GetMenuStringW.restype = ctypes.c_int
user32.GetMenuItemID.argtypes = (wintypes.HMENU, ctypes.c_int)
user32.GetMenuItemID.restype = wintypes.UINT
user32.IsWindow.argtypes = (wintypes.HWND,)
user32.IsWindow.restype = wintypes.BOOL

def label(menu, index):
    buffer = ctypes.create_unicode_buffer(512)
    assert user32.GetMenuStringW(menu, index, buffer, len(buffer), 0x400) > 0
    return buffer.value

def recovery_menu():
    menu, callbacks = app._tray._menu_handle
    for index in range(user32.GetMenuItemCount(menu)):
        if label(menu, index) == "Recover saved dictation":
            submenu = user32.GetSubMenu(menu, index)
            assert submenu
            return submenu, callbacks
    raise AssertionError("Recovery submenu missing from actual Windows menu")

def recovery_labels():
    menu, _ = recovery_menu()
    return [label(menu, index) for index in range(user32.GetMenuItemCount(menu))]

def run():
    try:
        apollo.run_tray(lambda icon: icon.stop(), app)
    except BaseException:
        errors.append(traceback.format_exc())

thread = threading.Thread(target=run, daemon=True, name="native-recovery-tray-smoke")
thread.start()
hwnd = None
try:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        assert not errors, errors
        icon = app._tray
        if icon is not None and icon.visible and getattr(icon, "_menu_handle", None):
            break
        time.sleep(0.02)
    else:
        raise AssertionError("Native tray did not become ready")
    assert isinstance(icon, pystray.Icon)
    hwnd = icon._hwnd
    assert user32.IsWindow(hwnd)
    assert recovery_labels() == ["No saved dictations"]

    audio = app.recovery.create(16000, 1, "dictate")
    audio.append(b"\x00\x00" * 8000)
    audio.finish()
    audio.update(state="failed")
    # Windows caches its menu until the application explicitly refreshes it.
    assert recovery_labels() == ["No saved dictations"]
    app.refresh_recovery_menu()
    assert len(recovery_labels()) == 1
    assert recovery_labels()[0].endswith("| dictate | Retry audio")

    app._busy_recordings.add(audio.id)
    app.refresh_recovery_menu()
    assert recovery_labels() == ["No saved dictations"]
    app._busy_recordings.remove(audio.id)

    text = app.recovery.create(16000, 1, "polish")
    text.append(b"\x00\x00" * 8000)
    text.finish()
    text.save_transcript("Synthetic recovery smoke test.", final=True)
    text.update(state="ready")
    app.refresh_recovery_menu()
    labels = recovery_labels()
    assert len(labels) == 2
    assert any(item.endswith("| polish | Copy text") for item in labels)
    assert any(item.endswith("| dictate | Retry audio") for item in labels)

    # Dispatch through the callbacks associated with real native menu item IDs.
    # Pystray invokes the app action with (icon, item), catching signature and
    # loop-variable capture regressions without copying text or sending audio.
    for expected_mode, expected_id in (("dictate", audio.id), ("polish", text.id)):
        menu, callbacks = recovery_menu()
        index = next(i for i in range(user32.GetMenuItemCount(menu))
                     if f"| {expected_mode} |" in label(menu, i))
        item_id = user32.GetMenuItemID(menu, index)
        assert 0 < item_id <= len(callbacks)
        callbacks[item_id - 1](icon)
        assert dispatched[-1] == expected_id
    assert dispatched == [audio.id, text.id]
finally:
    if app._tray is not None:
        app._tray.stop()
    thread.join(5)
    app.close()
assert not thread.is_alive(), "Test tray message loop did not exit"
assert not errors, errors
assert hwnd is not None and not user32.IsWindow(hwnd), "Test tray window was not destroyed"
print("native-tray-ok: empty, refreshed, busy-filtered, two correct callbacks, clean shutdown")
'''


@pytest.mark.skipif(os.name != "nt", reason="Uses an isolated native Windows tray icon")
def test_native_recovery_menu_refresh_callbacks_and_shutdown(tmp_path):
    process = subprocess.Popen(
        [sys.executable, "-c", NATIVE_TRAY_SMOKE, str(tmp_path)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        stdout, stderr = process.communicate(timeout=45)
    except subprocess.TimeoutExpired:
        # Windows venv Python can be a launcher with an interpreter child.
        # Terminate only this test's process tree, including that child/icon.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, timeout=10, check=False,
        )
        stdout, stderr = process.communicate(timeout=5)
        pytest.fail("Native tray smoke timed out; test process tree stopped.\n" + stdout + stderr)
    assert process.returncode == 0, stdout + stderr
    assert "native-tray-ok:" in stdout
