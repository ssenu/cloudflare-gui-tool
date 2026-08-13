from __future__ import annotations

import time
import webbrowser

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (QApplication, QComboBox, QFrame, QHBoxLayout, QLabel,
                             QMenu, QMessageBox, QPushButton, QScrollArea,
                             QSizePolicy, QVBoxLayout, QWidget)

from app.context import AppContext
from app.core.cloudflared import CloudflaredError
from app.core.config_yml import get_routes, parse_config, set_routes
from app.core.process_mgr import TunnelState
from app.core.confirm import (group_by_owner, owner_group_label, owner_label,
                              route_display_label)
from app.core.store import RouteMeta, SshProfile, TunnelMeta, new_route_id
from app.ui.icons import make_icon
from app.ui.poller import BackgroundPoller
from app.ui.theme import STATE_COLORS, build_qss, current_palette, ensure_qss_icons
from app.ui.widgets import ModalOverlay, ToggleSwitch, danger_menu_action
from app.ui.winutil import apply_titlebar_theme
from app.ui.wizard import TunnelWizard

ADD_SSH_TARGET = "__add__"

TOP_BTN_HEIGHT = 34  # 상단 바 버튼/콤보 공통 높이

LOADING_TEXT = "터널 목록을 불러오는 중..."
EMPTY_TEXT = ("아직 터널이 없습니다.\n"
              "위의 '＋터널 생성'으로 첫 터널을 만들어 보세요.")

# C1: 폴링(_tick) 간격. 정상일 땐 1초, 대상 연결이 끊긴 것으로 보이면 5초로
# 물러나 실패한 원격 호출을 계속 재시도하며 UI를 붙잡지 않게 한다.
POLL_INTERVAL_NORMAL_MS = 1000
POLL_INTERVAL_BACKOFF_MS = 5000
POLL_FAILURE_HINT_THRESHOLD = 3

STATE_LABELS = {
    TunnelState.STOPPED: "중지됨",
    TunnelState.STARTING: "시작 중...",
    TunnelState.RUNNING: "실행 중",
    TunnelState.ERROR: "오류",
}

# O2: 자격증명이 이 대상에 없어 실행할 수 없는 터널의 토글 툴팁.
NO_CREDENTIALS_TOOLTIP = (
    "이 터널의 자격증명 파일이 이 기기에 없습니다. "
    "만든 기기에서 실행하거나, `~/.cloudflared/<uuid>.json`을 이 기기로 복사하세요.")


def _elide(text: str, metrics, width: int) -> str:
    return metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, width)


TOGGLE_LABEL_WIDTH = 30  # 헤더/라우트 행의 토글 라벨 폭을 맞춰 정렬을 유지한다


def _toggle_label(text: str, palette: dict) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {palette['muted']}; font-size: 11px;")
    lbl.setFixedWidth(TOGGLE_LABEL_WIDTH)
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return lbl


ROUTE_DOT_SIZE = 8  # 터널 동그라미(12px)보다 작게: 라우트가 하위임을 크기로 표현
ROUTE_DOT_MUTED = "muted"  # 서비스 미등록 라우트의 "해당 없음" 색은 팔레트 muted 사용

# I5: 이름/도메인/서비스 열을 고정 폭으로 잡아 여러 행에서 세로 정렬이
# 유지되게 한다. 서비스 열은 나머지 공간을 채운다(Expanding).
ROUTE_NAME_COL_WIDTH = 120
ROUTE_DOMAIN_COL_WIDTH = 220
ROUTE_SERVICE_COL_WIDTH = 220  # 텍스트 elide 계산용 목표 폭(실제 폭은 Expanding)


