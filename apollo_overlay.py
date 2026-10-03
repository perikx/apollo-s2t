"""Small native desktop controls. All Tk calls stay on the main thread."""
from collections import deque
from datetime import datetime
import ctypes
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk

from apollo_models import discover_models

BG = "#171b24"
FG = "#f0f2f7"
MUTED = "#aab3c5"
ACCENT = "#ffb000"
TRANSPARENT = "#010203"
LABELS = {"dictate": "Diktieren", "polish": "Bereinigen", "prompt": "Prompt"}


class FloatingUI:
    def __init__(self, app, on_quit, logo=None):
        if os.name == "nt":
            ctypes.windll.user32.SetProcessDPIAware()
        self.app, self.on_quit = app, on_quit
        self.events = app.ui_events
        self.root = tk.Tk()
        self.root.tk.call("tk", "scaling", 1.333333)
        self.root.withdraw()
        self.root.title("Apollo")
        self.root.configure(bg=BG)
        self.root.option_add("*Font", "{Segoe UI} 10")
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TCombobox", padding=7)
        style.configure("TCombobox", fieldbackground="#222938", background="#293243",
                        foreground=FG, arrowcolor=FG, bordercolor="#343e50")
        style.map("TCombobox", fieldbackground=[("readonly", "#222938")],
                  foreground=[("readonly", FG)], selectbackground=[("!disabled", "#46516a")])
        self.orb = tk.Toplevel(self.root)
        self.orb.withdraw()
        self.orb.overrideredirect(True)
        self.orb.attributes("-topmost", True)
        self.orb.configure(bg=TRANSPARENT)
        if os.name == "nt":
            self.orb.attributes("-transparentcolor", TRANSPARENT)
            self.orb.attributes("-toolwindow", True)
        self.canvas = tk.Canvas(self.orb, width=270, height=248, bg=TRANSPARENT,
                                highlightthickness=0)
        self.canvas.pack()
        self.expanded = False
        self.visible = app.cfg["overlay"]["visible"]
        self.dialog = None
        self.page = None
        self.status = "Bereit"
        self.levels = deque([0.0] * 9, maxlen=9)
        self.drag = None
        self.moved = False
        self.logo = None
        if logo is not None:
            from PIL import Image, ImageOps, ImageTk
            self.logo = ImageTk.PhotoImage(
                ImageOps.contain(logo, (42, 42), Image.Resampling.LANCZOS), master=self.root)
            self.root.iconphoto(True, self.logo)
        self.x = app.cfg["overlay"]["x"]
        self.y = app.cfg["overlay"]["y"]
        self.x = self.root.winfo_screenwidth() - 100 if self.x is None else self.x
        self.y = self.root.winfo_screenheight() // 2 if self.y is None else self.y
        self.place()
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.motion)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.orb.bind("<Escape>", lambda e: self.collapse())
        self.orb.update_idletasks()
        if os.name == "nt":
            from ctypes import wintypes
            user = ctypes.windll.user32
            user.GetParent.argtypes = (wintypes.HWND,)
            user.GetParent.restype = wintypes.HWND
            user.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
            user.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_long)
            hwnd = user.GetParent(self.orb.winfo_id())
            user.SetWindowLongW(hwnd, -20, user.GetWindowLongW(hwnd, -20) | 0x08000000)
        if self.visible:
            self.orb.deiconify()
        self.tick_count = 0
        self.tick()

    def bounds(self):
        if os.name == "nt":
            from ctypes import wintypes
            class MonitorInfo(ctypes.Structure):
                _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT),
                            ("work", wintypes.RECT), ("flags", wintypes.DWORD)]
            user = ctypes.windll.user32
            user.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
            user.MonitorFromPoint.restype = wintypes.HANDLE
            user.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MonitorInfo))
            info = MonitorInfo()
            info.size = ctypes.sizeof(info)
            monitor = user.MonitorFromPoint(wintypes.POINT(self.x, self.y), 2)
            if user.GetMonitorInfoW(monitor, ctypes.byref(info)):
                r = info.work
                return r.left, r.top, r.right, r.bottom
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def place(self):
        left, top, right, bottom = self.bounds()
        self.x = max(left + (166 if self.expanded else 32), min(right - 76, self.x))
        self.y = max(top + 124, min(bottom - 124, self.y))
        # Windows coordinates may be negative on a secondary monitor.
        self.orb.geometry(f"270x248+{self.x - 178}+{self.y - 124}")

    def persist_position(self):
        try:
            self.app.update_preferences({"overlay": {"visible": self.visible, "x": self.x, "y": self.y}})
        except (OSError, ValueError):
            self.status = "Position konnte nicht gespeichert werden"

    def hide(self):
        self.visible = False
        self.expanded = False
        self.orb.withdraw()
        self.close_dialog()
        self.persist_position()

    def show(self):
        self.visible = True
        self.place()
        self.orb.deiconify()
        self.persist_position()

    def collapse(self):
        self.expanded = False
        self.close_dialog()
        self.draw()

    def hit(self, x, y):
        circles = [(178, 124, 30, "logo")]
        if self.expanded:
            circles += [(103, 51, 29, "recovery"), (66, 124, 29, "settings"),
                        (103, 197, 29, "models"), (239, 124, 18, "close")]
        for cx, cy, radius, action in circles:
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2:
                return action

    def press(self, event):
        self.pressed = self.hit(event.x, event.y)
        self.moved = False
        if self.pressed == "logo":
            self.drag = (event.x_root, event.y_root, self.x, self.y)

    def motion(self, event):
        if self.drag:
            dx, dy = event.x_root - self.drag[0], event.y_root - self.drag[1]
            if abs(dx) + abs(dy) > 5:
                self.moved = True
                self.x, self.y = self.drag[2] + dx, self.drag[3] + dy
                self.orb.geometry(f"270x248+{self.x - 178}+{self.y - 124}")

    def release(self, event):
        self.drag = None
        if self.moved:
            if self.x >= self.bounds()[2] - 24:
                self.hide()
            else:
                self.place()
                self.persist_position()
            return
        action = self.hit(event.x, event.y)
        if action != getattr(self, "pressed", None):
            return
        if action == "logo":
            self.expanded = not self.expanded
            self.place()
            if not self.expanded:
                self.close_dialog()
        elif action == "close":
            self.collapse()
        elif action:
            self.open_page(action)
        self.draw()

    def draw(self):
        c = self.canvas
        c.delete("all")
        active = self.app.recording
        color = ACCENT if active else "#343e50"
        c.create_oval(146, 92, 210, 156, fill=BG, outline=color, width=2)
        if active:
            for i, level in enumerate(self.levels):
                h = max(3, min(35, level * 100))
                x = 158 + i * 5
                c.create_line(x, 124 - h / 2, x, 124 + h / 2, fill=ACCENT, width=3, capstyle="round")
            key = self.app.cfg["hotkeys"].get(self.app.active_mode, "")
            self.badge(178, 174, key.upper())
        elif self.logo:
            c.create_image(178, 124, image=self.logo)
        else:
            c.create_text(178, 124, text="A", fill=ACCENT, font=("Segoe UI", 22, "bold"))
        if self.status != "Bereit" and not active:
            c.create_oval(201, 94, 211, 104, fill=ACCENT, outline=BG)
        if not active and self.app._busy_recordings:
            c.create_arc(142, 88, 214, 160, start=(self.tick_count * 12) % 360,
                         extent=90, outline=ACCENT, width=2, style="arc")
        if self.expanded:
            for x, y, glyph, label in [(103, 51, "↶", "Recovery"), (66, 124, "≡", "Einstellungen"),
                                        (103, 197, "◇", "Modelle")]:
                c.create_oval(x-27, y-27, x+27, y+27, fill=BG, outline="#343e50", width=1)
                c.create_text(x, y-3, text=glyph, fill=FG, font=("Segoe UI", 20))
                self.badge(x, y+37, label)
            c.create_oval(222, 107, 256, 141, fill=BG, outline="#343e50")
            c.create_text(239, 123, text="×", fill=MUTED, font=("Segoe UI", 19))

    def badge(self, x, y, text):
        label = self.canvas.create_text(x, y, text=text, fill=FG, font=("Segoe UI", 9))
        bounds = self.canvas.bbox(label)
        plate = self.canvas.create_rectangle(bounds[0]-5, bounds[1]-2, bounds[2]+5, bounds[3]+2,
                                              fill=BG, outline=BG)
        self.canvas.tag_lower(plate, label)

    def tick(self):
        if self.app._closing.is_set():
            self.root.destroy()
            return
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.status = value
                if self.page == "recovery":
                    self.refresh_recovery()
            elif kind == "open":
                self.show()
                if value:
                    self.open_page(value)
            elif kind == "catalog":
                self.catalog_result(*value)
            elif kind == "refresh" and self.page == "recovery":
                self.refresh_recovery()
        self.levels.append(getattr(self.app.recorder, "level", 0.0) if self.app.recording else 0.0)
        self.draw()
        self.tick_count += 1
        if self.tick_count % 300 == 0:
            self.app.prune_recovery()
            if self.page == "recovery":
                self.refresh_recovery()
        self.root.after(50, self.tick)

    def close_dialog(self):
        if self.dialog is not None:
            self.app.ui_windows.clear()
            self.dialog.destroy()
        self.dialog = None
        self.page = None

    def label(self, parent, text, **kwargs):
        widget = tk.Label(parent, text=text, bg=BG, fg=FG, anchor="w", **kwargs)
        widget.pack(fill="x", pady=(12, 4))
        return widget

    def button(self, parent, text, command):
        widget = tk.Button(parent, text=text, command=command, bg="#293243", fg=FG,
                           activebackground="#3a465c", activeforeground=FG, relief="flat",
                           padx=12, pady=8, cursor="hand2")
        widget.pack(side="left", padx=(0, 8), pady=12)
        return widget

    def open_page(self, page):
        self.close_dialog()
        self.page = page
        window = self.dialog = tk.Toplevel(self.root)
        window.title("Apollo · " + {"recovery": "Recovery", "settings": "Einstellungen", "models": "Modelle"}[page])
        window.configure(bg=BG)
        window.attributes("-topmost", True)
        height = 440 if page == "models" else 560
        window.minsize(520, height)
        left, top, right, bottom = self.bounds()
        window.geometry(f"520x{height}+{max(left, min(right-520, self.x-740))}+{max(top, min(bottom-height-40, self.y-220))}")
        window.protocol("WM_DELETE_WINDOW", self.close_dialog)
        window.bind("<Escape>", lambda e: self.close_dialog())
        body = tk.Frame(window, bg=BG, padx=24, pady=12)
        body.pack(fill="both", expand=True)
        if page == "recovery":
            self.recovery_page(body)
        elif page == "settings":
            self.settings_page(body)
        else:
            self.models_page(body)
        window.update_idletasks()
        if os.name == "nt":
            self.app.ui_windows.add(ctypes.windll.user32.GetParent(window.winfo_id()))

    def recovery_page(self, body):
        self.label(body, "Letzte Aufnahmen", font=("Segoe UI", 18, "bold"))
        cache = self.app.cfg["recovery_cache"]
        self.label(body, f'{cache["minutes"]} Minuten · max. {cache["max_entries"]} Aufnahmen · {cache["max_mb"]} MB')
        self.recovery_list = tk.Listbox(body, height=5, bg="#222938", fg=FG, relief="flat",
                                       selectbackground="#46516a", exportselection=False)
        self.recovery_list.pack(fill="x", pady=8)
        self.recovery_list.bind("<<ListboxSelect>>", lambda e: self.preview())
        self.preview_text = tk.Text(body, bg="#222938", fg=FG, wrap="word", relief="flat", height=8,
                                    padx=12, pady=12, state="disabled")
        self.preview_text.pack(fill="both", expand=True)
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x")
        self.button(row, "Wiederherstellen", self.recover_selected)
        self.button(row, "Text kopieren", self.copy_selected)
        self.button(row, "Löschen", self.delete_selected)
        self.recovery_status = self.label(body, self.status, wraplength=465)
        self.refresh_recovery()

    def selected(self):
        choice = self.recovery_list.curselection()
        return self.entries[choice[0]] if choice and choice[0] < len(self.entries) else None

    def refresh_recovery(self):
        old = self.selected().id if hasattr(self, "entries") and self.selected() else None
        self.entries = self.app.recovery_items()
        self.recovery_list.delete(0, "end")
        index = 0
        for i, entry in enumerate(self.entries):
            meta = entry.metadata
            stamp = datetime.fromisoformat(meta["created_at"]).astimezone().strftime("%H:%M:%S")
            key = meta.get("hotkey") or "Taste unbekannt"
            label = LABELS.get(meta["mode"], meta["mode"])
            state = "Text" if entry.read_transcript() else "Erneut versuchen"
            self.recovery_list.insert("end", f'{stamp}   {key.upper()} · {label}   {state}')
            if entry.id == old:
                index = i
        if self.entries:
            self.recovery_list.selection_set(index)
        self.recovery_status.configure(text=self.status)
        self.preview()

    def preview(self):
        entry = self.selected()
        try:
            text = entry.read_transcript() if entry else "Noch keine Aufnahme im Cache."
        except (OSError, ValueError):
            text = "Aufnahme nicht mehr verfügbar."
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", text or "Audio gesichert. Wiederherstellen versucht die Transkription erneut.")
        self.preview_text.configure(state="disabled")

    def recover_selected(self):
        entry = self.selected()
        if entry:
            self.app.recover(entry.id)
            self.status = "Wiederherstellung läuft …"
            self.refresh_recovery()

    def copy_selected(self):
        entry = self.selected()
        if entry:
            self.app.copy_recovery(entry.id)

    def delete_selected(self):
        entry = self.selected()
        if entry:
            self.app.delete_recovery(entry.id)
            self.refresh_recovery()

    def settings_page(self, body):
        self.label(body, "Einstellungen", font=("Segoe UI", 18, "bold"))
        for mode, label in LABELS.items():
            self.label(body, f'{self.app.cfg["hotkeys"][mode].upper()} · {label}')
        self.label(body, "Prompt-Profil")
        profile = tk.StringVar(value=self.app.cfg["prompt_profiles"]["active"])
        ttk.Combobox(body, textvariable=profile, values=self.app.available_profiles(), state="readonly").pack(fill="x")
        self.label(body, "Prompt-Sprache")
        language = tk.StringVar(value=self.app.cfg["prompt_profiles"]["output_language"])
        ttk.Combobox(body, textvariable=language, values=("english", "german", "match")).pack(fill="x")
        self.label(body, "Recovery behalten (Minuten)")
        minutes = tk.StringVar(value=str(self.app.cfg["recovery_cache"]["minutes"]))
        ttk.Combobox(body, textvariable=minutes, values=(5, 10, 15, 30, 60), state="readonly").pack(fill="x")
        self.label(body, "Abgelaufene Aufnahmen werden automatisch gelöscht.\nLogo an den rechten Bildschirmrand ziehen: im Tray ausblenden.", wraplength=460)
        result = self.label(body, "")
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x")
        def save():
            try:
                self.app.update_preferences({"prompt_profiles": {"active": profile.get(), "output_language": language.get()},
                                             "recovery_cache": {"minutes": int(minutes.get())}})
                self.app.prune_recovery()
                result.configure(text="Gespeichert · gilt ab der nächsten Aufnahme")
            except (OSError, ValueError):
                result.configure(text="Einstellungen konnten nicht gespeichert werden.")
        self.button(row, "Speichern", save)
        self.button(row, "Im Tray ausblenden", self.hide)

    def models_page(self, body):
        self.label(body, "Modelle", font=("Segoe UI", 18, "bold"))
        self.model_generation = object()
        generation = self.model_generation
        self.model_vars, self.model_boxes, self.catalogs = {}, {}, {}
        self.catalog_finished = set()
        for kind, title, section in (("transcription", "Transkription", "openrouter_stt"), ("text", "Cleanup & Prompt", "smoothing")):
            self.label(body, title)
            var = tk.StringVar(value=self.app.cfg[section]["model"])
            box = ttk.Combobox(body, textvariable=var)
            box.pack(fill="x")
            self.model_vars[kind], self.model_boxes[kind] = var, box
            box.bind("<KeyRelease>", lambda e, k=kind: self.filter_models(k))
        self.label(body, "Tippen zum Filtern, Pfeil zum Auswählen.\nTranskription zeigt ausschließlich kompatible Sprachmodelle.\nMAI-2 verwendet bei 429 weiterhin MAI-1.5 als Fallback.", wraplength=460)
        self.model_status = self.label(body, "Modellkatalog wird geladen …", wraplength=460)
        row = tk.Frame(body, bg=BG)
        row.pack(fill="x")
        self.button(row, "Speichern", self.save_models)
        self.button(row, "Neu laden", lambda: self.open_page("models"))
        def fetch(kind):
            try:
                data = discover_models(kind)
            except Exception:
                data = None
            self.events.put(("catalog", (generation, kind, data)))
        for kind in ("transcription", "text"):
            threading.Thread(target=fetch, args=(kind,), daemon=True, name="apollo-models").start()

    def filter_models(self, kind):
        query = self.model_vars[kind].get().lower()
        values = [name for name, label in self.catalogs.get(kind, {}).items() if query in name.lower() or query in label.lower()]
        self.model_boxes[kind]["values"] = values

    def catalog_result(self, generation, kind, data):
        if self.page != "models" or generation is not self.model_generation:
            return
        self.catalog_finished.add(kind)
        if data:
            self.catalogs[kind] = data
            self.model_boxes[kind]["values"] = list(data)
        self.model_status.configure(text=("Katalog geladen. Auswahl gilt ab der nächsten Aufnahme."
            if len(self.catalogs) == 2 else "Katalog wird geladen …" if len(self.catalog_finished) < 2 else
            "Katalog nicht erreichbar. Bestehende Modelle bleiben erhalten; bitte neu laden."))

    def save_models(self):
        changes = {}
        for kind, section in (("transcription", "openrouter_stt"), ("text", "smoothing")):
            value = self.model_vars[kind].get()
            if value != self.app.cfg[section]["model"] and value not in self.catalogs.get(kind, {}):
                self.model_status.configure(text="Bitte ein kompatibles Modell aus dem Katalog auswählen.")
                return
            changes[section] = {"model": value}
        try:
            self.app.update_preferences(changes)
            self.model_status.configure(text="Modelle gespeichert.")
        except (OSError, ValueError):
            self.model_status.configure(text="Modelle konnten nicht gespeichert werden.")

    def run(self):
        self.root.mainloop()
