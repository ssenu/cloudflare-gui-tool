from __future__ import annotations

import threading

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QStackedWidget,
                             QVBoxLayout, QWidget)

from app.context import AppContext
from app.core.store import TunnelMeta
from app.core.wizard_logic import (execute_creation, plan_steps, validate_name,
                                   validate_service, validate_subdomain)


class TunnelWizard(QDialog):
    def __init__(self, ctx: AppContext, existing_names: list[str], parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.existing = existing_names
        self.created_meta: TunnelMeta | None = None
        self._events: list[tuple] = []  # 워커 스레드 → UI 폴링 큐
        self.setWindowTitle("터널 생성")
        self.setMinimumSize(520, 380)

        self.stack = QStackedWidget()
        self.err = QLabel()
        self.err.setStyleSheet("color: #f85149;")
        self.preview = QLabel()
        self.preview.setStyleSheet(
            "font-family: Consolas; color: #ffd28e; background: #1f2330;"
            "padding: 8px; border-radius: 6px;")
        self.preview.setWordWrap(True)

        self.back_btn = QPushButton("← 이전")
        self.next_btn = QPushButton("다음 →")
        self.next_btn.setObjectName("primary")
        self.back_btn.clicked.connect(self._back)
        self.next_btn.clicked.connect(self._next)

        lay = QVBoxLayout(self)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.preview)
        lay.addWidget(self.err)
        nav = QHBoxLayout()
        nav.addWidget(self.back_btn)
        nav.addStretch(1)
        nav.addWidget(self.next_btn)
        lay.addLayout(nav)

        self._build_pages()
        self._go(0)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._drain_events)
        self._timer.start(150)

    # ---- 페이지 구성 ----
    def _page(self, title: str, *widgets) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        t = QLabel(title)
        t.setStyleSheet("font-size: 16px; font-weight: 700;")
        v.addWidget(t)
        for x in widgets:
            v.addWidget(x)
        v.addStretch(1)
        return w

    def _build_pages(self):
        s = self.ctx.store.settings
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("예: mysite")
        self.sub_edit = QLineEdit()
        self.sub_edit.setPlaceholderText("예: mysite")
        self.domain_edit = QLineEdit(s.root_domain)
        self.domain_edit.setPlaceholderText("예: example.com")
        self.service_edit = QLineEdit("http://localhost:8000")
        self.cmd_edit = QLineEdit()
        self.cmd_edit.setPlaceholderText("예: uvicorn main:app --port 8000 (선택)")
        self.cwd_edit = QLineEdit()
        self.cwd_edit.setPlaceholderText("작업 폴더 (선택)")
        browse = QPushButton("폴더 선택...")
        browse.clicked.connect(lambda: self.cwd_edit.setText(
            QFileDialog.getExistingDirectory(self, "작업 폴더") or self.cwd_edit.text()))
        self.together_chk = QCheckBox("터널을 켤 때 서버도 함께 시작")

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)

        self.stack.addWidget(self._page("① 터널 이름",
                                        QLabel("터널을 구분할 이름입니다."), self.name_edit))
        self.stack.addWidget(self._page("② 연결할 도메인",
                                        QLabel("서브도메인"), self.sub_edit,
                                        QLabel("루트 도메인"), self.domain_edit))
        self.stack.addWidget(self._page("③ 로컬 서비스 주소",
                                        QLabel("터널이 전달할 로컬 서비스입니다."),
                                        self.service_edit))
        self.stack.addWidget(self._page("④ 웹서버 명령 (선택)",
                                        self.cmd_edit, self.cwd_edit, browse,
                                        self.together_chk))
        self.stack.addWidget(self._page("⑤ 실행", self.status_label))

        for e in (self.name_edit, self.sub_edit, self.domain_edit,
                  self.service_edit):
            e.textChanged.connect(self._update_preview)

    # ---- 네비게이션/검증 ----
    def _hostname(self) -> str:
        return f"{self.sub_edit.text().strip()}.{self.domain_edit.text().strip()}"

    def _validate_current(self) -> str:
        i = self.stack.currentIndex()
        if i == 0:
            return validate_name(self.name_edit.text().strip(), self.existing)
        if i == 1:
            e = validate_subdomain(self.sub_edit.text().strip())
            if not e and not self.domain_edit.text().strip():
                e = "루트 도메인을 입력하세요"
            return e
        if i == 2:
            return validate_service(self.service_edit.text().strip())
        return ""

    def _update_preview(self):
        i = self.stack.currentIndex()
        steps = plan_steps(self.name_edit.text().strip() or "<이름>",
                           self._hostname(), self.service_edit.text().strip())
        if i in (0, 1, 2):
            self.preview.setText("실행될 명령: " + steps[i].preview)
            self.preview.show()
        else:
            self.preview.hide()

    def _go(self, i: int):
        self.stack.setCurrentIndex(i)
        self.back_btn.setVisible(0 < i < 4)
        self.next_btn.setText("생성 시작" if i == 3 else "다음 →")
        self.err.clear()
        self._update_preview()

    def _back(self):
        self._go(self.stack.currentIndex() - 1)

    def _next(self):
        e = self._validate_current()
        if e:
            self.err.setText(e)
            return
        i = self.stack.currentIndex()
        if i < 3:
            self._go(i + 1)
        elif i == 3:
            self._go(4)
            self._start_creation()

    # ---- 실행 ----
    def _start_creation(self):
        self.next_btn.setEnabled(False)
        self.back_btn.hide()
        name = self.name_edit.text().strip()
        hostname = self._hostname()
        service = self.service_edit.text().strip()
        client = self.ctx.client
        lines: list[str] = []

        def progress(idx, msg, ok):
            self._events.append(("log", f"{'✅' if ok else '❌'} {msg}"))

        def work():
            try:
                execute_creation(client, name, hostname, service, progress)
                self._events.append(("done", name, hostname, service))
            except Exception as ex:  # CloudflaredError 포함
                self._events.append(("fail", str(ex)))

        self._log_lines = lines
        threading.Thread(target=work, daemon=True).start()

    def _drain_events(self):
        while self._events:
            ev = self._events.pop(0)
            if ev[0] == "log":
                self.status_label.setText(
                    (self.status_label.text() + "\n" + ev[1]).strip())
            elif ev[0] == "done":
                _, name, hostname, service = ev
                self.created_meta = TunnelMeta(
                    name=name, hostname=hostname, service=service,
                    server_cmd=self.cmd_edit.text().strip(),
                    server_cwd=self.cwd_edit.text().strip(),
                    start_together=self.together_chk.isChecked())
                # 루트 도메인 기억
                self.ctx.store.settings.root_domain = self.domain_edit.text().strip()
                self.next_btn.setText("완료")
                self.next_btn.setEnabled(True)
                self.next_btn.clicked.disconnect()
                self.next_btn.clicked.connect(self.accept)
            elif ev[0] == "fail":
                self.status_label.setText(
                    self.status_label.text() + f"\n❌ 실패: {ev[1]}")
                self.next_btn.setText("닫기")
                self.next_btn.setEnabled(True)
                self.next_btn.clicked.disconnect()
                self.next_btn.clicked.connect(self.reject)
