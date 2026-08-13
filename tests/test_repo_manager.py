"""프로젝트 목록: 카드 표시, 프로젝트별 ⋮ 메뉴, git pull 중 상태 표시.

표에서 카드로 바꾼 이유는 상태 동그라미와 ⋮ 버튼이 격자 위에 얹힌 별개
물체처럼 떠 보였기 때문이다. 카드에서는 동그라미가 이름 앞 상태 표시로 읽힌다.
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QPushButton

from app.context import AppContext
from app.core.git_repo import STATE_CLONING, STATE_NONE, STATE_READY
from app.core.runner import RunResult
from app.core.store import RepoMeta, SettingsStore
from app.ui.repo_manager import (DEPLOYING_LABEL, PULLING_LABEL, STATE_DOT_COLORS, RepoCard,
                                 RepoManagerDialog)
from tests.fake_runner import FakeRunner


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def make_dialog(tmp_path, count: int = 2):
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    ctx = AppContext(store=store)
    runner = FakeRunner(home="/home/pi")
    runner.name = "ssh:webPi"
    ctx.runner = runner
    ctx.store.settings.repos["ssh:webPi"] = [
        RepoMeta(id=f"r{i}", name=f"app{i}", url=f"git@x:a/b{i}.git",
                 path=f"/srv/apps/app{i}")
        for i in range(count)]
    dlg = RepoManagerDialog(ctx, None)
    dlg._timer.stop()
    return dlg


def test_one_card_per_project(qapp, tmp_path):
    dlg = make_dialog(tmp_path)
    assert [c.repo.name for c in dlg.cards] == ["app0", "app1"]
    assert all(isinstance(c, RepoCard) for c in dlg.cards)
    assert dlg.empty_label.isHidden()


def test_empty_state_when_no_projects(qapp, tmp_path):
    dlg = make_dialog(tmp_path, count=0)
    assert dlg.cards == []
    assert not dlg.empty_label.isHidden()
    assert "클론 추가" in dlg.empty_label.text()


def test_card_shows_url_and_path_together(qapp, tmp_path):
    dlg = make_dialog(tmp_path, count=1)
    tip = dlg.cards[0].meta_label.toolTip()
    assert "git@x:a/b0.git" in tip and "/srv/apps/app0" in tip


def test_menu_opens_for_that_card(qapp, tmp_path, monkeypatch):
    dlg = make_dialog(tmp_path)
    monkeypatch.setattr("app.ui.repo_manager.QMenu.exec", lambda *a, **k: None)
    opened = {}
    monkeypatch.setattr("app.ui.env_editor.EnvEditorDialog.exec",
                        lambda self: opened.setdefault("repo", self.repo.name))

    dlg._edit_env(dlg.cards[1].repo)

    assert opened["repo"] == "app1"


def test_card_has_menu_button(qapp, tmp_path):
    dlg = make_dialog(tmp_path, count=1)
    assert dlg.cards[0].findChild(QPushButton) is not None


# ---- 상태 표시 ----

def test_ready_card_shows_commit(qapp, tmp_path):
    dlg = make_dialog(tmp_path, count=1)
    card = dlg.cards[0]
    card.update_state(STATE_READY, "7fd1a60abcdef")
    assert "준비됨" in card.state_label.text()
    assert "7fd1a60" in card.state_label.text()


def test_cloning_card_uses_progress_color(qapp, tmp_path):
    dlg = make_dialog(tmp_path, count=1)
    card = dlg.cards[0]
    card.update_state(STATE_CLONING, "")
    assert card.state_label.text() == "클론 중"
    assert STATE_DOT_COLORS[STATE_CLONING] in card.dot.styleSheet()


def test_pull_shows_update_label_and_orange_dot(qapp, tmp_path):
    """git pull 중에는 왼쪽 동그라미가 진행 색(주황)이어야 한다."""
    dlg = make_dialog(tmp_path, count=1)
    card = dlg.cards[0]
    card.update_state(STATE_CLONING, "", PULLING_LABEL)

    assert card.state_label.text() == PULLING_LABEL
    assert STATE_DOT_COLORS[STATE_CLONING] in card.dot.styleSheet()


def test_update_repo_clears_ready_cache_so_pull_shows_progress(qapp, tmp_path):
    """ready 캐시를 비우지 않으면 pull 중에도 계속 '준비됨'으로 보인다."""
    dlg = make_dialog(tmp_path, count=1)
    repo = dlg.cards[0].repo
    runner = dlg.ctx.runner
    # 클론이 끝난 상태를 흉내낸다: .git 존재 + 커밋 조회 성공
    runner.files[f"{repo.path}/.git"] = ""
    runner.run_results[("git", "-C", repo.path, "rev-parse", "--short", "HEAD")] = \
        RunResult(0, "abc1234", "")
    dlg._tick()
    assert dlg._state_cache[repo.id].get("ready") is True

    dlg._update_repo(repo)

    assert repo.id in dlg._pulling
    # PID를 기록해야 상태 기계가 "진행 중"으로 본다(클론과 동일한 경로)
    assert runner.file_exists(dlg.reg.pid_path(dlg.reg.unit_clone(repo.id)))
    assert dlg.cards[0].state_label.text() == PULLING_LABEL
    assert STATE_DOT_COLORS[STATE_CLONING] in dlg.cards[0].dot.styleSheet()


def test_pulling_flag_cleared_when_process_finishes(qapp, tmp_path):
    dlg = make_dialog(tmp_path, count=1)
    repo = dlg.cards[0].repo
    dlg._pulling.add(repo.id)

    dlg._tick()  # PID 파일이 없으므로 CLONING이 아니다

    assert repo.id not in dlg._pulling


def test_none_state_dot_is_grey(qapp, tmp_path):
    dlg = make_dialog(tmp_path, count=1)
    card = dlg.cards[0]
    card.update_state(STATE_NONE, "")
    assert STATE_DOT_COLORS[STATE_NONE] in card.dot.styleSheet()


# ---- 배포(pull + 재시작) ----

def test_deploy_runs_pull_and_compose_in_one_command(qapp, tmp_path, monkeypatch):
    dlg = make_dialog(tmp_path, count=1)
    repo = dlg.cards[0].repo
    runner = dlg.ctx.runner
    runner.files[f"{repo.path}/.git"] = ""
    runner.files[f"{repo.path}/docker-compose.yml"] = "services: {}"

    dlg._deploy_repo(repo)

    argv = runner.spawn_detached_calls[-1][0]
    line = argv[-1]
    assert "git pull --ff-only" in line
    assert "docker compose up --build -d" in line
    assert line.index("git pull") < line.index("docker compose")  # 받고 나서 띄운다
    assert repo.id in dlg._deploying
    assert dlg.cards[0].state_label.text() == DEPLOYING_LABEL


def test_deploy_without_compose_asks_and_pulls_only(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    dlg = make_dialog(tmp_path, count=1)
    repo = dlg.cards[0].repo
    dlg.ctx.runner.files[f"{repo.path}/.git"] = ""   # compose 파일은 없음
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)

    dlg._deploy_repo(repo)

    line = dlg.ctx.runner.spawn_detached_calls[-1][0][-1]
    assert "git pull --ff-only" in line
    assert "docker compose" not in line


def test_deploy_can_be_cancelled_when_no_compose(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    dlg = make_dialog(tmp_path, count=1)
    repo = dlg.cards[0].repo
    dlg.ctx.runner.files[f"{repo.path}/.git"] = ""
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.No)

    dlg._deploy_repo(repo)

    assert dlg.ctx.runner.spawn_detached_calls == []
