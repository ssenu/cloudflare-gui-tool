"""MainWindow 통합 테스트: C1(폴링 예외 방어) / I7(라우트 삭제 롤백).

PyQt6 위젯을 실제로 만들어야 하는 검증이라 QApplication이 필요하다.
conftest.py가 QT_QPA_PLATFORM=offscreen을 강제해 디스플레이 없이도 돌아간다.
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from app.context import AppContext
from app.core.runner import RunResult
from app.core.store import (RouteMeta, ServiceSpec, SettingsStore, SshProfile,
                            TunnelMeta, new_route_id)
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
    # D3: 로컬 대상은 "연결"이 애초에 없으므로 원격용 연결 문구가 아니라
    # 상태 파일 접근 실패 문구를 보여줘야 한다(read_record()가 로컬
    # PermissionError 같은 OSError를 그대로 전파하게 되면서 생긴 요구사항).
    win = make_window(qapp, tmp_path)
    assert not win.ctx.is_remote

    def boom(_tunnels):
        raise ConnectionError("SSH 연결이 끊겼습니다")

    win.ctx.manager.refresh = boom

    win._tick()  # 예외가 슬롯 밖으로 새어나가면 안 된다 (PyQt6 abort 방지)

    assert not win.banner.isHidden()
    assert "상태 파일을 읽지 못했습니다" in win.banner.text()
    assert win._timer.interval() == 5000  # 백오프


def test_tick_exception_on_remote_shows_connection_banner(qapp, tmp_path):
    # 원격 대상에서의 폴링 실패는 여전히 "연결이 끊겼다" 문구를 써야 한다.
    win = make_window(qapp, tmp_path)
    win.ctx.runner = FakeRunner(home="/home/remote")  # local_runner와 다른 인스턴스 -> is_remote
    assert win.ctx.is_remote

    win.ctx.manager.refresh = lambda _t: (_ for _ in ()).throw(ConnectionError("no route"))

    win._tick()

    assert not win.banner.isHidden()
    assert "연결이 끊겼" in win.banner.text()
    assert win._timer.interval() == 5000


def test_tick_repeated_failures_add_hint_after_threshold(qapp, tmp_path):
    # 재시도 안내 힌트("로컬로 전환")는 원격 대상에서만 의미가 있다 -
    # 로컬은 이미 로컬이므로 힌트를 보여주지 않는다(D3).
    win = make_window(qapp, tmp_path)
    win.ctx.runner = FakeRunner(home="/home/remote")
    win.ctx.manager.refresh = lambda _t: (_ for _ in ()).throw(OSError("no route"))

    for _ in range(4):
        win._tick()

    assert "로컬로 전환" in win.banner.text()


def test_tick_repeated_failures_on_local_have_no_reconnect_hint(qapp, tmp_path):
    win = make_window(qapp, tmp_path)
    win.ctx.manager.refresh = lambda _t: (_ for _ in ()).throw(OSError("no route"))

    for _ in range(4):
        win._tick()

    assert "상태 파일을 읽지 못했습니다" in win.banner.text()
    assert "로컬로 전환" not in win.banner.text()


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
    ctx.store.settings.tunnels_for(ctx.runner.name)["t1"] = meta
    win = MainWindow(ctx)
    win._timer.stop()
    card = win.cards[0]
    assert len(card.meta.routes) == 1

    # write_file을 강제로 실패시켜 config 갱신이 깨지는 상황을 흉내낸다
    def boom_write(path, text):
        raise OSError("디스크 가득 참")
    monkeypatch.setattr(runner, "write_file", boom_write)
    monkeypatch.setattr("app.ui.main_window.QMessageBox.warning", staticmethod(lambda *a, **k: None))

    # 확인 다이얼로그는 이미 수락된 것으로 보고, 실제 삭제 로직만 검증한다.
    win._do_delete_route(card, route)

    # config 쓰기가 실패했으므로 라우트가 여전히 남아있어야 한다 (고아 방지)
    assert len(ctx.store.settings.tunnels_for(ctx.runner.name)["t1"].routes) == 1
    assert ctx.store.settings.tunnels_for(ctx.runner.name)["t1"].routes[0].id == route.id


# ---- 2-d: 삭제 확인 다이얼로그의 대시보드 버튼 ----

def test_confirm_delete_dashboard_button_opens_browser_without_closing(qapp, tmp_path, monkeypatch):
    from app.ui.confirm_dialogs import ConfirmDeleteDialog

    win = make_window(qapp, tmp_path)
    opened = []
    monkeypatch.setattr("app.ui.confirm_dialogs.webbrowser.open", lambda url: opened.append(url))

    dlg = ConfirmDeleteDialog(win.ctx, "제목", "본문", ["a.example.com"], win)
    finished_calls = []
    dlg.finished.connect(lambda r: finished_calls.append(r))
    dlg._open_dashboard()

    assert opened == ["https://dash.cloudflare.com"]
    assert finished_calls == []  # 대시보드 클릭만으로는 accept/reject가 일어나지 않는다


def test_tunnel_delete_dialog_requires_exact_name_match(qapp, tmp_path):
    from app.ui.confirm_dialogs import TunnelDeleteDialog

    win = make_window(qapp, tmp_path)
    dlg = TunnelDeleteDialog(win.ctx, "mysite", ["a.example.com"], win)

    assert not dlg.confirm_btn.isEnabled()
    dlg.name_edit.setText("wrong")
    assert not dlg.confirm_btn.isEnabled()
    dlg.name_edit.setText("mysite")
    assert dlg.confirm_btn.isEnabled()
    dlg.name_edit.setText("  mysite  ")
    assert dlg.confirm_btn.isEnabled()
    dlg.name_edit.setText("MySite")
    assert not dlg.confirm_btn.isEnabled()  # 대소문자 구분


# ---- B1: 대상별로 분리된 설정을 카드가 사용하는지 ----

def test_refresh_uses_metas_for_current_target(qapp, tmp_path):
    runner_a = FakeRunner(home="/home/a")
    runner_a.name = "local"
    runner_a.run_results[LIST_TUNNELS_CMD] = RunResult(
        0, '[{"id":"x","name":"t1","created_at":"","connections":[]}]', "")

    win = make_window(qapp, tmp_path, runner_a)
    route_a = RouteMeta(id=new_route_id(), hostname="a.local.example.com",
                        service="http://localhost:1111")
    win.ctx.store.settings.tunnels_for("local")["t1"] = TunnelMeta(
        name="t1", routes=[route_a])
    win.refresh()
    assert win.cards[0].meta.routes[0].hostname == "a.local.example.com"

    # 대상을 SSH 프로필로 바꾼다 (실제 SSH 연결 없이 러너만 교체)
    runner_b = FakeRunner(home="/home/b")
    runner_b.name = "ssh:rpi"
    runner_b.run_results[LIST_TUNNELS_CMD] = RunResult(
        0, '[{"id":"x","name":"t1","created_at":"","connections":[]}]', "")
    route_b = RouteMeta(id=new_route_id(), hostname="b.rpi.example.com",
                        service="http://localhost:2222")
    win.ctx.store.settings.tunnels_for("ssh:rpi")["t1"] = TunnelMeta(
        name="t1", routes=[route_b])
    win.ctx.runner = runner_b

    win.refresh()
    # 대상이 바뀌었으므로 카드는 로컬이 아니라 ssh:rpi 대상의 메타(다른 hostname)를 써야 한다
    assert win.cards[0].meta.routes[0].hostname == "b.rpi.example.com"
    assert win.cards[0].meta.routes[0].hostname != "a.local.example.com"


# ---- B3: SSH 프로필 삭제 시 콤보/러너가 로컬로 되돌아가는지 ----

def test_reload_targets_falls_back_to_local_when_profile_deleted(qapp, tmp_path):
    win = make_window(qapp, tmp_path)
    profile = SshProfile(name="rpi", host="1.2.3.4")
    win.ctx.store.settings.ssh_profiles.append(profile)
    win._reload_targets()

    # rpi를 현재 대상으로 선택한 것처럼 상태를 맞춘다 (실제 SSH 연결은 하지 않음)
    win._current_target_key = "rpi"
    win._reload_targets()
    assert win.target_combo.currentIndex() == 1
    assert win.target_combo.currentData() is profile

    # 사용 중이던 프로필을 삭제한 뒤 다시 로드하면 로컬로 되돌아가야 한다
    win.ctx.store.settings.ssh_profiles.remove(profile)
    win._reload_targets()

    assert win._current_target_key is None
    assert win.target_combo.currentIndex() == 0
    assert win.target_combo.currentData() is None
    assert win.ctx.runner is win.ctx.local_runner


# ---- O: 터널 카드의 "만든 곳" 배지 / 자격증명 없을 때 실행 불가 표시 ----

def test_card_shows_owner_badge_when_owner_set(qapp, tmp_path):
    runner = FakeRunner(home="/home/fake")
    runner.run_results[LIST_TUNNELS_CMD] = RunResult(
        0, '[{"id":"tid1","name":"t1","created_at":"","connections":[]}]', "")
    runner.files["/home/fake/.cloudflared/tid1.json"] = "{}"  # 자격증명 있음

    win = make_window(qapp, tmp_path, runner)
    win.ctx.store.settings.tunnels_for("fake")["t1"] = TunnelMeta(
        name="t1", owner="local")
    win.refresh()

    card = win.cards[0]
    assert not card.owner_badge.isHidden()
    assert "이 PC" in card.owner_badge.text()
    assert card.cannot_run_label.isHidden()
    assert card.tunnel_switch.isEnabled()


def test_card_hides_owner_badge_when_owner_empty(qapp, tmp_path):
    runner = FakeRunner(home="/home/fake")
    runner.run_results[LIST_TUNNELS_CMD] = RunResult(
        0, '[{"id":"tid1","name":"t1","created_at":"","connections":[]}]', "")
    # 자격증명이 없어야 backfill이 일어나지 않고 owner가 빈 채로 남는다
    win = make_window(qapp, tmp_path, runner)
    win.ctx.store.settings.tunnels_for("fake")["t1"] = TunnelMeta(name="t1", owner="")
    win.refresh()

    card = win.cards[0]
    assert card.owner_badge.isHidden()


def test_card_shows_cannot_run_and_disables_toggle_without_credentials(qapp, tmp_path):
    runner = FakeRunner(home="/home/fake")
    runner.run_results[LIST_TUNNELS_CMD] = RunResult(
        0, '[{"id":"tid1","name":"t1","created_at":"","connections":[]}]', "")
    # 자격증명 파일을 만들지 않는다 -> has_credentials False

    win = make_window(qapp, tmp_path, runner)
    win.ctx.store.settings.tunnels_for("fake")["t1"] = TunnelMeta(
        name="t1", owner="ssh:webPi")
    win.refresh()

    card = win.cards[0]
    assert not card.cannot_run_label.isHidden()
    assert "실행 불가" in card.cannot_run_label.text()
    assert not card.tunnel_switch.isEnabled()
    assert "자격증명" in card.tunnel_switch.toolTip()

    # _tick()으로 update_state()가 다시 돌아도 비활성 상태가 유지되어야 한다
    win._tick()
    assert not card.tunnel_switch.isEnabled()


def test_refresh_backfills_owner_when_credentials_present(qapp, tmp_path):
    runner = FakeRunner(home="/home/fake")
    runner.run_results[LIST_TUNNELS_CMD] = RunResult(
        0, '[{"id":"tid1","name":"t1","created_at":"","connections":[]}]', "")
    runner.files["/home/fake/.cloudflared/tid1.json"] = "{}"

    win = make_window(qapp, tmp_path, runner)
    win.ctx.store.settings.tunnels_for("fake")["t1"] = TunnelMeta(name="t1", owner="")
    win.refresh()

    meta = win.ctx.store.settings.tunnels_for("fake")["t1"]
    assert meta.owner == "fake"  # 현재 대상(runner.name)으로 채워졌어야 함

    # 저장까지 반영됐는지 재로드로 확인
    reloaded = SettingsStore(path=win.ctx.store.path).load()
    assert reloaded.tunnels_for("fake")["t1"].owner == "fake"


def test_credentials_check_happens_once_per_refresh_not_on_tick(qapp, tmp_path):
    calls = {"n": 0}

    class CountingRunner(FakeRunner):
        def file_exists(self, path):
            if path.endswith(".json") and "/.cloudflared/" in path and "config-" not in path:
                calls["n"] += 1
            return super().file_exists(path)

    runner = CountingRunner(home="/home/fake")
    runner.run_results[LIST_TUNNELS_CMD] = RunResult(
        0, '[{"id":"tid1","name":"t1","created_at":"","connections":[]}]', "")
    runner.files["/home/fake/.cloudflared/tid1.json"] = "{}"

    win = make_window(qapp, tmp_path, runner)
    win.ctx.store.settings.tunnels_for("fake")["t1"] = TunnelMeta(name="t1", owner="local")
    win.refresh()
    after_refresh = calls["n"]
    assert after_refresh >= 1

    for _ in range(5):
        win._tick()

    # _tick()(1초 폴링)에서는 자격증명 파일을 다시 조회하지 않아야 한다
    assert calls["n"] == after_refresh
