import os
import subprocess
import sys
import time

import pytest

from app.core.runner import LocalRunner, decode_tail
from app.core.ssh_runner import build_spawn_detached_command, build_pids_alive_command, \
    build_kill_pid_command

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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

    def test_spawn_detached_survives_parent_process_exit(self, tmp_path):
        """부모(이 테스트를 실행하는 헬퍼 프로세스)가 죽어도 자식이 살아있는지 검증.

        헬퍼 스크립트를 별도 프로세스로 실행해 LocalRunner().spawn_detached로
        30초 sleep 자식을 띄우고 PID를 출력한 뒤 즉시 종료한다. 헬퍼 프로세스가
        완전히 끝난 후에도(=부모 소멸) 자식 PID가 살아있어야 진짜 분리 실행이다.
        """
        log_path = str(tmp_path / "detached.log")
        helper = tmp_path / "spawn_helper.py"
        helper.write_text(
            "import sys\n"
            f"sys.path.insert(0, {REPO_ROOT!r})\n"
            "from app.core.runner import LocalRunner\n"
            "r = LocalRunner()\n"
            "pid = r.spawn_detached(\n"
            f"    [sys.executable, '-c', 'import time; time.sleep(30)'],\n"
            f"    cwd=None, log_path={log_path!r})\n"
            "print(pid)\n",
            encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(helper)],
            capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        pid = int(result.stdout.strip())

        # 헬퍼 프로세스(부모)는 이미 종료됐다. 자식이 여전히 살아있어야 한다.
        r = LocalRunner()
        try:
            assert _wait_until(lambda: pid in r.pids_alive([pid]))
        finally:
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

    def test_tail_file_does_not_corrupt_multibyte_char_at_boundary(self, tmp_path):
        """읽기 경계가 한글(3바이트 UTF-8) 문자 중간에 걸려도 손상 없이 이어붙일 수 있어야 한다."""
        r = LocalRunner()
        log_path = tmp_path / "korean.log"
        text = "안녕하세요"
        data = text.encode("utf-8")
        log_path.write_bytes(data)

        # 첫 글자('안', 3바이트) 중간에서 끊기는 offset을 골라 두 번에 나눠 읽는다
        split = 2  # '안'의 3바이트 중 2바이트만 포함
        first_bytes = data[:split]
        with open(log_path, "wb") as f:
            f.write(first_bytes)

        offset1, text1 = r.tail_file(str(log_path), 0)
        # 잘린 시퀀스는 소비하지 않아야 하므로 아직 아무것도 디코드되지 않음
        assert text1 == ""
        assert offset1 == 0

        # 나머지 바이트를 파일에 추가
        with open(log_path, "ab") as f:
            f.write(data[split:])

        offset2, text2 = r.tail_file(str(log_path), offset1)
        assert offset2 == len(data)
        assert text1 + text2 == text

    def test_decode_tail_leaves_truncated_multibyte_suffix(self):
        data = "안녕".encode("utf-8")
        # 마지막 문자의 첫 바이트만 남기고 자른다
        truncated = data[:-2]
        text, consumed = decode_tail(truncated)
        assert text == "안"
        assert consumed == len(truncated) - 1  # 마지막 1바이트는 보류

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
