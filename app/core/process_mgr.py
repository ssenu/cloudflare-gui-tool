from __future__ import annotations

import shlex
import threading
from collections import deque
from enum import Enum, auto

from app.core.cloudflared import CloudflaredClient
from app.core.runner import CommandRunner, ManagedProcess
from app.core.store import TunnelMeta

RUNNING_MARKER = "Registered tunnel connection"


class TunnelState(Enum):
    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    ERROR = auto()


class StatusTracker:
    def __init__(self):
        self.state = TunnelState.STOPPED
        self._stopping = False

    def mark_starting(self):
        self.state = TunnelState.STARTING
        self._stopping = False

    def mark_stopping(self):
        self._stopping = True

    def feed(self, line: str):
        if RUNNING_MARKER in line:
            self.state = TunnelState.RUNNING

    def on_exit(self, code: int):
        if self._stopping or code == 0:
            self.state = TunnelState.STOPPED
        else:
            self.state = TunnelState.ERROR


class LogBuffer:
    def __init__(self, maxlen: int = 2000):
        self._lines: deque[tuple[int, str]] = deque(maxlen=maxlen)
        self._seq = 0
        self._lock = threading.Lock()

    def append(self, stream: str, line: str):
        with self._lock:
            self._seq += 1
            self._lines.append((self._seq, line))

    def get_since(self, seq: int) -> tuple[int, list[str]]:
        with self._lock:
            new = [l for s, l in self._lines if s > seq]
            return self._seq, new

    def clear(self):
        with self._lock:
            self._lines.clear()


class _Handle:
    def __init__(self, proc: ManagedProcess, tracker: StatusTracker, log: LogBuffer):
        self.proc = proc
        self.tracker = tracker
        self.log = log


class ProcessManager:
    def __init__(self):
        self._tunnels: dict[str, _Handle] = {}
        self._servers: dict[str, _Handle] = {}
        self._log_cache: dict[str, LogBuffer] = {}

    # ---- 터널 ----
    def start_tunnel(self, name: str, runner: CommandRunner,
                     client: CloudflaredClient) -> None:
        if name in self._tunnels and self._tunnels[name].proc.is_running():
            return
        tracker = StatusTracker()
        log = self.tunnel_log(name)
        tracker.mark_starting()

        def on_line(stream, line):
            log.append(stream, line)
            tracker.feed(line)

        proc = runner.spawn(client.run_args(name), on_line=on_line,
                            on_exit=tracker.on_exit)
        self._tunnels[name] = _Handle(proc, tracker, log)

    def stop_tunnel(self, name: str) -> None:
        h = self._tunnels.get(name)
        if h:
            h.tracker.mark_stopping()
            h.proc.stop()

    def tunnel_state(self, name: str) -> TunnelState:
        h = self._tunnels.get(name)
        if not h:
            return TunnelState.STOPPED
        if h.tracker.state == TunnelState.RUNNING and not h.proc.is_running():
            return TunnelState.ERROR
        return h.tracker.state

    def tunnel_log(self, name: str) -> LogBuffer:
        h = self._tunnels.get(name)
        return h.log if h else self._make_log("_t_" + name)

    # ---- 웹서버 ----
    def start_server(self, meta: TunnelMeta, runner: CommandRunner) -> None:
        name = meta.name
        if name in self._servers and self._servers[name].proc.is_running():
            return
        log = self.server_log(name)
        tracker = StatusTracker()
        cmd = shlex.split(meta.server_cmd, posix=False)
        # posix=False: Windows 경로 역슬래시 보존. 따옴표는 벗겨준다.
        cmd = [c.strip('"') for c in cmd]
        proc = runner.spawn(cmd, cwd=meta.server_cwd or None,
                            on_line=lambda s, l: log.append(s, l),
                            on_exit=tracker.on_exit)
        self._servers[name] = _Handle(proc, tracker, log)

    def stop_server(self, name: str) -> None:
        h = self._servers.get(name)
        if h:
            h.tracker.mark_stopping()
            h.proc.stop()

    def server_running(self, name: str) -> bool:
        h = self._servers.get(name)
        return bool(h and h.proc.is_running())

    def server_log(self, name: str) -> LogBuffer:
        h = self._servers.get(name)
        return h.log if h else self._make_log("_s_" + name)

    # ---- 공통 ----
    def _make_log(self, key: str) -> LogBuffer:
        # start 전에 로그 객체를 요청해도 같은 인스턴스를 돌려주기 위한 캐시
        if key not in self._log_cache:
            self._log_cache[key] = LogBuffer()
        return self._log_cache[key]

    def stop_all(self) -> None:
        for name in list(self._tunnels):
            self.stop_tunnel(name)
        for name in list(self._servers):
            self.stop_server(name)

    def any_running(self) -> bool:
        return (any(h.proc.is_running() for h in self._tunnels.values())
                or any(h.proc.is_running() for h in self._servers.values()))
