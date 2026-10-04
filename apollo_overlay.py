"""DPI-aware Qt desktop overlay and in-app recovery/settings."""
from datetime import datetime
from collections import deque
import logging
import math
import queue
import time
from PySide6.QtCore import Qt, QRectF, QPointF, QPoint, QTimer, QEvent, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QFrame, QScrollArea,
                              QLineEdit, QListWidget, QListWidgetItem, QTextEdit, QPlainTextEdit, QToolTip, QSizePolicy, QMenu)
from apollo_i18n import t, set_language, LANGUAGES
from apollo_design import (qt_app, logo_path, logo_pixmap, Shell, ACCENT, TEXT,
                           label, button, vector, icon, paint_logo)
from apollo_widgets import Choice, KeyCapture, ModelCatalog, ModelField, MODES, register_window, WheelRouter


class DebugLog(logging.Handler):
    """Only Apollo's safe diagnostics, bounded in RAM; no response bodies/tracebacks."""
    def __init__(self):
        super().__init__(logging.INFO)
        self.pending = queue.Queue(maxsize=300)
    def emit(self, record):
        line = f'{datetime.fromtimestamp(record.created):%H:%M:%S} {record.levelname}  {record.getMessage()[:1000]}'
        if self.pending.full():
            try: self.pending.get_nowait()
            except queue.Empty: pass
        self.pending.put_nowait(line)

