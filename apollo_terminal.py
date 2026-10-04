"""Terminal-first onboarding, with finite effects and no additional UI runtime."""
from contextlib import contextmanager
from copy import deepcopy
import ctypes
import getpass
import os
from pathlib import Path
import sys
import shutil
import threading
import time

from apollo_config import api_key, normalize_config, save_config, ConfigError
from apollo_i18n import set_language, t

# Keep the setup copy separate from user text. English is the first-run default.
COPY = {
    "welcome": ("Make room for your voice.", "Platz für deine Stimme.", "让你的声音自由表达。"),
    "help": ("Enter to keep · /cancel to exit", "Enter behalten · /cancel beenden", "回车保留 · /cancel 退出"),
    "key": ("OpenRouter key (hidden)", "OpenRouter-Schlüssel (verdeckt)", "OpenRouter 密钥（隐藏）"),
    "checking": ("Checking access", "Zugang prüfen", "正在验证访问权限"),
    "keys": ("Your recording keys", "Deine Aufnahmetasten", "录音快捷键"),
    "models": ("Your models", "Deine Modelle", "模型选择"),
    "custom": ("Customize keys, language and models? y/n", "Tasten, Sprache und Modelle anpassen? y/n", "自定义快捷键、语言和模型？y/n"),
    "speech": ("Speech language (auto/de/en/...)", "Aufnahmesprache (auto/de/en/...)", "录音语言（auto/de/en/...）"),
    "behavior": ("Key behavior (toggle/hold)", "Tastenverhalten (toggle/hold)", "按键模式（toggle/hold）"),
    "primary": ("Primary transcription", "Haupttranskription", "主要转录模型"),
    "fallback": ("Fallback transcription (none to disable)", "Fallback-Transkription (none zum Abschalten)", "备用转录模型（none 表示禁用）"),
    "rewrite": ("Cleanup and prompt", "Bereinigen und Prompt", "文本整理和提示词"),
    "login": ("Start at Windows login? y/n", "Mit Windows starten? y/n", "随 Windows 登录启动？y/n"),
    "save": ("Save and start? y/n", "Speichern und starten? y/n", "保存并启动？y/n"),
    "done": ("Ready. Your voice, your words.", "Bereit. Deine Stimme, deine Worte.", "准备就绪。你的声音，你的表达。"),
    "yesno": ("Please enter y or n.", "Bitte y oder n eingeben.", "请输入 y 或 n。"),
    "empty": ("Please enter an OpenRouter key.", "Bitte einen OpenRouter-Schlüssel eingeben.", "请输入 OpenRouter 密钥。"),
    "catalog": ("Choose a number or a compatible model ID; Enter keeps your model.", "Nummer oder kompatible Modell-ID wählen; Enter behält dein Modell.", "输入编号或兼容模型 ID；回车保留当前模型。"),
    "unavailable": ("Catalog unavailable. Keeping your models; change them later in the app.", "Katalog nicht erreichbar. Modelle bleiben erhalten; später in der App ändern.", "模型目录暂不可用。保留当前模型，可稍后在应用中更改。"),
    "invalid": ("Choose a compatible model from the list.", "Bitte ein kompatibles Modell aus der Liste wählen.", "请选择列表中的兼容模型。"),
}


class Cancelled(Exception): pass


# Original Apollo GitHub banner, retained rather than inventing another font.
APOLLO_BANNER = (
    " █████╗ ██████╗  ██████╗ ██╗     ██╗      ██████╗ ",
    "██╔══██╗██╔══██╗██╔═══██╗██║     ██║     ██╔═══██╗",
    "███████║██████╔╝██║   ██║██║     ██║     ██║   ██║",
    "██╔══██║██╔═══╝ ██║   ██║██║     ██║     ██║   ██║",
    "██║  ██║██║     ╚██████╔╝███████╗███████╗╚██████╔╝",
    "╚═╝  ╚═╝╚═╝      ╚═════╝ ╚══════╝╚══════╝ ╚═════╝ ",
)


