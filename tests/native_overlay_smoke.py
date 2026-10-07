"""Synthetic Qt interaction and rendered-output checks; no mic, paid API or clipboard."""
import sys
from pathlib import Path
import tempfile
import types
import logging
import json
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for name in ("keyboard", "sounddevice", "pyperclip", "mouse"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["keyboard"].key_to_scan_codes = lambda key: (sum(map(ord, key)),)
import apollo
from apollo_design import qt_app
import apollo_widgets
import apollo_overlay
import apollo_setup
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QScrollArea, QWidget, QAbstractScrollArea
from PySide6.QtTest import QTest
from apollo_config import default_config
from apollo_i18n import t, set_language
from PySide6.QtGui import QFontInfo, QFontDatabase
from PySide6.QtWidgets import QLabel
from apollo_design import Logo
qt_app()
assert "Figtree" in QFontDatabase.families()
assert QFontInfo(qt_app().font()).family() == "Figtree"

output = Path(sys.argv[1]).resolve(); output.mkdir(parents=True, exist_ok=True)
temporary = tempfile.TemporaryDirectory(); apollo.BASE_DIR = temporary.name
app = apollo.App({"ui_language": "de", "beep": False, "hotkeys": {"dictate": "n", "polish": "m", "prompt": "p"}})
apollo.log.setLevel(logging.INFO)
backup = app.recovery.create(16000, 1, "polish", hotkey="m")
backup.append(b"\x00\x00"*16000); backup.finish()
backup.save_transcript("Das ist wichtig. Wirklich wichtig. Bitte behalte meine Wiederholungen.", final=True); backup.update(state="ready")

def start(self, reload=False):
    self.started = True
    self.data = {"transcription": {
        "microsoft/mai-transcribe-2": {"name": "Microsoft: MAI Transcribe 2", "price": "Audio: $0.1 / Std. Audio"},
        "microsoft/mai-transcribe-1.5": {"name": "Microsoft: MAI Transcribe 1.5", "price": "Audio: $0.36 / Std. Audio"},
        "openai/whisper-1": {"name": "OpenAI: Whisper", "price": "Audio: $0.0001 / Sek. Audio"}},
        "text": {"google/gemini-3.5-flash-lite": {"name": "Google: Gemini 3.5 Flash Lite", "price": "$0.10 Eingabe · $0.40 Ausgabe / Mio. Tokens"}}}
    self.changed.emit()
apollo_widgets.ModelCatalog.start = start
ui = apollo_overlay.FloatingUI(app, app.close)
ui.x, ui.y = 550, 400; ui.show()
def screenshot(name, window):
    qt_app().processEvents()
    window.grab().save(str(output/name))

def click(action):
    point = ui.orb.CENTER if action == "logo" else ui.orb.ACTIONS[action]
    QTest.mouseClick(ui.orb, Qt.MouseButton.LeftButton, pos=point.toPoint())
    qt_app().processEvents()

click("logo"); assert ui.expanded
for _ in range(100):
    if ui.orb.menu_animation.state() == ui.orb.menu_animation.State.Stopped: break
    QTest.qWait(20)
assert ui.orb.menu_animation.state() == ui.orb.menu_animation.State.Stopped
assert ui.orb.size().width() == 200 and ui.orb.size().height() == 168
screenshot("overlay.png", ui.orb)
click("recovery"); assert ui.page == "recovery"
assert "M · Bereinigen" in ui.recovery_list.item(0).text()
assert "Wirklich wichtig" in ui.preview_text.toPlainText()
apollo.log.error("Transcription: HTTP 429. The upstream provider reported a rate/capacity limit. Audio kept.")
ui.tick(); ui.refresh_debug()
assert "HTTP 429" in ui.debug_text.toPlainText() and "N / M / P" in ui.debug_state.text()
backup.update(state="failed", error="Transcription: HTTP 429. Upstream capacity limit.")
ui.refresh_recovery()
assert "Ursache:" in ui.preview_text.toPlainText() and "HTTP 429" in ui.preview_text.toPlainText()
metadata_path = backup.path.with_suffix(".json")
metadata = json.loads(metadata_path.read_text()); metadata["error"] = ["malformed legacy diagnostic"]
metadata_path.write_text(json.dumps(metadata)); ui.refresh_recovery()
assert ui.dialog.isVisible() and "Wirklich wichtig" in ui.preview_text.toPlainText()
backup.update(state="ready", error=""); ui.refresh_recovery()
screenshot("recovery.png", ui.dialog)
click("settings"); assert ui.page == "settings"
assert not ui.dialog.findChildren(Logo)
assert any(item.text() == "apollo" for item in ui.dialog.findChildren(QLabel))
ui.interface_language.choose()
assert all(ui.interface_language.items.item(i).data(Qt.ItemDataRole.UserRole) != ui.interface_language.value for i in range(ui.interface_language.items.count()))
assert ui.interface_language.items.count() == 2
ui.interface_language.choose()
ui.vocabulary.setPlainText("PANDU\nSUPERBASE"); ui.save_settings()
assert app.cfg["openrouter_stt"]["vocabulary"] == ["PANDU", "SUPERBASE"]
ui.vocabulary.setPlainText("x"*101); ui.save_settings()
assert ui.dialog.isVisible() and app.cfg["openrouter_stt"]["vocabulary"] == ["PANDU", "SUPERBASE"]
ui.vocabulary.setPlainText("PANDU\nSUPERBASE")
assert not ui.dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
assert ui.dialog.windowType() == Qt.WindowType.Window
assert ui.dialog.windowTitle().startswith("apollo s2t")
# Native z-order: another ordinary window can cover settings when focus changes.
import ctypes
other = QWidget(); other.setWindowTitle("Apollo test focus peer"); other.resize(200, 100); other.show(); other.raise_(); other.activateWindow()
QTest.qWait(50)
user = ctypes.windll.user32
user.GetWindowLongPtrW.argtypes = (ctypes.c_void_p, ctypes.c_int); user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
assert not user.GetWindowLongPtrW(int(ui.dialog.winId()), -20) & 8  # WS_EX_TOPMOST
order = []
callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_ssize_t)(lambda hwnd, _: order.append(hwnd) or True)
user.EnumWindows(callback, 0)
assert order.index(int(other.winId())) < order.index(int(ui.dialog.winId()))
other.close(); ui.dialog.raise_(); ui.dialog.activateWindow()
# The wheel works on an unfocused field without an initial click.
ui.dialog.resize(680, 430); qt_app().processEvents()
area = ui.content.findChild(QScrollArea); bar = area.verticalScrollBar()
assert bar.maximum() > 0
bar.setValue(0); ui.nav_buttons["settings"].setFocus()
event = QWheelEvent(QPointF(8, 8), QPointF(ui.minutes.mapToGlobal(QPoint(8, 8))), QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
qt_app().sendEvent(ui.minutes, event)
assert bar.value() > 0 and area.horizontalScrollBar().maximum() == 0
# Nested selectors consume wheel input at both ends, including short lists,
# without moving the surrounding page or requiring keyboard focus.
def wheel(widget, delta):
    point = QPoint(8, 8)
    event = QWheelEvent(QPointF(point), QPointF(widget.mapToGlobal(point)), QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    event.ignore(); qt_app().sendEvent(widget, event)
    assert event.isAccepted()

for choice in (ui.key_mode, ui.profile, ui.language):
    choice.choose(); qt_app().processEvents()
    ui.nav_buttons["settings"].setFocus()
    outer_position = bar.maximum()//2; bar.setValue(outer_position)
    nested = choice.items.verticalScrollBar()
    nested.setValue(nested.maximum()); wheel(choice.items.viewport(), -120)
    assert bar.value() == outer_position and nested.value() == nested.maximum()
    nested.setValue(0); wheel(choice.items.viewport(), 120)
    assert bar.value() == outer_position and nested.value() == 0
    assert nested.sizeHint().width() == 0
    choice.choose()
ui.dialog.resize(680, 560); qt_app().processEvents()
# Profile/behavior choices are embedded too.
ui.key_mode.choose(); assert ui.key_mode.items.isVisible() and ui.key_mode.items.window() is ui.dialog
ui.key_mode.apply(ui.key_mode.items.item(0)); assert ui.key_mode.items.isHidden()
ui.minutes.setText("bad"); ui.save_settings()
assert ui.dialog.isVisible() and ui.minutes.text() == "bad" and "5 bis 60" in ui.result.text()
QTest.keyClick(ui.minutes, Qt.Key.Key_Return); assert ui.dialog.isVisible()
ui.minutes.setText("10")
QTest.mouseClick(ui.key_fields["dictate"], Qt.MouseButton.LeftButton)
QTest.keyClick(ui.key_fields["dictate"], Qt.Key.Key_K)
assert ui.key_fields["dictate"].value == "k"
settings_area = ui.form_scroll
settings_area.verticalScrollBar().setValue(settings_area.verticalScrollBar().maximum())
position = settings_area.verticalScrollBar().value()
ui.save_settings(); assert app.cfg["hotkeys"]["dictate"] == "k" and app.cfg["recovery_cache"]["minutes"] == 10
qt_app().processEvents()
assert ui.form_scroll is settings_area and ui.form_scroll.verticalScrollBar().value() == min(position, settings_area.verticalScrollBar().maximum())
area.verticalScrollBar().setValue(0)
screenshot("settings.png", ui.dialog)
click("models"); assert ui.page == "models"
original = app.cfg["openrouter_stt"]["model"]
ui.model_fields["primary"].value = "chat/unsupported"; ui.save_models()
assert app.cfg["openrouter_stt"]["model"] == original
ui.model_fields["primary"].value = original; ui.model_fields["primary"].refresh()
ui.model_fields["fallback"].value = original; ui.save_models()
assert "verschieden" in ui.result.text() and ui.dialog.isVisible()
ui.model_fields["fallback"].value = None; ui.model_fields["fallback"].refresh(); ui.save_models()
assert app.cfg["openrouter_stt"]["fallback_model"] is None
screenshot("models.png", ui.dialog)
windows = set(qt_app().topLevelWidgets())
QTest.mouseClick(ui.model_fields["fallback"].button, Qt.MouseButton.LeftButton); qt_app().processEvents()
picker = ui.model_fields["fallback"].picker
assert not picker.isWindow() and picker.window() is ui.dialog and set(qt_app().topLevelWidgets()) == windows
selectable = [picker.items.item(i) for i in range(picker.items.count()) if picker.items.item(i).flags() & Qt.ItemFlag.ItemIsSelectable]
assert [item.data(Qt.ItemDataRole.UserRole) for item in selectable] == [None, "microsoft/mai-transcribe-2", "microsoft/mai-transcribe-1.5", "openai/whisper-1"]
assert len(set(item.data(Qt.ItemDataRole.UserRole) for item in selectable)) == len(selectable)
assert [picker.items.item(i).text() for i in range(picker.items.count()) if picker.items.item(i).data(Qt.ItemDataRole.UserRole+2)] == ["Empfohlen für Apollo", "Weitere Modelle"]
qt_app().processEvents()
area = ui.content.findChild(QScrollArea); outer = area.verticalScrollBar()
nested = picker.items.verticalScrollBar(); assert nested.maximum() > 0
outer.setValue(outer.maximum()//2); position = outer.value()
nested.setValue(nested.maximum()); wheel(picker.items.viewport(), -120)
assert outer.value() == position and nested.value() == nested.maximum()
nested.setValue(0); wheel(picker.items.viewport(), -120)
assert nested.value() > 0 and outer.value() == position
nested.setValue(0); wheel(picker.items.viewport(), 120)
assert outer.value() == position and nested.value() == 0
screenshot("recommended-models.png", ui.dialog)
picker.search.setText("Whisper"); assert picker.items.count() == 1
assert picker.items.item(0).sizeHint().height() == 32
picker.items.setCurrentRow(0); screenshot("picker.png", ui.dialog)
QTest.mouseClick(picker.items.viewport(), Qt.MouseButton.LeftButton, pos=picker.items.visualItemRect(picker.items.item(0)).center())
assert ui.model_fields["fallback"].value == "openai/whisper-1"
assert picker.isHidden()
ui.model_fields["primary"].choose(); ui.model_fields["text"].choose()
assert ui.model_fields["primary"].picker.isHidden() and ui.model_fields["text"].picker.isVisible()
text_picker = ui.model_fields["text"].picker
QTest.keyClick(text_picker.search, Qt.Key.Key_Down)
assert text_picker.items.currentItem().data(Qt.ItemDataRole.UserRole) == "google/gemini-3.5-flash-lite"
assert "geladen" not in text_picker.note.text()
for scrollable in ui.dialog.findChildren(QAbstractScrollArea):
    assert scrollable.verticalScrollBar().sizeHint().width() == 0
    assert scrollable.horizontalScrollBar().sizeHint().height() == 0
ui.model_fields["text"].picker.search.setText("no such model")
assert ui.model_fields["text"].picker.items.count() == 0
QTest.keyClick(ui.model_fields["text"].picker.search, Qt.Key.Key_Escape)
assert ui.model_fields["text"].picker.isHidden() and ui.dialog.isVisible()
ui.save_models(); assert app.cfg["openrouter_stt"]["fallback_model"] == "openai/whisper-1"
click("close"); assert ui.dialog is None and not ui.expanded
app.recording = True; app.active_mode = "dictate"
app.recorder.visual_levels = tuple([.1, .3, .5, .75, .9, .65, .4, .1, 0]*3)
app.recorder._sample_count = 16000*65
size = ui.orb.size(); ui.tick(); screenshot("recording.png", ui.orb)
assert ui.orb.size() == size
ui.open_page("recovery")
# A synthetic one-shot buffer has no ongoing microphone callback. Control time
# here so rendering/build-machine load cannot falsely turn it into a stall.
with patch("apollo_overlay.time.monotonic", return_value=ui.sample_at):
    ui.refresh_debug()
    assert "K · Diktieren · 1:05" in ui.debug_state.text() and "Audiodaten kommen an" in ui.debug_state.text()
with patch("apollo_overlay.time.monotonic", return_value=ui.sample_at+2):
    ui.refresh_debug(); assert "Keine neuen Audiodaten" in ui.debug_state.text()
app.recorder.backup_failed = True; ui.refresh_debug(); assert "Backup fehlgeschlagen" in ui.debug_state.text()
app.recorder.backup_failed = False
for i in range(400): apollo.log.info("Queue diagnostic %d", i)
ui.tick(); ui.refresh_debug()
assert len(ui.debug_lines) == 300 and ui.debug_text.blockCount() <= 300
assert "Queue diagnostic 399" in ui.debug_text.toPlainText()
app.recording = False; app._busy_recordings.add(backup.id); ui.status = "Bereit"; ui.refresh_debug()
assert "Verarbeitung" in ui.debug_state.text() and "Bereit" not in ui.debug_state.text()
app._busy_recordings.clear()
ui.collapse()
app.recording = False; ui.tick()
ui.x = ui.bounds().right()-5; ui.orb.moved = True
ui.orb.mouseReleaseEvent(types.SimpleNamespace())
assert not ui.visible and not ui.orb.isVisible() and not app.cfg["overlay"]["visible"]
app.notify("Simulated 429, audio saved"); ui.tick(); assert not ui.orb.isVisible()
app.open_panel("recovery"); ui.tick(); assert ui.visible and ui.page == "recovery"
# Native close followed by reopen must register the new HWND again.
ui.dialog.reject(); qt_app().processEvents(); assert ui.dialog is None
ui.open_page("settings"); assert int(ui.dialog.winId()) in app.ui_windows
ui.collapse()

# Interface language is independent of transcription/output language, saves
# through the real preference path, and leaves original transcript text intact.
for language, settings_title in (("en", "Settings"), ("zh", "设置"), ("de", "Einstellungen")):
    ui.open_page("settings")
    output_language = app.cfg["prompt_profiles"]["output_language"]
    ui.form_scroll.verticalScrollBar().setValue(ui.form_scroll.verticalScrollBar().maximum()//2)
    position = ui.form_scroll.verticalScrollBar().value()
    ui.interface_language.value = language; ui.save_settings()
    QTest.qWait(30)
    assert ui.form_scroll.verticalScrollBar().value() == min(position, ui.form_scroll.verticalScrollBar().maximum())
    assert app.cfg["ui_language"] == language and ui.nav_buttons["settings"].text() == settings_title
    assert app.cfg["prompt_profiles"]["output_language"] == output_language
    screenshot(f"settings-{language}.png", ui.dialog)
    ui.open_page("recovery"); assert "Wirklich wichtig" in ui.preview_text.toPlainText()
    screenshot(f"recovery-{language}.png", ui.dialog)
ui.collapse()
for _ in range(100):
    if ui.orb.menu_animation.state() == ui.orb.menu_animation.State.Stopped: break
    QTest.qWait(20)
ui.orb.hover("logo"); QTest.qWait(130)
assert ui.orb.hover_animation.state() == ui.orb.hover_animation.State.Stopped
ui.orb.hover(None); QTest.qWait(130)
assert ui.orb.hover_animation.state() == ui.orb.hover_animation.State.Stopped
ui.tick()
with patch.object(ui.orb, "update") as redraw:
    ui.tick(); assert not redraw.called  # unchanged idle state never repaints
# Right-click at the logo opens a small menu; Hide persists and tray Show restores.
event = types.SimpleNamespace(pos=lambda: ui.orb.CENTER.toPoint(), globalPos=lambda: ui.orb.mapToGlobal(ui.orb.CENTER.toPoint()), accept=lambda: None)
ui.orb.contextMenuEvent(event); qt_app().processEvents()
assert ui.orb.context_menu.isVisible() and ui.orb.context_menu.actions()[0].text() == "Im Tray ausblenden"
ui.orb.context_menu.actions()[0].trigger(); ui.orb.context_menu.close()
assert not ui.visible and not app.cfg["overlay"]["visible"] and not ui.orb.isVisible()
app.open_panel(); ui.tick(); assert ui.visible and ui.orb.isVisible() and app.cfg["overlay"]["visible"]

# First-run defaults to English. Switching language preserves editable key
# entries and does not write configuration or perform network calls.
language_path = Path(temporary.name)/"language-wizard.json"
language_wizard = apollo_setup.SetupWizard(default_config(), language_path, lambda _: None)
language_wizard.show(); assert language_wizard.heading.text() == "Welcome to apollo s2t"
language_wizard.key.setText("Bereinigen")  # a catalog word must never mutate a key
for language, title in (("zh", "欢迎使用 apollo s2t"), ("de", "Willkommen bei apollo s2t"), ("en", "Welcome to apollo s2t")):
    language_wizard.change_language(language)
    assert language_wizard.heading.text() == title and language_wizard.key.text() == "Bereinigen"
    screenshot(f"setup-{language}.png", language_wizard)
language_wizard.key.clear(); language_wizard.advance()
assert language_wizard.step == 0 and language_wizard.error.text() == "Enter an OpenRouter API key."
language_wizard.step = 1; language_wizard.render(); language_wizard.keys["polish"].value = "m"
language_wizard.change_language("zh"); assert language_wizard.keys["polish"].value == "m"
language_wizard.step = 2; language_wizard.render(); language_wizard.fields["fallback"].value = None
language_wizard.autostart.setChecked(True); language_wizard.change_language("en")
assert language_wizard.fields["fallback"].value is None and language_wizard.autostart.isChecked()
language_wizard.reject(); assert not language_path.exists()
set_language("de")

startup = []; config_path = Path(temporary.name)/"wizard.json"
wizard_cfg = default_config(); wizard_cfg["ui_language"] = "de"
wizard = apollo_setup.SetupWizard(wizard_cfg, config_path, startup.append)
wizard.show(); qt_app().processEvents()
wizard.advance(); assert wizard.step == 0 and wizard.isVisible() and "API-Schlüssel" in wizard.error.text()
screenshot("setup.png", wizard)
apollo_setup.check_key = lambda key: "Schlüssel abgelehnt" if key == "bad" else ""
wizard.key.setText("bad"); wizard.advance()
for _ in range(20): qt_app().processEvents(); QTest.qWait(10)
assert wizard.step == 0 and wizard.key.isEnabled() and wizard.key.text() == "bad"
wizard.key.setText("test-key"); wizard.advance()
for _ in range(20): qt_app().processEvents(); QTest.qWait(10)
assert wizard.step == 1
wizard.keys["polish"].value = "f8"; wizard.advance(); assert wizard.step == 1 and wizard.isVisible()
wizard.keys["polish"].value = "f9"; wizard.advance(); assert wizard.step == 2
screenshot("setup-models.png", wizard)
wizard.fields["fallback"].value = wizard.fields["primary"].value
wizard.advance(); assert wizard.step == 2 and wizard.isVisible() and not config_path.exists()
wizard.fields["fallback"].value = None; wizard.advance()
assert config_path.exists() and startup == [False] and not wizard.isVisible()
# Cancellation has no write/registry side effect.
cancel_path = Path(temporary.name)/"cancel.json"
cancelled = apollo_setup.SetupWizard(default_config(), cancel_path, startup.append)
cancelled.show(); cancelled.reject(); assert not cancel_path.exists() and startup == [False]
app.close(); ui.detach_debug(); ui.timer.stop(); ui.orb.close(); temporary.cleanup()
assert ui.debug_log not in apollo.log.handlers
print("Native Qt overlay, retryable forms, editable keys, fallback, prices, full setup, hide/show: PASS", flush=True)
