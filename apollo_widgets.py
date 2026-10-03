"""Shared modern selectors. Network work never blocks the Qt event loop."""
import queue
import threading
import time

from PySide6.QtCore import QObject, Signal, Qt, QSize, QEvent, QRect
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import (QHBoxLayout, QVBoxLayout, QWidget, QPushButton,
                              QLineEdit, QListWidget, QListWidgetItem, QStyledItemDelegate,
                              QStyle, QStyleOptionViewItem, QScrollArea, QApplication, QSizePolicy)

from apollo_design import label, icon, TEXT, MUTED
from apollo_models import discover_catalog, discover_price

MODES = {"dictate": "Diktieren", "polish": "Bereinigen", "prompt": "Prompt"}


def register_window(window, app):
    """Hotkeys remain typeable inside Apollo; completed text cannot paste here."""
    if app is None:
        return
    handle = int(window.winId())
    app.ui_windows.add(handle)
    window.finished.connect(lambda _: app.ui_windows.discard(handle))


class KeyCapture(QPushButton):
    def __init__(self, key):
        super().__init__(key.upper())
        self.value = key
        self.capturing = False
        self.setMinimumSize(100, 34)
        self.setAutoDefault(False)
        self.setAccessibleName("Aufnahmetaste ändern")
        self.clicked.connect(self.capture)
    def capture(self):
        self.capturing = True
        self.setText("Taste drücken …")
        self.setFocus()
    def keyPressEvent(self, event):
        if not self.capturing:
            return super().keyPressEvent(event)
        k = event.key()
        if k == Qt.Key.Key_Escape:
            self.capturing = False
            self.setText(self.value.upper())
            return
        if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier):
            self.setText("Eine einzelne Taste")
            return
        names = {Qt.Key.Key_Space: "space", Qt.Key.Key_Tab: "tab",
                 Qt.Key.Key_Backspace: "backspace", Qt.Key.Key_Return: "enter",
                 Qt.Key.Key_Delete: "delete", Qt.Key.Key_Insert: "insert",
                 Qt.Key.Key_Home: "home", Qt.Key.Key_End: "end",
                 Qt.Key.Key_PageUp: "page up", Qt.Key.Key_PageDown: "page down"}
        if Qt.Key.Key_F1 <= k <= Qt.Key.Key_F24:
            value = "f" + str(k - Qt.Key.Key_F1 + 1)
        elif k in names:
            value = names[k]
        else:
            value = event.text().lower()
            if len(value) != 1 or not value.isalnum():
                self.setText("F-Taste / Buchstabe")
                return
        self.value = value
        self.capturing = False
        self.setText(value.upper())
        event.accept()


class Choice(QWidget):
    """Small inline choices; no extra native window or modal event loop."""
    def __init__(self, options, value, pixmap, app=None):
        super().__init__()
        self.options, self.value, self.pixmap, self.app = options, value, pixmap, app
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(4)
        self.button = QPushButton(options.get(value, str(value)))
        self.button.setAutoDefault(False); self.button.setMinimumHeight(34); self.button.setIcon(icon("chevron"))
        layout.addWidget(self.button)
        self.items = QListWidget(); self.items.setFixedHeight(min(len(options)*34+8, 150))
        for key, text in options.items():
            item = QListWidgetItem(text); item.setData(Qt.ItemDataRole.UserRole, key); self.items.addItem(item)
        layout.addWidget(self.items); self.items.hide()
        self.button.clicked.connect(self.choose)
        self.items.itemClicked.connect(self.apply); self.items.itemActivated.connect(self.apply)
        self.items.installEventFilter(self)
    def choose(self):
        self.items.setVisible(self.items.isHidden())
        if self.items.isVisible(): self.items.setFocus()
    def apply(self, item):
        self.value = item.data(Qt.ItemDataRole.UserRole)
        self.button.setText(self.options[self.value]); self.items.hide(); self.button.setFocus()
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.items.hide(); self.button.setFocus(); return True
        return super().eventFilter(watched, event)


class WheelRouter(QObject):
    """Wheel the area under the pointer, even when its fields lack focus.

    Lists and text previews retain their own scroll behavior. The app filter is
    parented to the area and automatically disappears when a page is destroyed.
    """
    def __init__(self, area):
        super().__init__(area); self.area = area
        QApplication.instance().installEventFilter(self)
    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, QWidget): return False
        node = watched
        while node is not None and node is not self.area:
            if isinstance(node, (QListWidget, QScrollArea)) or node.inherits("QTextEdit"): return False
            node = node.parentWidget()
        if node is not self.area: return False
        bar = self.area.verticalScrollBar()
        if bar.maximum() <= 0: return False
        delta = event.pixelDelta().y() or event.angleDelta().y()/120*bar.singleStep()*3
        if not delta: return False
        bar.setValue(bar.value()-round(delta)); event.accept(); return True


