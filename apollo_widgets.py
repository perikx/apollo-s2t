"""Shared modern selectors. Network work never blocks the Qt event loop."""
import queue
import threading
import time

from PySide6.QtCore import QObject, Signal, Qt, QSize, QEvent, QRect
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import (QHBoxLayout, QVBoxLayout, QWidget, QPushButton,
                              QLineEdit, QListWidget, QListWidgetItem, QStyledItemDelegate,
                              QStyle, QStyleOptionViewItem, QScrollArea, QAbstractScrollArea, QApplication, QSizePolicy)

from apollo_design import label, icon, TEXT, MUTED
from apollo_models import discover_catalog, discover_price, RECOMMENDATIONS
from apollo_i18n import t

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
        self.setAccessibleName(t("Aufnahmetaste ändern"))
        self.clicked.connect(self.capture)
    def capture(self):
        self.capturing = True
        self.setText(t("Taste drücken …"))
        self.setFocus()
    def keyPressEvent(self, event):
        if not self.capturing:
            return super().keyPressEvent(event)
        k = event.key()
        if k == Qt.Key.Key_Escape:
            self.capturing = False
            self.setText(t(self.value.upper()))
            return
        if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier):
            self.setText(t("Eine einzelne Taste"))
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
                self.setText(t("F-Taste / Buchstabe"))
                return
        self.value = value
        self.capturing = False
        self.setText(t(value.upper()))
        event.accept()


class Choice(QWidget):
    """Small inline choices; no extra native window or modal event loop."""
    changed = Signal(str)
    def __init__(self, options, value, pixmap, app=None, translate=True):
        super().__init__()
        self.options, self.value, self.pixmap, self.app = options, value, pixmap, app
        self.translate = translate
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(4)
        self.button = QPushButton(t(options.get(value, str(value))) if translate else options.get(value, str(value)))
        self.button.setAutoDefault(False); self.button.setMinimumHeight(34); self.button.setIcon(icon("chevron", "#fafafa"))
        layout.addWidget(self.button)
        self.items = QListWidget()
        self.refresh_options()
        layout.addWidget(self.items); self.items.hide()
        self.button.clicked.connect(self.choose)
        self.items.itemClicked.connect(self.apply); self.items.itemActivated.connect(self.apply)
        self.items.installEventFilter(self)
    def choose(self):
        self.refresh_options()
        self.items.setVisible(self.items.isHidden())
        if self.items.isVisible(): self.items.setFocus()
    def refresh_options(self):
        self.items.clear()
        for key, text in self.options.items():
            if key == self.value: continue
            item = QListWidgetItem(t(text) if self.translate else text)
            item.setData(Qt.ItemDataRole.UserRole, key); self.items.addItem(item)
        self.items.setFixedHeight(min(self.items.count()*34+8, 150))
        self.button.setEnabled(bool(self.items.count()))
    def apply(self, item):
        self.value = item.data(Qt.ItemDataRole.UserRole)
        self.button.setText(t(self.options[self.value]) if self.translate else self.options[self.value]); self.items.hide(); self.button.setFocus()
        self.changed.emit(self.value)
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.items.hide(); self.button.setFocus(); return True
        return super().eventFilter(watched, event)


class WheelRouter(QObject):
    """Wheel the area under the pointer, even when its fields lack focus.

    Consume nested-list/preview wheel events even at their limits, so Qt cannot
    propagate them to the surrounding page. The filter dies with the page.
    """
    def __init__(self, area):
        super().__init__(area); self.area = area
        QApplication.instance().installEventFilter(self)
    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, QWidget): return False
        node = watched; target = None
        while node is not None and node is not self.area:
            if target is None and isinstance(node, QAbstractScrollArea): target = node
            node = node.parentWidget()
        if node is not self.area: return False
        target = target or self.area
        horizontal = not (event.pixelDelta().y() or event.angleDelta().y())
        bar = target.horizontalScrollBar() if horizontal else target.verticalScrollBar()
        delta = (event.pixelDelta().x() if horizontal else event.pixelDelta().y()) or (event.angleDelta().x() if horizontal else event.angleDelta().y())/120*bar.singleStep()*3
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
        if value is None: return {"name": t("Kein Fallback"), "price": t("Bei einem Fehler bleibt die Aufnahme im Recovery-Cache.")}
        return self.data[kind].get(value, {"name": value, "price": "Preis nicht verfügbar"})


def compact_price(price):
    """Shorten only presentation; retain explicit, verified billing units."""
    price = t(price)
    return (price.replace(" Eingabe", " In").replace(" Ausgabe", " Out")
            .replace(" / Mio. Tokens", " / 1M Token").replace("Std. Audio", "h Audio")
            .replace("Sek. Audio", "s Audio").removeprefix("Audio: "))


def paint_model(painter, rect, name, price, text_color=TEXT, muted_color=MUTED):
    name_font = painter.font(); price_font = painter.font(); price_font.setPixelSize(12)
    painter.setFont(price_font); metrics = painter.fontMetrics()
    price_width = min(metrics.horizontalAdvance(price)+6, round(rect.width()*.58))
    painter.setPen(QColor(muted_color))
    painter.drawText(QRect(rect.right()-price_width, rect.y(), price_width, rect.height()),
                     Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, metrics.elidedText(price, Qt.TextElideMode.ElideRight, price_width))
    painter.setPen(QColor(text_color))
    painter.setFont(name_font); metrics = painter.fontMetrics()
    name_width = max(0, rect.width()-price_width-12)
    painter.drawText(QRect(rect.x(), rect.y(), name_width, rect.height()),
                     Qt.AlignmentFlag.AlignVCenter, metrics.elidedText(name, Qt.TextElideMode.ElideRight, name_width))


