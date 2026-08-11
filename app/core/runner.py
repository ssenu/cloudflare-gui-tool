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

# spawn_detached용 Windows 프로세스 생성 플래그
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


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
    def run(self, cmd: list[str], timeout: float = 60.0,
           cwd: str | None = None) -> RunResult: ...

    @abstractmethod
    def spawn(self, cmd: list[str], cwd: str | None = None,
              on_line: OnLine | None = None,
              on_exit: OnExit | None = None) -> ManagedProcess: ...

    @abstractmethod
    def read_file(self, path: str) -> str: ...

    @abstractmethod
    def write_file(self, path: str, text: str) -> None: ...

    @abstractmethod
    def append_file(self, path: str, text: str) -> None:
        """path 끝에 text를 이어 붙인다 (없으면 새로 만든다).

        기존 내용을 읽지 않으므로 반복 호출(폴링 등)에서 read-all + write-all
        방식보다 원격 왕복과 전송량이 훨씬 작다.
        """
        ...

    @abstractmethod
    def file_exists(self, path: str) -> bool: ...

    @abstractmethod
    def remove_file(self, path: str) -> None: ...

    @abstractmethod
    def home_dir(self) -> str: ...

    @abstractmethod
    def spawn_detached(self, cmd: list[str], cwd: str | None, log_path: str) -> int:
        """프로세스를 GUI와 독립적으로 실행하고 PID를 반환한다.

        stdout/stderr은 log_path에 append 모드로 리다이렉트된다.
        생성된 프로세스는 GUI 종료·연결 끊김과 무관하게 계속 실행된다.
        """
        ...

    @abstractmethod
    def pids_alive(self, pids: list[int], timeout: float = 60.0) -> set[int]:
        """주어진 PID 중 실제로 살아있는 것들을 일괄 조회한다."""
        ...

    @abstractmethod
    def pid_cmdlines(self, pids: list[int]) -> dict[int, str] | None:
        """주어진 PID들의 명령/이미지 이름을 일괄 조회한다 (PID 재사용 검증용).

        조회 자체가 실패하면(원격 연결 오류, ps/tasklist 실행 실패, 종료
        코드 비정상 등) ``None``을 반환한다 - 이는 "PID가 없다"는 것과
        구분되는 "모름" 상태다. 호출자는 None을 받으면 명령 대조를
        건너뛰고 PID 생존 여부만으로 판정해야 한다(fail-open) - 절대
        조회 실패를 "명령이 다르다"로 해석해 PID 파일을 지우면 안 된다.

        조회는 성공했지만 개별 PID가 응답에 없는 경우 그 PID는 결과
        dict에 없다. 로컬은 이미지 이름(taskkill의 Image Name 등),
        원격은 ``comm``(짧은 명령 이름)을 값으로 담는다.
        """
        ...

    @abstractmethod
    def kill_pid(self, pid: int) -> None:
        """PID로 프로세스(및 자식/그룹)를 종료한다."""
        ...

    @abstractmethod
    def tail_file(self, path: str, offset: int) -> tuple[int, str]:
        """offset 바이트부터 파일을 읽어 (새 offset, 텍스트)를 반환한다.

        파일이 없으면 (0, "")를 반환한다.
        offset이 파일 크기보다 크면(로그 로테이트됨) 처음부터 다시 읽는다.
        """
        ...

    @abstractmethod
    def file_size(self, path: str) -> int:
        """파일 크기를 바이트 단위로 반환한다. 파일이 없으면 0."""
        ...

    @abstractmethod
    def ensure_dir(self, path: str) -> None: ...

    @abstractmethod
    def list_dir(self, path: str) -> list[str]:
        """디렉터리 안의 파일명 목록을 반환한다. 없으면 빈 리스트."""
        ...