class ModelCatalog(QObject):
    changed = Signal()
    result = Signal(int, str, object, str)
    priced = Signal(int, str, str)
    def __init__(self):
        super().__init__()
        self.data = {"transcription": {}, "text": {}}
        self.errors = {}
        self.generation = 0
        self.started = False
        self.started_at = 0
        self.result.connect(self.receive)
        self.priced.connect(self.receive_price)
    def start(self, reload=False):
        if self.started and not reload: return
        if reload and time.monotonic()-self.started_at < 3: return
        self.started = True
        self.started_at = time.monotonic()
        self.generation += 1
        generation = self.generation
        self.errors.clear()
        def fetch(kind):
            try:
                models = discover_catalog(kind)
                self.result.emit(generation, kind, models, "")
                if kind == "transcription":
                    pending = queue.Queue()
                    for key in models: pending.put(key)
                    def prices():
                        while generation == self.generation:
                            try: key = pending.get_nowait()
                            except queue.Empty: return
                            try: price = discover_price(key)
                            except Exception: price = "Preis nicht verfügbar"
                            self.priced.emit(generation, key, price)
                    # Daemon workers let Quit finish immediately even during a network outage.
                    for _ in range(4): threading.Thread(target=prices, daemon=True, name="apollo-prices").start()
            except Exception:
                self.result.emit(generation, kind, {}, "Katalog nicht erreichbar. Bitte erneut laden.")
        for kind in self.data:
            threading.Thread(target=fetch, args=(kind,), daemon=True, name="apollo-catalog").start()
    def receive(self, generation, kind, data, error):
        if generation != self.generation: return
        if data: self.data[kind] = data
        self.errors[kind] = error
        self.changed.emit()
    def receive_price(self, generation, model, price):
        if generation != self.generation: return
        if model in self.data["transcription"]:
            self.data["transcription"][model]["price"] = price
            self.changed.emit()
    def details(self, kind, value):
        if value is None: return {"name": "Kein Fallback", "price": "Bei einem Fehler bleibt die Aufnahme im Recovery-Cache."}
        return self.data[kind].get(value, {"name": value, "price": "Preis nicht verfügbar"})


def compact_price(price):
    """Shorten only presentation; retain explicit, verified billing units."""
    return (price.replace(" Eingabe", " In").replace(" Ausgabe", " Out")
            .replace(" / Mio. Tokens", " / 1M Token").replace("Std. Audio", "h Audio")
            .replace("Sek. Audio", "s Audio").removeprefix("Audio: "))


def paint_model(painter, rect, name, price):
    name_font = painter.font(); price_font = painter.font(); price_font.setPixelSize(12)
    painter.setFont(price_font); metrics = painter.fontMetrics()
    price_width = min(metrics.horizontalAdvance(price)+6, round(rect.width()*.58))
    painter.setPen(QColor(MUTED))
    painter.drawText(QRect(rect.right()-price_width, rect.y(), price_width, rect.height()),
                     Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, metrics.elidedText(price, Qt.TextElideMode.ElideRight, price_width))
    painter.setPen(QColor(TEXT))
    painter.setFont(name_font); metrics = painter.fontMetrics()
    name_width = max(0, rect.width()-price_width-12)
    painter.drawText(QRect(rect.x(), rect.y(), name_width, rect.height()),
                     Qt.AlignmentFlag.AlignVCenter, metrics.elidedText(name, Qt.TextElideMode.ElideRight, name_width))


class ModelRow(QStyledItemDelegate):
    def paint(self, painter, option, index):
        option = QStyleOptionViewItem(option); self.initStyleOption(option, index); option.text = ""
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)
        painter.save(); painter.setFont(option.font)
        name, price = index.data(Qt.ItemDataRole.UserRole+1)
        paint_model(painter, option.rect.adjusted(10, 0, -10, 0), name,
                    compact_price(price) if index.data(Qt.ItemDataRole.UserRole) else "Aus"); painter.restore()