class ModelRow(QStyledItemDelegate):
    def paint(self, painter, option, index):
        if index.data(Qt.ItemDataRole.UserRole+2):
            painter.save(); painter.setPen(QColor(MUTED)); painter.setFont(option.font)
            painter.drawText(option.rect.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, index.data())
            painter.restore(); return
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
        paint_model(p, self.rect().adjusted(10, 0, -30, 0), info["name"], compact_price(info["price"]) if self.field.value else "Aus", "#fafafa", "#d6d6d3")
        icon("chevron", "#fafafa").paint(p, self.width()-24, (self.height()-16)//2, 16, 16); p.end()


class ModelPicker(QWidget):
    """Embedded search and one-line model rows in the existing form."""
    def __init__(self, field):
        super().__init__(field)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.field = field
        self.selection = field.value
        self.layout = QVBoxLayout(self); self.layout.setContentsMargins(0, 0, 0, 0); self.layout.setSpacing(4)
        self.search = QLineEdit(); self.search.setPlaceholderText(t("Modelle suchen …")); self.search.setAccessibleName(t(field.title + " suchen"))
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
            self.items.setFocus()
            for row in range(self.items.count()):
                if self.items.item(row).flags() & Qt.ItemFlag.ItemIsSelectable:
                    self.items.setCurrentRow(row); break
            return True
        return super().eventFilter(watched, event)
    def selected(self, item, previous):
        if item is not None and item.flags() & Qt.ItemFlag.ItemIsSelectable: self.selection = item.data(Qt.ItemDataRole.UserRole)
    def refresh(self):
        scroll = self.items.verticalScrollBar().value()
        self.items.blockSignals(True)
        self.items.clear()
        query = self.search.text().casefold()
        data = dict(self.field.catalog.data[self.field.kind])
        available = bool(data)
        recommendations = RECOMMENDATIONS[self.field.kind]
        recommended = {key: data.pop(key) for key in recommendations if key in data}
        groups = [("Empfohlen für Apollo", recommended), ("Weitere Modelle", data)]
        if self.field.allow_none:
            groups.insert(0, ("", {None: self.field.catalog.details(self.field.kind, None)}))
        for heading, models in groups:
            matches = [(model, info) for model, info in models.items() if query in (str(model) + " " + info["name"]).casefold()]
            if heading and matches and not query:
                header = QListWidgetItem(t(heading)); header.setFlags(Qt.ItemFlag.NoItemFlags)
                header.setData(Qt.ItemDataRole.UserRole+2, True); header.setSizeHint(QSize(0, 26)); self.items.addItem(header)
            for model, info in matches:
                item = QListWidgetItem(); item.setData(Qt.ItemDataRole.UserRole, model)
                item.setSizeHint(QSize(0, 32)); item.setText(info["name"])
                item.setData(Qt.ItemDataRole.UserRole+1, (info["name"], info["price"]))
                reason = recommendations.get(model, "")
                item.setToolTip(f'{info["name"]}\n{model or t("Deaktiviert")}\n{compact_price(info["price"])}' + (f'\n{t("Empfohlen")}: {t(reason)}' if reason else ""))
                self.items.addItem(item)
                if model == self.selection: self.items.setCurrentItem(item)
        self.items.blockSignals(False)
        self.items.setFixedHeight(max(38, min(self.items.count(), 5)*32+4))
        self.items.verticalScrollBar().setValue(scroll)
        error = self.field.catalog.errors.get(self.field.kind)
        if error: self.note.setText(t(error))
        elif not available: self.note.setText(t("Katalog wird geladen …"))
        elif not self.items.count(): self.note.setText(t("Keine passenden Modelle gefunden."))
        else: self.note.setText(t("{count} Modelle · USD").format(count=len(self.field.catalog.data[self.field.kind])))
    def apply(self):
        item = self.items.currentItem()
        if item is None or not item.flags() & Qt.ItemFlag.ItemIsSelectable:
            self.note.setText(t("Bitte ein Modell auswählen."))
            return
        self.field.value = item.data(Qt.ItemDataRole.UserRole)
        self.field.refresh()
        self.hide(); self.field.button.setFocus()


class ModelField(QWidget):
    def __init__(self, title, kind, value, catalog, pixmap, app=None, allow_none=False):
        super().__init__()
        self.title, self.kind, self.value = t(title), kind, value
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.catalog, self.pixmap, self.app, self.allow_none = catalog, pixmap, app, allow_none
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(6)
        self.button = ModelButton(); self.button.field = self; self.button.setAutoDefault(False)
        self.button.setFixedHeight(38); self.button.setAccessibleName(t(title))
        layout.addWidget(self.button); self.picker = None
        self.button.clicked.connect(self.choose)
        catalog.changed.connect(self.refresh)
        self.refresh()
    def refresh(self):
        info = self.catalog.details(self.kind, self.value)
        self.button.setAccessibleName(t(f'{self.title}: {info["name"]}. {info["price"]}'))
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