def wordmark(progress=1, color=False, compact=False, idle_phase=None):
    """Spectral intro followed by a slow silver shine while setup is active."""
    art = ("apollo",) if compact else APOLLO_BANNER
    palette = ((102,219,236),(114,145,243),(180,136,235),(234,164,214),(102,219,236))
    rows = []
    width = max(map(len, art))
    for y, line in enumerate(art):
        row = ""
        for x, glyph in enumerate(line):
            if glyph == " ": row += glyph; continue
            position = (x/max(1,width-1) + y/max(1,len(art)-1)) / 2
            reveal = min(1,max(0,(progress*1.5-position)*5))
            gradient = ((position-progress*.8)%1)*(len(palette)-1)
            index = min(len(palette)-2,int(gradient)); blend = gradient-index
            settle = min(1,max(0,(progress-.65)/.35))
            shine = max(0,1-abs(position-(progress*1.4-.2))/.16)*.75
            if idle_phase is not None:
                settle = 1
                shine = max(0,1-abs(position-((idle_phase*.22)%1.4-.2))/.18)
            rgb = []
            for channel in range(3):
                base = palette[index][channel]*(1-blend)+palette[index+1][channel]*blend
                base += (224-base)*settle
                if idle_phase is not None: base = 140+base*.18
                rgb.append(round((base+(255-base)*shine)*(.18+.82*reveal)))
            row += (f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m" if color else "") + glyph
        rows.append(row + ("\033[0m" if color else ""))
    return rows


@contextmanager
def console():
    """Allocate a console only for windowed builds; do not change the user's theme."""
    owned = False
    streams = (sys.stdin, sys.stdout, sys.stderr, sys.__stdin__, sys.__stdout__, sys.__stderr__)
    opened = []
    codepages = None
    kernel = ctypes.windll.kernel32 if os.name == "nt" else None
    mode = ctypes.c_ulong()
    output = None
    try:
        if kernel:
            kernel.GetStdHandle.restype = ctypes.c_void_p
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
            import msvcrt
            try: output = msvcrt.get_osfhandle(sys.stdout.fileno())
            except (OSError, ValueError): output = kernel.GetStdHandle(-11)
            kernel.GetConsoleMode.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
            kernel.SetConsoleMode.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
            if kernel.GetConsoleMode(output, ctypes.byref(mode)):
                kernel.SetConsoleMode(output, mode.value | 4)
            if owned:
                # Text selection must not pause onboarding output (Quick Edit).
                input_mode = ctypes.c_ulong(); input_handle = msvcrt.get_osfhandle(sys.stdin.fileno())
                if kernel.GetConsoleMode(input_handle, ctypes.byref(input_mode)):
                    kernel.SetConsoleMode(input_handle, (input_mode.value | 0x80) & ~0x40)
        yield owned
    finally:
        if sys.stdout is not None and sys.stdout.isatty():
            print("\033[0m\033[?25h", end="", flush=True)
        if kernel and output and mode.value: kernel.SetConsoleMode(output, mode.value)
        if codepages:
            kernel.SetConsoleCP(codepages[0]); kernel.SetConsoleOutputCP(codepages[1])
        for stream in opened: stream.close()
        sys.stdin, sys.stdout, sys.stderr, sys.__stdin__, sys.__stdout__, sys.__stderr__ = streams
        if owned: kernel.FreeConsole()


class Terminal:
    def __init__(self, language="en", read=None, secret=None, write=None, effects=True):
        self.language = language
        self.read = read or input; self.secret = secret or getpass.getpass
        self.write = write or (lambda value: print(value, flush=True))
        self.effects = effects and bool(sys.stdout and sys.stdout.isatty())
        self.introduced = False
        self.native_input = os.name == "nt" and read is None and secret is None and write is None and self.effects
        self.compact = False
        self.banner_top = None
        self.started_at = time.monotonic()

    def console_info(self):
        from ctypes import wintypes as w
        import msvcrt
        class Coord(ctypes.Structure): _fields_ = [("x",w.SHORT),("y",w.SHORT)]
        class Info(ctypes.Structure):
            _fields_ = [("size",Coord),("cursor",Coord),("attributes",w.WORD),("window",w.SMALL_RECT),("maximum",Coord)]
        info = Info()
        kernel = ctypes.windll.kernel32
        kernel.GetConsoleScreenBufferInfo.argtypes = (w.HANDLE,ctypes.POINTER(Info))
        return info if kernel.GetConsoleScreenBufferInfo(msvcrt.get_osfhandle(sys.stdout.fileno()),ctypes.byref(info)) else None

    def shimmer(self):
        if not self.native_input or self.banner_top is None: return
        info = self.console_info()
        rows = wordmark(color=True, compact=self.compact, idle_phase=time.monotonic()-self.started_at)
        if info is None or self.banner_top < info.window.Top or self.banner_top+len(rows)-1 > info.window.Bottom: return
        # Repaint only the visible banner, preserving input and its cursor.
        top = self.banner_top-info.window.Top+1
        sys.stdout.write("\0337" + f"\033[{top};1H" + "\r\n".join("  "+line+"\033[K" for line in rows) + "\0338")
        sys.stdout.flush()

    def text(self, key): return COPY[key][("en", "de", "zh").index(self.language)]

    def header(self, step, title):
        size = shutil.get_terminal_size((80,24))
        compact = size.columns < 54 or size.lines < 12
        self.compact = compact
        frames = 72 if self.effects and not self.introduced else 1
        try:
            if self.effects: self.write("\033[?25l\033[2J\033[H")
            if self.native_input:
                info = self.console_info()
                self.banner_top = info.window.Top if info else None
            for frame in range(frames):
                progress = frame/max(1,frames-1) if frames > 1 else 1
                lines = wordmark(progress, self.effects, compact) + [""]
                progress = "  ".join(("●" if i <= step else "○") for i in range(1,4))
                lines += [f"{progress}    {step} / 3", title]
                content = "\n".join("  "+line+("\033[K" if self.effects else "") for line in lines)
                self.write(("\033[H" if self.effects else "") + content)
                if self.effects and frames > 1: time.sleep(.03)
        finally:
            if self.effects: self.write("\033[0m\033[?25h")
        self.introduced = True

    def ask(self, label, current="", hidden=False):
        prompt = f"  {label}" + (f" [{current}]" if current and not hidden else "") + " › "
        if self.native_input:
            value = self.animated_input(prompt, hidden).strip()
        else:
            value = (self.secret if hidden else self.read)(prompt).strip()
        if value == "/cancel": raise Cancelled()
        return value or current

    def animated_input(self, prompt, hidden):
        import msvcrt
        chars = []
        sys.stdout.write(prompt); sys.stdout.flush()
        while True:
            self.shimmer()
            if not msvcrt.kbhit(): time.sleep(.12); continue
            char = msvcrt.getwch()
            if char in ("\x00", "\xe0"):
                msvcrt.getwch()  # Consume navigation keys, never insert their scan code.
                continue
            if char == "\x03": raise Cancelled()
            if char in ("\r", "\n"):
                sys.stdout.write("\n"); sys.stdout.flush()
                return "".join(chars)
            if char == "\b":
                if chars: chars.pop()
            elif char >= " " and char != "\x7f": chars.append(char)
            if not hidden:
                sys.stdout.write("\r" + prompt + "".join(chars) + "\033[K"); sys.stdout.flush()

    def yes(self, key, default="n"):
        while True:
            value = self.ask(self.text(key), default).lower()
            if value in ("y", "n"): return value == "y"
            self.write("  " + self.text("yesno"))

    def work(self, label, action):
        if not self.effects: return action()
        result, errors = [], []
        def run():
            try: result.append(action())
            except Exception as exc: errors.append(exc)
        thread = threading.Thread(target=run, daemon=True); thread.start()
        frame = 0
        try:
            self.write("\033[?25l")
            while thread.is_alive():
                self.shimmer()
                dots = "●"*(frame%4) + "·"*(3-frame%4)
                self.write(f"\033[1A\r  {dots}  {label}\033[K")
                frame += 1; thread.join(.08)
            self.write(f"\033[1A\r  · {label}\033[K")
        finally:
            self.write("\033[0m\033[?25h")
        if errors: raise errors[0]
        return result[0]

    def model(self, key, current, catalog, allow_none=False):
        from apollo_models import RECOMMENDATIONS
        kind = "text" if key == "rewrite" else "transcription"
        ordered = [m for m in RECOMMENDATIONS[kind] if m in catalog]
        ordered += [m for m in catalog if m not in ordered]
        self.write("\n  " + self.text(key))
        # The full catalog validates custom IDs; keep the terminal page compact.
        shown = ordered[:3]
        if current and current in catalog and current not in shown: shown.append(current)
        for i, model in enumerate(shown, 1):
            self.write(f"  {i:2}  {model} · {t(catalog[model]['price'], self.language)}")
        self.write("  " + self.text("catalog"))
        while True:
            value = self.ask(self.text(key), current or "none")
            if allow_none and value == "none": return None
            if value.isdigit() and 1 <= int(value) <= len(shown): value = shown[int(value)-1]
            if value == current or value in catalog: return value
            self.write("  " + self.text("invalid"))


def run_terminal_setup(cfg, path, set_autostart, *, terminal=None, check=None, discover=None):
    """Only save after validation and explicit completion; cancellation keeps all files."""
    from apollo_setup import check_key
    from apollo_models import discover_catalog, discover_price, RECOMMENDATIONS
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
                value = ui.ask(ui.text("key"), hidden=True)
                if value: cfg["smoothing"]["api_key"] = value
            key = api_key(cfg)
            if not key: ui.write("  " + ui.text("empty")); continue
            error = ui.work(ui.text("checking"), lambda: check(key))
            if not error: break
            ui.write("  " + t(error, language))
            if os.environ.get("OPENROUTER_API_KEY"):
                # A fixed environment key cannot be corrected in the form.
                ui.ask("OPENROUTER_API_KEY: /cancel", hidden=True)
        ui.header(2, ui.text("keys"))
        ui.write("  " + " · ".join(f"{key.upper()}: {mode}" for mode,key in cfg["hotkeys"].items()))
        customize = ui.yes("custom")
        if customize:
            while True:
                candidate = deepcopy(cfg)
                for mode in ("dictate", "polish", "prompt"):
                    candidate["hotkeys"][mode] = ui.ask(mode, cfg["hotkeys"][mode])
                candidate["hotkey_mode"] = ui.ask(ui.text("behavior"), cfg["hotkey_mode"])
                candidate["openrouter_stt"]["language"] = ui.ask(ui.text("speech"), cfg["openrouter_stt"]["language"] or "auto")
                try:
                    candidate, _ = normalize_config(candidate)
                    codes = set()
                    for key in candidate["hotkeys"].values():
                        found = set(keyboard.key_to_scan_codes(key))
                        if not found or codes & found: raise ConfigError("Bitte drei verschiedene gültige Tasten wählen.")
                        codes.update(found)
                    cfg = candidate; break
                except (ValueError, KeyError) as exc:
                    cfg = candidate
                    ui.write("  " + t(str(exc), language))
        ui.header(3, ui.text("models"))
        if customize:
            try:
                def catalog():
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        jobs = {kind: pool.submit(discover, kind) for kind in ("transcription", "text")}
                        result = {kind: job.result() for kind,job in jobs.items()}
                        # Audio billing units are absent from the models API.
                        models = set(RECOMMENDATIONS["transcription"]) | {cfg["openrouter_stt"]["model"], cfg["openrouter_stt"]["fallback_model"]}
                        prices = {model: pool.submit(discover_price, model) for model in models if model in result["transcription"]}
                        for model,job in prices.items():
                            try: result["transcription"][model]["price"] = job.result()
                            except Exception: result["transcription"][model]["price"] = "Preis nicht verfügbar"
                        return result
                catalogs = ui.work("OpenRouter", catalog)
            except Exception:
                ui.write("  " + ui.text("unavailable"))
            else:
                stt = cfg["openrouter_stt"]
                stt["model"] = ui.model("primary", stt["model"], catalogs["transcription"])
                while True:
                    stt["fallback_model"] = ui.model("fallback", stt["fallback_model"], catalogs["transcription"], True)
                    if stt["fallback_model"] != stt["model"]: break
                    ui.write("  " + t("Hauptmodell und Fallback müssen verschieden sein.", language))
                cfg["smoothing"]["model"] = ui.model("rewrite", cfg["smoothing"]["model"], catalogs["text"])
        ui.write("\n  " + cfg["openrouter_stt"]["model"] + "\n  → " + str(cfg["openrouter_stt"]["fallback_model"] or "none") + "\n  " + cfg["smoothing"]["model"] + "\n")
        autostart = ui.yes("login", "y")
        while True:
            if not ui.yes("save", "y"): return False
            try:
                cfg, _ = normalize_config(cfg)
                path = Path(path)
                if path.exists() and not path.with_name(path.name+".bak").exists():
                    path.with_name(path.name+".bak").write_bytes(path.read_bytes())
                save_config(path, cfg); set_autostart(autostart)
                break
            except OSError:
                ui.write("  " + t("Speichern fehlgeschlagen. Bitte erneut versuchen.", language))
        ui.write("\n  " + ui.text("done"))
        if ui.effects: time.sleep(.4)
        return True
    except (Cancelled, KeyboardInterrupt, EOFError):
        return False
