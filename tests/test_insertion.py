import time
import apollo


def restore_timers(desktop):
    for timer in list(desktop.timers):
        if timer.interval < 1:
            timer.fire()


def test_hybrid_in_text_field_pastes_and_restores_clipboard(app, desktop, monkeypatch):
    monkeypatch.setattr(apollo, "focused_is_editable", lambda: True)
    app.deliver_hybrid("hello world")
    assert desktop.sent == ["ctrl+v"]
    assert desktop.clip.text == "hello world"
    restore_timers(desktop)
    assert desktop.clip.text == "PREVIOUS"
    assert app._pending_text is None


def test_hybrid_not_a_field_keeps_on_clipboard(app, desktop, monkeypatch):
    monkeypatch.setattr(apollo, "focused_is_editable", lambda: False)
    app.deliver_hybrid("keep me")
    assert desktop.sent == []
    assert app._pending_text == desktop.clip.text == "keep me"
    app.disarm()
    restore_timers(desktop)
    assert desktop.clip.text == "keep me"


def test_hybrid_undetected_pastes_and_keeps_then_restores_on_expiry(app, desktop):
    app.deliver_hybrid("both ways")
    assert desktop.sent == ["ctrl+v"]
    assert app._pending_text == "both ways"
    app.disarm()
    restore_timers(desktop)
    assert desktop.clip.text == "PREVIOUS"


def test_batch_never_types_live(app):
    assert app.insert_live is False


def test_instant_still_pastes(app, desktop):
    app.insert_text("new text", time.monotonic())
    assert desktop.sent == ["ctrl+v"]
    restore_timers(desktop)
    assert desktop.clip.text == "PREVIOUS"


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


def test_previous_dictations_restore_cannot_clobber_new_load(app, desktop):
    apollo.paste_text("old dictation", {})
    app.arm("new dictation")
    restore_timers(desktop)
    assert desktop.clip.text == "new dictation"


def test_stale_disarm_timer_cannot_consume_new_load(app, desktop):
    app.arm("old")
    old_timer = desktop.timers[-1]
    app.arm("new")
    old_timer.fire(even_if_cancelled=True)
    assert app._pending_text == "new"


def test_click_outside_text_field_keeps_load(app, desktop, monkeypatch):
    app.arm("pending")
    monkeypatch.setattr(apollo, "focused_is_editable", lambda: False)
    app._try_fire_on_click()
    assert app._pending_text == "pending"
    assert not desktop.sent


def test_click_cannot_paste_replaced_clipboard(app, desktop):
    app.arm("dictation")
    desktop.clip.copy("something else")
    app._try_fire_on_click()
    assert not desktop.sent
    assert desktop.clip.text == "something else"


def test_stale_click_cannot_paste_new_load(app, desktop):
    app.arm("old")
    token = app._pending_token
    app.arm("new")
    app._try_fire_on_click(token)
    assert not desktop.sent
    assert app._pending_text == "new"


def test_failed_origin_focus_keeps_text_instead_of_pasting_elsewhere(app, desktop, monkeypatch):
    app.insert_target, app._origin_hwnd = "origin", "closed-window"
    monkeypatch.setattr(apollo, "focus_window", lambda hwnd: False)
    app.insert_text("recoverable", time.monotonic())
    assert not desktop.sent
    assert desktop.clip.text == "recoverable"


def test_armed_same_window_pastes_without_restoring(app, desktop):
    app._origin_hwnd = "window-a"
    app.deliver_armed("text")
    restore_timers(desktop)
    assert desktop.sent == ["ctrl+v"]
    assert desktop.clip.text == "text"


def test_detection_unknown_without_windows(monkeypatch):
    from conftest import ORIGINAL_FOCUSED_IS_EDITABLE
    from types import SimpleNamespace
    monkeypatch.setattr(apollo, "os", SimpleNamespace(name="posix"))
    assert ORIGINAL_FOCUSED_IS_EDITABLE() is None