class Orbit(QWidget):
    CENTER = QPointF(116, 84)
    ACTIONS = {"recovery": QPointF(74, 42), "settings": QPointF(56, 84),
               "models": QPointF(74, 126), "close": QPointF(176, 84)}
    CAPTIONS = {"logo": "apollo s2t · Menü öffnen", "recovery": "Recovery und Live-Debug",
                "settings": "Einstellungen", "models": "Modelle", "close": "Menü schließen"}
    def __init__(self, ui):
        super().__init__()
        self.ui = ui
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("apollo s2t"); self.setAccessibleName(t("apollo s2t · schwebendes Menü"))
        self.resize(200, 168)
        self.drag = None; self.pressed = None; self.moved = False
        self.wave = [0.] * 27
        self.menu_progress = 0.; self.hover_progress = 0.; self.hovered = None
        self.menu_animation = QVariantAnimation(self); self.menu_animation.setDuration(130)
        self.menu_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.menu_animation.valueChanged.connect(self.menu_frame)
        self.hover_animation = QVariantAnimation(self); self.hover_animation.setDuration(100)
        self.hover_animation.valueChanged.connect(self.hover_frame)
        self.setMouseTracking(True)
    def menu_frame(self, value): self.menu_progress = float(value); self.update()
    def hover_frame(self, value): self.hover_progress = float(value); self.update()
    def animate_menu(self):
        self.menu_animation.stop(); self.menu_animation.setStartValue(self.menu_progress)
        self.menu_animation.setEndValue(1. if self.ui.expanded else 0.); self.menu_animation.start()
    def hover(self, action):
        if action == self.hovered: return
        self.hovered = action
        self.hover_animation.stop(); self.hover_animation.setStartValue(self.hover_progress if action is None else 0.)
        self.hover_animation.setEndValue(1. if action else 0.); self.hover_animation.start()
    def leaveEvent(self, event): self.hover(None); super().leaveEvent(event)
    def contextMenuEvent(self, event):
        if self.hit(QPointF(event.pos())) != "logo": return
        self.context_menu = QMenu(self)
        self.context_menu.addAction(t("Hide in tray"), self.ui.hide)
        self.context_menu.addAction(t("Quit"), self.ui.on_quit)
        handle = int(self.context_menu.winId()); self.ui.app.ui_windows.add(handle)
        self.context_menu.aboutToHide.connect(lambda: self.ui.app.ui_windows.discard(handle))
        self.context_menu.aboutToHide.connect(self.context_menu.deleteLater)
        self.context_menu.popup(event.globalPos()); event.accept()
    def hit(self, point):
        if math.hypot(point.x()-self.CENTER.x(), point.y()-self.CENTER.y()) <= 26: return "logo"
        if self.ui.expanded:
            for action, center in self.ACTIONS.items():
                if math.hypot(point.x()-center.x(), point.y()-center.y()) <= 16: return action
    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            action = self.hit(event.pos())
            if action:
                text = t(self.CAPTIONS[action])
                if action == "logo" and self.ui.app.recording:
                    mode = self.ui.app.active_mode; key = self.ui.app.cfg["hotkeys"].get(mode, "").upper()
                    seconds = int(self.ui.app.recorder.sample_count/self.ui.app.samplerate)
                    text = f'{key} · {t(MODES.get(mode, ""))} · {seconds//60}:{seconds%60:02d}'
                QToolTip.showText(event.globalPos(), text, self)
            else: QToolTip.hideText()
            return True
        return super().event(event)
    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton: return
        self.pressed = self.hit(event.position()); self.moved = False
        if self.pressed == "logo": self.drag = (event.globalPosition(), self.ui.x, self.ui.y)
    def mouseMoveEvent(self, event):
        self.hover(self.hit(event.position()))
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.hit(event.position()) else Qt.CursorShape.ArrowCursor)
        if self.drag:
            delta = event.globalPosition() - self.drag[0]
            if delta.manhattanLength() > 5:
                self.moved = True
                self.ui.x, self.ui.y = self.drag[1]+delta.x(), self.drag[2]+delta.y()
                self.move(round(self.ui.x-self.CENTER.x()), round(self.ui.y-self.CENTER.y()))
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
            self.animate_menu()
            if not self.ui.expanded: self.ui.close_dialog()
            self.ui.place()
        elif action == "close": self.ui.collapse()
        elif action: self.ui.open_page(action)
        self.update()
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.CENTER.x()-22, self.CENTER.y()-22, 44, 44)
        if self.ui.app.recording:
            bubble = QRectF(self.CENTER.x()-28, self.CENTER.y()-18, 56, 36)
            p.setPen(QPen(QColor("#777774"), 1)); p.setBrush(QColor("#232323")); p.drawRoundedRect(bubble, 16, 16)
            tail = QPainterPath(); tail.moveTo(self.CENTER.x()-4, bubble.bottom()-1)
            tail.lineTo(self.CENTER.x()-4, bubble.bottom()+5); tail.lineTo(self.CENTER.x()+3, bubble.bottom()-1)
            p.setPen(Qt.PenStyle.NoPen); p.drawPath(tail)
            p.setPen(QPen(QColor("#fafafa"), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for i in range(11):
                strength = max(self.wave[i*2:i*2+3], default=0)
                x = self.CENTER.x()+(i-5)*4; h = 3+strength*23
                p.drawLine(QPointF(x, self.CENTER.y()-h/2), QPointF(x, self.CENTER.y()+h/2))
        else:
            paint_logo(p, self.ui.pixmap, rect)
            if self.hovered == "logo" and self.hover_progress:
                p.setPen(QPen(QColor(35, 35, 35, round(110*self.hover_progress)), 1))
                p.setBrush(Qt.BrushStyle.NoBrush); p.drawEllipse(rect.adjusted(-1, -1, 1, 1))
            if self.ui.app._busy_recordings:
                p.setPen(QPen(QColor(ACCENT), 2)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawArc(rect.adjusted(-3, -3, 3, 3), -self.ui.tick_count*7*16, 110*16)
            elif self.ui.status != "Bereit":
                p.setPen(QPen(QColor("#eeedeb"), 1)); p.setBrush(QColor(ACCENT)); p.drawEllipse(rect.adjusted(33, 0, 0, -33))
        if self.ui.expanded or self.menu_progress > 0:
            p.setOpacity(self.menu_progress)
            for action, c in self.ACTIONS.items():
                shade = round(238-20*self.hover_progress) if self.hovered == action else 238
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(shade, shade, shade))
                p.drawEllipse(QRectF(c.x()-16, c.y()-16, 32, 32))
                vector(p, action, QRectF(c.x()-9, c.y()-9, 18, 18), "#292929")
        p.end()

class FloatingUI:
    def __init__(self, app, on_quit, logo=None):
        self.app, self.on_quit = app, on_quit
        set_language(app.cfg["ui_language"])
        self.root = qt_app(); self.pixmap = logo_pixmap(logo_path(app.base_dir))
        self.catalog = ModelCatalog(); self.events = app.ui_events
        self.expanded = False; self.visible = app.cfg["overlay"]["visible"]
        self.dialog = None; self.page = None; self.status = "Bereit"; self.tick_count = 0
        self.debug_log = DebugLog(); logging.getLogger("apollo").addHandler(self.debug_log)
        self.debug_lines = deque(maxlen=300); self.debug_revision = 0
        self.last_samples = 0; self.sample_at = time.monotonic()
        self.root.aboutToQuit.connect(self.detach_debug)
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
        rect = self.bounds(); left = 76 if self.expanded else 32; right = 80 if self.expanded else 32
        self.x = max(rect.left()+left, min(rect.right()-right, self.x))
        top = bottom = 60 if self.expanded else 32
        self.y = max(rect.top()+top, min(rect.bottom()-bottom, self.y))
        self.orb.move(round(self.x-self.orb.CENTER.x()), round(self.y-self.orb.CENTER.y()))
    def persist_position(self):
        try: self.app.update_preferences({"overlay": {"visible": self.visible, "x": self.x, "y": self.y}})
        except (OSError, ValueError): self.status = "Position konnte nicht gespeichert werden."
    def hide(self):
        self.visible = False; self.expanded = False; self.orb.menu_animation.stop(); self.orb.menu_progress = 0.
        self.orb.hide(); self.close_dialog(); self.persist_position()
    def show(self):
        self.visible = True; self.place(); self.orb.show(); self.persist_position()
    def collapse(self):
        self.expanded = False; self.orb.animate_menu(); self.close_dialog(); self.place(); self.orb.update()
    def tick(self):
        if self.app._closing.is_set():
            self.detach_debug(); self.timer.stop(); self.orb.close(); self.close_dialog(); self.root.quit(); return
        while True:
            try: line = self.debug_log.pending.get_nowait()
            except queue.Empty: break
            self.debug_lines.append(line); self.debug_revision += 1
        while True:
            try: kind, value = self.events.get_nowait()
            except queue.Empty: break
            if kind == "status":
                self.status = value
                self.orb.update()
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
            self.was_recording = self.app.recording; self.place(); self.orb.update()
        if self.app.recording or self.app._busy_recordings: self.orb.update()
        self.tick_count += 1
        samples = self.app.recorder.sample_count
        if samples != self.last_samples or not self.app.recording:
            self.last_samples = samples; self.sample_at = time.monotonic()
        if self.page == "recovery" and self.tick_count % 6 == 0: self.refresh_debug()
        if time.monotonic() >= self.prune_at:
            self.prune_at = time.monotonic()+15; self.app.prune_recovery()
            if self.page == "recovery": self.refresh_recovery()
    def detach_debug(self):
        logging.getLogger("apollo").removeHandler(self.debug_log)
    def close_dialog(self):
        dialog, self.dialog = self.dialog, None
        self.page = None
        if dialog: dialog.reject(); dialog.deleteLater()
    def open_page(self, page):
        if page not in ("recovery", "settings", "models"): return
        modal = self.root.activeModalWidget()
        if modal: modal.reject()
        if not self.dialog:
            self.dialog = Shell("Einstellungen", self.pixmap, 680, 560)
            row = QHBoxLayout(); row.setSpacing(16)
            sidebar = QWidget(); sidebar.setFixedWidth(120)
            nav = QVBoxLayout(sidebar); nav.setContentsMargins(0, 0, 0, 0); nav.setSpacing(8)
            self.nav_buttons = {}
            for name, text in (("recovery", "Recovery"), ("settings", "Einstellungen"), ("models", "Modelle")):
                b = button(text, lambda checked=False, p=name: self.open_page(p))
                b.setObjectName("nav"); b.setCheckable(True); b.setIcon(icon(name, "#fafafa"))
                nav.addWidget(b); self.nav_buttons[name] = b
            from apollo import APP_NAME, APP_VERSION
            nav.addStretch(); nav.addWidget(label(f"{APP_NAME}\nVersion {APP_VERSION}", "muted")); row.addWidget(sidebar)
            self.content = QWidget(); self.content_layout = QVBoxLayout(self.content)
            self.content_layout.setContentsMargins(0, 0, 0, 0); self.content_layout.setSpacing(8)
            row.addWidget(self.content, 1); self.dialog.layout.addLayout(row, 1)
            self.dialog.finished.connect(self.dialog_closed); register_window(self.dialog, self.app)
            rect = self.bounds()
            self.dialog.resize(min(680, rect.width()-32), min(560, rect.height()-32))
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
        self.form_scroll = scroll
        scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus); scroll.wheel_router = WheelRouter(scroll)
        body = QWidget(); layout = QVBoxLayout(body); layout.setContentsMargins(0, 0, 8, 0); layout.setSpacing(8)
        scroll.setWidget(body); self.content_layout.addWidget(scroll, 1)
        return layout
    def card(self, layout, title):
        card = QFrame(); card.setObjectName("card"); inside = QVBoxLayout(card)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        inside.setContentsMargins(12, 8, 12, 8); inside.setSpacing(6)
        inside.addWidget(label(title, "section")); layout.addWidget(card); return inside
    def footer(self, text, save):
        self.result = label(text, "muted", True); self.content_layout.addWidget(self.result)
        self.content_layout.addWidget(button("Speichern", save, primary=True))
    def settings_page(self):
        cfg = self.app.cfg; layout = self.form(); self.key_fields = {}
        interface = self.card(layout, "Sprache der Oberfläche")
        self.interface_language = Choice(LANGUAGES, cfg["ui_language"], self.pixmap, self.app); interface.addWidget(self.interface_language)
        keys = self.card(layout, "Aufnahmetasten")
        for mode, text in MODES.items():
            row = QHBoxLayout(); row.addWidget(label(text)); row.addStretch()
            field = KeyCapture(cfg["hotkeys"][mode]); row.addWidget(field); keys.addLayout(row); self.key_fields[mode] = field
        for field in self.key_fields.values(): field.setToolTip(t("Taste anklicken und die neue Taste drücken"))
        self.key_mode = Choice({"toggle": "Antippen zum Starten / Stoppen", "hold": "Gedrückt halten"}, cfg["hotkey_mode"], self.pixmap, self.app); keys.addWidget(self.key_mode)
        profiles = self.card(layout, "Prompt-Profil")
        self.profile = Choice({p:p for p in self.app.available_profiles()}, cfg["prompt_profiles"]["active"], self.pixmap, self.app, translate=False); profiles.addWidget(self.profile)
        self.language = Choice({"english": "Ausgabe auf Englisch", "german": "Ausgabe auf Deutsch", "match": "Sprache der Aufnahme"}, cfg["prompt_profiles"]["output_language"], self.pixmap, self.app); profiles.addWidget(self.language)
        cache = self.card(layout, "Recovery-Cache")
        self.minutes = QLineEdit(str(cfg["recovery_cache"]["minutes"])); self.minutes.setAccessibleName(t("Recovery-Dauer in Minuten"))
        cache.addWidget(self.minutes); cache.addWidget(label("5–60 min", "muted"))
        self.minutes.setToolTip(t("5–60 Minuten · Abgelaufene Aufnahmen werden automatisch gelöscht."))
        words = self.card(layout, "Persönliches Wörterbuch")
        self.vocabulary = QPlainTextEdit("\n".join(cfg["openrouter_stt"]["vocabulary"]))
        self.vocabulary.setAccessibleName(t("Persönliches Wörterbuch")); self.vocabulary.setFixedHeight(90)
        self.vocabulary.setPlaceholderText("PANDU\nOpenRouter")
        words.addWidget(self.vocabulary)
        self.vocabulary.setToolTip(t("Ein Begriff pro Zeile · maximal 100. MAI-Transcribe 2 erhält diese Begriffe mit dem Audio als Erkennungshilfe. Andere Modelle verwenden sie derzeit nicht."))
        words.addWidget(label("Ein Begriff pro Zeile · mit Audio an MAI 2 gesendet", "muted", True))
        layout.addStretch(); self.footer("", self.save_settings)
    def save_settings(self):
        previous_language = self.app.cfg["ui_language"]
        scroll_position = self.form_scroll.verticalScrollBar().value()
        try:
            try: minutes = int(self.minutes.text())
            except ValueError: raise ValueError("Bitte eine Recovery-Dauer von 5 bis 60 Minuten eingeben.")
            if not 5 <= minutes <= 60: raise ValueError("Bitte eine Recovery-Dauer von 5 bis 60 Minuten eingeben.")
            self.app.update_preferences({"hotkeys": {k:v.value for k,v in self.key_fields.items()}, "hotkey_mode": self.key_mode.value,
                "ui_language": self.interface_language.value,
                "openrouter_stt": {"vocabulary": [line.strip() for line in self.vocabulary.toPlainText().splitlines() if line.strip()]},
                "prompt_profiles": {"active": self.profile.value, "output_language": self.language.value}, "recovery_cache": {"minutes": minutes}})
            self.app.prune_recovery(); self.result.setObjectName("muted")
            set_language(self.app.cfg["ui_language"])
            for name, b in self.nav_buttons.items(): b.setText(t({"recovery": "Recovery", "settings": "Einstellungen", "models": "Modelle"}[name]))
            if previous_language != self.app.cfg["ui_language"]:
                self.open_page("settings")
            self.result.setText(t("Gespeichert"))
            scroll = self.form_scroll
            QTimer.singleShot(0, lambda: scroll.verticalScrollBar().setValue(scroll_position))
        except (OSError, ValueError) as exc:
            self.result.setText(t(str(exc) if isinstance(exc, ValueError) else "Speichern fehlgeschlagen. Bitte erneut versuchen.")); self.result.setObjectName("error")
        self.result.style().unpolish(self.result); self.result.style().polish(self.result)
    def models_page(self):
        cfg = self.app.cfg; layout = self.form(); self.model_fields = {}
        for key, title, kind, value, allow_none in (
            ("primary", "Haupttranskription", "transcription", cfg["openrouter_stt"]["model"], False),
            ("fallback", "Fallback bei Rate Limit (429)", "transcription", cfg["openrouter_stt"].get("fallback_model"), True),
            ("text", "Bereinigen & Prompt", "text", cfg["smoothing"]["model"], False)):
            card = self.card(layout, title); field = ModelField(title, kind, value, self.catalog, self.pixmap, self.app, allow_none)
            card.addWidget(field); self.model_fields[key] = field
        layout.addWidget(button("Katalog erneut laden", lambda: self.catalog.start(reload=True), quiet=True))
        layout.addStretch(); self.footer("", self.save_models); self.catalog.start()
    def save_models(self):
        stt = self.app.cfg["openrouter_stt"]; text = self.app.cfg["smoothing"]; values = {k:f.value for k,f in self.model_fields.items()}
        for key, kind, original in (("primary", "transcription", stt["model"]), ("fallback", "transcription", stt.get("fallback_model")), ("text", "text", text["model"])):
            value = values[key]
            if value is None and key == "fallback": continue
            if value != original and value not in self.catalog.data[kind]:
                self.result.setText(t("Bitte ein kompatibles Modell aus dem Katalog auswählen.")); return
        try:
            self.app.update_preferences({"openrouter_stt": {"model": values["primary"], "fallback_model": values["fallback"]}, "smoothing": {"model": values["text"]}})
            self.result.setText(t("Gespeichert"))
        except (OSError, ValueError) as exc: self.result.setText(t(str(exc) if isinstance(exc, ValueError) else "Speichern fehlgeschlagen. Bitte erneut versuchen."))
    def recovery_page(self):
        cfg = self.app.cfg["recovery_cache"]
        self.recovery_list = QListWidget(); self.recovery_list.setFixedHeight(86); self.recovery_list.currentRowChanged.connect(self.preview)
        self.content_layout.addWidget(self.recovery_list)
        self.preview_text = QTextEdit(); self.preview_text.setReadOnly(True); self.preview_text.setMinimumHeight(60)
        self.preview_text.setPlaceholderText(t("Noch keine Aufnahme im Cache.")); self.content_layout.addWidget(self.preview_text, 1)
        controls = QWidget(); row = QHBoxLayout(controls); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(8)
        for text, action, primary in (("Wiederherstellen", self.recover_selected, True), ("Kopieren", self.copy_selected, False), ("Löschen", self.delete_selected, False)): row.addWidget(button(text, action, primary))
        self.content_layout.addWidget(controls); self.recovery_status = label(self.status, "muted", True); self.content_layout.addWidget(self.recovery_status)
        self.recovery_status.hide()  # live status is already shown next to the debug log
        self.content_layout.addWidget(label("Live-Debug", "section"))
        self.debug_state = label("", "muted", True); self.content_layout.addWidget(self.debug_state)
        self.debug_text = QPlainTextEdit(); self.debug_text.setReadOnly(True); self.debug_text.setMaximumBlockCount(300)
        self.debug_text.setPlaceholderText(t("Aufnahmeverlauf"))
        self.debug_text.setFixedHeight(108); self.debug_text.setAccessibleName(t("Live-Debug-Verlauf"))
        self.debug_text.setStyleSheet("QPlainTextEdit { background: #fafafa; border: 1px solid #cfcecb; border-radius: 8px; padding: 6px; font-size: 12px; }")
        self.content_layout.addWidget(self.debug_text); self.rendered_debug = -1
        self.entries = []; self.refresh_recovery()
        self.refresh_debug()
    def refresh_debug(self):
        app = self.app
        if app.recording:
            mode = app.active_mode; key = app.cfg["hotkeys"].get(mode, "").upper()
            seconds = int(app.recorder.sample_count/app.samplerate)
            mic = "Audiodaten kommen an"
            if not app.recorder.sample_count: mic = "Noch keine Audiodaten"
            if time.monotonic()-self.sample_at > 1: mic = "Keine neuen Audiodaten seit über 1 s"
            if app.recorder.capture_warning: mic = "Mikrofon meldet eine Unterbrechung"
            cache = "Audio-Cache aktiv" if not app.recorder.backup_failed else "Audio-Backup fehlgeschlagen"
            state = f'{t("Aufnahme")} · {key} · {t(MODES.get(mode, mode))} · {seconds//60}:{seconds%60:02d}\n{t(mic)} · {t(cache)}'
        elif app._busy_recordings:
            state = f'{t("Verarbeitung")} · {len(app._busy_recordings)} {t("Aufnahme(n)")}'
            if self.status != "Bereit": state += " · " + t(self.status)
        else:
            state = t("Bereit") + " · " + " / ".join(v.upper() for k,v in app.cfg["hotkeys"].items() if k in MODES)
            if self.status != "Bereit": state += "\n" + t(self.status)
        self.debug_state.setText(t(state))
        if self.rendered_debug != self.debug_revision:
            bar = self.debug_text.verticalScrollBar(); tail = bar.value() >= bar.maximum()-2; position = bar.value()
            self.debug_text.setPlainText("\n".join(self.debug_lines)); self.rendered_debug = self.debug_revision
            bar.setValue(bar.maximum() if tail else position)
    def selected(self):
        index = self.recovery_list.currentRow(); return self.entries[index] if 0 <= index < len(self.entries) else None
    def refresh_recovery(self):
        old = self.selected().id if self.selected() else None; self.recovery_list.blockSignals(True)
        self.entries = self.app.recovery_items(); self.recovery_list.clear(); selected = 0
        for i, entry in enumerate(self.entries):
            try:
                meta = entry.metadata; stamp = datetime.fromisoformat(meta["created_at"]).astimezone().strftime("%H:%M:%S")
                key = meta.get("hotkey") or "Taste unbekannt"
                state = {"recording": "Aufnahme läuft", "pending": "Wartet auf Verarbeitung", "processing": "Wird transkribiert",
                         "failed": "Fehlgeschlagen · Audio gesichert", "interrupted": "Unterbrochen · Audio prüfen", "too_short": "Aufnahme zu kurz", "ready": "Text verfügbar"}.get(meta["state"], meta["state"])
                text = f'{stamp}    {t(key).upper()} · {t(MODES.get(meta["mode"], meta["mode"]))}\n{t(state)}'
            except (OSError, ValueError): text = t("Aufnahme nicht mehr verfügbar")
            self.recovery_list.addItem(QListWidgetItem(text))
            if entry.id == old: selected = i
        if self.entries: self.recovery_list.setCurrentRow(selected)
        self.recovery_list.blockSignals(False); self.recovery_status.setText(t(self.status)); self.preview()
    def preview(self, *_):
        entry = self.selected()
        try:
            text = entry.read_transcript() if entry else t("Noch keine Aufnahme im Cache.")
            cause = entry.metadata.get("error") if entry else None
            if isinstance(cause, str) and cause: text = t("Ursache: ") + t(cause) + "\n\n" + (text or t("Audio gesichert. Wiederherstellen versucht die Transkription erneut."))
        except (OSError, ValueError): text = t("Aufnahme nicht mehr verfügbar.")
        self.preview_text.setPlainText(text or t("Audio gesichert. Wiederherstellen versucht die Transkription erneut."))
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
