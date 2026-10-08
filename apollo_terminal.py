"""Terminal-first onboarding. Plain print, input and getpass; no effects, no UI runtime."""
from contextlib import contextmanager
from copy import deepcopy
import ctypes
import getpass
import os
from pathlib import Path
import sys

from apollo_config import api_key, backup_config, normalize_config, save_config, scan_codes_of
from apollo_i18n import set_language, t
from apollo_models import curated, discover_catalog, model_note, price_text


class Cancelled(Exception): pass


@contextmanager
def console():
    """Allocate a console only for windowed builds; do not change the user's theme."""
    owned = False
    streams = (sys.stdin, sys.stdout, sys.stderr, sys.__stdin__, sys.__stdout__, sys.__stderr__)
    opened = []
    codepages = None
    kernel = ctypes.windll.kernel32 if os.name == "nt" else None
    try:
        if kernel:
            windowed = getattr(sys, "frozen", False) or sys.executable.lower().endswith("pythonw.exe")
            if sys.stdin is None or sys.stdout is None or (windowed and not kernel.GetConsoleWindow()):
                if not kernel.AllocConsole():
                    raise OSError("Could not open the setup terminal.")
                owned = True
                kernel.SetConsoleTitleW("apollo s2t · Setup")
                codepages = (kernel.GetConsoleCP(), kernel.GetConsoleOutputCP())
                kernel.SetConsoleCP(65001); kernel.SetConsoleOutputCP(65001)
                for name, path, access in (("stdin", "CONIN$", "r"), ("stdout", "CONOUT$", "w"), ("stderr", "CONOUT$", "w")):
                    stream = open(path, access, encoding="utf-8", buffering=1)
                    setattr(sys, name, stream); opened.append(stream)
                    setattr(sys, "__"+name+"__", stream)
        yield owned
    finally:
        if codepages:
            kernel.SetConsoleCP(codepages[0]); kernel.SetConsoleOutputCP(codepages[1])
        for stream in opened: stream.close()
        sys.stdin, sys.stdout, sys.stderr, sys.__stdin__, sys.__stdout__, sys.__stderr__ = streams
        if owned: kernel.FreeConsole()


class Terminal:
    def __init__(self, language="en", read=None, secret=None, write=None):
        self.language = language
        self.read = read or input; self.secret = secret or getpass.getpass
        self.write = write or (lambda value: print(value, flush=True))

    def text(self, english): return t(english, self.language)

    def header(self, step, title):
        self.write(f"\n  apollo s2t · {step} / 3\n  {title}")

    def ask(self, label, current="", hidden=False):
        prompt = f"  {label}" + (f" [{current}]" if current and not hidden else "") + " › "
        value = (self.secret if hidden else self.read)(prompt).strip()
        if value == "/cancel": raise Cancelled()
        return value or current

    def yes(self, question, default="n"):
        while True:
            value = self.ask(self.text(question), default).lower()
            if value in ("y", "n"): return value == "y"
            self.write("  " + self.text("Please enter y or n."))

    def model(self, title, kind, current, catalog, allow_none=False):
        ranked = curated(kind)
        self.write("\n  " + self.text(title))
        # The full catalog validates custom IDs; show the best three and the current model.
        shown = [m for m in ranked if m in catalog][:3]
        if current and current in catalog and current not in shown: shown.append(current)
        for i, model in enumerate(shown, 1):
            parts = [model, price_text(catalog[model]["price"]), model_note(model, ranked, None)]
            self.write(f"  {i:2}  " + " · ".join(filter(None, parts)))
        self.write("  " + self.text("Choose a number or a compatible model ID; Enter keeps your model."))
        while True:
            value = self.ask(self.text(title), current or "none")
            if allow_none and value == "none": return None
            if value.isdigit() and 1 <= int(value) <= len(shown): value = shown[int(value)-1]
            if value == current or value in catalog: return value
            self.write("  " + self.text("Choose a compatible model from the list."))


