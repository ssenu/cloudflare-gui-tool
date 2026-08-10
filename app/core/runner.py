from __future__ import annotations

import os
import subprocess
import sys
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable

OnLine = Callable[[str, str], None]  # (stream_name: "stdout"|"stderr", line)
OnExit = Callable[[int], None]

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str


class ManagedProcess(ABC):
    @abstractmethod
    def is_running(self) -> bool: ...

    @abstractmethod
    def stop(self, timeout: float = 5.0) -> None: ...


class CommandRunner(ABC):
    name: str = "local"

    @abstractmethod
    def run(self, cmd: list[str], timeout: float = 60.0) -> RunResult: ...

    @abstractmethod
    def spawn(self, cmd: list[str], cwd: str | None = None,
              on_line: OnLine | None = None,
              on_exit: OnExit | None = None) -> ManagedProcess: ...

    @abstractmethod
    def read_file(self, path: str) -> str: ...

    @abstractmethod
    def write_file(self, path: str, text: str) -> None: ...

    @abstractmethod
    def file_exists(self, path: str) -> bool: ...

    @abstractmethod
    def remove_file(self, path: str) -> None: ...

    @abstractmethod
    def home_dir(self) -> str: ...


class LocalProcess(ManagedProcess):
    def __init__(self, popen: subprocess.Popen,
                 on_line: OnLine | None, on_exit: OnExit | None):
        self._p = popen
        self._on_exit = on_exit
        self._threads = []
        for stream_name in ("stdout", "stderr"):
            pipe = getattr(popen, stream_name)
            t = threading.Thread(target=self._pump, args=(stream_name, pipe, on_line),
                                 daemon=True)
            t.start()
            self._threads.append(t)
        threading.Thread(target=self._wait, daemon=True).start()

    def _pump(self, name: str, pipe, on_line: OnLine | None):
        for raw in iter(pipe.readline, ""):
            if on_line:
                on_line(name, raw.rstrip("\r\n"))
        pipe.close()

    def _wait(self):
        code = self._p.wait()
        for t in self._threads:
            t.join(timeout=2)
        if self._on_exit:
            self._on_exit(code)

    def is_running(self) -> bool:
        return self._p.poll() is None

    def stop(self, timeout: float = 5.0) -> None:
        if not self.is_running():
            return
        self._p.terminate()
        try:
            self._p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._p.kill()
            self._p.wait(timeout=timeout)


class LocalRunner(CommandRunner):
    name = "local"

    def run(self, cmd: list[str], timeout: float = 60.0) -> RunResult:
        res = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, creationflags=CREATE_NO_WINDOW)
        return RunResult(res.returncode, res.stdout, res.stderr)

    def spawn(self, cmd, cwd=None, on_line=None, on_exit=None) -> ManagedProcess:
        popen = subprocess.Popen(
            cmd, cwd=cwd or None, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=CREATE_NO_WINDOW)
        return LocalProcess(popen, on_line, on_exit)

    def read_file(self, path: str) -> str:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            return f.read()

    def write_file(self, path: str, text: str) -> None:
        p = os.path.expanduser(path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def file_exists(self, path: str) -> bool:
        return os.path.exists(os.path.expanduser(path))

    def remove_file(self, path: str) -> None:
        os.remove(os.path.expanduser(path))

    def home_dir(self) -> str:
        return os.path.expanduser("~")
