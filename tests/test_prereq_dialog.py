"""준비물 점검 모달: 표시, '다음부터 보지 않기' 저장, 대상 전환 시 노출 규칙."""
from __future__ import annotations

import time

import pytest
from PyQt6.QtWidgets import QApplication

from app.context import AppContext
from app.core.runner import RunResult
from app.core.store import SettingsStore, SshProfile
from app.ui.prereq_dialog import PrereqDialog
from tests.fake_runner import FakeRunner
from tests.test_prereq import runner_with


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def make_ctx(tmp_path, runner: FakeRunner) -> AppContext:
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    ctx = AppContext(store=store)
    runner.name = "ssh:webPi"
    ctx.runner = runner
    ctx.store.settings.ssh_profiles.append(SshProfile(name="webPi", host="1.2.3.4"))
    return ctx


def wait_for_check(dlg, timeout: float = 3.0):
    """점검은 워커 스레드에서 돈다 - 끝날 때까지 이벤트 루프를 돌린다."""
    deadline = time.monotonic() + timeout
    while dlg._poller.is_busy() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.005)
    QApplication.processEvents()


def open_dialog(tmp_path, runner):
    ctx = make_ctx(tmp_path, runner)
    dlg = PrereqDialog(ctx, "webPi", None)
    wait_for_check(dlg)
    return ctx, dlg


def test_shows_one_row_per_item(qapp, tmp_path):
    _ctx, dlg = open_dialog(tmp_path, runner_with())
    assert [r.key for r in dlg.results] == ["cloudflared", "git", "docker"]
    assert dlg.items_box.count() == 3
    assert "모두 준비" in dlg.summary_label.text()


def test_skip_checkbox_enabled_only_when_all_ok(qapp, tmp_path):
    _ctx, ok_dlg = open_dialog(tmp_path, runner_with())
    assert ok_dlg.skip_chk.isEnabled()

    _ctx2, bad_dlg = open_dialog(tmp_path, runner_with(git=False))
    assert not bad_dlg.skip_chk.isEnabled()   # 문제가 있으면 끌 수 없다


def test_checking_skip_persists_for_that_target(qapp, tmp_path):
    ctx, dlg = open_dialog(tmp_path, runner_with())
    dlg.skip_chk.setChecked(True)
    dlg.reject()   # 확인/닫기

    assert ctx.store.settings.skip_prereq_check("ssh:webPi")
    # 저장까지 됐는지 재로드로 확인
    reloaded = SettingsStore(path=ctx.store.path).load()
    assert reloaded.skip_prereq_check("ssh:webPi")
    assert not reloaded.skip_prereq_check("ssh:other")  # 그 기기만


def test_not_checking_leaves_target_checked_next_time(qapp, tmp_path):
    ctx, dlg = open_dialog(tmp_path, runner_with())
    dlg.reject()

    assert not ctx.store.settings.skip_prereq_check("ssh:webPi")


def test_failed_state_cannot_be_skipped_even_if_forced(qapp, tmp_path):
    """문제가 있는 기기를 다시 안 보게 만들면 원인 모를 실패로 되돌아간다."""
    ctx, dlg = open_dialog(tmp_path, runner_with(docker=False))
    dlg.skip_chk.setChecked(True)   # 비활성이지만 강제로 체크해 본다
    dlg.reject()

    assert not ctx.store.settings.skip_prereq_check("ssh:webPi")


def test_recheck_updates_result(qapp, tmp_path):
    ctx, dlg = open_dialog(tmp_path, runner_with(git=False))
    assert not dlg.skip_chk.isEnabled()

    # 사용자가 git을 설치하고 '다시 검사'를 누른 상황
    ctx.runner.run_results[("git", "--version")] = RunResult(0, "git version 2.53.0", "")
    dlg.start_check()
    wait_for_check(dlg)

    assert dlg.skip_chk.isEnabled()
    assert "모두 준비" in dlg.summary_label.text()


def test_check_failure_shows_message(qapp, tmp_path):
    ctx, dlg = open_dialog(tmp_path, runner_with())

    def boom():
        raise OSError("연결 끊김")

    dlg._poller.run(boom)
    wait_for_check(dlg)

    assert "점검에 실패" in dlg.summary_label.text()


# ---- 대상 전환 시 노출 규칙 ----

def test_local_target_is_never_checked(qapp, tmp_path):
    from app.ui.main_window import MainWindow

    runner = FakeRunner(home="/home/fake")
    runner.run_results[("cloudflared", "tunnel", "list", "--output", "json")] = \
        RunResult(0, "[]", "")
    store = SettingsStore(path=str(tmp_path / "s.json"))
    ctx = AppContext(store=store)
    ctx.runner = runner
    ctx.local_runner = runner
    win = MainWindow(ctx)
    win._timer.stop()

    opened = []
    win._open_modal = lambda dlg: opened.append(dlg)
    win._maybe_check_prereqs()

    assert opened == []  # 내 PC는 이 점검 대상이 아니다


def test_remote_target_is_checked_until_skipped(qapp, tmp_path):
    from app.ui.main_window import MainWindow

    local = FakeRunner(home="/home/fake")
    local.run_results[("cloudflared", "tunnel", "list", "--output", "json")] = \
        RunResult(0, "[]", "")
    store = SettingsStore(path=str(tmp_path / "s.json"))
    ctx = AppContext(store=store)
    ctx.runner = ctx.local_runner = local
    win = MainWindow(ctx)
    win._timer.stop()

    remote = runner_with()
    remote.name = "ssh:webPi"
    remote.run_results[("cloudflared", "tunnel", "list", "--output", "json")] = \
        RunResult(0, "[]", "")
    ctx.runner = remote

    opened = []
    win._open_modal = lambda dlg: opened.append(dlg)

    win._maybe_check_prereqs()
    assert len(opened) == 1          # 최초 1회는 무조건

    ctx.store.settings.set_skip_prereq_check("ssh:webPi", True)
    win._maybe_check_prereqs()
    assert len(opened) == 1          # 체크해 뒀으면 더 이상 뜨지 않는다

    for dlg in opened:
        dlg._poller.stop()
