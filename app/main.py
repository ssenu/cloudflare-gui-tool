from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from app.context import AppContext
from app.ui.theme import build_qss, ensure_qss_icons


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Cloudflare Tunnel GUI")
    ctx = AppContext()
    app.setStyleSheet(build_qss(ctx.store.settings.theme, ensure_qss_icons(ctx.store.settings.theme)))
    from app.ui.onboarding import OnboardingDialog, needs_onboarding
    if needs_onboarding(ctx):
        dlg = OnboardingDialog(ctx)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return 0
    from app.ui.main_window import MainWindow
    win = MainWindow(ctx)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
