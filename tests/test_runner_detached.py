import sys
import time

import pytest

from app.core.runner import LocalRunner
from app.core.ssh_runner import build_spawn_detached_command, build_pids_alive_command, \
    build_kill_pid_command


def _wait_until(pred, timeout=5.0, interval=0.05):
    """조건이 참이 될 때까지 짧게 폴링 (상한 시간 존재)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


class TestLocalRunnerDetached:
    def test_spawn_detached_returns_alive_pid_then_dies_after_kill(self, tmp_path):
        r = LocalRunner()
        log_path = str(tmp_path / "out.log")
        pid = r.spawn_detached(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=None, log_path=log_path)
        assert isinstance(pid, int) and pid > 0

        assert _wait_until(lambda: pid in r.pids_alive([pid]))

        r.kill_pid(pid)

        assert _wait_until(lambda: pid not in r.pids_alive([pid]))

    def test_pids_alive_empty_list_returns_empty_set(self):
        r = LocalRunner()
        assert r.pids_alive([]) == set()

    def test_pids_alive_unknown_pid_not_included(self):
        r = LocalRunner()
        # 존재할 가능성이 극히 낮은 PID
        assert r.pids_alive([999999]) == set()

    def test_spawn_detached_output_goes_to_log_file(self, tmp_path):
        r = LocalRunner()
        log_path = str(tmp_path / "out.log")
        pid = r.spawn_detached(
            [sys.executable, "-u", "-c",
             "print('line1'); print('line2')"],
            cwd=None, log_path=log_path)

        assert _wait_until(lambda: pid not in r.pids_alive([pid]), timeout=5.0)

        with open(log_path, encoding="utf-8") as f:
            content = f.read()
        assert "line1" in content
        assert "line2" in content

    def test_tail_file_returns_only_new_content_on_second_call(self, tmp_path):
        r = LocalRunner()
        log_path = str(tmp_path / "out.log")
        pid = r.spawn_detached(
            [sys.executable, "-u", "-c",
             "print('first')"],
            cwd=None, log_path=log_path)
        assert _wait_until(lambda: pid not in r.pids_alive([pid]), timeout=5.0)

        offset1, text1 = r.tail_file(log_path, 0)
        assert "first" in text1
        assert offset1 > 0

        # 추가 내용 없이 재호출 -> 빈 문자열, offset 동일
        offset2, text2 = r.tail_file(log_path, offset1)
        assert text2 == ""
        assert offset2 == offset1

        # 파일에 내용 추가
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("second\n")

        offset3, text3 = r.tail_file(log_path, offset2)
        assert "second" in text3
        assert "first" not in text3
        assert offset3 > offset2

    def test_tail_file_missing_returns_zero_empty(self, tmp_path):
        r = LocalRunner()
        missing = str(tmp_path / "nope.log")
        offset, text = r.tail_file(missing, 0)
        assert offset == 0
        assert text == ""

    def test_tail_file_offset_beyond_size_restarts_from_zero(self, tmp_path):
        r = LocalRunner()
        log_path = tmp_path / "out.log"
        log_path.write_text("hello", encoding="utf-8")

        offset, text = r.tail_file(str(log_path), 9999)
        assert text == "hello"
        assert offset == len("hello")

    def test_ensure_dir_creates_nested_directory(self, tmp_path):
        r = LocalRunner()
        target = str(tmp_path / "a" / "b" / "c")
        r.ensure_dir(target)
        import os
        assert os.path.isdir(target)
        # 이미 존재해도 에러 없음
        r.ensure_dir(target)

    def test_list_dir_returns_filenames(self, tmp_path):
        r = LocalRunner()
        (tmp_path / "one.txt").write_text("x", encoding="utf-8")
        (tmp_path / "two.txt").write_text("y", encoding="utf-8")
        names = r.list_dir(str(tmp_path))
        assert set(names) == {"one.txt", "two.txt"}

    def test_list_dir_missing_returns_empty_list(self, tmp_path):
        r = LocalRunner()
        assert r.list_dir(str(tmp_path / "does_not_exist")) == []


class TestSshRunnerCommandBuilding:
    """SSH는 실제 연결 없이 순수 명령 조립 로직만 검증한다."""

    def test_spawn_detached_command_basic(self):
        cmd = build_spawn_detached_command(
            ["cloudflared", "tunnel", "run", "my-tunnel"],
            cwd=None, log_path="/home/pi/.cloudflare-gui/run/tunnel-my-tunnel.log")
        assert "setsid" in cmd
        assert "nohup" in cmd
        assert "cloudflared tunnel run my-tunnel" in cmd
        assert ">> /home/pi/.cloudflare-gui/run/tunnel-my-tunnel.log" in cmd
        assert "echo $!" in cmd
        assert "< /dev/null" in cmd

    def test_spawn_detached_command_with_cwd(self):
        cmd = build_spawn_detached_command(
            ["python3", "app.py"], cwd="/opt/my app", log_path="/tmp/x.log")
        assert "cd " in cmd
        assert "'/opt/my app'" in cmd

    def test_spawn_detached_command_quotes_special_chars(self):
        cmd = build_spawn_detached_command(
            ["echo", "hello world"], cwd=None, log_path="/tmp/a b.log")
        assert "'hello world'" in cmd
        assert "'/tmp/a b.log'" in cmd

    def test_pids_alive_command_lists_pids(self):
        cmd = build_pids_alive_command([101, 202, 303])
        assert "101" in cmd and "202" in cmd and "303" in cmd
        assert "kill -0" in cmd

    def test_pids_alive_command_empty_list_is_none(self):
        assert build_pids_alive_command([]) is None

    def test_kill_pid_command_tries_term_then_kill(self):
        cmd = build_kill_pid_command(1234)
        assert "kill -TERM -1234" in cmd
        assert "kill -TERM 1234" in cmd
        assert "kill -KILL -1234" in cmd
        assert "kill -KILL 1234" in cmd
        assert cmd.strip().endswith("true")
