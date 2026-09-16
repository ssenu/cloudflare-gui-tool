"""작업 폴더 선택 위젯: 로컬/원격에 따라 다른 버튼이 보여야 한다.

이 위젯이 생긴 이유가 곧 회귀 테스트의 대상이다 - 마법사에서는 SSH 대상인데도
내 PC의 폴더 선택창이 열렸다(route_dialog에만 고침이 들어가 있었다).
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QLineEdit

from app.context import AppContext
from app.core.store import RepoMeta, SettingsStore
from app.ui.repo_picker import CwdPickerRow, detect_service_port
from tests.fake_runner import FakeRunner


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def make_ctx(tmp_path, remote: bool):
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    ctx = AppContext(store=store)
    local = FakeRunner(home="/home/fake")
    ctx.local_runner = local
    if remote:
        runner = FakeRunner(home="/home/pi")
        runner.name = "ssh:webPi"
        ctx.runner = runner
    else:
        ctx.runner = local
    return ctx


def test_local_target_shows_folder_dialog_button(qapp, tmp_path):
    ctx = make_ctx(tmp_path, remote=False)
    row = CwdPickerRow(ctx, QLineEdit(), None)
    assert row.browse_btn.isVisible() or not row.browse_btn.isHidden()
    assert row.repo_btn.isHidden()


def test_remote_target_shows_project_button_only(qapp, tmp_path):
    # 핵심 회귀 검증: 원격에서는 내 PC 폴더 선택창을 열 수 없어야 한다.
    ctx = make_ctx(tmp_path, remote=True)
    row = CwdPickerRow(ctx, QLineEdit(), None)
    assert row.browse_btn.isHidden()
    assert not row.repo_btn.isHidden()


def test_wizard_uses_same_picker_on_remote(qapp, tmp_path):
    from app.ui.wizard import TunnelWizard

    ctx = make_ctx(tmp_path, remote=True)
    wiz = TunnelWizard(ctx, [], None)
    wiz._timer.stop()
    assert wiz.cwd_picker.browse_btn.isHidden()
    assert not wiz.cwd_picker.repo_btn.isHidden()


def test_route_dialog_uses_same_picker_on_remote(qapp, tmp_path):
    from app.core.store import TunnelMeta
    from app.ui.route_dialog import RouteDialog

    ctx = make_ctx(tmp_path, remote=True)
    dlg = RouteDialog(ctx, TunnelMeta(name="t1"), None, None)
    assert dlg.cwd_picker.browse_btn.isHidden()
    assert not dlg.cwd_picker.repo_btn.isHidden()


# ---- 포트 자동 감지 ----

def test_detect_port_prefers_env_over_compose(qapp, tmp_path):
    ctx = make_ctx(tmp_path, remote=True)
    ctx.runner.files["/srv/apps/x/.env"] = "HOST_PORT=8003\n"
    ctx.runner.files["/srv/apps/x/docker-compose.yml"] = (
        'services:\n  app:\n    ports:\n      - "127.0.0.1:8000:8000"\n')
    assert detect_service_port(ctx, "/srv/apps/x") == "8003"


def test_detect_port_falls_back_to_compose(qapp, tmp_path):
    ctx = make_ctx(tmp_path, remote=True)
    ctx.runner.files["/srv/apps/x/docker-compose.yml"] = (
        'services:\n  app:\n    ports:\n      - "127.0.0.1:8002:8000"\n')
    assert detect_service_port(ctx, "/srv/apps/x") == "8002"


def test_detect_port_missing_files_is_empty(qapp, tmp_path):
    ctx = make_ctx(tmp_path, remote=True)
    assert detect_service_port(ctx, "/srv/apps/none") == ""


def test_detect_port_survives_runner_failure(qapp, tmp_path):
    """원격 조회가 터져도 다이얼로그는 열려야 한다(편의 기능이므로 조용히 포기)."""
    ctx = make_ctx(tmp_path, remote=True)

    def boom(_path):
        raise OSError("연결 끊김")

    ctx.runner.file_exists = boom
    assert detect_service_port(ctx, "/srv/apps/x") == ""


def test_picked_project_fills_service_port(qapp, tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path, remote=True)
    ctx.store.settings.repos["ssh:webPi"] = [
        RepoMeta(id="r1", name="app", url="git@x:a/b.git", path="/srv/apps/app")]
    ctx.runner.files["/srv/apps/app/.env"] = "HOST_PORT=8001\n"

    cwd_edit, service_edit = QLineEdit(), QLineEdit("http://localhost:8000")
    row = CwdPickerRow(ctx, cwd_edit, service_edit)

    # QInputDialog를 띄우지 않고 첫 프로젝트를 고른 것으로 흉내낸다
    monkeypatch.setattr("app.ui.repo_picker.QInputDialog.getItem",
                        lambda *a, **k: ("app (/srv/apps/app)", True))
    row._pick_repo()

    assert cwd_edit.text() == "/srv/apps/app"
    assert service_edit.text() == "http://localhost:8001"


# ---- 경로 직접 입력 ----
# 원격 대상에서는 "프로젝트에서 선택" 목록에 없는 폴더(예: 클론한 리포의
# 하위 deploy/ 폴더, 앱을 거치지 않고 직접 클론한 리포)를 쓸 방법이 눈에
# 보이지 않았다. 입력칸에 타이핑은 됐지만 그걸 알 수 없었고, 쳐 넣어도 폴더
# 존재 확인과 포트 자동 채움이 없었다.

def _remote_row(tmp_path, monkeypatch, typed: str, exists: bool,
                env_port: str = ""):
    from PyQt6.QtWidgets import QInputDialog
    ctx = make_ctx(tmp_path, remote=True)
    if exists:
        ctx.runner.dirs.add(typed)
        if env_port:
            ctx.runner.files[f"{typed}/.env"] = f"HOST_PORT={env_port}\n"
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: (typed, True)))
    cwd, svc = QLineEdit(), QLineEdit("http://localhost:8000")
    return CwdPickerRow(ctx, cwd, svc), cwd, svc


def test_remote_target_shows_manual_path_button(qapp, tmp_path):
    ctx = make_ctx(tmp_path, remote=True)
    row = CwdPickerRow(ctx, QLineEdit(), None)
    assert not row.manual_btn.isHidden()


def test_local_target_hides_manual_path_button(qapp, tmp_path):
    """로컬은 폴더 선택창이 있고 입력칸도 바로 옆이라 버튼이 하나 더 있을 이유가 없다."""
    ctx = make_ctx(tmp_path, remote=False)
    row = CwdPickerRow(ctx, QLineEdit(), None)
    assert row.manual_btn.isHidden()


def test_manual_path_sets_cwd_and_detects_port(qapp, tmp_path, monkeypatch):
    row, cwd, svc = _remote_row(tmp_path, monkeypatch,
                                "/srv/apps/Woo-MoMo-Project/deploy",
                                exists=True, env_port="8006")

    row._manual()

    assert cwd.text() == "/srv/apps/Woo-MoMo-Project/deploy"
    assert svc.text() == "http://localhost:8006"


def test_manual_path_strips_whitespace(qapp, tmp_path, monkeypatch):
    row, cwd, _ = _remote_row(tmp_path, monkeypatch, "  /srv/apps/x  ", exists=False)
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    row._manual()

    assert cwd.text() == "/srv/apps/x"


def test_manual_path_missing_on_target_asks_and_can_refuse(qapp, tmp_path, monkeypatch):
    """대상 기기에 없는 폴더는 오타일 가능성이 크다 - 묻고, 거절하면 바꾸지 않는다."""
    from PyQt6.QtWidgets import QMessageBox
    row, cwd, svc = _remote_row(tmp_path, monkeypatch, "/srv/apps/없는폴더", exists=False)
    cwd.setText("/srv/apps/원래값")
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No))

    row._manual()

    assert asked and "없는폴더" in asked[0]
    assert cwd.text() == "/srv/apps/원래값"
    assert svc.text() == "http://localhost:8000"


def test_manual_path_missing_on_target_can_be_forced(qapp, tmp_path, monkeypatch):
    """아직 클론 전이라 폴더가 없을 수도 있다 - 그래도 쓰겠다면 받아들인다."""
    from PyQt6.QtWidgets import QMessageBox
    row, cwd, _ = _remote_row(tmp_path, monkeypatch, "/srv/apps/곧생길폴더", exists=False)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    row._manual()

    assert cwd.text() == "/srv/apps/곧생길폴더"


def test_manual_path_cancel_changes_nothing(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QInputDialog
    ctx = make_ctx(tmp_path, remote=True)
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("", False)))
    cwd = QLineEdit("/srv/apps/원래값")
    row = CwdPickerRow(ctx, cwd, None)

    row._manual()

    assert cwd.text() == "/srv/apps/원래값"


def test_no_projects_message_points_to_manual_input(qapp, tmp_path, monkeypatch):
    """클론한 프로젝트가 없을 때 '먼저 클론하라'고만 하면 막다른 길이다."""
    from PyQt6.QtWidgets import QMessageBox
    from app.ui.repo_picker import pick_repo_path
    ctx = make_ctx(tmp_path, remote=True)
    shown = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append(a[2])))

    assert pick_repo_path(None, ctx) is None
    assert shown and "직접 입력" in shown[0]
