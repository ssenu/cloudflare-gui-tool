"""공용 UI 위젯: 토글 스위치, 위험(삭제) 메뉴 항목 등.

core 모듈에 Qt 의존성이 유입되지 않도록 이 모듈은 app/ui 안에서만 사용한다.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QVBoxLayout,
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


class ModalOverlay(QWidget):
    """부모 위젯 전체를 덮는 반투명 모달 오버레이.

    별도 OS 창(작업표시줄에 따로 뜨는 것)을 새로 여는 대신, 부모(주로
    MainWindow) 안에 그려지는 자식 위젯이다. 내용 위젯(주로
    `setWindowFlags(Qt.WindowType.Widget)`로 일반 위젯이 된 QDialog)을
    가운데 정렬해 얹고, 그 뒤쪽 영역은 반투명 배경으로 어둡게 칠하며
    클릭을 흡수해 뒤 화면을 조작하지 못하게 막는다.

    부모가 리사이즈되면 이벤트 필터로 크기를 맞추고, Esc 키는 내용
    위젯의 reject()를 호출해 닫는다. 사용이 끝나면 cleanup()을 호출해
    이벤트 필터를 해제하고 스스로를 정리해야 한다(보통 내용 위젯의
    finished 시그널에 연결).
    """

    def __init__(self, parent: QWidget, content: QWidget, palette: dict | None = None):
        super().__init__(parent)
        self._content = content
        self._bg_color = QColor(0, 0, 0, 140)  # 검정 55% 알파
        content.setParent(self)
        if palette:
            # 내용 위젯이 앱 배경과 같은 색이면 모달 경계가 보이지 않는다.
            # 카드처럼 패널 배경 + 테두리를 줘서 떠 있는 시트로 읽히게 한다.
            content.setObjectName("modalContent")
            content.setStyleSheet(
                f"QDialog#modalContent {{ background: {palette['panel']}; "
                f"border: 1px solid {palette['border']}; border-radius: 12px; }}")

        # 레이아웃 대신 직접 배치한다 - 자식이 네이티브 윈도우 핸들을 갖는
        # 경우(타이틀바 테마 적용 등) 레이아웃 정렬이 어긋나는 일이 있어서
        # 중앙 정렬을 계산으로 못박는다.
        self.setGeometry(parent.rect())
        parent.installEventFilter(self)
        content.installEventFilter(self)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.show()
        self.raise_()
        content.show()
        # 부모가 아직 화면에 없으면 showEvent가 오지 않으므로 여기서도 맞춘다.
        self._center_content()
        self.setFocus()

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() in (
                QEvent.Type.Resize, QEvent.Type.LayoutRequest):
            self.setGeometry(self.parent().rect())
        elif obj is self._content and event.type() == QEvent.Type.Resize:
            self._center_content()
        return False

    # 내용 위젯이 부모보다 커지지 않도록 제한하는 비율. GuideDialog(1040x780)
    # 처럼 큰 다이얼로그가 작은 창 안에서 화면 밖으로 넘치는 것을 막는다.
    MAX_CONTENT_RATIO = 0.9

    def _center_content(self) -> None:
        # 부모 rect를 기준으로 계산한다 - 부모가 아직 표시되기 전이면 self의
        # geometry가 아직 반영되지 않은 경우가 있다.
        parent = self.parent()
        area = parent.rect() if parent is not None else self.rect()
        if self.geometry() != area:
            self.setGeometry(area)
        c = self._content
        max_w = int(area.width() * self.MAX_CONTENT_RATIO)
        max_h = int(area.height() * self.MAX_CONTENT_RATIO)
        if max_w > 0 and max_h > 0 and (c.width() > max_w or c.height() > max_h):
            c.resize(min(c.width(), max_w), min(c.height(), max_h))
        c.move(max(0, (area.width() - c.width()) // 2),
               max(0, (area.height() - c.height()) // 2))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._center_content()

    def showEvent(self, event) -> None:
        # 부모가 생성 직후 레이아웃으로 커지는 경우가 있어 표시 시점에 한 번 더 맞춘다.
        parent = self.parent()
        if parent is not None:
            self.setGeometry(parent.rect())
        self._center_content()
        super().showEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._bg_color)

    def mousePressEvent(self, event) -> None:
        # 오버레이 배경(내용 위젯 바깥) 클릭은 뒤 화면으로 넘어가지 않게 흡수한다.
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            reject = getattr(self._content, "reject", None)
            if callable(reject):
                reject()
                return
        super().keyPressEvent(event)

    def cleanup(self) -> None:
        parent = self.parent()
        if parent is not None:
            parent.removeEventFilter(self)
        self.hide()
        self.deleteLater()
