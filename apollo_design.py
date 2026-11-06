"""Shared Qt surfaces, vector icons and DPI-aware typography."""
from pathlib import Path
import sys
from string import Template
from apollo_i18n import t

from PySide6.QtCore import Qt, QRectF, QPointF, QSize, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap, QPalette, QBrush
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QMenu,
                              QPushButton, QVBoxLayout, QWidget)
from apollo_glass import Backdrop, paint_glass

# Windows, menus and the orb are liquid glass in the chosen material. The
# colors are warm grays with the orange of the sun as the one accent.
SUN = "#ff8a00"
_LIGHT = dict(text="#232323", muted="#5f5f5c", error="#a72e29", card="rgba(255, 255, 255, 110)",
              line="rgba(35, 35, 35, 30)", field="rgba(255, 255, 255, 170)", accent=SUN, accent_hover="#ff9d2e",
              accent_text="#232323", button="rgba(35, 35, 35, 20)", button_hover="rgba(35, 35, 35, 36)",
              button_pressed="rgba(35, 35, 35, 52)", nav_checked="rgba(255, 138, 0, 70)",
              disabled="rgba(35, 35, 35, 12)", disabled_text="#8a8a86", item_hover="rgba(35, 35, 35, 16)",
              item_selected="rgba(255, 138, 0, 60)", check_border="#868682", window="#f6f6f3")
MATERIAL_STYLES = {
    "light": _LIGHT,
    "clear": dict(_LIGHT, card="rgba(255, 255, 255, 80)", field="rgba(255, 255, 255, 140)"),
    "dark": dict(_LIGHT, text="#fafafa", muted="#b4b4af", error="#ff9b8f", card="rgba(255, 255, 255, 16)",
                 line="rgba(255, 255, 255, 34)", field="rgba(255, 255, 255, 22)", accent_text="#1c1c1e",
                 button="rgba(255, 255, 255, 28)", button_hover="rgba(255, 255, 255, 44)",
                 button_pressed="rgba(255, 255, 255, 18)", nav_checked="rgba(255, 138, 0, 90)",
                 disabled="rgba(255, 255, 255, 12)", disabled_text="#8d8d89", item_hover="rgba(255, 255, 255, 18)",
                 item_selected="rgba(255, 138, 0, 80)", check_border="#b4b4af", window="#1c1c1e"),
}
_STYLE = Template("""
QWidget { color: $text; font-family: 'Figtree'; font-size: 14px; }
QFrame#surface { background: transparent; border: none; }
QFrame#card { background: $card; border: 1px solid $line; border-radius: 16px; }
QLabel { background: transparent; border: none; }
QLabel#title { font-size: 21px; font-weight: 600; }
QLabel#section { font-size: 14px; font-weight: 600; }
QLabel#muted { color: $muted; font-size: 12px; }
QLabel#error { color: $error; font-size: 13px; }
QPushButton { background: $button; color: $text; border: 1px solid transparent; border-radius: 12px;
              padding: 7px 10px; font-size: 14px; text-align: left; }
QPushButton:hover { background: $button_hover; }
QPushButton:pressed { background: $button_pressed; }
QPushButton:focus { border-color: $accent; }
QPushButton:disabled { background: $disabled; color: $disabled_text; }
QPushButton#primary { background: $accent; color: $accent_text; font-weight: 600; text-align: center; }
QPushButton#primary:hover { background: $accent_hover; }
QPushButton#primary:focus { border-color: $text; }
QPushButton#quiet { background: transparent; border: none; color: $muted; }
QPushButton#nav { padding: 8px; }
QPushButton#nav:checked { background: $nav_checked; }
QLineEdit, QTextEdit, QPlainTextEdit { background: $field; color: $text; border: 1px solid $line; border-radius: 12px;
                                    padding: 8px; selection-background-color: $accent; selection-color: $accent_text; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus { border-color: $accent; }
QPlainTextEdit#log { border-radius: 8px; padding: 6px; font-size: 12px; }
QListWidget { background: transparent; border: none; outline: none; padding: 3px; }
QListWidget::item { background: $card; color: $text; border: 1px solid $line; border-radius: 13px; margin: 2px 0px; padding: 8px; }
QListWidget::item:selected { background: $item_selected; border-color: $accent; color: $text; }
QListWidget::item:hover { background: $item_hover; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { width: 0px; }
QScrollBar:horizontal { height: 0px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }
QCheckBox { spacing: 10px; }
QCheckBox::indicator { width: 22px; height: 22px; border: 1px solid $check_border; border-radius: 6px; background: $field; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }
QToolTip { background: #232323; color: #fafafa; border: 1px solid #50504e; padding: 8px; }
QMenu { background: transparent; color: $text; border: none; padding: 6px; }
QMenu::item { padding: 8px 16px; border-radius: 8px; }
QMenu::item:selected { background: $item_selected; color: $text; }
""")
_current = {"name": "light"}


