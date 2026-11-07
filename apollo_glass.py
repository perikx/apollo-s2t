"""Liquid glass for Qt windows, after design/liquid-glass/glass.html.

The glass shows the desktop behind the window: blurred, slightly magnified,
bent at the rim and tinted. Windows excludes the glass windows from screen
capture, so a capture of their area returns the desktop behind them. If the
exclusion fails, the glass falls back to the plain tint.
"""
import ctypes
import sys
import time

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen

# Values from the MATERIALS table of the prototype. "lens" is for the orb and
# the droplets. "panel" is for windows and menus with text: they need more
# frost and tint than an icon, or the desktop behind competes with the text.
GLASS = {
    "clear": {"blur": 1, "disp": 16, "bezel": 20, "zoom": .06, "lens": QColor(255, 255, 255, 8),
              "panel_blur": 6, "panel": QColor(250, 250, 248, 96)},
    "light": {"blur": 8, "disp": 14, "bezel": 22, "zoom": .05, "lens": QColor(255, 255, 255, 77),
              "panel_blur": 16, "panel": QColor(248, 248, 246, 178)},
    "dark": {"blur": 8, "disp": 14, "bezel": 22, "zoom": .05, "lens": QColor(0, 0, 0, 64),
             "panel_blur": 16, "panel": QColor(24, 24, 26, 178)},
}
# Clear windows and menus over a dark desktop: a dark tint behind their light text.
GLASS["clear_dark"] = dict(GLASS["clear"], panel=QColor(20, 20, 22, 110))
# Light ink and dark ink have equal contrast at a luminance of about .21. The gap between
# the two limits stops a flicker when the luminance behind is near that point.
LIGHT_INK_BELOW, DARK_INK_ABOVE = .17, .25
MARGIN = 40  # the bent rim shows pixels up to this far past the window
REACH = 200  # the capture reaches this far, so a drag inside it needs no new capture
SLICES = 8


def exclude_from_capture(widget):
    """Hide the window from screen captures. Then a capture shows what is behind it."""
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), 0x11))
    except (AttributeError, OSError):
        return False