def run_terminal_setup(cfg, path, set_autostart, *, terminal=None, check=None, discover=None):
    """Only save after validation and explicit completion; cancellation keeps all files."""
    from apollo_api import check_key
    import keyboard
    cfg = deepcopy(cfg); ui = terminal or Terminal(cfg["ui_language"])
    check = check or check_key; discover = discover or discover_catalog
    try:
        ui.header(1, "English · Deutsch · 简体中文")
        while True:
            language = ui.ask("Language / Sprache / 语言 (en/de/zh)", cfg["ui_language"])
            if language in ("en", "de", "zh"): break
        cfg["ui_language"] = ui.language = language; set_language(language)
        ui.header(1, "OpenRouter")
        ui.write("  openrouter.ai/keys · /cancel\n")
        while True:
            if not os.environ.get("OPENROUTER_API_KEY"):
                value = ui.ask(ui.text("OpenRouter key (hidden)"), hidden=True)
                if value: cfg["smoothing"]["api_key"] = value
            key = api_key(cfg)
            if not key: ui.write("  " + ui.text("Please enter an OpenRouter key.")); continue
            ui.write("  " + ui.text("Checking access") + " …")
            error = check(key)
            if not error: break
            ui.write("  " + t(error, language))
            if os.environ.get("OPENROUTER_API_KEY"):
                # A fixed environment key cannot be corrected in the form.
                ui.ask("OPENROUTER_API_KEY: /cancel", hidden=True)
        ui.header(2, ui.text("Your recording keys"))
        ui.write("  " + " · ".join(f"{key.upper()}: {mode}" for mode,key in cfg["hotkeys"].items()))
        customize = ui.yes("Customize keys, language and models? y/n")
        if customize:
            while True:
                candidate = deepcopy(cfg)
                for mode in ("dictate", "polish", "prompt"):
                    candidate["hotkeys"][mode] = ui.ask(mode, cfg["hotkeys"][mode])
                candidate["hotkey_mode"] = ui.ask(ui.text("Key behavior (toggle/hold)"), cfg["hotkey_mode"])
                candidate["openrouter_stt"]["language"] = ui.ask(ui.text("Speech language (auto/de/en/...)"), cfg["openrouter_stt"]["language"] or "auto")
                try:
                    candidate, _ = normalize_config(candidate)
                    scan_codes_of(candidate["hotkeys"].values(), keyboard.key_to_scan_codes)
                    cfg = candidate; break
                except (ValueError, KeyError) as exc:
                    cfg = candidate
                    ui.write("  " + t(str(exc), language))
        ui.header(3, ui.text("Your models"))
        if customize:
            try:
                catalogs = {kind: discover(kind) for kind in ("transcription", "text")}
            except Exception:
                ui.write("  " + ui.text("Catalog unavailable. Keeping your models; change them later in the app."))
            else:
                stt = cfg["openrouter_stt"]
                stt["model"] = ui.model("Primary transcription", "transcription", stt["model"], catalogs["transcription"])
                while True:
                    stt["fallback_model"] = ui.model("Fallback transcription (none to disable)", "transcription", stt["fallback_model"], catalogs["transcription"], True)
                    if stt["fallback_model"] != stt["model"]: break
                    ui.write("  " + t("Primary and fallback models must be different.", language))
                cfg["smoothing"]["model"] = ui.model("Cleanup and prompt", "text", cfg["smoothing"]["model"], catalogs["text"])
        ui.write("\n  " + cfg["openrouter_stt"]["model"] + "\n  → " + str(cfg["openrouter_stt"]["fallback_model"] or "none") + "\n  " + cfg["smoothing"]["model"] + "\n")
        autostart = ui.yes("Start at Windows login? y/n", "y")
        while True:
            if not ui.yes("Save and start? y/n", "y"): return False
            try:
                cfg, _ = normalize_config(cfg)
                path = Path(path)
                if path.exists(): backup_config(path, path.read_bytes())
                save_config(path, cfg); set_autostart(autostart)
                break
            except OSError:
                ui.write("  " + t("Speichern fehlgeschlagen. Bitte erneut versuchen.", language))
        ui.write("\n  " + ui.text("Ready. Your voice, your words."))
        return True
    except (Cancelled, KeyboardInterrupt, EOFError):
        return False
