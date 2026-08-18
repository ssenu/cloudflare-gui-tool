"""서버 카테고리의 서버 추가·편집 다이얼로그.

라우트 다이얼로그와 달리 도메인·DNS·ingress를 전혀 건드리지 않는 것이
이 다이얼로그의 존재 이유다 - 그 점을 테스트로 못박아 둔다.
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from app.context import AppContext
from app.core.store import RouteMeta, ServerGroupMeta, ServiceSpec, SettingsStore
from app.ui.server_dialog import ServerDialog
from tests.fake_runner import FakeRunner


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _silence(monkeypatch):
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: None)


@pytest.fixture
def ctx(tmp_path):
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    app_ctx = AppContext(store=store)
    runner = FakeRunner(home="/home/fake")
    app_ctx.runner = runner
    app_ctx.local_runner = runner
    return app_ctx


def _group(servers=None):
    return ServerGroupMeta(id="aabbccdd", name="백엔드", servers=servers or [])


def test_name_is_required(qapp, ctx):
    dlg = ServerDialog(ctx, _group())
    dlg.name_edit.setText("")
    assert "이름" in dlg._validate()


def test_docker_requires_working_directory(qapp, ctx):
    dlg = ServerDialog(ctx, _group())
    dlg.name_edit.setText("api")
    dlg.kind_combo.setCurrentIndex(1)  # 도커
    dlg.cwd_edit.setText("")
    assert "작업 폴더" in dlg._validate()


def test_service_address_is_validated(qapp, ctx):
    dlg = ServerDialog(ctx, _group())
    dlg.name_edit.setText("api")
    dlg.service_edit.setText("localhost:8000")  # http:// 없음
    assert dlg._validate() != ""


def test_duplicate_name_in_same_group_is_rejected(qapp, ctx):
    existing = RouteMeta(id="11112222", hostname="", service="http://localhost:1",
                         label="api", server=ServiceSpec(kind="command"))
    dlg = ServerDialog(ctx, _group([existing]))
    dlg.name_edit.setText("api")
    dlg.service_edit.setText("http://localhost:8000")
    assert "이미" in dlg._validate()


def test_valid_command_server_passes(qapp, ctx):
    dlg = ServerDialog(ctx, _group())
    dlg.name_edit.setText("api")
    dlg.service_edit.setText("http://localhost:8000")
    dlg.start_cmd_edit.setText("uvicorn main:app")
    assert dlg._validate() == ""


def test_saving_appends_server_without_touching_dns_or_config(qapp, ctx, monkeypatch):
    """도메인이 없으므로 DNS·ingress 경로를 아예 타지 않아야 한다."""
    group = _group()
    ctx.store.settings.server_groups_for("fake").append(group)
    dlg = ServerDialog(ctx, group)
    dlg.name_edit.setText("api")
    dlg.service_edit.setText("http://localhost:8000")
    dlg.kind_combo.setCurrentIndex(1)
    dlg.cwd_edit.setText("/srv/api")
    monkeypatch.setattr("app.ui.server_dialog.detect_service_port",
                        lambda ctx_, cwd: None)

    dlg._on_save()

    assert len(group.servers) == 1
    saved = group.servers[0]
    assert saved.label == "api"
    assert saved.hostname == ""          # 도메인 없음이 이 기능의 핵심
    assert saved.server.kind == "docker"
    assert saved.server.cwd == "/srv/api"
    # 도커 기본 명령이 채워진다
    assert "docker compose" in saved.server.start_cmd
    # 설정 파일에 실제로 기록됐는지
    reloaded = SettingsStore(path=ctx.store.path).load()
    assert reloaded.server_groups_for("fake")[0].servers[0].label == "api"


def test_editing_keeps_the_same_server_id(qapp, ctx, monkeypatch):
    existing = RouteMeta(id="11112222", hostname="", service="http://localhost:1",
                         label="api", server=ServiceSpec(kind="command",
                                                         start_cmd="old"))
    group = _group([existing])
    ctx.store.settings.server_groups_for("fake").append(group)
    dlg = ServerDialog(ctx, group, server=existing)
    dlg.start_cmd_edit.setText("new-cmd")

    dlg._on_save()

    assert len(group.servers) == 1
    assert group.servers[0].id == "11112222"
    assert group.servers[0].server.start_cmd == "new-cmd"
