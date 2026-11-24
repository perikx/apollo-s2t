"""Liquid glass for Qt windows, after design/liquid-glass/glass.html.

The glass shows the desktop behind the window: blurred, slightly magnified,
bent at the rim and tinted. Windows excludes the glass windows from screen
capture, so a capture of their area returns the desktop behind them. If the
exclusion fails, the glass falls back to the plain tint.

While a glass window is visible, a capture thread takes the desktop behind it
about 60 times a second, so the glass follows scrolling and video behind it.
When nothing changes and nobody types or moves the mouse, it takes the desktop
only 4 times a second, because each capture costs CPU also on a still desktop.
"""
import ctypes
import sys
import threading
import time

from PySide6.QtCore import Qt, QEvent, QObject, QPoint, QRect, QRectF, QPointF, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen

# Values from the MATERIALS table of the prototype. "lens" is for the orb and
# the droplets. "panel" is for windows and menus with text: they need more
# frost and tint than an icon, or the desktop behind competes with the text.
GLASS = {
    "clear": {"blur": 1, "disp": 16, "bezel": 20, "zoom": .06, "lens": QColor(255, 255, 255, 8),
              "panel_blur": 6, "panel": QColor(250, 250, 248, 160)},
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
# The capture thread takes small glass (orb, menus) 60 times a second. Large glass (the panel
# window) 30 times: its blur hides the difference, and a large capture costs more.
FRAME, LARGE_FRAME, LARGE = 1/60, 1/30, 300_000
MOVED = .4  # seconds after a move in which the live capture reaches REACH instead of MARGIN
# Windows needs about 20 ms for each capture, also when nothing changed. So the full rate lasts
# only STILL seconds after a change behind the glass, a move of the window, or keyboard or mouse
# input. Then the capture thread takes one capture every IDLE_FRAME seconds until one changes.
STILL, IDLE_FRAME = 1., .25


def exclude_from_capture(widget):
    """Hide the window from screen captures. Then a capture shows what is behind it."""
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), 0x11))
    except (AttributeError, OSError):
        return False


def _blur(source, blur):
    """Down and up scaling is a cheap blur of roughly `blur` pixels."""
    factor = max(1., blur/1.5)
    small = source.scaled(max(1, round(source.width()/factor)), max(1, round(source.height()/factor)),
                          Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return small.scaled(source.width(), source.height(), Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", ctypes.c_uint16), ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32)]


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("dwTime", ctypes.c_uint32)]


def _win32():
    """user32 and gdi32 with the argument types for 64-bit handles, or None off Windows."""
    if sys.platform != "win32":
        return None
    user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    handle = ctypes.c_void_p
    user32.GetDC.restype = handle; user32.GetDC.argtypes = (handle,)
    user32.ReleaseDC.argtypes = (handle, handle)
    user32.GetWindowRect.argtypes = (handle, ctypes.POINTER(_RECT))
    user32.GetLastInputInfo.argtypes = (ctypes.POINTER(_LASTINPUTINFO),)
    user32.OpenInputDesktop.restype = handle
    user32.OpenInputDesktop.argtypes = (ctypes.c_uint, ctypes.c_int, ctypes.c_uint)
    user32.OpenDesktopW.restype = handle
    user32.OpenDesktopW.argtypes = (ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_uint)
    user32.SetThreadDesktop.argtypes = (handle,); user32.CloseDesktop.argtypes = (handle,)
    gdi32.CreateCompatibleDC.restype = handle; gdi32.CreateCompatibleDC.argtypes = (handle,)
    gdi32.CreateDIBSection.restype = handle
    gdi32.CreateDIBSection.argtypes = (handle, ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p), handle, ctypes.c_uint32)
    gdi32.SelectObject.restype = handle; gdi32.SelectObject.argtypes = (handle, handle)
    gdi32.DeleteObject.argtypes = (handle,); gdi32.DeleteDC.argtypes = (handle,)
    gdi32.BitBlt.argtypes = (handle, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, handle, ctypes.c_int, ctypes.c_int, ctypes.c_uint32)
    gdi32.GdiFlush.argtypes = ()
    # Load no C runtime here: the exe of 0.5.3 could not load ucrtbase and stopped at start.
    return user32, gdi32


