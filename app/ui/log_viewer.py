from __future__ import annotations

import html
import re

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QPlainTextEdit,
                             QPushButton, QTabWidget, QVBoxLayout, QWidget)

from app.context import AppContext
from app.core.runner import CommandRunner
from app.ui.theme import current_palette
from app.ui.winutil import apply_titlebar_theme

ERROR_RE = re.compile(r"\b(ERR|error|failed|Failed)\b")

# 파일이 이보다 크면 처음부터 읽지 않고 마지막 이만큼만 읽어 시작한다.
TAIL_START_LIMIT = 1024 * 1024


class _LogTab(QWidget):
    def __init__(self, runner: CommandRunner, path: str, error_color: str):
        super().__init__()
        self.runner = runner
        self.path = path
        self.error_color = error_color
        self.offset = 0
        self._error_shown = False

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(2000)
        self.follow = QCheckBox("자동 스크롤")
        self.follow.setChecked(True)
        clear_btn = QPushButton("화면 지우기")
        clear_btn.setToolTip("화면에 표시된 내용만 지웁니다. 로그 파일은 그대로 유지됩니다.")
        clear_btn.clicked.connect(self._clear)

        bar = QHBoxLayout()
        bar.addWidget(self.follow)
        bar.addStretch(1)
        bar.addWidget(clear_btn)
        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.view, 1)

        # 재시작 후 처음 열 때: 파일이 아주 크면 마지막 TAIL_START_LIMIT
        # 바이트만 읽도록 시작 offset을 조정한다(그래도 setMaximumBlockCount가
        # 화면에는 마지막 2000줄만 남긴다).
        try:
            size = self.runner.file_size(self.path)
        except Exception:
            size = 0
        if size > TAIL_START_LIMIT:
            self.offset = size - TAIL_START_LIMIT

    def poll(self):
        try:
            self.offset, text = self.runner.tail_file(self.path, self.offset)
        except Exception as ex:
            if not self._error_shown:
                self.view.appendHtml(
                    f'<span style="color:{self.error_color}">'
                    f'[로그 읽기 실패] {html.escape(str(ex))}</span>')
                self._error_shown = True
            return
        self._error_shown = False
        if not text:
            return
        lines = text.splitlines()
        for line in lines:
            if ERROR_RE.search(line):
                self.view.appendHtml(
                    f'<span style="color:{self.error_color}">{html.escape(line)}</span>')
            else:
                self.view.appendPlainText(line)
        if self.follow.isChecked():
            sb = self.view.verticalScrollBar()
            sb.setValue(sb.maximum())

    def _clear(self):
        self.view.clear()


class LogViewer(QDialog):
    def __init__(self, ctx: AppContext, title: str, log_paths: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"로그 — {title}")
        self.resize(720, 480)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        error_color = current_palette(ctx.store.settings.theme)["danger"]
        tabs = QTabWidget()
        self._tabs: list[_LogTab] = []
        for name, path in log_paths.items():
            tab = _LogTab(ctx.runner, path, error_color)
            self._tabs.append(tab)
            tabs.addTab(tab, name)

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(200)

        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

    def _poll(self):
        for tab in self._tabs:
            tab.poll()

    def closeEvent(self, event: QCloseEvent):
        self._timer.stop()
        super().closeEvent(event)
