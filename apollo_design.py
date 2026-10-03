"""Shared Qt surfaces, vector icons and DPI-aware typography."""
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QRectF, QPointF, QSize
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPalette, QBrush
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel,
                              QPushButton, QVBoxLayout, QWidget)

GOLD = "#f5b546"
TEXT = "#f5f3ef"
MUTED = "#aaa7a2"
STYLE = """
QWidget { color: #f5f3ef; font-family: 'Segoe UI'; font-size: 16px; }
QFrame#surface { background: #202020; border: 1px solid #42403b; border-radius: 24px; }
QFrame#card { background: #2a2a29; border: 1px solid #383734; border-radius: 16px; }
QLabel { background: transparent; border: none; }
QLabel#title { font-size: 27px; font-weight: 600; }
QLabel#section { font-size: 17px; font-weight: 600; }
QLabel#muted { color: #aaa7a2; font-size: 14px; }
QLabel#error { color: #ffb5a6; font-size: 15px; }
QPushButton { background: #333332; border: 1px solid #494741; border-radius: 12px;
              padding: 12px 16px; font-size: 16px; text-align: left; }
QPushButton:hover { background: #3e3d39; border-color: #71654c; }
QPushButton:pressed { background: #474238; }
QPushButton:focus { border-color: #f5b546; }
QPushButton:disabled { color: #74716b; border-color: #373633; }
QPushButton#primary { background: #f5b546; color: #231b0c; border: none; font-weight: 600; text-align: center; }
QPushButton#primary:hover { background: #ffc96e; }
QPushButton#quiet { background: transparent; border: none; color: #aaa7a2; }
QPushButton#nav { background: transparent; border: none; color: #aaa7a2; padding: 14px; }
QPushButton#nav:checked { background: #39352d; color: #f5c56e; }
QLineEdit, QTextEdit { background: #292928; border: 1px solid #494741; border-radius: 12px;
                       padding: 12px; selection-background-color: #725725; }
QLineEdit:focus, QTextEdit:focus { border-color: #f5b546; }
QListWidget { background: transparent; border: none; outline: none; padding: 3px; }
QListWidget::item { background: #2b2b29; border: 1px solid #393835; border-radius: 13px;
                    margin: 4px 0px; padding: 14px; }
QListWidget::item:selected { background: #3e3628; border-color: #ae8240; }
QListWidget::item:hover { background: #353431; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 6px 0px; }
QScrollBar::handle:vertical { background: #5a5751; min-height: 32px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QCheckBox { spacing: 10px; }
QCheckBox::indicator { width: 22px; height: 22px; border: 1px solid #777065; border-radius: 6px; background: #292928; }
QCheckBox::indicator:checked { background: #f5b546; border-color: #f5b546; }
QToolTip { background: #2b2b29; color: #f5f3ef; border: 1px solid #555049; padding: 8px; }
"""


def qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
        app.setApplicationName("Apollo")
        app.setQuitOnLastWindowClosed(False)
        app.setStyle("Fusion")
        palette = QPalette()
        for role, color in ((QPalette.ColorRole.Window, "#202020"), (QPalette.ColorRole.Base, "#292928"),
                            (QPalette.ColorRole.Text, TEXT), (QPalette.ColorRole.WindowText, TEXT),
                            (QPalette.ColorRole.Button, "#333332"), (QPalette.ColorRole.ButtonText, TEXT),
                            (QPalette.ColorRole.Highlight, "#725725"), (QPalette.ColorRole.HighlightedText, TEXT)):
            palette.setColor(role, QColor(color))
        app.setPalette(palette)
        app.setFont(QFont("Segoe UI", 12))
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
        painter.drawArc(QRectF(4, 4, 16, 16), -55*16, 290*16)
        painter.drawLine(QPointF(3, 3), QPointF(3, 9)); painter.drawLine(QPointF(3, 9), QPointF(9, 9))
        painter.drawLine(QPointF(12, 7), QPointF(12, 12)); painter.drawLine(QPointF(12, 12), QPointF(16, 14))
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
    def __init__(self, pixmap, size=42):
        super().__init__(); self.pixmap = pixmap; self.setFixedSize(size, size)
    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing); p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        paint_logo(p, self.pixmap, QRectF(self.rect())); p.end()


def label(text, name=None, wrap=False):
    result = QLabel(text)
    if name: result.setObjectName(name)
    result.setWordWrap(wrap)
    return result


def button(text, callback, primary=False, quiet=False):
    result = QPushButton(text); result.setCursor(Qt.CursorShape.PointingHandCursor)
    result.setAutoDefault(False)
    result.setMinimumHeight(46)
    if primary: result.setObjectName("primary")
    if quiet: result.setObjectName("quiet")
    result.clicked.connect(callback)
    return result


class Shell(QDialog):
    """Frameless rounded popup with its own draggable title bar and Apollo icon."""
    def __init__(self, title, pixmap, width=840, height=680, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Apollo · " + title); self.setWindowIcon(QIcon(pixmap))
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(width, height); self.setMinimumSize(min(width, 620), min(height, 460))
        outer = QVBoxLayout(self); outer.setContentsMargins(8, 8, 8, 8)
        self.surface = QFrame(); self.surface.setObjectName("surface"); outer.addWidget(self.surface)
        self.layout = QVBoxLayout(self.surface); self.layout.setContentsMargins(24, 20, 24, 24); self.layout.setSpacing(20)
        header = QWidget(); header.setFixedHeight(48); row = QHBoxLayout(header); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(12)
        row.addWidget(Logo(pixmap)); row.addWidget(label("Apollo", "section")); row.addStretch()
        close = QPushButton(); close.setIcon(icon("close")); close.setIconSize(QSize(22, 22)); close.setFixedSize(42, 42)
        close.setObjectName("quiet"); close.setAccessibleName("Fenster schließen"); close.clicked.connect(self.reject); row.addWidget(close)
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