_API = _win32()


def _attach_input_desktop():
    """Point this thread at the visible desktop before the first capture.

    GetDC(None) captures the desktop the calling thread was started on. A launch
    from an IDE or another launcher can start the capture thread on a desktop
    that is not the visible one, and every grab then returns black. Failures are
    ignored: the glass then keeps whatever it captured before.
    """
    if not _API:
        return
    user32 = _API[0]
    desktop = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
    if not desktop:
        desktop = user32.OpenDesktopW("Default", 0, False, 0x01FF)
    if desktop:
        user32.SetThreadDesktop(desktop)
        user32.CloseDesktop(desktop)  # the thread keeps its own reference


def _window_rect(hwnd):
    """The window rectangle in physical pixels, or None."""
    rect = _RECT()
    return (rect.left, rect.top, rect.right, rect.bottom) if _API and _API[0].GetWindowRect(hwnd, ctypes.byref(rect)) else None


def _grab(x, y, width, height, previous=None):
    """Return the screen pixels as BGRA bytes, or None if unchanged or unavailable."""
    user32, gdi32 = _API
    screen = user32.GetDC(None); memory = gdi32.CreateCompatibleDC(screen)
    info = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p(); bitmap = gdi32.CreateDIBSection(screen, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
    try:
        if not bitmap: return None
        old = gdi32.SelectObject(memory, bitmap)
        if not old: return None
        try:
            if not gdi32.BitBlt(memory, 0, 0, width, height, screen, x, y, 0x00CC0020): return None
            if not gdi32.GdiFlush(): return None  # finish the blit before reading the bitmap
            data = ctypes.string_at(bits, width*height*4)
            return None if data == previous else data
        finally:
            gdi32.SelectObject(memory, old)
    finally:
        if bitmap: gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory); user32.ReleaseDC(None, screen)


def _input_age():
    """Seconds since the last keyboard or mouse input, or 0 if Windows does not tell."""
    info = _LASTINPUTINFO(ctypes.sizeof(_LASTINPUTINFO), 0)
    if not _API[0].GetLastInputInfo(ctypes.byref(info)): return 0.
    return ((ctypes.windll.kernel32.GetTickCount()-info.dwTime) & 0xFFFFFFFF)/1000  # the tick count wraps


class _Live(QObject):
    """One capture thread for all visible glass windows. Frames reach the Qt thread as a signal."""
    frame = Signal(object, object)
    def __init__(self):
        super().__init__()
        self.backdrops, self.lock, self.thread = set(), threading.Lock(), None
        self.frame.connect(self.deliver, Qt.ConnectionType.QueuedConnection)
    def deliver(self, backdrop, payload):
        backdrop.receive(payload)
    def add(self, backdrop):
        with self.lock:
            self.backdrops.add(backdrop)
            if self.thread is None:
                self.thread = threading.Thread(target=self.run, daemon=True, name="apollo-glass"); self.thread.start()
    def remove(self, backdrop):
        with self.lock: self.backdrops.discard(backdrop)
    def run(self):
        _attach_input_desktop()
        while True:
            start = time.perf_counter()
            with self.lock: backdrops = list(self.backdrops)
            # Input wakes an idle glass at once: a key or a click often changes the desktop behind it.
            woken = any(backdrop.idle for backdrop in backdrops) and _input_age() < STILL
            for backdrop in backdrops:
                if start < backdrop.due and not (woken and backdrop.idle): continue
                try:
                    payload = backdrop.capture_live()
                except (OSError, ValueError):
                    payload = None
                try:
                    if payload is not None: self.frame.emit(backdrop, payload)
                except RuntimeError:
                    return  # Qt is shutting down
            time.sleep(max(.002, FRAME-(time.perf_counter()-start)) if backdrops else .1)


_live = None


