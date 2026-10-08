"""Inspect a real first-run console and cancel through native console input.

Source: python packaging/check-terminal.py --source
Frozen: python packaging/check-terminal.py (isolated copy, no API/mic/config)
"""
import ctypes
from ctypes import wintypes as w
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

root = Path(__file__).resolve().parents[1]
ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
kernel = ctypes.windll.kernel32
kernel.GetConsoleWindow.restype = w.HWND
kernel.GetStdHandle.restype = w.HANDLE
kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
kernel.CloseHandle.argtypes = (w.HANDLE,)
kernel.CreateFileW.argtypes = (w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE)
kernel.CreateFileW.restype = w.HANDLE
class Process(ctypes.Structure):
    _fields_ = [("size",w.DWORD),("usage",w.DWORD),("pid",w.DWORD),("heap",ctypes.c_size_t),("module",w.DWORD),("threads",w.DWORD),("parent",w.DWORD),("priority",w.LONG),("flags",w.DWORD),("exe",w.WCHAR*260)]
kernel.Process32FirstW.argtypes = (w.HANDLE, ctypes.POINTER(Process))
kernel.Process32NextW.argtypes = (w.HANDLE, ctypes.POINTER(Process))
class Coord(ctypes.Structure): _fields_ = [("x",w.SHORT),("y",w.SHORT)]
class Info(ctypes.Structure): _fields_ = [("size",Coord),("cursor",Coord),("attributes",w.WORD),("window",w.SMALL_RECT),("maximum",Coord)]
class Key(ctypes.Structure): _fields_ = [("down",w.BOOL),("repeat",w.WORD),("virtual",w.WORD),("scan",w.WORD),("char",w.WCHAR),("state",w.DWORD)]
class Event(ctypes.Union): _fields_ = [("key",Key),("padding",ctypes.c_byte*16)]
class Record(ctypes.Structure): _fields_ = [("type",w.WORD),("event",Event)]
kernel.GetConsoleScreenBufferInfo.argtypes = (w.HANDLE,ctypes.POINTER(Info))
kernel.ReadConsoleOutputCharacterW.argtypes = (w.HANDLE,w.LPWSTR,w.DWORD,Coord,ctypes.POINTER(w.DWORD))
kernel.WriteConsoleInputW.argtypes = (w.HANDLE,ctypes.POINTER(Record),w.DWORD,ctypes.POINTER(w.DWORD))

def children(pid):
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0); item = Process(); item.size = ctypes.sizeof(item); found = []
    try:
        okay = kernel.Process32FirstW(snapshot,ctypes.byref(item))
        while okay:
            if item.parent == pid: found.append(item.pid)
            okay = kernel.Process32NextW(snapshot,ctypes.byref(item))
    finally: kernel.CloseHandle(snapshot)
    return found + [child for parent in found for child in children(parent)]


assert not kernel.GetConsoleWindow(), "Run this check without an attached user console"
# Some launchers attach an invisible console despite GetConsoleWindow returning
# zero. Detach only this inspector process before attaching to the child.
kernel.FreeConsole()
with tempfile.TemporaryDirectory(prefix="apollo-terminal-check-", ignore_cleanup_errors=True) as directory:
    folder = Path(directory); env = os.environ.copy(); env.pop("OPENROUTER_API_KEY", None)
    if "--source" in sys.argv:
        script = folder/"source.py"
        script.write_text("import sys\nsys.path.insert(0,"+repr(str(root))+")\nfrom apollo_terminal import console,run_terminal_setup\nfrom apollo_config import default_config\nwith console():\n run_terminal_setup(default_config(), 'config.json', lambda enabled: None)\n", encoding="utf-8")
        command = [str(root/".venv/Scripts/pythonw.exe"),str(script)]
    else:
        target = folder/"apollo.exe"; shutil.copy2(root/"dist/apollo.exe",target); command = [str(target)]
    process = subprocess.Popen(command,cwd=folder,env=env)
    attached = False
    try:
        deadline = time.monotonic()+90
        while time.monotonic() < deadline:
            for pid in [process.pid]+children(process.pid):
                if kernel.AttachConsole(pid): attached = True; break
            if attached: break
            assert process.poll() is None, "Setup exited before creating its console"
            time.sleep(.25)
        assert attached, "No first-run terminal appeared"
        output = kernel.CreateFileW("CONOUT$",0x80000000,3,None,3,0,None); info = Info(); text = ""
        while time.monotonic() < deadline:
            assert kernel.GetConsoleScreenBufferInfo(output,ctypes.byref(info))
            count = min(info.size.x*info.size.y, 100000); buffer = ctypes.create_unicode_buffer(count+1); read = w.DWORD()
            assert kernel.ReadConsoleOutputCharacterW(output,buffer,count,Coord(0,0),ctypes.byref(read))
            text = buffer.value
            if "Language / Sprache / 语言" in text: break
            time.sleep(.2)
        assert "Language / Sprache" in text, repr(text[:1000])
        input_handle = kernel.CreateFileW("CONIN$",0x40000000,3,None,3,0,None)
        def type_text(value):
            records = []
            for char in value:
                for down in (True,False):
                    record = Record(); record.type = 1
                    record.event.key = Key(down,1,13 if char == "\r" else 8 if char == "\b" else 0,0,char,0)
                    records.append(record)
            array = (Record*len(records))(*records); written = w.DWORD()
            assert kernel.WriteConsoleInputW(input_handle,array,len(array),ctypes.byref(written))
            assert written.value == len(array)
        type_text("en\r")
        while time.monotonic() < deadline:
            kernel.ReadConsoleOutputCharacterW(output,buffer,count,Coord(0,0),ctypes.byref(read))
            if "OpenRouter key (hidden)" in buffer.value: break
            time.sleep(.1)
        assert "OpenRouter key (hidden)" in buffer.value
        private = "fixture-key-must-stay-hidden"
        type_text(private); time.sleep(.2)
        kernel.ReadConsoleOutputCharacterW(output,buffer,count,Coord(0,0),ctypes.byref(read))
        assert private not in buffer.value, "Password input was visible"
        type_text("\b"*len(private)+"/cancel\r")
        kernel.CloseHandle(input_handle); kernel.CloseHandle(output)
        kernel.FreeConsole(); attached = False
        assert process.wait(timeout=30) == 0
        assert not (folder/"config.json").exists() and not (folder/"config.json.bak").exists()
        print("Real Windows terminal first-run, multilingual prompt, hidden key input and cancellation without side effects: PASS")
    finally:
        if attached: kernel.FreeConsole()
        if process.poll() is None:
            subprocess.run(["taskkill","/PID",str(process.pid),"/T","/F"],capture_output=True,timeout=15)
            process.wait(timeout=15)
