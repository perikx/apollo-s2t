"""DPI-aware Qt desktop overlay and in-app recovery/settings."""
from datetime import datetime
from collections import deque
import logging
import math
import queue
import threading
import time
from PySide6.QtCore import Qt, QRectF, QPointF, QPoint, QTimer, QEvent, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QScrollArea, QStackedWidget,
                              QLineEdit, QListWidget, QListWidgetItem, QTextEdit, QCheckBox, QPlainTextEdit, QToolTip, QMenu, QSystemTrayIcon)
from apollo_i18n import t, set_language, LANGUAGES
from apollo_design import (qt_app, apply_material, glyph_button, GlassMenu, Shell, label, button, vector)
from apollo_glass import GLASS, Backdrop, dark_behind, paint_glass
from apollo_widgets import Choice, KeyCapture, ModelCatalog, ModelField, MODES, register_window
from apollo_api import check_key, KEY_UNUSABLE
from apollo_config import api_key



PROFILE_TEMPLATE = """<!--
Notes for you; F10 does not send them. Describe the project below.
-->

## Project

## Stack

## Conventions
"""

# Icon colors on the glass. Each icon takes the light ink over a dark backdrop and the dark
# ink over a light one, in every material.
INK = {"dark": "#232323", "light": "#fafafa"}

SUN = "#ff8a00"
# The pages of the panel window, in the order of the sidebar. The settings have three sections.
SECTIONS = {"settings": "Allgemein", "recording": "Aufnahme", "prompt": "Prompt"}
PAGES = {"recovery": "Recovery", **SECTIONS, "models": "Modelle"}
TITLES = {**PAGES, "recovery": "Letzte Aufnahmen"}