class ModelButton(QPushButton):
    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self); p.setFont(self.font())
        info = self.field.catalog.details(self.field.kind, self.field.value)
        paint_model(p, self.rect().adjusted(10, 0, -30, 0), info["name"], compact_price(info["price"]) if self.field.value else "Aus")
        icon("chevron").paint(p, self.width()-24, (self.height()-16)//2, 16, 16); p.end()


class ModelPicker(QWidget):
    """Embedded search and one-line model rows in the existing form."""
    def __init__(self, field):
        super().__init__(field)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.field = field
        self.selection = field.value
        self.layout = QVBoxLayout(self); self.layout.setContentsMargins(0, 0, 0, 0); self.layout.setSpacing(4)
        self.search = QLineEdit(); self.search.setPlaceholderText("Modelle suchen …"); self.search.setAccessibleName(field.title + " suchen")
        self.layout.addWidget(self.search)
        self.items = QListWidget(); self.items.setStyleSheet("QListWidget::item { padding: 0; margin: 0; border-radius: 6px; }")
        self.items.setItemDelegate(ModelRow(self.items)); self.items.setFixedHeight(166)
        self.layout.addWidget(self.items)
        self.note = label("Eingabe / Ausgabe · USD · Einheit beim Modell", "muted", True)
        self.layout.addWidget(self.note)
        self.items.currentItemChanged.connect(self.selected)
        self.items.itemClicked.connect(lambda _: self.apply()); self.items.itemActivated.connect(lambda _: self.apply())
        self.search.textChanged.connect(self.refresh)
        self.search.returnPressed.connect(self.apply)
        self.search.installEventFilter(self); self.items.installEventFilter(self)
        field.catalog.changed.connect(self.refresh)
        self.refresh()
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.hide(); self.field.button.setFocus(); return True
        if watched is self.search and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Down:
            self.items.setFocus(); self.items.setCurrentRow(0); return True
        return super().eventFilter(watched, event)
    def selected(self, item, previous):
        if item is not None: self.selection = item.data(Qt.ItemDataRole.UserRole)
    def refresh(self):
        scroll = self.items.verticalScrollBar().value()
        self.items.blockSignals(True)
        self.items.clear()
        query = self.search.text().casefold()
        data = dict(self.field.catalog.data[self.field.kind])
        if self.field.allow_none:
            data = {None: self.field.catalog.details(self.field.kind, None), **data}
        for model, info in data.items():
            if query not in (str(model) + " " + info["name"]).casefold(): continue
            item = QListWidgetItem(); item.setData(Qt.ItemDataRole.UserRole, model)
            item.setSizeHint(QSize(0, 32)); item.setText(info["name"])
            item.setData(Qt.ItemDataRole.UserRole+1, (info["name"], info["price"]))
            item.setToolTip(f'{info["name"]}\n{model or "Deaktiviert"}\n{info["price"]}')
            self.items.addItem(item)
            if model == self.selection: self.items.setCurrentItem(item)
        self.items.blockSignals(False)
        self.items.setFixedHeight(max(38, min(self.items.count(), 5)*32+4))
        self.items.verticalScrollBar().setValue(scroll)
        error = self.field.catalog.errors.get(self.field.kind)
        if error: self.note.setText(error)
        elif not data: self.note.setText("Katalog wird geladen …")
        elif not self.items.count(): self.note.setText("Keine passenden Modelle gefunden.")
        else: self.note.setText("Eingabe / Ausgabe · USD · Einheit beim Modell")
    def apply(self):
        item = self.items.currentItem()
        if item is None:
            self.note.setText("Bitte ein Modell auswählen.")
            return
        self.field.value = item.data(Qt.ItemDataRole.UserRole)
        self.field.refresh()
        self.hide(); self.field.button.setFocus()


class ModelField(QWidget):
    def __init__(self, title, kind, value, catalog, pixmap, app=None, allow_none=False):
        super().__init__()
        self.title, self.kind, self.value = title, kind, value
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.catalog, self.pixmap, self.app, self.allow_none = catalog, pixmap, app, allow_none
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(6)
        self.button = ModelButton(); self.button.field = self; self.button.setAutoDefault(False)
        self.button.setFixedHeight(38); self.button.setAccessibleName(title)
        layout.addWidget(self.button); self.picker = None
        self.button.clicked.connect(self.choose)
        catalog.changed.connect(self.refresh)
        self.refresh()
    def refresh(self):
        info = self.catalog.details(self.kind, self.value)
        self.button.setAccessibleName(f'{self.title}: {info["name"]}. {info["price"]}')
        self.button.setToolTip(f'{info["name"]}\n{self.value or "Deaktiviert"}\n{info["price"]}'); self.button.update()
    def choose(self):
        self.catalog.start()
        if self.picker is None:
            self.picker = ModelPicker(self); self.layout().addWidget(self.picker); self.picker.hide()
        opening = self.picker.isHidden()
        for field in self.window().findChildren(ModelField):
            if field.picker: field.picker.hide()
        self.picker.setVisible(opening)
        if opening:
            self.picker.selection = self.value; self.picker.search.clear(); self.picker.refresh(); self.picker.search.setFocus()
            area = self.parentWidget()
            while area and not isinstance(area, QScrollArea): area = area.parentWidget()
            if area: area.ensureWidgetVisible(self.picker)
