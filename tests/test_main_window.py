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


# ---- O/C1: 터널 카드의 "만든 곳" 배지 / 자격증명 없을 때 실행 불가 표시 ----
#
# owner는 계정 단위(Settings.tunnel_owners, 키=터널 UUID)에 저장된다 - 대상별
# targets에 두면 대상을 바꾸는 순간 다른 dict를 보게 되어 기능이 무력화된다
# (C1). 아래 테스트들은 owner를 TunnelMeta가 아니라 tunnel_owners에 직접
# 심어서, 실제 저장 위치를 기준으로 검증한다.

def test_group_header_shows_owner_device(qapp, tmp_path):
    runner = FakeRunner(home="/home/fake")
    runner.run_results[LIST_TUNNELS_CMD] = RunResult(
        0, '[{"id":"tid1","name":"t1","created_at":"","connections":[]}]', "")
    runner.files["/home/fake/.cloudflared/tid1.json"] = "{}"  # 자격증명 있음

    win = make_window(qapp, tmp_path, runner)
    win.ctx.store.settings.tunnels_for("fake")["t1"] = TunnelMeta(name="t1")
    win.ctx.store.settings.tunnel_owners["tid1"] = "local"
    win.refresh()

    # 카드에는 '만든 곳' 문구가 없고, 대신 카테고리 머리글이 기기를 알려준다
    assert [h.label_text for h in win.group_headers] == ["이 PC"]
    card = win.cards[0]
    assert not hasattr(card, "owner_badge")
    assert card.cannot_run_label.isHidden()
    assert card.tunnel_switch.isEnabled()


def test_group_header_falls_back_when_owner_not_recorded(qapp, tmp_path):
    runner = FakeRunner(home="/home/fake")
    runner.run_results[LIST_TUNNELS_CMD] = RunResult(
        0, '[{"id":"tid1","name":"t1","created_at":"","connections":[]}]', "")
    # 자격증명이 없어야 backfill이 일어나지 않고 owner가 기록되지 않은 채로 남는다
    win = make_window(qapp, tmp_path, runner)
    win.ctx.store.settings.tunnels_for("fake")["t1"] = TunnelMeta(name="t1")
    win.refresh()

    assert [h.label_text for h in win.group_headers] == ["기기 미확인"]
    assert win.ctx.store.settings.tunnel_owners == {}


def test_groups_split_by_owner_and_unknown_goes_last(qapp, tmp_path):
    runner = FakeRunner(home="/home/fake")
    runner.run_results[LIST_TUNNELS_CMD] = RunResult(0, """[
        {"id":"tid-unknown","name":"t-unknown","created_at":"","connections":[]},
        {"id":"tid-pi","name":"t-pi","created_at":"","connections":[]},
        {"id":"tid-pc","name":"t-pc","created_at":"","connections":[]}]""", "")

    win = make_window(qapp, tmp_path, runner)
    win.ctx.store.settings.ssh_profiles.append(SshProfile(name="webPi", host="1.2.3.4"))
    win.ctx.store.settings.tunnel_owners["tid-pi"] = "ssh:webPi"
    win.ctx.store.settings.tunnel_owners["tid-pc"] = "local"
    win.refresh()

    # 이 PC → 등록된 SSH 프로필 순 → 소유 기기를 모르는 것은 항상 마지막
    assert [h.label_text for h in win.group_headers] == ["이 PC", "webPi", "기기 미확인"]
    # cards 순서도 화면 순서와 같아야 한다 (Ctrl+1~9 단축키가 이 순서를 쓴다)
    assert [c.tunnel_name for c in win.cards] == ["t-pc", "t-pi", "t-unknown"]


def test_card_shows_cannot_run_and_disables_toggle_without_credentials(qapp, tmp_path):
    runner = FakeRunner(home="/home/fake")
    runner.run_results[LIST_TUNNELS_CMD] = RunResult(
        0, '[{"id":"tid1","name":"t1","created_at":"","connections":[]}]', "")
    # 자격증명 파일을 만들지 않는다 -> has_credentials False

    win = make_window(qapp, tmp_path, runner)
    win.ctx.store.settings.tunnels_for("fake")["t1"] = TunnelMeta(name="t1")
    win.ctx.store.settings.tunnel_owners["tid1"] = "ssh:webPi"
    win.refresh()

    card = win.cards[0]
    assert not card.cannot_run_label.isHidden()
    assert "실행 불가" in card.cannot_run_label.text()
    assert not card.tunnel_switch.isEnabled()
    assert "자격증명" in card.tunnel_switch.toolTip()

    # _tick()으로 update_state()가 다시 돌아도 비활성 상태가 유지되어야 한다
    win._tick()
    assert not card.tunnel_switch.isEnabled()


