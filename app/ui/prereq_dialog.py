"""원격 대상 준비물 점검 화면.

SSH 대상에 붙을 때마다 cloudflared·git·docker를 확인해 보여준다. 셋 다
정상이면 "다음부터 보지 않기"를 체크해 그 기기의 점검을 끌 수 있다 - 체크는
정상일 때만 가능해서, 새 기기는 반드시 한 번은 점검을 거친다.

점검 자체(원격 명령 3~5회)는 워커 스레드에서 돌린다. GUI에서 직접 부르면
연결이 느릴 때 창이 그만큼 멈춘다.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFrame,
                             QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from app.context import AppContext
from app.core.prereq import PrereqResult, all_ok, check_prereqs, summary
from app.ui.poller import BackgroundPoller
from app.ui.theme import current_palette
from app.ui.winutil import apply_titlebar_theme

OK_COLOR = "#3fb950"
FAIL_COLOR = "#f85149"
WARN_COLOR = "#d29922"


class _ItemRow(QFrame):
    """준비물 하나: 상태 점 + 이름 + 설명, 없으면 설치 명령까지."""

    def __init__(self, result: PrereqResult, palette: dict):
        super().__init__()
        self.setObjectName("card")
        color = OK_COLOR if result.ok else FAIL_COLOR
        if result.ok and result.warning:
            color = WARN_COLOR

        dot = QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background: {color}; border-radius: 5px;")

        name = QLabel(result.label)
        name.setStyleSheet("font-weight: 700;")

        state = QLabel("설치됨" if result.ok else "없음")
        state.setStyleSheet(f"color: {color}; font-size: 12px;")

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(dot)
        head.addWidget(name)
        head.addWidget(state)
        head.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)
        lay.addLayout(head)

        detail = result.detail if result.ok else result.why
        if detail:
            lbl = QLabel(detail)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"color: {palette['muted']}; font-size: 11px;")
            lay.addWidget(lbl)

        if result.warning:
            warn = QLabel(f"! {result.warning}")
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color: {WARN_COLOR}; font-size: 11px;")
            lay.addWidget(warn)

        if not result.ok and result.install:
            box = QPlainTextEdit("\n".join(result.install))
            box.setReadOnly(True)
            box.setFixedHeight(22 * len(result.install) + 16)
            lay.addWidget(QLabel("대상 기기에서 실행:"))
            lay.addWidget(box)


class PrereqDialog(QDialog):
    # 모달은 부모의 90%까지만 커진다. 결과가 워커에서 늦게 도착하므로 크기를
    # 내용에 맞춰 계산하면 "도착 전"의 빈 높이로 굳는다. 처음부터 넉넉한
    # 크기로 잡고 내용은 스크롤로 흡수한다(안내 창과 같은 방식).
    PREFERRED_WIDTH = 540
    PREFERRED_HEIGHT = 520

    def __init__(self, ctx: AppContext, target_label: str, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.target_key = ctx.runner.name
        self.results: list[PrereqResult] = []
        self.setWindowTitle("대상 점검")
        self.resize(self.PREFERRED_WIDTH, self.PREFERRED_HEIGHT)

        self.palette_ = current_palette(ctx.store.settings.theme)

        title = QLabel(f"{target_label} 점검")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.summary_label = QLabel("확인 중...")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(f"color: {self.palette_['muted']};")

        self.items_box = QVBoxLayout()
        self.items_box.setSpacing(6)
        self.items_box.setContentsMargins(0, 0, 8, 0)  # 세로 스크롤바 자리
        items = QWidget()
        items.setLayout(self.items_box)
        self._items = items
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(items)

        self.skip_chk = QCheckBox("다음부터 이 기기는 점검하지 않기")
        self.skip_chk.setEnabled(False)
        self.skip_chk.setToolTip("셋 다 정상일 때만 끌 수 있습니다")

        self.recheck_btn = QPushButton("다시 검사")
        self.recheck_btn.clicked.connect(self.start_check)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("확인")
        buttons.rejected.connect(self.reject)

        foot = QHBoxLayout()
        foot.addWidget(self.skip_chk)
        foot.addStretch(1)
        foot.addWidget(self.recheck_btn)
        foot.addWidget(buttons)

        root = QVBoxLayout(self)
        root.addWidget(title)
        root.addWidget(self.summary_label)
        root.addWidget(scroll, 1)
        root.addLayout(foot)

        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

        self._poller = BackgroundPoller(self)
        self._poller.finished.connect(self._on_checked)
        self.start_check()

    # ---- 점검 ----
    def start_check(self):
        self.recheck_btn.setEnabled(False)
        self.summary_label.setText("확인 중...")
        binary = self.ctx._binary()
        runner = self.ctx.runner
        self._poller.run(lambda: check_prereqs(runner, binary))

    def _on_checked(self, ok: bool, result, error: str):
        self.recheck_btn.setEnabled(True)
        if not ok:
            self.summary_label.setText(f"점검에 실패했습니다: {error}")
            return
        self.results = result
        self._render()

    def _render(self):
        while self.items_box.count():
            item = self.items_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        for res in self.results:
            self.items_box.addWidget(_ItemRow(res, self.palette_))
        self.summary_label.setText(summary(self.results))
        # 체크박스는 "셋 다 정상"일 때만 켤 수 있다 - 문제가 있는 기기를
        # 다시는 확인하지 않게 만들면 원인 모를 실패로 되돌아간다.
        self.skip_chk.setEnabled(all_ok(self.results))
        if not all_ok(self.results):
            self.skip_chk.setChecked(False)

    def fit_to_content(self) -> None:
        """ModalOverlay가 표시 직후 부르는 훅.

        내용에 맞춰 재는 대신 처음부터 넉넉한 크기로 편다 - 점검 결과는
        워커에서 나중에 도착해서, 그때 크기를 재면 빈 화면 높이로 굳는다.
        """
        parent = self.parentWidget()
        if parent is None:
            self.resize(self.PREFERRED_WIDTH, self.PREFERRED_HEIGHT)
            return
        area = parent.rect()
        self.resize(min(self.PREFERRED_WIDTH, int(area.width() * 0.9)),
                    min(self.PREFERRED_HEIGHT, int(area.height() * 0.9)))

    # ---- 닫기 ----
    def _persist_skip(self):
        skip = self.skip_chk.isChecked() and all_ok(self.results)
        settings = self.ctx.store.settings
        if settings.skip_prereq_check(self.target_key) != skip:
            settings.set_skip_prereq_check(self.target_key, skip)
            self.ctx.store.save()

    def closeEvent(self, event):
        self._persist_skip()
        self._poller.stop()
        super().closeEvent(event)

    def reject(self):
        self._persist_skip()
        self._poller.stop()
        super().reject()

    def accept(self):
        self._persist_skip()
        self._poller.stop()
        super().accept()
