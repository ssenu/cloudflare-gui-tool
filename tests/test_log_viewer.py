"""로그 뷰어: 도커 컨테이너 로그 탭, 파일 탭, 보이는 탭만 읽기."""
from __future__ import annotations

import time

import pytest
from PyQt6.QtWidgets import QApplication

from app.context import AppContext
from app.core.runner import RunResult
from app.core.store import (RouteMeta, ServiceSpec, SettingsStore, TunnelMeta,
                            new_route_id)
from app.ui.log_viewer import LogViewer, _ComposeLogTab, _LogTab
from tests.fake_runner import FakeRunner

COMPOSE_LOGS = ("docker", "compose", "logs", "--tail", "200", "--no-color")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def make_ctx(tmp_path, runner=None):
    store = SettingsStore(path=str(tmp_path / "s.json"))
    ctx = AppContext(store=store)
    runner = runner or FakeRunner(home="/home/pi")
    ctx.runner = runner
    return ctx


def pump(viewer, timeout=2.0):
    viewer._poll()
    end = time.monotonic() + timeout
    while viewer._poller.is_busy() and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)
    QApplication.processEvents()


def test_compose_tab_shows_container_logs(qapp, tmp_path):
    """파일에는 빌드 출력만 있어서, 앱이 뱉은 트레이스백은 이 탭에만 있다."""
    runner = FakeRunner(home="/home/pi")
    runner.run_results[COMPOSE_LOGS] = RunResult(
        0, "app-1  | Traceback (most recent call last):\napp-1  | ValueError", "")
    ctx = make_ctx(tmp_path, runner)

    viewer = LogViewer(ctx, "t", {"앱": ("compose", "/srv/apps/x")})
    viewer._timer.stop()
    pump(viewer)

    tab = viewer._tabs[0]
    assert isinstance(tab, _ComposeLogTab)
    assert "Traceback" in tab.view.toPlainText()
    # cwd를 그 프로젝트로 지정해 실행해야 한다
    assert any(cwd == "/srv/apps/x" for cmd, cwd in runner.run_calls
               if cmd == COMPOSE_LOGS)


def test_compose_tab_reports_failure(qapp, tmp_path):
    runner = FakeRunner(home="/home/pi")
    runner.run_results[COMPOSE_LOGS] = RunResult(1, "", "no such service")
    ctx = make_ctx(tmp_path, runner)

    viewer = LogViewer(ctx, "t", {"앱": ("compose", "/srv/apps/x")})
    viewer._timer.stop()
    pump(viewer)

    assert "no such service" in viewer._tabs[0].view.toPlainText()


def test_compose_tab_does_not_redraw_when_unchanged(qapp, tmp_path):
    runner = FakeRunner(home="/home/pi")
    runner.run_results[COMPOSE_LOGS] = RunResult(0, "같은 내용", "")
    ctx = make_ctx(tmp_path, runner)
    viewer = LogViewer(ctx, "t", {"앱": ("compose", "/srv/apps/x")})
    viewer._timer.stop()
    pump(viewer)

    tab = viewer._tabs[0]
    redraws = []
    tab.view.setPlainText = lambda t: redraws.append(t)
    pump(viewer)

    assert redraws == []   # 내용이 같으면 다시 그리지 않는다(스크롤 유지)


def test_file_tab_still_tails_incrementally(qapp, tmp_path):
    runner = FakeRunner(home="/home/pi")
    runner.files["/log/a.log"] = "첫 줄\n"
    ctx = make_ctx(tmp_path, runner)

    viewer = LogViewer(ctx, "t", {"터널": "/log/a.log"})
    viewer._timer.stop()
    pump(viewer)
    assert isinstance(viewer._tabs[0], _LogTab)
    assert "첫 줄" in viewer._tabs[0].view.toPlainText()

    runner.files["/log/a.log"] = "첫 줄\n둘째 줄\n"
    pump(viewer)
    assert "둘째 줄" in viewer._tabs[0].view.toPlainText()


