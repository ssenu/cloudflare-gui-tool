"""느린 조회를 GUI 스레드 밖에서 돌리기 위한 워커.

왜 필요한가: 상태 폴링 한 번(ProcessManager.refresh)이 SSH 대상에서 실측
100~500ms, 로그 한 탭을 읽는 것도 70ms 안팎이 걸린다. CPU가 아니라 원격 왕복
대기 시간이다. 이걸 1초(로그는 0.2초)마다 GUI 스레드에서 하면 그 시간 동안
창이 통째로 멈춘다 - 클릭도 스크롤도 밀린다. "가끔씩 버벅인다"의 정체였다.

규칙은 단순하게 둔다:

- 한 번에 하나만 돈다. 이전 작업이 끝나지 않았으면 이번 차례는 건너뛴다
  (큐에 쌓아 두면 연결이 느려질수록 밀린 작업이 끝없이 이어진다).
- 워커는 넘겨받은 함수만 호출한다. 위젯은 절대 건드리지 않고, 결과 반영은
  finished 시그널을 받은 GUI 스레드가 한다.
- 러너를 두 스레드가 동시에 쓰지 않도록 SshRunner 쪽에 락이 있다. 사용자
  조작(토글, 다이얼로그)은 GUI 스레드에서 그대로 동기 실행되며, 워커가
  진행 중이면 그 한 번이 끝날 때까지만 기다린다.
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QObject, QThread, pyqtSignal


class _Task(QObject):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable[[], object]):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
        except Exception as ex:  # 연결 끊김 등 - 호출측이 배너/백오프 처리
            self.failed.emit(str(ex))
        else:
            self.done.emit(result)


class BackgroundPoller(QObject):
    """함수 하나를 워커 스레드에서 한 번씩 돌린다.

    finished(ok, result, error)가 GUI 스레드에서 발생한다.
    """

    finished = pyqtSignal(bool, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: QThread | None = None
        self._task: _Task | None = None

    def is_busy(self) -> bool:
        return self._thread is not None

    def run(self, fn: Callable[[], object]) -> bool:
        """작업을 시작한다. 이미 돌고 있으면 False를 반환하고 아무것도 안 한다."""
        if self._thread is not None:
            return False

        thread = QThread()
        task = _Task(fn)
        task.moveToThread(thread)
        thread.started.connect(task.run)
        # 반드시 self(GUI 스레드에 속한 QObject)의 바운드 메서드로 연결한다.
        # 람다로 연결하면 수신자가 없어 워커 스레드에서 직접 실행되고, 그
        # 안에서 자기 스레드를 quit()/wait()하다 멈춰버린다.
        task.done.connect(self._on_ok)
        task.failed.connect(self._on_fail)
        self._thread = thread
        self._task = task
        thread.start()
        return True

    def _on_ok(self, result):
        self._finish(True, result, "")

    def _on_fail(self, error: str):
        self._finish(False, None, error)

    def _finish(self, ok: bool, result, error: str):
        thread, self._thread = self._thread, None
        self._task = None
        if thread is not None:
            thread.quit()
            thread.wait(2000)
            thread.deleteLater()
        self.finished.emit(ok, result, error)

    def stop(self):
        """창을 닫을 때 호출한다. 진행 중인 작업이 끝날 때까지만 기다린다."""
        thread, self._thread = self._thread, None
        self._task = None
        if thread is not None:
            thread.quit()
            thread.wait(3000)
