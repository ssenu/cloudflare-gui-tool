from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QMenu,
                             QMessageBox, QPushButton, QScrollArea, QVBoxLayout,
                             QWidget)

from app.context import AppContext
from app.core.cloudflared import CloudflaredError, TunnelInfo
from app.core.config_yml import get_main_ingress, parse_config
from app.core.process_mgr import TunnelState
from app.core.store import TunnelMeta
from app.ui.icons import make_icon
from app.ui.theme import STATE_COLORS, build_qss, current_palette
from app.ui.winutil import apply_titlebar_theme
from app.ui.wizard import TunnelWizard

ADD_SSH_TARGET = "__add__"

STATE_LABELS = {
    TunnelState.STOPPED: "중지됨",
    TunnelState.STARTING: "시작 중...",
    TunnelState.RUNNING: "실행 중",
    TunnelState.ERROR: "오류",
}


class TunnelCard(QFrame):
    def __init__(self, win: "MainWindow", info: TunnelInfo, meta: TunnelMeta):
        super().__init__()
        self.setObjectName("card")
        self.win = win
        self.info = info
        self.meta = meta
        self.tunnel_name = info.name

        self.dot = QLabel()
        self.dot.setFixedSize(12, 12)
        title = QLabel(info.name)
        title.setObjectName("cardTitle")
        sub = QLabel(f"{meta.hostname or '(도메인 미설정)'}  ·  "
                     f"{meta.service or '(서비스 미설정)'}")
        sub.setObjectName("cardSub")
        self.state_label = QLabel()
        self.state_label.setObjectName("cardSub")

        icon_color = current_palette(win.ctx.store.settings.theme)["text"]
        self.toggle_btn = QPushButton()
        self.server_btn = QPushButton("서버")
        self.server_btn.setVisible(bool(meta.server_cmd))
        log_btn = QPushButton("로그")
        log_btn.setIcon(make_icon("log", icon_color))
        menu_btn = QPushButton()
        menu_btn.setIcon(make_icon("dots", icon_color))
        menu_btn.setFixedWidth(34)

        self.toggle_btn.clicked.connect(self.toggle_tunnel)
        self.server_btn.clicked.connect(self.toggle_server)
        log_btn.clicked.connect(lambda: win._open_log(self.tunnel_name))
        menu_btn.clicked.connect(lambda: self._menu(menu_btn))

        left = QVBoxLayout()
        top = QHBoxLayout()
        top.addWidget(self.dot)
        top.addWidget(title)
        top.addWidget(self.state_label)
        top.addStretch(1)
        left.addLayout(top)
        left.addWidget(sub)

        lay = QHBoxLayout(self)
        lay.addLayout(left, 1)
        lay.addWidget(self.toggle_btn)
        lay.addWidget(self.server_btn)
        lay.addWidget(log_btn)
        lay.addWidget(menu_btn)
        self.update_state()

    # ---- 동작 ----
    def toggle_tunnel(self):
        ctx = self.win.ctx
        st = ctx.manager.tunnel_state(self.tunnel_name, ctx.runner.name)
        try:
            if st in (TunnelState.STOPPED, TunnelState.ERROR):
                if not ctx.runner.file_exists(ctx.client.config_path(self.tunnel_name)):
                    QMessageBox.warning(self, "설정 없음",
                                        f"config-{self.tunnel_name}.yml 이 없습니다.\n"
                                        "카드의 메뉴 버튼에서 설정을 편집하세요.")
                    return
                ctx.manager.start_tunnel(self.tunnel_name, ctx.runner, ctx.client)
                if self.meta.start_together and self.meta.server_cmd \
                        and not ctx.manager.server_running(self.tunnel_name, ctx.runner.name):
                    ctx.manager.start_server(self.meta, ctx.runner)
            else:
                ctx.manager.stop_tunnel(self.tunnel_name, ctx.runner.name)
        except Exception as ex:
            QMessageBox.critical(self, "오류", str(ex))
        self.update_state()

    def toggle_server(self):
        ctx = self.win.ctx
        try:
            if ctx.manager.server_running(self.tunnel_name, ctx.runner.name):
                ctx.manager.stop_server(self.tunnel_name, ctx.runner.name)
            else:
                ctx.manager.start_server(self.meta, ctx.runner)
        except Exception as ex:
            QMessageBox.critical(self, "서버 오류", str(ex))
        self.update_state()

    def _menu(self, anchor: QPushButton):
        m = QMenu(self)
        edit = QAction("편집", m)
        delete = QAction("삭제", m)
        edit.triggered.connect(lambda: self.win._edit_tunnel(self))
        delete.triggered.connect(lambda: self.win._delete_tunnel(self))
        m.addAction(edit)
        m.addAction(delete)
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    # ---- 표시 갱신 ----
    def update_state(self):
        ctx = self.win.ctx
        icon_color = current_palette(ctx.store.settings.theme)["text"]
        st = ctx.manager.tunnel_state(self.tunnel_name, ctx.runner.name)
        self.dot.setStyleSheet(
            f"background: {STATE_COLORS[st]}; border-radius: 6px;")
        self.state_label.setText(STATE_LABELS[st])
        running = st in (TunnelState.STARTING, TunnelState.RUNNING)
        self.toggle_btn.setText("끄기" if running else "켜기")
        self.toggle_btn.setIcon(make_icon("stop" if running else "play", icon_color))
        if self.meta.server_cmd:
            s_run = ctx.manager.server_running(self.tunnel_name, ctx.runner.name)
            self.server_btn.setIcon(make_icon("stop" if s_run else "play", icon_color))


