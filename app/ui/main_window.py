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
from app.core.store import RouteMeta, SshProfile, TunnelMeta, new_route_id
from app.ui.icons import make_icon
from app.ui.theme import STATE_COLORS, build_qss, current_palette, ensure_qss_icons
from app.ui.widgets import Spinner, ToggleSwitch, danger_menu_action
from app.ui.winutil import apply_titlebar_theme
from app.ui.wizard import TunnelWizard

ADD_SSH_TARGET = "__add__"

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


def _elide(text: str, metrics, width: int) -> str:
    return metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, width)


TOGGLE_LABEL_WIDTH = 30  # 헤더/라우트 행의 토글 라벨 폭을 맞춰 정렬을 유지한다


def _toggle_label(text: str, palette: dict) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color: {palette['muted']}; font-size: 11px;")
    lbl.setFixedWidth(TOGGLE_LABEL_WIDTH)
    lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return lbl


class RouteRow(QWidget):
    """라우트 한 줄: hostname → 서비스 주소, 서버 토글(또는 등록 버튼), 로그, 메뉴."""

    def __init__(self, card: "TunnelCard", route: RouteMeta):
        super().__init__(card)
        self.card = card
        self.route = route
        win = card.win
        palette = current_palette(win.ctx.store.settings.theme)
        icon_color = palette["text"]

        hostname = route.hostname or "(hostname 미설정)"
        service = route.service or "(서비스 미설정)"
        full_text = f"{hostname}  →  {service}"
        self.text_label = QLabel()
        metrics = self.text_label.fontMetrics()
        self.text_label.setText(_elide(full_text, metrics, 340))
        self.text_label.setToolTip(full_text)
        self.text_label.setSizePolicy(QSizePolicy.Policy.Expanding,
                                      QSizePolicy.Policy.Preferred)

        # I12: 도커는 start_cmd가 비어 있어도(기본값 폴백) 서버로 취급해야
        # 토글이 사라지지 않는다.
        self.has_service = bool(route.server.start_cmd) or route.server.kind == "docker"
        self.server_switch: ToggleSwitch | None = None
        self.register_btn: QPushButton | None = None
        self.spinner = Spinner(palette)
        self.spinner.hide()
        toggle_label = _toggle_label("서버", palette)
        if self.has_service:
            self.server_switch = ToggleSwitch(palette)
            self.server_switch.toggled.connect(self._on_server_toggled)
        else:
            toggle_label.setText("")  # 등록 버튼 자체가 의미를 나타내므로 라벨은 폭 정렬용 공백
            self.register_btn = QPushButton("서버 등록")
            self.register_btn.clicked.connect(self._edit_route)

        self.log_btn = QPushButton("로그")
        self.log_btn.setIcon(make_icon("log", icon_color))
        self.log_btn.setVisible(self.has_service)
        self.log_btn.clicked.connect(
            lambda: win._open_log_service(card.tunnel_name, self.route))

        menu_btn = QPushButton()
        menu_btn.setIcon(make_icon("dots", icon_color))
        menu_btn.setFixedWidth(34)
        menu_btn.clicked.connect(lambda: self._menu(menu_btn))

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.addWidget(self.text_label, 1)
        lay.addWidget(toggle_label)
        lay.addWidget(self.spinner)
        lay.addWidget(self.server_switch if self.server_switch else self.register_btn)
        lay.addWidget(self.log_btn)
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
        danger_menu_action(m, "삭제", palette, self._delete_route)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _edit_route(self):
        self.card.win._edit_route(self.card, self.route)

    def _delete_route(self):
        self.card.win._delete_route(self.card, self.route)

    def update_state(self):
        ctx = self.card.win.ctx
        if self.server_switch is not None:
            pending = ctx.manager.service_pending(self.card.tunnel_name, self.route)
            self.spinner.setVisible(pending)
            if pending:
                self.spinner.start()
            else:
                self.spinner.stop()
            running = ctx.manager.service_running(self.card.tunnel_name, self.route)
            self.server_switch.blockSignals(True)
            self.server_switch.setChecked(running)
            self.server_switch.blockSignals(False)
            # blockSignals로 setChecked하면 toggled가 안 나가 툴팁이 안
            # 갱신되므로 직접 호출한다.
            self.server_switch.update_tooltip()
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