def decode_tail(data: bytes) -> tuple[str, int]:
    """말미에 잘린 UTF-8 시퀀스는 남겨두고 (텍스트, 소비한 바이트 수)를 반환한다.

    다음 tail_file 호출에서 이어서 디코드할 수 있도록, 멀티바이트 문자
    중간에서 끊긴 꼬리 바이트는 소비하지 않고 offset에 남겨둔다.
    """
    for back in range(0, min(3, len(data)) + 1):
        chunk = data[:len(data) - back] if back else data
        try:
            return chunk.decode("utf-8"), len(chunk)
        except UnicodeDecodeError:
            continue
    # 중간에 깨진 바이트열이면 되돌릴 수 없으므로 치환하며 전부 소비
    return data.decode("utf-8", errors="replace"), len(data)


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

    def run(self, cmd: list[str], timeout: float = 60.0,
           cwd: str | None = None) -> RunResult:
        res = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, creationflags=CREATE_NO_WINDOW,
            cwd=cwd or None)
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

    def append_file(self, path: str, text: str) -> None:
        p = os.path.expanduser(path)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(text)

    def file_exists(self, path: str) -> bool:
        return os.path.exists(os.path.expanduser(path))

    def remove_file(self, path: str) -> None:
        os.remove(os.path.expanduser(path))

    def home_dir(self) -> str:
        return os.path.expanduser("~")

    def spawn_detached(self, cmd: list[str], cwd: str | None, log_path: str) -> int:
        log_path = os.path.expanduser(log_path)
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        logfile = open(log_path, "ab")
        try:
            if sys.platform == "win32":
                popen = subprocess.Popen(
                    cmd, cwd=cwd or None, stdout=logfile, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                    close_fds=True)
            else:
                popen = subprocess.Popen(
                    cmd, cwd=cwd or None, stdout=logfile, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, start_new_session=True,
                    close_fds=True)
        finally:
            logfile.close()
        return popen.pid

    def pids_alive(self, pids: list[int], timeout: float = 60.0) -> set[int]:
        # 로컬 생존 확인은 시스템 콜 하나라 timeout이 의미 없다 - 인터페이스
        # 통일을 위해 매개변수만 받고 무시한다.
        if not pids:
            return set()
        if sys.platform == "win32":
            return {pid for pid in pids if self._win_pid_alive(pid)}
        alive = set()
        for pid in pids:
            try:
                os.kill(pid, 0)
                alive.add(pid)
            except (OSError, ProcessLookupError, PermissionError):
                # PermissionError는 접근 권한이 없을 뿐 살아는 있는 경우지만,
                # 우리가 띄운 프로세스라면 발생하지 않으므로 죽은 것으로 간주
                pass
        return alive

    def pid_cmdlines(self, pids: list[int]) -> dict[int, str] | None:
        if not pids:
            return {}
        result: dict[int, str] = {}
        wanted = set(pids)
        if sys.platform == "win32":
            try:
                res = subprocess.run(
                    ["tasklist", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=5.0, creationflags=CREATE_NO_WINDOW)
            except (subprocess.SubprocessError, OSError):
                return None
            if res.returncode != 0:
                return None
            for line in res.stdout.splitlines():
                parts = [p.strip('"') for p in line.split('","')]
                if len(parts) < 2:
                    continue
                image, pid_str = parts[0], parts[1]
                try:
                    pid = int(pid_str)
                except ValueError:
                    continue
                if pid in wanted:
                    result[pid] = image
        else:
            pid_arg = ",".join(str(p) for p in pids)
            try:
                res = subprocess.run(
                    ["ps", "-p", pid_arg, "-o", "pid=,comm="],
                    capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=5.0)
            except (subprocess.SubprocessError, OSError):
                return None
            if res.returncode != 0:
                # busybox 등 -p/-o를 지원하지 않는 ps는 보통 여기서 걸린다 -
                # 대조 불가능(모름)으로 취급한다.
                return None
            for line in res.stdout.splitlines():
                bits = line.strip().split(None, 1)
                if len(bits) != 2:
                    continue
                try:
                    pid = int(bits[0])
                except ValueError:
                    continue
                if pid in wanted:
                    result[pid] = bits[1]
        return result

    @staticmethod
    def _win_pid_alive(pid: int) -> bool:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong(0)
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            # 참고: 프로세스가 실제로 종료 코드 259(STILL_ACTIVE)로 정상 종료한
            # 드문 경우 살아있다고 오판할 수 있다. 알려진 한계로 수용한다.
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    def kill_pid(self, pid: int) -> None:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, creationflags=CREATE_NO_WINDOW)
        else:
            import signal
            # spawn_detached가 start_new_session=True로 새 프로세스 그룹을
            # 만들었으므로 그룹 전체에 먼저 시도하고, 실패하면 단일 PID로 폴백
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError:
                try:
                    os.kill(pid, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass

    def tail_file(self, path: str, offset: int) -> tuple[int, str]:
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            return 0, ""
        size = os.path.getsize(path)
        if offset > size:
            offset = 0  # 로그가 로테이트/축소된 경우 처음부터 다시 읽는다
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
        text, consumed = decode_tail(data)
        return offset + consumed, text

    def file_size(self, path: str) -> int:
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            return 0
        return os.path.getsize(path)

    def ensure_dir(self, path: str) -> None:
        os.makedirs(os.path.expanduser(path), exist_ok=True)

    def list_dir(self, path: str) -> list[str]:
        path = os.path.expanduser(path)
        if not os.path.isdir(path):
            return []
        return os.listdir(path)
