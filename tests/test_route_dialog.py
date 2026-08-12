"""라우트 추가·편집 다이얼로그.

핵심 시나리오: 설정 파일(config-<터널>.yml)이 아직 없는 터널에 첫 라우트를
붙이는 경우. 예전에는 "설정 파일이 없어 저장할 수 없습니다"로 거절했는데,
도메인 없이 터널만 먼저 만드는 경로가 생기면서 정상적인 상황이 되었다.
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from app.context import AppContext
from app.core.config_yml import get_routes, parse_config
from app.core.runner import RunResult
from app.core.store import RouteMeta, SettingsStore, TunnelMeta, new_route_id
from app.ui.route_dialog import RouteDialog
from tests.fake_runner import FakeRunner

LIST_CMD = ("cloudflared", "tunnel", "list", "--output", "json")
CONFIG = "/home/fake/.cloudflared/config-web.yml"
CRED = "/home/fake/.cloudflared/tid1.json"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _silence(monkeypatch):
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: None)


def make_dialog(tmp_path, tunnel: TunnelMeta, files: dict | None = None,
                route: RouteMeta | None = None):
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    ctx = AppContext(store=store)
    runner = FakeRunner(home="/home/fake")
    runner.run_results[LIST_CMD] = RunResult(
        0, '[{"id":"tid1","name":"web","created_at":"","connections":[]}]', "")
    runner.files.update(files or {})
    ctx.runner = runner
    ctx.local_runner = runner
    return RouteDialog(ctx, tunnel, route, None), runner


def fill(dlg, sub="app", domain="example.com", service="http://localhost:8000"):
    dlg.sub_edit.setText(sub)
    dlg.domain_edit.setText(domain)
    dlg.service_edit.setText(service)


def test_first_route_creates_missing_config(qapp, tmp_path):
    """터널만 먼저 만든 뒤 라우트를 붙이면 설정 파일이 생겨야 한다."""
    tunnel = TunnelMeta(name="web", routes=[])
    dlg, runner = make_dialog(tmp_path, tunnel, {CRED: "{}"})  # config 없음
    fill(dlg)

    dlg._on_save()

    cfg = parse_config(runner.files[CONFIG])
    assert cfg["tunnel"] == "tid1"
    assert cfg["credentials-file"] == CRED
    assert get_routes(cfg) == [("app.example.com", "http://localhost:8000")]
    assert len(tunnel.routes) == 1
    assert dlg.saved_route is not None


def test_second_route_is_appended_to_existing_config(qapp, tmp_path):
    existing = RouteMeta(id=new_route_id(), hostname="a.example.com",
                         service="http://localhost:1111")
    tunnel = TunnelMeta(name="web", routes=[existing])
    dlg, runner = make_dialog(tmp_path, tunnel, {
        CRED: "{}",
        CONFIG: ("tunnel: tid1\ncredentials-file: " + CRED + "\ningress:\n"
                 "- hostname: a.example.com\n  service: http://localhost:1111\n"
                 "- service: http_status:404\n"),
    })
    fill(dlg, sub="b")

    dlg._on_save()

    routes = get_routes(parse_config(runner.files[CONFIG]))
    assert routes == [("a.example.com", "http://localhost:1111"),
                      ("b.example.com", "http://localhost:8000")]
    assert len(tunnel.routes) == 2


def test_no_credentials_blocks_save_and_writes_nothing(qapp, tmp_path):
    """자격증명이 없는 대상에서는 설정을 만들지 않고 막는다."""
    tunnel = TunnelMeta(name="web", routes=[])
    dlg, runner = make_dialog(tmp_path, tunnel, {})  # config도 자격증명도 없음
    fill(dlg)

    dlg._on_save()

    assert CONFIG not in runner.files
    assert tunnel.routes == []
    assert dlg.saved_route is None


def test_unknown_tunnel_blocks_save(qapp, tmp_path):
    tunnel = TunnelMeta(name="ghost", routes=[])
    dlg, runner = make_dialog(tmp_path, tunnel, {})
    fill(dlg)

    dlg._on_save()

    assert "/home/fake/.cloudflared/config-ghost.yml" not in runner.files
    assert dlg.saved_route is None


def test_dns_is_registered_for_new_route(qapp, tmp_path):
    tunnel = TunnelMeta(name="web", routes=[])
    dlg, runner = make_dialog(tmp_path, tunnel, {CRED: "{}"})
    fill(dlg)

    dlg._on_save()

    assert (("cloudflared", "tunnel", "route", "dns", "web", "app.example.com"), None) \
        in runner.run_calls
