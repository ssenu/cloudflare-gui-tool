from __future__ import annotations

import shlex
import socket
import threading

import functools
import threading

import paramiko

from app.core.runner import (CommandRunner, ManagedProcess, OnExit, OnLine,
                              RunResult, decode_tail, resumes_mid_character)
from app.core.store import SshProfile


def quote_cmd(cmd: list[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)


def build_spawn_detached_command(cmd: list[str], cwd: str | None, log_path: str) -> str:
    """원격에서 프로세스를 분리 실행하는 셸 명령 문자열을 조립한다.

    setsid로 새 세션(프로세스 그룹)을 만들고 nohup으로 SIGHUP을 무시한다.
    stdout/stderr은 로그 파일에 append하고, 실행 직후 PID를 echo한다.
    """
    full = quote_cmd(cmd)
    log_quoted = shlex.quote(log_path)
    line = f"setsid nohup {full} >> {log_quoted} 2>&1 < /dev/null & echo $!"
    if cwd:
        line = f"cd {shlex.quote(cwd)} && {line}"
    return line


def build_pids_alive_command(pids: list[int]) -> str | None:
    """여러 PID의 생존 여부를 한 번의 명령으로 조회하는 셸 명령을 조립한다.

    살아있는 PID만 한 줄씩 출력된다. pids가 비어있으면 None을 반환한다.
    """
    if not pids:
        return None
    pid_list = " ".join(str(p) for p in pids)
    return f"for p in {pid_list}; do kill -0 $p 2>/dev/null && echo $p; done"


def build_kill_pid_command(pid: int) -> str:
    """PID(프로세스 그룹)를 종료하는 셸 명령을 조립한다.

    먼저 프로세스 그룹에 TERM을 보내고, 실패하면 단일 PID에 TERM을 보낸다.
    1초 유예 후 같은 방식으로 KILL을 보낸다. 이미 죽은 프로세스에도
    에러 없이 항상 성공(true)으로 끝난다.
    """
    return (
        f"kill -TERM -{pid} 2>/dev/null || kill -TERM {pid} 2>/dev/null; "
        f"sleep 1; "
        f"kill -KILL -{pid} 2>/dev/null || kill -KILL {pid} 2>/dev/null; "
        f"true"
    )


class SshProcess(ManagedProcess):
    def __init__(self, channel: paramiko.Channel,
                 on_line: OnLine | None, on_exit: OnExit | None):
        self._ch = channel
        self._on_exit = on_exit
        self._closed = False
        threading.Thread(target=self._pump, args=(on_line,), daemon=True).start()

    def _pump(self, on_line: OnLine | None):
        buf = ""
        code = 1  # default error code
        try:
            while True:
                data = self._ch.recv(4096)
                if not data:
                    break
                buf += data.decode("utf-8", errors="replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    if on_line:
                        on_line("stdout", line.rstrip("\r"))
            code = self._ch.recv_exit_status() if not self._closed else 0
        except Exception:
            # recv() or recv_exit_status() raised; code stays 1 (error)
            pass
        finally:
            # Guarantee on_exit is called exactly once
            if self._on_exit:
                self._on_exit(code)

    def is_running(self) -> bool:
        return not self._ch.closed and not self._ch.exit_status_ready()

    def stop(self, timeout: float = 5.0) -> None:
        self._closed = True
        self._ch.close()


def _synchronized(method):
    """SshRunner의 공개 메서드를 인스턴스 락으로 감싼다(클래스 주석 참고)."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class SshRunner(CommandRunner):
    """SSH/SFTP 러너.

    스레드 안전: 상태 폴링은 워커 스레드에서, 사용자 조작(토글·다이얼로그)은
    GUI 스레드에서 같은 연결을 쓴다. paramiko의 SFTPClient는 동시 호출에
    안전하지 않으므로 공개 메서드 전체를 하나의 락으로 직렬화한다. 대기 시간은
    "다른 쪽 호출 하나가 끝날 때까지"로, 폴링 한 번이 짧아 체감되지 않는다.
    """

    def __init__(self, profile: SshProfile, password: str | None = None):
        # RLock인 이유: 공개 메서드가 다른 공개 메서드를 부르는 경우가 있다
        # (예: spawn_detached -> ensure_dir). 같은 스레드의 재진입은 막지 않는다.
        self._lock = threading.RLock()
        self.profile = profile
        self.password = password
        self.name = f"ssh:{profile.name}"
        self._client: paramiko.SSHClient | None = None
        self._sftp: paramiko.SFTPClient | None = None
        self._home: str | None = None

    @_synchronized
    def connect(self) -> None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict = dict(hostname=self.profile.host, port=self.profile.port,
                            username=self.profile.username, timeout=10)
        if self.profile.key_path:
            kwargs["key_filename"] = self.profile.key_path
        if self.password:
            kwargs["password"] = self.password
        client.connect(**kwargs)
        try:
            self._sftp = client.open_sftp()
        except Exception:
            client.close()
            raise
        self._client = client

    @_synchronized
    def close(self) -> None:
        if self._sftp:
            self._sftp.close()
        if self._client:
            self._client.close()
        self._client = self._sftp = None
        self._home = None

    @_synchronized
    def is_connected(self) -> bool:
        t = self._client.get_transport() if self._client else None
        return bool(t and t.is_active())

    def _require(self) -> paramiko.SSHClient:
        if not self.is_connected():
            raise ConnectionError("SSH 연결이 없습니다. 먼저 연결하세요.")
        return self._client  # type: ignore[return-value]

    @_synchronized
    def run(self, cmd: list[str], timeout: float = 60.0,
           cwd: str | None = None) -> RunResult:
        command_str = quote_cmd(cmd)
        if cwd:
            command_str = f"cd {shlex.quote(cwd)} && {command_str}"
        _, stdout, stderr = self._require().exec_command(command_str,
                                                         timeout=timeout)
        try:
            # paramiko's timeout is per-recv inactivity, not overall command time.
            # If command is slow but still outputting, stdout.read() can raise socket.timeout.
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            return RunResult(stdout.channel.recv_exit_status(), out, err)
        except socket.timeout:
            raise TimeoutError(f"SSH 명령 시간 초과: {' '.join(cmd)}")

    @_synchronized
    def spawn(self, cmd, cwd=None, on_line=None, on_exit=None) -> ManagedProcess:
        transport = self._require().get_transport()
        ch = transport.open_session()
        ch.get_pty()  # 채널 close 시 원격 프로세스 종료 보장
        full = quote_cmd(cmd)
        if cwd:
            full = f"cd {shlex.quote(cwd)} && {full}"
        ch.exec_command(full)
        return SshProcess(ch, on_line, on_exit)

    @_synchronized
    def read_file(self, path: str) -> str:
        assert self._sftp
        with self._sftp.open(self._expand(path)) as f:
            return f.read().decode("utf-8")

    @_synchronized
    def read_files(self, paths: list[str]) -> dict[str, str | None]:
        """여러 파일을 셸 한 번으로 읽는다(왕복 N -> 1).

        구분자를 앞에 찍고 cat 하는 방식이다. PID 파일은 우리가 쓰는 3줄짜리
        파일이라 구분자가 내용에 섞일 일이 없지만, 혹시 모를 충돌을 피하려고
        일반 텍스트에 나오지 않는 문자열을 쓴다. 파일이 없으면 값은 None.
        """
        if not paths:
            return {}
        marker = "CFT"
        parts = []
        for path in paths:
            quoted = shlex.quote(self._expand(path))
            head = shlex.quote(marker + path + chr(1))
            # 파일이 있을 때만 구분자를 찍는다 - 없는 파일과 빈 파일을
            # 구분해야 호출측 계약("없으면 None")이 지켜진다.
            parts.append(f"if [ -f {quoted} ]; then printf '%s' {head}; "
                         f"cat {quoted} 2>/dev/null; fi")
        res = self.run(["sh", "-c", "; ".join(parts)], timeout=30.0)
        out: dict[str, str | None] = {p: None for p in paths}
        if res.exit_code != 0 and not res.stdout:
            return out
        for chunk in res.stdout.split("CFT"):
            if not chunk:
                continue
            path, _, content = chunk.partition("")
            if path in out:
                out[path] = content
        return out

    @_synchronized
    def read_pid_files(self, paths: list[str]) -> dict[str, tuple[str | None, bool]]:
        """PID 파일 읽기와 생존 확인을 셸 한 번으로 끝낸다(왕복 2 -> 1).

        유닛마다 `[구분자]경로[구분자]alive|dead[구분자]내용`을 찍는다.
        파일이 없으면 아무것도 찍지 않아 호출측에서 None으로 남는다.
        """
        if not paths:
            return {}
        sep = chr(1)
        parts = []
        for path in paths:
            quoted = shlex.quote(self._expand(path))
            head = shlex.quote(sep + path + sep)
            parts.append(
                f"if [ -f {quoted} ]; then "
                f"pid=$(head -n1 {quoted} 2>/dev/null); "
                f"st=dead; kill -0 \"$pid\" 2>/dev/null && st=alive; "
                f"printf '%s%s%s' {head} \"$st\" {shlex.quote(sep)}; "
                f"cat {quoted} 2>/dev/null; fi")
        res = self.run(["sh", "-c", "; ".join(parts)], timeout=30.0)
        out: dict[str, tuple[str | None, bool]] = {p: (None, False) for p in paths}
        # 형식: sep + 경로 + sep + 상태 + sep + 내용  (유닛마다 반복)
        pieces = res.stdout.split(sep)
        i = 1
        while i + 2 <= len(pieces):
            path, state = pieces[i], pieces[i + 1]
            content = pieces[i + 2] if i + 2 < len(pieces) else ""
            if path in out:
                out[path] = (content, state == "alive")
            i += 3
        return out

    @_synchronized
    def write_file(self, path: str, text: str) -> None:
        assert self._sftp
        with self._sftp.open(self._expand(path), "w") as f:
            f.write(text)

    @_synchronized
    def append_file(self, path: str, text: str) -> None:
        assert self._sftp
        with self._sftp.open(self._expand(path), "a") as f:
            f.write(text)

    @_synchronized
    def file_exists(self, path: str) -> bool:
        assert self._sftp
        try:
            self._sftp.stat(self._expand(path))
            return True
        except FileNotFoundError:
            return False

    @_synchronized
    def remove_file(self, path: str) -> None:
        assert self._sftp
        self._sftp.remove(self._expand(path))

    @_synchronized
    def remove_tree(self, path: str) -> None:
        # run()이 이미 리스트 인자를 shlex.quote로 안전하게 조립해준다.
        self.run(["rm", "-rf", self._expand(path)])

    @_synchronized
    def home_dir(self) -> str:
        assert self._sftp
        if self._home is None:
            self._home = self._sftp.normalize(".")
        return self._home

    def _expand(self, path: str) -> str:
        if path.startswith("~"):
            return self.home_dir() + path[1:]
        return path

    @_synchronized
    def spawn_detached(self, cmd: list[str], cwd: str | None, log_path: str) -> int:
        line = build_spawn_detached_command(cmd, cwd, log_path)
        res = self.run(["sh", "-c", line])
        # 셸 배너 등이 섞일 수 있으므로 숫자로만 이루어진 마지막 줄만 PID로 인정
        digit_lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip().isdigit()]
        if not digit_lines:
            raise RuntimeError(f"원격 프로세스 시작 실패: PID를 읽을 수 없습니다 ({res.stdout!r}, {res.stderr!r})")
        return int(digit_lines[-1])

    @_synchronized
    def pids_alive(self, pids: list[int], timeout: float = 60.0) -> set[int]:
        cmd = build_pids_alive_command(pids)
        if cmd is None:
            return set()
        res = self.run(["sh", "-c", cmd], timeout=timeout)
        return {int(ln) for ln in res.stdout.splitlines() if ln.strip()}

    @_synchronized
    def pid_cmdlines(self, pids: list[int]) -> dict[int, str] | None:
        # D1: comm(실행 이미지 이름)이 아니라 args(전체 커맨드라인)를 쓴다.
        # comm은 인터프리터/래퍼로 실행되는 프로세스에서 실제 실행 파일
        # 이름을 주지 않는다 - shebang이 /usr/bin/python3인 uvicorn은
        # comm이 "python3"로, node로 실행되는 /usr/bin/npm은 "node"로
        # 나와 등록된 명령과 결정적으로 어긋난다. 반면 args에는 실제
        # 실행한 스크립트/명령 경로가 그대로 남아 포함(in) 비교가 통한다.
        if not pids:
            return {}
        pid_arg = ",".join(str(p) for p in pids)
        try:
            res = self.run(["ps", "-p", pid_arg, "-o", "pid=,args="], timeout=5.0)
        except (TimeoutError, ConnectionError, OSError):
            return None
        if res.exit_code != 0:
            # 연결 순단, busybox ps의 -p/-o 미지원 등 - 대조 불가(모름)로 취급한다.
            return None
        result: dict[int, str] = {}
        for line in res.stdout.splitlines():
            # args는 공백을 포함하므로 맨 앞 PID 토큰만 떼고 나머지는 통째로
            # 남긴다 (maxsplit=1). 파싱 실패(토큰이 하나뿐인 라인 등)는
            # 대조 불가 라인으로 보고 건너뛴다.
            bits = line.strip().split(None, 1)
            if len(bits) != 2:
                continue
            try:
                pid = int(bits[0])
            except ValueError:
                continue
            result[pid] = bits[1]
        return result

    @_synchronized
    def kill_pid(self, pid: int) -> None:
        cmd = build_kill_pid_command(pid)
        self.run(["sh", "-c", cmd], timeout=5.0)

    @_synchronized
    def tail_file(self, path: str, offset: int) -> tuple[int, str]:
        assert self._sftp
        remote_path = self._expand(path)
        try:
            attrs = self._sftp.stat(remote_path)
        except FileNotFoundError:
            return 0, ""
        size = attrs.st_size or 0
        if offset > size:
            offset = 0
        with self._sftp.open(remote_path, "rb") as f:
            f.seek(offset)
            data = f.read()
            if resumes_mid_character(data):
                # 로컬 러너와 같은 규칙(runner.resumes_mid_character 주석 참고)
                offset = 0
                f.seek(0)
                data = f.read()
        text, consumed = decode_tail(data)
        return offset + consumed, text

    @_synchronized
    def file_size(self, path: str) -> int:
        assert self._sftp
        try:
            attrs = self._sftp.stat(self._expand(path))
        except FileNotFoundError:
            return 0
        return attrs.st_size or 0

    @_synchronized
    def ensure_dir(self, path: str) -> None:
        self.run(["mkdir", "-p", self._expand(path)])

    @_synchronized
    def list_dir(self, path: str) -> list[str]:
        assert self._sftp
        try:
            return self._sftp.listdir(self._expand(path))
        except FileNotFoundError:
            return []
