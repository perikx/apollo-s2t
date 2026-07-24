# PyInstaller spec for Apollo s2t -> a single windowed Apollo.exe with the logo.
#
# Build on Windows:   packaging\build-exe.bat
# (or directly:       .venv\Scripts\python.exe -m PyInstaller packaging\apollo.spec)
#
# The exe writes config.json / apollo.log next to itself. Bundled read-only
# resources (config.example.json, default prompts, the logo) travel inside the exe;
# apollo.py's BASE_DIR/RES_DIR split resolves both correctly.
import os

# SPECPATH is injected by PyInstaller = the directory containing this .spec file.
root = os.path.dirname(SPECPATH)

datas = [
    (os.path.join(root, "config.example.json"), "."),
    (os.path.join(root, "prompts"), "prompts"),
    (os.path.join(root, "assets", "apollo.ico"), "assets"),
    (os.path.join(root, "assets", "apollo.png"), "assets"),
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
    excludes=[],
    noarchive=False,
)

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