class _Watcher(QObject):
    """Starts the live capture when the window shows and stops it when the window hides."""
    def __init__(self, backdrop):
        super().__init__(backdrop.widget); self.backdrop = backdrop
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show: self.backdrop.start_live()
        elif event.type() in (QEvent.Type.Hide, QEvent.Type.Close): self.backdrop.stop_live()
        return False


class Backdrop:
    """The blurred desktop behind one window."""
    def __init__(self, widget, on_frame=None):
        self.widget, self.on_frame = widget, on_frame
        self.images, self.area, self.taken = {}, None, 0.
        self.excluded = False
        self.hwnd, self.dpr, self.blurs, self.last, self.window_at, self.moved_at = None, 1., set(), None, None, 0.
        self.due = 0.  # the capture thread takes the next frame at this time (time.perf_counter)
        self.changed_at, self.idle = 0., False  # the last change behind the glass; True at the IDLE_FRAME rate
        self.pending = False  # a frame waits for the Qt thread: take no new one, so none pile up
        self.watcher = _Watcher(self); widget.installEventFilter(self.watcher)
    def start_live(self):
        global _live
        if _API is None: return
        if not self.excluded: self.excluded = exclude_from_capture(self.widget)
        if not self.excluded: return  # a capture would show the window itself
        self.hwnd, self.dpr = int(self.widget.winId()), self.widget.devicePixelRatioF()
        if _live is None: _live = _Live()
        _live.add(self)
    def stop_live(self):
        if _live is not None: _live.remove(self)
    def capture_live(self):
        """Capture thread: the desktop behind the window if it changed since the last frame, else None."""
        window = _window_rect(self.hwnd)
        if window is None or self.pending: return None
        now = time.monotonic()
        if window[:2] != self.window_at: self.window_at, self.moved_at = window[:2], now
        reach = round((REACH if now-self.moved_at < MOVED else MARGIN)*self.dpr)
        x, y = window[0]-reach, window[1]-reach
        width, height = window[2]-window[0]+2*reach, window[3]-window[1]+2*reach
        start = time.perf_counter()
        previous = self.last[0] if self.last is not None and self.last[1] == width else None
        data = _grab(x, y, width, height, previous)
        if data is not None: self.changed_at = now
        self.idle = now-max(self.moved_at, self.changed_at) > STILL and _input_age() > STILL
        self.due = start+(IDLE_FRAME if self.idle else LARGE_FRAME if width*height > LARGE else FRAME)-.002
        if data is None: return None
        self.last = (data, width)
        image = QImage(data, width, height, QImage.Format.Format_RGB32).copy()
        if self.dpr != 1:
            image = image.scaled(round(width/self.dpr), round(height/self.dpr), Qt.AspectRatioMode.IgnoreAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        self.pending = True
        return image, {blur: _blur(image, blur) for blur in list(self.blurs)}, window[:2], reach/self.dpr
    def receive(self, payload):
        """Qt thread: show a frame of the capture thread."""
        image, blurs, window_at, reach = payload
        self.pending = False
        try:
            now = _window_rect(self.hwnd)
            if now is None or not self.widget.isVisible(): return
            self.dpr = self.widget.devicePixelRatioF()
            # The window may have moved since the capture: place the capture where it was taken.
            shift = QPoint(round((window_at[0]-now[0])/self.dpr), round((window_at[1]-now[1])/self.dpr))
            corner = self.widget.frameGeometry().topLeft()+shift-QPoint(round(reach), round(reach))
            self.source, self.images, self.area = image, dict(blurs), QRect(corner, image.size())
            self.taken = time.monotonic()
            if self.on_frame: self.on_frame()
            self.widget.update()
        except RuntimeError:
            self.stop_live()  # the window is gone
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
        """The capture, blurred by roughly `blur` pixels. The capture thread prepares each blur in use."""
        if not hasattr(self, "source"):
            return None
        self.blurs.add(blur)
        if blur not in self.images: self.images[blur] = _blur(self.source, blur)
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
    return {"clear": QColor(255, 255, 255, 120), "clear_dark": QColor(20, 20, 22, 110), "light": QColor(250, 250, 248, 222),
            "dark": QColor(28, 28, 30, 222)}.get(material, QColor(250, 250, 248, 222))
