"""프로젝트 환경설정(.env) 편집 다이얼로그.

docker-compose.yml을 직접 고치지 않는 이유는 app/core/env_file.py 첫머리에
적어 두었다. 요약하면 (1) YAML을 다시 쓰면 주석이 사라지고 (2) compose는
git 추적 대상이라 대상 머신에서 고치면 다음 pull이 거부되기 때문이다.

포트는 별도 입력칸으로 빼서 검증한다 - 여러 프로젝트를 올릴 때 실제로
바꾸는 값이 이것 하나뿐이라, 표 안에 섞어 두면 매번 찾아야 한다.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
                             QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QScrollArea, QVBoxLayout,
                             QWidget)

from app.context import AppContext
from app.core.env_file import (HOST_PORT_KEY, EnvFile, is_secret_key,
                               validate_env_key, validate_port)
from app.core.store import RepoMeta
from app.ui.theme import current_palette
from app.ui.winutil import apply_titlebar_theme

PORT_HINT = ("compose에 <code>${HOST_PORT:-8000}</code> 으로 적어 두면 이 값이 "
             "실제로 열리는 포트가 됩니다. 프로젝트마다 다르게 주세요.")


class _EnvRow(QWidget):
    """키=값 한 줄. 비밀번호류는 가려서 보여준다."""

    def __init__(self, key: str, value: str, on_delete, show_values: bool):
        super().__init__()
        self.key_edit = QLineEdit(key)
        self.key_edit.setPlaceholderText("이름")
        self.key_edit.setFixedWidth(180)
        self.value_edit = QLineEdit(value)
        self.value_edit.setPlaceholderText("값")
        self.deleted = False

        del_btn = QPushButton("삭제")
        del_btn.setObjectName("danger")
        del_btn.setFixedWidth(56)
        del_btn.clicked.connect(lambda: on_delete(self))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.key_edit)
        lay.addWidget(self.value_edit, 1)
        lay.addWidget(del_btn)

        self.key_edit.textChanged.connect(lambda _t: self.apply_echo(show_values))
        self.apply_echo(show_values)

    def apply_echo(self, show_values: bool):
        secret = is_secret_key(self.key_edit.text().strip())
        mode = (QLineEdit.EchoMode.Normal if show_values or not secret
                else QLineEdit.EchoMode.Password)
        self.value_edit.setEchoMode(mode)

    def key(self) -> str:
        return self.key_edit.text().strip()

    def value(self) -> str:
        return self.value_edit.text()


class EnvEditorDialog(QDialog):
    def __init__(self, ctx: AppContext, repo: RepoMeta, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.repo = repo
        self.path = f"{repo.path.rstrip('/')}/.env"
        self.saved = False
        self.loaded_from = ""  # 내 PC에서 불러온 파일 경로(있으면)
        self.setWindowTitle(f"환경설정 — {repo.name}")
        self.setMinimumWidth(560)

        palette = current_palette(ctx.store.settings.theme)

        # 원본을 통째로 들고 있다가 저장할 때 이 위에 덮어쓴다 - 주석과 모르는
        # 줄을 보존하기 위해서다(EnvFile이 그 역할을 한다).
        self.env = EnvFile.load(ctx.runner, self.path)

        path_label = QLabel(self.path)
        path_label.setStyleSheet(f"color: {palette['muted']}; font-size: 11px;")

        self.port_edit = QLineEdit(self.env.get(HOST_PORT_KEY))
        self.port_edit.setPlaceholderText("예: 8001 (비우면 compose 기본값)")
        port_hint = QLabel(PORT_HINT)
        port_hint.setWordWrap(True)
        port_hint.setStyleSheet(f"color: {palette['muted']}; font-size: 11px;")

        self.show_chk = QCheckBox("값 표시")
        self.show_chk.toggled.connect(self._apply_echo)

        self.rows: list[_EnvRow] = []
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(4)
        rows_widget = QWidget()
        rows_widget.setLayout(self.rows_box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(rows_widget)
        scroll.setMinimumHeight(180)

        for key, value in self.env.items():
            if key == HOST_PORT_KEY:
                continue  # 전용 입력칸에서 다룬다
            self._add_row(key, value)

        add_btn = QPushButton("항목 추가")
        add_btn.clicked.connect(lambda: self._add_row("", ""))

        # 내 PC에 있는 .env를 그대로 올리는 경로. SSH 대상에서는 원격 파일을
        # 고를 수 없으므로 QFileDialog는 항상 이 PC를 본다 - 그게 의도다.
        self.upload_btn = QPushButton("파일에서 불러오기")
        self.upload_btn.setToolTip(
            "내 PC의 .env 파일을 읽어 이 화면을 채웁니다. "
            "저장을 눌러야 대상 머신에 반영됩니다.")
        self.upload_btn.clicked.connect(self._load_from_file)

        self.err = QLabel()
        self.err.setStyleSheet(f"color: {palette['danger']};")
        self.err.setWordWrap(True)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Save).setText("저장")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow("파일", path_label)
        form.addRow("호스트 포트", self.port_edit)
        form.addRow("", port_hint)

        top = QHBoxLayout()
        top.addWidget(QLabel("환경변수"))
        top.addStretch(1)
        top.addWidget(self.show_chk)
        top.addWidget(self.upload_btn)
        top.addWidget(add_btn)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addLayout(top)
        root.addWidget(scroll, 1)
        root.addWidget(self.err)
        root.addWidget(btns)

        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

    # ---- 행 관리 ----
    def _add_row(self, key: str, value: str):
        row = _EnvRow(key, value, self._delete_row, self.show_chk.isChecked())
        self.rows.append(row)
        self.rows_box.addWidget(row)

    def _delete_row(self, row: _EnvRow):
        row.deleted = True
        row.setParent(None)
        self.rows = [r for r in self.rows if r is not row]

    def _apply_echo(self, show: bool):
        for row in self.rows:
            row.apply_echo(show)

    # ---- 내 PC의 .env 올리기 ----
    def _load_from_file(self):
        path, _filter = QFileDialog.getOpenFileName(
            self, "불러올 .env 파일", "",
            "환경변수 파일 (*.env .env* *.txt);;모든 파일 (*)")
        if not path:
            return
        try:
            # .env는 사람이 손으로 쓰는 파일이라 인코딩이 제각각이다. UTF-8이
            # 아니면 치환해서라도 읽는다 - 여기서 실패해 아무것도 못 하는 것보다
            # 값이 보이는 편이 낫고, 저장은 항상 UTF-8로 쓴다.
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as ex:
            QMessageBox.warning(self, "읽기 실패", str(ex))
            return

        incoming = EnvFile(text)
        items = incoming.items()
        if not items:
            QMessageBox.information(
                self, "비어 있음",
                "KEY=VALUE 형식의 줄을 찾지 못했습니다. 다른 파일인지 확인해 주세요.")
            return

        if self.rows or self.port_edit.text().strip():
            ok = QMessageBox.question(
                self, "불러오기",
                f"{len(items)}개 항목을 읽었습니다.\n"
                "지금 화면의 내용을 이 파일로 대체할까요?\n"
                "(저장을 누르기 전까지 대상 머신은 바뀌지 않습니다)")
            if ok != QMessageBox.StandardButton.Yes:
                return

        for row in list(self.rows):
            self._delete_row(row)
        self.port_edit.setText(incoming.get(HOST_PORT_KEY))
        for key, value in items:
            if key == HOST_PORT_KEY:
                continue
            self._add_row(key, value)
        self.err.setText("")
        self.loaded_from = path

    # ---- 저장 ----
    def _collect(self) -> tuple[list[tuple[str, str]], str]:
        """화면의 키-값을 모은다. (항목들, 오류 메시지)"""
        items: list[tuple[str, str]] = []
        seen: set[str] = set()
        for row in self.rows:
            key = row.key()
            if not key and not row.value():
                continue  # 빈 줄은 그냥 무시
            err = validate_env_key(key)
            if err:
                return [], f"'{key or '(빈 이름)'}': {err}"
            if key == HOST_PORT_KEY:
                return [], f"{HOST_PORT_KEY}는 위의 '호스트 포트' 칸에서 설정하세요"
            if key in seen:
                return [], f"'{key}'가 중복됩니다"
            seen.add(key)
            items.append((key, row.value()))
        return items, ""

    def _on_save(self):
        port = self.port_edit.text().strip()
        if port:
            err = validate_port(port)
            if err:
                self.err.setText(f"호스트 포트: {err}")
                return

        items, err = self._collect()
        if err:
            self.err.setText(err)
            return

        # 화면에서 지운 키는 파일에서도 지운다. 원본에 있었지만 지금 목록에
        # 없는 키가 그 대상이다(주석은 EnvFile.remove가 남겨둔다).
        current_keys = {k for k, _ in items}
        if port:
            current_keys.add(HOST_PORT_KEY)
        for key, _value in self.env.items():
            if key not in current_keys:
                self.env.remove(key)

        if port:
            self.env.set(HOST_PORT_KEY, port)
        for key, value in items:
            self.env.set(key, value)

        try:
            self.ctx.runner.write_file(self.path, self.env.to_text())
        except Exception as ex:
            self.err.setText(f"저장 실패: {ex}")
            return

        self.saved = True
        QMessageBox.information(
            self, "저장됨",
            "환경설정을 저장했습니다.\n"
            "이미 실행 중인 서버라면 껐다 켜야 새 값이 적용됩니다.")
        self.accept()