class TunnelCard(QFrame):
    def __init__(self, win: "MainWindow", info, meta: TunnelMeta):
        super().__init__()
        self.setObjectName("card")
        self.win = win
        self.info = info
        self.meta = meta
        self.tunnel_name = info.name

        palette = current_palette(win.ctx.store.settings.theme)
        icon_color = palette["text"]

        self.dot = QLabel()
        self.dot.setFixedSize(12, 12)
        title = QLabel(info.name)
        title.setObjectName("cardTitle")
        self.state_label = QLabel()
        self.state_label.setObjectName("cardSub")

        self.tunnel_switch = ToggleSwitch(palette)
        self.tunnel_switch.toggled.connect(self._on_tunnel_toggled)
        self.spinner = Spinner(palette)
        self.spinner.hide()
        log_btn = QPushButton("로그")
        log_btn.setIcon(make_icon("log", icon_color))
        log_btn.clicked.connect(lambda: win._open_log_tunnel(self.tunnel_name))
        menu_btn = QPushButton()
        menu_btn.setIcon(make_icon("dots", icon_color))
        menu_btn.setFixedWidth(34)
        menu_btn.clicked.connect(lambda: self._tunnel_menu(menu_btn))

        header = QHBoxLayout()
        header.addWidget(self.dot)
        header.addWidget(title)
        header.addWidget(self.state_label)
        header.addStretch(1)
        header.addWidget(_toggle_label("터널", palette))
        header.addWidget(self.spinner)
        header.addWidget(self.tunnel_switch)
        header.addWidget(log_btn)
        header.addWidget(menu_btn)

        self.route_rows: list[RouteRow] = []
        routes_lay = QVBoxLayout()
        routes_lay.setContentsMargins(24, 4, 0, 0)
        for route in meta.routes:
            row = RouteRow(self, route)
            self.route_rows.append(row)
            routes_lay.addWidget(row)

        add_route_btn = QPushButton("라우트 추가")
        add_route_btn.setIcon(make_icon("plus", icon_color))
        add_route_btn.clicked.connect(lambda: win._add_route(self))
        add_row = QHBoxLayout()
        add_row.setContentsMargins(24, 4, 0, 0)
        add_row.addWidget(add_route_btn)
        add_row.addStretch(1)

        lay = QVBoxLayout(self)
        lay.addLayout(header)
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
                    self.tunnel_switch.blockSignals(True)
                    self.tunnel_switch.setChecked(False)
                    self.tunnel_switch.blockSignals(False)
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

    def _tunnel_menu(self, anchor: QPushButton):
        palette = current_palette(self.win.ctx.store.settings.theme)
        m = QMenu(self)
        danger_menu_action(m, "삭제", palette, lambda: self.win._delete_tunnel(self))
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    # ---- 표시 갱신 ----
    def update_state(self):
        ctx = self.win.ctx
        st = ctx.manager.tunnel_state(self.tunnel_name)
        self.dot.setStyleSheet(
            f"background: {STATE_COLORS[st]}; border-radius: 6px;")
        self.state_label.setText(STATE_LABELS[st])
        pending = st == TunnelState.STARTING or ctx.manager.tunnel_pending(self.tunnel_name)
        self.spinner.setVisible(pending)
        if pending:
            self.spinner.start()
        else:
            self.spinner.stop()
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
        self.tunnel_switch.blockSignals(True)
        self.tunnel_switch.setChecked(running)
        self.tunnel_switch.blockSignals(False)
        self.tunnel_switch.update_tooltip()
        if mismatch_reason:
            # 도커 오류 툴팁과 같은 자리를 재사용한다: update_tooltip()이
            # 기본 켜기/끄기 문구로 덮어쓴 뒤 사유로 다시 덮어쓴다.
            self.tunnel_switch.setToolTip(mismatch_reason)
        for row in self.route_rows:
            row.update_state()


