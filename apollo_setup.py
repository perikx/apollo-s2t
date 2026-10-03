"""Complete first-launch wizard. Errors remain editable in the same window."""
from copy import deepcopy
import os
from pathlib import Path
import threading
import requests
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLineEdit, QHBoxLayout, QWidget, QVBoxLayout, QCheckBox, QScrollArea
from apollo_config import api_key, normalize_config, save_config, ConfigError
from apollo_design import Shell, qt_app, logo_path, logo_pixmap, label, button
from apollo_widgets import KeyCapture, Choice, ModelCatalog, ModelField, MODES


def check_key(key):
    """Read-only authentication check; no model inference, audio or billing."""
    try:
        response = requests.get("https://openrouter.ai/api/v1/key", headers={"Authorization": "Bearer " + key}, timeout=(5, 15))
        try:
            if response.status_code in (401, 403):
                return "Der API-Schlüssel wurde abgelehnt. Bitte korrigieren und erneut versuchen."
            response.raise_for_status()
            data = response.json()
            if not isinstance(data.get("data"), dict): return "Schlüssel konnte nicht geprüft werden. Bitte erneut versuchen."
            return ""
        finally: response.close()
    except (requests.RequestException, ValueError):
        return "OpenRouter ist gerade nicht erreichbar. Bitte erneut versuchen."


