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

REMOTE_STEPS = [
    ("공개키 만들기",
     "PC에서 ssh-keygen -t ed25519 로 키를 만들고, 공개키(.pub)를 대상 기기의 "
     "~/.ssh/authorized_keys 에 넣습니다. 이 앱은 키 인증만 지원하며 비밀번호는 "
     "저장하지 않습니다."),
    ("대상 추가",
     "상단 '대상' 목록에서 'SSH 대상 추가...'를 고르고 이름·호스트·사용자·키 "
     "파일을 입력합니다. 호스트는 Tailscale 주소나 LAN IP를 넣습니다."),
    ("바깥에서도 접속하려면",
     "Tailscale 같은 메시 VPN을 깔면 공유기 설정 없이 어디서나 같은 주소로 "
     "붙을 수 있습니다. 자세한 절차는 docs/rpi-selfhost-setup.md 에 있습니다."),
    ("대상 전환",
     "대상을 바꾸면 터널·서버·프로젝트·로그가 모두 그 기기 것으로 바뀝니다. "
     "이전 대상에서 돌던 것은 계속 실행된 채 화면에서만 사라집니다."),
    ("그 기기에서도 로그인",
     "원격 기기에서 터널을 만들려면 그 기기에서도 cloudflared tunnel login 을 "
     "한 번 해야 합니다. 자격증명이 없는 터널은 '이 대상에서 실행 불가'로 "
     "표시되고 토글이 잠깁니다."),
]

PROJECT_STEPS = [
    ("클론 추가",
     "SSH 대상을 고른 뒤 '프로젝트' 버튼을 누르고 Git 주소를 넣으면 대상 "
     "기기에 내려받습니다(내 PC에서는 쓰지 않는 기능이라 잠겨 있습니다). "
     "비공개 저장소는 git@github.com:이름/저장소.git 형식과 대상 기기에 등록한 "
     "키가 필요합니다."),
    ("진행 확인",
     "받는 동안 왼쪽 동그라미가 주황색이 되고, 끝나면 초록색 '준비됨 · 커밋'이 "
     "됩니다. ⋮ → 로그에서 진행 내용을 볼 수 있습니다."),
    ("환경설정(.env)",
     "⋮ → 환경설정에서 비밀번호 같은 값을 넣습니다. 비밀번호류는 가려서 "
     "보여주고, 내 PC에 있던 .env를 '파일에서 불러오기'로 올릴 수도 있습니다."),
    ("포트 정하기",
     "여러 프로젝트를 올릴 땐 호스트 포트를 다르게 줍니다. compose에 "
     "\"127.0.0.1:${HOST_PORT:-8000}:8000\" 처럼 적어 두면 .env의 HOST_PORT만 "
     "바꾸면 되고, compose 파일을 건드리지 않아 git pull과 충돌하지 않습니다."),
    ("서버로 등록",
     "라우트 편집창의 '프로젝트에서 선택'으로 그 폴더를 작업 폴더에 넣고 종류를 "
     "'도커 컴포즈'로 고릅니다. 포트도 자동으로 채워집니다."),
    ("업데이트",
     "코드를 고친 뒤에는 ⋮ → 업데이트(git pull)로 받아오고, 서버 토글을 껐다 "
     "켜면 새 코드로 다시 뜹니다."),
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
        content_lay.addWidget(self._heading("원격 기기 관리 (SSH)"))
        for i, (title, desc) in enumerate(REMOTE_STEPS, start=1):
            content_lay.addWidget(self._step_row(i, title, desc))

        content_lay.addSpacing(6)
        content_lay.addWidget(self._heading("프로젝트 올리기 (Git 클론)"))
        for i, (title, desc) in enumerate(PROJECT_STEPS, start=1):
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
