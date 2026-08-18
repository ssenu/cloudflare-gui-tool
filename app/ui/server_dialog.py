"""서버 카테고리에 들어가는 서버의 추가·편집 다이얼로그.

RouteDialog와 갈라놓은 이유: 그쪽은 절반 이상이 서브도메인 검증, DNS 레코드
생성, config-*.yml의 ingress 재작성 코드다. 여기서는 그 셋을 **하나도 하지
않는다** - 도메인이 없는 것이 이 기능의 존재 이유이기 때문이다. 같은 파일에
"도메인 없는 모드" 분기를 넣으면 두 흐름이 서로를 가리게 된다.

대신 서버 실행에 관한 규칙(도커면 작업 폴더 필수, 포트 불일치 경고, 실행 중인
서버의 실행 설정을 바꿀 때의 정지 확인)은 RouteDialog와 같은 것을 쓴다.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                             QLabel, QLineEdit, QMessageBox)

from app.context import AppContext
from app.core.prereq import port_mismatch
from app.core.run_registry import group_owner_key
from app.core.store import (RouteMeta, ServerGroupMeta, ServiceSpec,
                            new_route_id)
from app.core.wizard_logic import validate_service
from app.ui.repo_picker import CwdPickerRow, detect_service_port
from app.ui.route_dialog import (DOCKER_START_DEFAULT, DOCKER_STOP_DEFAULT,
                                 KIND_LABELS)
from app.ui.theme import current_palette
from app.ui.winutil import apply_titlebar_theme


class ServerDialog(QDialog):
    """서버 추가·편집 겸용.

    server가 None이면 추가, 아니면 해당 RouteMeta를 편집한다. 저장에 성공하면
    group.servers와 settings가 함께 갱신된다.
    """

    def __init__(self, ctx: AppContext, group: ServerGroupMeta,
                 server: RouteMeta | None = None, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.group = group
        self.server = server
        self.saved_server: RouteMeta | None = None
        self.setWindowTitle("서버 편집" if server else "서버 추가")
        self.setMinimumWidth(480)

        palette = current_palette(ctx.store.settings.theme)
        icon_color = palette["text"]

        self.name_edit = QLineEdit(server.label if server else "")
        self.name_edit.setPlaceholderText("예: api (카드에 표시할 이름)")

        self.service_edit = QLineEdit(
            server.service if server else "http://localhost:8000")

        self.kind_combo = QComboBox()
        for value, label in KIND_LABELS:
            self.kind_combo.addItem(label, value)
        if server and server.server.kind == "docker":
            self.kind_combo.setCurrentIndex(1)
        self.kind_combo.currentIndexChanged.connect(self._on_kind_changed)

        self.start_cmd_edit = QLineEdit(server.server.start_cmd if server else "")
        self.start_cmd_edit.setPlaceholderText("예: uvicorn main:app --port 8000 (선택)")
        self.stop_cmd_edit = QLineEdit(server.server.stop_cmd if server else "")
        self.stop_cmd_edit.setPlaceholderText("비우면 프로세스를 강제 종료합니다 (선택)")
        self.cwd_edit = QLineEdit(server.server.cwd if server else "")
        self.cwd_edit.setPlaceholderText("작업 폴더 (도커는 필수)")
        self.cwd_picker = CwdPickerRow(ctx, self.cwd_edit, self.service_edit,
                                       icon_color)

        self.err_label = QLabel()
        self.err_label.setStyleSheet(f"color: {palette['danger']};")
        self.err_label.setWordWrap(True)

        hint = QLabel("이 서버는 터널에 연결되지 않습니다 - 도메인도 DNS 레코드도 "
                      "만들지 않고, 대상 기기 안에서만 실행됩니다.")
        hint.setStyleSheet(f"color: {palette['muted']}; font-size: 11px;")
        hint.setWordWrap(True)

        form = QFormLayout()
        form.addRow("이름", self.name_edit)
        form.addRow("주소", self.service_edit)
        form.addRow("서버 종류", self.kind_combo)
        form.addRow("시작 명령", self.start_cmd_edit)
        form.addRow("정지 명령", self.stop_cmd_edit)
        form.addRow("작업 폴더", self.cwd_edit)
        form.addRow("", self.cwd_picker)
        form.addRow("", hint)
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
        name = self.name_edit.text().strip()
        if not name:
            return "이름을 입력하세요"
        for s in self.group.servers:
            if s.label == name and (self.server is None or s.id != self.server.id):
                return f"이 카테고리에 이미 '{name}' 서버가 있습니다"
        e = validate_service(self.service_edit.text().strip())
        if e:
            return e
        kind = self.kind_combo.currentData()
        if kind == "docker" and not self.cwd_edit.text().strip():
            return "도커 컴포즈는 작업 폴더(compose 파일 위치)가 필요합니다"
        if kind == "command" and not self.start_cmd_edit.text().strip():
            return "시작 명령을 입력하세요"
        return ""

    # ---- 저장 ----
    def _on_save(self):
        err = self._validate()
        if err:
            self.err_label.setText(err)
            return
        self.err_label.clear()

        name = self.name_edit.text().strip()
        service = self.service_edit.text().strip()
        kind = self.kind_combo.currentData()
        cwd = self.cwd_edit.text().strip()
        start_cmd = self.start_cmd_edit.text().strip()
        stop_cmd = self.stop_cmd_edit.text().strip()
        if kind == "docker":
            # 종류를 바꾸지 않고 저장하면 _on_kind_changed가 불리지 않는다.
            # 명령이 비어 있으면 실행 시점의 기본값과 같은 값을 채워 둔다 -
            # 카드에서 무엇이 실행되는지 보이는 편이 낫다.
            start_cmd = start_cmd or DOCKER_START_DEFAULT
            stop_cmd = stop_cmd or DOCKER_STOP_DEFAULT

        # 도커라면 compose가 실제로 여는 포트와 대조한다. 다르면 상태 판정이
        # 쓰는 주소에 아무도 없어 영영 "실행 중"이 되지 않는다.
        if kind == "docker" and cwd:
            detected = detect_service_port(self.ctx, cwd)
            warn = port_mismatch(service, detected)
            if warn:
                ok = QMessageBox.question(
                    self, "포트가 다릅니다",
                    warn + "\n\n그래도 이대로 저장할까요?")
                if ok != QMessageBox.StandardButton.Yes:
                    return

        owner = group_owner_key(self.group.id)
        is_new = self.server is None

        # 실행 중인 서버의 실행 설정을 바꾸면 기존 프로세스(또는 컨테이너)가
        # GUI에서 더 이상 손댈 수 없는 고아가 된다 (RouteDialog의 I8과 같은 이유).
        if not is_new and self.ctx.manager.service_running(owner, self.server):
            spec = self.server.server
            changed = (kind != spec.kind or start_cmd != spec.start_cmd
                       or stop_cmd != spec.stop_cmd or cwd != spec.cwd)
            if changed:
                ok = QMessageBox.question(
                    self, "서버 중지 필요",
                    "변경을 적용하려면 서버를 중지해야 합니다. 지금 중지할까요?")
                if ok != QMessageBox.StandardButton.Yes:
                    return
                try:
                    self.ctx.manager.stop_service(owner, self.server)
                except Exception as ex:
                    QMessageBox.critical(self, "중지 실패", str(ex))
                    return

        if is_new:
            server = RouteMeta(
                id=new_route_id(), hostname="", service=service, label=name,
                server=ServiceSpec(kind=kind, start_cmd=start_cmd,
                                   stop_cmd=stop_cmd, cwd=cwd, autostart=False))
            self.group.servers.append(server)
            self.saved_server = server
        else:
            self.server.service = service
            self.server.label = name
            self.server.server.kind = kind
            self.server.server.start_cmd = start_cmd
            self.server.server.stop_cmd = stop_cmd
            self.server.server.cwd = cwd
            self.saved_server = self.server

        self.ctx.store.save()
        self.accept()
