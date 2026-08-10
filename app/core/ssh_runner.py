from __future__ import annotations

import shlex
import socket
import threading

import paramiko

from app.core.runner import CommandRunner, ManagedProcess, OnExit, OnLine, RunResult
from app.core.store import SshProfile


def quote_cmd(cmd: list[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)


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

    def run(self, cmd: list[str], timeout: float = 60.0) -> RunResult:
        _, stdout, stderr = self._require().exec_command(quote_cmd(cmd),
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
