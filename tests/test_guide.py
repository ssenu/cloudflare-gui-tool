"""사용 흐름 안내(?) 창: 모달 안에서 좌우 스크롤 없이 들어가야 한다.

예전에는 1040x780으로 잡고 가로로 4개짜리 고정 폭(200px) 단계 상자를
늘어놓아, 부모의 90%로 제한되는 모달 폭보다 내용이 넓어 좌우로 잘렸다.
"""
from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QScrollArea

from app.context import AppContext
from app.core.runner import RunResult
from app.core.store import SettingsStore
from app.ui.guide import GuideDialog
from app.ui.main_window import MainWindow
from tests.fake_runner import FakeRunner

LIST_TUNNELS_CMD = ("cloudflared", "tunnel", "list", "--output", "json")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def make_window(tmp_path, size=(820, 620)) -> MainWindow:
    store = SettingsStore(path=str(tmp_path / "settings.json"))
    ctx = AppContext(store=store)
    runner = FakeRunner(home="/home/fake")
    runner.run_results[LIST_TUNNELS_CMD] = RunResult(0, "[]", "")
    ctx.runner = runner
    ctx.local_runner = runner
    win = MainWindow(ctx)
    win._timer.stop()
    win.refresh()
    win.resize(*size)
    win.show()
    QApplication.processEvents()
    return win


def open_guide(win) -> GuideDialog:
    dlg = GuideDialog(win.ctx, win)
    win._open_modal(dlg)
    QApplication.processEvents()
    return dlg


def test_no_horizontal_scrolling(qapp, tmp_path):
    win = make_window(tmp_path)
    dlg = open_guide(win)

    scroll = dlg.findChild(QScrollArea)
    assert scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    bar = scroll.horizontalScrollBar()
    assert bar.maximum() == 0  # 스크롤할 여지 자체가 없어야 한다


def test_fits_inside_small_window(qapp, tmp_path):
    """작은 창에서도 모달이 부모 밖으로 넘치지 않아야 한다."""
    win = make_window(tmp_path, size=(700, 520))
    dlg = open_guide(win)

    assert dlg.width() <= win.width()
    assert dlg.height() <= win.height()
    assert dlg.findChild(QScrollArea).horizontalScrollBar().maximum() == 0


def test_uses_available_height_not_tiny_default(qapp, tmp_path):
    """스크롤 영역을 품은 다이얼로그는 그냥 두면 아주 작게 잡힌다."""
    win = make_window(tmp_path)
    dlg = open_guide(win)

    assert dlg.height() > 450  # 몇 줄만 보이는 상태가 아니어야 한다
    assert dlg.width() >= 480


def test_no_fixed_width_children(qapp, tmp_path):
    """고정 폭 위젯이 있으면 좁은 창에서 다시 좌우로 넘친다(회귀 방지).

    번호 배지(22px)만 예외 - 원형이라 폭이 고정되어야 한다.
    """
    win = make_window(tmp_path)
    dlg = open_guide(win)

    from PyQt6.QtWidgets import QLabel
    wide_fixed = [w for w in dlg.findChildren(QLabel)
                  if w.minimumWidth() == w.maximumWidth() and w.minimumWidth() > 40]
    assert wide_fixed == []
