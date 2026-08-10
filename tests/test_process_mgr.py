import sys
import time
from app.core.process_mgr import (LogBuffer, ProcessManager, StatusTracker,
                                  TunnelState)
from app.core.runner import LocalRunner
from app.core.store import TunnelMeta


def test_tracker_transitions_to_running():
    t = StatusTracker()
    assert t.state == TunnelState.STOPPED
    t.mark_starting()
    assert t.state == TunnelState.STARTING
    t.feed("2026-08-10T00:00:00Z INF Registered tunnel connection connIndex=0")
    assert t.state == TunnelState.RUNNING


def test_tracker_user_stop_vs_crash():
    t = StatusTracker()
    t.mark_starting()
    t.mark_stopping()
    t.on_exit(1)
    assert t.state == TunnelState.STOPPED  # 사용자가 중지한 경우
    t2 = StatusTracker()
    t2.mark_starting()
    t2.on_exit(1)
    assert t2.state == TunnelState.ERROR  # 비정상 종료


def test_log_buffer_incremental():
    b = LogBuffer(maxlen=10)
    b.append("stdout", "a")
    b.append("stderr", "b")
    seq, lines = b.get_since(0)
    assert lines == ["a", "b"]
    b.append("stdout", "c")
    seq2, lines2 = b.get_since(seq)
    assert lines2 == ["c"]


def test_server_start_stop():
    mgr = ProcessManager()
    runner = LocalRunner()
    meta = TunnelMeta(name="t1",
                      server_cmd=f'"{sys.executable}" -c "import time; time.sleep(60)"')
    mgr.start_server(meta, runner)
    assert mgr.server_running("t1")
    mgr.stop_server("t1")
    time.sleep(0.3)
    assert not mgr.server_running("t1")
    mgr.stop_all()
