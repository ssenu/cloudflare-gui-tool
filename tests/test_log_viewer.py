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

# 첫 읽기 명령. 이후 폴링은 --since로 새 줄만 받는다.
COMPOSE_LOGS = ("docker", "compose", "logs", "--no-color", "-t",
                "--tail", "200")


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


def test_docker_route_log_has_app_and_build_subtabs(qapp, tmp_path):
    """빌드 실패와 앱 오류는 서로 다른 곳에 남는다 - 한 서버 창에서 둘 다 본다.

    예전에는 터널 버튼 하나가 모든 서버를 탭으로 몰아서 열었다. 지금은
    서버마다 창이 따로 뜨고, 그 안에 앱/빌드 두 탭만 있다.
    """
    from app.ui.main_window import MainWindow

    runner = FakeRunner(home="/home/fake")
    runner.run_results[("cloudflared", "tunnel", "list", "--output", "json")] =         RunResult(0, '[{"id":"t1","name":"t1","created_at":"","connections":[]}]', "")
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
    win._open_log_route(win.cards[0], route)

    assert list(opened) == ["앱", "빌드"]
    assert opened["앱"] == ("compose", "/srv/apps/x")


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


# ---- B: 여는 순간 GUI 스레드에서 원격 호출을 하지 않는다 ----
# 예전에는 _LogTab.__init__이 file_size()를 직접 불렀다. 뷰어는 모든 탭을
# 생성자에서 만들므로, 탭 5개짜리 터널을 열면 SSH 왕복 5번이 GUI 스레드에서
# 줄줄이 일어나 창이 굳었다(빌드 중에는 한 번이 초 단위).

def test_opening_a_tab_makes_no_remote_call(qapp, tmp_path):
    runner = FakeRunner(home="/home/pi")
    calls = []
    runner.file_size = lambda p: calls.append(p) or 0

    _LogTab(runner, "/x.log", "#f00")

    assert calls == []


def test_first_read_seeks_to_the_tail_of_a_big_file(qapp, tmp_path):
    """크기 확인은 워커에서, 첫 읽기 때 한 번만 한다."""
    from app.ui.log_viewer import TAIL_START_LIMIT
    runner = FakeRunner(home="/home/pi")
    big = TAIL_START_LIMIT + 5000
    sizes = []
    runner.file_size = lambda p: sizes.append(p) or big
    seen = []
    runner.tail_file = lambda p, off: seen.append(off) or (big, "tail")

    tab = _LogTab(runner, "/x.log", "#f00")
    tab.read_new()
    tab.read_new()

    assert seen[0] == big - TAIL_START_LIMIT   # 끝부분만
    assert len(sizes) == 1                      # 두 번째 읽기에선 다시 안 잰다


# ---- C: 컨테이너 로그를 증분으로 ----

def test_compose_tab_first_read_uses_tail(qapp, tmp_path):
    runner = FakeRunner(home="/home/pi")
    seen = []
    runner.run = lambda cmd, **kw: seen.append(list(cmd)) or RunResult(0, "", "")

    _ComposeLogTab(runner, "/srv/app", "#f00").read_new()

    assert "--tail" in seen[0] and "-t" in seen[0]


def test_compose_tab_second_read_asks_only_for_new_lines(qapp, tmp_path):
    """매번 전체를 다시 긁으면 파이에서 한 번에 10초까지 걸린다."""
    runner = FakeRunner(home="/home/pi")
    out = "web-1  | 2026-09-20T06:11:15.100000000Z 첫 줄\n"
    seen = []

    def fake_run(cmd, **kw):
        seen.append(list(cmd))
        return RunResult(0, out, "")

    runner.run = fake_run
    tab = _ComposeLogTab(runner, "/srv/app", "#f00")
    tab.apply_new(tab.read_new())
    tab.read_new()

    assert "--since" in seen[1]
    assert seen[1][seen[1].index("--since") + 1] == "2026-09-20T06:11:15.100000000Z"
    assert "--tail" not in seen[1]


def test_compose_tab_drops_the_repeated_boundary_line(qapp, tmp_path):
    """--since는 경계 줄을 다시 주기도 한다. 화면에 두 번 찍히면 안 된다."""
    runner = FakeRunner(home="/home/pi")
    tab = _ComposeLogTab(runner, "/srv/app", "#f00")
    line = "web-1  | 2026-09-20T06:11:15.100000000Z 같은 줄"
    tab.apply_new(RunResult(0, line + "\n", ""))
    before = tab.view.toPlainText()

    tab.apply_new(RunResult(0, line + "\n", ""))

    assert tab.view.toPlainText() == before


def test_compose_tab_appends_instead_of_redrawing(qapp, tmp_path):
    """전체 다시 그리기(setPlainText)는 줄 수가 많을수록 GUI를 잡아먹는다."""
    runner = FakeRunner(home="/home/pi")
    tab = _ComposeLogTab(runner, "/srv/app", "#f00")
    tab.apply_new(RunResult(0, "web-1  | 2026-09-20T06:11:15.100000000Z 하나\n", ""))
    tab.apply_new(RunResult(0, "web-1  | 2026-09-20T06:11:16.100000000Z 둘\n", ""))

    text = tab.view.toPlainText()
    assert "하나" in text and "둘" in text


