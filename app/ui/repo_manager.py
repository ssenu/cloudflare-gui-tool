"""프로젝트(클론된 Git 저장소) 관리 다이얼로그."""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView,
                             QLabel, QMenu, QMessageBox, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QWidget)

from app.context import AppContext
from app.core.git_repo import (STATE_CLONING, STATE_FAILED, STATE_NONE,
                               STATE_READY, GitClient, clone_state)
from app.core.run_registry import RunRegistry
from app.core.store import RepoMeta
from app.ui.icons import make_icon
from app.ui.theme import STATE_COLORS, current_palette
from app.ui.widgets import danger_menu_action
from app.ui.winutil import apply_titlebar_theme
from app.core.process_mgr import TunnelState

STATE_LABELS = {
    STATE_CLONING: "클론 중",
    STATE_READY: "준비됨",
    STATE_FAILED: "실패",
    STATE_NONE: "-",
}

STATE_DOT_COLORS = {
    STATE_CLONING: STATE_COLORS[TunnelState.STARTING],
    STATE_READY: STATE_COLORS[TunnelState.RUNNING],
    STATE_FAILED: STATE_COLORS[TunnelState.ERROR],
    STATE_NONE: "#888888",
}

COLS = ["", "이름", "URL", "경로", "커밋", ""]
MENU_COL = 5  # 프로젝트별 동작(⋮). 터널 라우트 행과 같은 방식으로 통일한다.


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
        # clone_state()가 PID 재사용 대조 결과와 "ready 확정" 여부를 이
        # dict에 쌓는다. RepoManagerDialog가 살아있는 동안(여러 틱에 걸쳐)
        # 계속 재사용해야 캐싱 효과(원격 왕복 감소)가 있다.
        self._state_cache: dict[str, dict] = {}

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
        header.setSectionResizeMode(MENU_COL, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(MENU_COL, 34)
        # 선택 행을 눈에 띄게 만드는 색은 theme.py의 QTableWidget::item:selected가
        # 담당한다. 여기서는 행 전체가 함께 선택되도록만 맞춘다.
        self.table.itemSelectionChanged.connect(self._on_selection_changed)

        add_btn = QPushButton("클론 추가")
        add_btn.setObjectName("primary")
        add_btn.clicked.connect(self._add_clone)

        # 프로젝트별 동작은 각 행의 ⋮ 메뉴로 옮겼다. 예전처럼 아래쪽 버튼을
        # 쓰면 "무엇이 선택됐는지" 확신이 없는 상태에서 삭제를 누르게 된다.
        self.hint_label = QLabel("각 줄의 ⋮ 에서 환경설정·로그·업데이트·삭제를 할 수 있습니다")
        self.hint_label.setStyleSheet(f"color: {palette['muted']}; font-size: 11px;")

        btns = QHBoxLayout()
        btns.addWidget(add_btn)
        btns.addWidget(self.hint_label)
        btns.addStretch(1)

        self.selected_label = QLabel()
        self.selected_label.setStyleSheet(
            f"color: {palette['accent']}; font-size: 12px; font-weight: 600;")

        root = QVBoxLayout(self)
        root.addWidget(title)
        root.addWidget(self.selected_label)
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

    def _cached_commit(self, repo: RepoMeta) -> str:
        """clone_state()가 ready 확정 시점에 이미 조회해 캐시에 남겨둔 커밋을
        재사용한다 - 표시를 위해 원격에 또 왕복하지 않는다."""
        return self._state_cache.get(repo.id, {}).get("commit", "")

    def _reload(self):
        repos = self._repos()
        self.table.setRowCount(len(repos))
        for i, repo in enumerate(repos):
            state = clone_state(self.reg, self.git, repo, self._state_cache)
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

            commit = self._cached_commit(repo) if state == STATE_READY else ""
            self.table.setItem(i, 4, QTableWidgetItem(commit))

            self.table.setCellWidget(i, MENU_COL, self._menu_widget(repo))
        self._on_selection_changed()

    # ---- 프로젝트별 메뉴 ----
    def _menu_widget(self, repo: RepoMeta) -> QWidget:
        palette = current_palette(self.ctx.store.settings.theme)
        btn = QPushButton()
        btn.setIcon(make_icon("dots", palette["text"]))
        btn.setFixedWidth(28)
        btn.setToolTip(f"{repo.name} 동작")
        btn.clicked.connect(lambda: self._open_menu(btn, repo))
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(btn)
        return wrap

    def _open_menu(self, anchor: QPushButton, repo: RepoMeta):
        # 메뉴를 연 줄을 선택 상태로 만든다 - 어떤 프로젝트에 대한 동작인지
        # 화면에서도 분명해진다.
        self._select_repo(repo)
        palette = current_palette(self.ctx.store.settings.theme)
        menu = QMenu(self)
        for text, handler in (("환경설정", self._edit_env),
                              ("로그", self._show_log),
                              ("업데이트 (git pull)", self._update_repo)):
            action = QAction(text, menu)
            action.triggered.connect(lambda _c=False, h=handler: h(repo))
            menu.addAction(action)
        menu.addSeparator()
        remove_action = QAction("목록에서 제거", menu)
        remove_action.triggered.connect(lambda: self._remove_repo(repo))
        menu.addAction(remove_action)
        danger_menu_action(menu, "폴더까지 삭제", palette,
                           lambda: self._delete_repo(repo))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _select_repo(self, repo: RepoMeta):
        for i, r in enumerate(self._repos()):
            if r.id == repo.id:
                self.table.selectRow(i)
                return

    def _on_selection_changed(self):
        repo = self._selected_repo()
        self.selected_label.setText(f"선택됨: {repo.name}" if repo else "")

    def _tick(self):
        repos = self._repos()
        for i, repo in enumerate(repos):
            if i >= self.table.rowCount():
                break
            # I2: clone_state()가 이미 ready로 확정된 유닛은 원격 조회 없이
            # 즉시 캐시값을 반환하므로, 안정된(ready) 항목은 사실상 폴링
            # 비용이 0이다 - 매 틱 부르는 것 자체가 안전하다.
            state = clone_state(self.reg, self.git, repo, self._state_cache)
            prev = self._last_state.get(repo.id)
            if state != prev:
                self._last_state[repo.id] = state
                self.table.setCellWidget(i, 0, self._dot_widget(STATE_DOT_COLORS[state]))
                if state == STATE_READY:
                    self.table.setItem(i, 4, QTableWidgetItem(self._cached_commit(repo)))

    # ---- 동작 ----
    def _add_clone(self):
        from app.ui.repo_clone_dialog import RepoCloneDialog
        existing = [r.name for r in self._repos()]
        dlg = RepoCloneDialog(self.ctx, existing, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._reload()

    def _is_cloning(self, repo: RepoMeta) -> bool:
        return clone_state(self.reg, self.git, repo, self._state_cache) == STATE_CLONING

    def _edit_env(self, repo: RepoMeta):
        from app.ui.env_editor import EnvEditorDialog
        dlg = EnvEditorDialog(self.ctx, repo, self)
        dlg.exec()

    def _update_repo(self, repo: RepoMeta):
        if self._is_cloning(repo):
            QMessageBox.warning(self, "업데이트 불가",
                                "클론이 진행 중입니다. 먼저 완료되기를 기다리세요.")
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

    def _show_log(self, repo: RepoMeta):
        from app.ui.log_viewer import LogViewer
        unit = self.reg.unit_clone(repo.id)
        viewer = LogViewer(self.ctx, repo.name, {"클론/업데이트": self.reg.log_path(unit)}, self)
        viewer.show()

    def _remove_repo(self, repo: RepoMeta):
        ok = QMessageBox.question(
            self, "목록에서 제거",
            f"'{repo.name}'을(를) 목록에서만 제거할까요?\n대상 머신의 폴더는 그대로 남습니다.")
        if ok != QMessageBox.StandardButton.Yes:
            return
        self._remove_from_settings(repo)
        self._reload()

    def _delete_repo(self, repo: RepoMeta):
        if self._is_cloning(repo):
            QMessageBox.warning(self, "삭제 불가",
                                "클론이 진행 중입니다. 먼저 완료되기를 기다리세요.")
            return
        ok = QMessageBox.question(
            self, "폴더까지 삭제",
            f"'{repo.path}'\n\n이 폴더가 대상 머신에서 영구 삭제됩니다. 계속할까요?")
        if ok != QMessageBox.StandardButton.Yes:
            return
        try:
            self.ctx.runner.remove_tree(repo.path)
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
        self._state_cache.pop(repo.id, None)
        self._last_state.pop(repo.id, None)
        # 클론/업데이트 로그·PID 파일도 정리한다
        unit = self.reg.unit_clone(repo.id)
        self.reg.clear_pid(unit)
        for path in (self.reg.log_path(unit), self.reg.log_path(unit) + ".1"):
            try:
                if self.ctx.runner.file_exists(path):
                    self.ctx.runner.remove_file(path)
            except Exception:
                pass
