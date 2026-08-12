"""Git 저장소 클론 다이얼로그.

검증 후 클론을 시작(spawn_detached)하고 곧바로 다이얼로그를 닫아 프로젝트
목록(RepoManagerDialog)으로 돌아간다 - 클론은 몇 분 걸릴 수 있어 진행 상황은
목록 화면의 상태 점과 "로그" 버튼으로 확인하는 편이 자연스럽다(다이얼로그
안에 로그를 또 띄우면 창이 두 겹이 되어 오히려 산만하다).
"""
from __future__ import annotations

from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel,
                             QLineEdit, QMessageBox, QVBoxLayout)

from app.context import AppContext
from app.core.git_repo import (clone_argv, join_path, parse_repo_name,
                               validate_repo_name, validate_repo_url)
from app.core.run_registry import RunRegistry
from app.core.store import RepoMeta, new_route_id
from app.ui.theme import current_palette
from app.ui.winutil import apply_titlebar_theme


class RepoCloneDialog(QDialog):
    def __init__(self, ctx: AppContext, existing_names: list[str], parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.existing_names = existing_names
        self.created_repo: RepoMeta | None = None
        self.setWindowTitle("클론 추가")
        self.setMinimumWidth(460)

        palette = current_palette(ctx.store.settings.theme)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(
            "예: https://github.com/사용자/저장소.git 또는 git@github.com:사용자/저장소.git")
        self.url_edit.textChanged.connect(self._on_url_changed)

        self.name_edit = QLineEdit()
        self._name_auto = True
        self.name_edit.textEdited.connect(self._on_name_edited)

        self.repo_root = ctx.store.settings.repo_root
        self.path_preview = QLabel(join_path(self.repo_root, ""))
        self.path_preview.setStyleSheet(f"color: {palette['muted']};")

        self.branch_edit = QLineEdit()
        self.branch_edit.setPlaceholderText("비우면 기본 브랜치")

        hint = QLabel(
            "비공개 저장소는 대상 서버에 배포 키(Deploy Key)가 등록되어 있어야 하며, "
            "이때는 git@ 형식 주소를 사용하세요.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {palette['muted']}; font-size: 11px;")

        self.err_label = QLabel()
        self.err_label.setStyleSheet(f"color: {palette['danger']};")
        self.err_label.setWordWrap(True)

        form = QFormLayout()
        form.addRow(hint)
        form.addRow("Git 주소", self.url_edit)
        form.addRow("이름", self.name_edit)
        form.addRow("설치 위치", self.path_preview)
        form.addRow("브랜치", self.branch_edit)
        form.addRow(self.err_label)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        start_btn = btns.addButton("클론 시작", QDialogButtonBox.ButtonRole.AcceptRole)
        start_btn.setObjectName("primary")
        btns.accepted.connect(self._on_start)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

        self.setLayout(form)
        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

    # ---- 이름 자동 채움 ----
    def _on_url_changed(self, text: str):
        if self._name_auto:
            self.name_edit.setText(parse_repo_name(text))
        self._update_preview()

    def _on_name_edited(self, _text: str):
        self._name_auto = False
        self._update_preview()

    def _update_preview(self):
        name = self.name_edit.text().strip()
        self.path_preview.setText(join_path(self.repo_root, name))

    # ---- 검증 ----
    def _validate(self) -> str:
        url = self.url_edit.text().strip()
        e = validate_repo_url(url)
        if e:
            return e
        name = self.name_edit.text().strip()
        e = validate_repo_name(name, self.existing_names)
        if e:
            return e
        return ""

    # ---- 클론 시작 ----
    def _on_start(self):
        err = self._validate()
        if err:
            self.err_label.setText(err)
            return
        self.err_label.clear()

        url = self.url_edit.text().strip()
        name = self.name_edit.text().strip()
        branch = self.branch_edit.text().strip()
        path = join_path(self.repo_root, name)

        repo = RepoMeta(id=new_route_id(), name=name, url=url, path=path, branch=branch)

        reg = RunRegistry(self.ctx.runner)
        unit = reg.unit_clone(repo.id)
        try:
            # SSH 대상은 write_file/spawn_detached가 상위 디렉터리를 자동
            # 생성해주지 않는다(SFTP는 로컬 write_file과 달리 mkdir을 하지
            # 않음) - run 디렉터리가 아직 없는 새 대상에서 최초 클론이 로그
            # 리다이렉션 실패로 조용히 죽는 것을 막기 위해 미리 만든다.
            reg.runner.ensure_dir(reg.run_dir())
            reg.rotate_log_if_big(unit)
            log_path = reg.log_path(unit)
            argv = clone_argv(url, path, branch)
            pid = self.ctx.runner.spawn_detached(argv, None, log_path)
            reg.write_pid(unit, pid, cmd="git")
        except Exception as ex:
            QMessageBox.critical(self, "클론 시작 실패", str(ex))
            return

        self.ctx.store.settings.repos_for(self.ctx.runner.name).append(repo)
        self.ctx.store.save()
        self.created_repo = repo
        self.accept()
