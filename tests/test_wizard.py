"""TunnelWizard: DNS 충돌 시 덮어쓰기 버튼, 성공 시 자동 닫기 카운트다운."""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication

from app.context import AppContext
from app.core.runner import RunResult
from app.core.store import SettingsStore
from app.ui.wizard import TunnelWizard
from tests.fake_runner import FakeRunner


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def make_wizard(qapp, tmp_path) -> TunnelWizard:
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    ctx = AppContext(store=store)
    runner = FakeRunner(home="/home/fake")
    ctx.runner = runner
    ctx.local_runner = runner
    wiz = TunnelWizard(ctx, [], None)
    wiz._timer.stop()  # 이벤트 드레인은 테스트에서 수동으로 호출
    return wiz


def test_dns_conflict_shows_overwrite_button_with_hint(qapp, tmp_path):
    wiz = make_wizard(qapp, tmp_path)
    wiz._created = {"tunnel_id": "tid", "credentials": "/cred.json"}
    wiz._events.append(
        ("fail", "code: 1003, reason: ... already exists", True, "mysite", True))

    wiz._drain_events()

    assert not wiz.overwrite_btn.isHidden()
    assert not wiz.overwrite_hint.isHidden()
    assert not wiz.rollback_btn.isHidden()
    # 실패 시에는 자동으로 닫히지 않는다
    assert not wiz._close_timer.isActive()


def test_non_dns_conflict_failure_hides_overwrite_button(qapp, tmp_path):
    wiz = make_wizard(qapp, tmp_path)
    wiz._events.append(("fail", "그냥 실패", True, "mysite", False))

    wiz._drain_events()

    assert wiz.overwrite_btn.isHidden()
    assert not wiz.rollback_btn.isHidden()


def test_success_starts_close_countdown_and_updates_button_text(qapp, tmp_path):
    wiz = make_wizard(qapp, tmp_path)
    wiz.kind_combo.setCurrentIndex(0)
    wiz._events.append(("done", "mysite", "mysite.example.com", "http://localhost:8000"))

    wiz._drain_events()

    assert wiz._close_timer.isActive()
    assert wiz.next_btn.text() == "완료 (5)"
    assert wiz.created_meta is not None


def test_close_countdown_reaching_zero_accepts_dialog(qapp, tmp_path):
    wiz = make_wizard(qapp, tmp_path)
    wiz.kind_combo.setCurrentIndex(0)
    wiz._events.append(("done", "mysite", "mysite.example.com", "http://localhost:8000"))
    wiz._drain_events()

    results = []
    wiz.accepted.connect(lambda: results.append(True))
    for _ in range(5):
        wiz._on_close_tick()

    assert results == [True]
    assert not wiz._close_timer.isActive()


def test_clicking_done_button_closes_immediately(qapp, tmp_path):
    wiz = make_wizard(qapp, tmp_path)
    wiz.kind_combo.setCurrentIndex(0)
    wiz._events.append(("done", "mysite", "mysite.example.com", "http://localhost:8000"))
    wiz._drain_events()

    results = []
    wiz.accepted.connect(lambda: results.append(True))
    wiz._on_next_clicked()  # _next_mode == "done" -> accept()

    assert results == [True]
    assert not wiz._close_timer.isActive()


def test_close_timer_stopped_on_close_event(qapp, tmp_path):
    # 다이얼로그가 닫힌 뒤 타이머 발화로 크래시하지 않도록 정지되어야 한다.
    wiz = make_wizard(qapp, tmp_path)
    wiz.kind_combo.setCurrentIndex(0)
    wiz._events.append(("done", "mysite", "mysite.example.com", "http://localhost:8000"))
    wiz._drain_events()
    assert wiz._close_timer.isActive()

    from PyQt6.QtGui import QCloseEvent
    wiz.closeEvent(QCloseEvent())

    assert not wiz._close_timer.isActive()


# ---- 터널만 만들기 (도메인은 나중에 연결) ----

def test_tunnel_only_skips_domain_validation_and_jumps_to_run(qapp, tmp_path):
    wiz = make_wizard(qapp, tmp_path)
    wiz.name_edit.setText("mysite")
    wiz._go(1)
    wiz.later_chk.setChecked(True)

    # 서브도메인이 비어 있어도 통과해야 한다
    assert wiz._validate_current() == ""
    assert wiz.next_btn.text() == "생성 시작"
    assert not wiz.sub_edit.isEnabled()

    started = []
    wiz._start_creation = lambda: started.append(True)
    wiz._next()

    assert wiz._page_index == 4  # 3·4단계를 건너뛴다
    assert started == [True]


def test_tunnel_only_back_returns_to_domain_page(qapp, tmp_path):
    wiz = make_wizard(qapp, tmp_path)
    wiz.later_chk.setChecked(True)
    wiz._go(4)
    wiz._back()
    assert wiz._page_index == 1


def test_tunnel_only_done_creates_meta_without_routes(qapp, tmp_path):
    wiz = make_wizard(qapp, tmp_path)
    wiz._created = {"tunnel_id": "tid1"}
    wiz._events.append(("done", "mysite", "", ""))

    wiz._drain_events()

    assert wiz.created_meta is not None
    assert wiz.created_meta.routes == []
    assert wiz.created_tunnel_id == "tid1"


def test_tunnel_only_does_not_overwrite_saved_root_domain(qapp, tmp_path):
    # 도메인 칸을 쓰지 않았으므로 기억해 둔 루트 도메인을 지우면 안 된다.
    wiz = make_wizard(qapp, tmp_path)
    wiz.ctx.store.settings.root_domain = "example.com"
    wiz.domain_edit.setText("")
    wiz._events.append(("done", "mysite", "", ""))

    wiz._drain_events()

    assert wiz.ctx.store.settings.root_domain == "example.com"


def test_normal_path_still_creates_route(qapp, tmp_path):
    wiz = make_wizard(qapp, tmp_path)
    wiz._events.append(("done", "mysite", "a.example.com", "http://localhost:8000"))

    wiz._drain_events()

    assert len(wiz.created_meta.routes) == 1
    assert wiz.created_meta.routes[0].hostname == "a.example.com"
