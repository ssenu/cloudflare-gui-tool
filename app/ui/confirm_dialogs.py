"""삭제 확인 다이얼로그: hostname 목록 + DNS 잔존 안내 + 대시보드 버튼.

MainWindow._open_modal()로 오버레이에 임베드되는 것을 전제로 exec()는 쓰지
않는다 - 호출부는 finished 시그널로 accept/reject를 받는다. "Cloudflare
대시보드 열기" 버튼은 브라우저만 열고 다이얼로그는 그대로 열린 채로 둔다
(닫지 않음 - 사용자가 대시보드를 확인한 뒤 다시 판단할 수 있게).
"""
from __future__ import annotations

import webbrowser

from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QVBoxLayout)

from app.core.confirm import tunnel_name_matches
from app.ui.theme import current_palette
from app.ui.winutil import apply_titlebar_theme


class ConfirmDeleteDialog(QDialog):
    """라우트 삭제 등 일반 삭제 확인에 쓰는 공용 다이얼로그."""

    def __init__(self, ctx, title: str, body: str, hostnames: list[str],
                 parent=None, heading: str = ""):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        palette = current_palette(ctx.store.settings.theme)

        heading_label = None
        if heading:
            heading_label = QLabel(heading)
            heading_label.setWordWrap(True)
            heading_label.setStyleSheet(
                f"font-size: 16px; font-weight: 700; color: {palette['text']};")

        names = "\n".join(f"- {h}" for h in hostnames) if hostnames \
            else "- (hostname 미설정)"
        text = QLabel(
            f"{body}\n\n"
            f"다음 주소의 DNS 레코드는 Cloudflare에 그대로 남습니다:\n{names}\n\n"
            "나중에 같은 주소를 다시 쓰려면 대시보드에서 지우거나, 다시 "
            "연결할 때 덮어쓰기를 선택하세요.")
        text.setWordWrap(True)

        self.confirm_btn = QPushButton("삭제")
        self.confirm_btn.setObjectName("danger")
        self.confirm_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("취소")
        cancel_btn.clicked.connect(self.reject)
        dash_btn = QPushButton("Cloudflare 대시보드 열기")
        dash_btn.clicked.connect(self._open_dashboard)

        btn_row = QHBoxLayout()
        btn_row.addWidget(dash_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self.confirm_btn)

        root = QVBoxLayout(self)
        if heading_label is not None:
            root.addWidget(heading_label)
        root.addWidget(text)
        # 하위 클래스(TunnelDeleteDialog)가 이름 입력칸을 끼워 넣는 자리.
        self._extra_layout = QVBoxLayout()
        root.addLayout(self._extra_layout)
        root.addLayout(btn_row)

        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

    def _open_dashboard(self) -> None:
        webbrowser.open("https://dash.cloudflare.com")


class TunnelDeleteDialog(ConfirmDeleteDialog):
    """터널 삭제 확인: 터널 이름을 정확히 입력해야 삭제 버튼이 활성화된다."""

    def __init__(self, ctx, tunnel_name: str, hostnames: list[str], parent=None):
        body = ("- 실행 중인 터널과 모든 라우트의 서버가 중지됩니다\n"
                "- 등록된 서버 정보(라우트·실행 명령 등)가 모두 삭제됩니다\n"
                "- config 파일이 삭제됩니다")
        super().__init__(ctx, "터널 삭제", body, hostnames, parent,
                         heading=f"'{tunnel_name}' 터널을 삭제할까요?")
        self.tunnel_name = tunnel_name

        hint = QLabel(f"삭제하려면 터널 이름 {tunnel_name} 을 입력하세요")
        hint.setWordWrap(True)
        palette = current_palette(ctx.store.settings.theme)
        hint.setStyleSheet(f"color: {palette['muted']};")
        self.name_edit = QLineEdit()
        self.name_edit.textChanged.connect(self._update_confirm_enabled)

        self._extra_layout.addWidget(hint)
        self._extra_layout.addWidget(self.name_edit)
        self.confirm_btn.setEnabled(False)

    def _update_confirm_enabled(self, text: str) -> None:
        self.confirm_btn.setEnabled(tunnel_name_matches(text, self.tunnel_name))
