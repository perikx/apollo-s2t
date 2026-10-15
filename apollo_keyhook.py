"""Low-level keyboard hook that stays in front of Remote Desktop's own hook.

Windows calls the most recently installed WH_KEYBOARD_LL hook first. A Remote Desktop
client (mstsc, msrdc) installs its own hook when it gets the focus and sends every key
to the remote computer without passing it on. Apollo then never sees F8. While such a
window is in front, this hook installs itself again every second, so it is first again.
"""
import ctypes
import os
import threading
from ctypes import wintypes
from types import SimpleNamespace

WH_KEYBOARD_LL = 13
WM_QUIT, WM_TIMER = 0x0012, 0x0113
KEY_DOWN_MESSAGES = (0x0100, 0x0104)  # WM_KEYDOWN, WM_SYSKEYDOWN
LLKHF_INJECTED = 0x10
VK_PACKET = 0xE7
REINSTALL_SECONDS = 1.0
# Remote Desktop Connection, the Windows App / Remote Desktop client and Citrix.
REMOTE_PROCESSES = ("mstsc.exe", "msrdc.exe", "msrdcw.exe", "wfica32.exe", "cdviewer.exe")
REMOTE_CLASSES = ("TscShellContainerClass", "RAIL_WINDOW")  # full desktop and RemoteApp windows

LRESULT = wintypes.LPARAM
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


def _user32():
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
    user.SetWindowsHookExW.restype = wintypes.HHOOK
    user.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user.CallNextHookEx.restype = LRESULT
    user.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user.SetTimer.argtypes = (wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p)
    user.SetTimer.restype = ctypes.c_size_t
    user.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    return user


def remote_desktop_in_front(user=None):
    """True if the foreground window belongs to a Remote Desktop or Citrix client."""
    try:
        user = user or _user32()
        hwnd = user.GetForegroundWindow()
        if not hwnd:
            return False
        name = ctypes.create_unicode_buffer(64)
        user.GetClassNameW(hwnd, name, 64)
        if name.value in REMOTE_CLASSES:
            return True
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.restype = wintypes.HANDLE
        process = kernel.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not process:
            return False
        try:
            path, size = ctypes.create_unicode_buffer(520), wintypes.DWORD(520)
            if not kernel.QueryFullProcessImageNameW(wintypes.HANDLE(process), 0, path, ctypes.byref(size)):
                return False
            return os.path.basename(path.value).lower() in REMOTE_PROCESSES
        finally:
            kernel.CloseHandle(wintypes.HANDLE(process))
    except Exception:
        return False


class KeyHook:
    """Calls callback(event) for each key; callback returns False to block the key.

    event has scan_code and event_type ("down" or "up"), like the keyboard library.
    """

    def __init__(self, callback, in_front=remote_desktop_in_front):
        self.callback, self.in_front = callback, in_front
        self.reinstalls = 0
        self._handle = None
        self._thread_id = None
        self._proc = HOOKPROC(self._on_key)  # keep a reference: Windows calls it
        self._ready = threading.Event()
        self._error = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="apollo-keyhook")
        self._thread.start()
        self._ready.wait(5)
        if self._error is not None or self._handle is None:
            raise OSError("Could not install the keyboard hook.")

    def _on_key(self, code, message, data):
        try:
            if code == 0:
                key = ctypes.cast(data, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                # Like the keyboard library: skip Unicode text input and the fake injected Alt.
                if key.vkCode != VK_PACKET and key.flags & (LLKHF_INJECTED | 0x20) != LLKHF_INJECTED | 0x20:
                    event = SimpleNamespace(scan_code=key.scanCode or -key.vkCode,
                                            event_type="down" if message in KEY_DOWN_MESSAGES else "up")
                    if self.callback(event) is False:
                        return 1
        except Exception:
            pass  # never break the keyboard of the whole system
        return self._user.CallNextHookEx(None, code, message, data)

    def _install(self):
        handle = self._user.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        if not handle:
            raise OSError("SetWindowsHookExW failed.")
        # The new hook is first in the chain before the old one goes; no key is lost or seen twice,
        # because this thread handles the hook calls one at a time.
        old, self._handle = self._handle, handle
        if old:
            self._user.UnhookWindowsHookEx(old)

    def _run(self):
        try:
            self._user = _user32()
            self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
            self._install()
            self._user.SetTimer(None, 0, int(REINSTALL_SECONDS * 1000), None)
        except Exception as exc:
            self._error = exc
            self._ready.set()
            return
        self._ready.set()
        message = wintypes.MSG()
        try:
            while self._user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_TIMER:
                    if self.in_front():
                        try:
                            self._install()
                            self.reinstalls += 1
                        except OSError:
                            pass  # the old hook stays active
                    continue
                self._user.TranslateMessage(ctypes.byref(message))
                self._user.DispatchMessageW(ctypes.byref(message))
        finally:
            if self._handle:
                self._user.UnhookWindowsHookEx(self._handle)
                self._handle = None

    def remove(self):
        if self._thread_id is not None and self._thread.is_alive():
            self._user.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(2)