class Backdrop:
    """The blurred desktop behind one window."""
    def __init__(self, widget):
        self.widget = widget
        self.images, self.area, self.taken = {}, None, 0.
        self.excluded = False
    @property
    def origin(self):
        """Where the capture sits in the window, from the window position of this moment.

        A moved window paints the right part of the capture at once, without a new capture."""
        return QPointF(self.widget.mapFromGlobal(self.area.topLeft())) if self.area else QPointF()
    def refresh(self, max_age):
        """Capture again when the last capture is older than max_age seconds, or when the
        window left the captured area. True if the picture changed."""
        need = self.widget.frameGeometry().adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN)
        if self.area is not None and self.area.contains(need) and time.monotonic()-self.taken < max_age:
            return False
        if not self.excluded:
            self.excluded = exclude_from_capture(self.widget)
            if not self.excluded and self.widget.isVisible():
                return False  # a capture would show the window itself
        screen = self.widget.screen()
        if screen is None:
            return False
        frame = self.widget.frameGeometry().adjusted(-REACH, -REACH, REACH, REACH)
        geometry = screen.geometry()
        image = screen.grabWindow(0, frame.x()-geometry.x(), frame.y()-geometry.y(), frame.width(), frame.height()).toImage()
        if image.isNull():
            return False
        image.setDevicePixelRatio(1)
        image = image.scaled(frame.width(), frame.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        self.taken = time.monotonic()
        if getattr(self, "source", None) == image and frame == self.area:
            return False  # nothing changed behind the window: no repaint
        self.source, self.images, self.area = image, {}, frame
        return True
    def lightness(self, rect, tint=None):
        """Mean relative luminance (0 to 1) of the desktop behind `rect` under the glass `tint`,
        or None without a capture."""
        if not hasattr(self, "source"):
            return None
        area = QRectF(rect).translated(-self.origin).toAlignedRect().intersected(self.source.rect())
        if area.isEmpty():
            return None
        small = self.source.copy(area).scaled(6, 6, Qt.AspectRatioMode.IgnoreAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation)
        def linear(v):
            return v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4
        tint = tint or QColor(0, 0, 0, 0); a = tint.alphaF()
        def mix(back, front):
            return back*(1-a)+front*a
        total = 0.
        for y in range(6):
            for x in range(6):
                c = small.pixelColor(x, y)
                total += (.2126*linear(mix(c.redF(), tint.redF()))+.7152*linear(mix(c.greenF(), tint.greenF()))
                          +.0722*linear(mix(c.blueF(), tint.blueF())))
        return total/36
    def image(self, blur):
        """The capture, blurred by roughly `blur` pixels (down and up scaling is a cheap blur)."""
        if not hasattr(self, "source"):
            return None
        if blur not in self.images:
            source = self.source; factor = max(1., blur/1.5)
            small = source.scaled(max(1, round(source.width()/factor)), max(1, round(source.height()/factor)),
                                  Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.images[blur] = small.scaled(source.width(), source.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
        return self.images[blur]


def dark_behind(level, was_dark):
    """True if text on the glass needs the light ink. None (no capture) keeps the last answer."""
    if level is None:
        return was_dark
    return level < (DARK_INK_ABOVE if was_dark else LIGHT_INK_BELOW)


def shape(rect, radius, inset=0.):
    path = QPainterPath()
    r = rect.adjusted(inset, inset, -inset, -inset)
    if r.width() > 0 and r.height() > 0:
        path.addRoundedRect(r, max(0., radius-inset), max(0., radius-inset))
    return path


def _profile(t):
    """Convex squircle bezel: the rim bends the light most, the inside not at all."""
    return 1-(1-(1-t)**4)**.25


def paint_glass(painter, rect, radius, backdrop, material, panel=False):
    """Paint one glass shape: refraction of the backdrop, tint and rim light."""
    glass = GLASS.get(material, GLASS["light"])
    rect = QRectF(rect); outline = shape(rect, radius)
    image = backdrop.image(glass["panel_blur"] if panel else glass["blur"]) if backdrop else None
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    if image is not None:
        center = rect.center(); half_w, half_h = rect.width()/2, rect.height()/2
        bezel = min(glass["bezel"], half_w, half_h)
        def draw(clip, bend):
            # Sample farther out at the rim and slightly inwards everywhere: a lens.
            sx = 1-glass["zoom"]+bend/half_w; sy = 1-glass["zoom"]+bend/half_h
            painter.save(); painter.setClipPath(clip)
            painter.translate(center); painter.scale(1/sx, 1/sy); painter.translate(-center)
            painter.drawImage(backdrop.origin, image); painter.restore()
        # Outer slices first; each smaller one covers the middle of the last, so no seams show.
        for i in range(SLICES):
            draw(shape(rect, radius, bezel*i/SLICES), glass["disp"]*_profile((i+.5)/SLICES))
        draw(shape(rect, radius, bezel), 0.)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush((glass["panel"] if panel else glass["lens"]) if image is not None else _fallback(material))
    painter.drawPath(outline)
    # Rim light from the top left, a weaker edge opposite, as in the prototype.
    light = QLinearGradient(rect.topLeft(), rect.bottomRight())
    light.setColorAt(0, QColor(255, 255, 255, 245)); light.setColorAt(.5, QColor(255, 255, 255, 90))
    light.setColorAt(1, QColor(255, 255, 255, 170))
    painter.setBrush(Qt.BrushStyle.NoBrush); painter.setPen(QPen(light, 1.4))
    painter.drawPath(shape(rect, radius, .7))
    bevel = QLinearGradient(rect.topLeft(), rect.center())  # soft inner bevel facing the light
    bevel.setColorAt(0, QColor(255, 255, 255, 70)); bevel.setColorAt(1, QColor(255, 255, 255, 0))
    painter.setPen(QPen(bevel, 3)); painter.drawPath(shape(rect, radius, 2.4))
    painter.restore()


def _fallback(material):
    """Tint without a backdrop: dense enough to read, still lighter than an opaque window."""
    return {"clear": QColor(255, 255, 255, 60), "clear_dark": QColor(20, 20, 22, 110), "light": QColor(250, 250, 248, 222),
            "dark": QColor(28, 28, 30, 222)}.get(material, QColor(250, 250, 248, 222))
