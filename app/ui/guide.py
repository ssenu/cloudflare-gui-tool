"""상단 '?' 버튼의 사용 흐름 안내.

이 창은 메인 창 안에 모달로 얹히므로 부모의 90%를 넘을 수 없다. 예전에는
1040x780으로 잡고 4개짜리 가로 단계 상자(각 200px 고정)를 늘어놓아, 실제로
표시되는 폭보다 내용이 넓어 좌우 스크롤이 생겼다. 지금은 한 줄에 한 단계씩
쌓는 단일 열 구성이라 폭이 얼마든 줄바꿈으로 흡수된다.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
                             QLabel, QScrollArea, QVBoxLayout, QWidget)

from app.context import AppContext
from app.ui.theme import current_palette
from app.ui.winutil import apply_titlebar_theme

SETUP_STEPS = [
    ("도메인 준비",
     "원하는 등록업체에서 도메인을 구입합니다."),
    ("Cloudflare에 등록",
     "대시보드에 사이트를 추가하고, 등록업체에서 네임서버를 Cloudflare 주소로 "
     "바꿉니다. 상태가 Active가 되어야 합니다."),
    ("cloudflared 설치",
     "터널을 연결해 주는 프로그램입니다. PATH에 없으면 설정에서 경로를 지정할 "
     "수 있습니다."),
    ("로그인",
     "cloudflared tunnel login 으로 인증서(cert.pem)를 받습니다. 원격 기기에서 "
     "터널을 만들 거라면 그 기기에서도 한 번 해야 합니다."),
]

PUBLISH_STEPS = [
    ("터널 생성",
     "'＋터널 생성'을 누릅니다. 터널 이름은 목록에서 구분하기 위한 것이라 "
     "접속 주소와는 상관없습니다."),
    ("도메인 연결",
     "서브도메인과 루트 도메인을 넣으면 CNAME이 자동으로 만들어집니다. "
     "나중에 붙이려면 '터널만 만들기'를 고르고 뒤에 '라우트 추가'로 연결합니다."),
    ("로컬 서비스 주소",
     "서버가 실제로 듣고 있는 주소입니다(예: http://localhost:8000). "
     "도커라면 compose에 공개한 호스트 포트와 반드시 같아야 합니다."),
    ("서버 등록 (선택)",
     "실행 명령을 적거나 종류를 '도커 컴포즈'로 고르면, 그다음부터 카드의 "
     "'서버' 토글로 켜고 끌 수 있습니다."),
    ("켜기",
     "서버를 먼저, 터널을 나중에 켜는 편이 로그가 깨끗합니다. 라우트를 "
     "추가·수정한 뒤에는 터널을 껐다 켜야 반영됩니다."),
    ("접속 확인",
     "라우트 줄의 도메인을 누르면 그 주소가 브라우저에서 열립니다."),
]

NOTES = [
    "공유기 포트포워딩이나 방화벽 개방이 필요 없습니다. cloudflared가 바깥으로 "
    "나가는 연결만 만들기 때문입니다.",
    "HTTPS는 Cloudflare가 처리합니다. 로컬 서버는 http로 두어도 됩니다.",
    "웹서버는 127.0.0.1에만 바인딩하는 것이 안전합니다(도커라면 "
    "\"127.0.0.1:8000:8000\").",
    "앱을 종료해도 터널과 서버는 대상 머신에서 계속 실행됩니다. 24시간 "
    "운영하려면 라즈베리파이에 올리고 SSH 원격 모드로 관리하세요.",
    "404가 뜨면 그 주소가 ingress에 없는 것입니다 - 라우트를 추가했는데도 "
    "그렇다면 터널을 껐다 켜세요. 502는 반대로 라우트는 맞는데 그 포트에 "
    "서버가 없다는 뜻입니다.",
    "터널을 켜는 순간 주소를 아는 누구나 접속할 수 있습니다. 개인용이라면 "
    "Cloudflare Access로 제한을 거세요.",
]


class GuideDialog(QDialog):
    # 모달은 부모의 90%까지만 커진다. 그보다 작게 잡아 클램프에 걸리지 않게 한다.
    PREFERRED_WIDTH = 520
    PREFERRED_HEIGHT = 560

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.p = current_palette(ctx.store.settings.theme)
        self.setWindowTitle("사용 흐름 안내")
        self.resize(self.PREFERRED_WIDTH, self.PREFERRED_HEIGHT)

        content = QWidget()
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 8, 0)  # 세로 스크롤바 자리
        content_lay.setSpacing(10)

        content_lay.addWidget(self._heading("사전 준비 (최초 1회)"))
        for i, (title, desc) in enumerate(SETUP_STEPS, start=1):
            content_lay.addWidget(self._step_row(i, title, desc))

        content_lay.addSpacing(6)
        content_lay.addWidget(self._heading("사이트 하나 올리기"))
        for i, (title, desc) in enumerate(PUBLISH_STEPS, start=1):
            content_lay.addWidget(self._step_row(i, title, desc))

        content_lay.addSpacing(6)
        content_lay.addWidget(self._heading("알아두기"))
        content_lay.addWidget(self._notes())
        content_lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # 좌우 스크롤은 만들지 않는다. 모든 글자는 줄바꿈으로 흡수한다.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        self._content = content

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("확인")
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(scroll, 1)
        root.addWidget(buttons)

        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

    def fit_to_content(self) -> None:
        """ModalOverlay가 표시 직후 부르는 훅.

        기본값(sizeHint)대로 두면 스크롤 영역을 품은 다이얼로그가 아주 작게
        잡혀 몇 줄만 보이고 계속 스크롤해야 한다. 창이 허용하는 만큼 펴 준다
        (오버레이가 다시 부모의 90%로 제한하므로 넘칠 일은 없다).
        """
        parent = self.parentWidget()
        if parent is None:
            self.resize(self.PREFERRED_WIDTH, self.PREFERRED_HEIGHT)
            return
        area = parent.rect()
        self.resize(min(self.PREFERRED_WIDTH, int(area.width() * 0.9)),
                    min(self.PREFERRED_HEIGHT, int(area.height() * 0.9)))

    # ---- 조각 ----
    def _heading(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {self.p['accent']};")
        return lbl

    def _step_row(self, num: int, title: str, desc: str) -> QWidget:
        """번호 배지 + (제목/설명) 한 줄. 카드 테두리를 두르지 않아 가볍게 읽힌다."""
        badge = QLabel(str(num))
        badge.setFixedSize(22, 22)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background: {self.p['accent']}; color: {self.p['on_accent']}; "
            "border-radius: 11px; font-size: 11px; font-weight: 700;")

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-weight: 700;")
        title_lbl.setWordWrap(True)

        desc_lbl = QLabel(desc)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"color: {self.p['muted']}; font-size: 12px;")

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        text_col.addWidget(title_lbl)
        text_col.addWidget(desc_lbl)

        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(text_col, 1)
        return row

    def _notes(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        for line in NOTES:
            lbl = QLabel(f"· {line}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("font-size: 12px;")
            lay.addWidget(lbl)
        return frame
