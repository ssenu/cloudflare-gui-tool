"""모달 오버레이의 닫기(X) 버튼이 내용과 겹치지 않아야 한다.

예전에는 내용 위젯의 위 여백에 20px을 '더하는' 방식이라, 기본 여백이 작은
다이얼로그에서는 버튼이 차지하는 높이(8+26=34)에 못 미쳐 첫 줄과 몇 px
겹쳤다. 지금은 필요한 만큼 끌어올린다.
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import (QApplication, QLabel, QLineEdit, QDialog,
                             QFormLayout, QVBoxLayout, QWidget)

from app.ui.theme import current_palette
from app.ui.widgets import ModalOverlay


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def make_host(qapp) -> QWidget:
    host = QWidget()
    host.resize(800, 600)
    host.show()
    return host


def open_overlay(host, content) -> ModalOverlay:
    from PyQt6.QtCore import Qt
    content.setWindowFlags(Qt.WindowType.Widget)
    overlay = ModalOverlay(host, content, current_palette("dark"))
    content.show()
    overlay.refit()
    QApplication.processEvents()
    return overlay


def test_close_button_does_not_overlap_first_row(qapp):
    dlg = QDialog()
    form = QFormLayout()
    form.addRow("이름", QLineEdit())
    form.addRow("값", QLineEdit())
    dlg.setLayout(form)

    host = make_host(qapp)
    overlay = open_overlay(host, dlg)

    btn = overlay.close_btn.geometry()
    for child in dlg.findChildren(QWidget):
        if child.parent() is not dlg or not child.isVisible():
            continue
        rect = child.geometry().translated(dlg.pos())
        assert not rect.intersects(btn), f"{child} 가 닫기 버튼과 겹친다"


def test_top_margin_is_at_least_button_height(qapp):
    dlg = QDialog()
    lay = QVBoxLayout()
    lay.setContentsMargins(10, 4, 10, 10)  # 위 여백이 아주 작은 경우
    lay.addWidget(QLabel("첫 줄"))
    dlg.setLayout(lay)

    host = make_host(qapp)
    open_overlay(host, dlg)

    assert dlg.layout().contentsMargins().top() >= ModalOverlay.close_space_needed()
    # 버튼이 실제로 차지하는 높이보다 커야 한다(그래야 겹치지 않는다)
    assert ModalOverlay.close_space_needed() > (
        ModalOverlay.CLOSE_BTN_INSET + ModalOverlay.CLOSE_BTN_SIZE)


def test_generous_top_margin_is_not_inflated(qapp):
    """이미 넉넉한 여백은 그대로 둔다 - 더하기 방식이면 계속 벌어진다."""
    dlg = QDialog()
    lay = QVBoxLayout()
    lay.setContentsMargins(10, 80, 10, 10)
    lay.addWidget(QLabel("첫 줄"))
    dlg.setLayout(lay)

    host = make_host(qapp)
    open_overlay(host, dlg)

    assert dlg.layout().contentsMargins().top() == 80


def test_reserve_runs_only_once(qapp):
    dlg = QDialog()
    lay = QVBoxLayout()
    lay.setContentsMargins(10, 4, 10, 10)
    lay.addWidget(QLabel("첫 줄"))
    dlg.setLayout(lay)

    host = make_host(qapp)
    overlay = open_overlay(host, dlg)
    first = dlg.layout().contentsMargins().top()

    overlay._reserve_close_space()
    overlay.refit()

    assert dlg.layout().contentsMargins().top() == first
