from __future__ import annotations

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
from app.ui.theme import STATE_COLORS
from app.ui.wizard import TunnelWizard

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

        self.dot = QLabel("●")
        title = QLabel(info.name)
        title.setObjectName("cardTitle")
        sub = QLabel(f"{meta.hostname or '(도메인 미설정)'}  ·  "
                     f"{meta.service or '(서비스 미설정)'}")
        sub.setObjectName("cardSub")
        self.state_label = QLabel()
        self.state_label.setObjectName("cardSub")

        self.toggle_btn = QPushButton()
        self.server_btn = QPushButton("▶ 서버")
        self.server_btn.setVisible(bool(meta.server_cmd))
        log_btn = QPushButton("📜 로그")
        menu_btn = QPushButton("⋮")
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
        st = ctx.manager.tunnel_state(self.tunnel_name)
        try:
            if st in (TunnelState.STOPPED, TunnelState.ERROR):
                if not ctx.runner.file_exists(ctx.client.config_path(self.tunnel_name)):
                    QMessageBox.warning(self, "설정 없음",
                                        f"config-{self.tunnel_name}.yml 이 없습니다.\n"
                                        "⋮ 메뉴에서 설정을 편집하세요.")
                    return
                ctx.manager.start_tunnel(self.tunnel_name, ctx.runner, ctx.client)
                if self.meta.start_together and self.meta.server_cmd \
                        and not ctx.manager.server_running(self.tunnel_name):
                    ctx.manager.start_server(self.meta, ctx.runner)
            else:
                ctx.manager.stop_tunnel(self.tunnel_name)
        except Exception as ex:
            QMessageBox.critical(self, "오류", str(ex))
        self.update_state()

    def toggle_server(self):
        ctx = self.win.ctx
        try:
            if ctx.manager.server_running(self.tunnel_name):
                ctx.manager.stop_server(self.tunnel_name)
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
        st = self.win.ctx.manager.tunnel_state(self.tunnel_name)
        self.dot.setStyleSheet(f"color: {STATE_COLORS[st]}; font-size: 16px;")
        self.state_label.setText(STATE_LABELS[st])
        running = st in (TunnelState.STARTING, TunnelState.RUNNING)
        self.toggle_btn.setText("■ 끄기" if running else "▶ 켜기")
        if self.meta.server_cmd:
            s_run = self.win.ctx.manager.server_running(self.tunnel_name)
            self.server_btn.setText("■ 서버" if s_run else "▶ 서버")


class MainWindow(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.cards: list[TunnelCard] = []
        self.setWindowTitle("Cloudflare Tunnel GUI")
        self.resize(780, 580)

        # 상단 바
        self.target_combo = QComboBox()
        refresh_btn = QPushButton("🔄 새로고침")
        add_btn = QPushButton("＋ 터널 생성")
        add_btn.setObjectName("primary")
        settings_btn = QPushButton("⚙ 설정")
        refresh_btn.clicked.connect(self.refresh)
        add_btn.clicked.connect(self._create_tunnel)
        settings_btn.clicked.connect(self._open_settings)
        self.target_combo.currentIndexChanged.connect(self._switch_target)

        top = QHBoxLayout()
        top.addWidget(QLabel("대상:"))
        top.addWidget(self.target_combo, 1)
        top.addWidget(refresh_btn)
        top.addWidget(add_btn)
        top.addWidget(settings_btn)

        # 카드 목록
        self.list_lay = QVBoxLayout()
        self.list_lay.addStretch(1)
        inner = QWidget()
        inner.setLayout(self.list_lay)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)

        self.banner = QLabel()
        self.banner.setStyleSheet(
            "background:#3a1d1d;color:#f85149;padding:6px;border-radius:6px;")
        self.banner.hide()

        root = QVBoxLayout(self)
        root.addLayout(top)
        root.addWidget(self.banner)
        root.addWidget(scroll, 1)

        self._reload_targets()
        self.refresh()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    # ---- 대상 전환 ----
    def _reload_targets(self):
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        self.target_combo.addItem("🖥️ 이 PC", None)
        for p in self.ctx.store.settings.ssh_profiles:
            self.target_combo.addItem(f"🍓 {p.name} ({p.host})", p)
        self.target_combo.blockSignals(False)

    def _switch_target(self):
        profile = self.target_combo.currentData()
        self.banner.hide()
        try:
            if profile is None:
                self.ctx.set_local()
            else:
                self.setCursor(Qt.CursorShape.WaitCursor)
                self.ctx.set_remote(profile)
        except Exception as ex:
            self.banner.setText(f"SSH 연결 실패: {ex}")
            self.banner.show()
            self.target_combo.setCurrentIndex(0)
            self.ctx.set_local()
        finally:
            self.unsetCursor()
        self.refresh()

    # ---- 목록 ----
    def refresh(self):
        try:
            self.setCursor(Qt.CursorShape.WaitCursor)
            infos = self.ctx.client.list_tunnels()
        except Exception as ex:
            self.banner.setText(f"터널 목록 조회 실패: {ex}")
            self.banner.show()
            infos = []
        finally:
            self.unsetCursor()

        for c in self.cards:
            c.setParent(None)
        self.cards.clear()

        metas = self.ctx.store.settings.tunnels
        for info in infos:
            meta = metas.get(info.name) or self._meta_from_config(info.name)
            card = TunnelCard(self, info, meta)
            self.list_lay.insertWidget(self.list_lay.count() - 1, card)
            self.cards.append(card)

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
        self.ctx.store.save()
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
        try:
            self.ctx.manager.stop_tunnel(name)
            self.ctx.manager.stop_server(name)
            self.ctx.client.delete_tunnel(name)
            path = self.ctx.client.config_path(name)
            if self.ctx.runner.file_exists(path):
                self.ctx.runner.remove_file(path)
            self.ctx.store.settings.tunnels.pop(name, None)
            self.ctx.store.save()
        except CloudflaredError as ex:
            QMessageBox.critical(self, "삭제 실패", str(ex))
        self.refresh()

    # ---- 이후 태스크에서 연결 ----
    def _open_log(self, name: str):
        pass  # Task 11

    def _open_settings(self):
        pass  # Task 12

    def _tick(self):
        for c in self.cards:
            c.update_state()
