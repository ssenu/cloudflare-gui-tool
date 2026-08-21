"""서버 카테고리(터널 없는 서버 묶음)의 메인 창 통합 테스트.

핵심: 이 카드에는 터널 토글이 없고, 터널 목록과 같은 목록에 섞여 나오며,
드래그로 순서를 바꿀 수 있고, 그 순서가 저장된다.
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from app.context import AppContext
from app.core.runner import RunResult
from app.core.store import (RouteMeta, ServerGroupMeta, ServiceSpec,
                            SettingsStore, new_route_id)
from app.ui.main_window import MainWindow, ServerGroupCard
from tests.fake_runner import FakeRunner

LIST_TUNNELS_CMD = ("cloudflared", "tunnel", "list", "--output", "json")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _silence(monkeypatch):
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: None)


def make_window(qapp, tmp_path, tunnels_json="[]", groups=None):
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    ctx = AppContext(store=store)
    runner = FakeRunner(home="/home/fake")
    runner.run_results[LIST_TUNNELS_CMD] = RunResult(0, tunnels_json, "")
    ctx.runner = runner
    ctx.local_runner = runner
    for g in groups or []:
        ctx.store.settings.server_groups_for(runner.name).append(g)
    win = MainWindow(ctx)
    win._timer.stop()
    win.refresh()
    return win


def docker_server(label="api", cwd="/srv/api"):
    return RouteMeta(id=new_route_id(), hostname="", service="http://localhost:8000",
                     label=label,
                     server=ServiceSpec(kind="docker", cwd=cwd,
                                        start_cmd="docker compose up -d",
                                        stop_cmd="docker compose down"))


# ---- 카드 렌더링 ----

def test_server_group_card_is_rendered_without_a_tunnel_switch(qapp, tmp_path):
    group = ServerGroupMeta(id="aabbccdd", name="백엔드", servers=[docker_server()])
    win = make_window(qapp, tmp_path, groups=[group])

    assert len(win.server_cards) == 1
    card = win.server_cards[0]
    assert isinstance(card, ServerGroupCard)
    assert card.group.name == "백엔드"
    # 터널 토글이 없는 것이 이 카드의 정의다
    assert not hasattr(card, "tunnel_switch")
    # 서버는 라우트 행과 같은 방식으로 한 줄씩 나온다
    assert len(card.route_rows) == 1
    assert card.route_rows[0].route.label == "api"


def test_server_row_has_no_domain_column(qapp, tmp_path):
    group = ServerGroupMeta(id="aabbccdd", name="백엔드", servers=[docker_server()])
    win = make_window(qapp, tmp_path, groups=[group])

    row = win.server_cards[0].route_rows[0]
    assert row.domain_label is None


def test_group_card_owner_key_is_namespaced_for_units(qapp, tmp_path):
    """유닛 이름이 터널과 겹치지 않아야 한다."""
    from app.core.run_registry import group_owner_key

    group = ServerGroupMeta(id="aabbccdd", name="백엔드", servers=[docker_server()])
    win = make_window(qapp, tmp_path, groups=[group])

    assert win.server_cards[0].owner == group_owner_key("aabbccdd")


def test_adding_a_group_persists_and_renders(qapp, tmp_path):
    win = make_window(qapp, tmp_path)

    win._create_server_group("백엔드")

    assert len(win.server_cards) == 1
    saved = SettingsStore(path=win.ctx.store.path).load()
    assert [g.name for g in saved.server_groups_for("fake")] == ["백엔드"]


def test_duplicate_group_name_is_rejected(qapp, tmp_path):
    win = make_window(qapp, tmp_path)
    win._create_server_group("백엔드")
    win._create_server_group("백엔드")
    assert len(win.ctx.store.settings.server_groups_for("fake")) == 1


# ---- 폴링 ----

def test_tick_polls_group_servers(qapp, tmp_path):
    """그룹 서버도 도커 폴링 대상에 들어가야 상태가 갱신된다."""
    group = ServerGroupMeta(id="aabbccdd", name="백엔드", servers=[docker_server()])
    win = make_window(qapp, tmp_path, groups=[group])
    win.ctx.runner.run_results[("docker", "compose", "ps", "-q")] = RunResult(
        0, "abc123\n", "")

    win.ctx.manager.refresh([], groups=[group])

    from app.core.run_registry import group_owner_key
    assert win.ctx.manager.service_running(group_owner_key(group.id),
                                           group.servers[0]) is True


# ---- 순서 바꾸기 ----

def test_reorder_moves_card_and_persists(qapp, tmp_path):
    a = ServerGroupMeta(id="aaaaaaaa", name="A")
    b = ServerGroupMeta(id="bbbbbbbb", name="B")
    win = make_window(qapp, tmp_path, groups=[a, b])
    assert [c.group.name for c in win.server_cards] == ["A", "B"]

    win._reorder_card("fake", "s:bbbbbbbb", before="s:aaaaaaaa")

    assert [c.group.name for c in win.server_cards] == ["B", "A"]
    saved = SettingsStore(path=win.ctx.store.path).load()
    assert saved.card_order["fake"] == ["s:bbbbbbbb", "s:aaaaaaaa"]


def test_saved_order_survives_a_rerender(qapp, tmp_path):
    """1초 폴링이 화면을 다시 그려도 순서가 되돌아가면 안 된다."""
    a = ServerGroupMeta(id="aaaaaaaa", name="A")
    b = ServerGroupMeta(id="bbbbbbbb", name="B")
    win = make_window(qapp, tmp_path, groups=[a, b])
    win._reorder_card("fake", "s:bbbbbbbb", before="s:aaaaaaaa")

    win.refresh(fetch=False)

    assert [c.group.name for c in win.server_cards] == ["B", "A"]


def test_reorder_across_owner_groups_is_rejected(qapp, tmp_path):
    """터널이 어느 기기 소속인지는 자격증명 위치라 드래그로 바꿀 수 없다."""
    a = ServerGroupMeta(id="aaaaaaaa", name="A")
    win = make_window(qapp, tmp_path, groups=[a])

    moved = win._reorder_card("ssh:다른기기", "s:aaaaaaaa", before=None)

    assert moved is False
    assert "ssh:다른기기" not in win.ctx.store.settings.card_order


# ---- 삭제 ----

def test_deleting_a_group_stops_servers_and_removes_it(qapp, tmp_path):
    server = docker_server()
    group = ServerGroupMeta(id="aabbccdd", name="백엔드", servers=[server])
    win = make_window(qapp, tmp_path, groups=[group])
    stopped = []
    win.ctx.manager.stop_service = lambda owner, route: stopped.append(route.id)

    win._do_delete_server_group(win.server_cards[0])

    assert stopped == [server.id]
    assert win.ctx.store.settings.server_groups_for("fake") == []
    assert win.server_cards == []


def test_drop_before_uses_real_card_geometry(qapp, tmp_path):
    """드롭 지점 y가 어느 카드 앞인지 실제 위젯 위치로 계산되어야 한다."""
    a = ServerGroupMeta(id="aaaaaaaa", name="A")
    b = ServerGroupMeta(id="bbbbbbbb", name="B")
    win = make_window(qapp, tmp_path, groups=[a, b])
    win.resize(900, 600)
    win.show()
    QApplication.processEvents()
    win.card_list.layout().activate()

    first, second = win.server_cards
    # 첫 카드 위쪽 절반 -> 첫 카드 앞
    assert win._drop_before("fake", first.y() + 2, "s:bbbbbbbb") == "s:aaaaaaaa"
    # 둘째 카드 아래쪽 절반 -> 맨 뒤
    assert win._drop_before("fake", second.y() + second.height() - 2,
                            "s:aaaaaaaa") is None
    win.close()


# ---- 장치 주소 링크 ----

def test_server_row_shows_device_ip_link(qapp, tmp_path, monkeypatch):
    """도메인이 없는 대신, 그 장치에서 열리는 주소를 링크로 보여준다."""
    monkeypatch.setattr("app.ui.main_window.local_lan_ip", lambda: "192.168.0.10")
    group = ServerGroupMeta(id="aabbccdd", name="백엔드", servers=[docker_server()])
    win = make_window(qapp, tmp_path, groups=[group])

    row = win.server_cards[0].route_rows[0]
    assert row.device_link == "http://192.168.0.10:8000"
    # 표시 문구는 열 폭에 맞춰 줄어들 수 있으므로 툴팁으로 전체를 확인한다
    assert row.link_label.toolTip() == "http://192.168.0.10:8000 열기"


def test_server_row_link_falls_back_when_host_unknown(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("app.ui.main_window.local_lan_ip", lambda: "")
    group = ServerGroupMeta(id="aabbccdd", name="백엔드", servers=[docker_server()])
    win = make_window(qapp, tmp_path, groups=[group])

    row = win.server_cards[0].route_rows[0]
    assert row.device_link == ""
    assert "주소 없음" in row.link_label.text()


def test_remote_target_uses_the_ssh_host(qapp, tmp_path, monkeypatch):
    """원격 대상은 SSH로 접속하는 host를 그대로 쓴다 - 이미 닿고 있는 주소다."""
    from app.core.store import SshProfile

    monkeypatch.setattr("app.ui.main_window.local_lan_ip", lambda: "192.168.0.10")
    group = ServerGroupMeta(id="aabbccdd", name="백엔드", servers=[docker_server()])
    win = make_window(qapp, tmp_path, groups=[group])
    win.ctx.runner.profile = SshProfile(name="rpi", host="10.0.0.7")

    win._device_hosts.clear()
    assert win._device_host() == "10.0.0.7"


def test_clicking_the_link_opens_the_browser(qapp, tmp_path, monkeypatch):
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent

    monkeypatch.setattr("app.ui.main_window.local_lan_ip", lambda: "192.168.0.10")
    opened = []
    monkeypatch.setattr("app.ui.main_window.webbrowser",
                        type("W", (), {"open": staticmethod(opened.append)}))
    group = ServerGroupMeta(id="aabbccdd", name="백엔드", servers=[docker_server()])
    win = make_window(qapp, tmp_path, groups=[group])

    row = win.server_cards[0].route_rows[0]
    event = QMouseEvent(QMouseEvent.Type.MouseButtonRelease, QPointF(1, 1),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.NoModifier)
    row.link_label.mouseReleaseEvent(event)

    assert opened == ["http://192.168.0.10:8000"]


# ---- 진행 상황 한 줄 ----

def test_server_toggle_reports_start_then_completion(qapp, tmp_path):
    """켜는 중 -> 응답 대기 -> 켜졌습니다. 한 줄이 상황에 따라 계속 바뀐다."""
    group = ServerGroupMeta(id="aabbccdd", name="백엔드", servers=[docker_server()])
    win = make_window(qapp, tmp_path, groups=[group])
    win.ctx.manager.start_service = lambda owner, route: None
    ready = {"on": False}
    win.ctx.manager.service_running = lambda owner, route: ready["on"]
    win.ctx.manager.service_pending = lambda owner, route: not ready["on"]

    row = win.server_cards[0].route_rows[0]
    row._on_server_toggled(True)

    assert "켜는 중" in win.info_label.text()
    win._process_progress_watches()
    assert "웹이 열리기" in win.info_label.text()   # 아직 준비 전

    ready["on"] = True
    win._process_progress_watches()

    assert "켜졌" in win.info_label.text()
    assert win._progress_watches == []
    assert win._info_hide_timer.isActive()      # 완료 문구는 저절로 사라진다


def test_server_toggle_off_reports_until_stopped(qapp, tmp_path):
    group = ServerGroupMeta(id="aabbccdd", name="백엔드", servers=[docker_server()])
    win = make_window(qapp, tmp_path, groups=[group])
    win.ctx.manager.stop_service = lambda owner, route: None
    running = {"on": True}
    win.ctx.manager.service_running = lambda owner, route: running["on"]
    win.ctx.manager.service_pending = lambda owner, route: False

    row = win.server_cards[0].route_rows[0]
    row._on_server_toggled(False)
    assert "끄는 중" in win.info_label.text()

    running["on"] = False
    win._process_progress_watches()

    assert "껐" in win.info_label.text()
    assert win._progress_watches == []


def test_repeated_toggling_keeps_only_the_latest_watch(qapp, tmp_path):
    """켰다 껐다를 빠르게 하면 반대 방향 감시가 둘 다 남아 문구가 흔들린다."""
    group = ServerGroupMeta(id="aabbccdd", name="백엔드", servers=[docker_server()])
    win = make_window(qapp, tmp_path, groups=[group])
    win.ctx.manager.start_service = lambda owner, route: None
    win.ctx.manager.stop_service = lambda owner, route: None
    win.ctx.manager.service_running = lambda owner, route: False
    win.ctx.manager.service_pending = lambda owner, route: True

    row = win.server_cards[0].route_rows[0]
    row._on_server_toggled(True)
    row._on_server_toggled(False)

    assert len(win._progress_watches) == 1
    assert win._progress_watches[0]["want"] is False


def test_same_text_does_not_reset_the_auto_hide_timer(qapp, tmp_path):
    """1초마다 같은 문구를 다시 넣으면 완료 알림이 영영 안 사라진다."""
    win = make_window(qapp, tmp_path)
    win._set_progress("완료!", auto_hide_ms=5000)
    first = win._info_hide_timer.remainingTime()

    win._set_progress("완료!", auto_hide_ms=5000)

    assert win._info_hide_timer.remainingTime() <= first


def test_tunnel_watch_reports_until_running(qapp, tmp_path):
    """터널도 같은 한 줄을 쓴다: 켜는 중 -> 켜졌습니다."""
    from app.core.process_mgr import TunnelState

    win = make_window(qapp, tmp_path)
    state = {"v": TunnelState.STARTING}
    win.ctx.manager.tunnel_state = lambda name: state["v"]

    win._watch(kind="tunnel", label="my-tunnel", want=True, timeout=120,
               name="my-tunnel")
    win._process_progress_watches()
    assert "my-tunnel" in win.info_label.text()
    assert "켜는 중" in win.info_label.text()

    state["v"] = TunnelState.RUNNING
    win._process_progress_watches()

    assert "켜졌" in win.info_label.text()
    assert win._progress_watches == []
