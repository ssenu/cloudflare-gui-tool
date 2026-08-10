from __future__ import annotations

import html
import re

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QPlainTextEdit,
                             QPushButton, QTabWidget, QVBoxLayout, QWidget)

from app.context import AppContext
from app.core.process_mgr import LogBuffer

ERROR_RE = re.compile(r"\b(ERR|error|failed|Failed)\b")


class _LogTab(QWidget):
    def __init__(self, buffer: LogBuffer):
        super().__init__()
        self.buffer = buffer
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
                    f'<span style="color:#f85149">{html.escape(line)}</span>')
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

        tabs = QTabWidget()
        self.t_tab = _LogTab(ctx.manager.tunnel_log(tunnel_name))
        self.s_tab = _LogTab(ctx.manager.server_log(tunnel_name))
        tabs.addTab(self.t_tab, "터널 (cloudflared)")
        tabs.addTab(self.s_tab, "웹서버")

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(200)

    def _poll(self):
        self.t_tab.poll()
        self.s_tab.poll()
