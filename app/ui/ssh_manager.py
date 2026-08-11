from __future__ import annotations

from PyQt6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                             QHBoxLayout, QLineEdit, QListWidget, QMessageBox,
                             QPushButton, QSpinBox, QVBoxLayout)

from app.context import AppContext
from app.core.ssh_runner import SshRunner
from app.core.store import SshProfile
from app.ui.icons import make_icon
from app.ui.theme import current_palette
from app.ui.winutil import apply_titlebar_theme


class SshManagerDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("SSH 대상 관리")
        self.setMinimumSize(560, 420)

        palette = current_palette(ctx.store.settings.theme)
        icon_color = palette["text"]

        # SSH 프로필 목록
        self.listw = QListWidget()
        self.listw.currentRowChanged.connect(self._load_profile)
        add_btn = QPushButton("추가")
        add_btn.setIcon(make_icon("plus", icon_color))
        del_btn = QPushButton("삭제")
        del_btn.setIcon(make_icon("trash", icon_color))
        add_btn.clicked.connect(self._add)
        del_btn.clicked.connect(self._delete)

        # 프로필 폼
        self.name_edit = QLineEdit()
        self.host_edit = QLineEdit()
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(22)
        self.user_edit = QLineEdit("pi")
        self.key_edit = QLineEdit()
        key_btn = QPushButton("키 파일...")
        key_btn.setIcon(make_icon("folder", icon_color))
        key_btn.clicked.connect(lambda: self.key_edit.setText(
            QFileDialog.getOpenFileName(self, "SSH 키 파일")[0] or self.key_edit.text()))
        test_btn = QPushButton("연결 테스트")
        test_btn.clicked.connect(self._test)
        save_btn = QPushButton("프로필 저장")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save_profile)

        form = QFormLayout()
        form.addRow("이름", self.name_edit)
        form.addRow("호스트", self.host_edit)
        form.addRow("포트", self.port_spin)
        form.addRow("사용자", self.user_edit)
        form.addRow("키 파일", self.key_edit)
        form.addRow("", key_btn)
        row = QHBoxLayout()
        row.addWidget(test_btn)
        row.addWidget(save_btn)
        form.addRow(row)

        left = QVBoxLayout()
        left.addWidget(self.listw, 1)
        btns = QHBoxLayout()
        btns.addWidget(add_btn)
        btns.addWidget(del_btn)
        left.addLayout(btns)

        body = QHBoxLayout()
        body.addLayout(left, 1)
        body.addLayout(form, 2)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("닫기")
        close.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.addLayout(body, 1)
        root.addWidget(close)
        self._reload_list()

        apply_titlebar_theme(self, ctx.store.settings.theme == "dark")

    # ---- 목록 ----
    def _profiles(self) -> list[SshProfile]:
        return self.ctx.store.settings.ssh_profiles

    def _reload_list(self):
        self.listw.clear()
        for p in self._profiles():
            self.listw.addItem(f"{p.name} ({p.username}@{p.host}:{p.port})")

    def _load_profile(self, row: int):
        if 0 <= row < len(self._profiles()):
            p = self._profiles()[row]
            self.name_edit.setText(p.name)
            self.host_edit.setText(p.host)
            self.port_spin.setValue(p.port)
            self.user_edit.setText(p.username)
            self.key_edit.setText(p.key_path)

    def _current_form(self) -> SshProfile:
        return SshProfile(name=self.name_edit.text().strip(),
                          host=self.host_edit.text().strip(),
                          port=self.port_spin.value(),
                          username=self.user_edit.text().strip(),
                          key_path=self.key_edit.text().strip())

    def _add(self):
        self.listw.clearSelection()
        self.name_edit.clear()
        self.host_edit.clear()
        self.user_edit.setText("pi")
        self.key_edit.clear()

    def _save_profile(self):
        p = self._current_form()
        if not p.name or not p.host:
            QMessageBox.warning(self, "입력 오류", "이름과 호스트는 필수입니다")
            return
        profiles = self._profiles()
        for i, ex in enumerate(profiles):
            if ex.name == p.name:
                profiles[i] = p
                break
        else:
            profiles.append(p)
        self.ctx.store.save()
        self._reload_list()

    def _delete(self):
        row = self.listw.currentRow()
        if 0 <= row < len(self._profiles()):
            del self._profiles()[row]
            self.ctx.store.save()
            self._reload_list()
            self._add()

    def _test(self):
        try:
            r = SshRunner(self._current_form())
            r.connect()
            res = r.run(["echo", "ok"])
            r.close()
            if res.exit_code == 0:
                QMessageBox.information(self, "연결 테스트", "연결 성공")
            else:
                QMessageBox.warning(self, "연결 테스트", f"명령 실패: {res.stderr}")
        except Exception as ex:
            QMessageBox.critical(self, "연결 테스트", f"실패: {ex}")
