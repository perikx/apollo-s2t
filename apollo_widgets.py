"""Shared modern selectors. Network work never blocks the Qt event loop."""
import threading
import time

from PySide6.QtCore import QObject, Signal, Qt, QSize, QEvent, QRect
from PySide6.QtGui import QPainter, QColor, QFont
from PySide6.QtWidgets import (QVBoxLayout, QWidget, QPushButton, QLineEdit, QListWidget,
                              QListWidgetItem, QStyledItemDelegate, QStyle, QStyleOptionViewItem,
                              QScrollArea, QSizePolicy)

from apollo_design import label, icon, TEXT, MUTED
from apollo_models import discover_catalog, audio_price, curated, load_stats, model_note, price_text, STT
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
    def __init__(self, options, value, translate=True):
        super().__init__()
        self.options, self.value = options, value
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


INFO, MORE = Qt.ItemDataRole.UserRole + 1, Qt.ItemDataRole.UserRole + 2
_stats_path = None


def set_stats_path(path):
    """apollo.py calls this once; pickers show the per-model stats stored there."""
    global _stats_path
    _stats_path = path


class ModelCatalog(QObject):
    changed = Signal()
    result = Signal(int, str, object)
    def __init__(self):
        super().__init__()
        self.data = {"transcription": {}, "text": {}}
        self.errors = {}
        self.generation = 0
        self.started = False
        self.started_at = 0
        self.result.connect(self.receive)
    def start(self, reload=False):
        if self.started and not reload: return
        if reload and time.monotonic()-self.started_at < 3: return
        self.started = True
        self.started_at = time.monotonic()
        self.generation += 1
        generation = self.generation
        def fetch(kind):
            try: models = discover_catalog(kind)
            except Exception: models = {}
            self.result.emit(generation, kind, models)
        # Daemon workers let Quit finish immediately even during a network outage.
        for kind in self.data:
            threading.Thread(target=fetch, args=(kind,), daemon=True, name="apollo-catalog").start()
    def receive(self, generation, kind, data):
        if generation != self.generation: return
        if data: self.data[kind] = data
        self.errors[kind] = not data
        self.changed.emit()
    def details(self, kind, value):
        if value is None: return {"name": t("No fallback"), "price": None}
        return self.data[kind].get(value) or {"name": value, "price": audio_price(value) if kind == "transcription" else None}


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
        if index.data(MORE):
            painter.save(); painter.setPen(QColor(MUTED)); painter.setFont(option.font)
            painter.drawText(option.rect.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, index.data())
            painter.restore(); return
        option = QStyleOptionViewItem(option); self.initStyleOption(option, index); option.text = ""
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)
        painter.save(); painter.setFont(option.font)
        name, price, note = index.data(INFO)
        rect = option.rect.adjusted(10, 0, -10, 0)
        if note:
            paint_model(painter, QRect(rect.x(), rect.y()+2, rect.width(), 22), name, price)
            small = QFont(option.font); small.setPixelSize(12); painter.setFont(small); painter.setPen(QColor(MUTED))
            painter.drawText(QRect(rect.x(), rect.y()+24, rect.width(), 16), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(note, Qt.TextElideMode.ElideRight, rect.width()))
        else: paint_model(painter, rect, name, price)
        painter.restore()


