from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFormLayout, QLabel, QLineEdit, QMessageBox)

from app.context import AppContext
from app.core.cloudflared import CloudflaredError, DnsRecordExistsError
from app.core.config_yml import set_routes
from app.core.store import RouteMeta, ServiceSpec, TunnelMeta, new_route_id
from app.core.wizard_logic import (ConfigRecoveryError, ensure_config,
                                   validate_service, validate_subdomain)
from app.core.prereq import port_mismatch
from app.ui.poller import BackgroundPoller
from app.ui.repo_picker import CwdPickerRow, detect_service_port
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
        self._saving = False
        self._poller = BackgroundPoller(self)
        self._poller.finished.connect(lambda *a: None)
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
        self.cwd_edit.setPlaceholderText("작업 폴더 - 직접 입력하거나 아래 버튼으로 선택 (도커는 필수)")
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
    # 저장은 원격 왕복이 여러 번이다(포트 감지, 설정 파일, DNS 등록, 서버 정지,
    # 설정 쓰기). 파이가 빌드 중이면 각각이 몇 초씩이라, GUI 스레드에서
    # 차례로 기다리면 그동안 창이 통째로 굳는다(실측 ls 2.2초, cloudflared 17초).
    # 그래서 원격 호출은 전부 워커에서 하고, 사용자에게 물어야 하는 지점에서만
    # GUI로 돌아온다. 단계: 포트 감지 -> 준비(설정 파일·정지) -> DNS -> 설정 쓰기.

    def _bg(self, fn, done):
        """fn을 워커에서 돌리고 done(exc, value)를 GUI에서 부른다.

        예외를 문자열이 아니라 객체로 돌려받아야 DnsRecordExistsError 같은
        종류별 분기가 된다.
        """
        def wrapped():
            try:
                return (None, fn())
            except Exception as ex:
                return (ex, None)

        def _finish(ok, result, error):
            exc, value = result if ok else (RuntimeError(error), None)
            done(exc, value)

        self._poller.finished.disconnect()
        self._poller.finished.connect(_finish)
        self._poller.run(wrapped)

    def _set_busy(self, busy: bool):
        self._saving = busy
        self.setEnabled(not busy)
        self.err_label.setText("저장하는 중..." if busy else "")

    def _on_save(self):
        err = self._validate()
        if err:
            self.err_label.setText(err)
            return
        self.err_label.clear()

        sub = self.sub_edit.text().strip()
        domain = self.domain_edit.text().strip()
        f = self._form = {
            "hostname": sub + "." + domain,
            "service": self.service_edit.text().strip(),
            "label": self.label_edit.text().strip(),
            "kind": self.kind_combo.currentData(),
            "start_cmd": self.start_cmd_edit.text().strip(),
            "stop_cmd": self.stop_cmd_edit.text().strip(),
            "cwd": self.cwd_edit.text().strip(),
            "autostart": self.autostart_chk.isChecked(),
        }
        self._set_busy(True)
        # 1단계: 도커라면 compose가 실제로 여는 포트와 대조한다. 다르면 터널은
        # 붙는데 그 포트에 아무도 없어 502가 난다 - 저장 전에 알려준다.
        if f["kind"] == "docker" and f["cwd"]:
            self._bg(lambda: detect_service_port(self.ctx, f["cwd"]), self._after_detect)
        else:
            self._after_detect(None, "")

    def _after_detect(self, exc, detected):
        f = self._form
        warn = port_mismatch(f["service"], detected or "") if exc is None else ""
        if warn:
            self._set_busy(False)
            ok = QMessageBox.question(
                self, "포트가 다릅니다", warn + "\n\n그래도 이대로 저장할까요?")
            if ok != QMessageBox.StandardButton.Yes:
                return
            self._set_busy(True)

        # I8: 실행 중인 서비스의 kind/start_cmd/stop_cmd/cwd를 바꾸면 기존
        # 프로세스(또는 도커 컨테이너)가 GUI에서 더 이상 손댈 수 없는 고아가
        # 된다. 저장 전에 중지 여부를 확인한다.
        need_stop = False
        if self.route is not None and self.ctx.manager.service_running(
                self.tunnel.name, self.route):
            server = self.route.server
            need_stop = (f["kind"] != server.kind or f["start_cmd"] != server.start_cmd
                         or f["stop_cmd"] != server.stop_cmd or f["cwd"] != server.cwd)
            if need_stop:
                self._set_busy(False)
                ok = QMessageBox.question(
                    self, "서버 중지 필요",
                    "변경을 적용하려면 서버를 중지해야 합니다. 지금 중지할까요?")
                if ok != QMessageBox.StandardButton.Yes:
                    return
                self._set_busy(True)

        # 2단계: 설정 파일 보장(+ 필요하면 정지). 설정 파일이 없으면 여기서
        # 만든다 - 라우트를 붙이려는 것 자체가 설정 파일을 원한다는 뜻이다.
        def prepare():
            ensure_config(self.ctx.client, self.tunnel.name)
            if need_stop:
                self.ctx.manager.stop_service(self.tunnel.name, self.route)

        self._bg(prepare, self._after_prepare)

    def _after_prepare(self, exc, _value):
        if exc is not None:
            self._set_busy(False)
            if isinstance(exc, ConfigRecoveryError):
                QMessageBox.warning(self, "설정 파일을 만들 수 없음", str(exc))
            else:
                QMessageBox.critical(self, "설정 파일 생성 실패", str(exc))
            return
        f = self._form
        is_new = self.route is None
        hostname_changed = (not is_new) and self.route.hostname != f["hostname"]
        # 3단계: DNS. Cloudflare API 왕복이라 가장 느리다.
        if is_new or hostname_changed:
            self._bg(lambda: self.ctx.client.route_dns(self.tunnel.name, f["hostname"]),
                     lambda exc, _v: self._after_dns(exc, overwrite=False))
        else:
            self._after_dns(None, overwrite=False)

    def _after_dns(self, exc, overwrite: bool):
        f = self._form
        if isinstance(exc, DnsRecordExistsError) and not overwrite:
            self._set_busy(False)
            ok = QMessageBox.question(
                self, "DNS 레코드 충돌",
                "이 주소(" + f["hostname"] + ")에 이미 DNS 레코드가 있습니다.\n"
                "이 터널로 덮어쓸까요?")
            if ok != QMessageBox.StandardButton.Yes:
                return
            self._set_busy(True)
            self._bg(lambda: self.ctx.client.route_dns(self.tunnel.name, f["hostname"],
                                                       overwrite=True),
                     lambda exc, _v: self._after_dns(exc, overwrite=True))
            return
        if exc is not None:
            self._set_busy(False)
            QMessageBox.critical(self, "DNS 연결 실패", str(exc))
            return
        is_new = self.route is None
        if (not is_new) and self.route.hostname != f["hostname"]:
            QMessageBox.information(
                self, "이전 DNS 레코드",
                "이전 주소(" + self.route.hostname + ")의 DNS 레코드는 Cloudflare "
                "대시보드에서 직접 삭제해야 합니다.")

        # 4단계: config.yml 반영. 실패하면 tunnel.routes를 건드리지 않도록
        # mutate는 쓰기 성공 이후로 미룬다.
        if is_new:
            routes_for_config = [(r.hostname, r.service) for r in self.tunnel.routes]
            routes_for_config.append((f["hostname"], f["service"]))
        else:
            routes_for_config = [
                (f["hostname"], f["service"]) if r.id == self.route.id
                else (r.hostname, r.service) for r in self.tunnel.routes]
        path = self.ctx.client.config_path(self.tunnel.name)

        def write():
            text = self.ctx.runner.read_file(path)
            self.ctx.runner.write_file(path, set_routes(text, routes_for_config))

        self._bg(write, self._after_write)

    def _after_write(self, exc, _value):
        self._set_busy(False)
        if exc is not None:
            QMessageBox.critical(self, "설정 저장 실패", str(exc))
            return
        f = self._form
        if self.route is None:
            route = RouteMeta(
                id=new_route_id(), hostname=f["hostname"], service=f["service"],
                label=f["label"],
                server=ServiceSpec(kind=f["kind"], start_cmd=f["start_cmd"],
                                   stop_cmd=f["stop_cmd"], cwd=f["cwd"],
                                   autostart=f["autostart"]))
            self.tunnel.routes.append(route)
            self.saved_route = route
        else:
            self.route.hostname = f["hostname"]
            self.route.service = f["service"]
            self.route.label = f["label"]
            self.route.server.kind = f["kind"]
            self.route.server.start_cmd = f["start_cmd"]
            self.route.server.stop_cmd = f["stop_cmd"]
            self.route.server.cwd = f["cwd"]
            self.route.server.autostart = f["autostart"]
            self.saved_route = self.route

        self.ctx.store.settings.root_domain = self.domain_edit.text().strip()
        self.ctx.store.settings.tunnels_for(self.ctx.runner.name)[self.tunnel.name] = self.tunnel
        self.ctx.store.save()
        self.accept()
