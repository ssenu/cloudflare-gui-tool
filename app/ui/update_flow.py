"""업데이트 확인 · 내려받기 · 설치 실행의 화면 쪽 흐름.

로직(버전 비교, 릴리스 조회, 내려받기)은 app/core/updater.py에 있다. 여기는
그것을 워커에서 돌리고 결과를 사용자에게 묻고 보여 주는 일만 한다. 네트워크는
전부 워커에서 돈다 - GitHub이 느려도 창이 굳지 않는다.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from app.core import updater
from app.ui.poller import BackgroundPoller
from app.version import __version__


class UpdateFlow(QObject):
    """업데이트 한 번의 흐름. 창(parent)에 붙어 산다.

    check(manual=True): 설정의 버튼. 결과를 항상 알려 준다(최신이어도).
    check(manual=False): 켤 때 자동 확인. 새 버전이 있을 때만 on_available을 부른다.
    """

    available = pyqtSignal(object)   # Release

    def __init__(self, parent, store, current: str = __version__,
                 kind: str | None = None, fetch=updater.fetch_latest):
        super().__init__(parent)
        self.parent_widget = parent
        self.store = store
        self.current = current
        self.kind = kind or updater.install_kind()
        self._fetch = fetch
        self._poller = BackgroundPoller(self)
        self._manual = False
        self._poller.finished.connect(self._on_checked)
        self.latest: updater.Release | None = None

    # ---- 확인 ----
    def check(self, manual: bool) -> None:
        if self._poller.is_busy():
            return
        self._manual = manual
        self._poller.run(self._fetch)

    def _on_checked(self, ok, release, error):
        self.store.settings.last_update_check = time.time()
        try:
            self.store.save()
        except OSError:
            pass
        if not ok:
            if self._manual:
                QMessageBox.warning(self.parent_widget, "업데이트 확인 실패",
                                    f"GitHub에 연결하지 못했습니다.\n{error}")
            return
        self.latest = release
        newer = updater.is_newer(release.version, self.current)
        if not newer:
            if self._manual:
                QMessageBox.information(
                    self.parent_widget, "업데이트",
                    f"최신 버전입니다 (v{self.current}).")
            return
        if self._manual:
            self.offer(release)
        else:
            self.available.emit(release)

    # ---- 제안 ----
    def offer(self, release: "updater.Release") -> None:
        """새 버전을 알려 주고, 설치본이면 내려받아 설치할지 묻는다."""
        notes = release.notes[:800] + ("…" if len(release.notes) > 800 else "")
        head = f"v{self.current}  →  v{release.version}"
        if self.kind != "installed" or not release.setup_url:
            # 무설치(zip)·개발 실행·설치본이 아직 안 올라온 릴리스: 앱이 덮어쓸
            # 위치를 정할 수 없으니 페이지만 열어 준다.
            why = {"portable": "무설치(zip) 버전이라 자동으로 설치할 수 없습니다.",
                   "source": "개발 실행 중이라 자동으로 설치하지 않습니다."}.get(
                       self.kind, "설치 파일이 아직 올라오지 않았습니다.")
            ok = QMessageBox.question(
                self.parent_widget, "새 버전이 있습니다",
                f"{head}\n\n{notes}\n\n{why}\n릴리스 페이지를 열까요?")
            if ok == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(QUrl(release.page_url))
            return
        size = f" ({release.setup_size // 1048576}MB)" if release.setup_size else ""
        ok = QMessageBox.question(
            self.parent_widget, "새 버전이 있습니다",
            f"{head}\n\n{notes}\n\n지금 내려받아 설치할까요?{size}\n"
            "설치하는 동안 앱이 잠깐 닫혔다가 다시 켜집니다. 터널과 서버는 "
            "대상 기기에서 계속 돌아갑니다.")
        if ok == QMessageBox.StandardButton.Yes:
            self.install(release)

    # ---- 내려받기 + 설치 ----
    def install(self, release: "updater.Release") -> None:
        dest = os.path.join(tempfile.gettempdir(),
                            f"CloudflareTunnelGUI-Setup-{release.version}.exe")
        dlg = QProgressDialog("설치 파일을 내려받는 중...", "취소", 0, 100,
                              self.parent_widget)
        dlg.setWindowTitle("업데이트")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setValue(0)
        self._cancelled = False
        dlg.canceled.connect(lambda: setattr(self, "_cancelled", True))
        self._progress = (0, release.setup_size)

        def work():
            def report(done, total):
                self._progress = (done, total or release.setup_size)
                if self._cancelled:
                    raise InterruptedError("취소됨")
            return updater.download(release.setup_url, dest, progress=report)

        dl = BackgroundPoller(self)
        self._dl = dl
        from PyQt6.QtCore import QTimer
        tick = QTimer(self)
        tick.timeout.connect(lambda: self._update_bar(dlg))
        tick.start(150)

        def done(ok, path, error):
            tick.stop()
            dlg.reset()
            if not ok:
                if not self._cancelled:
                    QMessageBox.warning(self.parent_widget, "업데이트 실패",
                                        f"설치 파일을 받지 못했습니다.\n{error}")
                return
            self.launch_installer(path)

        dl.finished.connect(done)
        dl.run(work)

    def _update_bar(self, dlg):
        done, total = self._progress
        if total:
            dlg.setValue(min(100, int(done * 100 / total)))

    def launch_installer(self, path: str) -> None:
        """설치본을 조용히 띄우고 앱을 닫는다. 설치가 끝나면 설치본이 앱을 다시 띄운다."""
        try:
            subprocess.Popen([path, *updater.SETUP_ARGS],
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        except OSError as ex:
            QMessageBox.warning(self.parent_widget, "업데이트 실패",
                                f"설치 프로그램을 실행하지 못했습니다.\n{ex}")
            return
        QApplication.instance().quit()
