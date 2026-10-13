"""Qt 테스트 공용 헬퍼."""
from __future__ import annotations

import time

from PyQt6.QtWidgets import QApplication


def refresh_now(win, fetch: bool = True, timeout: float = 3.0) -> None:
    """refresh()를 부르고 워커가 결과를 그릴 때까지 이벤트 루프를 돌린다.

    refresh(fetch=True)는 터널 목록을 워커에서 받아 오므로 호출 직후에는
    카드가 아직 옛것이다. 테스트는 동기처럼 쓰고 싶으니 여기서 기다린다.
    """
    win.refresh(fetch=fetch)
    wait_refresh(win, timeout)


def wait_refresh(win, timeout: float = 3.0) -> None:
    """이미 시작된 refresh 워커가 끝나 화면에 반영될 때까지 기다린다."""
    end = time.monotonic() + timeout
    while win._refresh_poller.is_busy() and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)
    QApplication.processEvents()


def save_now(dlg, timeout: float = 3.0) -> None:
    """다이얼로그의 저장을 누르고, 워커 단계들이 다 끝날 때까지 기다린다."""
    dlg._on_save()
    end = time.monotonic() + timeout
    while (dlg._saving or dlg._poller.is_busy()) and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)
    QApplication.processEvents()
