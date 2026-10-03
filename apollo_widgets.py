"""Shared modern selectors. Network work never blocks the Qt event loop."""
import queue
import threading
import time

from PySide6.QtCore import QObject, Signal, Qt, QSize
from PySide6.QtWidgets import (QHBoxLayout, QVBoxLayout, QWidget, QPushButton,
                              QLineEdit, QListWidget, QListWidgetItem)

from apollo_design import Shell, button, label, icon
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
        self.setMinimumSize(112, 48)
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


class Choice(QPushButton):
    """A readable custom list instead of a platform-native dropdown."""
    def __init__(self, options, value, pixmap, app=None):
        self.options, self.value, self.pixmap, self.app = options, value, pixmap, app
        super().__init__(options.get(value, str(value)))
        self.setMinimumHeight(48)
        self.setAutoDefault(False)
        self.setIcon(icon("chevron"))
        self.clicked.connect(self.choose)
    def choose(self):
        picker = Shell("Auswahl", self.pixmap, 620, 460, self.window())
        register_window(picker, self.app)
        picker.layout.addWidget(label("Auswählen", "title"))
        choices = QListWidget()
        for key, text in self.options.items():
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, key)
            choices.addItem(item)
            if key == self.value: choices.setCurrentItem(item)
        picker.layout.addWidget(choices, 1)
        def apply():
            if choices.currentItem():
                self.value = choices.currentItem().data(Qt.ItemDataRole.UserRole)
                self.setText(self.options[self.value])
                picker.accept()
        picker.layout.addWidget(button("Übernehmen", apply, primary=True))
        picker.exec()


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


class ModelPicker(Shell):
    def __init__(self, field):
        super().__init__(field.title, field.pixmap, 760, 680, field.window())
        self.field = field
        self.selection = field.value
        register_window(self, field.app)
        self.layout.addWidget(label(field.title, "title"))
        self.search = QLineEdit(); self.search.setPlaceholderText("Modelle suchen …"); self.search.setMinimumHeight(50)
        self.layout.addWidget(self.search)
        self.items = QListWidget(); self.items.setStyleSheet("QListWidget::item { padding: 0; }")
        self.layout.addWidget(self.items, 1)
        self.note = label("OpenRouter · USD · Preise werden direkt vom Anbieter geladen", "muted", True)
        self.layout.addWidget(self.note)
        self.layout.addWidget(button("Modell verwenden", self.apply, primary=True))
        self.items.currentItemChanged.connect(self.selected)
        self.search.textChanged.connect(self.refresh)
        field.catalog.changed.connect(self.refresh)
        self.finished.connect(lambda _: field.catalog.changed.disconnect(self.refresh))
        self.refresh()
    def selected(self, item, previous):
        if item is not None: self.selection = item.data(Qt.ItemDataRole.UserRole)
    def refresh(self):
        self.items.blockSignals(True)
        self.items.clear()
        query = self.search.text().casefold()
        data = dict(self.field.catalog.data[self.field.kind])
        if self.field.allow_none:
            data = {None: self.field.catalog.details(self.field.kind, None), **data}
        for model, info in data.items():
            if query not in (str(model) + " " + info["name"]).casefold(): continue
            item = QListWidgetItem(); item.setData(Qt.ItemDataRole.UserRole, model)
            item.setSizeHint(QSize(0, 104))
            self.items.addItem(item)
            row = QWidget(); layout = QVBoxLayout(row); layout.setContentsMargins(16, 10, 16, 10); layout.setSpacing(4)
            name = label(info["name"], "section"); name.setWordWrap(True); layout.addWidget(name)
            layout.addWidget(label(model or "Deaktiviert", "muted"))
            layout.addWidget(label(info["price"], "muted", True))
            row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.items.setItemWidget(item, row)
            if model == self.selection: self.items.setCurrentItem(item)
        self.items.blockSignals(False)
        error = self.field.catalog.errors.get(self.field.kind)
        if error: self.note.setText(error)
        elif not data: self.note.setText("Katalog wird geladen …")
        else: self.note.setText("OpenRouter · USD · Audio- und Tokenpreise zeigen ihre jeweilige Einheit")
    def apply(self):
        item = self.items.currentItem()
        if item is None:
            self.note.setText("Bitte ein Modell auswählen.")
            return
        self.field.value = item.data(Qt.ItemDataRole.UserRole)
        self.field.refresh()
        self.accept()


class ModelField(QPushButton):
    def __init__(self, title, kind, value, catalog, pixmap, app=None, allow_none=False):
        super().__init__()
        self.title, self.kind, self.value = title, kind, value
        self.catalog, self.pixmap, self.app, self.allow_none = catalog, pixmap, app, allow_none
        self.setMinimumHeight(72)
        self.setAutoDefault(False)
        self.setAccessibleName(title)
        row = QHBoxLayout(self); row.setContentsMargins(16, 12, 16, 12); row.setSpacing(12)
        layout = QVBoxLayout(); layout.setSpacing(5); row.addLayout(layout, 1)
        arrow = label(""); arrow.setPixmap(icon("chevron").pixmap(QSize(22, 22)))
        arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents); row.addWidget(arrow)
        self.name_label = label("", "section", True)
        self.price_label = label("", "muted", True)
        for child in (self.name_label, self.price_label):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(child)
        self.clicked.connect(self.choose)
        catalog.changed.connect(self.refresh)
        self.refresh()
    def refresh(self):
        info = self.catalog.details(self.kind, self.value)
        self.name_label.setText(info["name"])
        self.price_label.setText(info["price"])
    def choose(self):
        self.catalog.start()
        picker = ModelPicker(self)
        picker.exec()
