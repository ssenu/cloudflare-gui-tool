from __future__ import annotations

import shlex
import socket
import threading

import paramiko

from app.core.runner import CommandRunner, ManagedProcess, OnExit, OnLine, RunResult, decode_tail
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


class SshRunner(CommandRunner):
    def __init__(self, profile: SshProfile, password: str | None = None):
        self.profile = profile
        self.password = password
        self.name = f"ssh:{profile.name}"
        self._client: paramiko.SSHClient | None = None
        self._sftp: paramiko.SFTPClient | None = None
        self._home: str | None = None

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

    def close(self) -> None:
        if self._sftp:
            self._sftp.close()
        if self._client:
            self._client.close()
        self._client = self._sftp = None
        self._home = None

    def is_connected(self) -> bool:
        t = self._client.get_transport() if self._client else None
        return bool(t and t.is_active())

    def _require(self) -> paramiko.SSHClient:
        if not self.is_connected():
            raise ConnectionError("SSH 연결이 없습니다. 먼저 연결하세요.")
        return self._client  # type: ignore[return-value]

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

    def spawn(self, cmd, cwd=None, on_line=None, on_exit=None) -> ManagedProcess:
        transport = self._require().get_transport()
        ch = transport.open_session()
        ch.get_pty()  # 채널 close 시 원격 프로세스 종료 보장
        full = quote_cmd(cmd)
        if cwd:
            full = f"cd {shlex.quote(cwd)} && {full}"
        ch.exec_command(full)
        return SshProcess(ch, on_line, on_exit)

    def read_file(self, path: str) -> str:
        assert self._sftp
        with self._sftp.open(self._expand(path)) as f:
            return f.read().decode("utf-8")

    def write_file(self, path: str, text: str) -> None:
        assert self._sftp
        with self._sftp.open(self._expand(path), "w") as f:
            f.write(text)

    def file_exists(self, path: str) -> bool:
        assert self._sftp
        try:
            self._sftp.stat(self._expand(path))
            return True
        except FileNotFoundError:
            return False

    def remove_file(self, path: str) -> None:
        assert self._sftp
        self._sftp.remove(self._expand(path))

    def home_dir(self) -> str:
        assert self._sftp
        if self._home is None:
            self._home = self._sftp.normalize(".")
        return self._home

    def _expand(self, path: str) -> str:
        if path.startswith("~"):
            return self.home_dir() + path[1:]
        return path

    def spawn_detached(self, cmd: list[str], cwd: str | None, log_path: str) -> int:
        line = build_spawn_detached_command(cmd, cwd, log_path)
        res = self.run(["sh", "-c", line])
        # 셸 배너 등이 섞일 수 있으므로 숫자로만 이루어진 마지막 줄만 PID로 인정
        digit_lines = [ln.strip() for ln in res.stdout.splitlines() if ln.strip().isdigit()]
        if not digit_lines:
            raise RuntimeError(f"원격 프로세스 시작 실패: PID를 읽을 수 없습니다 ({res.stdout!r}, {res.stderr!r})")
        return int(digit_lines[-1])

    def pids_alive(self, pids: list[int]) -> set[int]:
        cmd = build_pids_alive_command(pids)
        if cmd is None:
            return set()
        res = self.run(["sh", "-c", cmd])
        return {int(ln) for ln in res.stdout.splitlines() if ln.strip()}

    def kill_pid(self, pid: int) -> None:
        cmd = build_kill_pid_command(pid)
        self.run(["sh", "-c", cmd], timeout=5.0)

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
        text, consumed = decode_tail(data)
        return offset + consumed, text

    def ensure_dir(self, path: str) -> None:
        self.run(["mkdir", "-p", self._expand(path)])

    def list_dir(self, path: str) -> list[str]:
        assert self._sftp
        try:
            return self._sftp.listdir(self._expand(path))
        except FileNotFoundError:
            return []
