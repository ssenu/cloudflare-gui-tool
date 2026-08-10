from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication, QMainWindow

from app.context import AppContext
from app.ui.theme import APP_QSS


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Cloudflare Tunnel GUI")
    app.setStyleSheet(APP_QSS)
    ctx = AppContext()
    from app.ui.onboarding import OnboardingDialog, needs_onboarding
    if needs_onboarding(ctx):
        dlg = OnboardingDialog(ctx)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return 0
    win = QMainWindow()  # Task 10에서 MainWindow(ctx)로 교체
    win.setWindowTitle("Cloudflare Tunnel GUI")
    win.resize(760, 560)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
