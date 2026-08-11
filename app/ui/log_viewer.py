from __future__ import annotations

import html
import re

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QPlainTextEdit,
                             QPushButton, QTabWidget, QVBoxLayout, QWidget)

from app.context import AppContext
from app.core.process_mgr import LogBuffer
from app.ui.theme import current_palette
from app.ui.winutil import apply_titlebar_theme

ERROR_RE = re.compile(r"\b(ERR|error|failed|Failed)\b")


class _LogTab(QWidget):
    def __init__(self, buffer: LogBuffer, error_color: str):
        super().__init__()
        self.buffer = buffer
        self.error_color = error_color
        self.seq = 0
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(2000)
        self.follow = QCheckBox("자동 스크롤")
        self.follow.setChecked(True)
        clear_btn = QPushButton("지우기")
        clear_btn.clicked.connect(self._clear)

        bar = QHBoxLayout()
        bar.addWidget(self.follow)
        bar.addStretch(1)
        bar.addWidget(clear_btn)
        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.view, 1)

    def poll(self):
        self.seq, lines = self.buffer.get_since(self.seq)
        for line in lines:
            if ERROR_RE.search(line):
                self.view.appendHtml(
                    f'<span style="color:{self.error_color}">{html.escape(line)}</span>')
            else:
                self.view.appendPlainText(line)
        if lines and self.follow.isChecked():
            sb = self.view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _clear(self):
        self.view.clear()
        self.buffer.clear()
        self.seq = 0


class LogViewer(QDialog):
    def __init__(self, ctx: AppContext, tunnel_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"로그 — {tunnel_name}")
        self.resize(720, 480)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        error_color = current_palette(ctx.store.settings.theme)["danger"]
        tabs = QTabWidget()
        self.t_tab = _LogTab(ctx.manager.tunnel_log(tunnel_name), error_color)
        self.s_tab = _LogTab(ctx.manager.server_log(tunnel_name), error_color)
        tabs.addTab(self.t_tab, "터널 (cloudflared)")
        tabs.addTab(self.s_tab, "웹서버")

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(200)

        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

    def _poll(self):
        self.t_tab.poll()
        self.s_tab.poll()

    def closeEvent(self, event: QCloseEvent):
        self._timer.stop()
        super().closeEvent(event)
