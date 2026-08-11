from __future__ import annotations

import threading

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog,
                             QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QStackedWidget, QVBoxLayout, QWidget)

from app.context import AppContext
from app.core.cloudflared import DnsRecordExistsError
from app.core.store import RouteMeta, ServiceSpec, TunnelMeta, new_route_id
from app.core.wizard_logic import (execute_creation, finish_creation, plan_steps,
                                   validate_name, validate_service, validate_subdomain)
from app.ui.icons import make_icon
from app.ui.route_dialog import (DOCKER_START_DEFAULT, DOCKER_STOP_DEFAULT,
                                 KIND_LABELS)
from app.ui.theme import current_palette
from app.ui.winutil import apply_titlebar_theme


class TunnelWizard(QDialog):
    def __init__(self, ctx: AppContext, existing_names: list[str], parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.existing = existing_names
        self.created_meta: TunnelMeta | None = None
        self._events: list[tuple] = []  # 워커 스레드 → UI 폴링 큐
        self._next_mode = "nav"  # "nav" | "close" | "done"
        self._tunnel_created = False  # 터널 생성 성공 여부 추적
        self._current_tunnel_name = ""  # 롤백/재개용 터널 이름
        self._created: dict = {}  # execute_creation이 채우는 tunnel_id/credentials (재개용)
        self._last_error_is_dns_conflict = False
        self._close_countdown = 5  # 성공 시 자동 닫기 카운트다운(초)
        self.setWindowTitle("터널 생성")
        self.setMinimumSize(600, 580)

        palette = current_palette(ctx.store.settings.theme)
        self.stack = QStackedWidget()
        self.err = QLabel()
        self.err.setStyleSheet(f"color: {palette['danger']};")
        self.preview = QLabel()
        self.preview.setStyleSheet(
            f"font-family: Consolas; color: {palette['preview_text']};"
            f"background: {palette['panel2']}; padding: 8px; border-radius: 6px;")
        self.preview.setWordWrap(True)

        icon_color = palette["text"]
        self.back_btn = QPushButton("← 이전")
        self.next_btn = QPushButton("다음 →")
        self.next_btn.setObjectName("primary")
        self.rollback_btn = QPushButton("터널 롤백(삭제)")
        self.rollback_btn.setIcon(make_icon("trash", icon_color))
        self.rollback_btn.setVisible(False)
        self.overwrite_btn = QPushButton("기존 DNS 레코드 덮어쓰기")
        self.overwrite_btn.setVisible(False)
        self.overwrite_hint = self._hint(
            "이미 있는 레코드를 이 터널로 바꿔 연결합니다.")
        self.overwrite_hint.setVisible(False)
        self.back_btn.clicked.connect(self._back)
        self.next_btn.clicked.connect(self._on_next_clicked)  # 상태 기반 디스패처
        self.rollback_btn.clicked.connect(self._start_rollback)
        self.overwrite_btn.clicked.connect(self._start_overwrite)

        lay = QVBoxLayout(self)
        lay.addWidget(self.stack, 1)
        lay.addWidget(self.preview)
        lay.addWidget(self.err)
        lay.addWidget(self.overwrite_hint)
        nav = QHBoxLayout()
        nav.addWidget(self.back_btn)
        nav.addStretch(1)
        nav.addWidget(self.overwrite_btn)
        nav.addWidget(self.rollback_btn)
        nav.addWidget(self.next_btn)
        lay.addLayout(nav)

        self._close_timer = QTimer(self)
        self._close_timer.timeout.connect(self._on_close_tick)

        self._build_pages()
        self._go(0)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._drain_events)
        self._timer.start(150)

        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

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

    def _hint(self, text: str) -> QLabel:
        palette = current_palette(self.ctx.store.settings.theme)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {palette['muted']};")
        return lbl

    def _build_pages(self):
        s = self.ctx.store.settings
        palette = current_palette(self.ctx.store.settings.theme)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("예: mysite")
        self.sub_edit = QLineEdit()
        self.sub_edit.setPlaceholderText("예: mysite")
        self.domain_edit = QLineEdit(s.root_domain)
        self.domain_edit.setPlaceholderText("예: example.com")
        self.service_edit = QLineEdit("http://localhost:8000")

        self.kind_combo = QComboBox()
        for value, label in KIND_LABELS:
            self.kind_combo.addItem(label, value)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)

        self.cmd_edit = QLineEdit()
        self.cmd_edit.setPlaceholderText("예: uvicorn main:app --port 8000 (선택)")
        self.stop_cmd_edit = QLineEdit()
        self.stop_cmd_edit.setPlaceholderText("비우면 프로세스를 강제 종료합니다 (선택)")
        self.cwd_edit = QLineEdit()
        self.cwd_edit.setPlaceholderText("작업 폴더 (선택, 도커는 필수)")
        browse = QPushButton("폴더 선택...")
        browse.clicked.connect(lambda: self.cwd_edit.setText(
            QFileDialog.getExistingDirectory(self, "작업 폴더") or self.cwd_edit.text()))
        self.together_chk = QCheckBox("터널을 켤 때 서버도 함께 시작")

        self.hostname_result = QLabel()
        self.hostname_result.setStyleSheet(
            f"color: {palette['accent']}; font-weight: 700;")

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)

        self.stack.addWidget(self._page(
            "1. 터널 이름",
            self._hint("이 터널을 구분하기 위한 이름입니다. Cloudflare 계정 안에서만 "
                       "사용되며 실제 접속 주소와는 관계가 없습니다."),
            self._hint("영문, 숫자, 하이픈(-), 밑줄(_)만 사용할 수 있습니다. 설정 파일 "
                       "이름(config-<이름>.yml)에도 그대로 쓰입니다."),
            self._hint("예: mysite, blog, home-api"),
            self.name_edit))
        self.stack.addWidget(self._page(
            "2. 연결할 도메인",
            self._hint("방문자가 브라우저에 입력하게 될 주소입니다. 두 칸을 합친 "
                       "주소로 Cloudflare에 CNAME 레코드가 자동 생성됩니다."),
            self._hint("루트 도메인은 Cloudflare에 등록되어 상태가 Active여야 합니다."),
            QLabel("서브도메인"), self.sub_edit,
            QLabel("루트 도메인"), self.domain_edit,
            self.hostname_result))
        self.stack.addWidget(self._page(
            "3. 로컬 서비스 주소",
            self._hint("터널이 트래픽을 전달할 내 컴퓨터의 주소입니다. 웹서버가 실제로 "
                       "듣고 있는 포트를 적어야 합니다."),
            self._hint("로컬 구간은 http로 충분합니다. 외부 접속의 HTTPS는 Cloudflare가 "
                       "자동으로 처리합니다."),
            self._hint("예: http://localhost:8000 (uvicorn), "
                       "http://localhost:5173 (Vite), http://localhost:3000 (Next.js)"),
            self.service_edit))
        self.stack.addWidget(self._page(
            "4. 웹서버 실행 명령 (선택)",
            self._hint("터널과 함께 켤 웹서버 명령을 등록해 두면 카드에서 한 번에 실행할 "
                       "수 있습니다. 비워 두면 서버는 직접 실행해야 합니다."),
            self._hint("작업 폴더는 명령을 실행할 위치입니다. 보통 프로젝트 폴더를 "
                       "지정합니다."),
            self._hint("명령 예: uvicorn main:app --port 8000, npm run dev"),
            self._hint("도커 컴포즈를 고르면 시작/정지 명령 기본값이 채워지고, "
                       "작업 폴더(compose 파일 위치)가 필수가 됩니다."),
            QLabel("서비스 종류"), self.kind_combo,
            QLabel("시작 명령"), self.cmd_edit,
            QLabel("정지 명령"), self.stop_cmd_edit,
            QLabel("작업 폴더"), self.cwd_edit, browse,
            self.together_chk))
        self.stack.addWidget(self._page(
            "5. 실행",
            self._hint("터널 생성 → DNS 연결 → 설정 파일 작성 순서로 자동 진행됩니다. "
                       "중간에 실패하면 그 단계에서 멈추며, 이미 만들어진 터널은 롤백 "
                       "버튼으로 삭제할 수 있습니다."),
            self.status_label))

        for e in (self.name_edit, self.sub_edit, self.domain_edit,
                  self.service_edit):
            e.textChanged.connect(self._update_preview)

    # ---- 종류 변경 ----
    def _on_kind_changed(self):
        if self.kind_combo.currentData() == "docker":
            if not self.cmd_edit.text().strip():
                self.cmd_edit.setText(DOCKER_START_DEFAULT)
            if not self.stop_cmd_edit.text().strip():
                self.stop_cmd_edit.setText(DOCKER_STOP_DEFAULT)

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
        if i == 3:
            if self.kind_combo.currentData() == "docker" \
                    and not self.cwd_edit.text().strip():
                return "도커 컴포즈는 작업 폴더(compose 파일 위치)가 필요합니다"
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

        sub = self.sub_edit.text().strip()
        domain = self.domain_edit.text().strip()
        if sub and domain:
            self.hostname_result.setText(f"접속 주소: {sub}.{domain}")
        else:
            self.hostname_result.setText("접속 주소: (서브도메인과 루트 도메인을 입력하세요)")

    def _go(self, i: int):
        self.stack.setCurrentIndex(i)
        self.back_btn.setVisible(0 < i < 4)
        self.next_btn.setText("생성 시작" if i == 3 else "다음 →")
        self.err.clear()
        self._update_preview()

        # 페이지 0-3으로 이동하면 상태 초기화 (재시도 경로)
        if i < 4:
            self._next_mode = "nav"
            self.next_btn.setEnabled(True)
            self.rollback_btn.setVisible(False)
            self.overwrite_btn.setVisible(False)
            self.overwrite_hint.setVisible(False)
            self.status_label.setText("")
            self._tunnel_created = False
            self._close_timer.stop()

    def _back(self):
        i = self.stack.currentIndex()
        # 실행 페이지(4)에서 뒤로가면 페이지 3으로 돌아가고, 생성 시작 버튼 준비
        if i == 4:
            self._go(3)
        else:
            self._go(i - 1)

    def _on_next_clicked(self):
        """상태 기반 next_btn 디스패처 (signal rewiring 제거)"""
        if self._next_mode == "nav":
            self._next()
        elif self._next_mode == "close":
            self.reject()
        elif self._next_mode == "done":
            self.accept()

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
        self.rollback_btn.setVisible(False)
        self.overwrite_btn.setVisible(False)
        self.overwrite_hint.setVisible(False)
        self._tunnel_created = False
        self._created = {}
        name = self.name_edit.text().strip()
        hostname = self._hostname()
        service = self.service_edit.text().strip()
        client = self.ctx.client

        def progress(idx, msg, ok):
            self._events.append(("log", f"{'[성공]' if ok else '[실패]'} {msg}"))
            # idx >= 1이면 create_tunnel(idx==0)이 성공했다는 뜻
            # 워커 스레드에서 동기 설정 (이벤트 드레인 대기 불필요)
            if idx >= 1:
                self._tunnel_created = True

        def work():
            try:
                execute_creation(client, name, hostname, service, progress,
                                 created=self._created)
                self._events.append(("done", name, hostname, service))
            except Exception as ex:  # CloudflaredError 포함
                is_dns_conflict = isinstance(ex, DnsRecordExistsError)
                self._events.append(
                    ("fail", str(ex), self._tunnel_created, name, is_dns_conflict))

        threading.Thread(target=work, daemon=True).start()

    def _start_overwrite(self):
        """DNS 레코드 충돌 후 재개: 터널은 그대로 두고 DNS 연결부터 다시 진행한다."""
        self.overwrite_btn.setEnabled(False)
        self.rollback_btn.setEnabled(False)
        self.next_btn.setEnabled(False)
        name = self.name_edit.text().strip()
        hostname = self._hostname()
        service = self.service_edit.text().strip()
        client = self.ctx.client
        tunnel_id = self._created.get("tunnel_id")
        credentials = self._created.get("credentials")

        def progress(idx, msg, ok):
            self._events.append(("log", f"{'[성공]' if ok else '[실패]'} {msg}"))

        def work():
            try:
                finish_creation(client, name, hostname, service, tunnel_id,
                                credentials, progress, overwrite_dns=True)
                self._events.append(("done", name, hostname, service))
            except Exception as ex:
                is_dns_conflict = isinstance(ex, DnsRecordExistsError)
                self._events.append(
                    ("fail", str(ex), True, name, is_dns_conflict))

        threading.Thread(target=work, daemon=True).start()

    def _start_rollback(self):
        self.rollback_btn.setEnabled(False)
        self.next_btn.setEnabled(False)
        name = self._current_tunnel_name
        client = self.ctx.client

        def work():
            try:
                client.delete_tunnel(name)
                # 설정 파일도 삭제 시도
                try:
                    config_file = client.config_path(name)
                    if client.runner.file_exists(config_file):
                        client.runner.remove_file(config_file)
                except Exception:
                    pass  # 설정 파일 삭제 실패는 무시
                self._events.append(("rollback_ok",))
            except Exception as ex:
                self._events.append(("rollback_fail", str(ex)))

        threading.Thread(target=work, daemon=True).start()

    # ---- 성공 시 자동 닫기 ----
    def _start_close_countdown(self):
        self._close_countdown = 5
        self.next_btn.setText(f"완료 ({self._close_countdown})")
        self._close_timer.start(1000)

    def _on_close_tick(self):
        self._close_countdown -= 1
        if self._close_countdown <= 0:
            self._close_timer.stop()
            self.accept()
            return
        self.next_btn.setText(f"완료 ({self._close_countdown})")

    def closeEvent(self, event):
        # 다이얼로그가 닫힌 뒤 타이머가 발화해 크래시하지 않도록 정지한다.
        self._close_timer.stop()
        self._timer.stop()
        super().closeEvent(event)

    def reject(self):
        self._close_timer.stop()
        super().reject()

    def accept(self):
        self._close_timer.stop()
        super().accept()

    def _drain_events(self):
        while self._events:
            ev = self._events.pop(0)
            if ev[0] == "log":
                self.status_label.setText(
                    (self.status_label.text() + "\n" + ev[1]).strip())
            elif ev[0] == "done":
                _, name, hostname, service = ev
                route = RouteMeta(
                    id=new_route_id(), hostname=hostname, service=service,
                    server=ServiceSpec(
                        kind=self.kind_combo.currentData(),
                        start_cmd=self.cmd_edit.text().strip(),
                        stop_cmd=self.stop_cmd_edit.text().strip(),
                        cwd=self.cwd_edit.text().strip(),
                        autostart=self.together_chk.isChecked()))
                self.created_meta = TunnelMeta(name=name, routes=[route])
                # 루트 도메인 기억
                self.ctx.store.settings.root_domain = self.domain_edit.text().strip()
                self._next_mode = "done"
                self.next_btn.setEnabled(True)
                self.overwrite_btn.setVisible(False)
                self.overwrite_hint.setVisible(False)
                self.rollback_btn.setVisible(False)
                self._start_close_countdown()
            elif ev[0] == "fail":
                _, msg, tunnel_created, name, is_dns_conflict = ev
                self.status_label.setText(
                    self.status_label.text() + f"\n[실패] {msg}")
                # 실패 시에는 절대 자동으로 닫지 않는다 (롤백/덮어쓰기를 눌러야 함)
                self._close_timer.stop()
                # 터널이 생성됐으면 롤백 버튼 표시, 아니면 닫기만
                self._next_mode = "close"
                self.next_btn.setText("닫기")
                self.next_btn.setEnabled(True)
                self.overwrite_btn.setEnabled(True)
                self.rollback_btn.setEnabled(True)
                if tunnel_created:
                    self.rollback_btn.setVisible(True)
                    self._current_tunnel_name = name
                    # 재시도 가능하도록 뒤로가기 버튼 표시
                    self.back_btn.setVisible(True)
                    if is_dns_conflict:
                        self.overwrite_btn.setVisible(True)
                        self.overwrite_hint.setVisible(True)
            elif ev[0] == "rollback_ok":
                self.status_label.setText(
                    self.status_label.text() + "\n[성공] 터널 삭제 완료")
                self.rollback_btn.setVisible(False)
                self.overwrite_btn.setVisible(False)
                self.overwrite_hint.setVisible(False)
                self.next_btn.setText("닫기")
                self._next_mode = "close"
            elif ev[0] == "rollback_fail":
                self.status_label.setText(
                    self.status_label.text() + f"\n[실패] 롤백 실패: {ev[1]}")
                self.next_btn.setText("닫기")
                self._next_mode = "close"
