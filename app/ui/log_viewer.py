from __future__ import annotations

import html
import re
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel,
                             QPlainTextEdit, QPushButton, QTabWidget,
                             QVBoxLayout, QWidget)

from app.context import AppContext
from app.core.runner import CommandRunner
from app.ui.poller import BackgroundPoller
from app.ui.theme import current_palette
from app.ui.winutil import apply_titlebar_theme

ERROR_RE = re.compile(r"\b(ERR|error|failed|Failed)\b")

# 파일이 이보다 크면 처음부터 읽지 않고 마지막 이만큼만 읽어 시작한다.
TAIL_START_LIMIT = 1024 * 1024

# 로그 갱신 주기. 원격은 한 번 읽는 데 수십 ms가 걸려 촘촘하게 볼 이유가 없다.
LOCAL_POLL_MS = 250
REMOTE_POLL_MS = 700


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
        # 뷰어가 모든 탭을 생성자에서 만들기 때문에, 여기서 file_size()를
        # 부르면 탭 수만큼 SSH 왕복이 GUI 스레드에서 줄줄이 일어나 창이 굳는다
        # (빌드 중에는 한 번이 초 단위). 첫 읽기 때 워커에서 한 번만 잰다.
        self._seeked = False

    min_interval_ms = 0   # 파일 tail은 싸다 - 뷰어 기본 주기를 그대로 쓴다

    def read_new(self) -> tuple[int, str]:
        """새로 늘어난 부분을 읽는다. 워커 스레드에서 호출된다(위젯 접근 금지)."""
        if not self._seeked:
            self._seeked = True
            try:
                size = self.runner.file_size(self.path)
            except Exception:
                size = 0
            if size > TAIL_START_LIMIT:
                self.offset = size - TAIL_START_LIMIT
        return self.runner.tail_file(self.path, self.offset)

    def apply_new(self, offset: int, text: str) -> None:
        """read_new()의 결과를 화면에 반영한다(GUI 스레드)."""
        self.offset = offset
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

    def show_error(self, message: str) -> None:
        if self._error_shown:
            return
        self.view.appendHtml(
            f'<span style="color:{self.error_color}">'
            f'[로그 읽기 실패] {html.escape(message)}</span>')
        self._error_shown = True

    def _clear(self):
        self.view.clear()


class _ComposeLogTab(QWidget):
    """도커 컨테이너의 로그(`docker compose logs`)를 보여주는 탭.

    왜 별도인가: 우리가 남기는 서비스 로그 파일에는 `docker compose up`의
    출력(빌드·기동 메시지)만 들어간다. 정작 앱이 뱉는 트레이스백은 컨테이너
    안에 있어 파일에는 없다. 사이트가 500을 뱉을 때 필요한 것은 이쪽이다.

    파일이 아니라 명령이라 "이어 읽기"가 없다. 매번 마지막 N줄을 통째로
    가져와 내용이 바뀌었을 때만 다시 그린다(스크롤 위치는 유지).
    """

    TAIL_LINES = 200
    # 파일 tail보다 훨씬 비싸다(도커 CLI 기동 + 로그 스캔). 실측 콜드 2.1초,
    # SD카드 페이지 캐시에서 밀려나면 10초 이상. 뷰어가 이 주기 전에는 다시
    # 부르지 않는다.
    min_interval_ms = 3000
    # compose 출력에서 시각을 뽑는다: "web-1  | 2026-09-20T06:11:15.1Z ..."
    TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T[\d:.]+Z)")

    def __init__(self, runner: CommandRunner, cwd: str, error_color: str):
        super().__init__()
        self.runner = runner
        self.cwd = cwd
        self.error_color = error_color
        self._since = ""

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(2000)
        self.follow = QCheckBox("자동 스크롤")
        self.follow.setChecked(True)
        hint = QLabel(f"docker compose logs --tail {self.TAIL_LINES}")
        hint.setStyleSheet("color: gray; font-size: 11px;")

        bar = QHBoxLayout()
        bar.addWidget(self.follow)
        bar.addStretch(1)
        bar.addWidget(hint)
        lay = QVBoxLayout(self)
        lay.addLayout(bar)
        lay.addWidget(self.view, 1)

    def read_new(self):
        """워커 스레드에서 호출된다(위젯 접근 금지)."""
        cmd = ["docker", "compose", "logs", "--no-color", "-t"]
        if self._since:
            # 첫 읽기 뒤로는 새로 생긴 줄만 받는다. 매번 전체를 긁으면
            # 파이에서 한 번에 몇 초씩 걸린다.
            cmd += ["--since", self._since]
        else:
            cmd += ["--tail", str(self.TAIL_LINES)]
        return self.runner.run(cmd, cwd=self.cwd, timeout=20.0)

    def apply_new(self, result) -> None:
        text = (result.stdout or "") + (result.stderr or "")
        if result.exit_code != 0 and not text.strip():
            self.view.appendPlainText(
                f"(컨테이너 로그를 읽지 못했습니다. 종료 코드 {result.exit_code})")
            return
        newest = self._since
        shown = 0
        for line in text.splitlines():
            m = self.TS_RE.search(line)
            ts = m.group(1) if m else ""
            # --since는 경계 줄을 다시 주기도 한다. 이미 본 시각은 버린다.
            if ts and self._since and ts <= self._since:
                continue
            if ts and ts > newest:
                newest = ts
            self.view.appendPlainText(line)
            shown += 1
        self._since = newest
        if shown and self.follow.isChecked():
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())

    def show_error(self, message: str) -> None:
        self.view.setPlainText(f"[로그 읽기 실패] {message}")


