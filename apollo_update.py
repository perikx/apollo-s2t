"""Automatic updates from the GitHub releases of Apollo (only for the Windows exe).

The new exe comes from the latest GitHub release and must match the SHA-256 in
apollo.exe.sha256 of the same release. Windows lets a running exe be renamed, so
the update swaps the files at once and the new version runs after the restart.
"""
import hashlib
import os
import re
import requests

RELEASE_URL = "https://api.github.com/repos/perikx/apollo-s2t/releases/latest"
EXE_ASSET = "apollo.exe"
SUM_ASSET = "apollo.exe.sha256"
MAX_EXE_BYTES = 200 * 1024 * 1024


class UpdateError(ValueError):
    """The release is incomplete or the download does not match its checksum."""


def parse_version(text):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (text or "").strip())
    return tuple(int(part) for part in match.groups()) if match else None


def latest_release(get=requests.get):
    """Return (version, exe URL, checksum URL) of the latest release, or None."""
    response = get(RELEASE_URL, headers={"Accept": "application/vnd.github+json"}, timeout=(5, 15))
    try:
        response.raise_for_status()
        data = response.json()
    finally:
        response.close()
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    version = parse_version(data.get("tag_name"))
    assets = {asset.get("name"): asset.get("browser_download_url")
              for asset in data.get("assets") or [] if isinstance(asset, dict)}
    if version is None or not assets.get(EXE_ASSET) or not assets.get(SUM_ASSET):
        return None
    return version, assets[EXE_ASSET], assets[SUM_ASSET]


def _expected_sum(url, get):
    response = get(url, timeout=(5, 15))
    try:
        response.raise_for_status()
        words = response.text.split()
    finally:
        response.close()
    if not words or not re.fullmatch(r"[0-9a-fA-F]{64}", words[0]):
        raise UpdateError("The release has no valid checksum.")
    return words[0].lower()


def _download(url, path, expected, get):
    digest, size = hashlib.sha256(), 0
    response = get(url, stream=True, timeout=(5, 60))
    try:
        response.raise_for_status()
        with open(path, "wb") as handle:
            for chunk in response.iter_content(1 << 16):
                size += len(chunk)
                if size > MAX_EXE_BYTES:
                    raise UpdateError("The release file is too large.")
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        response.close()
    if digest.hexdigest() != expected:
        raise UpdateError("The download does not match the release checksum.")


def install_update(exe_path, current_version, get=requests.get):
    """Replace exe_path with a newer release. Return the new version text, or None if Apollo is current.

    The old exe stays as <exe>.old until the next start removes it.
    """
    current = parse_version(current_version)
    release = latest_release(get)
    if release is None or current is None or release[0] <= current:
        return None
    version, exe_url, sum_url = release
    expected = _expected_sum(sum_url, get)
    new_path, old_path = exe_path + ".new", exe_path + ".old"
    try:
        _download(exe_url, new_path, expected, get)
        if os.path.exists(old_path):
            os.remove(old_path)
        os.replace(exe_path, old_path)
        try:
            os.replace(new_path, exe_path)
        except OSError:
            os.replace(old_path, exe_path)  # put the running version back
            raise
    finally:
        if os.path.exists(new_path):
            os.remove(new_path)
    return ".".join(map(str, version))


def remove_old(exe_path):
    """Delete the exe that the last update replaced."""
    try:
        os.remove(exe_path + ".old")
    except OSError:
        pass
