"""DPI-aware Qt desktop overlay and in-app recovery/settings."""
from datetime import datetime
import math
import queue
import time
from PySide6.QtCore import Qt, QRectF, QPointF, QPoint, QTimer, QSize
from PySide6.QtGui import QColor, QPainter, QPen, QFont
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QFrame, QScrollArea,
                              QLineEdit, QListWidget, QListWidgetItem, QTextEdit)
from apollo_design import (qt_app, logo_path, logo_pixmap, Shell, GOLD, TEXT,
                           label, button, vector, icon, paint_logo)
from apollo_widgets import Choice, KeyCapture, ModelCatalog, ModelField, MODES, register_window

class Orbit(QWidget):
    CENTER = QPointF(252, 185)
    ACTIONS = {"recovery": QPointF(136, 69), "settings": QPointF(88, 185),
               "models": QPointF(136, 301), "close": QPointF(416, 185)}
    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Apollo")
        self.resize(454, 386)
        self.drag = None; self.pressed = None; self.moved = False
        self.wave = [0.] * 27
        self.setMouseTracking(True)
    def hit(self, point):
        if math.hypot(point.x()-252, point.y()-185) <= 44: return "logo"
        if self.ui.expanded:
            for action, center in self.ACTIONS.items():
                if math.hypot(point.x()-center.x(), point.y()-center.y()) <= 32: return action
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton: return
        self.pressed = self.hit(event.position()); self.moved = False
        if self.pressed == "logo": self.drag = (event.globalPosition(), self.ui.x, self.ui.y)
    def mouseMoveEvent(self, event):
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.hit(event.position()) else Qt.CursorShape.ArrowCursor)
        if self.drag:
            delta = event.globalPosition() - self.drag[0]
            if delta.manhattanLength() > 5:
                self.moved = True
                self.ui.x, self.ui.y = self.drag[1]+delta.x(), self.drag[2]+delta.y()
                self.move(round(self.ui.x-252), round(self.ui.y-185))
    def mouseReleaseEvent(self, event):
        self.drag = None
        if self.moved:
            if self.ui.x >= self.ui.bounds().right()-24: self.ui.hide()
            else: self.ui.place(); self.ui.persist_position()
            return
        action = self.hit(event.position())
        if action != self.pressed: return
        if action == "logo":
            self.ui.expanded = not self.ui.expanded
            if not self.ui.expanded: self.ui.close_dialog()
            self.ui.place()
        elif action == "close": self.ui.collapse()
        elif action: self.ui.open_page(action)
        self.update()
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(208, 141, 88, 88)
        paint_logo(p, self.ui.pixmap, rect)
        if self.ui.app.recording:
            strength = max(self.wave, default=0)
            p.setPen(QPen(QColor(GOLD), 2+strength*2)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(rect.adjusted(-5-strength*3, -5-strength*3, 5+strength*3, 5+strength*3))
        elif self.ui.app._busy_recordings:
            p.setPen(QPen(QColor(GOLD), 3)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(rect.adjusted(-7, -7, 7, 7), -self.ui.tick_count*7*16, 110*16)
        elif self.ui.status != "Bereit":
            p.setPen(QPen(QColor("#202020"), 2)); p.setBrush(QColor(GOLD)); p.drawEllipse(QRectF(280, 143, 14, 14))
        if self.ui.expanded:
            captions = {"recovery": "Recovery", "settings": "Einstellungen", "models": "Modelle", "close": ""}
            p.setFont(QFont("Segoe UI", 11))
            for action, c in self.ACTIONS.items():
                p.setPen(QPen(QColor("#4d4840"), 1)); p.setBrush(QColor("#242422"))
                p.drawEllipse(QRectF(c.x()-32, c.y()-32, 64, 64))
                vector(p, action, QRectF(c.x()-14, c.y()-14, 28, 28), TEXT)
                if captions[action]:
                    width = p.fontMetrics().horizontalAdvance(captions[action])+20
                    plate = QRectF(c.x()-width/2, c.y()+40, width, 28)
                    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor("#242422")); p.drawRoundedRect(plate, 8, 8)
                    p.setPen(QColor(TEXT)); p.drawText(plate, Qt.AlignmentFlag.AlignCenter, captions[action])
        if self.ui.app.recording:
            plate = QRectF(112, 391 if self.ui.expanded else 241, 280, 92)
            p.setPen(QPen(QColor("#665235"), 1)); p.setBrush(QColor("#242422")); p.drawRoundedRect(plate, 22, 22)
            p.setPen(QPen(QColor(GOLD), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for i, strength in enumerate(self.wave):
                x = plate.center().x()+(i-13)*8; h = 3+strength*37
                p.drawLine(QPointF(x, plate.top()+30-h/2), QPointF(x, plate.top()+30+h/2))
            mode = self.ui.app.active_mode; key = self.ui.app.cfg["hotkeys"].get(mode, "").upper()
            seconds = int(self.ui.app.recorder.sample_count/self.ui.app.samplerate)
            p.setFont(QFont("Segoe UI", 11)); p.setPen(QColor(TEXT))
            p.drawText(QRectF(plate.x(), plate.y()+57, plate.width(), 24), Qt.AlignmentFlag.AlignCenter,
                       f"{key} · {MODES.get(mode, '')}  ·  {seconds//60}:{seconds%60:02d}")
        p.end()

class FloatingUI:
    def __init__(self, app, on_quit, logo=None):
        self.app, self.on_quit = app, on_quit
        self.root = qt_app(); self.pixmap = logo_pixmap(logo_path(app.base_dir))
        self.catalog = ModelCatalog(); self.events = app.ui_events
        self.expanded = False; self.visible = app.cfg["overlay"]["visible"]
        self.dialog = None; self.page = None; self.status = "Bereit"; self.tick_count = 0
        self.orb = Orbit(self)
        screen = self.root.primaryScreen().availableGeometry()
        self.x = app.cfg["overlay"]["x"]; self.y = app.cfg["overlay"]["y"]
        if self.x is None: self.x = screen.right()-100
        if self.y is None: self.y = screen.center().y()
        self.place()
        if self.visible: self.orb.show()
        self.timer = QTimer(); self.timer.timeout.connect(self.tick); self.timer.start(33)
        self.prune_at = time.monotonic()+15
    def bounds(self):
        screen = self.root.screenAt(QPoint(round(self.x), round(self.y))) or self.root.primaryScreen()
        return screen.availableGeometry()
    def place(self):
        rect = self.bounds(); left = 240 if self.expanded else 48; right = 204 if self.expanded else 48
        self.x = max(rect.left()+left, min(rect.right()-right, self.x))
        top = 160 if self.expanded else 52
        bottom = 308 if self.expanded and self.app.recording else 200 if self.expanded else 155 if self.app.recording else 52
        self.y = max(rect.top()+top, min(rect.bottom()-bottom, self.y))
        self.orb.resize(454, 494 if self.expanded and self.app.recording else 386)
        self.orb.move(round(self.x-252), round(self.y-185))
    def persist_position(self):
        try: self.app.update_preferences({"overlay": {"visible": self.visible, "x": self.x, "y": self.y}})
        except (OSError, ValueError): self.status = "Position konnte nicht gespeichert werden."
    def hide(self):
        self.visible = False; self.expanded = False; self.orb.hide(); self.close_dialog(); self.persist_position()
    def show(self):
        self.visible = True; self.place(); self.orb.show(); self.persist_position()
    def collapse(self):
        self.expanded = False; self.close_dialog(); self.place(); self.orb.update()
    def tick(self):
        if self.app._closing.is_set():
            self.timer.stop(); self.orb.close(); self.close_dialog(); self.root.quit(); return
        while True:
            try: kind, value = self.events.get_nowait()
            except queue.Empty: break
            if kind == "status":
                self.status = value
                if self.page == "recovery": self.refresh_recovery()
            elif kind == "open":
                self.show()
                if value: self.open_page(value)
            elif kind == "refresh" and self.page == "recovery": self.refresh_recovery()
        target = list(getattr(self.app.recorder, "visual_levels", ())) if self.app.recording else []
        target = ([0.]*27+target)[-27:]
        for i, level in enumerate(target):
            old = self.orb.wave[i]; self.orb.wave[i] = old+(level-old)*(.7 if level > old else .28)
        if getattr(self, "was_recording", False) != self.app.recording:
            self.was_recording = self.app.recording; self.place()
        self.orb.update(); self.tick_count += 1
        if time.monotonic() >= self.prune_at:
            self.prune_at = time.monotonic()+15; self.app.prune_recovery()
            if self.page == "recovery": self.refresh_recovery()
    def close_dialog(self):
        dialog, self.dialog = self.dialog, None
        self.page = None
        if dialog: dialog.reject(); dialog.deleteLater()
    def open_page(self, page):
        if page not in ("recovery", "settings", "models"): return
        modal = self.root.activeModalWidget()
        if modal: modal.reject()
        if not self.dialog:
            self.dialog = Shell("Einstellungen", self.pixmap, 920, 740)
            row = QHBoxLayout(); row.setSpacing(24)
            sidebar = QWidget(); sidebar.setFixedWidth(176)
            nav = QVBoxLayout(sidebar); nav.setContentsMargins(0, 0, 0, 0); nav.setSpacing(8)
            self.nav_buttons = {}
            for name, text in (("recovery", "Recovery"), ("settings", "Einstellungen"), ("models", "Modelle")):
                b = button(text, lambda checked=False, p=name: self.open_page(p))
                b.setObjectName("nav"); b.setCheckable(True); b.setIcon(icon(name))
                nav.addWidget(b); self.nav_buttons[name] = b
            nav.addStretch(); nav.addWidget(label("Apollo s2t\nVersion 0.4.0", "muted")); row.addWidget(sidebar)
            self.content = QWidget(); self.content_layout = QVBoxLayout(self.content)
            self.content_layout.setContentsMargins(0, 0, 0, 0); self.content_layout.setSpacing(16)
            row.addWidget(self.content, 1); self.dialog.layout.addLayout(row, 1)
            self.dialog.finished.connect(self.dialog_closed); register_window(self.dialog, self.app)
            rect = self.bounds()
            self.dialog.resize(min(920, rect.width()-32), min(740, rect.height()-32))
            self.dialog.move(rect.center()-self.dialog.rect().center())
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget(): item.widget().hide(); item.widget().deleteLater()
        self.page = page
        for name, b in self.nav_buttons.items(): b.setChecked(name == page)
        self.content_layout.addWidget(label({"recovery": "Letzte Aufnahmen", "settings": "Einstellungen", "models": "Modelle"}[page], "title"))
        getattr(self, page+"_page")()
        self.dialog.show(); self.dialog.raise_(); self.dialog.activateWindow()
    def dialog_closed(self, _):
        dialog, self.dialog = self.dialog, None
        self.page = None
        if dialog: dialog.deleteLater()
    def form(self):
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        body = QWidget(); layout = QVBoxLayout(body); layout.setContentsMargins(0, 0, 8, 0); layout.setSpacing(16)
        scroll.setWidget(body); self.content_layout.addWidget(scroll, 1)
        return layout
    def card(self, layout, title):
        card = QFrame(); card.setObjectName("card"); inside = QVBoxLayout(card)
        inside.setContentsMargins(16, 16, 16, 16); inside.setSpacing(12)
        inside.addWidget(label(title, "section")); layout.addWidget(card); return inside
    def footer(self, text, save):
        self.result = label(text, "muted", True); self.content_layout.addWidget(self.result)
        self.content_layout.addWidget(button("Speichern", save, primary=True))
    def settings_page(self):
        cfg = self.app.cfg; layout = self.form(); keys = self.card(layout, "Aufnahmetasten"); self.key_fields = {}
        for mode, text in MODES.items():
            row = QHBoxLayout(); row.addWidget(label(text)); row.addStretch()
            field = KeyCapture(cfg["hotkeys"][mode]); row.addWidget(field); keys.addLayout(row); self.key_fields[mode] = field
        keys.addWidget(label("Taste anklicken und die neue Taste drücken", "muted"))
        self.key_mode = Choice({"toggle": "Antippen zum Starten / Stoppen", "hold": "Gedrückt halten"}, cfg["hotkey_mode"], self.pixmap, self.app); keys.addWidget(self.key_mode)
        profiles = self.card(layout, "Prompt-Profil")
        self.profile = Choice({p:p for p in self.app.available_profiles()}, cfg["prompt_profiles"]["active"], self.pixmap, self.app); profiles.addWidget(self.profile)
        self.language = Choice({"english": "Ausgabe auf Englisch", "german": "Ausgabe auf Deutsch", "match": "Sprache der Aufnahme"}, cfg["prompt_profiles"]["output_language"], self.pixmap, self.app); profiles.addWidget(self.language)
        cache = self.card(layout, "Recovery-Cache")
        self.minutes = QLineEdit(str(cfg["recovery_cache"]["minutes"])); self.minutes.setAccessibleName("Recovery-Dauer in Minuten")
        cache.addWidget(self.minutes); cache.addWidget(label("5–60 Minuten · Abgelaufene Aufnahmen werden automatisch gelöscht.", "muted", True))
        layout.addStretch(); self.footer("Änderungen gelten ab der nächsten Aufnahme.", self.save_settings)
    def save_settings(self):
        try:
            try: minutes = int(self.minutes.text())
            except ValueError: raise ValueError("Bitte eine Recovery-Dauer von 5 bis 60 Minuten eingeben.")
            if not 5 <= minutes <= 60: raise ValueError("Bitte eine Recovery-Dauer von 5 bis 60 Minuten eingeben.")
            self.app.update_preferences({"hotkeys": {k:v.value for k,v in self.key_fields.items()}, "hotkey_mode": self.key_mode.value,
                "prompt_profiles": {"active": self.profile.value, "output_language": self.language.value}, "recovery_cache": {"minutes": minutes}})
            self.app.prune_recovery(); self.result.setText("Gespeichert · Änderungen gelten ab der nächsten Aufnahme."); self.result.setObjectName("muted")
        except (OSError, ValueError) as exc:
            self.result.setText(str(exc) if isinstance(exc, ValueError) else "Speichern fehlgeschlagen. Bitte erneut versuchen."); self.result.setObjectName("error")
        self.result.style().unpolish(self.result); self.result.style().polish(self.result)
    def models_page(self):
        cfg = self.app.cfg; layout = self.form(); self.model_fields = {}
        for key, title, kind, value, allow_none in (
            ("primary", "Haupttranskription", "transcription", cfg["openrouter_stt"]["model"], False),
            ("fallback", "Fallback bei Rate Limit (429)", "transcription", cfg["openrouter_stt"].get("fallback_model"), True),
            ("text", "Bereinigen & Prompt", "text", cfg["smoothing"]["model"], False)):
            card = self.card(layout, title); field = ModelField(title, kind, value, self.catalog, self.pixmap, self.app, allow_none)
            card.addWidget(field); self.model_fields[key] = field
        layout.addWidget(label("Transkription zeigt nur kompatible Sprachmodelle.\nPreise in USD von OpenRouter; die Einheit steht direkt beim Modell.", "muted", True))
        layout.addWidget(button("Katalog erneut laden", lambda: self.catalog.start(reload=True), quiet=True))
        layout.addStretch(); self.footer("Die Audioaufnahme bleibt auch bei einem API-Fehler im Recovery-Cache.", self.save_models); self.catalog.start()
    def save_models(self):
        stt = self.app.cfg["openrouter_stt"]; text = self.app.cfg["smoothing"]; values = {k:f.value for k,f in self.model_fields.items()}
        for key, kind, original in (("primary", "transcription", stt["model"]), ("fallback", "transcription", stt.get("fallback_model")), ("text", "text", text["model"])):
            value = values[key]
            if value is None and key == "fallback": continue
            if value != original and value not in self.catalog.data[kind]:
                self.result.setText("Bitte ein kompatibles Modell aus dem Katalog auswählen."); return
        try:
            self.app.update_preferences({"openrouter_stt": {"model": values["primary"], "fallback_model": values["fallback"]}, "smoothing": {"model": values["text"]}})
            self.result.setText("Modelle gespeichert · gültig ab der nächsten Aufnahme.")
        except (OSError, ValueError) as exc: self.result.setText(str(exc) if isinstance(exc, ValueError) else "Speichern fehlgeschlagen. Bitte erneut versuchen.")
    def recovery_page(self):
        cfg = self.app.cfg["recovery_cache"]
        self.content_layout.addWidget(label(f'{cfg["minutes"]} Minuten · max. {cfg["max_entries"]} Aufnahmen · {cfg["max_mb"]} MB', "muted"))
        self.recovery_list = QListWidget(); self.recovery_list.setMaximumHeight(230); self.recovery_list.currentRowChanged.connect(self.preview)
        self.content_layout.addWidget(self.recovery_list)
        self.preview_text = QTextEdit(); self.preview_text.setReadOnly(True); self.preview_text.setPlaceholderText("Noch keine Aufnahme im Cache."); self.content_layout.addWidget(self.preview_text, 1)
        controls = QWidget(); row = QHBoxLayout(controls); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(8)
        for text, action, primary in (("Wiederherstellen", self.recover_selected, True), ("Kopieren", self.copy_selected, False), ("Löschen", self.delete_selected, False)): row.addWidget(button(text, action, primary))
        self.content_layout.addWidget(controls); self.recovery_status = label(self.status, "muted", True); self.content_layout.addWidget(self.recovery_status)
        self.entries = []; self.refresh_recovery()
    def selected(self):
        index = self.recovery_list.currentRow(); return self.entries[index] if 0 <= index < len(self.entries) else None
    def refresh_recovery(self):
        old = self.selected().id if self.selected() else None; self.recovery_list.blockSignals(True)
        self.entries = self.app.recovery_items(); self.recovery_list.clear(); selected = 0
        for i, entry in enumerate(self.entries):
            try:
                meta = entry.metadata; stamp = datetime.fromisoformat(meta["created_at"]).astimezone().strftime("%H:%M:%S")
                key = meta.get("hotkey") or "Taste unbekannt"
                text = f'{stamp}    {key.upper()} · {MODES.get(meta["mode"], meta["mode"])}\n'+("Text verfügbar" if entry.read_transcript() else "Audio gesichert · erneut versuchen")
            except (OSError, ValueError): text = "Aufnahme nicht mehr verfügbar"
            self.recovery_list.addItem(QListWidgetItem(text))
            if entry.id == old: selected = i
        if self.entries: self.recovery_list.setCurrentRow(selected)
        self.recovery_list.blockSignals(False); self.recovery_status.setText(self.status); self.preview()
    def preview(self, *_):
        entry = self.selected()
        try: text = entry.read_transcript() if entry else "Noch keine Aufnahme im Cache."
        except (OSError, ValueError): text = "Aufnahme nicht mehr verfügbar."
        self.preview_text.setPlainText(text or "Audio gesichert. Wiederherstellen versucht die Transkription erneut.")
    def recover_selected(self):
        entry = self.selected()
        if entry: self.app.recover(entry.id); self.status = "Wiederherstellung läuft …"; self.refresh_recovery()
    def copy_selected(self):
        entry = self.selected()
        if entry: self.app.copy_recovery(entry.id)
    def delete_selected(self):
        entry = self.selected()
        if entry: self.app.delete_recovery(entry.id); self.refresh_recovery()
    def run(self): self.root.exec()
