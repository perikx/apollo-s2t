import sys
import pytest
import selftest
from apollo_config import default_config, save_config


def configured(tmp_path, monkeypatch):
    cfg = default_config()
    cfg["smoothing"]["api_key"] = "private-key"
    save_config(tmp_path / "config.json", cfg)
    monkeypatch.setattr(selftest, "BASE", tmp_path)
    sd = sys.modules["sounddevice"]
    monkeypatch.setattr(sd, "check_input_settings", lambda **kwargs: None, raising=False)
    monkeypatch.setattr(sd, "query_devices", lambda: [{"name": "Test mic", "max_input_channels": 1}], raising=False)
    return sd


def test_offline_selftest_makes_no_recording_or_paid_call(tmp_path, monkeypatch, capsys):
    sd = configured(tmp_path, monkeypatch)
    def forbidden(**kwargs):
        raise AssertionError("Offline must not record")
    monkeypatch.setattr(sd, "rec", forbidden, raising=False)
    assert selftest.main([]) == 0
    output = capsys.readouterr().out
    assert "Offline checks only" in output and "private-key" not in output


def test_failed_selftest_has_failure_exit_status(tmp_path, monkeypatch):
    sd = configured(tmp_path, monkeypatch)
    def unavailable(**kwargs):
        raise OSError("No mic")
    monkeypatch.setattr(sd, "check_input_settings", unavailable)
    assert selftest.main([]) == 1


def test_live_duration_is_bounded():
    with pytest.raises(SystemExit) as result:
        selftest.main(["--live", "--seconds", "10000"])
    assert result.value.code == 2
