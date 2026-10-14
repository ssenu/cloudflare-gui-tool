from __future__ import annotations

from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialogButtonBox, QFormLayout,
                             QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                             QPushButton, QVBoxLayout, QDialog)

from app.context import AppContext
from app.core.git_repo import validate_repo_root
from app.version import __version__
from app.ui.winutil import apply_titlebar_theme

THEME_LABELS = {"dark": "다크", "light": "라이트"}
THEME_VALUES = {v: k for k, v in THEME_LABELS.items()}


class SettingsDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("설정")
        self.setMinimumSize(420, 200)

        form = QFormLayout()

        # cloudflared 경로 재정의
        self.cf_path_edit = QLineEdit(ctx.store.settings.cloudflared_path)
        self.cf_path_edit.setPlaceholderText("비우면 PATH에서 cloudflared 탐색")
        form.addRow("cloudflared 경로", self.cf_path_edit)

        # 프로젝트 클론 기본 위치
        self.repo_root_edit = QLineEdit(ctx.store.settings.repo_root)
        self.repo_root_edit.setPlaceholderText("예: /srv/apps")
        form.addRow("프로젝트 클론 위치", self.repo_root_edit)

        # 테마
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(THEME_LABELS.values()))
        current_label = THEME_LABELS.get(ctx.store.settings.theme, "다크")
        self.theme_combo.setCurrentText(current_label)
        form.addRow("테마", self.theme_combo)

        # 업데이트: 현재 버전 + 지금 확인 버튼 + 켤 때 자동 확인
        self.version_label = QLabel(f"v{__version__}")
        self.update_btn = QPushButton("업데이트 확인")
        self.update_btn.clicked.connect(self._check_update)
        ver_row = QHBoxLayout()
        ver_row.addWidget(self.version_label)
        ver_row.addStretch(1)
        ver_row.addWidget(self.update_btn)
        form.addRow("버전", ver_row)
        self.auto_update_chk = QCheckBox("켤 때 하루 한 번 새 버전 확인")
        self.auto_update_chk.setChecked(ctx.store.settings.auto_update_check)
        form.addRow("", self.auto_update_chk)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("확인")
        close.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(form)
        root.addStretch(1)
        root.addWidget(close)

        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

    def _persist(self):
        self.ctx.store.settings.cloudflared_path = self.cf_path_edit.text().strip()

        # I3: repo_root는 클론 목적지 조립과 "폴더까지 삭제"의 rm -rf 인자로
        # 그대로 쓰이므로 name과 같은 수준으로 엄격히 검증한다. 잘못된
        # 값이면 경고하고 이전 값을 유지한다(입력칸도 이전 값으로 되돌린다).
        new_root = self.repo_root_edit.text().strip()
        err = validate_repo_root(new_root)
        if err:
            QMessageBox.warning(self, "클론 위치 오류",
                                f"{err}\n이전 값을 그대로 유지합니다.")
            self.repo_root_edit.setText(self.ctx.store.settings.repo_root)
        else:
            self.ctx.store.settings.repo_root = new_root

        self.ctx.store.settings.theme = THEME_VALUES.get(
            self.theme_combo.currentText(), "dark")
        self.ctx.store.settings.auto_update_check = self.auto_update_chk.isChecked()
        self.ctx.store.save()

    def _check_update(self):
        """지금 바로 확인. 흐름은 메인 창이 들고 있다(설정 창이 닫혀도 이어지게)."""
        flow = getattr(self.parent(), "update_flow", None)
        if flow is None:
            from app.ui.update_flow import UpdateFlow
            flow = UpdateFlow(self, self.ctx.store)
        flow.check(manual=True)

    def closeEvent(self, event: QCloseEvent):
        self._persist()
        super().closeEvent(event)

    def reject(self):
        self._persist()
        super().reject()

    def accept(self):
        self._persist()
        super().accept()