def test_owner_group_survives_target_switch(qapp, tmp_path):
    """C1의 핵심 증거: 대상 A에서 기록된 owner가 대상 B로 전환해도 그대로 보여야 한다.

    targets는 대상별로 나뉘어 있지만 tunnel_owners는 계정 단위 단일 맵이므로,
    대상을 바꿔도(러너 교체) 같은 owner를 봐야 한다.
    """
    runner_a = FakeRunner(home="/home/a")
    runner_a.name = "local"
    runner_a.run_results[LIST_TUNNELS_CMD] = RunResult(
        0, '[{"id":"tid-shared","name":"t1","created_at":"","connections":[]}]', "")
    runner_a.files["/home/a/.cloudflared/tid-shared.json"] = "{}"  # A에 자격증명 있음

    win = make_window(qapp, tmp_path, runner_a)
    win.ctx.store.settings.tunnels_for("local")["t1"] = TunnelMeta(name="t1")
    win.refresh()  # backfill: tunnel_owners["tid-shared"] = "local"

    assert win.ctx.store.settings.tunnel_owners["tid-shared"] == "local"
    assert [h.label_text for h in win.group_headers] == ["이 PC"]

    # 대상을 SSH 프로필(webPi)로 전환한다 - 실제 연결 없이 러너만 교체
    runner_b = FakeRunner(home="/home/b")
    runner_b.name = "ssh:webPi"
    runner_b.run_results[LIST_TUNNELS_CMD] = RunResult(
        0, '[{"id":"tid-shared","name":"t1","created_at":"","connections":[]}]', "")
    # B에는 자격증명이 없다 - 그래도 owner는 "이 PC"로 보여야 한다(A가 만든 것)
    win.ctx.store.settings.tunnels_for("ssh:webPi")["t1"] = TunnelMeta(name="t1")
    win.ctx.runner = runner_b
    win.refresh()

    # 여전히 대상 A(local)가 만든 것으로 분류된다 - 지금 보고 있는 대상이 B라도
    assert [h.label_text for h in win.group_headers] == ["이 PC"]
    assert not win.group_headers[0].current_label.isVisible()  # 현재 대상은 B
    card = win.cards[0]
    assert not card.cannot_run_label.isHidden()  # B에는 자격증명이 없으므로 실행 불가


def test_backfill_never_overwrites_existing_owner(qapp, tmp_path):
    """자격증명이 양쪽 대상에 다 있어도, 이미 기록된 owner를 가로채면 안 된다(C2)."""
    runner = FakeRunner(home="/home/fake")
    runner.run_results[LIST_TUNNELS_CMD] = RunResult(
        0, '[{"id":"tid1","name":"t1","created_at":"","connections":[]}]', "")
    runner.files["/home/fake/.cloudflared/tid1.json"] = "{}"  # 지금 대상에도 자격증명 있음

    win = make_window(qapp, tmp_path, runner)
    win.ctx.store.settings.tunnels_for("fake")["t1"] = TunnelMeta(name="t1")
    win.ctx.store.settings.tunnel_owners["tid1"] = "ssh:webPi"  # 이미 다른 대상이 owner
    win.refresh()

    # backfill 조건("id가 없을 때만")에 해당하지 않으므로 덮어쓰지 않는다
    assert win.ctx.store.settings.tunnel_owners["tid1"] == "ssh:webPi"
    assert [h.label_text for h in win.group_headers] == ["webPi"]


