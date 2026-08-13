"""프로젝트(클론된 Git 저장소) 관리 다이얼로그.

표(QTableWidget) 대신 카드 목록을 쓴다. 표에서는 상태 동그라미와 ⋮ 버튼을
셀 위젯으로 얹어야 했는데, 그것들이 격자 위에 얹힌 별개의 물체처럼 떠 보였다.
카드로 묶으면 동그라미가 이름 앞에 붙은 상태 표시로 읽히고, 메인 창의 터널
카드와도 같은 시각 언어를 쓰게 된다.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QMenu,
                             QMessageBox, QPushButton, QScrollArea, QVBoxLayout,
                             QWidget)

from app.context import AppContext
from app.core.git_repo import (STATE_CLONING, STATE_FAILED, STATE_NONE,
                               STATE_READY, GitClient, clone_state, deploy_argv)
from app.core.process_mgr import TunnelState
from app.core.run_registry import RunRegistry
from app.core.store import RepoMeta
from app.ui.icons import make_icon
from app.ui.repo_picker import COMPOSE_NAMES
from app.ui.theme import STATE_COLORS, current_palette
from app.ui.widgets import danger_menu_action
from app.ui.winutil import apply_titlebar_theme

STATE_LABELS = {
    STATE_CLONING: "클론 중",
    STATE_READY: "준비됨",
    STATE_FAILED: "실패",
    STATE_NONE: "없음",
}

# git pull도 클론과 같은 유닛(clone-<id>)으로 돌기 때문에 상태값은 CLONING으로
# 같다. 사용자에게는 다른 일이므로 문구만 바꿔 보여준다.
PULLING_LABEL = "업데이트 중"
DEPLOYING_LABEL = "배포 중"

STATE_DOT_COLORS = {
    STATE_CLONING: STATE_COLORS[TunnelState.STARTING],  # 주황: 진행 중
    STATE_READY: STATE_COLORS[TunnelState.RUNNING],
    STATE_FAILED: STATE_COLORS[TunnelState.ERROR],
    STATE_NONE: "#888888",
}

DOT_SIZE = 10
EMPTY_TEXT = ("아직 클론한 프로젝트가 없습니다.\n"
              "'클론 추가'로 Git 주소를 넣어 대상 머신에 내려받으세요.")


def _elide(label: QLabel, text: str, width: int) -> None:
    label.setText(label.fontMetrics().elidedText(
        text, Qt.TextElideMode.ElideMiddle, width))
    label.setToolTip(text)


class RepoCard(QFrame):
    """프로젝트 한 개. 첫 줄에 상태+이름+동작, 둘째 줄에 주소/경로."""

    NAME_WIDTH = 260
    META_WIDTH = 620

    def __init__(self, dialog: "RepoManagerDialog", repo: RepoMeta):
        super().__init__()
        self.setObjectName("card")
        self.dialog = dialog
        self.repo = repo
        palette = current_palette(dialog.ctx.store.settings.theme)
        self._palette = palette

        self.dot = QLabel()
        self.dot.setFixedSize(DOT_SIZE, DOT_SIZE)

        self.name_label = QLabel()
        self.name_label.setStyleSheet("font-size: 14px; font-weight: 600;")
        _elide(self.name_label, repo.name, self.NAME_WIDTH)

        self.state_label = QLabel()
        self.state_label.setStyleSheet(f"color: {palette['muted']}; font-size: 12px;")

        menu_btn = QPushButton()
        menu_btn.setIcon(make_icon("dots", palette["text"]))
        menu_btn.setFixedWidth(28)
        menu_btn.setToolTip(f"{repo.name} 동작")
        menu_btn.clicked.connect(lambda: dialog._open_menu(menu_btn, repo))

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self.dot)
        head.addWidget(self.name_label)
        head.addStretch(1)
        head.addWidget(self.state_label)
        head.addWidget(menu_btn)

        self.meta_label = QLabel()
        self.meta_label.setStyleSheet(f"color: {palette['muted']}; font-size: 11px;")
        _elide(self.meta_label, f"{repo.url}   ·   {repo.path}", self.META_WIDTH)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 10, 10)
        lay.setSpacing(2)
        lay.addLayout(head)
        # 동그라미(10px) 폭 + 간격만큼 들여써서 이름 아래에 정렬한다.
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(DOT_SIZE + 8, 0, 0, 0)
        meta_row.addWidget(self.meta_label)
        meta_row.addStretch(1)
        lay.addLayout(meta_row)

    def update_state(self, state: str, commit: str, busy_label: str = "") -> None:
        color = STATE_DOT_COLORS.get(state, STATE_DOT_COLORS[STATE_NONE])
        self.dot.setStyleSheet(
            f"background: {color}; border-radius: {DOT_SIZE // 2}px;")

        text = STATE_LABELS.get(state, state)
        if state == STATE_CLONING and busy_label:
            text = busy_label
        if state == STATE_READY and commit:
            text = f"{text} · {commit[:7]}"
        self.state_label.setText(text)
        # 진행 중일 때는 문구도 같은 색으로 올려 눈에 띄게 한다.
        emphasize = state in (STATE_CLONING, STATE_FAILED)
        self.state_label.setStyleSheet(
            f"color: {color if emphasize else self._palette['muted']};"
            " font-size: 12px;")


class RepoManagerDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("프로젝트")
        self.setMinimumSize(700, 420)
        palette = current_palette(ctx.store.settings.theme)

        self.reg = RunRegistry(ctx.runner)
        self.git = GitClient(ctx.runner)
        self.cards: list[RepoCard] = []
        # clone_state()가 PID 재사용 대조 결과와 "ready 확정" 여부를 이
        # dict에 쌓는다. 다이얼로그가 살아있는 동안 계속 재사용해야 캐싱
        # 효과(원격 왕복 감소)가 있다.
        self._state_cache: dict[str, dict] = {}
        # 지금 git pull을 돌린 프로젝트. 상태값은 클론과 같아서 문구 구분용이다.
        self._pulling: set[str] = set()
        # 배포(pull + 재시작) 중인 프로젝트. 상태값은 같지만 문구를 구분한다.
        self._deploying: set[str] = set()

        target_name = "이 PC" if not ctx.is_remote else ctx.runner.name.split(":", 1)[-1]
        title = QLabel(f"프로젝트 — 대상: {target_name}")
        title.setStyleSheet("font-size: 15px; font-weight: 600;")

        add_btn = QPushButton("클론 추가")
        add_btn.setObjectName("primary")
        add_btn.setIcon(make_icon("plus", palette["on_accent"]))
        add_btn.setFixedHeight(32)
        add_btn.clicked.connect(self._add_clone)

        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(add_btn)

        hint = QLabel("각 프로젝트의 ⋮ 에서 환경설정 · 로그 · 업데이트 · 삭제를 할 수 있습니다")
        hint.setStyleSheet(f"color: {palette['muted']}; font-size: 11px;")

        self.list_lay = QVBoxLayout()
        self.list_lay.setSpacing(8)
        self.empty_label = QLabel(EMPTY_TEXT)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet(
            f"color: {palette['muted']}; font-size: 13px; padding: 40px 0;")
        self.list_lay.addWidget(self.empty_label)
        self.list_lay.addStretch(1)
        inner = QWidget()
        inner.setLayout(self.list_lay)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)

        root = QVBoxLayout(self)
        root.addLayout(head)
        root.addWidget(hint)
        root.addWidget(scroll, 1)

        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

        self._reload()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self.finished.connect(lambda _r: self._timer.stop())

    # ---- 데이터 ----
    def _repos(self) -> list[RepoMeta]:
        return self.ctx.store.settings.repos_for(self.ctx.runner.name)

    def _cached_commit(self, repo: RepoMeta) -> str:
        """clone_state()가 ready 확정 시점에 이미 조회해 캐시에 남겨둔 커밋을
        재사용한다 - 표시를 위해 원격에 또 왕복하지 않는다."""
        return self._state_cache.get(repo.id, {}).get("commit", "")

    def _reload(self):
        for card in self.cards:
            card.setParent(None)
        self.cards.clear()
        for repo in self._repos():
            card = RepoCard(self, repo)
            self.list_lay.insertWidget(self.list_lay.count() - 1, card)
            self.cards.append(card)
        self.empty_label.setVisible(not self.cards)
        self._tick()

    def _tick(self):
        for card in self.cards:
            # I2: clone_state()는 ready로 확정된 유닛을 캐시에서 즉시 반환하므로
            # 안정된 항목의 폴링 비용은 사실상 0이다.
            state = clone_state(self.reg, self.git, card.repo, self._state_cache)
            if state != STATE_CLONING:
                self._pulling.discard(card.repo.id)
                self._deploying.discard(card.repo.id)
            busy = ""
            if card.repo.id in self._deploying:
                busy = DEPLOYING_LABEL
            elif card.repo.id in self._pulling:
                busy = PULLING_LABEL
            card.update_state(state, self._cached_commit(card.repo), busy)

    # ---- 프로젝트별 메뉴 ----
    def _open_menu(self, anchor: QPushButton, repo: RepoMeta):
        palette = current_palette(self.ctx.store.settings.theme)
        menu = QMenu(self)
        for text, handler in (("배포 (pull + 재시작)", self._deploy_repo),
                              ("환경설정", self._edit_env),
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

    def _deploy_repo(self, repo: RepoMeta):
        """최신 코드를 받고 컨테이너를 다시 띄운다(한 번에).

        compose 파일이 없으면 pull만 한다 - 그때는 '업데이트'와 같다.
        """
        if self._is_cloning(repo):
            QMessageBox.warning(self, "배포 불가",
                                "이미 다른 작업이 진행 중입니다. 끝난 뒤 다시 시도하세요.")
            return
        if not self.git.is_repo(repo.path):
            QMessageBox.warning(self, "배포 불가", "아직 클론되지 않았거나 저장소가 아닙니다")
            return

        compose = any(self.ctx.runner.file_exists(f"{repo.path}/{name}")
                      for name in COMPOSE_NAMES)
        if not compose:
            ok = QMessageBox.question(
                self, "compose 파일 없음",
                "docker-compose.yml을 찾지 못했습니다.\n"
                "코드만 받아올까요? (서버는 다시 뜨지 않습니다)")
            if ok != QMessageBox.StandardButton.Yes:
                return

        unit = self.reg.unit_clone(repo.id)
        try:
            self.reg.runner.ensure_dir(self.reg.run_dir())
            self.reg.rotate_log_if_big(unit)
            pid = self.reg.runner.spawn_detached(
                deploy_argv(repo.path, compose), None, self.reg.log_path(unit))
            self.reg.write_pid(unit, pid, cmd="sh")
        except Exception as ex:
            QMessageBox.critical(self, "배포 실패", str(ex))
            return
        self._state_cache.get(repo.id, {}).pop("ready", None)
        self._deploying.add(repo.id)
        self._pulling.discard(repo.id)
        self._tick()

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
            self.reg.rotate_log_if_big(unit)
            pid = self.reg.runner.spawn_detached(argv, None, self.reg.log_path(unit))
            # 클론과 달리 PID를 기록하지 않아서, pull이 도는 동안 상태 판정이
            # 그 프로세스를 전혀 보지 못했다(그래서 진행 표시가 안 떴다).
            # 클론과 같은 방식으로 기록해 같은 상태 기계를 타게 한다.
            self.reg.write_pid(unit, pid, cmd="git")
        except Exception as ex:
            QMessageBox.critical(self, "업데이트 실패", str(ex))
            return
        # ready로 확정된 캐시를 비워야 pull이 도는 동안 진행 중(주황)으로
        # 보이고, 끝난 뒤에는 바뀐 커밋을 다시 읽는다. 비우지 않으면 캐시가
        # 계속 "준비됨 + 예전 커밋"을 돌려준다.
        self._state_cache.get(repo.id, {}).pop("ready", None)
        self._pulling.add(repo.id)
        self._tick()

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
        self._pulling.discard(repo.id)
        # 클론/업데이트 로그·PID 파일도 정리한다
        unit = self.reg.unit_clone(repo.id)
        self.reg.clear_pid(unit)
        for path in (self.reg.log_path(unit), self.reg.log_path(unit) + ".1"):
            try:
                if self.ctx.runner.file_exists(path):
                    self.ctx.runner.remove_file(path)
            except Exception:
                pass