def _route_vline(palette: dict) -> QFrame:
    """열 사이의 옅은 세로 구분선. border 색이지만 행 높이보다 짧게 잡아 은은하게 보이게 한다."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.VLine)
    line.setFixedWidth(1)
    line.setFixedHeight(14)
    line.setStyleSheet(f"background: {palette['border']}; border: none;")
    return line


class RouteRow(QWidget):
    """라우트 한 줄: 상태 동그라미, 이름/도메인/서비스 3열, 서버 토글(또는 등록 버튼), 메뉴."""

    def __init__(self, card: "TunnelCard", route: RouteMeta):
        super().__init__(card)
        self.card = card
        self.route = route
        win = card.win
        palette = current_palette(win.ctx.store.settings.theme)
        icon_color = palette["text"]

        self.dot = QLabel()
        self.dot.setFixedSize(ROUTE_DOT_SIZE, ROUTE_DOT_SIZE)
        self._dot_color = ""  # 마지막으로 적용한 색(같으면 다시 칠하지 않는다)

        # I4/I5: 이름 열 - 비어 있으면 hostname의 첫 라벨을 muted 색으로 대신 보여준다.
        label_text, is_placeholder = route_display_label(route.label, route.hostname)
        self.name_label = QLabel()
        self.name_label.setFixedWidth(ROUTE_NAME_COL_WIDTH)
        name_metrics = self.name_label.fontMetrics()
        self.name_label.setText(_elide(label_text or "-", name_metrics,
                                       ROUTE_NAME_COL_WIDTH - 8))
        self.name_label.setToolTip(label_text or "(이름 없음)")
        if is_placeholder:
            self.name_label.setStyleSheet(f"color: {palette['muted']};")

        # 도메인은 눌러서 브라우저로 여는 링크다. 라우트를 만든 직후 "실제로
        # 열리나" 확인하는 것이 가장 흔한 다음 행동이라, 주소를 복사해 붙여넣는
        # 과정을 없앤다. hostname이 없으면 링크가 아니라 안내 문구로 둔다.
        hostname = route.hostname or "(hostname 미설정)"
        self.domain_label = QLabel()
        self.domain_label.setFixedWidth(ROUTE_DOMAIN_COL_WIDTH)
        domain_metrics = self.domain_label.fontMetrics()
        elided = _elide(hostname, domain_metrics, ROUTE_DOMAIN_COL_WIDTH - 8)
        if route.hostname:
            self.domain_label.setText(elided)
            self.domain_label.setStyleSheet(
                f"color: {palette['accent2']}; text-decoration: underline;")
            self.domain_label.setCursor(Qt.CursorShape.PointingHandCursor)
            self.domain_label.setToolTip(f"https://{hostname} 열기")
            self.domain_label.mouseReleaseEvent = self._open_site
        else:
            self.domain_label.setText(elided)
            self.domain_label.setStyleSheet(f"color: {palette['muted']};")
            self.domain_label.setToolTip(hostname)

        service = route.service or "(서비스 미설정)"
        self.service_label = QLabel()
        service_metrics = self.service_label.fontMetrics()
        self.service_label.setText(_elide(service, service_metrics,
                                          ROUTE_SERVICE_COL_WIDTH))
        self.service_label.setToolTip(service)
        self.service_label.setSizePolicy(QSizePolicy.Policy.Expanding,
                                         QSizePolicy.Policy.Preferred)

        # I12: 도커는 start_cmd가 비어 있어도(기본값 폴백) 서버로 취급해야
        # 토글이 사라지지 않는다.
        self.has_service = bool(route.server.start_cmd) or route.server.kind == "docker"
        self.server_switch: ToggleSwitch | None = None
        self.register_btn: QPushButton | None = None
        toggle_label = _toggle_label("서버", palette)
        if self.has_service:
            self.server_switch = ToggleSwitch(palette)
            self.server_switch.toggled.connect(self._on_server_toggled)
        else:
            toggle_label.setText("")  # 등록 버튼 자체가 의미를 나타내므로 라벨은 폭 정렬용 공백
            self.register_btn = QPushButton("서버 등록")
            self.register_btn.clicked.connect(self._edit_route)

        menu_btn = QPushButton()
        menu_btn.setIcon(make_icon("dots", icon_color))
        menu_btn.setFixedWidth(28)  # 터널 메뉴(34px)보다 작게: 하위 위계 표현
        menu_btn.clicked.connect(lambda: self._menu(menu_btn))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setSpacing(6)
        lay.addWidget(self.dot)
        lay.addWidget(self.name_label)
        lay.addWidget(_route_vline(palette))
        lay.addWidget(self.domain_label)
        lay.addWidget(_route_vline(palette))
        lay.addWidget(self.service_label, 1)
        lay.addWidget(toggle_label)
        lay.addWidget(self.server_switch if self.server_switch else self.register_btn)
        lay.addWidget(menu_btn)

        self.update_state()

    def _on_server_toggled(self, checked: bool):
        ctx = self.card.win.ctx
        try:
            if checked:
                ctx.manager.start_service(self.card.tunnel_name, self.route)
            else:
                ctx.manager.stop_service(self.card.tunnel_name, self.route)
        except Exception as ex:
            QMessageBox.critical(self, "서버 오류", str(ex))
        self.card.win.info_banner.hide()
        self.update_state()

    def _menu(self, anchor: QPushButton):
        win = self.card.win
        palette = current_palette(win.ctx.store.settings.theme)
        m = QMenu(self)
        edit_action = QAction("편집", m)
        edit_action.triggered.connect(self._edit_route)
        m.addAction(edit_action)
        m.addSeparator()
        danger_menu_action(m, "삭제", palette, self._delete_route)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _open_site(self, event):
        """도메인 클릭 -> 기본 브라우저로 그 주소를 연다."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self.route.hostname:
            return
        webbrowser.open(f"https://{self.route.hostname}")

    def _edit_route(self):
        self.card.win._edit_route(self.card, self.route)

    def _delete_route(self):
        self.card.win._delete_route(self.card, self.route)

    def update_state(self):
        ctx = self.card.win.ctx
        palette = current_palette(ctx.store.settings.theme)
        pending = False
        running = False
        if self.server_switch is not None:
            pending = ctx.manager.service_pending(self.card.tunnel_name, self.route)
            running = ctx.manager.service_running(self.card.tunnel_name, self.route)
            # 전이 중에는 "가는 방향"에 노브를 고정한다. 실제 상태로 그리면
            # 시작 직후 살아있다고 나왔다가 폴링 결과가 도착하며 꺼진 자리로
            # 되돌아오는 식으로 노브가 왔다 갔다 한다.
            desired = ctx.manager.service_pending_desired(self.card.tunnel_name,
                                                          self.route)
            shown = desired if (pending and desired is not None) else running
            self.server_switch.sync(shown, pending)
            if self.route.server.kind == "docker":
                # I5: 도커 조회 실패 사유를 토글 툴팁으로 보여준다.
                err = ctx.manager.docker_error(self.card.tunnel_name, self.route)
                if err:
                    self.server_switch.setToolTip(err)
            else:
                # D2: PID 재사용 의심(명령 불일치 확정) 사유를 같은 자리에
                # 툴팁으로 보여준다. 서비스는 꺼짐으로 표시하지 않는다 -
                # PID는 여전히 살아있으므로 "확인 불가"일 뿐이다.
                reason = ctx.manager.service_mismatch_reason(self.card.tunnel_name,
                                                              self.route)
                if reason:
                    self.server_switch.setToolTip(reason)

        # 라우트 동그라미: 실행 중이면 RUNNING, 전이 중이면 STARTING, 그 외/
        # 서비스 미등록이면 STOPPED(=회색). 미등록 라우트는 팔레트 muted로
        # "해당 없음"을 표현한다(STATE_COLORS[STOPPED]는 회색이지만 muted가
        # 테마별 무채색 뉘앙스를 더 잘 맞춘다).
        if not self.has_service:
            dot_color = palette["muted"]
        elif pending:
            dot_color = STATE_COLORS[TunnelState.STARTING]
        elif running:
            dot_color = STATE_COLORS[TunnelState.RUNNING]
        else:
            dot_color = STATE_COLORS[TunnelState.STOPPED]
        # 성능: setStyleSheet는 스타일 재계산·리페인트를 부른다. 1초마다 같은
        # 값을 다시 넣으면 그만큼 헛일이라, 바뀔 때만 적용한다.
        if dot_color != self._dot_color:
            self._dot_color = dot_color
            self.dot.setStyleSheet(
                f"background: {dot_color}; border-radius: {ROUTE_DOT_SIZE // 2}px;")


class OwnerGroupHeader(QWidget):
    """터널 목록을 기기별로 나누는 카테고리 머리글 (좌측 정렬, 접기/펴기).

    아래에 붙는 카드들이 "이 기기에서 만든 터널"임을 한 줄로 알려준다.
    머리글을 누르면 그 그룹의 카드를 접거나 펼 수 있다 - 지금 보고 있는 대상이
    아닌 그룹은 어차피 실행할 수 없으므로 기본값으로 접혀 있다.
    """

    def __init__(self, owner_key: str, palette: dict, is_current: bool,
                 count: int, collapsed: bool, on_toggle=None):
        super().__init__()
        self.owner_key = owner_key
        self.label_text = owner_group_label(owner_key)
        self.collapsed = collapsed
        self._on_toggle = on_toggle
        self._palette = palette
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.chevron = QLabel()
        self.chevron.setFixedWidth(12)

        icon_name = "monitor" if owner_key == "local" else "server"
        icon_label = QLabel()
        icon_label.setPixmap(make_icon(icon_name, palette["muted"]).pixmap(14, 14))

        self.name_label = QLabel(self.label_text)
        self.name_label.setStyleSheet(
            f"color: {palette['muted']}; font-size: 12px; font-weight: 600;")

        self.count_label = QLabel(f"{count}개")
        self.count_label.setStyleSheet(
            f"color: {palette['muted']}; font-size: 11px;")

        # 지금 보고 있는 대상이 어느 그룹인지 표시해, 실행 가능한 터널이 어디
        # 모여 있는지 한눈에 알 수 있게 한다.
        self.current_label = QLabel("현재 대상")
        self.current_label.setStyleSheet(
            f"color: {palette['accent']}; font-size: 11px; font-weight: 600;")
        self.current_label.setVisible(is_current)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {palette['border']}; border: none;")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 8, 2, 2)
        lay.setSpacing(6)
        lay.addWidget(self.chevron)
        lay.addWidget(icon_label)
        lay.addWidget(self.name_label)
        lay.addWidget(self.count_label)
        lay.addWidget(self.current_label)
        lay.addWidget(line, 1)

        self._update_chevron()

    def _update_chevron(self):
        # 접힘 상태를 삼각형 방향으로 표현한다(아이콘 리소스를 늘리지 않으려고
        # 문자를 쓴다 - 이모지가 아니라 기하 도형이라 폰트 의존도 낮다).
        self.chevron.setText("▶" if self.collapsed else "▼")
        self.chevron.setStyleSheet(
            f"color: {self._palette['muted']}; font-size: 9px;")
        self.setToolTip("눌러서 펼치기" if self.collapsed else "눌러서 접기")

    def set_collapsed(self, collapsed: bool):
        self.collapsed = collapsed
        self._update_chevron()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._on_toggle:
            self._on_toggle(self.owner_key)
        super().mouseReleaseEvent(event)


