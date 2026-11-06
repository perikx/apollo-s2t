"""Shared Qt surfaces, vector icons and DPI-aware typography."""
from pathlib import Path
import sys
from string import Template
from apollo_i18n import t

from PySide6.QtCore import Qt, QRectF, QPointF, QSize
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap, QPalette, QBrush
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel,
                              QPushButton, QVBoxLayout, QWidget)

# Panels and menus use the same material as the floating orb. Windows gives a
# frameless Qt window no reliable backdrop blur, so the glass is a tint: clear
# shows the most of the desktop, milky light and milky dark show less of it.
_LIGHT = dict(surface="rgba(246, 246, 243, 236)", edge="rgba(255, 255, 255, 235)", card="rgba(255, 255, 255, 130)",
              line="rgba(35, 35, 35, 34)", text="#232323", muted="#5f5f5c", error="#a72e29",
              field="rgba(255, 255, 255, 200)", focus="#232323", button="#232323", button_text="#fafafa",
              button_hover="#3c3c3c", button_pressed="#111111", nav_checked="#50504e",
              disabled="rgba(35, 35, 35, 40)", disabled_text="#6f6f6c", item_hover="rgba(35, 35, 35, 16)",
              item_selected="rgba(35, 35, 35, 34)", item_border="rgba(35, 35, 35, 90)", check_border="#868682")
MATERIAL_STYLES = {
    "light": _LIGHT,
    "clear": dict(_LIGHT, surface="rgba(250, 250, 248, 200)", card="rgba(255, 255, 255, 96)", field="rgba(255, 255, 255, 176)"),
    "dark": dict(surface="rgba(28, 28, 30, 236)", edge="rgba(255, 255, 255, 55)", card="rgba(255, 255, 255, 14)",
                 line="rgba(255, 255, 255, 34)", text="#fafafa", muted="#b4b4af", error="#ff9b8f",
                 field="rgba(255, 255, 255, 20)", focus="#fafafa", button="rgba(255, 255, 255, 34)", button_text="#fafafa",
                 button_hover="rgba(255, 255, 255, 52)", button_pressed="rgba(255, 255, 255, 22)",
                 nav_checked="rgba(255, 255, 255, 78)", disabled="rgba(255, 255, 255, 14)", disabled_text="#8d8d89",
                 item_hover="rgba(255, 255, 255, 18)", item_selected="rgba(255, 255, 255, 36)",
                 item_border="rgba(255, 255, 255, 110)", check_border="#b4b4af"),
}
_STYLE = Template("""
QWidget { color: $text; font-family: 'Figtree'; font-size: 14px; }
QFrame#surface { background: $surface; border: 1px solid $edge; border-radius: 24px; }
QFrame#card { background: $card; border: 1px solid $line; border-radius: 16px; }
QLabel { background: transparent; border: none; }
QLabel#title { font-size: 21px; font-weight: 600; }
QLabel#section { font-size: 14px; font-weight: 600; }
QLabel#muted { color: $muted; font-size: 12px; }
QLabel#error { color: $error; font-size: 13px; }
QPushButton { background: $button; color: $button_text; border: 1px solid transparent; border-radius: 12px;
              padding: 7px 10px; font-size: 14px; text-align: left; }
QPushButton:hover { background: $button_hover; }
QPushButton:pressed { background: $button_pressed; }
QPushButton:focus { border-color: $check_border; }
QPushButton:disabled { background: $disabled; color: $disabled_text; }
QPushButton#primary { font-weight: 600; text-align: center; }
QPushButton#quiet { background: transparent; border: none; color: $muted; }
QPushButton#nav { padding: 8px; }
QPushButton#nav:checked { background: $nav_checked; }
QLineEdit, QTextEdit, QPlainTextEdit { background: $field; color: $text; border: 1px solid $line; border-radius: 12px;
                                    padding: 8px; selection-background-color: $focus; selection-color: $selection_text; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus { border-color: $focus; }
QPlainTextEdit#log { border-radius: 8px; padding: 6px; font-size: 12px; }
QListWidget { background: transparent; border: none; outline: none; padding: 3px; }
QListWidget::item { background: $card; color: $text; border: 1px solid $line; border-radius: 13px; margin: 2px 0px; padding: 8px; }
QListWidget::item:selected { background: $item_selected; border-color: $item_border; color: $text; }
QListWidget::item:hover { background: $item_hover; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { width: 0px; }
QScrollBar:horizontal { height: 0px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }
QCheckBox { spacing: 10px; }
QCheckBox::indicator { width: 22px; height: 22px; border: 1px solid $check_border; border-radius: 6px; background: $field; }
QCheckBox::indicator:checked { background: $focus; border-color: $focus; }
QToolTip { background: #232323; color: #fafafa; border: 1px solid #50504e; padding: 8px; }
QMenu { background: $surface; color: $text; border: 1px solid $edge; border-radius: 12px; padding: 4px; }
QMenu::item { padding: 8px 16px; border-radius: 8px; }
QMenu::item:selected { background: $item_selected; color: $text; }
""")
_current = {"name": "light"}


def theme():
    """The colors of the active material, for widgets that paint text themselves."""
    return MATERIAL_STYLES[_current["name"]]


def style(material):
    colors = MATERIAL_STYLES.get(material, _LIGHT)
    return _STYLE.substitute(colors, selection_text="#fafafa" if colors["focus"] == "#232323" else "#232323")


def apply_material(material):
    """Restyle all Apollo windows and menus. Open windows change at once."""
    _current["name"] = material if material in MATERIAL_STYLES else "light"
    colors = theme(); app = qt_app(); palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, colors["surface"]), (QPalette.ColorRole.Base, colors["field"]),
                        (QPalette.ColorRole.Text, colors["text"]), (QPalette.ColorRole.WindowText, colors["text"]),
                        (QPalette.ColorRole.Button, colors["button"]), (QPalette.ColorRole.ButtonText, colors["button_text"]),
                        (QPalette.ColorRole.Highlight, colors["focus"]), (QPalette.ColorRole.HighlightedText, colors["surface"])):
        palette.setColor(role, QColor(color))
    app.setPalette(palette); app.setStyleSheet(style(_current["name"]))
    for window in app.topLevelWidgets():
        if isinstance(window, Shell): window.restyle()


def glass_menu(menu):
    """Let a menu show the material tint and round corners instead of a square native frame."""
    menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    return menu


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
        self.close_button = close = QPushButton(); close.setIcon(icon("close")); close.setIconSize(QSize(18, 18)); close.setFixedSize(30, 30)
        close.setObjectName("quiet"); close.setAccessibleName(t("Fenster schließen")); close.clicked.connect(self.reject); row.addWidget(close)
        self.layout.addWidget(header)
        header.mousePressEvent = self.start_move
        self._move_origin = None
        header.mouseMoveEvent = self.move_window
        self.finished.connect(lambda _: self.hide())
    def restyle(self):
        self.close_button.setIcon(icon("close"))
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
