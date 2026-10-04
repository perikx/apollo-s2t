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
import sys

from PyInstaller.archive.readers import CArchiveReader

root = Path(__file__).resolve().parents[1]
exe = root / 'dist/apollo.exe'
archive = CArchiveReader(str(exe))
pyz = next(name for name in archive.toc if name.endswith('.pyz'))
modules = archive.open_embedded_archive(pyz).toc
assert all(name in modules for name in ('apollo_overlay', 'apollo_models', 'apollo_recovery', 'apollo_design', 'apollo_widgets', 'apollo_setup', 'apollo_terminal'))
assert 'config.json' not in archive.toc
assert not any(Path(name).name.lower() == 'icuuc.dll' for name in archive.toc), 'Qt must use native Windows ICU'
import hashlib
assert hashlib.sha256(archive.extract('assets\\apollo.png')).digest() == hashlib.sha256((root/'assets/apollo.png').read_bytes()).digest()
assert hashlib.sha256(archive.extract('assets\\fonts\\Figtree.ttf')).digest() == hashlib.sha256((root/'assets/fonts/Figtree.ttf').read_bytes()).digest()
assert 'assets\\fonts\\OFL.txt' in archive.toc
assert 'THIRD_PARTY_NOTICES.md' in archive.toc
assert 'tkinter' not in modules
assert any('QtWidgets.pyd' in name for name in archive.toc)
assert any(name.endswith('qwindows.dll') for name in archive.toc)
print('Frozen Qt UI and Windows platform plugin included; Tk and private config excluded.', flush=True)
# The public EXE is windowed during normal use, but creates a real terminal for
# first-run onboarding. This acceptance check reads that console and types Cancel.
subprocess.run([sys.executable, str(root / 'packaging/check-terminal.py')], check=True)
