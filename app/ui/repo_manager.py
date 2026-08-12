"""프로젝트(클론된 Git 저장소) 관리 다이얼로그."""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView,
                             QLabel, QMessageBox, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from app.context import AppContext
from app.core.git_repo import (STATE_CLONING, STATE_FAILED, STATE_READY,
                               GitClient, clone_state)
from app.core.run_registry import RunRegistry
from app.core.store import RepoMeta
from app.ui.theme import STATE_COLORS, current_palette
from app.ui.winutil import apply_titlebar_theme
from app.core.process_mgr import TunnelState

STATE_LABELS = {
    STATE_CLONING: "클론 중",
    STATE_READY: "준비됨",
    STATE_FAILED: "실패",
    "none": "-",
}

STATE_DOT_COLORS = {
    STATE_CLONING: STATE_COLORS[TunnelState.STARTING],
    STATE_READY: STATE_COLORS[TunnelState.RUNNING],
    STATE_FAILED: STATE_COLORS[TunnelState.ERROR],
    "none": "#888888",
}

COLS = ["", "이름", "URL", "경로", "커밋"]


class RepoManagerDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("프로젝트")
        self.setMinimumSize(720, 420)
        palette = current_palette(ctx.store.settings.theme)

        self.reg = RunRegistry(ctx.runner)
        self.git = GitClient(ctx.runner)
        self._last_state: dict[str, str] = {}

        target_name = "이 PC" if not ctx.is_remote else ctx.runner.name.split(":", 1)[-1]
        title = QLabel(f"프로젝트 — 대상: {target_name}")
        title.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {palette['text']};")

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 28)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        add_btn = QPushButton("클론 추가")
        add_btn.setObjectName("primary")
        add_btn.clicked.connect(self._add_clone)
        update_btn = QPushButton("업데이트")
        update_btn.setToolTip("선택한 프로젝트를 git pull로 업데이트합니다")
        update_btn.clicked.connect(self._update_selected)
        log_btn = QPushButton("로그")
        log_btn.clicked.connect(self._show_log)
        remove_btn = QPushButton("목록에서 제거")
        remove_btn.clicked.connect(self._remove_selected)
        delete_btn = QPushButton("폴더까지 삭제")
        delete_btn.setObjectName("danger")
        delete_btn.clicked.connect(self._delete_selected)

        btns = QHBoxLayout()
        btns.addWidget(add_btn)
        btns.addWidget(update_btn)
        btns.addWidget(log_btn)
        btns.addStretch(1)
        btns.addWidget(remove_btn)
        btns.addWidget(delete_btn)

        root = QVBoxLayout(self)
        root.addWidget(title)
        root.addWidget(self.table, 1)
        root.addLayout(btns)

        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

        self._reload()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self.finished.connect(lambda _r: self._timer.stop())

    # ---- 데이터 ----
    def _repos(self) -> list[RepoMeta]:
        return self.ctx.store.settings.repos_for(self.ctx.runner.name)

    def _selected_repo(self) -> RepoMeta | None:
        row = self.table.currentRow()
        repos = self._repos()
        if 0 <= row < len(repos):
            return repos[row]
        return None

    def _dot_widget(self, color: str) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dot = QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background: {color}; border-radius: 5px;")
        lay.addWidget(dot)
        return w

    def _reload(self):
        repos = self._repos()
        self.table.setRowCount(len(repos))
        for i, repo in enumerate(repos):
            state = clone_state(self.reg, self.git, repo)
            self._last_state[repo.id] = state
            self.table.setCellWidget(i, 0, self._dot_widget(STATE_DOT_COLORS[state]))

            name_item = QTableWidgetItem(repo.name)
            self.table.setItem(i, 1, name_item)

            url_item = QTableWidgetItem(repo.url)
            url_item.setToolTip(repo.url)
            self.table.setItem(i, 2, url_item)

            path_item = QTableWidgetItem(repo.path)
            path_item.setToolTip(repo.path)
            self.table.setItem(i, 3, path_item)

            commit = self.git.current_commit(repo.path) if state == STATE_READY else ""
            self.table.setItem(i, 4, QTableWidgetItem(commit))

    def _tick(self):
        repos = self._repos()
        for i, repo in enumerate(repos):
            if i >= self.table.rowCount():
                break
            state = clone_state(self.reg, self.git, repo)
            prev = self._last_state.get(repo.id)
            if state != prev:
                self._last_state[repo.id] = state
                self.table.setCellWidget(i, 0, self._dot_widget(STATE_DOT_COLORS[state]))
                if state == STATE_READY:
                    commit = self.git.current_commit(repo.path)
                    self.table.setItem(i, 4, QTableWidgetItem(commit))

    # ---- 동작 ----
    def _add_clone(self):
        from app.ui.repo_clone_dialog import RepoCloneDialog
        existing = [r.name for r in self._repos()]
        dlg = RepoCloneDialog(self.ctx, existing, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._reload()

    def _update_selected(self):
        repo = self._selected_repo()
        if repo is None:
            QMessageBox.information(self, "업데이트", "먼저 프로젝트를 선택하세요")
            return
        if not self.git.is_repo(repo.path):
            QMessageBox.warning(self, "업데이트 불가", "아직 클론되지 않았거나 저장소가 아닙니다")
            return
        unit = self.reg.unit_clone(repo.id)
        argv = self.git.pull_argv(repo.path)
        try:
            self.reg.runner.ensure_dir(self.reg.run_dir())
            self.reg.runner.spawn_detached(argv, None, self.reg.log_path(unit))
        except Exception as ex:
            QMessageBox.critical(self, "업데이트 실패", str(ex))
            return
        self._reload()

    def _show_log(self):
        repo = self._selected_repo()
        if repo is None:
            QMessageBox.information(self, "로그", "먼저 프로젝트를 선택하세요")
            return
        from app.ui.log_viewer import LogViewer
        unit = self.reg.unit_clone(repo.id)
        viewer = LogViewer(self.ctx, repo.name, {"클론/업데이트": self.reg.log_path(unit)}, self)
        viewer.show()

    def _remove_selected(self):
        repo = self._selected_repo()
        if repo is None:
            return
        ok = QMessageBox.question(
            self, "목록에서 제거",
            f"'{repo.name}'을(를) 목록에서만 제거할까요?\n대상 머신의 폴더는 그대로 남습니다.")
        if ok != QMessageBox.StandardButton.Yes:
            return
        self._remove_from_settings(repo)
        self._reload()

    def _delete_selected(self):
        repo = self._selected_repo()
        if repo is None:
            return
        ok = QMessageBox.question(
            self, "폴더까지 삭제",
            f"'{repo.path}'\n\n이 폴더가 대상 머신에서 영구 삭제됩니다. 계속할까요?")
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            self.ctx.runner.run(["rm", "-rf", repo.path])
        except Exception as ex:
            QMessageBox.critical(self, "삭제 실패", str(ex))
            return
        self._remove_from_settings(repo)
        self._reload()

    def _remove_from_settings(self, repo: RepoMeta):
        repos = self._repos()
        self.ctx.store.settings.repos[self.ctx.runner.name] = [
            r for r in repos if r.id != repo.id]
        self.ctx.store.save()
        # 클론/업데이트 로그·PID 파일도 정리한다
        unit = self.reg.unit_clone(repo.id)
        self.reg.clear_pid(unit)
        for path in (self.reg.log_path(unit), self.reg.log_path(unit) + ".1"):
            try:
                if self.ctx.runner.file_exists(path):
                    self.ctx.runner.remove_file(path)
            except Exception:
                pass