class _LogGroup(QWidget):
    """한 서버의 로그 묶음: 위에는 서버 이름 탭 하나, 안에 하위 탭(앱/빌드).

    도커 서버는 로그가 두 종류(컨테이너 앱 로그, compose 빌드 출력)라서
    최상위에 나란히 두면 서버 2개에 탭이 5개가 된다. 서버당 탭 하나로 묶고
    종류는 그 안에서 고르게 한다.
    """

    def __init__(self, tabs: list[tuple[str, QWidget]]):
        super().__init__()
        self.inner = QTabWidget()
        # 하위 탭임을 시각적으로 구분(문서 스타일 - 상위 탭과 모양이 달라진다)
        self.inner.setDocumentMode(True)
        for name, widget in tabs:
            self.inner.addTab(widget, name)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.addWidget(self.inner)

    def current(self):
        return self.inner.currentWidget()


def _make_tab(ctx, source, error_color):
    """소스 하나 -> 로그 탭 위젯. 파일 경로 또는 ("compose", cwd)."""
    if isinstance(source, tuple) and source[0] == "compose":
        return _ComposeLogTab(ctx.runner, source[1], error_color)
    return _LogTab(ctx.runner, source, error_color)


class LogViewer(QDialog):
    def __init__(self, ctx: AppContext, title: str, log_paths: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"로그 — {title}")
        self.resize(720, 480)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        error_color = current_palette(ctx.store.settings.theme)["danger"]
        tabs = QTabWidget()
        self._tabs: list = []          # 모든 로그 탭(하위 탭 포함, 평평하게)
        for name, source in log_paths.items():
            if isinstance(source, dict):
                # 하위 탭 묶음: {"앱": ..., "빌드": ...}
                children = []
                for sub_name, sub_source in source.items():
                    tab = _make_tab(ctx, sub_source, error_color)
                    self._tabs.append(tab)
                    children.append((sub_name, tab))
                tabs.addTab(_LogGroup(children), name)
            else:
                tab = _make_tab(ctx, source, error_color)
                self._tabs.append(tab)
                tabs.addTab(tab, name)

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)

        self._tab_widget = tabs
        self._last_read: dict[int, float] = {}
        # 로그 읽기도 원격 왕복이라 GUI 스레드에서 하면 창이 멈칫한다.
        # 워커로 돌리고, 결과만 여기서 화면에 붙인다.
        self._poller = BackgroundPoller(self)
        self._poller.finished.connect(self._on_read_done)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        # 예전에는 200ms마다 "모든 탭"을 읽어, 탭이 3개인 SSH 대상에서는
        # 주기보다 읽는 시간이 더 걸렸다(사실상 계속 멈춰 있는 상태).
        # 지금은 보이는 탭 하나만, 대상에 맞는 주기로 읽는다.
        self._timer.start(REMOTE_POLL_MS if ctx.is_remote else LOCAL_POLL_MS)

        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

    def _current_tab(self):
        widget = self._tab_widget.currentWidget()
        if isinstance(widget, _LogGroup):
            widget = widget.current()
        return widget if isinstance(widget, (_LogTab, _ComposeLogTab)) else None

    def _poll(self):
        tab = self._current_tab()
        if tab is None:
            return
        # 탭마다 읽는 비용이 다르다. 컨테이너 로그는 도커 CLI를 띄우는 비용이
        # 커서, 파일 tail과 같은 주기로 부르면 파이가 그 일만 하게 된다.
        wait = getattr(tab, "min_interval_ms", 0)
        if wait:
            last = self._last_read.get(id(tab), 0.0)
            if (time.monotonic() - last) * 1000 < wait:
                return
            self._last_read[id(tab)] = time.monotonic()
        self._pending_tab = tab
        self._poller.run(tab.read_new)

    def _on_read_done(self, ok: bool, result, error: str):
        tab = getattr(self, "_pending_tab", None)
        if tab is None:
            return
        if ok:
            if isinstance(tab, _ComposeLogTab):
                tab.apply_new(result)
            else:
                offset, text = result
                tab.apply_new(*result)
        else:
            tab.show_error(error)

    def closeEvent(self, event: QCloseEvent):
        self._timer.stop()
        self._poller.stop()
        super().closeEvent(event)