def theme():
    """The colors of the active material, for widgets that paint text themselves."""
    return MATERIAL_STYLES[_current["name"]]


def material():
    return _current["name"]


def style(material):
    return _STYLE.substitute(MATERIAL_STYLES.get(material, _LIGHT))


def apply_material(material):
    """Restyle all Apollo windows and menus. Open windows change at once."""
    _current["name"] = material if material in MATERIAL_STYLES else "light"
    colors = theme(); app = qt_app(); palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, colors["window"]), (QPalette.ColorRole.Base, colors["window"]),
                        (QPalette.ColorRole.Text, colors["text"]), (QPalette.ColorRole.WindowText, colors["text"]),
                        (QPalette.ColorRole.Button, colors["window"]), (QPalette.ColorRole.ButtonText, colors["text"]),
                        (QPalette.ColorRole.Highlight, colors["accent"]), (QPalette.ColorRole.HighlightedText, colors["accent_text"])):
        palette.setColor(role, QColor(color))
    app.setPalette(palette); app.setStyleSheet(style(_current["name"]))
    for widget in app.allWidgets():
        if widget.property("glyph"): widget.setIcon(icon(widget.property("glyph")))
        if isinstance(widget, (Shell, GlassMenu)): widget.update()


def glyph_button(widget, kind):
    """An icon on a button that follows the text color of the material."""
    widget.setProperty("glyph", kind); widget.setIcon(icon(kind)); return widget


