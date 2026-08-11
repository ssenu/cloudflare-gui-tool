"""공용 UI 위젯: 토글 스위치, 위험(삭제) 메뉴 항목 등.

core 모듈에 Qt 의존성이 유입되지 않도록 이 모듈은 app/ui 안에서만 사용한다.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
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

    def update_tooltip(self) -> None:
        """켜기/끄기 툴팁을 현재 체크 상태에 맞게 갱신한다.

        폴링으로 setChecked()를 blockSignals(True) 상태에서 호출하면 toggled
        시그널이 안 나가 _on_toggled()가 자동으로 불리지 않는다. 그런 경로에서는
        이 메서드를 명시적으로 호출해야 한다.
        """
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


class Spinner(QWidget):
    """전이(pending) 상태를 알리는 작은 회전 스피너.

    지름 16px, 12단계 회전(80ms 간격). 숨겨져 있을 때는 타이머를 돌리지
    않는다(보이지 않는 위젯이 CPU를 쓰지 않도록) - start()/stop()이 타이머
    수명을 관리하고, showEvent/hideEvent에서도 안전망으로 맞춰준다.
    """

    DIAMETER = 16
    STEPS = 12
    INTERVAL_MS = 80

    def __init__(self, palette: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self._palette = palette
        self._step = 0
        self.setFixedSize(self.DIAMETER, self.DIAMETER)
        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._advance)

    def set_palette(self, palette: dict) -> None:
        self._palette = palette
        self.update()

    def start(self) -> None:
        if self.isVisible():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _advance(self) -> None:
        self._step = (self._step + 1) % self.STEPS
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self._palette["accent"])
        pen = QPen(color)
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        rect = QRectF(2, 2, self.DIAMETER - 4, self.DIAMETER - 4)
        start_angle = int(-self._step * (360 / self.STEPS) * 16)
        span_angle = int(270 * 16)
        painter.drawArc(rect, start_angle, span_angle)
        painter.end()


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
