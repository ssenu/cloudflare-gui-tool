"""느린 조회를 GUI 스레드 밖에서 돌리기 위한 워커.

왜 필요한가: 상태 폴링 한 번(ProcessManager.refresh)이 SSH 대상에서 실측
100~500ms, 로그 한 탭을 읽는 것도 70ms 안팎이 걸린다. CPU가 아니라 원격 왕복
대기 시간이다. 이걸 1초(로그는 0.7초)마다 GUI 스레드에서 하면 그 시간 동안
창이 통째로 멈춘다 - 클릭도 스크롤도 밀린다. "가끔씩 버벅인다"의 정체였다.

규칙은 단순하게 둔다:

- **Qt의 스레드 풀을 쓴다.** 예전에는 폴링 한 번마다 QThread를 만들고
  버렸는데, 창 하나에 초당 두세 개씩 스레드를 생성·파괴하는 셈이라 낭비였다.
  풀은 스레드를 재사용하고, 우리가 수명을 관리할 QThread 객체도 없다
  (창이 먼저 사라져도 파괴 중인 스레드를 기다리는 문제가 생기지 않는다).
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

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal


class _TaskSignals(QObject):
    """작업이 스스로 들고 있는 시그널 객체.

    수신자(BackgroundPoller)가 먼저 파괴되면 Qt가 연결을 자동으로 끊으므로,
    창이 닫힌 뒤 결과가 도착해도 안전하다.
    """

    done = pyqtSignal(bool, object, str)


class _Task(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = _TaskSignals()

    def run(self):
        try:
            result = self.fn()
        except Exception as ex:  # 연결 끊김 등 - 호출측이 배너/백오프 처리
            self.signals.done.emit(False, None, str(ex))
        else:
            self.signals.done.emit(True, result, "")


class BackgroundPoller(QObject):
    """함수 하나를 워커 스레드에서 한 번씩 돌린다.

    finished(ok, result, error)가 GUI 스레드에서 발생한다.
    """

    finished = pyqtSignal(bool, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = False
        self._task: _Task | None = None

    def is_busy(self) -> bool:
        return self._busy

    def run(self, fn: Callable[[], object]) -> bool:
        """작업을 시작한다. 이미 돌고 있으면 False를 반환하고 아무것도 안 한다."""
        if self._busy:
            return False
        task = _Task(fn)
        # 바운드 메서드로 연결한다(람다 금지) - 수신자가 파괴되면 Qt가 연결을
        # 자동으로 끊어, 창이 닫힌 뒤 결과가 도착해도 죽지 않는다.
        task.signals.done.connect(self._on_done)
        self._busy = True
        self._task = task
        QThreadPool.globalInstance().start(task)
        return True

    def _on_done(self, ok: bool, result, error: str):
        self._busy = False
        self._task = None
        self.finished.emit(ok, result, error)

    def stop(self):
        """창을 닫을 때 호출한다. 진행 중인 작업이 끝나기를 기다린다."""
        self._busy = False
        self._task = None
        QThreadPool.globalInstance().waitForDone(3000)
