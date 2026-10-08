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
