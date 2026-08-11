"""MainWindow 통합 테스트: C1(폴링 예외 방어) / I7(라우트 삭제 롤백).

PyQt6 위젯을 실제로 만들어야 하는 검증이라 QApplication이 필요하다.
conftest.py가 QT_QPA_PLATFORM=offscreen을 강제해 디스플레이 없이도 돌아간다.
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from app.context import AppContext
from app.core.runner import RunResult
from app.core.store import RouteMeta, ServiceSpec, SettingsStore, TunnelMeta, new_route_id
from app.ui.main_window import MainWindow
from tests.fake_runner import FakeRunner

LIST_TUNNELS_CMD = ("cloudflared", "tunnel", "list", "--output", "json")


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def make_ctx(tmp_path, runner: FakeRunner | None = None) -> AppContext:
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    ctx = AppContext(store=store)
    runner = runner or FakeRunner(home="/home/fake")
    runner.run_results.setdefault(LIST_TUNNELS_CMD, RunResult(0, "[]", ""))
    ctx.runner = runner
    ctx.local_runner = runner  # is_remote 판정에도 일관되게 로컬로 남도록
    return ctx


def make_window(qapp, tmp_path, runner: FakeRunner | None = None) -> MainWindow:
    ctx = make_ctx(tmp_path, runner)
    win = MainWindow(ctx)
    win._timer.stop()  # 테스트 중 실제 QTimer가 돌지 않도록 (수동으로 _tick 호출)
    return win


# ---- C1: _tick() 예외 방어 + 배너 + 백오프 ----

def test_tick_exception_shows_banner_and_backs_off_without_crashing(qapp, tmp_path):
    win = make_window(qapp, tmp_path)

    def boom(_tunnels):
        raise ConnectionError("SSH 연결이 끊겼습니다")

    win.ctx.manager.refresh = boom

    win._tick()  # 예외가 슬롯 밖으로 새어나가면 안 된다 (PyQt6 abort 방지)

    assert not win.banner.isHidden()
    assert "연결이 끊겼" in win.banner.text()
    assert win._timer.interval() == 5000  # 백오프


def test_tick_repeated_failures_add_hint_after_threshold(qapp, tmp_path):
    win = make_window(qapp, tmp_path)
    win.ctx.manager.refresh = lambda _t: (_ for _ in ()).throw(OSError("no route"))

    for _ in range(4):
        win._tick()

    assert "로컬로 전환" in win.banner.text()


def test_tick_recovers_and_returns_to_normal_interval(qapp, tmp_path):
    win = make_window(qapp, tmp_path)
    fail = {"on": True}

    def maybe_boom(_tunnels):
        if fail["on"]:
            raise EOFError("연결 끊김")

    win.ctx.manager.refresh = maybe_boom
    win._tick()
    assert win._timer.interval() == 5000
    assert not win.banner.isHidden()

    fail["on"] = False
    win._tick()

    assert win._timer.interval() == 1000
    assert win.banner.isHidden()


# ---- I7: 라우트 삭제는 config 쓰기 실패 시 아무것도 바꾸지 않는다 ----

def test_delete_route_rolls_back_when_config_write_fails(qapp, tmp_path, monkeypatch):
    runner = FakeRunner(home="/home/fake")
    runner.run_results[LIST_TUNNELS_CMD] = RunResult(
        0, '[{"id":"aaa","name":"t1","created_at":"","connections":[]}]', "")
    config_path = "/home/fake/.cloudflared/config-t1.yml"
    runner.files[config_path] = "tunnel: t1\ncredentials-file: x\ningress:\n  - service: http_status:404\n"

    route = RouteMeta(id=new_route_id(), hostname="a.example.com", service="http://localhost:8000",
                      server=ServiceSpec(kind="command", start_cmd="myserver"))
    meta = TunnelMeta(name="t1", routes=[route])

    ctx = make_ctx(tmp_path, runner)
    ctx.store.settings.tunnels["t1"] = meta
    win = MainWindow(ctx)
    win._timer.stop()
    card = win.cards[0]
    assert len(card.meta.routes) == 1

    # write_file을 강제로 실패시켜 config 갱신이 깨지는 상황을 흉내낸다
    def boom_write(path, text):
        raise OSError("디스크 가득 참")
    monkeypatch.setattr(runner, "write_file", boom_write)
    monkeypatch.setattr("app.ui.main_window.QMessageBox.question",
                        staticmethod(lambda *a, **k: __import__("PyQt6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox.StandardButton.Yes))
    monkeypatch.setattr("app.ui.main_window.QMessageBox.warning", staticmethod(lambda *a, **k: None))

    win._delete_route(card, route)

    # config 쓰기가 실패했으므로 라우트가 여전히 남아있어야 한다 (고아 방지)
    assert len(ctx.store.settings.tunnels["t1"].routes) == 1
    assert ctx.store.settings.tunnels["t1"].routes[0].id == route.id
