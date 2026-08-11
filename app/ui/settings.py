from __future__ import annotations

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (QComboBox, QDialogButtonBox, QFormLayout, QLineEdit,
                             QVBoxLayout, QDialog)

from app.context import AppContext
from app.ui.winutil import apply_titlebar_theme

THEME_LABELS = {"dark": "다크", "light": "라이트"}
THEME_VALUES = {v: k for k, v in THEME_LABELS.items()}


class SettingsDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("설정")
        self.setMinimumSize(420, 200)

        form = QFormLayout()

        # cloudflared 경로 재정의
        self.cf_path_edit = QLineEdit(ctx.store.settings.cloudflared_path)
        self.cf_path_edit.setPlaceholderText("비우면 PATH에서 cloudflared 탐색")
        form.addRow("cloudflared 경로", self.cf_path_edit)

        # 테마
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(THEME_LABELS.values()))
        current_label = THEME_LABELS.get(ctx.store.settings.theme, "다크")
        self.theme_combo.setCurrentText(current_label)
        form.addRow("테마", self.theme_combo)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("닫기")
        close.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addStretch(1)
        root.addWidget(close)

        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

    def _persist(self):
        self.ctx.store.settings.cloudflared_path = self.cf_path_edit.text().strip()
        self.ctx.store.settings.theme = THEME_VALUES.get(
            self.theme_combo.currentText(), "dark")
        self.ctx.store.save()

    def closeEvent(self, event: QCloseEvent):
        self._persist()
        super().closeEvent(event)

    def reject(self):
        self._persist()
        super().reject()

    def accept(self):
        self._persist()
        super().accept()
