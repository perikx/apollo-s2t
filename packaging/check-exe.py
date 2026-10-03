"""Windows packaging acceptance: source assets, native Qt startup and cancellation.

Run after building: python packaging/check-exe.py
No microphone, API request, global key hook or private configuration is used.
"""
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from PyInstaller.archive.readers import CArchiveReader

root = Path(__file__).resolve().parents[1]
exe = root / 'dist/apollo.exe'
archive = CArchiveReader(str(exe))
pyz = next(name for name in archive.toc if name.endswith('.pyz'))
modules = archive.open_embedded_archive(pyz).toc
assert all(name in modules for name in ('apollo_overlay', 'apollo_models', 'apollo_recovery', 'apollo_design', 'apollo_widgets', 'apollo_setup'))
assert 'config.json' not in archive.toc
assert not any(Path(name).name.lower() == 'icuuc.dll' for name in archive.toc), 'Qt must use native Windows ICU'
import hashlib
assert hashlib.sha256(archive.extract('assets\\apollo.png')).digest() == hashlib.sha256((root/'assets/apollo.png').read_bytes()).digest()
assert 'THIRD_PARTY_NOTICES.md' in archive.toc
assert 'tkinter' not in modules
assert any('QtWidgets.pyd' in name for name in archive.toc)
assert any(name.endswith('qwindows.dll') for name in archive.toc)
print('Frozen Qt UI and Windows platform plugin included; Tk and private config excluded.', flush=True)
kernel, user = ctypes.windll.kernel32, ctypes.windll.user32
user.SetProcessDPIAware()
kernel.OpenMutexW.argtypes = (w.DWORD, w.BOOL, w.LPCWSTR)
kernel.OpenMutexW.restype = w.HANDLE
kernel.CloseHandle.argtypes = (w.HANDLE,)
kernel.OpenProcess.argtypes = (w.DWORD, w.BOOL, w.DWORD)
kernel.OpenProcess.restype = w.HANDLE
kernel.QueryFullProcessImageNameW.argtypes = (w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD))
user.GetWindowThreadProcessId.argtypes = (w.HWND, ctypes.POINTER(w.DWORD))
user.GetWindowTextW.argtypes = (w.HWND, w.LPWSTR, ctypes.c_int)
user.GetClassNameW.argtypes = (w.HWND, w.LPWSTR, ctypes.c_int)
user.IsWindowVisible.argtypes = (w.HWND,)
user.GetWindowRect.argtypes = (w.HWND, ctypes.POINTER(w.RECT))
user.PostMessageW.argtypes = (w.HWND, w.UINT, w.WPARAM, w.LPARAM)
callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
user.EnumWindows.argtypes = (callback_type, w.LPARAM)

with tempfile.TemporaryDirectory(prefix='apollo-package-check-') as directory:
    folder = Path(directory)
    target = folder / 'apollo.exe'
    shutil.copy2(exe, target)
    env = os.environ.copy()
    env.pop('OPENROUTER_API_KEY', None)
    process = subprocess.Popen([str(target)], cwd=folder, env=env)
    windows = []
    def collect(hwnd, _):
        pid = w.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return True
        try:
            path, size = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
            if not kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                return True
            if Path(path.value).resolve() != target.resolve():
                return True
            title, kind = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(256)
            user.GetWindowTextW(hwnd, title, 256)
            user.GetClassNameW(hwnd, kind, 256)
            windows.append((hwnd, title.value, kind.value, bool(user.IsWindowVisible(hwnd))))
        finally:
            kernel.CloseHandle(handle)
        return True
    callback = callback_type(collect)
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            windows.clear()
            user.EnumWindows(callback, 0)
            visible = [h for h, title, kind, shown in windows if kind.startswith('Qt') and shown and 'Einrichten' in title]
            if any('Unhandled exception' in title for h,title,kind,shown in windows):
                messages=[]
                for h,title,kind,shown in windows:
                    if 'Unhandled exception' not in title: continue
                    @callback_type
                    def read_child(child,_):
                        text=ctypes.create_unicode_buffer(4096)
                        user.GetWindowTextW(child,text,4096)
                        if text.value: messages.append(text.value)
                        return True
                    user.EnumChildWindows.argtypes=(w.HWND,callback_type,w.LPARAM)
                    user.EnumChildWindows(h,read_child,0)
                    user.PostMessageW(h,0x10,0,0)
                process.wait(timeout=15)
                raise AssertionError('Frozen app error: '+repr(messages))
            if visible:
                break
            if process.poll() is not None:
                raise AssertionError('Frozen app exited before showing UI')
            time.sleep(.3)
        assert visible, 'Frozen UI did not become visible'
        time.sleep(.5)
        rect = w.RECT()
        user.GetWindowRect(visible[0], ctypes.byref(rect))
        assert rect.right-rect.left >= 620 and rect.bottom-rect.top >= 460
        user.PostMessageW(visible[0], 0x10, 0, 0)
        assert process.wait(timeout=30) == 0
        assert not (folder / 'config.json').exists()
        print('Frozen Apollo first-run Qt setup and cancel without side effects: PASS; no audio/API/paste.', flush=True)
    finally:
        if process.poll() is None:
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=15)
            process.wait(timeout=15)