def test_docker_route_gets_grouped_subtabs(qapp, tmp_path):
    """빌드 실패와 앱 오류는 서로 다른 곳에 남는다 - 둘 다 볼 수 있어야 한다.

    다만 최상위에 나란히 두면 서버 수의 두 배로 탭이 늘어나므로, 서버 이름
    탭 하나에 하위 탭(앱/빌드)으로 묶는다.
    """
    from app.ui.main_window import MainWindow

    runner = FakeRunner(home="/home/fake")
    runner.run_results[("cloudflared", "tunnel", "list", "--output", "json")] = \
        RunResult(0, '[{"id":"t1","name":"t1","created_at":"","connections":[]}]', "")
    ctx = make_ctx(tmp_path, runner)
    ctx.local_runner = runner
    route = RouteMeta(id=new_route_id(), hostname="a.example.com",
                      service="http://localhost:8000",
                      server=ServiceSpec(kind="docker", cwd="/srv/apps/x"))
    ctx.store.settings.tunnels_for(runner.name)["t1"] = TunnelMeta(
        name="t1", routes=[route])
    win = MainWindow(ctx)
    win._timer.stop()
    win.refresh()

    opened = {}
    win._open_log_viewer = lambda key, title, paths: opened.update(paths)
    win._open_log_tunnel(win.cards[0])

    assert list(opened) == ["터널", "a.example.com"]      # 최상위 탭은 서버당 하나
    group = opened["a.example.com"]
    assert list(group) == ["앱", "빌드"]                   # 하위 탭 둘
    assert group["앱"] == ("compose", "/srv/apps/x")


def test_command_route_gets_single_tab(qapp, tmp_path):
    from app.ui.main_window import MainWindow

    runner = FakeRunner(home="/home/fake")
    runner.run_results[("cloudflared", "tunnel", "list", "--output", "json")] = \
        RunResult(0, '[{"id":"t1","name":"t1","created_at":"","connections":[]}]', "")
    ctx = make_ctx(tmp_path, runner)
    ctx.local_runner = runner
    route = RouteMeta(id=new_route_id(), hostname="a.example.com",
                      service="http://localhost:8000",
                      server=ServiceSpec(kind="command", start_cmd="run"))
    ctx.store.settings.tunnels_for(runner.name)["t1"] = TunnelMeta(
        name="t1", routes=[route])
    win = MainWindow(ctx)
    win._timer.stop()
    win.refresh()

    opened = {}
    win._open_log_viewer = lambda key, title, paths: opened.update(paths)
    win._open_log_tunnel(win.cards[0])

    assert not any(isinstance(v, dict) for v in opened.values())  # 하위 탭 없음


def test_grouped_viewer_polls_active_subtab(qapp, tmp_path):
    """폴링은 지금 보이는 하위 탭 하나만 읽어야 한다."""
    runner = FakeRunner(home="/home/pi")
    runner.run_results[COMPOSE_LOGS] = RunResult(0, "앱 로그 내용", "")
    runner.files["/log/build.log"] = "빌드 로그 내용\n"
    ctx = make_ctx(tmp_path, runner)

    viewer = LogViewer(ctx, "t", {
        "blog.example.com": {"앱": ("compose", "/srv/apps/x"),
                             "빌드": "/log/build.log"}})
    viewer._timer.stop()

    # 기본 하위 탭은 "앱"
    pump(viewer)
    app_tab, build_tab = viewer._tabs
    assert "앱 로그 내용" in app_tab.view.toPlainText()
    assert build_tab.view.toPlainText() == ""     # 아직 안 읽음

    # 하위 탭을 "빌드"로 바꾸면 그쪽을 읽는다
    from app.ui.log_viewer import _LogGroup
    group = viewer._tab_widget.currentWidget()
    assert isinstance(group, _LogGroup)
    group.inner.setCurrentIndex(1)
    pump(viewer)
    assert "빌드 로그 내용" in build_tab.view.toPlainText()