def test_refresh_backfills_owner_when_credentials_present(qapp, tmp_path):
    runner = FakeRunner(home="/home/fake")
    runner.run_results[LIST_TUNNELS_CMD] = RunResult(
        0, '[{"id":"tid1","name":"t1","created_at":"","connections":[]}]', "")
    runner.files["/home/fake/.cloudflared/tid1.json"] = "{}"

    win = make_window(qapp, tmp_path, runner)
    win.ctx.store.settings.tunnels_for("fake")["t1"] = TunnelMeta(name="t1")
    win.refresh()

    assert win.ctx.store.settings.tunnel_owners["tid1"] == "fake"  # 현재 대상(runner.name)

    # 저장까지 반영됐는지 재로드로 확인
    reloaded = SettingsStore(path=win.ctx.store.path).load()
    assert reloaded.tunnel_owners["tid1"] == "fake"


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
    win.ctx.store.settings.tunnels_for("fake")["t1"] = TunnelMeta(name="t1")
    win.ctx.store.settings.tunnel_owners["tid1"] = "local"
    win.refresh()
    after_refresh = calls["n"]
    assert after_refresh >= 1

    for _ in range(5):
        win._tick()

    # _tick()(1초 폴링)에서는 자격증명 파일을 다시 조회하지 않아야 한다
    assert calls["n"] == after_refresh


# ---- 프로젝트(Git 클론) 버튼은 SSH 대상에서만 활성화 ----

def test_repos_button_disabled_on_local_target(qapp, tmp_path):
    win = make_window(qapp, tmp_path)
    assert not win.ctx.is_remote

    assert not win.repos_btn.isEnabled()
    assert "원격" in win.repos_btn.toolTip()

    # 비활성 버튼을 우회해 호출해도 다이얼로그가 열리면 안 된다
    opened = []
    win._open_modal = lambda dlg: opened.append(dlg)
    win._open_repos()
    assert opened == []


def test_repos_button_enabled_on_remote_target(qapp, tmp_path):
    win = make_window(qapp, tmp_path)
    remote = FakeRunner(home="/home/remote")
    remote.name = "ssh:webPi"
    remote.run_results[LIST_TUNNELS_CMD] = RunResult(0, "[]", "")
    win.ctx.runner = remote
    assert win.ctx.is_remote
    win.refresh()

    assert win.repos_btn.isEnabled()
    assert win.repos_btn.toolTip() == ""


# ---- I3: 단축키가 비활성화된(자격증명 없는) 토글을 우회하면 안 된다 ----

def test_shortcut_tunnel_ignores_disabled_toggle(qapp, tmp_path):
    runner = FakeRunner(home="/home/fake")
    runner.run_results[LIST_TUNNELS_CMD] = RunResult(
        0, '[{"id":"tid1","name":"t1","created_at":"","connections":[]}]', "")
    # 자격증명 없음 -> 토글 비활성화

    win = make_window(qapp, tmp_path, runner)
    win.ctx.store.settings.tunnels_for("fake")["t1"] = TunnelMeta(name="t1")
    win.refresh()

    card = win.cards[0]
    assert not card.tunnel_switch.isEnabled()

    calls = []
    win.ctx.manager.start_tunnel = lambda *a, **k: calls.append(a)

    win._shortcut_tunnel(0)  # Ctrl+1에 해당

    assert calls == []  # 비활성 토글이므로 아무 것도 호출되지 않아야 한다
    assert not card.tunnel_switch.isChecked()


def test_shortcut_server_ignores_disabled_toggle(qapp, tmp_path):
    from app.core.store import RouteMeta, ServiceSpec

    runner = FakeRunner(home="/home/fake")
    runner.run_results[LIST_TUNNELS_CMD] = RunResult(
        0, '[{"id":"tid1","name":"t1","created_at":"","connections":[]}]', "")
    runner.files["/home/fake/.cloudflared/tid1.json"] = "{}"  # 터널 자체는 실행 가능

    route = RouteMeta(id=new_route_id(), hostname="a.example.com",
                      service="http://localhost:8000",
                      server=ServiceSpec(kind="command", start_cmd="myserver"))
    win = make_window(qapp, tmp_path, runner)
    win.ctx.store.settings.tunnels_for("fake")["t1"] = TunnelMeta(name="t1", routes=[route])
    win.refresh()

    card = win.cards[0]
    switch = card.route_rows[0].server_switch
    switch.setEnabled(False)  # 서버 토글이 비활성화된 상황을 흉내낸다

    calls = []
    win.ctx.manager.start_service = lambda *a, **k: calls.append(a)

    win._shortcut_server(0)  # Ctrl+Shift+1에 해당

    assert calls == []
    assert not switch.isChecked()
