"""작업 폴더를 고르는 두 버튼(로컬 폴더 / 프로젝트 목록)을 한곳에서 만든다.

이 파일이 생긴 이유: 같은 기능이 route_dialog에는 있고 wizard에는 없어서,
처음 터널을 만들 때는 SSH 대상인데도 내 PC의 폴더 선택창이 열렸다. 두 곳이
따로 구현돼 있으면 한쪽만 고쳐지는 일이 반복되므로 하나로 합친다.

규칙은 대상에 따라 정반대다:
- 로컬 대상: QFileDialog로 내 PC 폴더를 고른다. 프로젝트(클론) 기능은 SSH
  전용이라 고를 목록이 없다.
- SSH 대상: 클론해 둔 프로젝트 목록에서 고른다. QFileDialog는 내 PC만 볼 수
  있어 원격 경로를 고를 수 없다.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (QFileDialog, QHBoxLayout, QInputDialog, QLineEdit,
                             QMessageBox, QPushButton, QWidget)

from app.context import AppContext
from app.core.env_file import HOST_PORT_KEY, EnvFile, compose_host_port
from app.ui.icons import make_icon

COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml")


def detect_service_port(ctx: AppContext, repo_path: str) -> str:
    """프로젝트 폴더에서 호스트 포트를 알아낸다. 못 찾으면 빈 문자열.

    .env의 HOST_PORT가 최우선이다 - compose가 ``${HOST_PORT:-8000}``으로
    적혀 있으면 실제로 열리는 포트는 .env가 정하기 때문이다. .env에 값이
    없을 때만 compose에 박힌 숫자를 본다.
    """
    runner = ctx.runner
    try:
        env = EnvFile.load(runner, f"{repo_path}/.env")
        port = env.get(HOST_PORT_KEY).strip()
        if port.isdigit():
            return port
        for name in COMPOSE_NAMES:
            path = f"{repo_path}/{name}"
            if runner.file_exists(path):
                return compose_host_port(runner.read_file(path))
    except Exception:
        # 포트 자동 채움은 편의 기능이다. 원격 조회가 실패해도 다이얼로그가
        # 열리지 않는 일은 없어야 하므로 조용히 포기한다.
        return ""
    return ""


def pick_repo_path(parent, ctx: AppContext) -> tuple[str, str] | None:
    """클론해 둔 프로젝트를 고른다. (경로, 감지된 포트) 또는 None.

    포트는 못 찾으면 빈 문자열이다.
    """
    repos = ctx.store.settings.repos_for(ctx.runner.name)
    if not repos:
        QMessageBox.information(
            parent, "프로젝트 없음",
            "먼저 상단 '프로젝트' 메뉴에서 저장소를 클론하세요.")
        return None
    names = [f"{r.name} ({r.path})" for r in repos]
    choice, ok = QInputDialog.getItem(
        parent, "프로젝트에서 선택", "작업 폴더로 사용할 프로젝트", names, 0, False)
    if not ok or not choice:
        return None
    repo = repos[names.index(choice)]
    return repo.path, detect_service_port(ctx, repo.path)


class CwdPickerRow(QWidget):
    """작업 폴더 입력칸 옆에 붙는 버튼 줄.

    service_edit를 함께 주면, 프로젝트를 고를 때 그 프로젝트의 포트를 찾아
    ``http://localhost:<포트>``로 서비스 주소까지 채워준다. 사용자가 이미
    직접 고쳐 둔 값은 덮어쓰지 않는다.
    """

    def __init__(self, ctx: AppContext, cwd_edit: QLineEdit,
                 service_edit: QLineEdit | None = None,
                 icon_color: str = "#000000", parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.cwd_edit = cwd_edit
        self.service_edit = service_edit

        self.browse_btn = QPushButton("폴더 선택...")
        self.browse_btn.setIcon(make_icon("folder", icon_color))
        self.browse_btn.clicked.connect(self._browse)

        self.repo_btn = QPushButton("프로젝트에서 선택")
        self.repo_btn.setIcon(make_icon("folder", icon_color))
        self.repo_btn.clicked.connect(self._pick_repo)

        # 대상에 따라 둘 중 하나만 보여준다(위 모듈 주석 참고).
        self.browse_btn.setVisible(not ctx.is_remote)
        self.repo_btn.setVisible(ctx.is_remote)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self.browse_btn)
        lay.addWidget(self.repo_btn)
        lay.addStretch(1)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "작업 폴더")
        if path:
            self.cwd_edit.setText(path)

    def _pick_repo(self):
        picked = pick_repo_path(self, self.ctx)
        if picked is None:
            return
        path, port = picked
        self.cwd_edit.setText(path)
        if port and self.service_edit is not None:
            self.service_edit.setText(f"http://localhost:{port}")
