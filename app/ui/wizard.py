from __future__ import annotations

import threading

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel,
                             QLineEdit, QPushButton, QScrollArea, QVBoxLayout,
                             QWidget)

from app.context import AppContext
from app.core.cloudflared import DnsRecordExistsError
from app.core.store import RouteMeta, ServiceSpec, TunnelMeta, new_route_id
from app.core.wizard_logic import (create_tunnel_only, execute_creation,
                                   finish_creation, plan_steps,
                                   plan_steps_tunnel_only, validate_name,
                                   validate_service, validate_subdomain)
from app.ui.icons import make_icon
from app.ui.repo_picker import CwdPickerRow
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
        # C1 수정: owner는 계정 단위(Settings.tunnel_owners)에 기록해야 하므로
        # created_meta에 담지 않고 터널 UUID를 별도로 실어 보낸다 - 호출측
        # (main_window._create_tunnel)이 tunnel_owners[tunnel_id]에 쓴다.
        self.created_tunnel_id: str = ""
        self._events: list[tuple] = []  # 워커 스레드 → UI 폴링 큐
        self._next_mode = "nav"  # "nav" | "close" | "done"
        self._tunnel_created = False  # 터널 생성 성공 여부 추적
        self._current_tunnel_name = ""  # 롤백/재개용 터널 이름
        self._created: dict = {}  # execute_creation이 채우는 tunnel_id/credentials (재개용)
        self._last_error_is_dns_conflict = False
        self._close_countdown = 5  # 성공 시 자동 닫기 카운트다운(초)
        self.setWindowTitle("터널 생성")
        # 창을 작게 고정하고, 내용이 넘치는 페이지(4단계)는 스크롤로 처리한다.
        # 페이지마다 창 크기가 들썩이면 산만하고, 가장 높은 페이지에 맞추면
        # 짧은 페이지에서 아래가 텅 빈다 - 작은 고정 크기 + 스크롤이 절충안이다.
        self.setFixedWidth(540)
        self.setFixedHeight(400)

        palette = current_palette(ctx.store.settings.theme)
        self.pages: list[QWidget] = []
        self._page_index = 0
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
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        page_area = QScrollArea()
        page_area.setWidgetResizable(True)
        page_area.setFrameShape(QScrollArea.Shape.NoFrame)
        page_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._page_area = page_area
        lay.addWidget(page_area, 1)
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
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        t = QLabel(title)
        t.setStyleSheet("font-size: 15px; font-weight: 700;")
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
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText(
            "비우면 hostname의 첫 라벨을 사용합니다 (예: app.example.com → app)")

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
        # 이전에는 여기서 QFileDialog를 직접 열었다. SSH 대상일 때도 내 PC의
        # 폴더 선택창이 떠서 원격 경로를 고를 수 없었다(같은 기능이 이미
        # route_dialog에는 제대로 있었다). 규칙을 공유 위젯으로 옮겼다.
        self.cwd_picker = CwdPickerRow(self.ctx, self.cwd_edit, self.service_edit,
                                       palette["text"])
        self.together_chk = QCheckBox("터널을 켤 때 서버도 함께 시작")

        # 터널 생성과 DNS 연결을 분리하는 선택지. 라우트 없이도 터널은
        # 만들어지므로(ingress에 404 폴백만 남는다) 2~4단계를 건너뛴다.
        self.later_chk = QCheckBox("지금은 도메인을 연결하지 않고 터널만 만들기")
        self.later_chk.toggled.connect(self._on_later_toggled)

        self.hostname_result = QLabel()
        self.hostname_result.setStyleSheet(
            f"color: {palette['accent']}; font-weight: 700;")

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)

        self.pages.append(self._page(
            "1. 터널 이름",
            self._hint("목록에서 구분하기 위한 이름입니다. 실제 접속 주소와는 "
                       "관계가 없고, 설정 파일 이름에 쓰입니다."),
            self._hint("영문, 숫자, 하이픈, 밑줄만 사용 가능. 예: mysite, blog, home-api"),
            self.name_edit))
        self.pages.append(self._page(
            "2. 연결할 도메인",
            self._hint("방문자가 브라우저에 입력하게 될 주소입니다. 두 칸을 합친 "
                       "주소로 Cloudflare에 CNAME 레코드가 자동 생성됩니다."),
            self._hint("루트 도메인은 Cloudflare에 등록되어 상태가 Active여야 합니다."),
            QLabel("서브도메인"), self.sub_edit,
            QLabel("루트 도메인"), self.domain_edit,
            self.hostname_result,
            self.later_chk,
            self._hint("터널만 먼저 만들어 두고, 도메인은 카드의 '라우트 추가'로 "
                       "나중에 붙일 수 있습니다. 라우트가 없는 동안에는 터널을 켜도 "
                       "404만 응답합니다.")))
        self.pages.append(self._page(
            "3. 로컬 서비스 주소",
            self._hint("웹서버가 실제로 듣고 있는 주소입니다. 로컬 구간은 http로 "
                       "충분하고, 외부 HTTPS는 Cloudflare가 처리합니다."),
            self._hint("예: http://localhost:8000 (uvicorn), :5173 (Vite), :3000 (Next.js)"),
            self.service_edit,
            QLabel("이름 (선택)"), self.label_edit))
        self.pages.append(self._page(
            "4. 웹서버 실행 명령 (선택)",
            self._hint("등록해 두면 카드에서 터널과 함께 켤 수 있습니다. 비워 두면 "
                       "서버는 직접 실행해야 합니다. 예: uvicorn main:app --port 8000"),
            self._hint("도커 컴포즈를 고르면 명령 기본값이 채워지고 작업 폴더"
                       "(compose 파일 위치)가 필수가 됩니다."),
            QLabel("서비스 종류"), self.kind_combo,
            QLabel("시작 명령"), self.cmd_edit,
            QLabel("정지 명령"), self.stop_cmd_edit,
            QLabel("작업 폴더"), self.cwd_edit, self.cwd_picker,
            self.together_chk))
        self.pages.append(self._page(
            "5. 실행",
            self._hint("터널 생성 → DNS 연결 → 설정 파일 작성 순서로 자동 진행됩니다. "
                       "중간에 실패하면 그 단계에서 멈추며, 이미 만들어진 터널은 롤백 "
                       "버튼으로 삭제할 수 있습니다."),
            self.status_label))

        for e in (self.name_edit, self.sub_edit, self.domain_edit,
                  self.service_edit):
            e.textChanged.connect(self._update_preview)

    # ---- 도메인 나중에 연결 ----
    def _on_later_toggled(self, checked: bool):
        # 입력칸을 비활성화해 "이 값은 지금 쓰이지 않는다"를 눈으로 알린다.
        self.sub_edit.setEnabled(not checked)
        self.domain_edit.setEnabled(not checked)
        self.hostname_result.setVisible(not checked)
        if self._page_index == 1:
            self.next_btn.setText("생성 시작" if checked else "다음 →")
            self.err.clear()
        self._update_preview()

    def _tunnel_only(self) -> bool:
        return self.later_chk.isChecked()

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
        i = self._page_index
        if i == 0:
            return validate_name(self.name_edit.text().strip(), self.existing)
        if i == 1:
            if self._tunnel_only():
                return ""  # 도메인을 쓰지 않으므로 검증할 것이 없다
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
        i = self._page_index
        name = self.name_edit.text().strip() or "<이름>"
        if self._tunnel_only():
            steps = plan_steps_tunnel_only(name)
        else:
            steps = plan_steps(name, self._hostname(),
                               self.service_edit.text().strip())
        if i in (0, 1, 2) and i < len(steps):
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

    def _show_page(self, i: int) -> None:
        """스크롤 영역에 현재 페이지만 얹는다.

        QStackedWidget을 쓰면 스택의 sizeHint가 항상 가장 높은 페이지 기준이라
        짧은 페이지에서도 불필요한 스크롤바가 생긴다. 한 번에 한 페이지만
        넣어 크기 계산이 실제 내용과 일치하게 한다.
        """
        if self._page_area.widget() is not None:
            taken = self._page_area.takeWidget()
            if taken is not None:
                taken.setParent(None)  # pages 리스트가 참조를 유지한다
        self._page_index = i
        self._page_area.setWidget(self.pages[i])
        self.pages[i].show()
        self._fit_height()

    def fit_to_content(self) -> None:
        """오버레이가 표시 직후 호출한다(ModalOverlay.refit)."""
        self._fit_height()

    def _fit_height(self) -> None:
        """현재 페이지 높이에 맞춰 창 높이만 조절한다(폭은 고정).

        스크롤 영역에 한 페이지만 들어 있으므로 heightForWidth로 실제 필요한
        높이를 정확히 얻을 수 있다. 너무 낮거나 높지 않게 범위를 제한한다.
        """
        page = self.pages[self._page_index]
        if page.layout() is None:
            return
        need = page.layout().heightForWidth(self.width() - 28)
        chrome = self.height() - self._page_area.height()
        if need <= 0 or chrome <= 0:
            return
        self.setFixedHeight(max(260, min(need + chrome + 8, 560)))
        parent = self.parentWidget()
        center = getattr(parent, "_center_content", None)
        if callable(center):
            center()

    def _go(self, i: int):
        self._show_page(i)
        self.back_btn.setVisible(0 < i < 4)
        # 도메인을 나중에 연결하는 경우 2단계가 마지막 입력 페이지가 된다.
        last_input = 1 if self._tunnel_only() else 3
        self.next_btn.setText("생성 시작" if i == last_input else "다음 →")
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
        i = self._page_index
        # 실행 페이지(4)에서 뒤로가면 마지막 입력 페이지로 돌아간다. 도메인을
        # 나중에 연결하는 경우에는 3·4단계를 건너뛰었으므로 2단계가 그 자리다.
        if i == 4:
            self._go(1 if self._tunnel_only() else 3)
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
        i = self._page_index
        if i == 1 and self._tunnel_only():
            # 3·4단계(서비스 주소, 서버 명령)는 라우트가 있어야 의미가 있다
            self._go(4)
            self._start_creation()
        elif i < 3:
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

        tunnel_only = self._tunnel_only()

        def work():
            try:
                if tunnel_only:
                    create_tunnel_only(client, name, progress,
                                       created=self._created)
                    self._events.append(("done", name, "", ""))
                else:
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
        # 재개 중에 페이지를 이동하면 워커가 뒤늦게 올린 결과가 엉뚱한 페이지에
        # 적용된다(_start_creation과 동일한 이유로 이동 자체를 막는다).
        self.back_btn.hide()
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
        self._stop_timers()
        super().reject()

    def accept(self):
        self._stop_timers()
        super().accept()

    def _stop_timers(self):
        # accept()/reject()는 closeEvent를 타지 않으므로 여기서도 멈춰야 한다.
        self._close_timer.stop()
        self._timer.stop()

    def _drain_events(self):
        while self._events:
            ev = self._events.pop(0)
            if ev[0] == "log":
                self.status_label.setText(
                    (self.status_label.text() + "\n" + ev[1]).strip())
            elif ev[0] == "done":
                _, name, hostname, service = ev
                if hostname:
                    route = RouteMeta(
                        id=new_route_id(), hostname=hostname, service=service,
                        label=self.label_edit.text().strip(),
                        server=ServiceSpec(
                            kind=self.kind_combo.currentData(),
                            start_cmd=self.cmd_edit.text().strip(),
                            stop_cmd=self.stop_cmd_edit.text().strip(),
                            cwd=self.cwd_edit.text().strip(),
                            autostart=self.together_chk.isChecked()))
                    routes = [route]
                    # 루트 도메인 기억 (도메인을 실제로 입력했을 때만)
                    self.ctx.store.settings.root_domain = \
                        self.domain_edit.text().strip()
                else:
                    routes = []  # 도메인은 나중에 '라우트 추가'로 붙인다
                self.created_meta = TunnelMeta(name=name, routes=routes)
                self.created_tunnel_id = self._created.get("tunnel_id", "")
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
