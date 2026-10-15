"""F10 profiles: notes stay local, the first save keeps the bundled profiles visible."""
import os
import pytest
import apollo
from apollo_config import ConfigError, default_config


def cfg_for(name):
    cfg = default_config()
    cfg["prompt_profiles"]["active"] = name
    return cfg


def test_notes_in_comments_never_reach_the_model(tmp_path):
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts" / "work.md").write_text("<!-- private note -->\n## Project\nApollo\n<!--\nmore\n-->", encoding="utf-8")
    system = apollo.build_prompt_system(cfg_for("work"), str(tmp_path))
    assert "Apollo" in system and "private note" not in system and "more" not in system


def test_bundled_default_profile_sends_no_context():
    system = apollo.build_prompt_system(cfg_for("default"), apollo.RES_DIR)
    assert "Project context" not in system


def test_first_save_copies_bundled_profiles(tmp_path, monkeypatch):
    bundled = tmp_path / "bundle" / "prompts"
    bundled.mkdir(parents=True)
    (bundled / "default.md").write_text("<!-- default -->", encoding="utf-8")
    monkeypatch.setattr(apollo, "RES_DIR", str(tmp_path / "bundle"))
    home = tmp_path / "home"
    home.mkdir()
    apollo.write_profile("my project", "## Project\nX", str(home))
    assert sorted(os.listdir(home / "prompts")) == ["default.md", "my project.md"]
    assert (home / "prompts" / "my project.md").read_text(encoding="utf-8") == "## Project\nX\n"


@pytest.mark.parametrize("name", ["", "../escape", "a/b", "con:", "x" * 41, "trailing."])
def test_profile_names_cannot_leave_the_folder(tmp_path, name):
    with pytest.raises(ConfigError):
        apollo.write_profile(name, "text", str(tmp_path))
    assert not (tmp_path / "prompts").exists()


def test_app_reads_and_saves_profile_text(app):
    app.save_profile("client", "<!-- note -->\nClient project")
    assert "client" in app.available_profiles()
    assert app.profile_text("client") == "<!-- note -->\nClient project\n"
