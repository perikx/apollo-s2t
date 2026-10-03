"""Run manually on Windows: python tests/native_overlay_smoke.py OUTPUT_DIR.

Synthetic state only: no microphone, global hooks, API calls or clipboard writes.
"""
import sys
from pathlib import Path
import tempfile
import types
import ctypes
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for name in ("keyboard", "sounddevice", "pyperclip", "mouse"):
    sys.modules[name] = types.ModuleType(name)
import apollo
import apollo_overlay
from PIL import ImageGrab

output = Path(sys.argv[1]).resolve()
output.mkdir(parents=True, exist_ok=True)
temporary = tempfile.TemporaryDirectory()
apollo.BASE_DIR = temporary.name
app = apollo.App({"beep": False, "hotkeys": {"dictate": "n", "polish": "m", "prompt": "p"}})
backup = app.recovery.create(16000, 1, "polish", hotkey="m")
backup.append(b"\x00\x00" * 16000)
backup.finish()
backup.save_transcript("Das ist wichtig. Wirklich wichtig. Bitte behalte meine Wiederholungen.", final=True)
backup.update(state="ready")
apollo_overlay.discover_models = lambda kind: {"microsoft/mai-transcribe-2": "MAI 2"} if kind == "transcription" else {"google/gemini-3.5-flash-lite": "Gemini"}
ui = apollo_overlay.FloatingUI(app, app.close, apollo.make_tray_image())
ui.x, ui.y = 850, 450
ui.show()

def screenshot(name, window):
    ui.root.update()
    ctypes.windll.dwmapi.DwmFlush()
    time.sleep(0.15)
    x, y = window.winfo_rootx(), window.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x+window.winfo_width(), y+window.winfo_height()), all_screens=True).save(output / name)

def run():
    try:
        def click(x, y):
            ui.canvas.event_generate("<ButtonPress-1>", x=x, y=y)
            ui.canvas.event_generate("<ButtonRelease-1>", x=x, y=y)
            ui.root.update()
        click(178, 124)
        assert ui.expanded
        screenshot("overlay.png", ui.orb)
        click(103, 51)
        assert ui.page == "recovery"
        assert "M · Bereinigen" in ui.recovery_list.get(0)
        assert "Wirklich wichtig" in ui.preview_text.get("1.0", "end")
        screenshot("recovery.png", ui.dialog)
        click(66, 124)
        assert ui.page == "settings"
        screenshot("settings.png", ui.dialog)
        click(103, 197)
        assert ui.page == "models"
        ui.root.update()
        ui.tick()
        original = app.cfg["openrouter_stt"]["model"]
        ui.model_vars["transcription"].set("chat/unsupported")
        ui.save_models()
        assert app.cfg["openrouter_stt"]["model"] == original
        ui.model_vars["transcription"].set(original)
        screenshot("models.png", ui.dialog)
        click(239, 124)
        assert not ui.expanded and ui.dialog is None
        app.recording, app.active_mode = True, "dictate"
        ui.levels.extend([.03, .08, .15, .24, .4, .24, .15, .08, .03])
        ui.draw()
        screenshot("recording.png", ui.orb)
        app.recording = False
        ui.x = ui.bounds()[2] - 5
        ui.moved = True
        ui.release(types.SimpleNamespace(x=178, y=124))
        assert ui.orb.state() == "withdrawn"
        assert not app.cfg["overlay"]["visible"]
        app.notify("Simulated 429, audio saved")
        ui.tick()
        assert ui.orb.state() == "withdrawn"
        app.open_panel("recovery")
        ui.tick()
        assert ui.visible and ui.page == "recovery"
        ui.collapse()
        assert ui.page is None and not ui.expanded
        print("Native overlay, recovery preview, settings, models, waveform, hide/show: PASS", flush=True)
    finally:
        app.close()
        ui.root.destroy()
        temporary.cleanup()

ui.root.after(1000, run)
ui.run()
