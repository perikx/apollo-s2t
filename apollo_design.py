"""Shared Qt surfaces, vector icons and DPI-aware typography."""
from pathlib import Path
import sys
from apollo_i18n import t

from PySide6.QtCore import Qt, QRectF, QPointF, QSize
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPen, QPixmap, QPalette, QBrush
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel,
                              QPushButton, QVBoxLayout, QWidget)

ACCENT = "#232323"
TEXT = "#232323"
MUTED = "#696966"
STYLE = """
QWidget { color: #232323; font-family: 'Figtree'; font-size: 14px; }
QFrame#surface { background: #eeedeb; border: 1px solid #cecdca; border-radius: 24px; }
QFrame#card { background: #f8f8f6; border: 1px solid #d7d6d3; border-radius: 16px; }
QLabel { background: transparent; border: none; }
QLabel#title { font-size: 21px; font-weight: 600; }
QLabel#section { font-size: 14px; font-weight: 600; }
QLabel#muted { color: #696966; font-size: 12px; }
QLabel#error { color: #a72e29; font-size: 13px; }
QPushButton { background: #232323; color: #fafafa; border: 1px solid #232323; border-radius: 12px;
              padding: 7px 10px; font-size: 14px; text-align: left; }
QPushButton:hover { background: #3c3c3c; border-color: #3c3c3c; }
QPushButton:pressed { background: #111111; }
QPushButton:focus { border-color: #81817e; }
QPushButton:disabled { background: #d4d4d1; color: #777774; border-color: #d4d4d1; }
QPushButton#primary { font-weight: 600; text-align: center; }
QPushButton#quiet { background: transparent; border: none; color: #696966; }
QPushButton#nav { padding: 8px; }
QPushButton#nav:checked { background: #50504e; border-color: #50504e; }
QLineEdit, QTextEdit, QPlainTextEdit { background: #fafafa; color: #232323; border: 1px solid #cfcecb; border-radius: 12px;
                                    padding: 8px; selection-background-color: #232323; selection-color: #fafafa; }
QLineEdit:focus, QTextEdit:focus { border-color: #232323; }
QListWidget { background: transparent; border: none; outline: none; padding: 3px; }
QListWidget::item { background: #f8f8f6; border: 1px solid #d7d6d3; border-radius: 13px; margin: 2px 0px; padding: 8px; }
QListWidget::item:selected { background: #dededb; border-color: #868682; color: #232323; }
QListWidget::item:hover { background: #e6e6e3; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { width: 0px; }
QScrollBar:horizontal { height: 0px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0px; height: 0px; }
QCheckBox { spacing: 10px; }
QCheckBox::indicator { width: 22px; height: 22px; border: 1px solid #868682; border-radius: 6px; background: #fafafa; }
QCheckBox::indicator:checked { background: #232323; border-color: #232323; }
QToolTip { background: #232323; color: #fafafa; border: 1px solid #50504e; padding: 8px; }
QMenu { background: #eeedeb; color: #232323; border: 1px solid #cecdca; padding: 4px; }
QMenu::item { padding: 8px 16px; border-radius: 6px; }
QMenu::item:selected { background: #232323; color: #fafafa; }
"""


def qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
        app.setApplicationName("apollo s2t")
        app.setQuitOnLastWindowClosed(False)
        app.setStyle("Fusion")
        palette = QPalette()
        for role, color in ((QPalette.ColorRole.Window, "#eeedeb"), (QPalette.ColorRole.Base, "#fafafa"),
                            (QPalette.ColorRole.Text, TEXT), (QPalette.ColorRole.WindowText, TEXT),
                            (QPalette.ColorRole.Button, ACCENT), (QPalette.ColorRole.ButtonText, "#fafafa"),
                            (QPalette.ColorRole.Highlight, ACCENT), (QPalette.ColorRole.HighlightedText, "#fafafa")):
            palette.setColor(role, QColor(color))
        app.setPalette(palette)
        font_path = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets/fonts/Figtree.ttf"
        QFontDatabase.addApplicationFont(str(font_path))
        app.setFont(QFont("Figtree", 10))
        app.setStyleSheet(STYLE)
    return app


def logo_path(base_dir=None):
    candidates = [Path(base_dir or ".") / "assets/apollo.png",
                  Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets/apollo.png"]
    return next((p for p in candidates if p.exists()), candidates[-1])


def logo_pixmap(path):
    """Use the opaque artwork's viewport; exclude stray transparent border pixels."""
    from PIL import Image
    import numpy as np
    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        alpha = np.array(rgba.getchannel("A"))
        rows = np.flatnonzero((alpha > 230).sum(axis=1) > image.width * .25)
        cols = np.flatnonzero((alpha > 230).sum(axis=0) > image.height * .25)
    pixmap = QPixmap(str(path))
    if len(rows) and len(cols):
        pixmap = pixmap.copy(int(cols[0]), int(rows[0]), int(cols[-1]-cols[0]+1), int(rows[-1]-rows[0]+1))
    return pixmap


def vector(painter, kind, rect, color=TEXT):
    """Consistent line icons, drawn as vectors at the current device scale."""
    painter.save()
    painter.translate(rect.x(), rect.y())
    painter.scale(rect.width()/24, rect.height()/24)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor(color), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    if kind == "close":
        painter.drawLine(QPointF(6, 6), QPointF(18, 18)); painter.drawLine(QPointF(18, 6), QPointF(6, 18))
    elif kind == "recovery":
        painter.drawArc(QRectF(4, 4, 16, 16), -90*16, 270*16)
        painter.drawLine(QPointF(1, 7), QPointF(4, 12)); painter.drawLine(QPointF(4, 12), QPointF(8, 9))
        painter.drawLine(QPointF(12, 7), QPointF(12, 12)); painter.drawLine(QPointF(12, 12), QPointF(15, 14))
    elif kind == "settings":
        for y, x in ((6, 9), (12, 16), (18, 7)):
            painter.drawLine(QPointF(3, y), QPointF(21, y))
            painter.setBrush(QColor("#2b2b29")); painter.drawEllipse(QRectF(x-2, y-2, 4, 4)); painter.setBrush(Qt.BrushStyle.NoBrush)
    elif kind == "models":
        for x, y in ((6, 6), (18, 6), (12, 18)):
            painter.drawEllipse(QRectF(x-2.5, y-2.5, 5, 5))
        painter.drawLine(QPointF(8.5, 6), QPointF(15.5, 6))
        painter.drawLine(QPointF(7, 8), QPointF(11, 16)); painter.drawLine(QPointF(17, 8), QPointF(13, 16))
    elif kind == "chevron":
        painter.drawLine(QPointF(8, 10), QPointF(12, 14)); painter.drawLine(QPointF(12, 14), QPointF(16, 10))
    painter.restore()


def icon(kind, color=TEXT):
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


class Logo(QWidget):
    def __init__(self, pixmap, size=28):
        super().__init__(); self.pixmap = pixmap; self.setFixedSize(size, size)
    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        paint_logo(p, self.pixmap, QRectF(self.rect())); p.end()


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
        close = QPushButton(); close.setIcon(icon("close")); close.setIconSize(QSize(18, 18)); close.setFixedSize(30, 30)
        close.setObjectName("quiet"); close.setAccessibleName(t("Fenster schließen")); close.clicked.connect(self.reject); row.addWidget(close)
        self.layout.addWidget(header)
        header.mousePressEvent = self.start_move
        self._move_origin = None
        header.mouseMoveEvent = self.move_window
        self.finished.connect(lambda _: self.hide())
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
