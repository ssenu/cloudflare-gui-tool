from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
                             QLabel, QScrollArea, QVBoxLayout, QWidget)

from app.context import AppContext
from app.ui.icons import make_icon
from app.ui.theme import current_palette
from app.ui.winutil import apply_titlebar_theme


class GuideDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.p = current_palette(ctx.store.settings.theme)
        self.setWindowTitle("사용 흐름 안내")
        self.resize(1040, 780)

        content = QWidget()
        content_lay = QVBoxLayout(content)
        content_lay.setSpacing(16)

        content_lay.addWidget(self._heading("사전 준비 (최초 1회)"))
        content_lay.addLayout(self._section1())

        content_lay.addWidget(self._heading("사이트마다 반복"))
        content_lay.addLayout(self._section2())

        content_lay.addWidget(self._heading("알아두기"))
        content_lay.addWidget(self._section3())

        content_lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self._content = content

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("확인")
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addWidget(scroll, 1)
        root.addWidget(buttons)

        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

    # ---- 섹션 구성 ----
    def _heading(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {self.p['accent']};")
        return lbl

    def _section1(self) -> QHBoxLayout:
        items = [
            (1, "도메인 구매",
             "가비아, Namecheap 등 원하는 등록업체에서 도메인을 구입합니다."),
            (2, "Cloudflare에 도메인 등록",
             "Cloudflare 대시보드에 사이트를 추가한 뒤, 구매한 곳에서 네임서버를 "
             "Cloudflare 주소로 변경합니다. 상태가 Active가 되기까지 몇 분에서 몇 "
             "시간이 걸립니다."),
            (3, "cloudflared 설치",
             "터널을 연결해 주는 프로그램입니다. 이 앱을 처음 실행하면 온보딩 화면에서 "
             "설치를 안내합니다."),
            (4, "Cloudflare 로그인",
             "브라우저에서 도메인을 선택하면 인증서(cert.pem)가 저장됩니다. 이 역시 "
             "온보딩 화면에서 한 번만 진행합니다."),
        ]
        lay = QHBoxLayout()
        for i, (num, title, desc) in enumerate(items):
            box = self._step_box(num, title, desc)
            box.setFixedWidth(200)
            lay.addWidget(box)
            if i < len(items) - 1:
                lay.addWidget(self._arrow("arrow_right"))
        return lay

    def _section2(self) -> QVBoxLayout:
        items = [
            (1, "터널 생성",
             "메인 화면의 '터널 생성' 버튼을 누르고 터널 이름을 입력합니다. 이름은 "
             "목록에서 구분하기 위한 것으로 접속 주소와는 무관합니다."),
            (2, "도메인 연결",
             "서브도메인과 루트 도메인을 입력하면 Cloudflare에 CNAME 레코드가 자동으로 "
             "만들어집니다. 예를 들어 mysite와 example.com을 넣으면 접속 주소는 "
             "mysite.example.com이 됩니다."),
            (3, "로컬 서비스 주소 입력",
             "내 컴퓨터에서 웹서버가 실제로 듣고 있는 주소를 적습니다. 예: "
             "http://localhost:8000. 이 내용으로 설정 파일(config.yml)이 자동 "
             "작성됩니다."),
            (4, "웹서버 실행 명령 등록 (선택)",
             "uvicorn main:app --port 8000 처럼 서버를 켜는 명령을 등록해 두면, 카드의 "
             "서버 토글이나 '함께 시작' 옵션으로 터널과 같이 실행할 수 있습니다. 도커 "
             "컴포즈로 띄우는 서비스라면 종류를 '도커 컴포즈'로 선택해 등록할 수도 "
             "있습니다."),
            (5, "웹서버 실행",
             "터널보다 웹서버를 먼저 켜는 것이 좋습니다. 순서가 반대여도 서버가 뜨면 "
             "자동으로 연결되지만, 그 사이 로그에 연결 거부 메시지가 남습니다."),
            (6, "터널 켜기",
             "카드의 '터널' 토글을 켭니다. 상태등이 초록색으로 바뀌고 로그에 "
             "Registered tunnel connection이 보이면 연결된 것입니다. 터널 토글과 라우트의 "
             "'서버' 토글은 서로 독립적으로 동작하므로, 터널만 켜 두고 서버는 필요할 때만 "
             "켜는 식으로 따로 관리할 수 있습니다."),
            (7, "접속 확인",
             "휴대폰에서 와이파이를 끄고 데이터로 접속 주소에 들어가 보세요. 페이지가 "
             "뜨면 외부 공개가 완료된 것입니다."),
        ]
        lay = QVBoxLayout()
        for i, (num, title, desc) in enumerate(items):
            highlight = (num == 7)
            box = self._step_box(num, title, desc, highlight=highlight)
            lay.addWidget(box)
            if i < len(items) - 1:
                lay.addWidget(self._arrow("arrow_down"))
        return lay

    def _section3(self) -> QFrame:
        lines = [
            "공유기 포트포워딩이나 방화벽 개방이 필요 없습니다. cloudflared가 바깥으로 "
            "나가는 연결만 만들기 때문입니다.",
            "HTTPS 인증서는 Cloudflare가 자동으로 처리합니다. 로컬 서버는 http로 두어도 "
            "됩니다.",
            "웹서버는 127.0.0.1에만 바인딩하는 것이 안전합니다. 예: uvicorn --host "
            "127.0.0.1",
            "이 앱을 종료해도 터널과 서버는 대상 머신에서 계속 실행됩니다. 다시 실행하면 "
            "그 상태를 그대로 이어서 보여줍니다. 24시간 운영하려면 라즈베리파이에 올리고 "
            "SSH 원격 모드로 관리하는 것을 권합니다 - 노트북을 꺼도 되기 때문입니다.",
            "터널을 켜는 순간 주소를 아는 누구나 접속할 수 있습니다. 개인용 페이지라면 "
            "Cloudflare Access로 접근 제한을 거는 것을 권합니다.",
        ]
        frame = QFrame()
        frame.setObjectName("card")
        lay = QVBoxLayout(frame)
        for line in lines:
            lbl = QLabel(f"· {line}")
            lbl.setWordWrap(True)
            lay.addWidget(lbl)
        return frame

    def _step_box(self, num: int, title: str, desc: str,
                  highlight: bool = False) -> QFrame:
        frame = QFrame()
        frame.setObjectName("card")
        if highlight:
            frame.setStyleSheet(
                f"QFrame#card {{ background: {self.p['panel']}; "
                f"border: 2px solid {self.p['accent']}; border-radius: 10px; }}")

        badge = QLabel(str(num))
        badge.setFixedSize(26, 26)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background: {self.p['accent']}; color: {self.p['on_accent']}; "
            "border-radius: 13px; font-weight: 700;")

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-weight: 700;")
        title_lbl.setWordWrap(True)

        desc_lbl = QLabel(desc)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"color: {self.p['muted']};")

        top = QHBoxLayout()
        top.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        top.addWidget(title_lbl, 1)

        lay = QVBoxLayout(frame)
        lay.addLayout(top)
        lay.addWidget(desc_lbl)
        # 박스마다 내용 길이가 달라도 위쪽 정렬을 유지
        lay.addStretch(1)
        return frame

    def _arrow(self, direction: str) -> QLabel:
        lbl = QLabel()
        lbl.setPixmap(make_icon(direction, self.p["muted"], 24).pixmap(24, 24))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return lbl