class MainWindow(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.cards: list[TunnelCard] = []
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
        self.refresh_btn = QPushButton()
        self.refresh_btn.setIcon(make_icon("refresh", icon_color))
        self.refresh_btn.setFixedWidth(40)
        self.refresh_btn.setToolTip("새로고침")
        self.add_btn = QPushButton("터널 생성")
        self.add_btn.setIcon(make_icon("plus", palette["on_accent"]))
        self.add_btn.setObjectName("primary")
        self.settings_btn = QPushButton("설정")
        self.settings_btn.setIcon(make_icon("gear", icon_color))
        self.help_btn = QPushButton("?")
        self.help_btn.setFixedSize(34, 34)
        self.help_btn.setToolTip("사용 흐름 안내")
        self.refresh_btn.clicked.connect(self.refresh)
        self.add_btn.clicked.connect(self._create_tunnel)
        self.settings_btn.clicked.connect(self._open_settings)
        self.help_btn.clicked.connect(self._open_guide)
        self.target_combo.currentIndexChanged.connect(self._switch_target)

        top = QHBoxLayout()
        top.addWidget(QLabel("대상:"))
        top.addWidget(self.target_combo, 1)
        top.addWidget(self.refresh_btn)
        top.addWidget(self.add_btn)
        top.addWidget(self.settings_btn)
        top.addWidget(self.help_btn)

        # 카드 목록
        self.list_lay = QVBoxLayout()
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

        root = QVBoxLayout(self)
        root.addLayout(top)
        root.addWidget(self.banner)
        root.addWidget(self.info_banner)
        root.addWidget(scroll, 1)

        # C1: 폴링 실패 연속 횟수와 백오프 활성 여부. _tick()에서 관리한다.
        # refresh()가 배너를 함부로 덮어쓰지 않으려면(연결 오류 배너 보호)
        # 첫 refresh() 호출보다 먼저 초기화되어 있어야 한다.
        self._poll_failures = 0
        self._poll_backoff_active = False

        self._reload_targets()
        self.refresh()
        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(POLL_INTERVAL_NORMAL_MS)

        # 단축키 등록
        from PyQt6.QtGui import QKeySequence, QShortcut
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
            SshManagerDialog(self.ctx, self).exec()
            self._reload_targets()
            # B3/I: 프로필 삭제로 _reload_targets()가 로컬로 폴백했을 수 있다.
            # 러너만 로컬로 바뀌고 카드 목록은 이전 대상 것이 남는 것을
            # 막기 위해 항상 refresh()한다(대상이 안 바뀌어도 비용은 미미함).
            self.refresh()
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
        self.refresh()

    def _close_all_log_viewers(self):
        for viewer in list(self._log_viewers.values()):
            viewer.close()

    def _target_display_name(self) -> str:
        if not self.ctx.is_remote:
            return "이 PC"
        return self.ctx.runner.name.split(":", 1)[-1]

    # ---- 목록 ----
    def refresh(self):
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

        for c in self.cards:
            c.setParent(None)
        self.cards.clear()

        # 대상(로컬/SSH 프로필)마다 독립된 터널 설정을 쓴다. 계정 단위인
        # tunnel list는 대상 간에 같아도, config-*.yml/서버 실행 명령은
        # 대상마다 다르기 때문이다.
        metas = self.ctx.store.settings.tunnels_for(self.ctx.runner.name)
        restored = False
        for info in infos:
            meta = metas.get(info.name)
            if meta is None:
                meta = self._meta_from_config(info.name)
                metas[info.name] = meta
                restored = True
            card = TunnelCard(self, info, meta)
            self.list_lay.insertWidget(self.list_lay.count() - 1, card)
            self.cards.append(card)
        if restored:
            self.ctx.store.save()

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

    # ---- 삭제 확인(공용) ----
    def _confirm_delete(self, title: str, body_prefix: str,
                        hostnames: list[str]) -> bool:
        """삭제될 hostname 목록과 DNS 잔존 안내를 보여주는 확인 다이얼로그.

        "Cloudflare 대시보드 열기" 버튼은 브라우저만 열고(다른 동작 없음)
        같은 확인 다이얼로그를 다시 띄운다 - 사용자가 대시보드를 확인한 뒤
        삭제 여부를 마저 결정할 수 있게 하기 위함이다.
        """
        names = "\n".join(f"- {h}" for h in hostnames) if hostnames \
            else "- (hostname 미설정)"
        msg = QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setText(
            f"{body_prefix}\n\n"
            f"다음 주소의 DNS 레코드는 Cloudflare에 그대로 남습니다:\n{names}\n\n"
            "나중에 같은 주소를 다시 쓰려면 대시보드에서 지우거나, 다시 "
            "연결할 때 덮어쓰기를 선택하세요.")
        yes_btn = msg.addButton("삭제", QMessageBox.ButtonRole.YesRole)
        msg.addButton("취소", QMessageBox.ButtonRole.NoRole)
        dash_btn = msg.addButton("Cloudflare 대시보드 열기",
                                 QMessageBox.ButtonRole.ActionRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is dash_btn:
            webbrowser.open("https://dash.cloudflare.com")
            return self._confirm_delete(title, body_prefix, hostnames)
        return clicked is yes_btn

    # ---- 생성/삭제 ----
    def _create_tunnel(self):
        wiz = TunnelWizard(self.ctx, [c.tunnel_name for c in self.cards], self)
        if wiz.exec() == wiz.DialogCode.Accepted and wiz.created_meta:
            meta = wiz.created_meta
            if isinstance(meta, TunnelMeta):
                self.ctx.store.settings.tunnels_for(self.ctx.runner.name)[meta.name] = meta
                self.ctx.store.save()
            self.refresh()

    def _delete_tunnel(self, card: TunnelCard):
        name = card.tunnel_name
        hostnames = [r.hostname for r in card.meta.routes]
        ok = self._confirm_delete(
            "터널 삭제",
            f"'{name}' 터널을 삭제할까요?\n"
            "- 실행 중인 터널과 모든 라우트의 서버가 중지됩니다\n"
            "- config 파일이 삭제됩니다",
            hostnames)
        if not ok:
            return

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
        if dlg.exec() == dlg.DialogCode.Accepted:
            self._notify_restart_needed(card.tunnel_name)
            self.refresh()

    def _edit_route(self, card: TunnelCard, route: RouteMeta):
        from app.ui.route_dialog import RouteDialog
        dlg = RouteDialog(self.ctx, card.meta, route, self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self._notify_restart_needed(card.tunnel_name)
            self.refresh()

    def _delete_route(self, card: TunnelCard, route: RouteMeta):
        ok = self._confirm_delete(
            "라우트 삭제",
            f"'{route.hostname or route.id}' 라우트를 삭제할까요?\n"
            "- 서비스가 실행 중이면 중지됩니다",
            [route.hostname] if route.hostname else [])
        if not ok:
            return

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
        self.refresh()

    def _notify_restart_needed(self, tunnel_name: str):
        if self.ctx.manager.tunnel_state(tunnel_name) in (
                TunnelState.RUNNING, TunnelState.STARTING):
            self.info_banner.setText("변경을 적용하려면 터널을 껐다 켜야 합니다.")
            self.info_banner.show()

    # ---- 로그 ----
    def _open_log_tunnel(self, name: str):
        # B2: 캐시 키에 대상(runner.name)을 포함시켜 대상 전환 후 다른 대상의
        # 뷰어가 잘못 재사용되지 않게 한다.
        target = self.ctx.runner.name
        title = f"{name} ({self._target_display_name()})"
        self._open_log_viewer(
            f"tunnel:{target}:{name}", title,
            {"터널": self.ctx.manager.log_path_for_tunnel(name)})

    def _open_log_service(self, tunnel_name: str, route: RouteMeta):
        target = self.ctx.runner.name
        title = f"{tunnel_name} · {route.hostname or route.id} ({self._target_display_name()})"
        self._open_log_viewer(
            f"svc:{target}:{tunnel_name}:{route.id}", title,
            {"서버": self.ctx.manager.log_path_for_service(tunnel_name, route)})

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
        SettingsDialog(self.ctx, self).exec()
        self._reload_targets()
        if self.ctx.store.settings.theme != prev_theme:
            self._apply_theme()  # 내부에서 refresh()까지 호출한다
        else:
            # B3/I: 프로필 삭제로 로컬 폴백이 일어났을 수 있으니 항상 갱신한다.
            self.refresh()

    def _open_guide(self):
        from app.ui.guide import GuideDialog
        GuideDialog(self.ctx, self).exec()

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
        icon_color = current_palette(mode)["text"]
        self.refresh_btn.setIcon(make_icon("refresh", icon_color))
        self.settings_btn.setIcon(make_icon("gear", icon_color))
        new_palette = current_palette(mode)
        for c in self.cards:
            c.spinner.set_palette(new_palette)
            for row in c.route_rows:
                row.spinner.set_palette(new_palette)
        self._reload_targets()
        self.refresh()  # 카드를 새 팔레트로 다시 그린다 (ToggleSwitch 포함)

    def _tick(self):
        # C1: 이 슬롯은 QTimer에서 호출되는데, PyQt6는 슬롯의 미처리 예외에서
        # 프로세스를 abort시킨다(실증됨). SSH가 끊기면 refresh() 안에서
        # paramiko가 OSError/EOFError/ConnectionError 등을 던질 수 있으므로
        # 전체를 감싸 앱이 죽지 않게 하고, 배너로 사용자에게 알린 뒤 폴링
        # 주기를 늘려(백오프) 계속 실패하는 원격 호출로 UI를 붙잡지 않는다.
        try:
            self.ctx.manager.refresh([c.meta for c in self.cards])
        except Exception:
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
        if idx < len(self.cards):
            self.cards[idx].tunnel_switch.toggle()

    def _shortcut_server(self, idx: int):
        if idx < len(self.cards):
            card = self.cards[idx]
            if card.route_rows and card.route_rows[0].server_switch is not None:
                card.route_rows[0].server_switch.toggle()

    # ---- 종료 정리 ----
    def closeEvent(self, event):
        # v2: GUI는 실행 주체가 아니라 관찰자다. 종료 시 아무것도 중지하지
        # 않는다 - 터널/서버는 대상 머신에서 계속 실행되고, 다음 실행 때
        # run 디렉터리를 읽어 상태를 복원한다.
        if self._any_running():
            QMessageBox.information(
                self, "종료",
                "터널과 서버는 계속 실행됩니다.\n다시 실행하면 상태를 이어서 표시합니다.")
        self.ctx.set_local()  # SSH 연결 정리 (원격 프로세스는 그대로 유지됨)
        event.accept()
