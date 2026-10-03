# PyInstaller spec for Apollo s2t -> a single windowed Apollo.exe with the logo.
#
# Build on Windows:   packaging\build-exe.bat
# (or directly:       .venv\Scripts\python.exe -m PyInstaller packaging\apollo.spec)
#
# The exe writes config.json / apollo.log next to itself. Bundled read-only
# resources (config.example.json, default prompts, the logo) travel inside the exe;
# apollo.py's BASE_DIR/RES_DIR split resolves both correctly.
import os
from pathlib import Path
import importlib.util

# SPECPATH is injected by PyInstaller = the directory containing this .spec file.
root = os.path.dirname(SPECPATH)

datas = [
    (os.path.join(root, "config.example.json"), "."),
    (os.path.join(root, "prompts"), "prompts"),
    (os.path.join(root, "assets", "apollo.ico"), "assets"),
    (os.path.join(root, "assets", "apollo.png"), "assets"),
    (os.path.join(root, "LICENSE"), "."),
    (os.path.join(root, "THIRD_PARTY_NOTICES.md"), "."),
    (os.path.join(root, "licenses"), "licenses"),
]

# Modules PyInstaller can miss because they're imported lazily / via COM.
hiddenimports = [
    "pystray._win32",
    "PIL.Image",
    "PIL.ImageDraw",
    "comtypes",
    "comtypes.client",
    "comtypes.stream",
]

a = Analysis(
    [os.path.join(root, "apollo.py")],
    pathex=[root],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore", "__main__"],
    noarchive=False,
)

# Qt uses the unsuffixed Windows ICU API. A tool on the build machine's PATH
# (e.g. Poppler) can supply a different icuuc.dll with version-suffixed exports.
# Bundling that causes QtCore to fail even though imports work in the venv.
# Leave Windows' own ICU in place; unrelated ICU data DLLs are not app resources.
a.binaries = [entry for entry in a.binaries
              if not (Path(entry[0]).name.lower() == "icuuc.dll"
                      or Path(entry[0]).name.lower().startswith("icudt"))]

# Python may ship an older MSVC runtime. Loading that at the archive root first
# can make QtCore fail with "specified procedure could not be found", even when
# the matching Qt runtime is bundled in a subdirectory. Use Qt's unmodified,
# backward-compatible runtime at the root so Python and Qt load the same version.
qt_dir = Path(next(iter(importlib.util.find_spec("PySide6").submodule_search_locations)))
for dll in ("VCRUNTIME140.dll", "VCRUNTIME140_1.dll", "MSVCP140.dll", "MSVCP140_1.dll", "MSVCP140_2.dll"):
    source = qt_dir / dll
    if source.exists():
        a.binaries = [entry for entry in a.binaries if entry[0].upper() != dll.upper()]
        a.binaries.append((dll, str(source), "BINARY"))

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Apollo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed: no console window; logs still go to apollo.log
    disable_windowed_traceback=False,
    icon=os.path.join(root, "assets", "apollo.ico"),
)