class TunnelCard(QFrame):
    def __init__(self, win: "MainWindow", info, meta: TunnelMeta,
                has_credentials: bool = True, owner_key: str = ""):
        super().__init__()
        self.setObjectName("card")
        self.win = win
        self.info = info
        self.meta = meta
        self.tunnel_name = info.name
        # O3: 자격증명 존재 여부는 refresh() 시점에 한 번만 원격 확인해 카드에
        # 들고 있는다 - 1초 폴링(_tick)에서 매번 stat을 날리지 않기 위해서다.
        self.has_credentials = has_credentials

        palette = current_palette(win.ctx.store.settings.theme)
        icon_color = palette["text"]

        self.dot = QLabel()
        self.dot.setFixedSize(12, 12)
        self._last_state = None  # 상태가 바뀔 때만 색/문구를 다시 넣는다
        title = QLabel(info.name)
        title.setObjectName("cardTitle")
        self.state_label = QLabel()
        self.state_label.setObjectName("cardSub")

        # O1/C1: 이 터널을 만든(또는 옮겨온) 대상. 카드에 배지로 붙이지 않고
        # refresh()가 이 값으로 카드들을 기기별 카테고리로 묶는다 - 같은 문구가
        # 카드마다 반복되는 대신 머리글 한 줄로 모인다.
        self.owner_key = owner_key

        # O2: 현재 대상에 자격증명이 없으면 실행할 수 없다는 표시 + 토글 비활성화.
        self.cannot_run_label = QLabel()
        self.cannot_run_label.setStyleSheet(f"color: {palette['danger']}; font-size: 11px;")
        if not has_credentials:
            self.cannot_run_label.setText("이 대상에서 실행 불가")
            self.cannot_run_label.setToolTip(NO_CREDENTIALS_TOOLTIP)
        else:
            self.cannot_run_label.setVisible(False)

        # 라우트가 하나도 없는 터널은 켜도 404만 응답한다(ingress에 폴백만 있음).
        # "터널을 켰는데 왜 안 되지"를 미리 막기 위해 카드에 밝혀 둔다.
        self.no_route_label = QLabel()
        self.no_route_label.setStyleSheet(
            f"color: {palette['muted']}; font-size: 11px;")
        if not meta.routes:
            self.no_route_label.setText("라우트 없음")
            self.no_route_label.setToolTip(
                "연결된 도메인이 없어 켜도 404만 응답합니다. "
                "아래 '라우트 추가'로 도메인을 붙이세요.")
        else:
            self.no_route_label.setVisible(False)

        self.tunnel_switch = ToggleSwitch(palette)
        self.tunnel_switch.toggled.connect(self._on_tunnel_toggled)
        log_btn = QPushButton("로그")
        # I1: 로그 버튼은 아이콘 없이 텍스트만 (사용자가 "이모지"라 부르는 그림 아이콘 제거)
        log_btn.setToolTip("터널과 모든 라우트의 로그를 탭으로 봅니다")
        log_btn.clicked.connect(lambda: win._open_log_tunnel(self))
        delete_btn = QPushButton("삭제")
        delete_btn.setObjectName("danger")
        delete_btn.setToolTip("터널 삭제")
        delete_btn.clicked.connect(lambda: win._delete_tunnel(self))

        header = QHBoxLayout()
        header.addWidget(self.dot)
        header.addWidget(title)
        header.addWidget(self.state_label)
        header.addWidget(self.no_route_label)
        header.addWidget(self.cannot_run_label)
        header.addStretch(1)
        header.addWidget(_toggle_label("터널", palette))
        header.addWidget(self.tunnel_switch)
        header.addWidget(log_btn)
        header.addWidget(delete_btn)

        # I3: 터널(상위)과 라우트(하위) 목록을 시각적으로 구분하는 얇은 구분선.
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet(f"background: {palette['border']}; max-height: 1px;")
        separator.setFixedHeight(1)

        self.route_rows: list[RouteRow] = []
        routes_lay = QVBoxLayout()
        routes_lay.setContentsMargins(24, 2, 0, 0)
        routes_lay.setSpacing(0)
        for route in meta.routes:
            row = RouteRow(self, route)
            self.route_rows.append(row)
            routes_lay.addWidget(row)

        add_route_btn = QPushButton("라우트 추가")
        add_route_btn.setIcon(make_icon("plus", icon_color))
        add_route_btn.clicked.connect(lambda: win._add_route(self))
        add_row = QHBoxLayout()
        add_row.setContentsMargins(24, 2, 0, 0)
        add_row.addWidget(add_route_btn)
        add_row.addStretch(1)

        lay = QVBoxLayout(self)
        lay.addLayout(header)
        lay.addWidget(separator)
        lay.addLayout(routes_lay)
        lay.addLayout(add_row)

        self.update_state()

    # ---- 동작 ----
    def _on_tunnel_toggled(self, checked: bool):
        ctx = self.win.ctx
        try:
            if checked:
                if not ctx.runner.file_exists(ctx.client.config_path(self.tunnel_name)):
                    QMessageBox.warning(self, "설정 없음",
                                        f"config-{self.tunnel_name}.yml 이 없습니다.\n"
                                        "라우트를 먼저 추가하세요.")
                    self.tunnel_switch.sync(False)
                    return
                ctx.manager.start_tunnel(self.tunnel_name, ctx.client)
                for route in self.meta.routes:
                    if route.server.autostart and route.server.start_cmd \
                            and not ctx.manager.service_running(self.tunnel_name, route):
                        ctx.manager.start_service(self.tunnel_name, route)
            else:
                ctx.manager.stop_tunnel(self.tunnel_name)
        except Exception as ex:
            QMessageBox.critical(self, "오류", str(ex))
        self.win.info_banner.hide()
        self.update_state()

    # ---- 표시 갱신 ----
    def update_state(self):
        ctx = self.win.ctx
        st = ctx.manager.tunnel_state(self.tunnel_name)
        if st != self._last_state:
            self._last_state = st
            self.dot.setStyleSheet(
                f"background: {STATE_COLORS[st]}; border-radius: 6px;")
            self.state_label.setText(STATE_LABELS[st])
        pending = st == TunnelState.STARTING or ctx.manager.tunnel_pending(self.tunnel_name)
        running = st in (TunnelState.STARTING, TunnelState.RUNNING)
        mismatch_reason = None
        if st == TunnelState.ERROR:
            mismatch_reason = ctx.manager.tunnel_mismatch_reason(self.tunnel_name)
            if mismatch_reason:
                # D2: PID는 여전히 살아있으므로(재사용 의심일 뿐) 토글을
                # ON으로 보여준다 - 그래야 사용자가 "끄기"를 눌러
                # stop_tunnel(kill_pid 없이 PID 파일만 정리)을 호출할 수
                # 있다. 죽은 PID로 인한 ERROR(사유 없음)는 그대로 OFF.
                running = True
        # 라우트 행과 같은 규칙: 전이 중에는 가는 방향에 노브를 고정한다.
        desired = ctx.manager.tunnel_pending_desired(self.tunnel_name)
        shown = desired if (pending and desired is not None) else running
        self.tunnel_switch.sync(shown, pending)
        if mismatch_reason:
            # 도커 오류 툴팁과 같은 자리를 재사용한다: update_tooltip()이
            # 기본 켜기/끄기 문구로 덮어쓴 뒤 사유로 다시 덮어쓴다.
            self.tunnel_switch.setToolTip(mismatch_reason)
        # O2: 자격증명이 없으면 토글 자체를 막는다 - 켜봐야 이 대상에서는
        # 실행될 수 없다. has_credentials는 refresh()에서 캐시된 값이라 이
        # 폴링 루프에서 추가 원격 호출이 일어나지 않는다.
        if not self.has_credentials:
            self.tunnel_switch.setEnabled(False)
            self.tunnel_switch.setToolTip(NO_CREDENTIALS_TOOLTIP)
        else:
            self.tunnel_switch.setEnabled(True)
        for row in self.route_rows:
            row.update_state()