class GlassMenu(QMenu):
    """A menu on liquid glass with round corners instead of a square native frame."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.glass = Backdrop(self)
    def showEvent(self, event):
        self.glass.refresh(0); super().showEvent(event)
    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint_glass(painter, QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 12, self.glass, material(), panel=True)
        painter.end(); super().paintEvent(event)


def qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
        app.setApplicationName("apollo s2t")
        app.setQuitOnLastWindowClosed(False)
        app.setStyle("Fusion")
        font_path = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets/fonts/Figtree.ttf"
        QFontDatabase.addApplicationFont(str(font_path))
        app.setFont(QFont("Figtree", 10))
        app.setStyleSheet(style(_current["name"]))
    return app


def logo_path(base_dir=None):
    candidates = [Path(base_dir or ".") / "assets/apollo-app.png",
                  Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets/apollo-app.png"]
    return next((p for p in candidates if p.exists()), candidates[-1])


def logo_pixmap(path):
    return QPixmap(str(path))


def vector(painter, kind, rect, color=None):
    """Consistent line icons, drawn as vectors at the current device scale."""
    painter.save()
    painter.translate(rect.x(), rect.y())
    painter.scale(rect.width()/24, rect.height()/24)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(color or theme()["text"]), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if kind == "close":
        painter.drawLine(QPointF(6, 6), QPointF(18, 18)); painter.drawLine(QPointF(18, 6), QPointF(6, 18))
    elif kind == "recovery":
        painter.drawArc(QRectF(4, 4, 16, 16), -90*16, 270*16)
        painter.drawLine(QPointF(1, 7), QPointF(4, 12)); painter.drawLine(QPointF(4, 12), QPointF(8, 9))
        painter.drawLine(QPointF(12, 7), QPointF(12, 12)); painter.drawLine(QPointF(12, 12), QPointF(15, 14))
    elif kind == "settings":
        for y, x in ((6, 9), (12, 16), (18, 7)):
            painter.drawLine(QPointF(3, y), QPointF(21, y))
            painter.setBrush(QColor(color)); painter.drawEllipse(QRectF(x-2, y-2, 4, 4)); painter.setBrush(Qt.BrushStyle.NoBrush)
    elif kind == "models":
        for x, y in ((6, 6), (18, 6), (12, 18)):
            painter.drawEllipse(QRectF(x-2.5, y-2.5, 5, 5))
        painter.drawLine(QPointF(8.5, 6), QPointF(15.5, 6))
        painter.drawLine(QPointF(7, 8), QPointF(11, 16)); painter.drawLine(QPointF(17, 8), QPointF(13, 16))
    elif kind == "chevron":
        painter.drawLine(QPointF(8, 10), QPointF(12, 14)); painter.drawLine(QPointF(12, 14), QPointF(16, 10))
    painter.restore()


def icon(kind, color=None):
    pix = QPixmap(48, 48); pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    vector(painter, kind, QRectF(0, 0, 48, 48), color); painter.end()
    return QIcon(pix)


_LOGO_TEXTURES = {}


def paint_logo(painter, pixmap, rect):
    """Antialiased ellipse fill rather than a binary clipping mask at the edge."""
    ratio = painter.device().devicePixelRatioF()
    key = (pixmap.cacheKey(), rect.width(), rect.height(), ratio)
    if key not in _LOGO_TEXTURES:
        if len(_LOGO_TEXTURES) > 32: _LOGO_TEXTURES.clear()
        texture = pixmap.scaled(round(rect.width()*ratio), round(rect.height()*ratio),
                                Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        texture.setDevicePixelRatio(ratio)
        _LOGO_TEXTURES[key] = texture
    painter.save(); painter.translate(rect.topLeft())
    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QBrush(_LOGO_TEXTURES[key]))
    painter.drawEllipse(QRectF(0, 0, rect.width(), rect.height())); painter.restore()


def label(text, name=None, wrap=False):
    result = QLabel(t(text))
    if name: result.setObjectName(name)
    result.setWordWrap(wrap)
    return result


def button(text, callback, primary=False, quiet=False):
    result = QPushButton(t(text)); result.setCursor(Qt.CursorShape.PointingHandCursor)
    result.setAutoDefault(False)
    result.setMinimumHeight(34)
    if primary: result.setObjectName("primary")
    if quiet: result.setObjectName("quiet")
    result.clicked.connect(callback)
    return result


class Shell(QDialog):
    """Frameless rounded popup with a plain draggable title bar."""
    def __init__(self, title, pixmap, width=680, height=540, parent=None):
        super().__init__(parent)
        self.setWindowTitle("apollo s2t · " + t(title)); self.setWindowIcon(QIcon(pixmap))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(width, height); self.setMinimumSize(min(width, 520), min(height, 400))
        outer = QVBoxLayout(self); outer.setContentsMargins(8, 8, 8, 8)
        self.surface = QFrame(); self.surface.setObjectName("surface"); outer.addWidget(self.surface)
        self.layout = QVBoxLayout(self.surface); self.layout.setContentsMargins(16, 12, 16, 16); self.layout.setSpacing(12)
        header = QWidget(); header.setFixedHeight(34); row = QHBoxLayout(header); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(8)
        row.addWidget(label("apollo", "section")); row.addStretch()
        close = glyph_button(QPushButton(), "close"); close.setIconSize(QSize(18, 18)); close.setFixedSize(30, 30)
        close.setObjectName("quiet"); close.setAccessibleName(t("Fenster schließen")); close.clicked.connect(self.reject); row.addWidget(close)
        self.layout.addWidget(header)
        header.mousePressEvent = self.start_move
        self._move_origin = None
        header.mouseMoveEvent = self.move_window
        self.finished.connect(lambda _: self.hide())
        self.glass = Backdrop(self)
        # The desktop behind can change; a capture takes about 7 ms.
        self.glass_timer = QTimer(self); self.glass_timer.timeout.connect(self.refresh_glass); self.glass_timer.start(500)
    def refresh_glass(self, max_age=.5):
        if self.isVisible() and self.glass.refresh(max_age): self.update()
    def showEvent(self, event):
        self.glass.refresh(0); super().showEvent(event)
    def moveEvent(self, event):
        super().moveEvent(event); self.refresh_glass(.03)
    def paintEvent(self, event):
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.surface.geometry())
        painter.setPen(Qt.PenStyle.NoPen)
        for spread, alpha in ((6, 10), (3, 18)):
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(rect.adjusted(-spread/2, -spread/4, spread/2, spread), 24+spread/2, 24+spread/2)
        paint_glass(painter, rect, 24, self.glass, material(), panel=True); painter.end()
    def start_move(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.windowHandle() and self.windowHandle().startSystemMove(): return
            self._move_origin = event.globalPosition().toPoint() - self.pos()
    def move_window(self, event):
        if self._move_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._move_origin)
    def keyPressEvent(self, event):
        # Enter in a form must not trigger QDialog.accept and silently close it.
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept(); return
        super().keyPressEvent(event)
