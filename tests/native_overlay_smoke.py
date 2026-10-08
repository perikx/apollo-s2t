"""Synthetic Qt interaction and rendered-output checks; no mic, paid API or clipboard."""
import argparse
import sys
from pathlib import Path
import tempfile
import types
import logging
import json
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for name in ("keyboard", "sounddevice", "pyperclip"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["keyboard"].key_to_scan_codes = lambda key: (sum(map(ord, key)),)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("out", nargs="?", default=None, help="screenshot folder (default: new temp dir)")
args = parser.parse_args()  # before the heavy imports so --help is fast
import apollo
from apollo_design import qt_app
import apollo_models
import apollo_widgets
import apollo_overlay
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea, QWidget, QAbstractScrollArea
from PySide6.QtTest import QTest
from apollo_config import default_config
from apollo_i18n import t, set_language
from PySide6.QtGui import QFontInfo, QFontDatabase
from PySide6.QtWidgets import QLabel
qt_app()
assert "Figtree" in QFontDatabase.families()
assert QFontInfo(qt_app().font()).family() == "Figtree"

output = Path(args.out or tempfile.mkdtemp(prefix="apollo-smoke-")).resolve(); output.mkdir(parents=True, exist_ok=True)
temporary = tempfile.TemporaryDirectory(); apollo.BASE_DIR = temporary.name
app = apollo.App({"ui_language": "de", "beep": False, "hotkeys": {"dictate": "n", "polish": "m", "prompt": "p"}})
apollo.log.setLevel(logging.INFO)
backup = app.recovery.create(16000, 1, "polish", hotkey="m")
backup.append(b"\x00\x00"*16000); backup.finish()
backup.save_transcript("Das ist wichtig. Wirklich wichtig. Bitte behalte meine Wiederholungen.", final=True); backup.update(state="ready")

def start(self, reload=False):
    self.started = True
    speech = ["microsoft/mai-transcribe-2", "elevenlabs/scribe-v2", "google/gemini-3.5-transcribe",
              "microsoft/mai-transcribe-1.5", "mistralai/voxtral-small-24b-2507-stt", "x-ai/grok-stt-1.0", "openai/whisper-1"]
    self.data = {"transcription": {m: {"name": m, "price": ("audio", apollo_models.STT[m][3])} for m in speech},
        "text": {"google/gemini-3.5-flash-lite": {"name": "Google: Gemini 3.5 Flash Lite", "price": ("tokens", 0.1, 0.4)}}}
    self.data["transcription"]["vendor/unranked-stt"] = {"name": "Vendor: Unranked", "price": None}
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
area = ui.content.findChild(QScrollArea)
assert area.horizontalScrollBar().maximum() == 0
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
MORE = Qt.ItemDataRole.UserRole + 2
listed = lambda: [picker.items.item(i).data(Qt.ItemDataRole.UserRole) for i in range(picker.items.count()) if not picker.items.item(i).data(MORE)]
# Ranked models first, in ranking order; the unranked one hides behind "Show all models".
assert listed() == [None, "microsoft/mai-transcribe-2", "elevenlabs/scribe-v2", "google/gemini-3.5-transcribe",
                    "microsoft/mai-transcribe-1.5", "mistralai/voxtral-small-24b-2507-stt", "x-ai/grok-stt-1.0", "openai/whisper-1"]
more = [picker.items.item(i) for i in range(picker.items.count()) if picker.items.item(i).data(MORE)]
assert [item.text() for item in more] == ["Alle Modelle anzeigen (1)"]
picker.items.setCurrentItem(more[0]); picker.apply()
assert listed()[-1] == "vendor/unranked-stt"
picker.items.setCurrentItem([picker.items.item(i) for i in range(picker.items.count()) if picker.items.item(i).data(MORE)][0]); picker.apply()
assert "vendor/unranked-stt" not in listed()
assert picker.items.item(1).data(Qt.ItemDataRole.UserRole+1)[1] == "$1.67 / 1000 Min."
qt_app().processEvents()
assert picker.items.verticalScrollBar().maximum() > 0
screenshot("recommended-models.png", ui.dialog)
picker.search.setText("whisper-1"); assert picker.items.count() == 1
assert picker.items.item(0).sizeHint().height() == 44  # ranked models carry a note line
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
app.recorder.visual_levels = lambda: tuple([.1, .3, .5, .75, .9, .65, .4, .1, 0]*3)
app.recorder._byte_count = 16000*65*2
size = ui.orb.size(); ui.tick(); screenshot("recording.png", ui.orb)
assert ui.timer.interval() == 33  # fast only while recording
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
app.recording = False; app._busy_recordings.add(backup.id); ui.status = "Ready"; ui.refresh_debug()
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
ui.tick(); assert ui.timer.interval() == 100  # slow when idle
with patch.object(ui.orb, "update") as redraw:
    ui.tick(); assert not redraw.called  # unchanged idle state never repaints
# Right-click at the logo opens a small menu; Hide persists and tray Show restores.
event = types.SimpleNamespace(pos=lambda: ui.orb.CENTER.toPoint(), globalPos=lambda: ui.orb.mapToGlobal(ui.orb.CENTER.toPoint()), accept=lambda: None)
ui.orb.contextMenuEvent(event); qt_app().processEvents()
assert ui.orb.context_menu.isVisible() and ui.orb.context_menu.actions()[0].text() == "Im Tray ausblenden"
ui.orb.context_menu.actions()[0].trigger(); ui.orb.context_menu.close()
assert not ui.visible and not app.cfg["overlay"]["visible"] and not ui.orb.isVisible()
app.open_panel(); ui.tick(); assert ui.visible and ui.orb.isVisible() and app.cfg["overlay"]["visible"]
# Tray: localized Show / Quit; Show brings a hidden logo back.
assert ui.tray.isVisible()
menu = ui.tray.contextMenu(); menu.aboutToShow.emit()
assert [action.text() for action in menu.actions()] == ["apollo s2t öffnen", "Beenden"]
ui.hide(); menu.actions()[0].trigger(); ui.tick(); assert ui.visible and ui.expanded and ui.orb.isVisible()
ui.hide(); ui.tray.activated.emit(apollo_overlay.QSystemTrayIcon.ActivationReason.Trigger); ui.tick(); assert ui.visible and ui.orb.isVisible()
menu.actions()[1].trigger(); assert app._closing.is_set()
ui.tray.hide()

app.close(); ui.detach_debug(); ui.timer.stop(); ui.orb.close(); temporary.cleanup()
assert ui.debug_log not in apollo.log.handlers
print("Native Qt overlay, retryable forms, editable keys, fallback, prices, hide/show: PASS", flush=True)