class SetupWizard(Shell):
    checked = Signal(str)
    def __init__(self, cfg, path, set_autostart, initial_error=""):
        super().__init__("Einrichten", logo_pixmap(logo_path()), 800, 740)
        self.cfg = deepcopy(cfg); self.path = Path(path); self.set_autostart = set_autostart
        self.catalog = ModelCatalog(); self.step = 0; self.checking = False
        self.verified_key = None; self.pixmap = logo_pixmap(logo_path())
        self.step_label = label("", "muted"); self.layout.addWidget(self.step_label)
        self.heading = label("", "title"); self.layout.addWidget(self.heading)
        self.body = QWidget(); self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0); self.body_layout.setSpacing(16)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(self.body)
        self.layout.addWidget(scroll, 1)
        self.error = label(initial_error, "error", True); self.layout.addWidget(self.error)
        row = QHBoxLayout(); self.back = button("Zurück", self.previous, quiet=True)
        self.next = button("Weiter", self.advance, primary=True)
        row.addWidget(self.back); row.addStretch(); row.addWidget(self.next); self.layout.addLayout(row)
        self.checked.connect(self.key_checked)
        self.render()
        if initial_error: self.error.setText(initial_error)
    def render(self):
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            if item.widget(): item.widget().hide(); item.widget().deleteLater()
        self.step_label.setText(f"Einrichten · Schritt {self.step+1} von 3")
        self.back.setVisible(self.step > 0); self.next.setText("Apollo starten" if self.step == 2 else "Weiter")
        self.error.setText("")
        if self.step == 0:
            self.heading.setText("Willkommen bei Apollo")
            self.body_layout.addWidget(label("Ein OpenRouter-Schlüssel verbindet Spracherkennung und Textbearbeitung.", wrap=True))
            self.body_layout.addWidget(label("API-Nutzung wird über dein OpenRouter-Guthaben abgerechnet.\nSchlüssel erstellen: openrouter.ai/keys", "muted", True))
            self.body_layout.addWidget(label("OpenRouter API-Schlüssel", "section"))
            self.key = QLineEdit(); self.key.setEchoMode(QLineEdit.EchoMode.Password)
            self.key.setMinimumHeight(50); self.key.setPlaceholderText("API-Schlüssel eingeben …")
            self.key.setText(self.cfg["smoothing"]["api_key"])
            self.key.setEnabled(not bool(os.environ.get("OPENROUTER_API_KEY")))
            self.body_layout.addWidget(self.key)
            self.body_layout.addWidget(label("Der Schlüssel wird ohne Modellaufruf geprüft.\nAufnahmen bleiben bei Fehlern im begrenzten Recovery-Cache.", "muted", True))
        elif self.step == 1:
            self.heading.setText("Deine Aufnahmetasten")
            self.body_layout.addWidget(label("Taste anklicken und deine gewünschte Taste drücken.", "muted", True))
            self.keys = {}
            for mode, text in MODES.items():
                container = QWidget(); row = QHBoxLayout(container); row.setContentsMargins(0, 0, 0, 0)
                row.addWidget(label(text)); row.addStretch()
                self.keys[mode] = KeyCapture(self.cfg["hotkeys"][mode]); row.addWidget(self.keys[mode])
                self.body_layout.addWidget(container)
            self.behavior = Choice({"toggle": "Antippen zum Starten / Stoppen", "hold": "Gedrückt halten"}, self.cfg["hotkey_mode"], self.pixmap)
            self.body_layout.addWidget(self.behavior)
            self.body_layout.addWidget(label("Sprache der Aufnahme", "section"))
            self.language = QLineEdit(self.cfg["openrouter_stt"]["language"] or "auto")
            self.language.setPlaceholderText("auto / de / en …"); self.body_layout.addWidget(self.language)
        else:
            self.heading.setText("Wähle deine Modelle")
            self.fields = {}
            for key, title, kind, value, allow_none in (
                ("primary", "Haupttranskription", "transcription", self.cfg["openrouter_stt"]["model"], False),
                ("fallback", "Fallback bei Rate Limit (429)", "transcription", self.cfg["openrouter_stt"].get("fallback_model"), True),
                ("text", "Bereinigen & Prompt", "text", self.cfg["smoothing"]["model"], False)):
                self.body_layout.addWidget(label(title, "section"))
                field = ModelField(title, kind, value, self.catalog, self.pixmap, allow_none=allow_none)
                self.fields[key] = field; self.body_layout.addWidget(field)
            self.autostart = QCheckBox("Apollo bei Windows-Anmeldung starten")
            self.body_layout.addWidget(self.autostart)
            self.body_layout.addWidget(label("Preise in USD · kompatible Modelle von OpenRouter\nDas schwebende Logo lässt sich verschieben und über den Tray wieder öffnen.", "muted", True))
            self.catalog.start()
        self.body_layout.addStretch()
    def previous(self):
        if self.checking: return
        if self.step == 2: self.save_model_choices()
        self.step = max(0, self.step-1); self.render()
    def save_model_choices(self):
        self.cfg["openrouter_stt"].update(model=self.fields["primary"].value, fallback_model=self.fields["fallback"].value)
        self.cfg["smoothing"]["model"] = self.fields["text"].value
    def advance(self):
        if self.checking: return
        if self.step == 0:
            self.cfg["smoothing"]["api_key"] = self.key.text().strip()
            value = api_key(self.cfg)
            if not value:
                self.error.setText("Bitte einen OpenRouter API-Schlüssel eingeben."); self.key.setFocus(); return
            if value != self.verified_key:
                self.checking = True; self.next.setEnabled(False); self.key.setEnabled(False)
                self.error.setText("Schlüssel wird geprüft …")
                threading.Thread(target=lambda: self.checked.emit(check_key(value)), daemon=True, name="apollo-key-check").start()
                return
        elif self.step == 1:
            candidate = deepcopy(self.cfg)
            candidate["hotkeys"] = {k:v.value for k,v in self.keys.items()}
            candidate["hotkey_mode"] = self.behavior.value
            candidate["openrouter_stt"]["language"] = self.language.text().strip()
            try:
                candidate, _ = normalize_config(candidate)
                import keyboard
                all_codes = set()
                for key in candidate["hotkeys"].values():
                    codes = set(keyboard.key_to_scan_codes(key))
                    if not codes or all_codes & codes: raise ConfigError("Bitte drei verschiedene gültige Tasten wählen.")
                    all_codes.update(codes)
            except (ValueError, KeyError) as exc: self.error.setText(str(exc)); return
            self.cfg = candidate
        else:
            try:
                # Choices come from capability-filtered lists; unchanged custom IDs survive an outage.
                self.save_model_choices()
                self.cfg, _ = normalize_config(self.cfg)
                if self.path.exists():
                    backup = self.path.with_name(self.path.name+".bak")
                    if not backup.exists(): backup.write_bytes(self.path.read_bytes())
                save_config(self.path, self.cfg)
                self.set_autostart(self.autostart.isChecked())
            except (OSError, ValueError):
                self.error.setText("Einrichten konnte nicht gespeichert werden. Bitte Eingaben prüfen und erneut versuchen."); return
            self.accept(); return
        self.step += 1; self.render()
    def key_checked(self, error):
        if not self.isVisible(): return
        self.checking = False; self.next.setEnabled(True)
        self.key.setEnabled(not bool(os.environ.get("OPENROUTER_API_KEY")))
        if error:
            self.error.setText(error); self.key.setFocus(); return
        self.verified_key = api_key(self.cfg)
        self.step = 1; self.render()


def run_windowed_setup(cfg, path, set_autostart, initial_error=""):
    qt_app()
    wizard = SetupWizard(cfg, path, set_autostart, initial_error)
    screen = qt_app().primaryScreen().availableGeometry()
    wizard.resize(min(800, screen.width()-32), min(740, screen.height()-32))
    wizard.move(screen.center()-wizard.rect().center())
    return wizard.exec() == wizard.DialogCode.Accepted
