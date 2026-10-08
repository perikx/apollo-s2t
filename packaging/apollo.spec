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
    (os.path.join(root, "assets", "apollo-app.png"), "assets"),  # small UI copy of apollo.png
    (os.path.join(root, "assets", "fonts"), "assets/fonts"),
    (os.path.join(root, "LICENSE"), "."),
    (os.path.join(root, "THIRD_PARTY_NOTICES.md"), "."),
    (os.path.join(root, "licenses"), "licenses"),
]

a = Analysis(
    [os.path.join(root, "apollo.py")],
    pathex=[root],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtWebEngineCore", "__main__",
              "setuptools", "pkg_resources", "unittest", "pydoc", "pydoc_data", "doctest",
              "multiprocessing", "xmlrpc", "lib2to3",
              "numpy", "PIL", "pystray", "comtypes", "mouse"],
    noarchive=False,
)

# Qt uses the unsuffixed Windows ICU API. A tool on the build machine's PATH
# (e.g. Poppler) can supply a different icuuc.dll with version-suffixed exports.
# Bundling that causes QtCore to fail even though imports work in the venv.
# Leave Windows' own ICU in place; unrelated ICU data DLLs are not app resources.
a.binaries = [entry for entry in a.binaries
              if not (Path(entry[0]).name.lower() == "icuuc.dll"
                      or Path(entry[0]).name.lower().startswith("icudt"))]

# Size: drop Qt parts the widget UI never loads (Quick/Qml/Pdf/Svg/Network/OpenGL,
# software GL, translations), all Qt plugins except qwindows + qico (PNG is built in),
# the Poppler OpenSSL copies (Python's own libssl-3/libcrypto-3 stay) and the
# PortAudio builds for other CPUs (sounddevice loads libportaudio64bit.dll on x64).
DROP = ("opengl32sw", "qt6quick", "qt6qml", "qt6pdf", "qt6virtualkeyboard", "qt6opengl",
        "qt6network", "qt6svg", "qtnetwork.pyd", "pyside6/translations",
        "libssl-3-x64", "libcrypto-3-x64")
def wanted(entry):
    name = entry[0].lower().replace("\\", "/")
    if name.startswith("pyside6/plugins/"):
        return name.endswith(("/qwindows.dll", "/qico.dll"))
    if name.startswith("_sounddevice_data/"):
        return name.endswith("/libportaudio64bit.dll")
    return not any(part in name for part in DROP)
a.binaries = [entry for entry in a.binaries if wanted(entry)]
a.datas = [entry for entry in a.datas if wanted(entry)]

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
    name="apollo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed: no console window; logs still go to apollo.log
    disable_windowed_traceback=False,
    icon=os.path.join(root, "assets", "apollo.ico"),
)
