import time
import apollo


def restore_timers(desktop):
    for timer in list(desktop.timers):
        timer.fire()


def test_instant_still_pastes(app, desktop):
    app.insert_text("new text", time.monotonic())
    assert desktop.sent == ["ctrl+v"]
    restore_timers(desktop)
    assert desktop.clip.text == "PREVIOUS"


def test_restore_waits_long_enough_for_slow_applications(app, desktop):
    app.insert_text("new text", time.monotonic())
    assert [timer.interval for timer in desktop.timers] == [1.5]


def test_paste_text_restores_previous_clipboard(desktop):
    apollo.paste_text("fresh", {})
    assert desktop.clip.text == "fresh"
    restore_timers(desktop)
    assert desktop.clip.text == "PREVIOUS"


def test_user_copy_is_not_overwritten_by_restore(desktop):
    apollo.paste_text("dictation", {})
    desktop.clip.copy("user copied this meanwhile")
    restore_timers(desktop)
    assert desktop.clip.text == "user copied this meanwhile"


def test_same_text_recopied_by_user_is_not_overwritten(desktop):
    apollo.paste_text("dictation", {})
    desktop.clip.copy("dictation")  # same text, different Windows clipboard sequence
    restore_timers(desktop)
    assert desktop.clip.text == "dictation"


def test_previous_dictations_restore_cannot_clobber_newer_copy(desktop):
    apollo.paste_text("old dictation", {})
    apollo._copy_owned("new dictation")
    restore_timers(desktop)
    assert desktop.clip.text == "new dictation"


def test_apollo_window_focus_copies_without_pasting(app, desktop):
    app.ui_windows.add("window-a")
    app.insert_text("saved words", time.monotonic())
    assert desktop.clip.text == "saved words"
    assert desktop.sent == []


def test_no_text_field_copies_and_plays_ready_beep(app, desktop, monkeypatch):
    beeps = []
    monkeypatch.setattr(apollo, "beep", lambda kind, enabled: beeps.append(kind))
    monkeypatch.setattr(apollo, "focused_is_editable", lambda: False)
    app.insert_text("words", time.monotonic())
    assert desktop.sent == [] and desktop.clip.text == "words"
    assert beeps == ["ready"]
    restore_timers(desktop)
    assert desktop.clip.text == "words"  # never restored: the user pastes it by hand


def test_unknown_target_pastes_keeps_text_and_beeps(app, desktop, monkeypatch):
    beeps = []
    monkeypatch.setattr(apollo, "beep", lambda kind, enabled: beeps.append(kind))
    monkeypatch.setattr(apollo, "focused_is_editable", lambda: None)
    app.insert_text("words", time.monotonic())
    assert desktop.sent == ["ctrl+v"] and beeps == ["ready"]
    restore_timers(desktop)
    assert desktop.clip.text == "words"


def test_text_field_paste_has_no_ready_beep(app, desktop, monkeypatch):
    beeps = []
    monkeypatch.setattr(apollo, "beep", lambda kind, enabled: beeps.append(kind))
    app.insert_text("words", time.monotonic())
    assert desktop.sent == ["ctrl+v"] and beeps == []


def test_skipped_paste_still_copies_and_beeps(app, desktop, monkeypatch):
    beeps = []
    monkeypatch.setattr(apollo, "beep", lambda kind, enabled: beeps.append(kind))
    monkeypatch.setattr(apollo, "_owns_clipboard", lambda token: False)  # another app took the clipboard
    app.insert_text("words", time.monotonic())
    assert desktop.sent == [] and desktop.clip.text == "words" and beeps == ["ready"]


def test_apollo_window_focus_plays_ready_beep(app, desktop, monkeypatch):
    beeps = []
    monkeypatch.setattr(apollo, "beep", lambda kind, enabled: beeps.append(kind))
    app.ui_windows.add("window-a")
    app.insert_text("words", time.monotonic())
    assert desktop.sent == [] and beeps == ["ready"]


def test_native_focus_checks_never_raise():
    assert apollo._uia_verdict() in (True, False, None)
    assert apollo.is_fullscreen(0) is False
