"""Self-update: only a newer release with a matching checksum replaces the exe."""
import hashlib
import time
import threading
import pytest
import requests
import apollo_update as update
import apollo

NEW_EXE = b"MZ new apollo"


class Response:
    def __init__(self, data=None, text="", body=b"", status=200):
        self.data, self.text, self.body, self.status_code = data, text, body, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)

    def json(self):
        return self.data

    def iter_content(self, size):
        yield self.body

    def close(self):
        pass


def server(tag="v9.0.0", body=NEW_EXE, checksum=None, assets=("apollo.exe", "apollo.exe.sha256")):
    checksum = checksum or hashlib.sha256(body).hexdigest()
    release = {"tag_name": tag, "draft": False, "prerelease": False,
               "assets": [{"name": name, "browser_download_url": "https://example.test/" + name} for name in assets]}
    def get(url, **kwargs):
        if url == update.RELEASE_URL:
            return Response(release)
        if url.endswith(".sha256"):
            return Response(text=f"{checksum}  apollo.exe\n")
        return Response(body=body)
    return get


@pytest.fixture
def exe(tmp_path):
    path = tmp_path / "apollo.exe"
    path.write_bytes(b"MZ old apollo")
    return path


def test_newer_release_replaces_exe_and_keeps_old_copy(exe):
    assert update.install_update(str(exe), "0.4.3", get=server()) == "9.0.0"
    assert exe.read_bytes() == NEW_EXE
    assert (exe.parent / "apollo.exe.old").read_bytes() == b"MZ old apollo"
    update.remove_old(str(exe))
    assert not (exe.parent / "apollo.exe.old").exists()


@pytest.mark.parametrize("tag", ["v0.4.3", "v0.4.2", "latest", ""])
def test_same_older_or_odd_release_changes_nothing(exe, tag):
    assert update.install_update(str(exe), "0.4.3", get=server(tag=tag)) is None
    assert exe.read_bytes() == b"MZ old apollo"


def test_wrong_checksum_keeps_running_exe(exe):
    with pytest.raises(update.UpdateError):
        update.install_update(str(exe), "0.4.3", get=server(checksum="0" * 64))
    assert exe.read_bytes() == b"MZ old apollo"
    assert sorted(p.name for p in exe.parent.iterdir()) == ["apollo.exe"]


def test_release_without_checksum_file_reports_error_and_keeps_exe(exe):
    with pytest.raises(update.UpdateError, match="missing"):
        update.install_update(str(exe), "0.4.3", get=server(assets=("apollo.exe",)))
    assert exe.read_bytes() == b"MZ old apollo"


def test_version_parser():
    assert update.parse_version("v0.10.2") == (0, 10, 2) > update.parse_version("0.9.9")
    assert update.parse_version("0.4") is None


def test_restart_waits_until_apollo_is_idle(app, monkeypatch):
    app._last_activity = time.monotonic()
    assert not app._idle_for(120)
    app._last_activity -= 200
    assert app._idle_for(120)
    app.recording = True
    assert not app._idle_for(120)
    app.recording = False


def test_update_preference_is_saved_and_on_by_default(app):
    assert app.cfg["auto_update"] is True
    app.update_preferences({"auto_update": False})
    assert app.cfg["auto_update"] is False


def test_manual_update_ignores_preference_and_waits_for_recording(app, monkeypatch):
    app.cfg["auto_update"] = False
    app._update_path = "isolated.exe"
    app.recording = True
    installed = threading.Event()
    restarted = threading.Event()
    calls = []
    def install(*args):
        calls.append(args)
        installed.set()
        return "9.0.0"
    def restart(path):
        assert not app.recording
        restarted.set()
        app._closing.set()
        return True
    monkeypatch.setattr(apollo, "install_update", install)
    monkeypatch.setattr(app, "_restart", restart)
    app.request_update()
    app.request_update()  # a second click must not queue another download
    thread = threading.Thread(target=app._update_loop, args=("isolated.exe",))
    thread.start()
    try:
        assert installed.wait(3)
        assert not restarted.is_set()
        with app._rec_lock:
            app.recording = False
        assert restarted.wait(3)
        assert len(calls) == 1
    finally:
        app.recording = False
        app._closing.set()
        thread.join(3)
    assert not thread.is_alive()


def test_failed_automatic_update_retries_soon_and_logs_safe_type(app, monkeypatch, caplog):
    monkeypatch.setattr(apollo, "UPDATE_FIRST_CHECK_SECONDS", 0)
    monkeypatch.setattr(apollo, "UPDATE_RETRY_SECONDS", 0)
    calls = []
    def install(*args):
        calls.append(args)
        if len(calls) == 1:
            raise requests.ConnectionError("secret-url-must-not-be-logged")
        app._closing.set()
        return None
    monkeypatch.setattr(apollo, "install_update", install)
    app._update_loop("isolated.exe")
    assert len(calls) == 2
    assert "ConnectionError" in caplog.text
    assert "secret-url" not in caplog.text
    assert not app.update_busy


def test_source_run_explains_why_it_cannot_replace_exe(app):
    app.request_update()
    assert "Windows app" in app.update_status
    assert not app.update_busy
