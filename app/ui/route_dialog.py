from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFormLayout, QLabel, QLineEdit, QMessageBox)

from app.context import AppContext
from app.core.cloudflared import CloudflaredError, DnsRecordExistsError
from app.core.config_yml import set_routes
from app.core.store import RouteMeta, ServiceSpec, TunnelMeta, new_route_id
from app.core.wizard_logic import (ConfigRecoveryError, ensure_config,
                                   validate_service, validate_subdomain)
from app.ui.repo_picker import CwdPickerRow
from app.ui.theme import current_palette
from app.ui.winutil import apply_titlebar_theme

KIND_LABELS = [("command", "명령"), ("docker", "도커 컴포즈")]

DOCKER_START_DEFAULT = "docker compose up --build -d"
DOCKER_STOP_DEFAULT = "docker compose down"


def _split_hostname(hostname: str, default_domain: str) -> tuple[str, str]:
    """기존 hostname을 서브도메인/루트 도메인 입력칸에 채우기 위해 분리한다."""
    if not hostname:
        return "", default_domain
    if "." in hostname:
        sub, domain = hostname.split(".", 1)
        return sub, domain
    return hostname, default_domain


class RouteDialog(QDialog):
    """라우트 추가·편집 겸용 다이얼로그.

    route가 None이면 추가, 아니면 해당 RouteMeta를 편집한다. tunnel.routes와
    config.yml, settings는 저장 성공 시(accept 직전) 함께 갱신된다 — 실패하면
    아무것도 바뀌지 않은 채 다이얼로그가 열린 상태로 남는다.
    """

    def __init__(self, ctx: AppContext, tunnel: TunnelMeta,
                route: RouteMeta | None = None, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.tunnel = tunnel
        self.route = route
        self.saved_route: RouteMeta | None = None
        self.setWindowTitle("라우트 편집" if route else "라우트 추가")
        self.setMinimumWidth(480)

        palette = current_palette(ctx.store.settings.theme)
        icon_color = palette["text"]

        self.label_edit = QLineEdit(route.label if route else "")
        self.label_edit.setPlaceholderText(
            "비우면 hostname의 첫 라벨을 사용합니다 (예: app.example.com → app)")

        default_domain = ctx.store.settings.root_domain
        sub, domain = _split_hostname(route.hostname if route else "", default_domain)
        self.sub_edit = QLineEdit(sub)
        self.sub_edit.setPlaceholderText("예: mysite")
        self.domain_edit = QLineEdit(domain)
        self.domain_edit.setPlaceholderText("예: example.com")

        self.service_edit = QLineEdit(route.service if route else "http://localhost:8000")

        self.kind_combo = QComboBox()
        for value, label in KIND_LABELS:
            self.kind_combo.addItem(label, value)
        if route and route.server.kind == "docker":
            self.kind_combo.setCurrentIndex(1)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)

        self.start_cmd_edit = QLineEdit(route.server.start_cmd if route else "")
        self.start_cmd_edit.setPlaceholderText("예: uvicorn main:app --port 8000 (선택)")
        self.stop_cmd_edit = QLineEdit(route.server.stop_cmd if route else "")
        self.stop_cmd_edit.setPlaceholderText("비우면 프로세스를 강제 종료합니다 (선택)")
        self.cwd_edit = QLineEdit(route.server.cwd if route else "")
        self.cwd_edit.setPlaceholderText("작업 폴더 (도커는 필수)")
        # 로컬/원격에 따라 폴더 선택 방식이 갈리는 규칙은 CwdPickerRow가 갖는다
        # (마법사와 공유해, 한쪽만 고쳐지는 일이 없게 한다).
        self.cwd_picker = CwdPickerRow(ctx, self.cwd_edit, self.service_edit,
                                       icon_color)

        self.autostart_chk = QCheckBox("터널을 켤 때 함께 시작")
        self.autostart_chk.setChecked(route.server.autostart if route else False)

        self.err_label = QLabel()
        self.err_label.setStyleSheet(f"color: {palette['danger']};")
        self.err_label.setWordWrap(True)

        form = QFormLayout()
        form.addRow("이름", self.label_edit)
        form.addRow("서브도메인", self.sub_edit)
        form.addRow("루트 도메인", self.domain_edit)
        form.addRow("로컬 서비스 주소", self.service_edit)
        form.addRow("서비스 종류", self.kind_combo)
        form.addRow("시작 명령", self.start_cmd_edit)
        form.addRow("정지 명령", self.stop_cmd_edit)
        form.addRow("작업 폴더", self.cwd_edit)
        form.addRow("", self.cwd_picker)
        form.addRow("", self.autostart_chk)
        form.addRow(self.err_label)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Save).setText("저장")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

        self.setLayout(form)
        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

    # ---- 종류 변경 ----
    def _on_kind_changed(self):
        if self.kind_combo.currentData() == "docker":
            if not self.start_cmd_edit.text().strip():
                self.start_cmd_edit.setText(DOCKER_START_DEFAULT)
            if not self.stop_cmd_edit.text().strip():
                self.stop_cmd_edit.setText(DOCKER_STOP_DEFAULT)

    # ---- 검증 ----
    def _validate(self) -> str:
        sub = self.sub_edit.text().strip()
        domain = self.domain_edit.text().strip()
        e = validate_subdomain(sub)
        if e:
            return e
        if not domain:
            return "루트 도메인을 입력하세요"
        e = validate_service(self.service_edit.text().strip())
        if e:
            return e
        if self.kind_combo.currentData() == "docker" and not self.cwd_edit.text().strip():
            return "도커 컴포즈는 작업 폴더(compose 파일 위치)가 필요합니다"
        hostname = f"{sub}.{domain}"
        for r in self.tunnel.routes:
            if r.hostname == hostname and (self.route is None or r.id != self.route.id):
                return f"같은 터널 안에 이미 '{hostname}' hostname이 있습니다"
        return ""

    # ---- 저장 ----
    def _on_save(self):
        err = self._validate()
        if err:
            self.err_label.setText(err)
            return
        self.err_label.clear()

        hostname = f"{self.sub_edit.text().strip()}.{self.domain_edit.text().strip()}"
        service = self.service_edit.text().strip()
        label = self.label_edit.text().strip()
        kind = self.kind_combo.currentData()
        start_cmd = self.start_cmd_edit.text().strip()
        stop_cmd = self.stop_cmd_edit.text().strip()
        cwd = self.cwd_edit.text().strip()
        autostart = self.autostart_chk.isChecked()

        # 설정 파일이 없으면 여기서 만든다. 도메인 없이 터널만 먼저 만들었거나
        # 터미널에서 직접 만든 터널이 이 경우인데, 라우트를 붙이려는 것 자체가
        # 설정 파일을 원한다는 뜻이므로 거절할 이유가 없다.
        path = self.ctx.client.config_path(self.tunnel.name)
        try:
            ensure_config(self.ctx.client, self.tunnel.name)
        except ConfigRecoveryError as ex:
            QMessageBox.warning(self, "설정 파일을 만들 수 없음", str(ex))
            return
        except Exception as ex:
            QMessageBox.critical(self, "설정 파일 생성 실패", str(ex))
            return

        is_new = self.route is None
        hostname_changed = (not is_new) and self.route.hostname != hostname

        # I8: 실행 중인 서비스의 kind/start_cmd/stop_cmd/cwd를 바꾸면 기존
        # 프로세스(또는 도커 컨테이너)가 GUI에서 더 이상 손댈 수 없는 고아가
        # 된다. 저장 전에 중지 여부를 확인한다.
        if not is_new and self.ctx.manager.service_running(self.tunnel.name, self.route):
            server = self.route.server
            changed = (kind != server.kind or start_cmd != server.start_cmd
                      or stop_cmd != server.stop_cmd or cwd != server.cwd)
            if changed:
                ok = QMessageBox.question(
                    self, "서버 중지 필요",
                    "변경을 적용하려면 서버를 중지해야 합니다. 지금 중지할까요?")
                if ok != QMessageBox.StandardButton.Yes:
                    return
                try:
                    self.ctx.manager.stop_service(self.tunnel.name, self.route)
                except Exception as ex:
                    QMessageBox.critical(self, "중지 실패", str(ex))
                    return

        if is_new or hostname_changed:
            try:
                self.ctx.client.route_dns(self.tunnel.name, hostname)
            except DnsRecordExistsError as ex:
                ok = QMessageBox.question(
                    self, "DNS 레코드 충돌",
                    f"이 주소({hostname})에 이미 DNS 레코드가 있습니다.\n"
                    "이 터널로 덮어쓸까요?")
                if ok != QMessageBox.StandardButton.Yes:
                    return
                try:
                    self.ctx.client.route_dns(self.tunnel.name, hostname, overwrite=True)
                except Exception as ex2:
                    QMessageBox.critical(self, "DNS 연결 실패", str(ex2))
                    return
            except CloudflaredError as ex:
                QMessageBox.critical(self, "DNS 연결 실패", str(ex))
                return
            except Exception as ex:
                QMessageBox.critical(self, "DNS 연결 실패", str(ex))
                return
            if hostname_changed:
                QMessageBox.information(
                    self, "이전 DNS 레코드",
                    f"이전 주소({self.route.hostname})의 DNS 레코드는 Cloudflare "
                    "대시보드에서 직접 삭제해야 합니다.")

        # config.yml에 반영될 라우트 목록을 먼저 계산한다 (실패 시 tunnel.routes를
        # 건드리지 않기 위해 mutate는 config 쓰기 성공 이후로 미룬다).
        if is_new:
            routes_for_config = [(r.hostname, r.service) for r in self.tunnel.routes]
            routes_for_config.append((hostname, service))
        else:
            routes_for_config = [
                (hostname, service) if r.id == self.route.id else (r.hostname, r.service)
                for r in self.tunnel.routes
            ]

        try:
            text = self.ctx.runner.read_file(path)
            self.ctx.runner.write_file(path, set_routes(text, routes_for_config))
        except Exception as ex:
            QMessageBox.critical(self, "설정 저장 실패", str(ex))
            return

        if is_new:
            route = RouteMeta(
                id=new_route_id(), hostname=hostname, service=service, label=label,
                server=ServiceSpec(kind=kind, start_cmd=start_cmd, stop_cmd=stop_cmd,
                                   cwd=cwd, autostart=autostart))
            self.tunnel.routes.append(route)
            self.saved_route = route
        else:
            self.route.hostname = hostname
            self.route.service = service
            self.route.label = label
            self.route.server.kind = kind
            self.route.server.start_cmd = start_cmd
            self.route.server.stop_cmd = stop_cmd
            self.route.server.cwd = cwd
            self.route.server.autostart = autostart
            self.saved_route = self.route

        self.ctx.store.settings.root_domain = self.domain_edit.text().strip()
        self.ctx.store.settings.tunnels_for(self.ctx.runner.name)[self.tunnel.name] = self.tunnel
        self.ctx.store.save()
        self.accept()
