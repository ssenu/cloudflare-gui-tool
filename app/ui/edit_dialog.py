from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
                             QFormLayout, QLineEdit, QMessageBox, QPushButton)

from app.context import AppContext
from app.core.config_yml import update_service
from app.core.store import TunnelMeta
from app.core.wizard_logic import validate_service


class EditTunnelDialog(QDialog):
    def __init__(self, ctx: AppContext, meta: TunnelMeta, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.meta = meta
        self.setWindowTitle(f"편집 — {meta.name}")
        self.setMinimumWidth(440)

        self.service_edit = QLineEdit(meta.service)
        self.cmd_edit = QLineEdit(meta.server_cmd)
        self.cwd_edit = QLineEdit(meta.server_cwd)
        browse = QPushButton("폴더 선택...")
        browse.clicked.connect(lambda: self.cwd_edit.setText(
            QFileDialog.getExistingDirectory(self, "작업 폴더") or self.cwd_edit.text()))
        self.together_chk = QCheckBox()
        self.together_chk.setChecked(meta.start_together)

        form = QFormLayout(self)
        form.addRow("서비스 주소", self.service_edit)
        form.addRow("서버 명령", self.cmd_edit)
        form.addRow("작업 폴더", self.cwd_edit)
        form.addRow("", browse)
        form.addRow("터널과 함께 시작", self.together_chk)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _save(self):
        service = self.service_edit.text().strip()
        err = validate_service(service)
        if err:
            QMessageBox.warning(self, "입력 오류", err)
            return
        if service != self.meta.service:
            path = self.ctx.client.config_path(self.meta.name)
            if self.ctx.runner.file_exists(path):
                text = self.ctx.runner.read_file(path)
                self.ctx.runner.write_file(path, update_service(text, service))
        self.meta.service = service
        self.meta.server_cmd = self.cmd_edit.text().strip()
        self.meta.server_cwd = self.cwd_edit.text().strip()
        self.meta.start_together = self.together_chk.isChecked()
        self.accept()