class MainWindow(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.cards: list[TunnelCard] = []
        self._log_viewers: dict[str, "LogViewer"] = {}
        self._current_target_index = 0
        self.setWindowTitle("Cloudflare Tunnel GUI")
        self.resize(780, 580)

        # 상단 바
        self.target_combo = QComboBox()
        icon_color = current_palette(ctx.store.settings.theme)["text"]
        self.refresh_btn = QPushButton()
        self.refresh_btn.setIcon(make_icon("refresh", icon_color))
        self.refresh_btn.setFixedWidth(40)
        self.refresh_btn.setToolTip("새로고침")
        self.add_btn = QPushButton("터널 생성")
        self.add_btn.setIcon(make_icon("plus", "#ffffff"))
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

        root = QVBoxLayout(self)
        root.addLayout(top)
        root.addWidget(self.banner)
        root.addWidget(scroll, 1)

        self._reload_targets()
        self.refresh()
        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

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

    # ---- 대상 전환 ----
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
        idx = self._current_target_index
        if idx >= self.target_combo.count():
            idx = 0
        self.target_combo.setCurrentIndex(idx)
        self.target_combo.blockSignals(False)

    def _switch_target(self):
        if self.target_combo.currentData() == ADD_SSH_TARGET:
            self.target_combo.blockSignals(True)
            self.target_combo.setCurrentIndex(self._current_target_index)
            self.target_combo.blockSignals(False)
            from app.ui.ssh_manager import SshManagerDialog
            SshManagerDialog(self.ctx, self).exec()
            self._reload_targets()
            return

        if self.ctx.manager.any_running():
            ok = QMessageBox.question(
                self, "대상 전환",
                "대상 전환 시 현재 대상에서 실행 중인 터널/서버가 모두 중지됩니다. 계속할까요?")
            if ok != QMessageBox.StandardButton.Yes:
                self.target_combo.blockSignals(True)
                self.target_combo.setCurrentIndex(self._current_target_index)
                self.target_combo.blockSignals(False)
                return
            self.ctx.manager.stop_all()

        profile = self.target_combo.currentData()
        self.banner.hide()
        try:
            if profile is None:
                self.ctx.set_local()
            else:
                self.setCursor(Qt.CursorShape.WaitCursor)
                self.ctx.set_remote(profile)
            self._current_target_index = self.target_combo.currentIndex()
        except Exception as ex:
            self.banner.setText(f"SSH 연결 실패: {ex}")
            self.banner.show()
            self.target_combo.blockSignals(True)
            self.target_combo.setCurrentIndex(0)
            self.target_combo.blockSignals(False)
            self.ctx.set_local()
            self._current_target_index = 0
        finally:
            self.unsetCursor()
        self.refresh()

    # ---- 목록 ----
    def refresh(self):
        try:
            self.setCursor(Qt.CursorShape.WaitCursor)
            infos = self.ctx.client.list_tunnels()
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

        # tunnels dict는 local/remote 대상 간에 공유된다 (v1 한계: 이름 충돌 시 메타가 섞일 수 있음)
        metas = self.ctx.store.settings.tunnels
        self._meta_restored = False
        for info in infos:
            meta = metas.get(info.name) or self._meta_from_config(info.name)
            card = TunnelCard(self, info, meta)
            self.list_lay.insertWidget(self.list_lay.count() - 1, card)
            self.cards.append(card)
        if self._meta_restored:
            self.ctx.store.save()

    def _meta_from_config(self, name: str) -> TunnelMeta:
        """settings.json에 없는 터널: config yml에서 hostname/service 복원."""
        meta = TunnelMeta(name=name)
        try:
            path = self.ctx.client.config_path(name)
            if self.ctx.runner.file_exists(path):
                cfg = parse_config(self.ctx.runner.read_file(path))
                meta.hostname, meta.service = get_main_ingress(cfg)
        except Exception:
            pass
        self.ctx.store.settings.tunnels[name] = meta
        self._meta_restored = True
        return meta

    # ---- 생성/편집/삭제 ----
    def _create_tunnel(self):
        wiz = TunnelWizard(self.ctx, [c.tunnel_name for c in self.cards], self)
        if wiz.exec() == wiz.DialogCode.Accepted and wiz.created_meta:
            self.ctx.store.settings.tunnels[wiz.created_meta.name] = wiz.created_meta
            self.ctx.store.save()
            self.refresh()

    def _edit_tunnel(self, card: TunnelCard):
        from app.ui.edit_dialog import EditTunnelDialog  # Task 12
        dlg = EditTunnelDialog(self.ctx, card.meta, self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self.ctx.store.save()
            self.refresh()

    def _delete_tunnel(self, card: TunnelCard):
        name = card.tunnel_name
        ok = QMessageBox.question(
            self, "터널 삭제",
            f"'{name}' 터널을 삭제할까요?\n\n"
            "- 실행 중이면 중지됩니다\n"
            "- config 파일이 삭제됩니다\n"
            "- DNS CNAME 레코드는 Cloudflare 대시보드에서 직접 삭제해야 합니다")
        if ok != QMessageBox.StandardButton.Yes:
            return
        runner_name = self.ctx.runner.name
        self.ctx.manager.stop_tunnel(name, runner_name)
        self.ctx.manager.stop_server(name, runner_name)
        # 프로세스가 완전히 종료될 때까지 잠시 대기 (cloudflared가 연결을 정리할 시간)
        for _ in range(10):
            if self.ctx.manager.tunnel_state(name, runner_name) in (
                    TunnelState.STOPPED, TunnelState.ERROR):
                break
            time.sleep(0.2)

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
            self.ctx.store.settings.tunnels.pop(name, None)
            self.ctx.store.save()
            try:
                path = self.ctx.client.config_path(name)
                if self.ctx.runner.file_exists(path):
                    self.ctx.runner.remove_file(path)
            except Exception as ex:
                QMessageBox.warning(self, "파일 삭제 실패",
                                    f"터널은 삭제되었지만 config 파일 삭제에 실패했습니다.\n{ex}")
        self.refresh()

    # ---- 이후 태스크에서 연결 ----
    def _open_log(self, name: str):
        from app.ui.log_viewer import LogViewer
        if name in self._log_viewers:
            viewer = self._log_viewers[name]
            if viewer.isVisible():
                viewer.raise_()
                viewer.activateWindow()
                return
        viewer = LogViewer(self.ctx, name, self)
        self._log_viewers[name] = viewer
        viewer.destroyed.connect(lambda *_, n=name: self._log_viewers.pop(n, None))
        viewer.show()

    def _open_settings(self):
        from app.ui.settings import SettingsDialog
        prev_theme = self.ctx.store.settings.theme
        SettingsDialog(self.ctx, self).exec()
        self._reload_targets()
        if self.ctx.store.settings.theme != prev_theme:
            self._apply_theme()

    def _open_guide(self):
        from app.ui.guide import GuideDialog
        GuideDialog(self.ctx, self).exec()

    def _apply_theme(self):
        from PyQt6.QtWidgets import QApplication
        mode = self.ctx.store.settings.theme
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_qss(mode))
        apply_titlebar_theme(self, mode == "dark")
        for viewer in list(self._log_viewers.values()):
            apply_titlebar_theme(viewer, mode == "dark")
        self._style_banner()
        icon_color = current_palette(mode)["text"]
        self.refresh_btn.setIcon(make_icon("refresh", icon_color))
        self.settings_btn.setIcon(make_icon("gear", icon_color))
        self._reload_targets()
        self.refresh()

    def _tick(self):
        for c in self.cards:
            c.update_state()

    # ---- 단축키 ----
    def _shortcut_tunnel(self, idx: int):
        if idx < len(self.cards):
            self.cards[idx].toggle_tunnel()

    def _shortcut_server(self, idx: int):
        if idx < len(self.cards):
            self.cards[idx].toggle_server()

    # ---- 종료 정리 ----
    def closeEvent(self, event):
        if self.ctx.manager.any_running():
            ok = QMessageBox.question(
                self, "종료", "실행 중인 터널/서버가 있습니다.\n"
                              "종료하면 모두 중지됩니다. 종료할까요?")
            if ok != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.ctx.manager.stop_all()
        self.ctx.set_local()  # SSH 연결 정리
        event.accept()
