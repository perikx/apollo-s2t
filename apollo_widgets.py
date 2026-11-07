"""Shared modern selectors. Network work never blocks the Qt event loop."""
import threading
import time

from PySide6.QtCore import QObject, Signal, Qt, QSize, QEvent, QRect, QPoint
from PySide6.QtGui import QPainter, QColor, QFont
from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QWidget, QPushButton, QButtonGroup, QLineEdit, QListWidget,
                              QListWidgetItem, QStyledItemDelegate, QStyle, QStyleOptionViewItem,
                              QSizePolicy)

from apollo_design import label, icon, theme, glyph_button, GlassMenu
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
    """Pick one option. Options show side by side; a `menu` choice opens a menu instead."""
    changed = Signal(str)
    def __init__(self, options, value, translate=True, menu=False):
        super().__init__()
        self.options, self.value, self.translate = options, value, translate
        layout = QHBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(4)
        self.buttons, self.button = {}, None
        if menu:
            # The options can change (new profiles), so the menu is built when it opens.
            self.button = glyph_button(QPushButton(self.text(value)), "chevron"); self.button.setObjectName("picker")
            self.button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)  # the chevron sits at the right end
            self.button.setAutoDefault(False); self.button.setMinimumHeight(34)
            self.button.clicked.connect(self.choose); layout.addWidget(self.button)
            self.menu = GlassMenu(self)
            return
        self.setObjectName("segments"); self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.group = QButtonGroup(self)
        for key in options:
            segment = QPushButton(self.text(key)); segment.setObjectName("segment")
            segment.setCheckable(True); segment.setChecked(key == value); segment.setAutoDefault(False)
            segment.setMinimumHeight(30); segment.setCursor(Qt.CursorShape.PointingHandCursor)
            segment.clicked.connect(lambda checked=False, key=key: self.pick(key))
            self.group.addButton(segment); layout.addWidget(segment, 1); self.buttons[key] = segment
    def text(self, key):
        text = self.options.get(key, str(key))
        return t(text) if self.translate else text
    def choose(self):
        self.menu.clear()
        for key in self.options:
            if key != self.value:
                self.menu.addAction(self.text(key)).triggered.connect(lambda checked=False, key=key: self.pick(key))
        self.menu.popup(self.button.mapToGlobal(QPoint(0, self.button.height()+4)))
    def pick(self, key):
        if key != self.value:
            self.set_value(key); self.changed.emit(key)
    def set_value(self, value):
        self.value = value
        if self.button is not None: self.button.setText(self.text(value))
        for key, segment in self.buttons.items(): segment.setChecked(key == value)


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


def paint_model(painter, rect, name, price, text_color=None, muted_color=None):
    name_font = painter.font(); price_font = painter.font(); price_font.setPixelSize(12)
    painter.setFont(price_font); metrics = painter.fontMetrics()
    price_width = min(metrics.horizontalAdvance(price)+6, round(rect.width()*.58))
    painter.setPen(QColor(muted_color or theme()["muted"]))
    painter.drawText(QRect(rect.right()-price_width, rect.y(), price_width, rect.height()),
                     Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, metrics.elidedText(price, Qt.TextElideMode.ElideRight, price_width))
    painter.setPen(QColor(text_color or theme()["text"]))
    painter.setFont(name_font); metrics = painter.fontMetrics()
    name_width = max(0, rect.width()-price_width-12)
    painter.drawText(QRect(rect.x(), rect.y(), name_width, rect.height()),
                     Qt.AlignmentFlag.AlignVCenter, metrics.elidedText(name, Qt.TextElideMode.ElideRight, name_width))


class ModelRow(QStyledItemDelegate):
    def paint(self, painter, option, index):
        if index.data(MORE):
            painter.save(); painter.setPen(QColor(theme()["muted"])); painter.setFont(option.font)
            painter.drawText(option.rect.adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, index.data())
            painter.restore(); return
        option = QStyleOptionViewItem(option); self.initStyleOption(option, index); option.text = ""
        option.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, option, painter, option.widget)
        painter.save(); painter.setFont(option.font)
        name, price, note = index.data(INFO)
        rect = option.rect.adjusted(10, 0, -10, 0)
        if note:
            paint_model(painter, QRect(rect.x(), rect.y()+2, rect.width(), 22), name, price)
            small = QFont(option.font); small.setPixelSize(12); painter.setFont(small); painter.setPen(QColor(theme()["muted"]))
            painter.drawText(QRect(rect.x(), rect.y()+24, rect.width(), 16), Qt.AlignmentFlag.AlignVCenter,
                             painter.fontMetrics().elidedText(note, Qt.TextElideMode.ElideRight, rect.width()))
        else: paint_model(painter, rect, name, price)
        painter.restore()


class ModelButton(QPushButton):
    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self); p.setFont(self.font())
        info = self.field.catalog.details(self.field.kind, self.field.value)
        paint_model(p, self.rect().adjusted(10, 0, -30, 0), info["name"], price_text(info["price"]) if self.field.value else t("Off"))
        icon("chevron").paint(p, self.width()-24, (self.height()-16)//2, 16, 16); p.end()


class ModelPicker(QWidget):
    """Embedded search and one ranked list; unranked models sit behind "Show all models".

    The list takes the free height of the page, so it is the only part that scrolls."""
    def __init__(self, field):
        super().__init__(field)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.field = field
        self.selection = field.value
        self.show_all = False
        self.stats = {}
        self.layout = QVBoxLayout(self); self.layout.setContentsMargins(0, 0, 0, 0); self.layout.setSpacing(4)
        self.search = QLineEdit(); self.search.setPlaceholderText(t("Search models …")); self.search.setAccessibleName(f"{field.title}: {t('Search models …')}")
        self.layout.addWidget(self.search)
        self.items = QListWidget(); self.items.setStyleSheet("QListWidget::item { padding: 0; margin: 0; }")
        self.items.setItemDelegate(ModelRow(self.items)); self.items.setMinimumHeight(88)
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
            self.field.close_picker(); return True
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
        self.field.close_picker()


class ModelField(QWidget):
    def __init__(self, title, kind, value, catalog, allow_none=False):
        super().__init__()
        self.title, self.kind, self.value = t(title), kind, value
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.catalog, self.allow_none = catalog, allow_none
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(8)
        layout.addWidget(label(title, "section"))
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
        if opening: self.picker.open()
        self.fit()
    def close_picker(self):
        self.picker.hide(); self.fit(); self.button.setFocus()
    def fit(self):
        """The field with an open picker takes the free height of the page; else it stays below."""
        page = self.parentWidget().layout() if self.parentWidget() else None
        if page is None: return
        for i in range(page.count()):
            item = page.itemAt(i); widget = item.widget()
            if isinstance(widget, ModelField):
                open_ = bool(widget.picker and not widget.picker.isHidden())
                widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding if open_ else QSizePolicy.Policy.Maximum)
                page.setStretch(i, int(open_))
            elif item.spacerItem():
                page.setStretch(i, 0 if any(f.picker and not f.picker.isHidden() for f in self.window().findChildren(ModelField)) else 1)
