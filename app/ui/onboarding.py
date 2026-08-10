from __future__ import annotations

import threading

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox,
                             QPushButton, QVBoxLayout)

from app.context import AppContext


def needs_onboarding(ctx: AppContext) -> bool:
    client = ctx.client
    return client.version() is None or not client.cert_exists()


class OnboardingDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("초기 설정")
        self.setMinimumWidth(460)

        lay = QVBoxLayout(self)
        title = QLabel("🌩️ Cloudflare Tunnel GUI 초기 설정")
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        lay.addWidget(title)

        # 1단계: 설치
        self.install_label = QLabel()
        self.install_btn = QPushButton("winget으로 설치")
        self.install_btn.clicked.connect(self._install)
        row1 = QHBoxLayout()
        row1.addWidget(self.install_label, 1)
        row1.addWidget(self.install_btn)
        lay.addLayout(row1)

        # 2단계: 로그인
        self.login_label = QLabel()
        self.login_btn = QPushButton("Cloudflare 로그인")
        self.login_btn.clicked.connect(self._login)
        row2 = QHBoxLayout()
        row2.addWidget(self.login_label, 1)
        row2.addWidget(self.login_btn)
        lay.addLayout(row2)

        self.done_btn = QPushButton("시작하기")
        self.done_btn.setObjectName("primary")
        self.done_btn.clicked.connect(self.accept)
        lay.addWidget(self.done_btn, alignment=Qt.AlignmentFlag.AlignRight)

        self._status: tuple[str | None, bool] | None = None
        self._polling = False
        self._install_exit: int | None = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(2000)
        self._refresh()

    def _poll_worker(self):
        try:
            client = self.ctx.client
            self._status = (client.version(), client.cert_exists())
        finally:
            self._polling = False

    def _refresh(self):
        if not self._polling:
            self._polling = True
            threading.Thread(target=self._poll_worker, daemon=True).start()

        status = self._status
        ver, cert_exists = status if status is not None else (None, False)
        installed = status is not None and ver is not None
        cert = status is not None and installed and cert_exists

        if status is None:
            install_text = "① cloudflared 설치: 확인 중..."
        elif installed:
            install_text = f"① cloudflared 설치: ✅ {ver}"
        else:
            install_text = "① cloudflared 설치: ❌ 미설치"

        if self._install_exit not in (None, 0) and not installed:
            install_text += " ❌ 설치 실패 — 다시 시도하세요"
            self.install_btn.setEnabled(True)
            self.install_btn.setVisible(True)
            self._install_exit = None

        self.install_label.setText(install_text)
        if status is not None:
            self.install_btn.setVisible(not installed)
        self.login_label.setText(
            f"② Cloudflare 로그인 (cert.pem): {'✅ 완료' if cert else '❌ 필요'}"
            if status is not None else "② Cloudflare 로그인 (cert.pem): 확인 중...")
        self.login_btn.setVisible(installed and not cert)
        self.done_btn.setEnabled(installed and cert)

    def _install(self):
        self.install_btn.setEnabled(False)
        self._install_exit = None
        try:
            self.ctx.local_runner.spawn(
                ["winget", "install", "--id", "Cloudflare.cloudflared",
                 "--accept-source-agreements", "--accept-package-agreements"],
                on_exit=lambda c: setattr(self, "_install_exit", c))
        except Exception as ex:
            self.install_btn.setEnabled(True)
            QMessageBox.critical(self, "실행 실패", str(ex))
            return
        QMessageBox.information(
            self, "설치 시작",
            "설치가 진행 중입니다. 완료되면 상태가 자동으로 갱신됩니다.\n"
            "(갱신되지 않으면 앱을 재시작하세요 — PATH 반영 때문)")

    def _login(self):
        # 브라우저가 열리고 사용자가 도메인을 선택하면 cert.pem이 생성된다.
        try:
            self.ctx.local_runner.spawn(self.ctx.client.login_args())
        except Exception as ex:
            QMessageBox.critical(self, "실행 실패", str(ex))
            return
        QMessageBox.information(
            self, "로그인", "브라우저에서 Cloudflare 로그인 후 도메인을 선택하세요.\n"
                          "완료되면 이 창의 상태가 자동으로 갱신됩니다.")

    def accept(self):
        self._timer.stop()
        super().accept()

    def reject(self):
        self._timer.stop()
        super().reject()