class MainWindow(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.cards: list[TunnelCard] = []
        self.group_headers: list[OwnerGroupHeader] = []
        # 카테고리 접힘: 사용자가 직접 누른 그룹만 여기 기록하고, 나머지는
        # "현재 대상만 펼침" 규칙을 따른다(대상을 바꾸면 자동으로 다시 접힌다).
        self._collapse_overrides: dict[str, bool] = {}
        # 마지막 tunnel list 결과와 마지막으로 그린 내용의 지문(성능).
        self._tunnel_cache = None
        self._render_sig: tuple | None = None
        self._log_viewers: dict[str, "LogViewer"] = {}
        # B3: 인덱스가 아니라 현재 대상의 식별자로 콤보를 재선택한다. None이면
        # 로컬, 문자열이면 SSH 프로필 이름. 인덱스만 쓰면 프로필이 삭제됐을 때
        # 콤보 항목이 밀려서 엉뚱한 대상을 가리키게 된다.
        self._current_target_key: str | None = None
        self.setWindowTitle("Cloudflare Tunnel GUI")
        self.resize(820, 620)

        # 상단 바
        self.target_combo = QComboBox()
        palette = current_palette(ctx.store.settings.theme)
        icon_color = palette["text"]
        # 대상 콤보가 창 폭을 다 먹으면 오른쪽 버튼들이 끝으로 밀려 서로
        # 멀어진다. 이름+호스트가 들어갈 만큼만 주고 나머지는 여백으로 둔다.
        self.target_combo.setMinimumWidth(220)
        self.target_combo.setMaximumWidth(340)
        self.refresh_btn = QPushButton()
        self.refresh_btn.setIcon(make_icon("refresh", icon_color))
        self.refresh_btn.setFixedSize(TOP_BTN_HEIGHT, TOP_BTN_HEIGHT)
        self.refresh_btn.setToolTip("새로고침 (F5)")
        self.add_btn = QPushButton("터널 생성")
        self.add_btn.setIcon(make_icon("plus", palette["on_accent"]))
        self.add_btn.setObjectName("primary")
        self.repos_btn = QPushButton("프로젝트")
        # 프로젝트 버튼도 설정 버튼과 마찬가지로 아이콘 없이 텍스트만 쓴다
        self.settings_btn = QPushButton("설정")
        # I1: 설정 버튼은 아이콘 없이 텍스트만
        self.help_btn = QPushButton("?")
        self.help_btn.setFixedSize(TOP_BTN_HEIGHT, TOP_BTN_HEIGHT)
        self.help_btn.setToolTip("사용 흐름 안내")
        # 상단 바 버튼 높이를 하나로 맞춘다(아이콘 버튼과 글자 버튼이 섞여
        # 있으면 기본 높이가 달라 위아래로 들쭉날쭉해 보인다).
        for btn in (self.add_btn, self.repos_btn, self.settings_btn):
            btn.setFixedHeight(TOP_BTN_HEIGHT)
        self.target_combo.setFixedHeight(TOP_BTN_HEIGHT)
        # clicked 시그널은 checked(bool)를 넘긴다. self.refresh를 그대로 연결하면
        # 그 False가 fetch 인자로 들어가 "새로고침을 눌러도 다시 조회하지 않는"
        # 정반대 동작이 된다. 인자를 끊어서 연결한다.
        self.refresh_btn.clicked.connect(lambda: self.refresh())
        self.add_btn.clicked.connect(self._create_tunnel)
        self.repos_btn.clicked.connect(self._open_repos)
        self.settings_btn.clicked.connect(self._open_settings)
        self.help_btn.clicked.connect(self._open_guide)
        self.target_combo.currentIndexChanged.connect(self._switch_target)

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(QLabel("대상:"))
        top.addWidget(self.target_combo)
        top.addWidget(self.refresh_btn)
        top.addStretch(1)  # 대상 선택과 동작 버튼을 시각적으로 갈라 놓는다
        top.addWidget(self.add_btn)
        top.addWidget(self.repos_btn)
        top.addWidget(self.settings_btn)
        top.addWidget(self.help_btn)

        # 카드 목록
        self.list_lay = QVBoxLayout()
        # 목록이 비어 있는 동안(불러오는 중 / 터널 0개) 빈 화면만 덩그러니
        # 보이지 않도록 자리를 채우는 안내. 두 상태가 동시에 나올 수 없으므로
        # 라벨 하나로 문구만 바꿔 쓴다 - 숨긴 라벨을 여러 개 두면 레이아웃이
        # 그 자리를 계속 잡고 있어 위쪽에 빈 공간이 남는다.
        self.list_status = QLabel(LOADING_TEXT)
        self.list_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list_status.setStyleSheet(
            f"color: {palette['muted']}; font-size: 13px; padding: 40px 0;")
        self.list_lay.addWidget(self.list_status)
        self.list_lay.addStretch(1)
        inner = QWidget()
        inner.setLayout(self.list_lay)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)

        self.banner = QLabel()
        self._style_banner()
        self.banner.hide()

        self.info_banner = QLabel()
        self._style_info_banner()
        self.info_banner.hide()

        # I7: 좌측 하단 서명. 스크롤 영역 밖(카드 목록과 별개)에 두어 스크롤과
        # 무관하게 항상 보이게 하고, 세로 공간을 거의 차지하지 않게 작게 둔다.
        self.signature_label = QLabel("by_ ssenu")
        self._style_signature()

        root = QVBoxLayout(self)
        root.addLayout(top)
        root.addWidget(self.banner)
        root.addWidget(self.info_banner)
        root.addWidget(scroll, 1)
        root.addWidget(self.signature_label)

        # C1: 폴링 실패 연속 횟수와 백오프 활성 여부. _tick()에서 관리한다.
        # refresh()가 배너를 함부로 덮어쓰지 않으려면(연결 오류 배너 보호)
        # 첫 refresh() 호출보다 먼저 초기화되어 있어야 한다.
        self._poll_failures = 0
        self._poll_backoff_active = False

        self._reload_targets()
        self._update_repos_btn()
        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

        # 첫 조회(cloudflared tunnel list)는 네트워크 왕복이라 수백 ms~수 초가
        # 걸린다. 생성자 안에서 부르면 창이 그려지기도 전에 그 시간만큼 멈춰
        # "켤 때 버벅인다"가 된다. 이벤트 루프에 넘겨 창을 먼저 띄운다.
        QTimer.singleShot(0, self.refresh)

        self._poller = BackgroundPoller(self)
        self._poller.finished.connect(self._on_poll_done)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(POLL_INTERVAL_NORMAL_MS)

        # 단축키 등록
        from PyQt6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence("F5"), self).activated.connect(self.refresh)
        for i in range(1, 10):
            sc = QShortcut(QKeySequence(f"Ctrl+{i}"), self)
            sc.activated.connect(lambda n=i - 1: self._shortcut_tunnel(n))
            sc2 = QShortcut(QKeySequence(f"Ctrl+Shift+{i}"), self)
            sc2.activated.connect(lambda n=i - 1: self._shortcut_server(n))

    def _style_banner(self):
        p = current_palette(self.ctx.store.settings.theme)
        self.banner.setStyleSheet(
            f"background:{p['danger_bg']};color:{p['danger']};"
            "padding:6px;border-radius:6px;")

    def _style_info_banner(self):
        p = current_palette(self.ctx.store.settings.theme)
        self.info_banner.setStyleSheet(
            f"background:{p['panel2']};color:{p['accent2']};"
            f"padding:6px;border-radius:6px;border:1px solid {p['border']};")

    def _style_signature(self):
        p = current_palette(self.ctx.store.settings.theme)
        self.signature_label.setStyleSheet(f"color: {p['muted']}; font-size: 11px;")

    # ---- 실행 여부 확인 ----
    def _any_running(self) -> bool:
        if not self.cards:
            return False
        self.ctx.manager.refresh([c.meta for c in self.cards])
        for c in self.cards:
            if self.ctx.manager.tunnel_state(c.tunnel_name) in (
                    TunnelState.RUNNING, TunnelState.STARTING):
                return True
            for r in c.meta.routes:
                if self.ctx.manager.service_running(c.tunnel_name, r):
                    return True
        return False

    # ---- 대상 전환 ----
    def _index_for_current_target(self) -> int | None:
        """현재 대상 식별자에 해당하는 콤보 인덱스. 없으면 None (예: 프로필 삭제됨)."""
        if self._current_target_key is None:
            return 0
        for i in range(self.target_combo.count()):
            data = self.target_combo.itemData(i)
            if isinstance(data, SshProfile) and data.name == self._current_target_key:
                return i
        return None

    def _reload_targets(self):
        icon_color = current_palette(self.ctx.store.settings.theme)["text"]
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        self.target_combo.addItem(make_icon("monitor", icon_color), "이 PC", None)
        for p in self.ctx.store.settings.ssh_profiles:
            self.target_combo.addItem(
                make_icon("server", icon_color), f"{p.name} ({p.host})", p)
        self.target_combo.addItem(
            make_icon("plus", icon_color), "SSH 대상 추가...", ADD_SSH_TARGET)

        idx = self._index_for_current_target()
        if idx is None:
            # B3: 사용 중이던 SSH 프로필이 삭제되어 더 이상 목록에 없다.
            # 콤보가 "SSH 대상 추가..."를 현재 대상처럼 보여주는 것을 막기
            # 위해 명시적으로 로컬로 되돌린다.
            self.ctx.set_local()
            self._current_target_key = None
            idx = 0
        self.target_combo.setCurrentIndex(idx)
        self.target_combo.blockSignals(False)

    def _switch_target(self):
        if self.target_combo.currentData() == ADD_SSH_TARGET:
            self.target_combo.blockSignals(True)
            self.target_combo.setCurrentIndex(self._index_for_current_target() or 0)
            self.target_combo.blockSignals(False)
            from app.ui.ssh_manager import SshManagerDialog
            dlg = SshManagerDialog(self.ctx, self)

            def _on_finished(_result):
                self._reload_targets()
                # B3/I: 프로필 삭제로 _reload_targets()가 로컬로 폴백했을 수
                # 있다. 러너만 로컬로 바뀌고 카드 목록은 이전 대상 것이 남는
                # 것을 막기 위해 항상 refresh()한다(대상이 안 바뀌어도 비용은
                # 미미함).
                self.refresh()

            dlg.finished.connect(_on_finished)
            self._open_modal(dlg)
            return

        if self._any_running():
            ok = QMessageBox.question(
                self, "대상 전환",
                "현재 대상에서 실행 중인 터널/서버가 있습니다.\n"
                "전환해도 계속 실행되며, 전환하는 동안 화면에서만 보이지 않게 됩니다. "
                "계속할까요?")
            if ok != QMessageBox.StandardButton.Yes:
                self.target_combo.blockSignals(True)
                self.target_combo.setCurrentIndex(self._index_for_current_target() or 0)
                self.target_combo.blockSignals(False)
                return

        profile = self.target_combo.currentData()
        # 대상을 바꾸면 연결을 닫는다. 워커가 그 연결로 조회하는 중이면
        # 파일 핸들이 발밑에서 사라지므로, 먼저 끝나기를 기다린다.
        self._poller.stop()
        self.banner.hide()
        # D3: 대상을 바꾸면 이전 대상 폴링 실패로 켜졌던 백오프 상태가 새
        # 대상에도 그대로 남아(5초 주기 + 배너 억제) 새 대상 상태가 굼뜨게
        # 보일 수 있다. 대상 전환은 곧 "다시 시도"이므로 정상 주기로
        # 리셋한다.
        self._poll_backoff_active = False
        self._poll_failures = 0
        self._timer.setInterval(POLL_INTERVAL_NORMAL_MS)
        try:
            if profile is None:
                self.ctx.set_local()
                self._current_target_key = None
            else:
                self.setCursor(Qt.CursorShape.WaitCursor)
                self.ctx.set_remote(profile)
                self._current_target_key = profile.name
        except Exception as ex:
            self.banner.setText(f"SSH 연결 실패: {ex}")
            self.banner.show()
            self.target_combo.blockSignals(True)
            self.target_combo.setCurrentIndex(0)
            self.target_combo.blockSignals(False)
            self.ctx.set_local()
            self._current_target_key = None
        finally:
            self.unsetCursor()
        # B2: 로그 뷰어는 열린 시점의 대상(runner)에 붙어 있어, 대상이 바뀌면
        # 죽은 세션을 보거나 다른 대상의 로그를 잘못 보여줄 수 있다. 가장
        # 단순하고 오해가 없는 방법으로 전부 닫는다.
        self._close_all_log_viewers()
        self._reset_group_collapse()
        self.refresh()
        self._maybe_check_prereqs()

    def _maybe_check_prereqs(self):
        """SSH 대상으로 바뀌었으면 준비물(cloudflared/git/docker)을 점검한다.

        "다음부터 보지 않기"를 체크해 둔 기기는 건너뛴다. 그 체크는 셋 다
        정상일 때만 가능하므로, 새 기기는 최초 1회 반드시 이 화면을 본다.
        """
        if not self.ctx.is_remote:
            return
        if self.ctx.store.settings.skip_prereq_check(self.ctx.runner.name):
            return
        from app.ui.prereq_dialog import PrereqDialog
        dlg = PrereqDialog(self.ctx, self._target_display_name(), self)
        self._open_modal(dlg)

    def _close_all_log_viewers(self):
        for viewer in list(self._log_viewers.values()):
            viewer.close()

    def _target_display_name(self) -> str:
        if not self.ctx.is_remote:
            return "이 PC"
        return self.ctx.runner.name.split(":", 1)[-1]

    # ---- 카테고리 접기/펴기 ----
    def _default_collapsed(self, owner_key: str) -> bool:
        """지금 보고 있는 대상이 만든 그룹만 펼친 상태로 둔다.

        다른 기기의 터널은 이 대상에서 실행할 수 없으므로(자격증명이 없다)
        기본값으로 접어 화면을 비운다. 사용자가 직접 편 그룹은
        _collapse_overrides가 기억한다.
        """
        return owner_key != self.ctx.runner.name

    def _is_collapsed(self, owner_key: str) -> bool:
        if owner_key in self._collapse_overrides:
            return self._collapse_overrides[owner_key]
        return self._default_collapsed(owner_key)

    def _reset_group_collapse(self):
        """대상이 바뀌면 손으로 편 기록을 지운다.

        새 대상 기준으로 다시 접히게 하기 위해서다 - Rpi로 옮기면 '이 PC'
        그룹은 다시 접혀야 화면이 지금 할 수 있는 일만 보여준다.
        """
        self._collapse_overrides.clear()

    def _toggle_group(self, owner_key: str):
        self._collapse_overrides[owner_key] = not self._is_collapsed(owner_key)
        self._apply_collapsed()

    def _apply_collapsed(self):
        for header in self.group_headers:
            collapsed = self._is_collapsed(header.owner_key)
            header.set_collapsed(collapsed)
            for card in self.cards:
                if card.owner_key == header.owner_key:
                    card.setVisible(not collapsed)

    # ---- 목록 ----
    def _credential_ids(self) -> set[str]:
        """이 대상에 자격증명 파일이 있는 터널 id 집합.

        예전에는 터널마다 file_exists()를 한 번씩 불렀는데, SSH 대상에서는
        터널 수만큼 왕복이 생겨 새로고침이 눈에 띄게 굼떴다. 디렉터리를 한 번
        읽어 이름만 비교한다(왕복 1회).
        """
        try:
            names = self.ctx.runner.list_dir(self.ctx.client.config_dir())
        except Exception:
            return set()
        return {n[:-5] for n in names if n.endswith(".json")}

    def refresh(self, fetch: bool = True):
        """카드 목록을 다시 그린다.

        fetch=False면 계정 터널 목록을 다시 받아오지 않고 마지막 결과를 쓴다.
        라우트 편집·설정 변경처럼 '계정의 터널 구성이 바뀔 수 없는' 경로에서
        네트워크 왕복(수백 ms~수 초)을 없애기 위한 것이다.
        """
        if not fetch and self._tunnel_cache is not None:
            self._render_cards(self._tunnel_cache)
            return
        try:
            self.setCursor(Qt.CursorShape.WaitCursor)
            infos = self.ctx.client.list_tunnels()
            # _tick()의 연결 오류 배너와 위젯을 공유한다. _tick()이 이미
            # 폴링 백오프 상태(연결 끊김으로 판단)라면 이 refresh()의 성공이
            # 그 배너를 조용히 지워버리지 않도록 둔다 - 다음 _tick()이
            # 스스로 판단해서 켜고 끈다.
            if not self._poll_backoff_active:
                self.banner.hide()
        except Exception as ex:
            self.banner.setText(f"터널 목록 조회 실패: {ex}")
            self.banner.show()
            infos = []
        finally:
            self.unsetCursor()

        self._tunnel_cache = infos
        self._render_cards(infos)

    def _render_cards(self, infos):
        self._update_repos_btn()

        # 대상(로컬/SSH 프로필)마다 독립된 터널 설정을 쓴다. 계정 단위인
        # tunnel list는 대상 간에 같아도, config-*.yml/서버 실행 명령은
        # 대상마다 다르기 때문이다.
        metas = self.ctx.store.settings.tunnels_for(self.ctx.runner.name)
        owners = self.ctx.store.settings.tunnel_owners
        restored = False
        owner_backfilled = False
        # O3/성능: 자격증명 확인은 카드를 그릴 때 한 번만 한다(1초 폴링에서는
        # 하지 않는다). 게다가 터널마다 stat을 날리지 않고 디렉터리 한 번 읽기로
        # 끝낸다 - SSH 대상에서 왕복 수가 터널 수에 비례하던 것을 1로 줄인다.
        cred_ids = self._credential_ids()
        rows: list[tuple[str, object, TunnelMeta, bool]] = []
        for info in infos:
            meta = metas.get(info.name)
            if meta is None:
                meta = self._meta_from_config(info.name)
                metas[info.name] = meta
                restored = True
            has_creds = info.id in cred_ids
            # C1/C2: owner는 계정 단위(터널 UUID 키)로만 기록한다 - 대상별
            # meta에 두면 대상을 바꾸는 순간 다른 dict를 보게 돼 기능이
            # 무력화된다. backfill도 "이 id로 기록된 owner가 아직 없을 때만"
            # 채운다 - 이미 있으면 절대 덮어쓰지 않는다(자격증명을 여러
            # 대상에 복사해도 소유권을 가로채지 못하게).
            if info.id not in owners and has_creds:
                owners[info.id] = self.ctx.runner.name
                owner_backfilled = True
            rows.append((owners.get(info.id, ""), info, meta, has_creds))

        if restored or owner_backfilled:
            self.ctx.store.save()

        # 성능: 화면에 그릴 내용이 지난번과 같으면 위젯을 부수고 다시 만들지
        # 않는다. 다이얼로그를 닫을 때마다 카드 수십 개를 재생성하던 것이
        # "버튼 누르면 잠깐 멈춤"의 원인이었다.
        signature = self._render_signature(rows)
        if signature == self._render_sig and self.cards:
            for card in self.cards:
                card.update_state()
            self._apply_collapsed()
            return
        self._render_sig = signature

        for c in self.cards:
            c.setParent(None)
        self.cards.clear()
        for h in self.group_headers:
            h.setParent(None)
        self.group_headers.clear()

        # 기기(카테고리)별로 묶어 머리글 아래에 카드를 배치한다. cards는
        # 화면에 보이는 순서와 같게 유지한다 - Ctrl+1~9 단축키가 이 순서를 쓴다.
        palette = current_palette(self.ctx.store.settings.theme)
        current_key = self.ctx.runner.name
        pairs = [(owner_key, (info, meta, has_creds))
                 for owner_key, info, meta, has_creds in rows]
        for owner_key, group in group_by_owner(pairs, self._owner_order()):
            header = OwnerGroupHeader(
                owner_key, palette, is_current=owner_key == current_key,
                count=len(group), collapsed=self._is_collapsed(owner_key),
                on_toggle=self._toggle_group)
            self.list_lay.insertWidget(self.list_lay.count() - 1, header)
            self.group_headers.append(header)
            for info, meta, has_creds in group:
                card = TunnelCard(self, info, meta, has_creds, owner_key)
                self.list_lay.insertWidget(self.list_lay.count() - 1, card)
                self.cards.append(card)

        self._set_list_status(EMPTY_TEXT if not self.cards else "")
        self._apply_collapsed()

    def _set_list_status(self, text: str):
        """목록 안내 문구(로딩/비어 있음). 빈 문자열이면 숨긴다."""
        self.list_status.setVisible(bool(text))
        if text:
            self.list_status.setText(text)
        # 숨긴 뒤에도 레이아웃이 옛 자리를 잡고 있지 않도록 즉시 다시 계산한다.
        self.list_lay.activate()

    @staticmethod
    def _render_signature(rows) -> tuple:
        """카드를 다시 만들어야 하는지 판단하는 값.

        카드가 보여주는 것(이름/소유자/자격증명 여부/라우트 구성)만 담는다.
        실행 상태처럼 매 틱 바뀌는 값은 update_state()가 따로 반영하므로
        여기 넣으면 안 된다 - 넣으면 1초마다 전체를 다시 그리게 된다.
        """
        return tuple(
            (owner_key, info.name, has_creds,
             tuple((r.id, r.label, r.hostname, r.service, r.server.kind,
                    r.server.start_cmd, r.server.stop_cmd, r.server.cwd,
                    r.server.autostart) for r in meta.routes))
            for owner_key, info, meta, has_creds in rows)

    def _owner_order(self) -> list[str]:
        """카테고리 표시 순서: 이 PC → 설정에 등록된 SSH 프로필 순."""
        return ["local"] + [f"ssh:{p.name}"
                            for p in self.ctx.store.settings.ssh_profiles]

    def _update_repos_btn(self):
        """프로젝트(Git 클론)는 SSH 대상에서만 쓴다.

        내 PC에서 개발하는 프로젝트는 이미 로컬에 있으므로 앱이 다시 클론할
        이유가 없고, 클론 위치(repo_root, 기본 /srv/apps)도 리눅스 경로라
        윈도우에서는 맞지 않는다. 그래서 로컬 대상일 땐 버튼을 비활성화한다.
        """
        remote = self.ctx.is_remote
        self.repos_btn.setEnabled(remote)
        self.repos_btn.setToolTip(
            "" if remote else
            "프로젝트 클론은 SSH로 연결한 원격 대상에서만 사용합니다. "
            "상단 '대상'에서 원격 기기를 선택하세요.")

    def _meta_from_config(self, name: str) -> TunnelMeta:
        """settings.json에 없는 터널: config yml에서 라우트 목록을 복원."""
        meta = TunnelMeta(name=name)
        try:
            path = self.ctx.client.config_path(name)
            if self.ctx.runner.file_exists(path):
                cfg = parse_config(self.ctx.runner.read_file(path))
                for hostname, service in get_routes(cfg):
                    meta.routes.append(
                        RouteMeta(id=new_route_id(), hostname=hostname, service=service))
        except Exception:
            pass
        return meta

    # ---- 모달 오버레이(별도 창 대신 메인 창 안에 겹쳐 띄우기) ----
    def _open_modal(self, dialog):
        """QDialog를 별도 OS 창 대신 메인 창 위 오버레이로 임베드한다.

        exec()는 쓰지 않는다 - ModalOverlay가 뒤쪽 클릭을 흡수해 이미
        모달 역할을 한다. 호출자는 dialog.finished에 연결해 결과(accept/
        reject)를 받는다.
        """
        dialog.setWindowFlags(Qt.WindowType.Widget)
        overlay = ModalOverlay(self, dialog,
                               current_palette(self.ctx.store.settings.theme))
        dialog.finished.connect(overlay.cleanup)
        dialog.show()
        overlay.refit()  # QDialog가 show()에서 스스로 잡은 크기를 바로잡는다

    # ---- 생성/삭제 ----
    def _create_tunnel(self):
        wiz = TunnelWizard(self.ctx, [c.tunnel_name for c in self.cards], self)

        def _on_finished(result):
            if result == wiz.DialogCode.Accepted and wiz.created_meta:
                meta = wiz.created_meta
                if isinstance(meta, TunnelMeta):
                    self.ctx.store.settings.tunnels_for(self.ctx.runner.name)[meta.name] = meta
                    # C1: owner는 계정 단위(터널 UUID 키)로 기록한다. 새로
                    # 만든 터널이라 항상 새 id이므로 여기서는 무조건 쓴다
                    # (backfill의 "이미 있으면 덮지 않기"는 refresh() 쪽 로직).
                    tid = wiz.created_tunnel_id
                    if tid:
                        self.ctx.store.settings.tunnel_owners[tid] = self.ctx.runner.name
                    self.ctx.store.save()
                self.refresh()

        wiz.finished.connect(_on_finished)
        self._open_modal(wiz)

    def _delete_tunnel(self, card: TunnelCard):
        name = card.tunnel_name
        hostnames = [r.hostname for r in card.meta.routes if r.hostname]
        from app.ui.confirm_dialogs import TunnelDeleteDialog
        dlg = TunnelDeleteDialog(self.ctx, name, hostnames, self)

        def _on_finished(result):
            if result == dlg.DialogCode.Accepted:
                # 오버레이 정리(같은 finished에 연결됨)가 먼저 끝나도록 한 틱
                # 미룬다. 삭제 중 대기 루프가 먼저 돌면 반투명 배경만 남은
                # 화면이 잠깐 보인다.
                QTimer.singleShot(0, lambda: self._do_delete_tunnel(card))

        dlg.finished.connect(_on_finished)
        self._open_modal(dlg)

    def _do_delete_tunnel(self, card: TunnelCard):
        name = card.tunnel_name
        for route in card.meta.routes:
            try:
                self.ctx.manager.stop_service(name, route)
            except Exception:
                pass
        try:
            self.ctx.manager.stop_tunnel(name)
        except Exception:
            pass
        # cloudflared가 연결을 정리할 시간을 준다 (곧바로 delete하면 "active
        # connection" 오류가 날 수 있다). time.sleep(1.0) 단독 호출은 UI
        # 스레드를 그대로 얼려버리므로, 짧게 나눠 자면서 그 사이 이벤트
        # 루프에 제어를 돌려준다. processEvents()는 그 사이 클릭을 배달할
        # 수 있어(삭제 메뉴 재진입, 삭제 중인 카드 토글 조작 등) 대기하는
        # 동안 창 전체를 비활성화해 재진입을 막는다.
        self.setEnabled(False)
        try:
            for _ in range(5):
                QApplication.processEvents()
                time.sleep(0.2)
        finally:
            self.setEnabled(True)

        deleted = False
        try:
            self.ctx.client.delete_tunnel(name)
            deleted = True
        except CloudflaredError as ex:
            msg = str(ex)
            if "active connection" in msg.lower():
                msg += "\n\n잠시 후 다시 시도해주세요."
            QMessageBox.critical(self, "삭제 실패", msg)
        except Exception as ex:
            QMessageBox.critical(self, "삭제 실패", str(ex))

        if deleted:
            self.ctx.store.settings.tunnels_for(self.ctx.runner.name).pop(name, None)
            self.ctx.store.save()
            try:
                path = self.ctx.client.config_path(name)
                if self.ctx.runner.file_exists(path):
                    self.ctx.runner.remove_file(path)
            except Exception as ex:
                QMessageBox.warning(self, "파일 삭제 실패",
                                    f"터널은 삭제되었지만 config 파일 삭제에 실패했습니다.\n{ex}")
            for route in card.meta.routes:
                self.ctx.manager.cleanup_logs_for_service(name, route)
            self.ctx.manager.cleanup_logs_for_tunnel(name)
        self.refresh()

    # ---- 라우트 추가/편집/삭제 ----
    def _add_route(self, card: TunnelCard):
        from app.ui.route_dialog import RouteDialog
        dlg = RouteDialog(self.ctx, card.meta, None, self)

        def _on_finished(result):
            if result == dlg.DialogCode.Accepted:
                self._notify_restart_needed(card.tunnel_name)
                # 라우트만 바뀌었으므로 계정 터널 목록은 다시 받지 않는다
                self.refresh(fetch=False)

        dlg.finished.connect(_on_finished)
        self._open_modal(dlg)

    def _edit_route(self, card: TunnelCard, route: RouteMeta):
        from app.ui.route_dialog import RouteDialog
        dlg = RouteDialog(self.ctx, card.meta, route, self)

        def _on_finished(result):
            if result == dlg.DialogCode.Accepted:
                self._notify_restart_needed(card.tunnel_name)
                # 라우트만 바뀌었으므로 계정 터널 목록은 다시 받지 않는다
                self.refresh(fetch=False)

        dlg.finished.connect(_on_finished)
        self._open_modal(dlg)

    def _delete_route(self, card: TunnelCard, route: RouteMeta):
        from app.ui.confirm_dialogs import ConfirmDeleteDialog
        dlg = ConfirmDeleteDialog(
            self.ctx, "라우트 삭제",
            f"'{route.hostname or route.id}' 라우트를 삭제할까요?\n"
            "- 서비스가 실행 중이면 중지됩니다",
            [route.hostname] if route.hostname else [], self)

        def _on_finished(result):
            if result == dlg.DialogCode.Accepted:
                self._do_delete_route(card, route)

        dlg.finished.connect(_on_finished)
        self._open_modal(dlg)

    def _do_delete_route(self, card: TunnelCard, route: RouteMeta):
        # I7: config.yml을 먼저 쓰고, 그것이 성공했을 때만 meta.routes와
        # settings를 갱신한다. 순서가 반대면 config 쓰기 실패 시 settings에는
        # 없는데 config/DNS에는 남는 고아 라우트가 생긴다.
        remaining = [r for r in card.meta.routes if r.id != route.id]
        try:
            path = self.ctx.client.config_path(card.tunnel_name)
            if self.ctx.runner.file_exists(path):
                text = self.ctx.runner.read_file(path)
                routes = [(r.hostname, r.service) for r in remaining]
                self.ctx.runner.write_file(path, set_routes(text, routes))
        except Exception as ex:
            QMessageBox.warning(self, "config 갱신 실패", str(ex))
            return  # 아무것도 바꾸지 않는다

        try:
            self.ctx.manager.stop_service(card.tunnel_name, route)
        except Exception:
            pass
        self.ctx.manager.cleanup_logs_for_service(card.tunnel_name, route)
        card.meta.routes = remaining
        self.ctx.store.settings.tunnels_for(self.ctx.runner.name)[card.tunnel_name] = card.meta
        self.ctx.store.save()
        self._notify_restart_needed(card.tunnel_name)
        self.refresh(fetch=False)

    def _notify_restart_needed(self, tunnel_name: str):
        if self.ctx.manager.tunnel_state(tunnel_name) in (
                TunnelState.RUNNING, TunnelState.STARTING):
            self.info_banner.setText("변경을 적용하려면 터널을 껐다 켜야 합니다.")
            self.info_banner.show()

    # ---- 로그 ----
    def _open_log_tunnel(self, card: TunnelCard):
        # I4: 터널당 로그 버튼 하나로 통합 - 첫 탭 "터널", 그 뒤로 라우트마다
        # 한 탭(탭 이름은 hostname, 없으면 "서버 N"). 라우트가 없으면 "터널"
        # 탭만 열어 탭이 불필요하게 많아지지 않게 한다.
        name = card.tunnel_name
        # B2: 캐시 키에 대상(runner.name)을 포함시켜 대상 전환 후 다른 대상의
        # 뷰어가 잘못 재사용되지 않게 한다.
        target = self.ctx.runner.name
        title = f"{name} ({self._target_display_name()})"
        log_paths = {"터널": self.ctx.manager.log_path_for_tunnel(name)}
        for i, route in enumerate(card.meta.routes, start=1):
            tab_name = route.hostname or f"서버 {i}"
            log_paths[tab_name] = self.ctx.manager.log_path_for_service(name, route)
        self._open_log_viewer(f"tunnel:{target}:{name}", title, log_paths)

    def _open_log_viewer(self, key: str, title: str, log_paths: dict[str, str]):
        from app.ui.log_viewer import LogViewer
        if key in self._log_viewers:
            viewer = self._log_viewers[key]
            if viewer.isVisible():
                viewer.raise_()
                viewer.activateWindow()
                return
        viewer = LogViewer(self.ctx, title, log_paths, self)
        self._log_viewers[key] = viewer

        def _cleanup(*_args, k=key, v=viewer):
            # 같은 key로 뷰어를 새로 열었으면 dict에는 이미 새 뷰어가 들어가
            # 있다. 늦게 도착한 옛 뷰어의 destroyed 시그널이 그 새 항목을
            # 지우지 않도록, key뿐 아니라 객체 동일성까지 확인하고 pop한다.
            if self._log_viewers.get(k) is v:
                self._log_viewers.pop(k, None)

        viewer.destroyed.connect(_cleanup)
        viewer.show()

    def _open_settings(self):
        from app.ui.settings import SettingsDialog
        prev_theme = self.ctx.store.settings.theme
        dlg = SettingsDialog(self.ctx, self)

        def _on_finished(_result):
            self._reload_targets()
            if self.ctx.store.settings.theme != prev_theme:
                self._apply_theme()  # 내부에서 refresh()까지 호출한다
            else:
                # B3/I: 프로필 삭제로 로컬 폴백이 일어났을 수 있으니 항상 갱신한다.
                # 설정만 바뀌었으므로 터널 목록은 다시 받지 않는다.
                self.refresh(fetch=False)

        dlg.finished.connect(_on_finished)
        self._open_modal(dlg)

    def _open_repos(self):
        if not self.ctx.is_remote:
            return  # 버튼이 비활성화돼 있지만 다른 경로로 불릴 때를 대비
        from app.ui.repo_manager import RepoManagerDialog
        dlg = RepoManagerDialog(self.ctx, self)
        self._open_modal(dlg)

    def _open_guide(self):
        from app.ui.guide import GuideDialog
        dlg = GuideDialog(self.ctx, self)
        self._open_modal(dlg)

    def _apply_theme(self):
        from PyQt6.QtWidgets import QApplication
        mode = self.ctx.store.settings.theme
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_qss(mode, ensure_qss_icons(mode)))
        apply_titlebar_theme(self, mode == "dark")
        for viewer in list(self._log_viewers.values()):
            apply_titlebar_theme(viewer, mode == "dark")
        self._style_banner()
        self._style_info_banner()
        self._style_signature()
        icon_color = current_palette(mode)["text"]
        self.refresh_btn.setIcon(make_icon("refresh", icon_color))
        self._reload_targets()
        # 표시 내용은 그대로여도 팔레트가 달라졌으므로 지문을 비워 강제로
        # 다시 그리게 한다(그러지 않으면 이전 색의 카드가 남는다).
        self._render_sig = None
        self.refresh(fetch=False)

    def _tick(self):
        """폴링 시작만 한다. 실제 조회는 워커 스레드에서 돌고 _on_poll_done이 받는다.

        예전에는 여기서 곧바로 manager.refresh()를 불렀는데, SSH 대상에서는
        한 번에 100~500ms가 걸려 그동안 창 전체가 멈췄다(도커 조회가 겹치는
        틱이 특히 길었다). 이제 이 슬롯은 즉시 반환한다.
        """
        # 이전 폴링이 아직 안 끝났으면 이번 차례는 건너뛴다. 큐에 쌓으면
        # 연결이 느릴수록 밀린 폴링이 끝없이 이어진다.
        metas = [c.meta for c in self.cards]
        self._poller.run(lambda: self.ctx.manager.refresh(metas))

    def _on_poll_done(self, ok: bool, _result, _error: str):
        # C1: PyQt6는 슬롯의 미처리 예외에서 프로세스를 abort시킨다(실증됨).
        # 워커가 예외를 문자열로 넘겨주므로 여기서는 예외가 나지 않는다.
        if not ok:
            self._poll_failures += 1
            # D3: read_record()가 OSError를 그대로 전파하게 되면서, 로컬의
            # PermissionError(상태 파일 접근 실패) 같은 것도 이 except로
            # 들어온다. 로컬은 "연결"이 아예 없으므로 원인에 맞는 문구로
            # 나눈다 - 원격은 연결 문제, 로컬은 파일 접근 문제.
            if self.ctx.is_remote:
                msg = "대상과의 연결이 끊겼습니다. 잠시 후 다시 시도합니다."
                if self._poll_failures > POLL_FAILURE_HINT_THRESHOLD:
                    msg += " 설정에서 대상을 다시 선택하거나 로컬로 전환하세요."
            else:
                msg = "상태 파일을 읽지 못했습니다. 잠시 후 다시 시도합니다."
            self.banner.setText(msg)
            self.banner.show()
            if not self._poll_backoff_active:
                self._poll_backoff_active = True
                self._timer.setInterval(POLL_INTERVAL_BACKOFF_MS)
            return

        if self._poll_backoff_active:
            self._poll_backoff_active = False
            self._poll_failures = 0
            self.banner.hide()
            self._timer.setInterval(POLL_INTERVAL_NORMAL_MS)

        for c in self.cards:
            c.update_state()

    # ---- 단축키 ----
    def _shortcut_tunnel(self, idx: int):
        # I3: QAbstractButton.toggle()은 isEnabled()를 보지 않으므로, 자격증명이
        # 없어 setEnabled(False)된 토글까지 단축키로 우회해 켜지는 것을 막는다.
        if idx < len(self.cards):
            switch = self.cards[idx].tunnel_switch
            if switch.isEnabled():
                switch.toggle()

    def _shortcut_server(self, idx: int):
        if idx < len(self.cards):
            card = self.cards[idx]
            if card.route_rows and card.route_rows[0].server_switch is not None:
                switch = card.route_rows[0].server_switch
                if switch.isEnabled():
                    switch.toggle()

    # ---- 종료 정리 ----
    def closeEvent(self, event):
        # v2: GUI는 실행 주체가 아니라 관찰자다. 종료 시 아무것도 중지하지
        # 않는다 - 터널/서버는 대상 머신에서 계속 실행되고, 다음 실행 때
        # run 디렉터리를 읽어 상태를 복원한다.
        if self._any_running():
            QMessageBox.information(
                self, "종료",
                "터널과 서버는 계속 실행됩니다.\n다시 실행하면 상태를 이어서 표시합니다.")
        # 워커가 돌고 있는 채로 연결을 닫으면 그 안에서 예외가 난다.
        self._timer.stop()
        self._poller.stop()
        self.ctx.set_local()  # SSH 연결 정리 (원격 프로세스는 그대로 유지됨)
        event.accept()
