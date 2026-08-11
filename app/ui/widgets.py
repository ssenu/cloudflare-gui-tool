"""공용 UI 위젯: 토글 스위치, 위험(삭제) 메뉴 항목 등.

core 모듈에 Qt 의존성이 유입되지 않도록 이 모듈은 app/ui 안에서만 사용한다.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QWidget,
    QWidgetAction,
)


class ToggleSwitch(QAbstractButton):
    """체크형 스위치 위젯. 켜짐: accent 배경 + 오른쪽 흰 노브.
    꺼짐: border 색 배경 + panel 색 노브(왼쪽)."""

    WIDTH = 44
    HEIGHT = 24

    def __init__(self, palette: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self._palette = palette
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.toggled.connect(self._on_toggled)
        self._update_tooltip()

    def set_palette(self, palette: dict) -> None:
        self._palette = palette
        self.update()

    def sizeHint(self):
        from PyQt6.QtCore import QSize

        return QSize(self.WIDTH, self.HEIGHT)

    def _on_toggled(self, _checked: bool) -> None:
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        self.setToolTip("켜기" if not self.isChecked() else "끄기")

    def paintEvent(self, _event) -> None:
        p = self._palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        track_rect = QRectF(1, 1, w - 2, h - 2)
        radius = track_rect.height() / 2

        checked = self.isChecked()
        track_color = QColor(p["accent"] if checked else p["border"])
        painter.setBrush(track_color)

        if self.hasFocus():
            pen_color = QColor(p["accent2"])
        else:
            pen_color = track_color
        painter.setPen(pen_color)
        painter.drawRoundedRect(track_rect, radius, radius)

        knob_d = h - 6
        knob_y = 3
        knob_x = w - knob_d - 3 if checked else 3
        knob_color = QColor(p["on_accent"]) if checked else QColor(p["panel"])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob_color)
        painter.drawEllipse(QRectF(knob_x, knob_y, knob_d, knob_d))

        painter.end()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self.update()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.update()


def danger_menu_action(menu: QMenu, text: str, palette: dict, on_trigger) -> QWidgetAction:
    """QMenu는 항목별 색을 QSS로 줄 수 없으므로 라벨 기반 위젯 액션으로 구현.
    기본은 danger 색 글씨, hover 시 danger 배경 + 흰 글씨."""
    action = QWidgetAction(menu)

    container = QWidget(menu)
    layout = QHBoxLayout(container)
    layout.setContentsMargins(20, 6, 20, 6)
    layout.setSpacing(0)

    label = QLabel(text, container)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    label.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
    label.setCursor(Qt.CursorShape.PointingHandCursor)
    label.setStyleSheet(
        f"""
        QLabel {{ color: {palette['danger']}; background: transparent; }}
        QLabel:hover {{ color: {palette['on_accent']}; background: {palette['danger']}; }}
        """
    )
    layout.addWidget(label)
    container.setLayout(layout)

    def _trigger(_checked: bool = False) -> None:
        on_trigger()
        menu.close()

    action.setDefaultWidget(container)
    action.triggered.connect(_trigger)

    def _mouse_release(event):
        _trigger()

    label.mouseReleaseEvent = _mouse_release  # type: ignore[method-assign]

    menu.addAction(action)
    return action
