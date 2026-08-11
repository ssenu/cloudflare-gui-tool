from __future__ import annotations

import sys
import traceback

from PyQt6.QtWidgets import QApplication

from app.context import AppContext
from app.ui.theme import build_qss, ensure_qss_icons


def _install_excepthook() -> None:
    """C1의 마지막 그물: 어떤 미처리 예외도 프로세스를 죽이지 않게 한다.

    PyQt6는 Qt 슬롯 안에서 발생한 미처리 예외를 잡지 못하면 프로세스를
    abort시킨다(실증됨). 각 슬롯을 개별적으로 try/except로 감싸는 것이
    1차 방어선이고, 이건 그마저 놓친 경우를 위한 2차 방어선이다 - 앱을
    강제 종료 대신 stderr에 남기고 계속 실행되게 한다.
    """
    def _hook(exc_type, exc_value, exc_tb):
        traceback.print_exception(exc_type, exc_value, exc_tb, file=sys.stderr)

    sys.excepthook = _hook


def main() -> int:
    _install_excepthook()
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