def paint_sun(painter, center, size, color=SUN):
    """Draw the Apollo sun mark."""
    painter.save(); painter.translate(center.x()-size/2, center.y()-size/2); painter.scale(size/24.0, size/24.0)
    painter.setPen(QPen(QColor(color), 2.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for i in range(12):
        angle, length = i*math.pi/6-math.pi/2, 2.4 if i % 2 else 4.2
        painter.drawLine(QPointF(12+math.cos(angle)*7, 12+math.sin(angle)*7),
                         QPointF(12+math.cos(angle)*(7+length), 12+math.sin(angle)*(7+length)))
    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(color))
    painter.drawEllipse(QPointF(12, 12), 4.4, 4.4)
    painter.restore()


def sun_pixmap(size=64):
    pix = QPixmap(size, size); pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint_sun(painter, QPointF(size/2, size/2), size*.78)
    painter.end(); return pix


def sun_icon(size=64):
    return QIcon(sun_pixmap(size))

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
    CENTER = QPointF(105, 105)
    ACTIONS = {"recovery": QPointF(51, 51), "settings": QPointF(29, 105),
               "models": QPointF(51, 159), "close": QPointF(181, 105)}
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
        self.resize(210, 210)
        self.drag = None; self.pressed = None; self.moved = False
        self.wave = [0.] * 27
        self.menu_progress = 0.; self.hover_progress = 0.; self.hovered = None
        self.menu_animation = QVariantAnimation(self); self.menu_animation.setDuration(130)
        self.menu_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.menu_animation.valueChanged.connect(self.menu_frame)
        self.hover_animation = QVariantAnimation(self); self.hover_animation.setDuration(100)
        self.hover_animation.valueChanged.connect(self.hover_frame)
        self.setMouseTracking(True)
        self.glass = Backdrop(self); self.light_ink = {}
    def ink(self, key, rect, material_name):
        """Icon color for one shape, from the desktop behind it and the tint of the glass."""
        level = self.glass.lightness(rect, GLASS.get(material_name, GLASS["light"])["lens"])
        state = material_name, key
        light = self.light_ink[state] = dark_behind(level, self.light_ink.get(state, material_name == "dark"))
        return INK["light" if light else "dark"]
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
        self.context_menu = GlassMenu(self)
        self.context_menu.addAction(t("Hide in tray"), self.ui.hide)
        self.context_menu.addAction(t("Quit"), self.ui.on_quit)
        handle = int(self.context_menu.winId()); self.ui.app.ui_windows.add(handle)
        self.context_menu.aboutToHide.connect(lambda: self.ui.app.ui_windows.discard(handle))
        self.context_menu.aboutToHide.connect(self.context_menu.deleteLater)
        self.context_menu.popup(event.globalPos()); event.accept()
    def hit(self, point):
        if math.hypot(point.x()-self.CENTER.x(), point.y()-self.CENTER.y()) <= 30: return "logo"
        if self.ui.expanded:
            for action, center in self.ACTIONS.items():
                if math.hypot(point.x()-center.x(), point.y()-center.y()) <= 22: return action
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
                # Paint the glass for the new position now, so it never trails the window.
                self.glass.refresh(.1); self.repaint()
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
        center = self.CENTER
        orb = QRectF(center.x()-29, center.y()-29, 58, 58)
        material_name = self.ui.app.cfg["overlay"].get("material", "light")
        paint_glass(p, orb, 29, self.glass, material_name)
        if self.ui.app.recording:
            p.setPen(QPen(QColor(SUN), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for i in range(11):
                strength = max(self.wave[i*2:i*2+3], default=0)
                x = center.x()+(i-5)*4.2; h = 4+strength*30
                p.drawLine(QPointF(x, center.y()-h/2), QPointF(x, center.y()+h/2))
        else:
            paint_sun(p, center, 34, SUN)
            if self.hovered == "logo" and self.hover_progress:
                hover = QColor(self.ink("logo", orb, material_name)); hover.setAlpha(round(110*self.hover_progress))
                p.setPen(QPen(hover, 1)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(orb.adjusted(-2, -2, 2, 2))
            if self.ui.app._busy_recordings:
                p.setPen(QPen(QColor(SUN), 2.4)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawArc(orb.adjusted(-4, -4, 4, 4), -self.ui.tick_count*7*16, 110*16)
            elif self.ui.status != "Ready":
                p.setPen(QPen(QColor("#eeedeb"), 1)); p.setBrush(QColor(SUN))
                p.drawEllipse(QPointF(orb.right()-2, orb.top()+2), 4.5, 4.5)
        if self.ui.expanded or self.menu_progress > 0:
            p.setOpacity(self.menu_progress)
            for action, target in self.ACTIONS.items():
                c = QPointF(center.x()+(target.x()-center.x())*self.menu_progress,
                            center.y()+(target.y()-center.y())*self.menu_progress)
                drop = QRectF(c.x()-21, c.y()-21, 42, 42)
                paint_glass(p, drop, 21, self.glass, material_name)
                ink = self.ink(action, drop, material_name)
                if self.hovered == action:
                    hover = QColor(ink); hover.setAlpha(28); p.setPen(Qt.PenStyle.NoPen); p.setBrush(hover)
                    p.drawEllipse(drop)
                vector(p, action, QRectF(c.x()-10, c.y()-10, 20, 20), ink)
        p.end()

class FloatingUI:
    def __init__(self, app, on_quit):
        self.app, self.on_quit = app, on_quit
        set_language(app.cfg["ui_language"])
        self.root = qt_app(); apply_material(app.cfg["overlay"].get("material", "light")); self.icon = sun_icon(); self.pixmap = sun_pixmap(64); self.root.setWindowIcon(self.icon)
        self.catalog = ModelCatalog(); self.events = app.ui_events
        self.expanded = False; self.visible = app.cfg["overlay"]["visible"]
        self.dialog = None; self.page = None; self.status = "Ready"; self.tick_count = 0
        self.debug_log = DebugLog(); logging.getLogger("apollo").addHandler(self.debug_log)
        self.debug_lines = deque(maxlen=300); self.debug_revision = 0
        self.last_samples = 0; self.sample_at = time.monotonic()
        self.root.aboutToQuit.connect(self.detach_debug)
        self.orb = Orbit(self); self.orb.setWindowIcon(self.icon)
        screen = self.root.primaryScreen().availableGeometry()
        self.x = app.cfg["overlay"]["x"]; self.y = app.cfg["overlay"]["y"]
        if self.x is None: self.x = screen.right()-100
        if self.y is None: self.y = screen.center().y()
        self.place()
        if self.visible: self.orb.show()
        # The tray is the way back when the floating logo is hidden.
        self.tray = QSystemTrayIcon(self.icon, self.root)
        self.tray.setToolTip("apollo s2t")
        self.tray_menu = GlassMenu(); show, quit_ = self.tray_menu.addAction(""), self.tray_menu.addAction("")
        show.triggered.connect(lambda: app.open_panel()); quit_.triggered.connect(lambda: on_quit())
        self.tray_menu.aboutToShow.connect(lambda: (show.setText(t("Show apollo s2t")), quit_.setText(t("Quit"))))
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(lambda reason: reason == QSystemTrayIcon.ActivationReason.Trigger and app.open_panel())
        self.tray.show()
        self.timer = QTimer(); self.timer.timeout.connect(self.tick); self.timer.start(100)
        self.prune_at = time.monotonic()+15
    def bounds(self):
        screen = self.root.screenAt(QPoint(round(self.x), round(self.y))) or self.root.primaryScreen()
        return screen.availableGeometry()
    def place(self):
        rect = self.bounds(); margin = 98 if self.expanded else 40
        self.x = max(rect.left()+margin, min(rect.right()-margin, self.x))
        self.y = max(rect.top()+margin, min(rect.bottom()-margin, self.y))
        self.orb.move(round(self.x-self.orb.CENTER.x()), round(self.y-self.orb.CENTER.y()))
    def persist_position(self):
        try: self.app.update_preferences({"overlay": {"visible": self.visible, "x": self.x, "y": self.y}})
        except (OSError, ValueError): self.status = "Position konnte nicht gespeichert werden."
    def hide(self):
        self.visible = False; self.expanded = False; self.orb.menu_animation.stop(); self.orb.menu_progress = 0.
        self.orb.hide(); self.close_dialog(); self.persist_position()
    def show(self):
        self.visible = True; self.place(); self.orb.showNormal(); self.orb.raise_(); self.persist_position()
    def reveal(self):
        # An explicit tray action must visibly bring Apollo back, even after a
        # monitor change, minimization or losing the tiny logo at a screen edge.
        rect = self.root.primaryScreen().availableGeometry()
        self.x, self.y = rect.right()-100, rect.center().y()
        self.expanded = True
        self.show(); self.orb.animate_menu(); self.orb.update()
    def collapse(self):
        self.expanded = False; self.orb.animate_menu(); self.close_dialog(); self.place(); self.orb.update()
    def tick(self):
        if self.app._closing.is_set():
            self.detach_debug(); self.timer.stop(); self.tray.hide(); self.orb.close(); self.close_dialog(); self.root.quit(); return
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
                if value: self.show(); self.open_page(value)
                else: self.reveal()
            elif kind == "refresh" and self.page == "recovery": self.refresh_recovery()
        target = list(self.app.recorder.visual_levels()) if self.app.recording else []
        target = ([0.]*27+target)[-27:]
        for i, level in enumerate(target):
            old = self.orb.wave[i]; self.orb.wave[i] = old+(level-old)*(.7 if level > old else .28)
        if getattr(self, "was_recording", False) != self.app.recording:
            self.was_recording = self.app.recording; self.place(); self.orb.update()
        busy = self.app.recording or self.app._busy_recordings
        moving = self.orb.menu_animation.state() == self.orb.menu_animation.State.Running
        if self.orb.isVisible() and self.orb.glass.refresh(.1 if busy or moving else .25): self.orb.update()
        if busy: self.orb.update()
        want = 33 if busy else 100  # animate fast only while recording or processing
        if self.timer.interval() != want: self.timer.setInterval(want)
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
        if page not in PAGES: return
        modal = self.root.activeModalWidget()
        if modal: modal.reject()
        if not self.dialog:
            self.dialog = Shell("Einstellungen", self.pixmap, 680, 560)
            row = QHBoxLayout(); row.setSpacing(16)
            sidebar = QWidget(); sidebar.setFixedWidth(120)
            nav = QVBoxLayout(sidebar); nav.setContentsMargins(0, 0, 0, 0); nav.setSpacing(8)
            self.nav_buttons = {}
            for name, text in PAGES.items():
                b = button(text, lambda checked=False, p=name: self.open_page(p))
                b.setObjectName("nav"); b.setCheckable(True); glyph_button(b, name)
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
        def clear(layout):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget(): item.widget().hide(); item.widget().deleteLater()
                elif item.layout(): clear(item.layout()); item.layout().deleteLater()
        if page in SECTIONS and self.page in SECTIONS:
            self.show_section(page)
        else:
            clear(self.content_layout)
            self.page = page
            for name, b in self.nav_buttons.items(): b.setChecked(name == page)
            self.page_title = label(TITLES[page], "title"); self.content_layout.addWidget(self.page_title)
            if page in SECTIONS: self.settings_page(); self.show_section(page)
            else: getattr(self, page+"_page")()
        self.dialog.show(); self.dialog.raise_(); self.dialog.activateWindow()
    def dialog_closed(self, _):
        dialog, self.dialog = self.dialog, None
        self.page = None
        if dialog: dialog.deleteLater()
    def form(self):
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        body = QWidget(); layout = QVBoxLayout(body); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(24)
        scroll.setWidget(body)
        return scroll, layout
    def group(self, layout, title):
        """One setting: its label, then its controls. Space separates the settings, not boxes."""
        inside = QVBoxLayout(); inside.setSpacing(8)
        inside.addWidget(label(title, "section")); layout.addLayout(inside); return inside
    def footer(self, text, save):
        row = QHBoxLayout(); row.setSpacing(12)
        self.result = label(text, "muted", True); row.addWidget(self.result, 1)
        save_button = button("Speichern", save, primary=True); save_button.setMinimumWidth(120); row.addWidget(save_button)
        self.content_layout.addLayout(row)
    def settings_page(self):
        """All settings in three sections. Only one shows; one Save stores all of them."""
        cfg = self.app.cfg; self.key_fields = {}
        self.sections = QStackedWidget(); self.section_scrolls = []
        def section():
            scroll, layout = self.form(); self.sections.addWidget(scroll); self.section_scrolls.append(scroll); return layout
        layout = section()  # general
        account = self.group(layout, "OpenRouter-API-Schlüssel")
        self.api_input = QLineEdit(); self.api_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_input.setAccessibleName(t("OpenRouter-API-Schlüssel"))
        saved = cfg["smoothing"]["api_key"].strip()
        self.api_input.setPlaceholderText(f"{t('Gespeichert')}: …{saved[-4:]}" if saved else
                                          t("Verwendet OPENROUTER_API_KEY") if api_key(cfg) else t("Kein Schlüssel gespeichert"))
        self.api_input.returnPressed.connect(self.save_api_key)
        row = QHBoxLayout(); row.addWidget(self.api_input, 1)
        self.api_button = button("Prüfen und speichern", self.save_api_key); row.addWidget(self.api_button); account.addLayout(row)
        self.api_status = label("", "muted", True); self.api_status.hide(); account.addWidget(self.api_status)
        interface = self.group(layout, "Sprache der Oberfläche")
        self.interface_language = Choice(LANGUAGES, cfg["ui_language"], translate=False); interface.addWidget(self.interface_language)
        material = self.group(layout, "Overlay-Material")
        self.overlay_material = Choice({"clear": "Klar", "light": "Milchig hell", "dark": "Milchig dunkel"},
                                       cfg["overlay"].get("material", "light"))
        material.addWidget(self.overlay_material)
        updates = self.group(layout, "Updates")
        self.auto_update = QCheckBox(t("Automatisch aktualisieren")); self.auto_update.setChecked(cfg["auto_update"])
        updates.addWidget(self.auto_update)
        updates.addWidget(label("Lädt neue Versionen von GitHub und startet neu, wenn du nicht diktierst.", "muted", True))
        layout.addStretch()
        layout = section()  # recording
        keys = self.group(layout, "Aufnahmetasten")
        row = QHBoxLayout(); row.setSpacing(16); keys.addLayout(row)
        for mode, text in MODES.items():
            column = QVBoxLayout(); column.setSpacing(4); column.addWidget(label(text, "muted"))
            field = KeyCapture(cfg["hotkeys"][mode]); column.addWidget(field); row.addLayout(column, 1); self.key_fields[mode] = field
        for field in self.key_fields.values(): field.setToolTip(t("Taste anklicken und die neue Taste drücken"))
        self.key_mode = Choice({"toggle": "Antippen zum Starten / Stoppen", "hold": "Gedrückt halten"}, cfg["hotkey_mode"]); keys.addWidget(self.key_mode)
        self.fullscreen_keys = QCheckBox(t("Tasten auch in Vollbild-Apps und Spielen"))
        self.fullscreen_keys.setChecked(cfg["hotkeys_in_fullscreen"]); keys.addWidget(self.fullscreen_keys)
        self.fullscreen_keys.setToolTip(t("Aus: Spiele wie Minecraft behalten F8, F9 und F10. Eine laufende Aufnahme lässt sich immer stoppen."))
        numbers = QHBoxLayout(); numbers.setSpacing(16); layout.addLayout(numbers)
        recording = self.group(numbers, "Maximale Aufnahmelänge (Minuten)")
        self.record_minutes = QLineEdit(str(cfg["recording"]["max_minutes"]))
        self.record_minutes.setAccessibleName(t("Maximale Aufnahmelänge (Minuten)"))
        recording.addWidget(self.record_minutes)
        cache = self.group(numbers, "Recovery-Dauer in Minuten")
        self.minutes = QLineEdit(str(cfg["recovery_cache"]["minutes"])); self.minutes.setAccessibleName(t("Recovery-Dauer in Minuten"))
        cache.addWidget(self.minutes)
        self.minutes.setToolTip(t("5–60 Minuten · Abgelaufene Aufnahmen werden automatisch gelöscht."))
        words = self.group(layout, "Persönliches Wörterbuch")
        self.vocabulary = QPlainTextEdit("\n".join(cfg["openrouter_stt"]["vocabulary"]))
        self.vocabulary.setAccessibleName(t("Persönliches Wörterbuch")); self.vocabulary.setFixedHeight(72)
        self.vocabulary.setPlaceholderText("PANDU\nOpenRouter")
        words.addWidget(self.vocabulary)
        self.vocabulary.setToolTip(t("Ein Begriff pro Zeile · maximal 100. MAI-Transcribe 2 erhält diese Begriffe mit dem Audio als Erkennungshilfe. Andere Modelle verwenden sie derzeit nicht."))
        words.addWidget(label("Ein Begriff pro Zeile · mit Audio an MAI 2 gesendet", "muted", True))
        layout.addStretch()
        layout = section()  # prompt
        output = self.group(layout, "Ausgabesprache")
        self.language = Choice({"english": "Englisch", "german": "Deutsch", "match": "Wie gesprochen"}, cfg["prompt_profiles"]["output_language"]); output.addWidget(self.language)
        profiles = self.group(layout, "Prompt-Profil")
        self.profile = Choice({p:p for p in self.app.available_profiles()}, cfg["prompt_profiles"]["active"], translate=False, menu=True); profiles.addWidget(self.profile)
        self.profile_text = QPlainTextEdit(self.app.profile_text(self.profile.value)); self.profile_text.setFixedHeight(140)
        self.profile_loaded = self.profile.value  # the profile whose text the editor shows
        self.profile_text.setAccessibleName(t("Inhalt des Prompt-Profils"))
        self.profile_text.setPlaceholderText(t("Projekt, Stack, Ordner und Regeln für F10"))
        self.profile.changed.connect(self.switch_profile); profiles.addWidget(self.profile_text)
        profiles.addWidget(label("F10 sendet diesen Text als Projekt-Kontext. Text zwischen <!-- und --> ist nur eine Notiz für dich.", "muted", True))
        row = QHBoxLayout(); self.new_profile = QLineEdit(); self.new_profile.setPlaceholderText(t("Name für ein neues Profil"))
        self.new_profile.setAccessibleName(t("Name für ein neues Profil")); self.new_profile.returnPressed.connect(self.create_profile)
        row.addWidget(self.new_profile, 1); row.addWidget(button("Anlegen", self.create_profile)); profiles.addLayout(row)
        layout.addStretch()
        self.content_layout.addWidget(self.sections, 1); self.footer("", self.save_settings)
    def show_section(self, page):
        """Switch between the settings sections without a rebuild, so unsaved edits stay."""
        self.page = page
        for name, b in self.nav_buttons.items(): b.setChecked(name == page)
        self.page_title.setText(t(TITLES[page]))
        index = list(SECTIONS).index(page)
        self.sections.setCurrentIndex(index); self.form_scroll = self.section_scrolls[index]
    def store_profile_text(self, name):
        """Write the editor text to its profile, if the user changed it."""
        if self.profile_text.document().isModified():
            self.app.save_profile(name, self.profile_text.toPlainText())
            self.profile_text.document().setModified(False)
    def switch_profile(self, name):
        try:
            self.store_profile_text(self.profile_loaded)
        except (OSError, ValueError) as exc:
            self.result.setText(t(str(exc) if isinstance(exc, ValueError) else "Speichern fehlgeschlagen. Bitte erneut versuchen.")); self.result.setObjectName("error")
        self.profile_text.setPlainText(self.app.profile_text(name)); self.profile_text.document().setModified(False)
        self.profile_loaded = name
    def create_profile(self):
        name = self.new_profile.text().strip()
        if not name: return
        try:
            if name in self.profile.options: raise ValueError("Dieses Profil gibt es schon.")
            self.store_profile_text(self.profile.value)
            self.app.save_profile(name, PROFILE_TEMPLATE)
            self.profile.options = {p:p for p in self.app.available_profiles()}
            self.profile.set_value(name); self.switch_profile(name); self.new_profile.clear()
            self.result.setObjectName("muted"); self.result.setText(t("Profil angelegt. Text eintragen und speichern."))
        except (OSError, ValueError) as exc:
            self.result.setText(t(str(exc) if isinstance(exc, ValueError) else "Speichern fehlgeschlagen. Bitte erneut versuchen.")); self.result.setObjectName("error")
        self.result.style().unpolish(self.result); self.result.style().polish(self.result)
    def save_api_key(self):
        key = self.api_input.text().strip()
        if not key: return
        self.api_button.setEnabled(False); self.set_api_status("Schlüssel wird geprüft …", "muted")
        result = []
        # The check is a network call: run it off the Qt thread and poll for the answer.
        threading.Thread(target=lambda: result.append(check_key(key)), daemon=True).start()
        def done():
            if not result: QTimer.singleShot(100, done); return
            problem = result[0]
            try:
                if problem not in KEY_UNUSABLE:
                    # Offline is not a reason to lose a new key; save it and say it is unchecked.
                    self.app.update_preferences({"smoothing": {"api_key": key}})
            except (OSError, ValueError):
                problem = "Speichern fehlgeschlagen. Bitte erneut versuchen."
            try:
                self.api_button.setEnabled(True)
                if problem in KEY_UNUSABLE or problem == "Speichern fehlgeschlagen. Bitte erneut versuchen.":
                    self.set_api_status(problem, "error"); return
                self.api_input.clear(); self.api_input.setPlaceholderText(f"{t('Gespeichert')}: …{key[-4:]}")
                self.set_api_status("Gespeichert und geprüft." if not problem else t("Gespeichert, aber nicht geprüft:") + " " + t(problem), "muted")
            except RuntimeError:
                pass  # the settings page closed while the check ran; the key is saved
        QTimer.singleShot(100, done)
    def set_api_status(self, text, kind):
        self.api_status.setText(t(text)); self.api_status.setObjectName(kind); self.api_status.show()
        self.api_status.style().unpolish(self.api_status); self.api_status.style().polish(self.api_status)
    def save_settings(self):
        previous_language = self.app.cfg["ui_language"]
        scroll_position = self.form_scroll.verticalScrollBar().value()
        try:
            try: record_minutes = int(self.record_minutes.text())
            except ValueError: raise ValueError("Bitte eine Aufnahmelänge von 1 bis 30 Minuten eingeben.")
            if not 1 <= record_minutes <= 30: raise ValueError("Bitte eine Aufnahmelänge von 1 bis 30 Minuten eingeben.")
            try: minutes = int(self.minutes.text())
            except ValueError: raise ValueError("Bitte eine Recovery-Dauer von 5 bis 60 Minuten eingeben.")
            if not 5 <= minutes <= 60: raise ValueError("Bitte eine Recovery-Dauer von 5 bis 60 Minuten eingeben.")
            self.store_profile_text(self.profile.value)
            self.app.update_preferences({"hotkeys": {k:v.value for k,v in self.key_fields.items()}, "hotkey_mode": self.key_mode.value,
                "recording": {"max_minutes": record_minutes},
                "hotkeys_in_fullscreen": self.fullscreen_keys.isChecked(), "auto_update": self.auto_update.isChecked(), "ui_language": self.interface_language.value,
                "overlay": {"material": self.overlay_material.value},
                "openrouter_stt": {"vocabulary": [line.strip() for line in self.vocabulary.toPlainText().splitlines() if line.strip()]},
                "prompt_profiles": {"active": self.profile.value, "output_language": self.language.value}, "recovery_cache": {"minutes": minutes}})
            self.app.prune_recovery(); self.result.setObjectName("muted")
            set_language(self.app.cfg["ui_language"])
            apply_material(self.app.cfg["overlay"]["material"]); self.dialog.follow_backdrop(); self.orb.update()
            for name, b in self.nav_buttons.items(): b.setText(t(PAGES[name]))
            if previous_language != self.app.cfg["ui_language"]:
                page, self.page = self.page, None; self.open_page(page)  # rebuild in the new language
            self.result.setText(t("Gespeichert"))
            scroll = self.form_scroll
            QTimer.singleShot(0, lambda: scroll.verticalScrollBar().setValue(scroll_position))
        except (OSError, ValueError) as exc:
            self.result.setText(t(str(exc) if isinstance(exc, ValueError) else "Speichern fehlgeschlagen. Bitte erneut versuchen.")); self.result.setObjectName("error")
        self.result.style().unpolish(self.result); self.result.style().polish(self.result)
    def models_page(self):
        cfg = self.app.cfg; layout = self.content_layout; self.model_fields = {}
        for key, title, kind, value, allow_none in (
            ("primary", "Haupttranskription", "transcription", cfg["openrouter_stt"]["model"], False),
            ("fallback", "Fallback bei Rate Limit (429)", "transcription", cfg["openrouter_stt"].get("fallback_model"), True),
            ("text", "Bereinigen & Prompt", "text", cfg["smoothing"]["model"], False)):
            field = ModelField(title, kind, value, self.catalog, allow_none)
            layout.addWidget(field); self.model_fields[key] = field
        reload = button("Katalog erneut laden", lambda: self.catalog.start(reload=True), quiet=True)
        layout.addWidget(reload, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1); self.footer("", self.save_models); self.catalog.start()
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
        self.content_layout.addWidget(controls)
        self.content_layout.addWidget(label("Live-Debug", "section"))
        self.debug_state = label("", "muted", True); self.content_layout.addWidget(self.debug_state)
        self.debug_text = QPlainTextEdit(); self.debug_text.setReadOnly(True); self.debug_text.setMaximumBlockCount(300)
        self.debug_text.setPlaceholderText(t("Aufnahmeverlauf"))
        self.debug_text.setFixedHeight(108); self.debug_text.setAccessibleName(t("Live-Debug-Verlauf"))
        self.debug_text.setObjectName("log")
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
            if self.status != "Ready": state += " · " + t(self.status)
        else:
            state = t("Ready") + " · " + " / ".join(v.upper() for k,v in app.cfg["hotkeys"].items() if k in MODES)
            if self.status != "Ready": state += "\n" + t(self.status)
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
        self.recovery_list.blockSignals(False); self.preview()
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
