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