# ---- C: 탭 종류별 폴링 주기 ----

def test_compose_tab_polls_less_often_than_file_tab(qapp, tmp_path):
    runner = FakeRunner(home="/home/pi")
    file_tab = _LogTab(runner, "/x.log", "#f00")
    compose_tab = _ComposeLogTab(runner, "/srv/app", "#f00")

    assert compose_tab.min_interval_ms > file_tab.min_interval_ms


def test_viewer_skips_compose_poll_until_interval_elapses(qapp, tmp_path):
    ctx = make_ctx(tmp_path)
    seen = []
    ctx.runner.run = lambda cmd, **kw: seen.append(1) or RunResult(0, "", "")
    viewer = LogViewer(ctx, "t", {"앱": ("compose", "/srv/app")})

    pump(viewer)
    pump(viewer)          # 바로 이어서 - 주기가 안 됐으므로 건너뛰어야 한다

    assert len(seen) == 1
    viewer.close()


# ---- A: 로그를 터널별 · 서버별로 나눈다 ----
# 예전에는 터널 로그 버튼 하나가 터널 + 모든 라우트를 탭으로 몰아서 열었다.
# 탭이 많을수록 여는 비용(첫 읽기)과 헷갈림이 함께 늘어난다.

def _win(qapp, tmp_path, routes):
    from app.core.runner import RunResult
    from app.ui.main_window import MainWindow
    runner = FakeRunner(home="/home/pi")
    runner.run_results[("cloudflared", "tunnel", "list", "--output", "json")] = \
        RunResult(0, '[{"id":"t1","name":"web","created_at":"","connections":[]}]', "")
    ctx = make_ctx(tmp_path, runner)
    ctx.local_runner = runner
    ctx.store.settings.tunnels_for(runner.name)["web"] = TunnelMeta(
        name="web", routes=routes)
    win = MainWindow(ctx)
    win._timer.stop()
    win._first_load.stop()   # 창을 안 닫고 두면 나중 테스트에서 뒤늦게 터진다
    win.refresh()
    return win


def close_all(win):
    for v in list(win._log_viewers.values()):
        v.close()
    win._log_viewers.clear()
    win.close()
    QApplication.processEvents()


def _docker_route(label="api"):
    return RouteMeta(id=new_route_id(), hostname=f"{label}.example.com",
                     service="http://localhost:8000", label=label,
                     server=ServiceSpec(kind="docker", cwd="/srv/apps/x",
                                        start_cmd="docker compose up -d"))


def test_tunnel_log_button_opens_only_the_tunnel_log(qapp, tmp_path):
    win = _win(qapp, tmp_path, [_docker_route(), _docker_route("worker")])

    win._open_log_tunnel(win.cards[0])

    viewer = list(win._log_viewers.values())[-1]
    assert viewer._tab_widget.count() == 1
    assert viewer._tab_widget.tabText(0) == "터널"
    close_all(win)


def test_route_log_opens_only_that_server(qapp, tmp_path):
    a, b = _docker_route("api"), _docker_route("worker")
    win = _win(qapp, tmp_path, [a, b])

    win._open_log_route(win.cards[0], b)

    viewer = list(win._log_viewers.values())[-1]
    # 도커 서버는 앱/빌드 두 탭만 (다른 서버 탭은 없다)
    assert [viewer._tab_widget.tabText(i) for i in range(viewer._tab_widget.count())] == ["앱", "빌드"]
    assert "worker" in viewer.windowTitle()
    close_all(win)


def test_command_route_log_is_a_single_tab(qapp, tmp_path):
    r = RouteMeta(id=new_route_id(), hostname="h.example.com",
                  service="http://localhost:9000", label="plain",
                  server=ServiceSpec(kind="command", start_cmd="python x.py"))
    win = _win(qapp, tmp_path, [r])

    win._open_log_route(win.cards[0], r)

    viewer = list(win._log_viewers.values())[-1]
    assert viewer._tab_widget.count() == 1
    close_all(win)


def test_each_server_gets_its_own_viewer(qapp, tmp_path):
    """서버별로 창이 따로 떠야 한다 - 같은 창을 재사용하면 분리한 의미가 없다."""
    a, b = _docker_route("api"), _docker_route("worker")
    win = _win(qapp, tmp_path, [a, b])

    win._open_log_route(win.cards[0], a)
    win._open_log_route(win.cards[0], b)

    assert len(win._log_viewers) == 2
    close_all(win)


def test_route_row_menu_has_a_log_action(qapp, tmp_path):
    win = _win(qapp, tmp_path, [_docker_route()])
    row = win.cards[0].route_rows[0]
    opened = []
    win._open_log_route = lambda card, route: opened.append(route.label)

    row._open_log()

    assert opened == ["api"]
    close_all(win)
