"""Synthetic Qt interaction and rendered-output checks; no mic, paid API or clipboard."""
import sys
from pathlib import Path
import tempfile
import types
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for name in ("keyboard", "sounddevice", "pyperclip", "mouse"):
    sys.modules[name] = types.ModuleType(name)
sys.modules["keyboard"].key_to_scan_codes = lambda key: (sum(map(ord, key)),)
import apollo
from apollo_design import qt_app
import apollo_widgets
import apollo_overlay
import apollo_setup
from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtTest import QTest
from apollo_widgets import ModelPicker
from apollo_config import default_config

output = Path(sys.argv[1]).resolve(); output.mkdir(parents=True, exist_ok=True)
temporary = tempfile.TemporaryDirectory(); apollo.BASE_DIR = temporary.name
app = apollo.App({"beep": False, "hotkeys": {"dictate": "n", "polish": "m", "prompt": "p"}})
backup = app.recovery.create(16000, 1, "polish", hotkey="m")
backup.append(b"\x00\x00"*16000); backup.finish()
backup.save_transcript("Das ist wichtig. Wirklich wichtig. Bitte behalte meine Wiederholungen.", final=True); backup.update(state="ready")

def start(self, reload=False):
    self.started = True
    self.data = {"transcription": {
        "microsoft/mai-transcribe-2": {"name": "Microsoft: MAI Transcribe 2", "price": "Audio: $0.1 / Std. Audio"},
        "microsoft/mai-transcribe-1.5": {"name": "Microsoft: MAI Transcribe 1.5", "price": "Audio: $0.22 / Std. Audio"},
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
screenshot("overlay.png", ui.orb)
click("recovery"); assert ui.page == "recovery"
assert "M · Bereinigen" in ui.recovery_list.item(0).text()
assert "Wirklich wichtig" in ui.preview_text.toPlainText()
screenshot("recovery.png", ui.dialog)
click("settings"); assert ui.page == "settings"
ui.minutes.setText("bad"); ui.save_settings()
assert ui.dialog.isVisible() and ui.minutes.text() == "bad" and "5 bis 60" in ui.result.text()
QTest.keyClick(ui.minutes, Qt.Key.Key_Return); assert ui.dialog.isVisible()
ui.minutes.setText("10")
QTest.mouseClick(ui.key_fields["dictate"], Qt.MouseButton.LeftButton)
QTest.keyClick(ui.key_fields["dictate"], Qt.Key.Key_K)
assert ui.key_fields["dictate"].value == "k"
ui.save_settings(); assert app.cfg["hotkeys"]["dictate"] == "k" and app.cfg["recovery_cache"]["minutes"] == 10
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
picker = ModelPicker(ui.model_fields["fallback"]); picker.show(); qt_app().processEvents()
assert picker.items.count() == 4
picker.search.setText("Whisper"); assert picker.items.count() == 1
picker.items.setCurrentRow(0); screenshot("picker.png", picker); picker.apply()
assert ui.model_fields["fallback"].value == "openai/whisper-1"
ui.save_models(); assert app.cfg["openrouter_stt"]["fallback_model"] == "openai/whisper-1"
click("close"); assert ui.dialog is None and not ui.expanded
app.recording = True; app.active_mode = "dictate"
app.recorder.visual_levels = tuple([.1, .3, .5, .75, .9, .65, .4, .1, 0]*3)
app.recorder._sample_count = 16000*65
ui.tick(); screenshot("recording.png", ui.orb)
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

startup = []; config_path = Path(temporary.name)/"wizard.json"
wizard = apollo_setup.SetupWizard(default_config(), config_path, startup.append)
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
app.close(); ui.timer.stop(); ui.orb.close(); temporary.cleanup()
print("Native Qt overlay, retryable forms, editable keys, fallback, prices, full setup, hide/show: PASS", flush=True)
