"""Collect the release files from dist\apollo.exe into a folder.

Usage: python packaging/release_files.py <out-folder>

The folder gets apollo.exe, apollo.exe.sha256 (the self-update checks it),
Apollo-Third-Party-Notices.zip and notes.md (the CHANGELOG section of APP_VERSION).
"""
import hashlib
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOTICES = [("licenses/GPL-3.0.txt", "licenses/GPL-3.0.txt"), ("licenses/LGPL-3.0.txt", "licenses/LGPL-3.0.txt"),
           ("LICENSE", "LICENSE"), ("THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
           ("assets/fonts/OFL.txt", "OFL.txt")]
FOOTER = ("**Windows:** Download `apollo.exe` below into a writable folder and run it. You do not need Python. "
          "Keep `config.json` and the `prompts` folder to preserve your settings. From 0.4.4 on, Apollo updates itself.")


def app_version():
    match = re.search(r'^APP_VERSION = "([^"]+)"', (ROOT / "apollo.py").read_text(encoding="utf-8"), re.M)
    if not match:
        raise SystemExit("APP_VERSION not found in apollo.py.")
    return match.group(1)


def release_notes(version):
    """The CHANGELOG section '## [version]' up to the next '## ' heading."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not match or not match.group(1).strip():
        raise SystemExit(f"CHANGELOG.md has no section for {version}. Date the Unreleased section first.")
    return match.group(1).strip() + "\n\n" + FOOTER + "\n"


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    version = app_version()
    notes = release_notes(version)
    exe = out / "apollo.exe"
    shutil.copyfile(ROOT / "dist" / "apollo.exe", exe)
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    (out / "apollo.exe.sha256").write_bytes(f"{digest}  apollo.exe\n".encode("ascii"))
    with zipfile.ZipFile(out / "Apollo-Third-Party-Notices.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for source, name in NOTICES:
            archive.write(ROOT / source, name)
    (out / "notes.md").write_text(notes, encoding="utf-8", newline="\n")
    print(f"apollo {version}: {exe.stat().st_size} bytes, SHA-256 {digest}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