class ModelButton(QPushButton):
    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self); p.setFont(self.font())
        info = self.field.catalog.details(self.field.kind, self.field.value)
        paint_model(p, self.rect().adjusted(10, 0, -30, 0), info["name"], price_text(info["price"]) if self.field.value else t("Off"), "#fafafa", "#d6d6d3")
        icon("chevron", "#fafafa").paint(p, self.width()-24, (self.height()-16)//2, 16, 16); p.end()


class ModelPicker(QWidget):
    """Embedded search and one ranked list; unranked models sit behind "Show all models"."""
    def __init__(self, field):
        super().__init__(field)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.field = field
        self.selection = field.value
        self.show_all = False
        self.stats = {}
        self.layout = QVBoxLayout(self); self.layout.setContentsMargins(0, 0, 0, 0); self.layout.setSpacing(4)
        self.search = QLineEdit(); self.search.setPlaceholderText(t("Search models …")); self.search.setAccessibleName(f"{field.title}: {t('Search models …')}")
        self.layout.addWidget(self.search)
        self.items = QListWidget(); self.items.setStyleSheet("QListWidget::item { padding: 0; margin: 0; border-radius: 6px; }")
        self.items.setItemDelegate(ModelRow(self.items)); self.items.setFixedHeight(166)
        self.layout.addWidget(self.items)
        self.note = label("", "muted", True)
        self.layout.addWidget(self.note)
        self.items.currentItemChanged.connect(self.selected)
        self.items.itemClicked.connect(lambda _: self.apply()); self.items.itemActivated.connect(lambda _: self.apply())
        self.search.textChanged.connect(self.refresh)
        self.search.returnPressed.connect(self.apply)
        self.search.installEventFilter(self); self.items.installEventFilter(self)
        field.catalog.changed.connect(self.refresh)
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.hide(); self.field.button.setFocus(); return True
        if watched is self.search and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Down:
            self.items.setFocus(); self.items.setCurrentRow(0)
            return True
        return super().eventFilter(watched, event)
    def open(self):
        self.selection = self.field.value
        # A current model outside the ranking must be visible.
        self.show_all = bool(self.selection) and self.selection not in curated(self.field.kind)
        self.stats = load_stats(_stats_path) if _stats_path else {}
        self.search.clear(); self.refresh(); self.search.setFocus()
    def selected(self, item, previous):
        if item is not None and not item.data(MORE): self.selection = item.data(Qt.ItemDataRole.UserRole)
    def add(self, model, info, ranked):
        note = model_note(model, ranked, self.stats.get(model))
        price = price_text(info["price"]) if model else t("Off")
        item = QListWidgetItem(info["name"]); item.setData(Qt.ItemDataRole.UserRole, model)
        item.setData(INFO, (info["name"], price, note)); item.setSizeHint(QSize(0, 44 if note else 32))
        tip = [info["name"], model or t("Off"), price, note]
        if model in STT: tip.append(t("English WER {wer}% · {speed}x real time").format(wer=STT[model][1], speed=STT[model][2]))
        item.setToolTip("\n".join(filter(None, tip)))
        self.items.addItem(item)
        if model == self.selection: self.items.setCurrentItem(item)
    def refresh(self):
        if self.isHidden(): return
        scroll = self.items.verticalScrollBar().value()
        self.items.blockSignals(True)
        self.items.clear()
        query = self.search.text().casefold()
        catalog, kind = self.field.catalog, self.field.kind
        data, ranked = catalog.data[kind], curated(kind)
        def found(model, info): return query in ((model or "") + " " + info["name"]).casefold()
        top = [(model, data[model]) for model in ranked if model in data]
        rest = [(model, info) for model, info in data.items() if model not in ranked]
        if self.field.allow_none and found(None, catalog.details(kind, None)): self.add(None, catalog.details(kind, None), ranked)
        for model, info in top:
            if found(model, info): self.add(model, info, ranked)
        if not query and rest:
            more = QListWidgetItem(t("Hide other models") if self.show_all else t("Show all models ({count})").format(count=len(rest)))
            more.setData(MORE, True); more.setSizeHint(QSize(0, 32)); self.items.addItem(more)
        for model, info in rest:
            if (self.show_all or query) and found(model, info): self.add(model, info, ranked)
        self.items.blockSignals(False)
        height = sum(self.items.item(i).sizeHint().height() for i in range(min(self.items.count(), 5)))
        self.items.setFixedHeight(max(38, height+4))
        self.items.verticalScrollBar().setValue(scroll)
        if catalog.errors.get(kind): self.note.setText(t("Catalog unavailable. Please reload."))
        elif not data: self.note.setText(t("Loading catalog …"))
        elif not self.items.count(): self.note.setText(t("No matching models found."))
        else: self.note.setText(t("{count} models · USD").format(count=len(data)))
    def apply(self):
        item = self.items.currentItem()
        if item is None:
            self.note.setText(t("Choose a model."))
            return
        if item.data(MORE):
            self.show_all = not self.show_all; self.refresh(); return
        self.field.value = item.data(Qt.ItemDataRole.UserRole)
        self.field.refresh()
        self.hide(); self.field.button.setFocus()


class ModelField(QWidget):
    def __init__(self, title, kind, value, catalog, allow_none=False):
        super().__init__()
        self.title, self.kind, self.value = t(title), kind, value
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.catalog, self.allow_none = catalog, allow_none
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(6)
        self.button = ModelButton(); self.button.field = self; self.button.setAutoDefault(False)
        self.button.setFixedHeight(38); self.button.setAccessibleName(t(title))
        layout.addWidget(self.button); self.picker = None
        self.button.clicked.connect(self.choose)
        catalog.changed.connect(self.refresh)
        self.refresh()
    def refresh(self):
        info = self.catalog.details(self.kind, self.value)
        price = price_text(info["price"])
        self.button.setAccessibleName(f'{self.title}: {info["name"]}. {price}')
        self.button.setToolTip(f'{info["name"]}\n{self.value or t("Off")}\n{price}'); self.button.update()
    def choose(self):
        self.catalog.start()
        if self.picker is None:
            self.picker = ModelPicker(self); self.layout().addWidget(self.picker); self.picker.hide()
        opening = self.picker.isHidden()
        for field in self.window().findChildren(ModelField):
            if field.picker: field.picker.hide()
        self.picker.setVisible(opening)
        if opening:
            self.picker.open()
            area = self.parentWidget()
            while area and not isinstance(area, QScrollArea): area = area.parentWidget()
            if area: area.ensureWidgetVisible(self.picker)
