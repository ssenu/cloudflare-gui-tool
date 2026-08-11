"""공용 UI 위젯: 토글 스위치, 위험(삭제) 메뉴 항목 등.

core 모듈에 Qt 의존성이 유입되지 않도록 이 모듈은 app/ui 안에서만 사용한다.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QRectF, QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
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
        # 자식 생성 과정에서 resizeEvent가 먼저 올 수 있어 미리 선언해 둔다.
        self.close_btn: QPushButton | None = None
        self._content_pref = content.size()
        self._clamping = False  # 우리가 건 resize를 선호 크기로 오인하지 않기 위한 가드
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

        # 모달마다 공통으로 오른쪽 위 닫기 버튼을 얹는다. 내용 위젯이 아니라
        # 오버레이의 자식이라 어떤 다이얼로그를 넣어도 자동으로 붙는다.
        from app.ui.icons import make_icon  # 순환 import 방지를 위한 지연 로드

        self.close_btn = QPushButton(self)
        self.close_btn.setFixedSize(26, 26)
        self.close_btn.setToolTip("닫기")
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if palette:
            self.close_btn.setIcon(make_icon("close", palette["muted"], 20))
            self.close_btn.setStyleSheet(
                "QPushButton { background: transparent; border: none; }"
                f"QPushButton:hover {{ background: {palette['panel2']};"
                " border-radius: 6px; }")
        self.close_btn.clicked.connect(self._request_close)

        self._recompute_pref()

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
            # 내용이 스스로 크기를 바꾼 경우(마법사 페이지 전환 등)만 선호 크기 갱신.
            # 오버레이가 건 클램프 resize는 제외해야 한 번 줄어든 크기가
            # 선호 크기로 굳어 영구히 쪼그라드는 것을 막는다.
            size = self._content.size()
            if not self._clamping and size.width() > 0 and size.height() > 0:
                self._content_pref = size
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
        # 부모가 아직 레이아웃 전이라 작을 때 클램프하면 내용이 영구히 쪼그라든다.
        # 선호 크기를 따로 들고 있다가, 공간이 생기면 다시 키운다.
        pref = self._content_pref
        if area.width() > 200 and area.height() > 200:
            max_w = int(area.width() * self.MAX_CONTENT_RATIO)
            max_h = int(area.height() * self.MAX_CONTENT_RATIO)
            target_w = min(pref.width(), max_w)
            target_h = min(pref.height(), max_h)
            if (c.width(), c.height()) != (target_w, target_h):
                self._clamping = True
                try:
                    c.resize(target_w, target_h)
                finally:
                    self._clamping = False
        c.move(max(0, (area.width() - c.width()) // 2),
               max(0, (area.height() - c.height()) // 2))
        # 닫기 버튼을 내용 카드의 오른쪽 위 모서리 안쪽에 붙인다.
        if self.close_btn is None:
            return
        self.close_btn.move(c.x() + c.width() - self.close_btn.width() - 8,
                            c.y() + 8)
        self.close_btn.raise_()

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

    def _recompute_pref(self) -> None:
        """내용의 자연스러운 크기를 다시 계산해 적용한다.

        최상위 창이 아닌 자식 위젯이 되면 adjustSize()가 줄바꿈 라벨의
        heightForWidth를 제대로 반영하지 못해 높이가 0에 가깝게 잡힌다.
        폭을 먼저 정하고 그 폭 기준 높이를 직접 계산한다.
        """
        content = self._content
        pref_w = max(content.sizeHint().width(), content.minimumWidth())
        pref_h = -1
        if content.layout() is not None:
            pref_h = content.layout().heightForWidth(pref_w)
        if pref_h <= 0:
            pref_h = max(content.sizeHint().height(), content.minimumHeight())
        self._content_pref = QSize(pref_w, pref_h)
        self._clamping = True
        try:
            # QDialog가 자식 위젯 상태에서 show() 시 최소 크기로 붕괴하는 것을
            # 막기 위해 최소 크기로 못박는다. 스스로 크기를 관리하는 다이얼로그
            # (마법사의 setFixedHeight)는 이후 호출에서 이 값을 덮어쓴다.
            content.setMinimumSize(pref_w, pref_h)
            content.resize(pref_w, pref_h)
        finally:
            self._clamping = False

    def refit(self) -> None:
        """내용 위젯을 표시한 뒤 호출한다.

        QDialog는 show() 시점에 스스로 크기를 다시 잡는데, 자식 위젯 상태에서는
        그 값이 잘못 나온다(높이가 최소값으로 붕괴). 표시 후 한 번 더 맞춘다.
        """
        self._recompute_pref()
        # 스스로 크기를 계산하는 내용 위젯(마법사)은 그 결과를 우선한다.
        fit = getattr(self._content, "fit_to_content", None)
        if callable(fit):
            fit()
            size = self._content.size()
            if size.width() > 0 and size.height() > 0:
                self._content_pref = size
        self._center_content()

    def _request_close(self) -> None:
        reject = getattr(self._content, "reject", None)
        if callable(reject):
            reject()
        else:
            self.cleanup()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._request_close()
            return
        super().keyPressEvent(event)

    def cleanup(self) -> None:
        parent = self.parent()
        if parent is not None:
            parent.removeEventFilter(self)
        self.hide()
        self.deleteLater()
