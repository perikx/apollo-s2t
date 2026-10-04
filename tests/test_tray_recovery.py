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
from ctypes import wintypes as w
import sys, threading, time, traceback, types
import pystray
for name in ("keyboard", "sounddevice", "pyperclip", "mouse"):
    sys.modules[name] = types.ModuleType(name)
import apollo
from apollo_i18n import set_language
apollo.BASE_DIR = sys.argv[1]
app = apollo.App({"beep": False})
errors = []
user = ctypes.windll.user32
user.GetMenuItemCount.argtypes = (w.HMENU,)
user.GetMenuStringW.argtypes = (w.HMENU, w.UINT, w.LPWSTR, ctypes.c_int, w.UINT)
user.GetMenuItemID.argtypes = (w.HMENU, ctypes.c_int)
user.IsWindow.argtypes = (w.HWND,)
def labels():
    menu, callbacks = app._tray._menu_handle
    values = []
    for index in range(user.GetMenuItemCount(menu)):
        text = ctypes.create_unicode_buffer(256)
        user.GetMenuStringW(menu, index, text, 256, 0x400)
        values.append(text.value)
    return values
def run():
    try: apollo.run_tray(lambda icon: (app.close(), icon.stop()), app)
    except BaseException: errors.append(traceback.format_exc())
thread = threading.Thread(target=run, daemon=True)
thread.start(); hwnd = None
try:
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        assert not errors, errors
        if app._tray and app._tray.visible and app._tray._menu_handle: break
        time.sleep(.02)
    else: raise AssertionError("Tray did not become ready")
    hwnd = app._tray._hwnd
    assert user.IsWindow(hwnd)
    for language, expected in (("en", ["Show apollo s2t", "Quit"]), ("zh", ["显示 apollo s2t", "退出"]), ("de", ["apollo s2t öffnen", "Beenden"])):
        app.update_preferences({"ui_language": language})
        assert labels() == expected
    menu, callbacks = app._tray._menu_handle
    callbacks[user.GetMenuItemID(menu, 0)-1](app._tray)
    assert app.ui_events.get_nowait() == ("open", None)
    assert app.ui_events.empty()
    menu, callbacks = app._tray._menu_handle  # dispatch refreshes the native menu
    callbacks[user.GetMenuItemID(menu, 1)-1](app._tray)
    thread.join(timeout=10)
    assert app._closing.is_set()
finally:
    if app._tray: app._tray.stop()
    thread.join(timeout=10); app.close()
assert not thread.is_alive() and not errors, errors
assert hwnd and not user.IsWindow(hwnd)
print("native-tray-ok: two localized actions, Show callback, Quit callback, clean shutdown")
'''


@pytest.mark.skipif(os.name != "nt", reason="Uses an isolated native Windows tray icon")
def test_native_minimal_tray_languages_callbacks_and_shutdown(tmp_path):
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
