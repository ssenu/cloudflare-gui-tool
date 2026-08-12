"""프로젝트 목록: 선택 표시와 프로젝트별 ⋮ 메뉴.

"선택됐는지 모르겠다"는 문제를 두 방향에서 막는다 - 색(테마 QSS)과 글자
("선택됨: 이름"), 그리고 애초에 선택에 의존하지 않는 행별 메뉴.
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QApplication, QPushButton

from app.context import AppContext
from app.core.store import RepoMeta, SettingsStore
from app.ui.repo_manager import MENU_COL, RepoManagerDialog
from app.ui.theme import build_qss
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


def test_every_row_has_menu_button(qapp, tmp_path):
    dlg = make_dialog(tmp_path)
    assert dlg.table.rowCount() == 2
    for row in range(2):
        assert dlg.table.cellWidget(row, MENU_COL) is not None


def test_selection_is_shown_as_text(qapp, tmp_path):
    dlg = make_dialog(tmp_path)
    assert dlg.selected_label.text() == ""  # 처음엔 선택 없음

    dlg.table.selectRow(1)
    assert dlg.selected_label.text() == "선택됨: app1"


def test_opening_menu_selects_that_row(qapp, tmp_path, monkeypatch):
    """어느 프로젝트에 대한 동작인지 화면에서도 분명해야 한다."""
    dlg = make_dialog(tmp_path)
    monkeypatch.setattr("app.ui.repo_manager.QMenu.exec", lambda *a, **k: None)

    repo = dlg._repos()[1]
    button = dlg.table.cellWidget(1, MENU_COL).findChild(QPushButton)
    dlg._open_menu(button, repo)

    assert dlg.table.currentRow() == 1
    assert dlg.selected_label.text() == "선택됨: app1"


def test_actions_take_explicit_repo_not_selection(qapp, tmp_path, monkeypatch):
    """행 메뉴는 선택 상태와 무관하게 그 행의 프로젝트를 대상으로 해야 한다."""
    dlg = make_dialog(tmp_path)
    dlg.table.selectRow(0)  # 0번을 선택해 둔 채로

    opened = {}
    monkeypatch.setattr("app.ui.env_editor.EnvEditorDialog.exec",
                        lambda self: opened.setdefault("repo", self.repo.name))

    dlg._edit_env(dlg._repos()[1])  # 1번에 대해 실행
    assert opened["repo"] == "app1"


def test_theme_defines_table_selection_color(qapp):
    # 선택 색이 빠져 있던 것이 "선택됐는지 모르겠다"의 원인이었다.
    for mode in ("dark", "light"):
        qss = build_qss(mode)
        assert "QTableWidget::item:selected" in qss
        assert ":!active" in qss  # 포커스를 잃어도 선택이 보여야 한다


def test_empty_list_has_no_rows(qapp, tmp_path):
    dlg = make_dialog(tmp_path, count=0)
    assert dlg.table.rowCount() == 0
    assert dlg.selected_label.text() == ""
