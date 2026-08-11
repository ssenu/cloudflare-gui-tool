from __future__ import annotations

import os

from app.core.runner import CommandRunner, RunResult


class FakeRunner(CommandRunner):
    """RunRegistry/ProcessManager 테스트용 가짜 러너.

    파일 시스템은 경로->내용 dict로, 살아있는 PID는 set으로 흉내 낸다.
    run()/pids_alive() 호출 횟수를 세어 "일괄 조회 1회" 같은 요구를 검증한다.
    """

    def __init__(self, home: str = "/home/fake"):
        self.name = "fake"
        self._home = home
        self.files: dict[str, str] = {}
        self.dirs: set[str] = set()
        self.live_pids: set[int] = set()
        self._next_pid = 1000
        self.pids_alive_calls = 0
        self.pid_cmdlines_calls = 0
        self.run_calls: list[tuple[tuple[str, ...], str | None]] = []
        # cmd 튜플 -> RunResult. 없으면 기본값(성공, 빈 출력)
        self.run_results: dict[tuple[str, ...], RunResult] = {}
        self.run_raises: dict[tuple[str, ...], Exception] = {}
        self.spawn_detached_calls: list[tuple] = []
        # pid -> 명령 이름(comm/이미지 이름). PID 재사용 검증 테스트용
        self.pid_cmdlines_map: dict[int, str] = {}
        # True면 pid_cmdlines()가 조회 실패(None)를 흉내낸다 - fail-open
        # 회귀 테스트용 (SSH 타임아웃, busybox ps -p/-o 미지원 등 재현)
        self.pid_cmdlines_fail: bool = False

    # ---- 명령 실행 ----
    def run(self, cmd, timeout: float = 60.0, cwd: str | None = None) -> RunResult:
        key = tuple(cmd)
        self.run_calls.append((key, cwd))
        if key in self.run_raises:
            raise self.run_raises[key]
        return self.run_results.get(key, RunResult(0, "", ""))

    def spawn(self, cmd, cwd=None, on_line=None, on_exit=None):
        raise NotImplementedError("이 테스트 러너는 spawn()을 쓰지 않는다")

    # ---- 파일 ----
    def read_file(self, path: str) -> str:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def write_file(self, path: str, text: str) -> None:
        self.files[path] = text

    def append_file(self, path: str, text: str) -> None:
        self.files[path] = self.files.get(path, "") + text

    def file_exists(self, path: str) -> bool:
        return path in self.files

    def remove_file(self, path: str) -> None:
        del self.files[path]

    def home_dir(self) -> str:
        return self._home

    # ---- 프로세스 ----
    def spawn_detached(self, cmd, cwd, log_path: str) -> int:
        pid = self._next_pid
        self._next_pid += 1
        self.live_pids.add(pid)
        self.files.setdefault(log_path, "")
        self.spawn_detached_calls.append((cmd, cwd, log_path))
        # 실제 러너라면 spawn된 프로세스의 명령이 곧 cmd[0]이다. 테스트가
        # PID 재사용을 흉내내려면 spawn_detached 이후 이 맵을 직접
        # 덮어써서 "다른 명령이 실행 중"인 상태를 만들면 된다.
        if cmd:
            self.pid_cmdlines_map[pid] = os.path.basename(str(cmd[0]).strip('"'))
        return pid

    def pids_alive(self, pids, timeout: float = 60.0):
        self.pids_alive_calls += 1
        return {p for p in pids if p in self.live_pids}

    def pid_cmdlines(self, pids):
        self.pid_cmdlines_calls += 1
        if self.pid_cmdlines_fail:
            return None
        return {p: self.pid_cmdlines_map[p] for p in pids if p in self.pid_cmdlines_map}

    def kill_pid(self, pid: int) -> None:
        self.live_pids.discard(pid)

    def tail_file(self, path: str, offset: int):
        if path not in self.files:
            return 0, ""
        data = self.files[path].encode("utf-8")
        if offset > len(data):
            offset = 0
        chunk = data[offset:]
        return offset + len(chunk), chunk.decode("utf-8")

    def file_size(self, path: str) -> int:
        if path not in self.files:
            return 0
        return len(self.files[path].encode("utf-8"))

    def ensure_dir(self, path: str) -> None:
        self.dirs.add(path)

    def list_dir(self, path: str) -> list[str]:
        prefix = path.rstrip("/") + "/"
        names = set()
        for p in self.files:
            if p.startswith(prefix):
                rest = p[len(prefix):]
                if "/" not in rest:
                    names.add(rest)
        return sorted(names)
